"""The gap calculation is pure, so its tests are plain data.

No database, no fixtures, no mocks — if a test here ever needs one, something
has leaked into gap.py that does not belong there.
"""

import pytest

from recon.gap import (BUILD_MODEL, RUN_ND, InFlight, NoGap, RunStep, Snapshot,
                       WaitingDownstream, calculate)


def terminal(**kw) -> Snapshot:
    """An outlet reach that drains into a lake, so it has a boundary polygon."""
    return Snapshot(**{"reach_id": 1, "revision": 0, "is_terminal": True,
                       "has_outflow_polygon": True, **kw})


def upstream(**kw) -> Snapshot:
    """A non-terminal reach; downstream reach 9 in whatever state kw says."""
    return Snapshot(**{"reach_id": 2, "revision": 0, "is_terminal": False,
                       "downstream_reach_id": 9, **kw})


def built(**kw) -> Snapshot:
    """A terminal past the model rung, so the nd rung is what answers."""
    return terminal(model_ok=True, **kw)


# --- the model rung -----------------------------------------------------

def test_terminal_with_nothing_builds_at_once():
    assert calculate(terminal()) == RunStep(step=BUILD_MODEL)

def test_upstream_waits_until_downstream_model_and_nd_exist():
    assert calculate(upstream()) == WaitingDownstream(reach_id=9, step=BUILD_MODEL)

def test_downstream_model_alone_is_not_enough():
    """The geometry transfer needs the downstream ND library, not just its model."""
    assert calculate(upstream(ds_model_ok=True)) == WaitingDownstream(reach_id=9, step=BUILD_MODEL)

def test_downstream_nd_alone_is_not_enough():
    assert calculate(upstream(ds_nd_ok=True)) == WaitingDownstream(reach_id=9, step=BUILD_MODEL)

def test_upstream_builds_once_downstream_model_and_nd_are_proven():
    assert calculate(upstream(ds_model_ok=True, ds_nd_ok=True)) == RunStep(step=BUILD_MODEL)

def test_model_is_judged_before_nd():
    """A reach with no model asks for a model, whatever its nd state."""
    assert calculate(terminal(nd_ok=True)) == RunStep(step=BUILD_MODEL)


# --- the nd rung --------------------------------------------------------

def test_terminal_runs_nd_once_its_model_exists():
    assert calculate(built()) == RunStep(step=RUN_ND)

def test_terminal_with_model_and_nd_is_satisfied():
    assert calculate(built(nd_ok=True)) == NoGap()

def test_terminal_without_a_water_body_runs_without_a_polygon():
    """The job derives the boundary when the optional polygon is absent."""
    decision = calculate(built(has_outflow_polygon=False))
    assert decision == RunStep(step=RUN_ND)

def test_upstream_nd_waits_for_the_downstream_library():
    """The outflow polygon is the downstream reach's max-q inundated area, so
    its library has to be proved before this one can start."""
    snap = upstream(model_ok=True, ds_model_ok=True)
    assert calculate(snap) == WaitingDownstream(reach_id=9, step=RUN_ND)

def test_upstream_runs_nd_once_the_downstream_library_is_proved():
    snap = upstream(model_ok=True, ds_model_ok=True, ds_nd_ok=True)
    assert calculate(snap) == RunStep(step=RUN_ND)

def test_upstream_nd_does_not_wait_on_downstream_kwse():
    """Only kwse waits on downstream kwse; nd needs the nd library alone."""
    snap = upstream(model_ok=True, ds_model_ok=True, ds_nd_ok=True, ds_kwse_ok=False)
    assert calculate(snap) == RunStep(step=RUN_ND)

def test_a_non_terminal_needs_no_polygon_of_its_own():
    """has_outflow_polygon describes water bodies and is read only for
    terminals; upstream reaches get their boundary from downstream."""
    snap = upstream(model_ok=True, ds_model_ok=True, ds_nd_ok=True,
                    has_outflow_polygon=False)
    assert calculate(snap) == RunStep(step=RUN_ND)


# --- the in-flight marker -----------------------------------------------

def test_does_not_resubmit_work_in_flight():
    assert calculate(terminal(in_flight_step=BUILD_MODEL)) == InFlight(step=BUILD_MODEL)

def test_does_not_resubmit_nd_in_flight():
    assert calculate(built(in_flight_step=RUN_ND)) == InFlight(step=RUN_ND)

def test_output_appearing_beats_a_marker_left_behind():
    """Satisfied plus a stale marker must be NoGap, or the reach would sit
    in flight forever against a job that finished long ago."""
    assert calculate(built(nd_ok=True, in_flight_step=RUN_ND)) == NoGap()

def test_a_finished_model_job_does_not_hold_up_the_next_rung():
    """The marker belongs to the model job that just proved model_ok. The nd
    rung must still report it in flight rather than submitting alongside it —
    one job per reach at a time — and the check clears it on the next pass."""
    assert calculate(built(in_flight_step=BUILD_MODEL)) == InFlight(step=BUILD_MODEL)

def test_waiting_reach_with_marker_reports_in_flight():
    """A submitted job outranks waiting: the work is already underway, so the
    only correct action is to leave it alone."""
    assert calculate(upstream(in_flight_step=BUILD_MODEL)) == InFlight(step=BUILD_MODEL)


# --- discipline ---------------------------------------------------------

@pytest.mark.parametrize("snap", [
    terminal(), built(), built(nd_ok=True), upstream(),
    upstream(ds_model_ok=True, ds_nd_ok=True),
    built(has_outflow_polygon=False),
    upstream(model_ok=True, in_flight_step=BUILD_MODEL),
])
def test_same_inputs_same_answer(snap):
    assert calculate(snap) == calculate(snap)

def test_snapshot_cannot_be_edited():
    with pytest.raises(Exception):
        terminal().model_ok = True  # type: ignore[misc]
