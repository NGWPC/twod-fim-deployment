"""Assembling the run_kwse_scenarios payload.

The interesting work is not the plan — plan.py is tested on its own — but the
gathering around it: pulling candidate boundaries out of BOTH downstream proofs,
turning each planned scenario into an address, and preserving the order the seeds
depend on. The database and bucket are stubbed so those stay the subject.
"""

import pytest

from recon import check

UPSTREAM, DOWNSTREAM = 100, 200
OWN_MODEL = "5f14368c_N350S296E449W355"
DS_MODEL = "aa119e0c_N120S140E200W180"
RUN_HASH = "af1436c4"

# Downstream reach: an ND run at each discharge (its per-discharge minimum),
# plus stage libraries whose achieved and imposed stages differ by ~3 m.
DS_CURVE = [{"q": 200, "wse": 223.0}, {"q": 900, "wse": 225.6}]
DS_INDEX = [
    {"q": 200, "runs": [{"wse": 224.3, "bc": 221.0}, {"wse": 225.2, "bc": 222.0},
                        {"wse": 226.4, "bc": 223.0}, {"wse": 227.1, "bc": 224.0}]},
    {"q": 900, "runs": [{"wse": 226.1, "bc": 223.0}, {"wse": 226.9, "bc": 224.0}]},
]


@pytest.fixture
def wired(monkeypatch):
    """Stub the two things the payload builder reads outside itself."""
    def fake_effective(reach_id, **kw):
        return {"reach_id": reach_id, "is_terminal": False, "reach_to_id": DOWNSTREAM,
                "ld_ds_z_delta": 1.0, "kwse_upper_bound": None}

    def fake_one(sql, params=None, **kw):
        reach = params[0] if params else None
        if "materialized_models" in sql:
            return {"model_id": OWN_MODEL}
        if "materialized_kwse_runs" in sql:
            return {"scenario_index": DS_INDEX} if reach == DOWNSTREAM else None
        if "materialized_nd_runs" in sql:
            if reach == UPSTREAM:
                return {"model_id": OWN_MODEL, "run_identity_hash": RUN_HASH,
                        "q_set": [200, 900]}
            return {"model_id": DS_MODEL, "run_identity_hash": RUN_HASH,
                    "us_min_wse_curve": DS_CURVE}
        raise AssertionError(f"unexpected query: {sql}")

    def fake_library(reach_id, model_id, run_hash):
        slope = "1.2E04" if reach_id == UPSTREAM else "9.0E03"
        return f"s3://b/version=v1/results/reach={reach_id}/{model_id}/{run_hash}/nd={slope}"

    monkeypatch.setattr(check.intent, "effective", fake_effective)
    monkeypatch.setattr(check.db, "one", fake_one)
    monkeypatch.setattr(check.storage, "nd_library_path", fake_library)


def test_payload_shape(wired):
    p = check._run_kwse_payload(UPSTREAM)
    assert p["model_manifest_path"].endswith(f"{OWN_MODEL}/model_manifest.json")
    # The bare results root: the job appends reach=/model_id/hash/ itself.
    assert p["model_results_base_path"].endswith("/results")
    assert p["scenarios"]
    assert set(p) == {"model_manifest_path", "model_results_base_path", "scenarios",
                      "volume_convergence_tolerance", "allow_water_on_edges"}


def test_scenario_keys_match_the_job_input_model(wired):
    """RunKWSEScenariosInputs forbids extras, so spelling is load-bearing."""
    s = check._run_kwse_payload(UPSTREAM)["scenarios"][0]
    assert set(s) == {"upstream_discharge", "bc_value", "downstream_Scenario", "hotstart"}
    assert set(s["hotstart"]) == {"upstream_discharge", "bc_type", "bc_value",
                                  "identity_hash"}


def test_candidates_come_from_both_downstream_proofs(wired):
    """A low target binds to the downstream ND run, higher ones to its libraries."""
    scenarios = check._run_kwse_payload(UPSTREAM)["scenarios"]
    hrefs = [s["downstream_Scenario"] for s in scenarios]
    assert any("/nd=9.0E03/" in h for h in hrefs)
    assert any("/kwse=" in h for h in hrefs)


def test_downstream_address_uses_the_imposed_stage_not_the_achieved_one(wired):
    """Our target 226.0 binds to a run that ACHIEVED 226.1 but sits in kwse=223.0.

    Note also which discharge appears in that address: the DOWNSTREAM run's, not
    ours. We are at q=200, and the nearest achieved stage anywhere downstream is
    226.1 from its q=900 run — nearer than its own q=200 run at 226.4. Our inflow
    and the downstream water surface are independent dimensions, which is the
    entire point of a stage library, so the two discharges need not agree.
    """
    scenarios = check._run_kwse_payload(UPSTREAM)["scenarios"]
    at_226 = next(s for s in scenarios if s["upstream_discharge"] == 200
                  and s["bc_value"] == pytest.approx(226.0))
    assert "/kwse=223.0/q=900/scenario_manifest.json" in at_226["downstream_Scenario"]


def test_downstream_address_is_under_the_downstream_reach_and_model(wired):
    s = check._run_kwse_payload(UPSTREAM)["scenarios"][0]
    assert f"/reach={DOWNSTREAM}/{DS_MODEL}/{RUN_HASH}/" in s["downstream_Scenario"]


def test_first_scenario_of_each_discharge_seeds_from_this_reach_nd_run(wired):
    """ND seeds carry the slope of THIS reach, not the downstream one."""
    scenarios = check._run_kwse_payload(UPSTREAM)["scenarios"]
    for q in (200, 900):
        first = next(s for s in scenarios if s["upstream_discharge"] == q)
        assert first["hotstart"]["bc_type"] == "ND"
        assert first["hotstart"]["upstream_discharge"] == q
        assert first["hotstart"]["bc_value"] == pytest.approx(12000.0)


def test_later_scenarios_seed_from_the_stage_below(wired):
    scenarios = check._run_kwse_payload(UPSTREAM)["scenarios"]
    at_200 = [s for s in scenarios if s["upstream_discharge"] == 200]
    for previous, current in zip(at_200, at_200[1:]):
        assert current["hotstart"]["bc_type"] == "KWSE"
        assert current["hotstart"]["bc_value"] == pytest.approx(previous["bc_value"])


def test_every_kwse_seed_appears_earlier_in_the_list(wired):
    """The job runs serially; a seed named before it exists points at nothing."""
    scenarios = check._run_kwse_payload(UPSTREAM)["scenarios"]
    seen = set()
    for s in scenarios:
        h = s["hotstart"]
        if h["bc_type"] == "KWSE":
            assert (h["upstream_discharge"], h["bc_value"]) in seen
        seen.add((s["upstream_discharge"], s["bc_value"]))


def test_hotstart_identity_hash_is_named_not_left_to_the_image(wired):
    """The job's default is baked into its image; this is the predicted hash."""
    for s in check._run_kwse_payload(UPSTREAM)["scenarios"]:
        assert s["hotstart"]["identity_hash"] == RUN_HASH


def test_a_terminal_reach_is_refused_rather_than_planned(wired, monkeypatch):
    """ISU-013: no downstream reach means no stage library can be bounded."""
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: {
        "reach_id": r, "is_terminal": True, "reach_to_id": None,
        "ld_ds_z_delta": 1.0, "kwse_upper_bound": None})
    with pytest.raises(RuntimeError, match="terminal"):
        check._run_kwse_payload(UPSTREAM)


def test_an_unauthored_stage_increment_is_refused(wired, monkeypatch):
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: {
        "reach_id": r, "is_terminal": False, "reach_to_id": DOWNSTREAM,
        "ld_ds_z_delta": None, "kwse_upper_bound": None})
    with pytest.raises(RuntimeError, match="ld_ds_z_delta"):
        check._run_kwse_payload(UPSTREAM)


def test_authored_ceiling_shrinks_the_library(wired, monkeypatch):
    full = len(check._run_kwse_payload(UPSTREAM)["scenarios"])
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: {
        "reach_id": r, "is_terminal": False, "reach_to_id": DOWNSTREAM,
        "ld_ds_z_delta": 1.0, "kwse_upper_bound": 225.0})
    assert len(check._run_kwse_payload(UPSTREAM)["scenarios"]) < full


# --- checked against the job's own input model ---------------------------
# The jobs repo is a sibling checkout, not a dependency. Where it is importable,
# the payload is validated by the very model the job will validate it with —
# which forbids extras, so a misspelled key fails here rather than at runtime.
try:
    from twod_fim_jobs.models.run_kwse_scenarios import RunKWSEScenariosInputs
except ImportError:  # pragma: no cover - depends on the developer's layout
    RunKWSEScenariosInputs = None

needs_jobs = pytest.mark.skipif(
    RunKWSEScenariosInputs is None, reason="twod-fim-jobs not importable")


@needs_jobs
def test_payload_validates_against_the_real_job_input_model(wired):
    parsed = RunKWSEScenariosInputs.model_validate(check._run_kwse_payload(UPSTREAM))
    assert parsed.scenarios
    first = parsed.scenarios[0]
    assert first.hotstart is not None
    assert first.hotstart.bc_type == "ND"


@needs_jobs
def test_every_scenario_survives_validation(wired):
    """Discharges must be whole and positive, stages parseable, seeds well formed."""
    parsed = RunKWSEScenariosInputs.model_validate(check._run_kwse_payload(UPSTREAM))
    for s in parsed.scenarios:
        assert isinstance(s.upstream_discharge, int) and s.upstream_discharge > 0
        assert s.downstream_Scenario.endswith("scenario_manifest.json")
        assert s.hotstart.bc_type in ("ND", "KWSE")
