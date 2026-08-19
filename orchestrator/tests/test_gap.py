"""The gap calculation is pure, so its tests are plain data.

No database, no fixtures, no mocks — if a test here ever needs one, something
has leaked into gap.py that does not belong there.
"""

import pytest

from recon.gap import (BUILD_MODEL, InFlight, NoGap, RunStep, Snapshot,
                       WaitingDownstream, calculate)


def terminal(**kw) -> Snapshot:
    return Snapshot(**{"reach_id": 1, "revision": 0, "is_terminal": True, **kw})


def upstream(**kw) -> Snapshot:
    """A non-terminal reach; downstream reach 9 in whatever state kw says."""
    return Snapshot(**{"reach_id": 2, "revision": 0, "is_terminal": False,
                       "downstream_reach_id": 9, **kw})


# --- the model rung -----------------------------------------------------

def test_terminal_with_nothing_builds_at_once():
    assert calculate(terminal()) == RunStep(step=BUILD_MODEL)

def test_terminal_with_model_is_satisfied():
    assert calculate(terminal(model_ok=True)) == NoGap()

def test_upstream_waits_until_downstream_model_and_nd_exist():
    assert calculate(upstream()) == WaitingDownstream(reach_id=9, step=BUILD_MODEL)

def test_downstream_model_alone_is_not_enough():
    """The geometry transfer needs the downstream ND library, not just its model."""
    assert calculate(upstream(ds_model_ok=True)) == WaitingDownstream(reach_id=9, step=BUILD_MODEL)

def test_downstream_nd_alone_is_not_enough():
    assert calculate(upstream(ds_nd_ok=True)) == WaitingDownstream(reach_id=9, step=BUILD_MODEL)

def test_upstream_builds_once_downstream_model_and_nd_are_proven():
    assert calculate(upstream(ds_model_ok=True, ds_nd_ok=True)) == RunStep(step=BUILD_MODEL)

def test_upstream_with_model_ignores_downstream():
    """The dependency governs performing the work, not the proof: a model that
    exists (adopted from storage, or built before the rule) satisfies intent
    regardless of what downstream looks like."""
    assert calculate(upstream(model_ok=True)) == NoGap()


# --- the in-flight marker -----------------------------------------------

def test_does_not_resubmit_work_in_flight():
    assert calculate(terminal(in_flight_step=BUILD_MODEL)) == InFlight(step=BUILD_MODEL)

def test_output_appearing_beats_a_marker_left_behind():
    """Satisfied plus a stale marker must be NoGap, or the reach would sit
    in flight forever against a job that finished long ago."""
    assert calculate(terminal(model_ok=True, in_flight_step=BUILD_MODEL)) == NoGap()

def test_waiting_reach_with_marker_reports_in_flight():
    """A submitted job outranks waiting: the work is already underway, so the
    only correct action is to leave it alone."""
    assert calculate(upstream(in_flight_step=BUILD_MODEL)) == InFlight(step=BUILD_MODEL)


# --- discipline ---------------------------------------------------------

@pytest.mark.parametrize("snap", [
    terminal(), terminal(model_ok=True), upstream(),
    upstream(ds_model_ok=True, ds_nd_ok=True),
    upstream(model_ok=True, in_flight_step=BUILD_MODEL),
])
def test_same_inputs_same_answer(snap):
    assert calculate(snap) == calculate(snap)

def test_snapshot_cannot_be_edited():
    with pytest.raises(Exception):
        terminal().model_ok = True  # type: ignore[misc]
