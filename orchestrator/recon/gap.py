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
          downstream max-q polygon                           (next milestone)
  kwse    needs this model and nd, and all three downstream; terminal reaches
          have no downstream and get no KWSE at all          (next milestone)

Terminal reaches skip every downstream condition — there is nothing below
them — which is why a fresh network starts building at its outlets.

"Materialized" here always means: that step's materialized_* row exists AND
proves the current revision. The snapshot loader reduces both to one boolean
per step, so this file never sees revisions of other reaches.
"""

from __future__ import annotations

from dataclasses import dataclass

BUILD_MODEL = "build_model"


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
class WaitingDownstream:
    """Something is needed but cannot start until the downstream reach catches up.

    The downstream reach requests a check here when it finishes, and the sweep
    finds this reach regardless, so nothing polls.
    """

    reach_id: int
    step: str  # the step that is blocked, for the activity log


@dataclass(frozen=True)
class RunStep:
    """Something is needed and nothing stands in the way. Submit this job."""

    step: str


Decision = NoGap | InFlight | WaitingDownstream | RunStep


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
        return WaitingDownstream(reach_id=s.downstream_reach_id, step=BUILD_MODEL)
    return RunStep(step=BUILD_MODEL)


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

    # -- seam: the nd rung attaches here (next milestone) ------------------
    # if not snapshot.nd_ok:
    #     startable when terminal or ds_nd_ok; the outflow polygon comes from
    #     the downstream nd library's max-q run
    #
    # -- seam: the kwse rung attaches after nd -----------------------------
    # if not snapshot.is_terminal and not snapshot.kwse_ok:
    #     needs nd_ok here plus ds_model_ok, ds_nd_ok, ds_kwse_ok; bounds per
    #     DR-032 ALT-D come from the downstream materialized rows.
    #     Terminal reaches get no KWSE: there is no downstream to bound one.
    # ----------------------------------------------------------------------

    return NoGap()
