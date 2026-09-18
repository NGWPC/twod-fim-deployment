"""Gap calculation.

Takes a snapshot of one reach and returns what, if anything, is missing. Pure:
no database, storage, clock or configuration, so the same snapshot always gives
the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass

BUILD_MODEL = "build_model"
RUN_ND = "run_nd_scenarios"
RUN_KWSE = "run_kwse_scenarios"


@dataclass(frozen=True)
class Snapshot:
    """What one reach looked like at the moment it was read."""

    reach_id: str
    revision: int
    is_terminal: bool
    downstream_reach_id: str | None = None
    model_ok: bool = False
    nd_ok: bool = False
    kwse_ok: bool = False
    ds_model_ok: bool = False
    ds_nd_ok: bool = False
    ds_kwse_ok: bool = False
    ds_is_terminal: bool = False
    has_stage_increment: bool = False
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
    """Something is needed but cannot start until the downstream reach catches up."""

    reach_id: str
    step: str


@dataclass(frozen=True)
class AwaitingInputs:
    """Something is needed, and no job can produce it — data is missing."""

    step: str
    reason: str


@dataclass(frozen=True)
class RunStep:
    """Something is needed and nothing stands in the way. Submit this job."""

    step: str


Decision = NoGap | InFlight | AwaitingDownstream | AwaitingInputs | RunStep


def _model_rung(s: Snapshot) -> Decision | None:
    """The model must exist before anything else can."""
    if s.model_ok:
        return None
    if s.in_flight_step is not None:
        return InFlight(step=s.in_flight_step)
    if not s.is_terminal and not (s.ds_model_ok and s.ds_nd_ok):
        return AwaitingDownstream(reach_id=s.downstream_reach_id, step=BUILD_MODEL)
    return RunStep(step=BUILD_MODEL)


def _nd_rung(s: Snapshot) -> Decision | None:
    """The normal-depth library, which needs a boundary to drain through."""
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
    """The stage libraries, which need real downstream runs to be bound to."""
    if s.is_terminal or s.kwse_ok:
        return None
    if s.in_flight_step is not None:
        return InFlight(step=s.in_flight_step)
    if not s.has_stage_increment:
        return AwaitingInputs(
            step=RUN_KWSE,
            reason="no stage increment authored on this reach or in the defaults")
    if not (s.ds_model_ok and s.ds_nd_ok and (s.ds_is_terminal or s.ds_kwse_ok)):
        return AwaitingDownstream(reach_id=s.downstream_reach_id, step=RUN_KWSE)
    return RunStep(step=RUN_KWSE)


def calculate(snapshot: Snapshot) -> Decision:
    """What, if anything, should happen to this reach now."""
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
