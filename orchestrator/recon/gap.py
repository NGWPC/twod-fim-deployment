"""Gap calculation figures out what should exist for a reach, minus what does.

Gap calculation is deliberately agnostic to database, storage, clock, configuration, and logging.
It takes a Snapshot, everything a decision could depend on, and returns a Decision.

This file stores full logic of gap calculation in order.
The gap calculation is per reach.
"""

from __future__ import annotations

from dataclasses import dataclass

BUILD_MODEL = "build_model"


@dataclass(frozen=True)
class Snapshot:
    """What one reach looked like at the moment it was read.

    Frozen because a decision must be a function of a fixed set of facts. If
    this could be edited between being read and being used, "the same inputs"
    would stop meaning anything.
    """

    reach_id: int
    # desired_state.revision at the time of reading. calculate() does not read
    # this — a need is a need at any revision — but the caller records it when
    # the gap turns out to be empty, and it must be the revision this snapshot
    # was taken at rather than whatever the table says later.
    revision: int
    # A model was seen in storage for this reach. Set by observe, never by a
    # job's return value.
    has_model: bool
    # A job was submitted for this reach and has not yet been accounted for.
    # At most one per reach, so this is the job, not a list of them.
    in_flight_step: str | None = None


@dataclass(frozen=True)
class NoGap:
    """Everything that should exist does. The reach is satisfied at its revision."""


@dataclass(frozen=True)
class InFlight:
    """Something is still needed, but a job for it is already running.

    The answer is to do nothing — not because the reach is finished, but
    because starting the same work again would only waste a solver run.
    """

    step: str


@dataclass(frozen=True)
class RunStep:
    """Something is needed and nothing is working on it. Submit this job."""

    step: str


# The document lists a fourth answer, WaitingDownstream, raised when KWSE work
# cannot start until the downstream reach has finished. It is absent here on
# purpose: only the KWSE rule can produce it, KWSE is not in milestone 1, and
# the Snapshot deliberately carries no downstream fields to produce it from. It
# arrives with that rule, together with the two fields it needs.
Decision = NoGap | InFlight | RunStep


def _first_unmet_need(snapshot: Snapshot) -> str | None:
    """The first job whose output is missing, or None if nothing is missing.

    Ordered, and the order is the dependency order: a model before runs against
    that model. Deliberately says nothing about whether work is already
    underway — that is a separate question, asked once, in calculate().

    The seam: ND attaches after the model rule, KWSE after ND. Each is the same
    shape — "is this output missing?" — except KWSE, which must also ask whether
    the downstream reach has finished, and returns WaitingDownstream when it has
    not.
    """
    if not snapshot.has_model:
        return BUILD_MODEL
    return None


def calculate(snapshot: Snapshot) -> Decision:
    """What, if anything, should happen to this reach now.

    Needs are worked out first and jobs second, on purpose. A job in flight
    suppresses *starting* work; it does not make a satisfied reach unsatisfied.
    So a reach whose output has appeared reports NoGap even though its marker is
    still set — which is exactly what lets the caller clear that marker.
    """
    need = _first_unmet_need(snapshot)
    if need is None:
        return NoGap()
    if snapshot.in_flight_step is not None:
        return InFlight(step=snapshot.in_flight_step)
    return RunStep(step=need)
