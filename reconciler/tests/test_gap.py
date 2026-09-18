"""Tests for gap."""

import pytest

from recon.gap import (BUILD_MODEL, RUN_KWSE, RUN_ND, AwaitingInputs, InFlight, NoGap,
                       RunStep, Snapshot, AwaitingDownstream, calculate)


def terminal(**kw) -> Snapshot:
    return Snapshot(**{"reach_id": "1", "revision": 0, "is_terminal": True, **kw})


def upstream(**kw) -> Snapshot:
    return Snapshot(**{"reach_id": "2", "revision": 0, "is_terminal": False,
                       "downstream_reach_id": "9", **kw})


def built(**kw) -> Snapshot:
    return terminal(model_ok=True, **kw)


def test_terminal_with_nothing_builds_at_once():
    assert calculate(terminal()) == RunStep(step=BUILD_MODEL)

def test_upstream_waits_until_downstream_model_and_nd_exist():
    assert calculate(upstream()) == AwaitingDownstream(reach_id="9", step=BUILD_MODEL)

def test_downstream_model_alone_is_not_enough():
    assert calculate(upstream(ds_model_ok=True)) == AwaitingDownstream(reach_id="9", step=BUILD_MODEL)

def test_downstream_nd_alone_is_not_enough():
    assert calculate(upstream(ds_nd_ok=True)) == AwaitingDownstream(reach_id="9", step=BUILD_MODEL)

def test_upstream_builds_once_downstream_model_and_nd_are_proven():
    assert calculate(upstream(ds_model_ok=True, ds_nd_ok=True)) == RunStep(step=BUILD_MODEL)

def test_model_is_judged_before_nd():
    assert calculate(terminal(nd_ok=True)) == RunStep(step=BUILD_MODEL)


def test_terminal_runs_nd_once_its_model_exists():
    assert calculate(built()) == RunStep(step=RUN_ND)

def test_terminal_with_model_and_nd_is_satisfied():
    assert calculate(built(nd_ok=True)) == NoGap()

def test_terminal_naming_no_water_body_still_runs():
    assert calculate(built()) == RunStep(step=RUN_ND)

def test_upstream_nd_waits_for_the_downstream_library():
    snap = upstream(model_ok=True, ds_model_ok=True)
    assert calculate(snap) == AwaitingDownstream(reach_id="9", step=RUN_ND)

def test_upstream_runs_nd_once_the_downstream_library_is_proved():
    snap = upstream(model_ok=True, ds_model_ok=True, ds_nd_ok=True)
    assert calculate(snap) == RunStep(step=RUN_ND)

def test_upstream_nd_does_not_wait_on_downstream_kwse():
    snap = upstream(model_ok=True, ds_model_ok=True, ds_nd_ok=True, ds_kwse_ok=False)
    assert calculate(snap) == RunStep(step=RUN_ND)

def test_a_non_terminal_gets_its_boundary_from_downstream():
    snap = upstream(model_ok=True, ds_model_ok=True, ds_nd_ok=True)
    assert calculate(snap) == RunStep(step=RUN_ND)


def test_does_not_resubmit_work_in_flight():
    assert calculate(terminal(in_flight_step=BUILD_MODEL)) == InFlight(step=BUILD_MODEL)

def test_does_not_resubmit_nd_in_flight():
    assert calculate(built(in_flight_step=RUN_ND)) == InFlight(step=RUN_ND)

def test_output_appearing_beats_a_marker_left_behind():
    assert calculate(built(nd_ok=True, in_flight_step=RUN_ND)) == NoGap()

def test_a_finished_model_job_does_not_hold_up_the_next_rung():
    assert calculate(built(in_flight_step=BUILD_MODEL)) == InFlight(step=BUILD_MODEL)

def test_waiting_reach_with_marker_reports_in_flight():
    assert calculate(upstream(in_flight_step=BUILD_MODEL)) == InFlight(step=BUILD_MODEL)


@pytest.mark.parametrize("snap", [
    terminal(), built(), built(nd_ok=True), upstream(),
    upstream(ds_model_ok=True, ds_nd_ok=True),
    upstream(model_ok=True, in_flight_step=BUILD_MODEL),
])
def test_same_inputs_same_answer(snap):
    assert calculate(snap) == calculate(snap)

def test_snapshot_cannot_be_edited():
    with pytest.raises(Exception):
        terminal().model_ok = True  # type: ignore[misc]


def ready(**kw) -> Snapshot:
    return upstream(**{"model_ok": True, "nd_ok": True, "ds_model_ok": True,
                       "ds_nd_ok": True, "ds_kwse_ok": True,
                       "has_stage_increment": True, **kw})


def test_kwse_runs_once_everything_below_is_proved():
    assert calculate(ready()) == RunStep(step=RUN_KWSE)


def test_kwse_satisfied_is_the_end_of_the_ladder():
    assert calculate(ready(kwse_ok=True)) == NoGap()


def test_a_terminal_reach_is_finished_after_nd_not_awaiting_kwse():
    assert calculate(terminal(model_ok=True, nd_ok=True)) == NoGap()


def test_a_terminal_downstream_does_not_block_the_reach_above():
    snap = ready(ds_kwse_ok=False, ds_is_terminal=True)
    assert calculate(snap) == RunStep(step=RUN_KWSE)


def test_kwse_waits_for_the_downstream_stage_libraries():
    decision = calculate(ready(ds_kwse_ok=False))
    assert isinstance(decision, AwaitingDownstream) and decision.step == RUN_KWSE


@pytest.mark.parametrize("missing", ["ds_model_ok", "ds_nd_ok"])
def test_kwse_waits_if_the_downstream_reach_regresses(missing):
    decision = calculate(ready(**{missing: False}))
    assert isinstance(decision, AwaitingDownstream) and decision.step == RUN_KWSE


def test_no_stage_increment_awaits_a_person_rather_than_submitting():
    decision = calculate(ready(has_stage_increment=False))
    assert isinstance(decision, AwaitingInputs) and decision.step == RUN_KWSE


def test_a_missing_increment_outranks_a_missing_downstream_library():
    decision = calculate(ready(has_stage_increment=False, ds_kwse_ok=False))
    assert isinstance(decision, AwaitingInputs)


def test_an_in_flight_job_suppresses_the_kwse_rung_too():
    snap = ready(in_flight_step=RUN_KWSE)
    assert calculate(snap) == InFlight(step=RUN_KWSE)
