"""Recording whether a reach's KWSE intent is materialized.

The subject is what counts as satisfied. A row here is proof, so the tests that
matter most are the ones about when NOT to write it — and the one about skipped
stage targets, which is the difference between a loop that settles and a loop
that resubmits the same work forever.

The database and bucket are stubbed; plan.py and scenarios.py are tested
separately, so what is under test is purely the judgement.
"""

import pytest

from recon import observe, plan, scenarios

REACH, DOWNSTREAM = 100, 200
MODEL = "5f14368c_N350S296E449W355"
RUN_HASH = "af1436c4"


def scenario(q, z, ds_wse, ds_bc, ds_type="KWSE"):
    return plan.PlannedScenario(
        q=q, z=z,
        downstream=plan.DownstreamRun(q=q, wse=ds_wse, bc_type=ds_type, bc_value=ds_bc),
        seed=plan.Seed(q=q, bc_type="ND", bc_value=12000.0))


def a_plan(scenarios_=(), skipped=()):
    return plan.Plan(scenarios=tuple(scenarios_), skipped=tuple(skipped), ceiling=230.0)


def context(p):
    return scenarios.Planned(
        plan=p, model_id=MODEL, run_identity_hash=RUN_HASH, nd_slope=12000.0,
        downstream_id=DOWNSTREAM, ds_model_id="aa119e0c_N1S1E1W1",
        ds_run_identity_hash=RUN_HASH)


@pytest.fixture
def wired(monkeypatch):
    """A reach whose model is materialized and matches what intent implies."""
    state = {"written": None, "deleted": False, "manifests": {}}

    monkeypatch.setattr(observe.intent, "effective",
                        lambda r, **kw: {"reach_id": r, "revision": 3})
    monkeypatch.setattr(observe.identity, "model_identity",
                        lambda w: ({}, "5f14368c"))
    monkeypatch.setattr(observe.identity, "verify_scenario_manifest",
                        lambda *a, **kw: [])

    def fake_one(sql, params=None, **kw):
        if "materialized_kwse_runs" in sql:
            return None                      # nothing recorded before
        if "materialized_models" in sql:
            return {"identity_hash": "5f14368c"}
        return None

    def fake_query(sql, params=None, **kw):
        if sql.strip().startswith("DELETE"):
            state["deleted"] = True
            return []
        state["written"] = params
        return []

    monkeypatch.setattr(observe.db, "one", fake_one)
    monkeypatch.setattr(observe.db, "query", fake_query)
    monkeypatch.setattr(observe.storage, "read_json",
                        lambda path: state["manifests"].get(path))
    monkeypatch.setattr(
        observe.storage, "scenario_manifest_path",
        lambda r, m, h, d: f"s3://b/reach={r}/{m}/{h}/{d}/scenario_manifest.json")
    return state


def publish(state, q, z, nominal_wse):
    """Pretend the job wrote this scenario's manifest."""
    folder = scenarios.scenario_dir("KWSE", z, q)
    state["manifests"][f"s3://b/reach={REACH}/{MODEL}/{RUN_HASH}/{folder}"
                       "/scenario_manifest.json"] = {
        "properties": {"nominal_wse": nominal_wse}}


# --- the reason this module exists --------------------------------------

def test_a_skipped_target_is_not_expected_and_does_not_block_the_row(wired, monkeypatch):
    """The livelock case. DR-033 skips a stage with no downstream run inside
    Δz/2, so asking for the whole grid would never be satisfied: the check would
    find the library short on every pass and resubmit forever."""
    p = a_plan([scenario(200, 224.0, 224.3, 221.0),
                scenario(200, 226.0, 226.1, 223.0)],
               skipped=[plan.SkippedTarget(q=200, z=225.0, nearest_wse=224.3,
                                           distance=0.7)])
    monkeypatch.setattr(scenarios, "planned", lambda r, **kw: context(p))
    publish(wired, 200, 224.0, 225.1)
    publish(wired, 200, 226.0, 227.4)

    result = observe.observe_kwse_runs(REACH)
    assert result["found"] == "2 scenarios"
    assert result["skipped"] == 1
    assert wired["written"] is not None


def test_an_empty_plan_is_materialized_rather_than_pending(wired, monkeypatch):
    """A reach asking for nothing has it. Recording that unblocks the reach
    above; withholding the row would stall the whole branch."""
    monkeypatch.setattr(scenarios, "planned", lambda r, **kw: context(a_plan()))
    result = observe.observe_kwse_runs(REACH)
    assert result["found"] == "0 scenarios"
    assert wired["written"] is not None


# --- a row is proof, so partial libraries write nothing ------------------

def test_a_missing_scenario_writes_no_row(wired, monkeypatch):
    p = a_plan([scenario(200, 224.0, 224.3, 221.0), scenario(200, 226.0, 226.1, 223.0)])
    monkeypatch.setattr(scenarios, "planned", lambda r, **kw: context(p))
    publish(wired, 200, 224.0, 225.1)          # the second never appears

    result = observe.observe_kwse_runs(REACH)
    assert result["found"] is None
    assert "no manifest yet" in result["note"]
    assert wired["written"] is None


def test_a_refused_manifest_writes_no_row(wired, monkeypatch):
    p = a_plan([scenario(200, 224.0, 224.3, 221.0)])
    monkeypatch.setattr(scenarios, "planned", lambda r, **kw: context(p))
    monkeypatch.setattr(observe.identity, "verify_scenario_manifest",
                        lambda *a, **kw: ["manifest reach_id 999 != 100"])
    publish(wired, 200, 224.0, 225.1)

    result = observe.observe_kwse_runs(REACH)
    assert result["found"] is None
    assert result["refused"]
    assert wired["written"] is None


def test_an_unplannable_reach_retracts_rather_than_writing(wired, monkeypatch):
    """Terminal reaches and reaches below an unfinished neighbour both land
    here; the gap calculation is what tells them apart."""
    def refuse(r, **kw):
        raise scenarios.NotPlannable("reach 100 is terminal")
    monkeypatch.setattr(scenarios, "planned", refuse)

    result = observe.observe_kwse_runs(REACH)
    assert result["found"] is None and "terminal" in result["note"]
    assert wired["deleted"] is True


def test_a_model_that_is_not_the_one_intent_implies_retracts(wired, monkeypatch):
    monkeypatch.setattr(observe.identity, "model_identity", lambda w: ({}, "deadbeef"))
    result = observe.observe_kwse_runs(REACH)
    assert "is not the deadbeef" in result["note"]


# --- what the row carries ------------------------------------------------

def test_the_index_pairs_achieved_stage_with_imposed_stage(wired, monkeypatch):
    """The two numbers the reach above needs: it matches on wse and addresses
    by bc, and they are not the same value."""
    p = a_plan([scenario(200, 224.0, 224.3, 221.0), scenario(900, 226.0, 226.1, 223.0)])
    monkeypatch.setattr(scenarios, "planned", lambda r, **kw: context(p))
    publish(wired, 200, 224.0, 225.1)
    publish(wired, 900, 226.0, 227.4)

    observe.observe_kwse_runs(REACH)
    import json
    index = json.loads(wired["written"][3])
    assert index == [{"q": 200, "runs": [{"wse": 225.1, "bc": 224.0}]},
                     {"q": 900, "runs": [{"wse": 227.4, "bc": 226.0}]}]


def test_runs_are_grouped_by_discharge_and_sorted_by_achieved_stage(wired, monkeypatch):
    p = a_plan([scenario(200, 226.0, 226.1, 223.0), scenario(200, 224.0, 224.3, 221.0)])
    monkeypatch.setattr(scenarios, "planned", lambda r, **kw: context(p))
    publish(wired, 200, 226.0, 227.4)
    publish(wired, 200, 224.0, 225.1)

    observe.observe_kwse_runs(REACH)
    import json
    index = json.loads(wired["written"][3])
    assert len(index) == 1
    assert [r["wse"] for r in index[0]["runs"]] == [225.1, 227.4]


def test_the_row_records_the_revision_it_proves(wired, monkeypatch):
    monkeypatch.setattr(scenarios, "planned", lambda r, **kw: context(a_plan()))
    observe.observe_kwse_runs(REACH)
    assert wired["written"][4] == 3          # intent revision
