"""Tests for KWSE payload assembly."""

import itertools
from types import SimpleNamespace

import pytest

from recon import check, scenarios, storage

UPSTREAM, DOWNSTREAM = "100", "200"
OWN_MODEL = "5f14368c_N350S296E449W355"
DS_MODEL = "aa119e0c_N120S140E200W180"
RUN_HASH = "af1436c4"

CHAIN_200 = [(200, z) for z in (223.0, 224.0, 225.0, 226.0, 227.0)]
CHAIN_900 = [(900, z) for z in (226.0, 227.0)]
EVERY_SCENARIO = CHAIN_200 + CHAIN_900

DS_CURVE = [{"q": 200, "wse": 223.0}, {"q": 900, "wse": 225.6}]
DS_INDEX = [
    {"q": 200, "runs": [{"wse": 224.3, "bc": 221.0}, {"wse": 225.2, "bc": 222.0},
                        {"wse": 226.4, "bc": 223.0}, {"wse": 227.1, "bc": 224.0}]},
    {"q": 900, "runs": [{"wse": 226.1, "bc": 223.0}, {"wse": 226.9, "bc": 224.0}]},
]


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


def test_one_job_per_discharge_chain_in_discharge_order(wired):
    members = group()
    assert [m["tags"] for m in members] == [["q:200"], ["q:900"]]
    assert [stages(m) for m in members] == [CHAIN_200, CHAIN_900]


def test_every_member_is_a_complete_job_payload(wired):
    for member in group():
        assert set(member) == {"inputs", "tags"}
        inputs = member["inputs"]
        assert inputs["model_manifest_path"].endswith(f"{OWN_MODEL}/model_manifest.json")
        assert inputs["model_results_base_path"].endswith("/results")
        assert set(inputs) == {"model_manifest_path", "model_results_base_path", "scenarios",
                               "volume_convergence_tolerance", "allow_water_on_edges"}


def test_scenario_keys_match_the_job_input_model(wired):
    s = all_scenarios()[0]
    assert set(s) == {"upstream_discharge", "bc_value", "downstream_Scenario", "hotstart"}
    assert set(s["hotstart"]) == {"upstream_discharge", "bc_type", "bc_value",
                                  "identity_hash"}


def test_scenarios_already_in_storage_are_left_out(wired):
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
    for q, z in CHAIN_200:
        if z != 225.0:
            publish(wired, q, z)

    member = member_for(200)
    assert stages(member) == [(200, 225.0)]
    assert member["inputs"]["scenarios"][0]["hotstart"]["bc_value"] == 224.0


def test_a_refused_manifest_counts_as_missing(wired):
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


def test_candidates_come_from_both_downstream_proofs(wired):
    hrefs = [s["downstream_Scenario"] for s in all_scenarios()]
    assert any("/nd=9.0E03/" in h for h in hrefs)
    assert any("/kwse=" in h for h in hrefs)


def test_downstream_address_uses_the_imposed_stage_not_the_achieved_one(wired):
    at_226 = next(s for s in all_scenarios() if s["upstream_discharge"] == 200
                  and s["bc_value"] == pytest.approx(226.0))
    assert "/kwse=223.0/q=900/scenario_manifest.json" in at_226["downstream_Scenario"]


def test_downstream_address_is_under_the_downstream_reach_and_model(wired):
    s = all_scenarios()[0]
    ds_identity, _, ds_domain = DS_MODEL.partition("_")
    assert f"/reach={DOWNSTREAM}/{ds_identity}/{RUN_HASH}/" in s["downstream_Scenario"]
    assert ds_domain not in s["downstream_Scenario"]


def test_each_job_starts_from_this_reach_nd_run_when_nothing_exists(wired):
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
    for s in all_scenarios():
        assert s["hotstart"]["identity_hash"] == RUN_HASH


def test_a_terminal_reach_is_refused_rather_than_planned(wired, monkeypatch):
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


def test_the_basin_reaches_the_planner(wired, monkeypatch):
    monkeypatch.setitem(AREA, UPSTREAM, 1000.0)
    at_226 = next(s for s in all_scenarios() if s["upstream_discharge"] == 200
                  and s["bc_value"] == pytest.approx(226.0))
    assert "/kwse=223.0/q=200/scenario_manifest.json" in at_226["downstream_Scenario"]


def test_a_leftover_downstream_discharge_is_not_rounded_onto(wired, monkeypatch):
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
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: intent_for(
        r, **({"q_upper_bound": None} if r == DOWNSTREAM else {})))
    with pytest.raises(RuntimeError, match="q_upper_bound"):
        group()


def test_a_missing_drainage_area_is_refused(wired, monkeypatch):
    monkeypatch.setattr(check.intent, "effective", lambda r, **kw: intent_for(
        r, **({"total_da_sqkm": None} if r == DOWNSTREAM else {})))
    with pytest.raises(RuntimeError, match="drainage area"):
        group()


try:
    from twod_fim_jobs.models.run_kwse_scenarios import RunKWSEScenariosInputs
except ImportError:  # pragma: no cover - depends on the developer's layout
    RunKWSEScenariosInputs = None

needs_jobs = pytest.mark.skipif(
    RunKWSEScenariosInputs is None, reason="twod-fim-jobs not importable")


@needs_jobs
def test_every_member_validates_against_the_real_job_input_model(wired):
    publish(wired, 200, 223.0)
    for member in group():
        parsed = RunKWSEScenariosInputs.model_validate(member["inputs"])
        assert parsed.scenarios
        for s in parsed.scenarios:
            assert isinstance(s.upstream_discharge, int) and s.upstream_discharge > 0
            assert s.downstream_Scenario.endswith("scenario_manifest.json")
            assert s.hotstart.bc_type in ("ND", "KWSE")
