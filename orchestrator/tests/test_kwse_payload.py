"""Assembling the run_kwse_scenarios group: one job per discharge chain.

The interesting work is not the plan — plan.py is tested on its own — but the
gathering around it: pulling candidate boundaries out of BOTH downstream proofs,
turning each planned scenario into an address, splitting the plan into chains
that can run side by side, and leaving out what storage already holds. The
database and bucket are stubbed so those stay the subject.

The fixture's plan, worked by hand: q=200 runs stages 223..227 (five
scenarios), q=900 runs 226 and 227 (two). Its floor is higher because the
downstream reach sits higher at that discharge.
"""

import itertools
from types import SimpleNamespace

import pytest

from recon import check, scenarios, storage

UPSTREAM, DOWNSTREAM = 100, 200
OWN_MODEL = "5f14368c_N350S296E449W355"
DS_MODEL = "aa119e0c_N120S140E200W180"
RUN_HASH = "af1436c4"

CHAIN_200 = [(200, z) for z in (223.0, 224.0, 225.0, 226.0, 227.0)]
CHAIN_900 = [(900, z) for z in (226.0, 227.0)]
EVERY_SCENARIO = CHAIN_200 + CHAIN_900

# Downstream reach: an ND run at each discharge (its per-discharge minimum),
# plus stage libraries whose achieved and imposed stages differ by ~3 m.
DS_CURVE = [{"q": 200, "wse": 223.0}, {"q": 900, "wse": 225.6}]
DS_INDEX = [
    {"q": 200, "runs": [{"wse": 224.3, "bc": 221.0}, {"wse": 225.2, "bc": 222.0},
                        {"wse": 226.4, "bc": 223.0}, {"wse": 227.1, "bc": 224.0}]},
    {"q": 900, "runs": [{"wse": 226.1, "bc": 223.0}, {"wse": 226.9, "bc": 224.0}]},
]


# The basin, for the ceiling (DR-044 ALT-G). This reach holds a tenth of the
# downstream reach's area, so everything else can add 0.9^0.7 x 1000 = 929 cms:
# every cap passes the downstream reach's largest discharge and the plan is the
# uncapped one, which is what the fixture's hand-worked chains assume.
AREA = {UPSTREAM: 100.0, DOWNSTREAM: 1000.0}
DS_Q_UPPER = 1000


def intent_for(reach_id, **override):
    return {"reach_id": reach_id, "is_terminal": False, "reach_to_id": DOWNSTREAM,
            "ld_ds_z_delta": 1.0, "kwse_upper_bound": None,
            "total_da_sqkm": AREA[reach_id],
            "q_upper_bound": DS_Q_UPPER if reach_id == DOWNSTREAM else 900,
            **override}


@pytest.fixture
def wired(monkeypatch):
    """Stub the database and bucket. Storage starts empty; publish() fills it."""
    state = SimpleNamespace(manifests={}, refused=set())

    def fake_effective(reach_id, **kw):
        return intent_for(reach_id)

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
                    "q_set": [200, 900], "us_min_wse_curve": DS_CURVE}
        raise AssertionError(f"unexpected query: {sql}")

    def fake_library(reach_id, model_id, run_hash):
        slope = "1.2E04" if reach_id == UPSTREAM else "9.0E03"
        return f"s3://b/version=v1/results/reach={reach_id}/{model_id}/{run_hash}/nd={slope}"

    def fake_verify(manifest, reach_id, run_hash, model_id, folder):
        return ["manifest reach_id 999 != 100"] if folder in state.refused else []

    monkeypatch.setattr(check.intent, "effective", fake_effective)
    monkeypatch.setattr(check.db, "one", fake_one)
    monkeypatch.setattr(check.storage, "nd_library_path", fake_library)
    monkeypatch.setattr(check.storage, "read_json", lambda path: state.manifests.get(path))
    monkeypatch.setattr(check.identity, "verify_scenario_manifest", fake_verify)
    return state


def publish(state, q, z, refused=False):
    """Pretend the job wrote this scenario of THIS reach."""
    folder = scenarios.scenario_dir("KWSE", z, q)
    path = storage.scenario_manifest_path(UPSTREAM, OWN_MODEL, RUN_HASH, folder)
    state.manifests[path] = {"properties": {"nominal_wse": z + 1.0}}
    if refused:
        state.refused.add(folder)


def group():
    return check._run_kwse_group(UPSTREAM)


def all_scenarios():
    return [s for member in group() for s in member["inputs"]["scenarios"]]


def stages(member):
    return [(s["upstream_discharge"], s["bc_value"]) for s in member["inputs"]["scenarios"]]


def member_for(q):
    return next(m for m in group() if m["tags"] == [f"q:{q}"])


# --- one job per chain -----------------------------------------------------

def test_one_job_per_discharge_chain_in_discharge_order(wired):
    members = group()
    assert [m["tags"] for m in members] == [["q:200"], ["q:900"]]
    assert [stages(m) for m in members] == [CHAIN_200, CHAIN_900]


def test_every_member_is_a_complete_job_payload(wired):
    """Each chain is an ordinary run_kwse_scenarios job, so each carries the
    whole payload, not just its scenarios."""
    for member in group():
        assert set(member) == {"inputs", "tags"}
        inputs = member["inputs"]
        assert inputs["model_manifest_path"].endswith(f"{OWN_MODEL}/model_manifest.json")
        # The bare results root: the job appends reach=/model_id/hash/ itself.
        assert inputs["model_results_base_path"].endswith("/results")
        assert set(inputs) == {"model_manifest_path", "model_results_base_path", "scenarios",
                               "volume_convergence_tolerance", "allow_water_on_edges"}


def test_scenario_keys_match_the_job_input_model(wired):
    """RunKWSEScenariosInputs forbids extras, so spelling is load-bearing."""
    s = all_scenarios()[0]
    assert set(s) == {"upstream_discharge", "bc_value", "downstream_Scenario", "hotstart"}
    assert set(s["hotstart"]) == {"upstream_discharge", "bc_type", "bc_value",
                                  "identity_hash"}


# --- what is already in storage is not submitted again --------------------

def test_scenarios_already_in_storage_are_left_out(wired):
    """A chain that got partway resumes at its first missing stage, seeded
    from the stage below — which is in storage, not in this job."""
    publish(wired, 200, 223.0)
    publish(wired, 200, 224.0)

    member = member_for(200)
    assert stages(member) == CHAIN_200[2:]
    first = member["inputs"]["scenarios"][0]["hotstart"]
    assert (first["bc_type"], first["bc_value"]) == ("KWSE", 224.0)
    assert stages(member_for(900)) == CHAIN_900


def test_a_complete_chain_collapses_out_of_the_group(wired):
    for q, z in CHAIN_900:
        publish(wired, q, z)
    assert [m["tags"] for m in group()] == [["q:200"]]


def test_a_hole_in_a_chain_is_all_that_runs(wired):
    """Only the missing middle stage runs; the stages above it already exist
    and are not rerun because their seed is being replaced."""
    for q, z in CHAIN_200:
        if z != 225.0:
            publish(wired, q, z)

    member = member_for(200)
    assert stages(member) == [(200, 225.0)]
    assert member["inputs"]["scenarios"][0]["hotstart"]["bc_value"] == 224.0


def test_a_refused_manifest_counts_as_missing(wired):
    """The same judgement observe makes: a manifest it would refuse is not a
    scenario, so leaving it out would leave the step unsatisfiable."""
    for q, z in CHAIN_200:
        publish(wired, q, z, refused=(z == 223.0))
    member = member_for(200)
    assert stages(member) == [(200, 223.0)]
    assert member["inputs"]["scenarios"][0]["hotstart"]["bc_type"] == "ND"


def test_nothing_missing_is_an_empty_group(wired):
    for q, z in EVERY_SCENARIO:
        publish(wired, q, z)
    assert group() == []


@pytest.mark.parametrize("published", [
    subset
    for n in range(len(EVERY_SCENARIO) + 1)
    for subset in itertools.combinations(EVERY_SCENARIO, n)
])
def test_whatever_storage_holds_every_seed_is_reachable(wired, published):
    """Across every combination of what already exists: exactly the missing
    scenarios are submitted, and every seed is either this reach's nd run, in
    storage already, or earlier in the same job. A seed that is none of those
    names a depth grid that will never exist."""
    for q, z in published:
        publish(wired, q, z)

    members = group()
    assert sorted(s for m in members for s in stages(m)) == \
        sorted(set(EVERY_SCENARIO) - set(published))

    for member in members:
        earlier = set()
        for s in member["inputs"]["scenarios"]:
            h = s["hotstart"]
            if h["bc_type"] == "KWSE":
                seed = (h["upstream_discharge"], h["bc_value"])
                assert seed in earlier or seed in published
            earlier.add((s["upstream_discharge"], s["bc_value"]))


# --- the scenarios themselves ------------------------------------------------

def test_candidates_come_from_both_downstream_proofs(wired):
    """A low target binds to the downstream ND run, higher ones to its libraries."""
    hrefs = [s["downstream_Scenario"] for s in all_scenarios()]
    assert any("/nd=9.0E03/" in h for h in hrefs)
    assert any("/kwse=" in h for h in hrefs)


def test_downstream_address_uses_the_imposed_stage_not_the_achieved_one(wired):
    """Our target 226.0 binds to a run that ACHIEVED 226.1 but sits in kwse=223.0.

    Note also which discharge appears in that address: the DOWNSTREAM run's, not
    ours. We are at q=200, and the nearest achieved stage downstream is 226.1
    from its q=900 run — nearer than its own q=200 run at 226.4. Our inflow and
    the downstream water surface are independent dimensions, which is the entire
    point of a stage library, so the two discharges need not agree — within
    what the rest of the basin can add, which in this fixture reaches past 900.
    """
    at_226 = next(s for s in all_scenarios() if s["upstream_discharge"] == 200
                  and s["bc_value"] == pytest.approx(226.0))
    assert "/kwse=223.0/q=900/scenario_manifest.json" in at_226["downstream_Scenario"]


def test_downstream_address_is_under_the_downstream_reach_and_model(wired):
    """Addressed by the downstream model's IDENTITY hash, with no domain code:
    the same grain the job writes at (guide.md)."""
    s = all_scenarios()[0]
    ds_identity, _, ds_domain = DS_MODEL.partition("_")
    assert f"/reach={DOWNSTREAM}/{ds_identity}/{RUN_HASH}/" in s["downstream_Scenario"]
    assert ds_domain not in s["downstream_Scenario"]


def test_each_job_starts_from_this_reach_nd_run_when_nothing_exists(wired):
    """ND seeds carry the slope of THIS reach, not the downstream one."""
    for q in (200, 900):
        first = member_for(q)["inputs"]["scenarios"][0]["hotstart"]
        assert first["bc_type"] == "ND"
        assert first["upstream_discharge"] == q
        assert first["bc_value"] == pytest.approx(12000.0)


def test_later_scenarios_seed_from_the_stage_below(wired):
    at_200 = member_for(200)["inputs"]["scenarios"]
    for previous, current in zip(at_200, at_200[1:]):
        assert current["hotstart"]["bc_type"] == "KWSE"
        assert current["hotstart"]["bc_value"] == pytest.approx(previous["bc_value"])


def test_hotstart_identity_hash_is_named_not_left_to_the_image(wired):
    """The job's default is baked into its image; this is the predicted hash."""
    for s in all_scenarios():
        assert s["hotstart"]["identity_hash"] == RUN_HASH


def test_a_terminal_reach_is_refused_rather_than_planned(wired, monkeypatch):
    """ISU-013: no downstream reach means no stage library can be bounded."""
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: intent_for(
        r, is_terminal=True, reach_to_id=None))
    with pytest.raises(RuntimeError, match="terminal"):
        group()


def test_an_unauthored_stage_increment_is_refused(wired, monkeypatch):
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: intent_for(
        r, ld_ds_z_delta=None))
    with pytest.raises(RuntimeError, match="ld_ds_z_delta"):
        group()


def test_authored_ceiling_shrinks_the_library(wired, monkeypatch):
    full = len(all_scenarios())
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: intent_for(
        r, kwse_upper_bound=225.0))
    assert len(all_scenarios()) < full


# --- the ceiling's basin inputs (DR-044 ALT-G) -----------------------------

def test_the_basin_reaches_the_planner(wired, monkeypatch):
    """Equal areas: nothing else drains into the downstream reach, so while we
    carry 200 it carries 200, and its q=900 runs are floods that cannot coincide.
    Stage 226 then binds to its own q=200 run (achieved 226.4, kwse=223.0)
    instead of the nearer q=900 one the uncapped plan picks."""
    monkeypatch.setitem(AREA, UPSTREAM, 1000.0)
    at_226 = next(s for s in all_scenarios() if s["upstream_discharge"] == 200
                  and s["bc_value"] == pytest.approx(226.0))
    assert "/kwse=223.0/q=200/scenario_manifest.json" in at_226["downstream_Scenario"]


def test_a_leftover_downstream_discharge_is_not_rounded_onto(wired, monkeypatch):
    """The downstream reach adopted 200 and 900, and an older sweep left a
    normal-depth run at 400. Holding 95% of its area, we leave 0.05^0.7 x 1000 =
    123 cms for everything else, so at q=200 the cap is 323. Rounding onto the
    leftover 400 would drop the q=900 stage runs and bind stage 226 to the q=200
    run; rounding onto the adopted 900 keeps the nearer q=900 run."""
    monkeypatch.setitem(AREA, UPSTREAM, 950.0)
    monkeypatch.setitem(globals(), "DS_CURVE",
                        [{"q": 200, "wse": 223.0}, {"q": 400, "wse": 224.0},
                         {"q": 900, "wse": 225.6}])
    at_226 = next(s for s in all_scenarios() if s["upstream_discharge"] == 200
                  and s["bc_value"] == pytest.approx(226.0))
    assert "/kwse=223.0/q=900/scenario_manifest.json" in at_226["downstream_Scenario"]


def test_more_area_than_the_downstream_reach_is_refused(wired, monkeypatch):
    monkeypatch.setitem(AREA, UPSTREAM, 1001.0)
    with pytest.raises(RuntimeError, match="only grows downstream"):
        group()


def test_an_unauthored_downstream_upper_bound_is_refused(wired, monkeypatch):
    """No fallback to the old single ceiling: the cause must stay visible."""
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: intent_for(
        r, **({"q_upper_bound": None} if r == DOWNSTREAM else {})))
    with pytest.raises(RuntimeError, match="q_upper_bound"):
        group()


def test_a_missing_drainage_area_is_refused(wired, monkeypatch):
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: intent_for(
        r, **({"total_da_sqkm": None} if r == DOWNSTREAM else {})))
    with pytest.raises(RuntimeError, match="drainage area"):
        group()


# --- checked against the job's own input model ---------------------------
# The jobs repo is a sibling checkout, not a dependency. Where it is importable,
# each member is validated by the very model the job will validate it with —
# which forbids extras, so a misspelled key fails here rather than at runtime.
try:
    from twod_fim_jobs.models.run_kwse_scenarios import RunKWSEScenariosInputs
except ImportError:  # pragma: no cover - depends on the developer's layout
    RunKWSEScenariosInputs = None

needs_jobs = pytest.mark.skipif(
    RunKWSEScenariosInputs is None, reason="twod-fim-jobs not importable")


@needs_jobs
def test_every_member_validates_against_the_real_job_input_model(wired):
    """Discharges must be whole and positive, stages parseable, seeds well formed."""
    publish(wired, 200, 223.0)          # one member resuming from a stored seed
    for member in group():
        parsed = RunKWSEScenariosInputs.model_validate(member["inputs"])
        assert parsed.scenarios
        for s in parsed.scenarios:
            assert isinstance(s.upstream_discharge, int) and s.upstream_discharge > 0
            assert s.downstream_Scenario.endswith("scenario_manifest.json")
            assert s.hotstart.bc_type in ("ND", "KWSE")
