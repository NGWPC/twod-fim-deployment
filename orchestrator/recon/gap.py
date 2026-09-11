"""Gap calculation figures out what should exist for a reach, minus what does.

Gap calculation is deliberately agnostic to database, storage, clock,
configuration, and logging. It takes a Snapshot — everything a decision could
depend on, read in one pass by the caller — and returns a Decision. That is
what makes rule 7 of reconciliation-loop.md ("the same answer every time for
the same inputs") testable, and it means every reason the loop ever acts is
written in this one file, in the order the reasons are considered.

The reasons form a ladder, and every rung below the first depends on the reach
DOWNSTREAM, because results transfer upstream along the network:

  model   needs downstream's model AND nd materialized — this reach's geometry
          uses the downstream max-q STL
  nd      needs this model, and downstream's nd — the outflow polygon is the
          downstream max-q polygon
  kwse    needs this model and nd, and all three downstream — its stage
          targets are bound to real downstream runs, which have to exist

Terminal reaches get no KWSE at all: there is no downstream reach to bound a
stage library with (ISU-013, until lake and coastal stages are decided). That
cuts both ways, and the second way is easy to miss — a reach whose DOWNSTREAM
neighbour is terminal must not wait for a KWSE library that will never be built
there, or nothing in the network would ever reach the rung. A terminal
downstream counts as satisfied, having nothing to satisfy.

Terminal reaches skip every downstream condition — there is nothing below
them — which is why a fresh network starts building at its outlets.

"Materialized" here always means: that step's materialized_* row exists AND
proves the current revision. The snapshot loader reduces both to one boolean
per step, so this file never sees revisions of other reaches.
"""

from __future__ import annotations

from dataclasses import dataclass

BUILD_MODEL = "build_model"
RUN_ND = "run_nd_scenarios"
RUN_KWSE = "run_kwse_scenarios"


@dataclass(frozen=True)
class Snapshot:
    """What one reach looked like at the moment it was read.

    Frozen because a decision must be a function of a fixed set of facts.
    The ds_* booleans describe the DOWNSTREAM reach, judged against its own
    revision; they are False when there is no downstream intent or no proof,
    and meaningless (never read) when the reach is terminal.
    """

    reach_id: int
    revision: int
    is_terminal: bool
    downstream_reach_id: int | None = None
    # This reach's own proofs, judged against this snapshot's revision.
    model_ok: bool = False
    nd_ok: bool = False
    kwse_ok: bool = False
    # The downstream reach's proofs, judged against ITS revision.
    ds_model_ok: bool = False
    ds_nd_ok: bool = False
    ds_kwse_ok: bool = False
    # Whether the DOWNSTREAM reach is itself terminal, meaning its stage
    # libraries are settled by having none rather than by being built. Read only
    # by the kwse rung, and only once this reach is known not to be terminal:
    # a reach with no downstream at all reports FALSE here, which says nothing
    # true and is never looked at.
    ds_is_terminal: bool = False
    # Whether a stage increment resolves for this reach, from its own intent or
    # the defaults row. Nothing derives it (DR-033 ALT-B picks from a fixed
    # menu), so without one no stage grid exists to plan.
    has_stage_increment: bool = False
    # Job already submitted for this reach and not yet accounted for.
    in_flight_step: str | None = None


@dataclass(frozen=True)
class NoGap:
    """Everything this milestone asks for exists. The reach is satisfied."""


@dataclass(frozen=True)
class InFlight:
    """Something is needed, but a job for it is already running — do nothing."""

    step: str


@dataclass(frozen=True)
class AwaitingDownstream:
    """Something is needed but cannot start until the downstream reach catches up.

    The downstream reach requests a check here when it finishes, and the sweep
    finds this reach regardless, so nothing polls.
    """

    reach_id: int
    step: str  # the step that is blocked, for the activity log


@dataclass(frozen=True)
class AwaitingInputs:
    """Something is needed, and no job can produce it — data is missing.

    Named for who it waits on, because that is the only thing distinguishing it
    from AwaitingDownstream: that one resolves itself as the wave moves upstream,
    this one resolves when a person authors the missing data. Reporting it beats
    submitting a job that must fail, which would burn the retry budget and end at
    halted with a misleading error.
    """

    step: str
    reason: str


@dataclass(frozen=True)
class RunStep:
    """Something is needed and nothing stands in the way. Submit this job."""

    step: str


Decision = NoGap | InFlight | AwaitingDownstream | AwaitingInputs | RunStep


def _model_rung(s: Snapshot) -> Decision | None:
    """The model must exist before anything else can.

    Non-terminal reaches wait for the downstream model AND its nd library,
    because build_model transfers the downstream max-q STL into this reach's
    geometry. Terminals depend on nothing.
    """
    if s.model_ok:
        return None
    if s.in_flight_step is not None:
        return InFlight(step=s.in_flight_step)
    if not s.is_terminal and not (s.ds_model_ok and s.ds_nd_ok):
        return AwaitingDownstream(reach_id=s.downstream_reach_id, step=BUILD_MODEL)
    return RunStep(step=BUILD_MODEL)


def _nd_rung(s: Snapshot) -> Decision | None:
    """The normal-depth library, which needs a boundary to drain through.

    A non-terminal reach uses the downstream reach's max-q inundation polygon,
    so it waits for that library to be proved.

    A terminal reach never waits. Where it drains into a lake or the coast the
    schema guarantees it names which one, so that polygon is always there. Where
    it is a plain outlet it names nothing and needs nothing: the polygon input is
    optional and the job derives an outflow area from the model's own domain and
    centerline instead. An outlet is not a reach missing an input, it is a reach
    with none to give.
    """
    if s.nd_ok:
        return None
    if s.in_flight_step is not None:
        return InFlight(step=s.in_flight_step)
    if s.is_terminal:
        return RunStep(step=RUN_ND)
    if not s.ds_nd_ok:
        return AwaitingDownstream(reach_id=s.downstream_reach_id, step=RUN_ND)
    return RunStep(step=RUN_ND)


def _kwse_rung(s: Snapshot) -> Decision | None:
    """The stage libraries, which need real downstream runs to be bound to.

    Every target is forced by the water surface of an actual downstream
    simulation (DR-031), so this rung waits on all three of that reach's steps
    rather than just its nd library — a target may bind to either kind.

    Terminal reaches return satisfied, not blocked. They have no downstream
    reach to bound a library with, so there is no work here and never will be
    until lake and coastal stages are decided (ISU-013). Calling that
    AwaitingInputs would be wrong twice over: nothing a person authors on THIS
    reach would unblock it, and it would leave every reach above it waiting.

    The missing increment is checked before the downstream condition, matching
    reach_status, because it is the answer a person can act on and it is true
    regardless of what the network below is doing.
    """
    if s.is_terminal or s.kwse_ok:
        return None
    if s.in_flight_step is not None:
        return InFlight(step=s.in_flight_step)
    if not s.has_stage_increment:
        return AwaitingInputs(
            step=RUN_KWSE,
            reason="no stage increment authored on this reach or in the defaults")
    # All three downstream steps, judged against that reach's own revision. A
    # terminal neighbour has no kwse library to prove and is settled without one.
    if not (s.ds_model_ok and s.ds_nd_ok and (s.ds_is_terminal or s.ds_kwse_ok)):
        return AwaitingDownstream(reach_id=s.downstream_reach_id, step=RUN_KWSE)
    return RunStep(step=RUN_KWSE)


def calculate(snapshot: Snapshot) -> Decision:
    """What, if anything, should happen to this reach now.

    Needs are considered before the in-flight marker, on purpose: a job in
    flight suppresses *starting* work, it never makes a satisfied reach
    unsatisfied. A reach whose output has appeared reports NoGap even with its
    marker still set, which is exactly what lets the caller clear that marker.
    """
    decision = _model_rung(snapshot)
    if decision is not None:
        return decision

    decision = _nd_rung(snapshot)
    if decision is not None:
        return decision

    decision = _kwse_rung(snapshot)
    if decision is not None:
        return decision

    return NoGap()
