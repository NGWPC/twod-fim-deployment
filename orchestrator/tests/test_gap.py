"""The gap calculation is pure, so its tests are plain data.

No database, no fixtures, no mocks — if a test here ever needs one, something
has leaked into gap.py that does not belong there.
"""

import pytest

from recon.gap import BUILD_MODEL, InFlight, NoGap, RunStep, Snapshot, calculate


def snapshot(**overrides) -> Snapshot:
    """A reach with nothing built and nothing running, unless stated otherwise."""
    return Snapshot(**{"reach_id": 1, "revision": 0, "has_model": False, **overrides})


def test_missing_model_is_work_to_do():
    assert calculate(snapshot()) == RunStep(step=BUILD_MODEL)


def test_existing_model_is_no_gap():
    assert calculate(snapshot(has_model=True)) == NoGap()


def test_does_not_resubmit_work_already_in_flight():
    decision = calculate(snapshot(in_flight_step=BUILD_MODEL))
    assert decision == InFlight(step=BUILD_MODEL)


def test_output_appearing_beats_a_marker_left_behind():
    """The job finished and observe recorded its output, but the marker is still set.

    This must report NoGap rather than InFlight. Reporting InFlight would be
    self-sustaining: the caller clears the marker when the gap is empty, so a
    reach that never reports an empty gap never gets its marker cleared, and
    would sit in flight forever against a job that finished long ago.
    """
    decision = calculate(snapshot(has_model=True, in_flight_step=BUILD_MODEL))
    assert decision == NoGap()


def test_revision_does_not_change_the_decision():
    """A need is a need at any revision; the revision is carried for the caller."""
    assert calculate(snapshot(revision=0)) == calculate(snapshot(revision=41))


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"has_model": True},
        {"in_flight_step": BUILD_MODEL},
        {"has_model": True, "in_flight_step": BUILD_MODEL},
    ],
)
def test_same_inputs_give_the_same_answer(state):
    """Rule 7 of reconciliation-loop.md, checked rather than assumed."""
    first = calculate(snapshot(**state))
    second = calculate(snapshot(**state))
    assert first == second


def test_snapshot_cannot_be_edited():
    """Frozen, so a decision cannot be made against facts that shifted underneath it."""
    s = snapshot()
    with pytest.raises(Exception):
        s.has_model = True  # type: ignore[misc]
