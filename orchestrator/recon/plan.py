"""Which KWSE scenarios a reach should run, and what seeds each one.

Pure, like gap.py: it takes what was read in one pass and returns a decision,
with no database, storage, clock or logging of its own. That is what makes the
two decision records below testable against their own worked examples.

Two decisions meet here.

DR-032 ALT-D sets the envelope. The ceiling is the downstream reach's highest
upstream-end stage — ONE value covering every discharge. The floor rises with
discharge: at our discharge q it is the downstream reach's lowest upstream-end
stage at the nearest downstream discharge at or below q. It is deliberately NOT
floored by this reach's own normal depth; that was ALT-C, dropped in July 2026
after the Ohio Ripple1D work showed a too-flat slope pushing normal-depth stages
above the downstream reach's own, which stitches into an artificial bump once
Flows2FIM joins the network up.

DR-033 ALT-B fills the envelope. Stages step by a fixed increment from the menu
`{0.25, 0.5, 1, 2, 5}`, on a grid anchored to zero rather than to the reach's own
values, so stages are consistent across the whole network. Both bounds are
ROUNDED to the nearest increment — not floored and ceiled — which is what the
DR's own examples show, and it means the grid may sit up to half an increment
outside the envelope at either end. That is not a bug: half an increment is
exactly the binding tolerance below, so an edge target still finds a run.

Binding is the part with no obvious shape until you notice that every downstream
run carries two different stages:

  achieved  the stage the solver produced at the downstream reach's UPSTREAM
            end. This is the water level we will actually see at our own
            downstream end, so it is what a target matches against.
  imposed   the stage that was pushed onto the downstream reach's OWN downstream
            end. This is what named its folder, so it is what an address is
            built from.

They differ by however much the water surface rose across that reach. Neither
can be computed from the other, so a plan has to carry both — matching on one and
addressing with the other.

One asymmetry is easy to get wrong: a single downstream discharge sets the FLOOR,
but every downstream run is a candidate for BINDING. The downstream reach reaches
its highest stages only at its highest discharges, so restricting the pool to one
discharge would put the top of the envelope permanently out of reach. It fails
hardest on a small tributary joining a large mainstem — the case where DR-033
says backwater runs matter most, and where a low flow in the tributary genuinely
does coincide with the mainstem in flood.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

# The stage increments DR-033 ALT-B allows. Mirrored by a CHECK constraint on
# desired_state.ld_ds_z_delta, so a value off the menu never reaches this far.
DZ_MENU = (0.25, 0.5, 1.0, 2.0, 5.0)

# Slack for float comparison. Stages are multiples of an exactly representable
# increment, so this only absorbs the accumulation in `lo + i * dz`.
_EPS = 1e-9


@dataclass(frozen=True)
class DownstreamRun:
    """One scenario of the DOWNSTREAM reach, as a candidate boundary for ours.

    Both kinds belong in the same pool. A low target often binds to that reach's
    normal-depth run, a higher one to its stage libraries, and the ladder makes
    no distinction — it is simply whichever achieved stage sits nearest.
    """

    q: int
    wse: float  # achieved at that reach's upstream end: what we match on
    bc_type: Literal["ND", "KWSE"]
    bc_value: float  # imposed at its downstream end: what names its folder


@dataclass(frozen=True)
class Seed:
    """A scenario of THIS reach whose depth grid hot-starts another of ours.

    Coordinates rather than a path, because the job rebuilds the address itself
    and is the only place that knows how a scenario folder is spelled.
    """

    q: int
    bc_type: Literal["ND", "KWSE"]
    bc_value: float


@dataclass(frozen=True)
class PlannedScenario:
    """One scenario to run: a point on the grid, its boundary, and its seed."""

    q: int
    z: float  # the grid stage; becomes bc_value and names our folder
    downstream: DownstreamRun
    seed: Seed


@dataclass(frozen=True)
class SkippedTarget:
    """A grid stage with no downstream run near enough to force it.

    DR-033 treats these as gaps in the DOWNSTREAM reach's sampling rather than
    errors, and calls the interval actually achieved a quality metric — so they
    are returned rather than dropped silently.
    """

    q: int
    z: float
    nearest_wse: float | None
    distance: float | None


@dataclass(frozen=True)
class Plan:
    """Everything the payload builder needs, plus what was left out and why."""

    scenarios: tuple[PlannedScenario, ...]
    skipped: tuple[SkippedTarget, ...]
    ceiling: float


def _snap(value: float, dz: float) -> float:
    """The nearest multiple of dz, with the grid anchored at zero.

    Anchored absolutely, not to the reach's own bounds: DR-033 is explicit that
    a dz of 1 lands on 585, 586 rather than 585.5, 586.5, which is what makes a
    stage mean the same thing on every reach in the network.
    """
    return round(value / dz) * dz


def _floor(downstream: Sequence[DownstreamRun], q: int) -> float:
    """The lowest stage worth modelling at our discharge q.

    DR-032 ALT-D reads the downstream reach's minimum at the nearest downstream
    discharge AT OR BELOW ours. That reach drains more area, so its discharges
    are generally higher and there may be none at or below; the curve is then
    clamped to its lowest, which the DR does not cover and is the one
    interpretation added here. It is also nearly free of consequence: stage rises
    with discharge, so a reach's overall minimum normally sits at its lowest
    discharge anyway.

    This is the ONLY place a single downstream discharge is selected. Binding a
    target to a run deliberately does not — see plan().
    """
    at_or_below = [r.q for r in downstream if r.q <= q]
    q_ds = max(at_or_below) if at_or_below else min(r.q for r in downstream)
    return min(r.wse for r in downstream if r.q == q_ds)


def plan(
    q_set: Sequence[int],
    dz: float,
    downstream: Sequence[DownstreamRun],
    nd_slope: float,
    kwse_upper_bound: float | None = None,
) -> Plan:
    """The KWSE scenarios this reach should run, in the order they must run.

    `q_set` is this reach's own normal-depth discharges, read back from its
    materialization because the adaptive sweep chose them. `nd_slope` is the
    slope naming this reach's own `nd=` folder, which roots every chain.

    Order is load-bearing. The job runs scenarios serially and a seed must
    already exist when it is named, so each discharge forms its own chain rooted
    in this reach's normal-depth run at that same discharge — the closest
    starting point available, and the reason no scenario starts dry.
    """
    if dz <= 0:
        raise ValueError(f"stage increment must be positive, got {dz}")
    if not downstream:
        raise ValueError("no downstream runs to bound a stage library with")

    # ONE ceiling for every discharge (DR-032 ALT-D). Authored intent can only
    # lower it: kwse_upper_bound is a cap on what to model, never a licence to
    # model above what the downstream reach ever reached.
    ceiling = max(r.wse for r in downstream)
    if kwse_upper_bound is not None:
        ceiling = min(ceiling, kwse_upper_bound)

    scenarios: list[PlannedScenario] = []
    skipped: list[SkippedTarget] = []

    for q in sorted(q_set):
        floor = _floor(downstream, q)

        lo, hi = _snap(floor, dz), _snap(ceiling, dz)
        if lo > hi + _EPS:
            # The envelope closed: at this discharge the downstream reach never
            # sat below the ceiling. Nothing to run, and not a failure.
            continue

        # Built by index rather than by repeated addition, so the last stage is
        # as exact as the first.
        steps = int(round((hi - lo) / dz))
        previous: float | None = None

        for i in range(steps + 1):
            z = _snap(lo + i * dz, dz)
            # EVERY downstream run is a candidate, not just those at the
            # discharge that set the floor. What a target needs is a water
            # surface at that stage, and the downstream reach reaches its
            # highest stages only at its highest discharges — so restricting the
            # pool would make the top of the envelope unreachable, and would hurt
            # worst exactly where DR-033 says backwater runs matter most: a small
            # tributary joining a large mainstem.
            #
            # Ties go to the lower discharge, which is the smaller footprint and
            # keeps the answer deterministic. DR-033 does not settle ties.
            nearest = min(downstream, key=lambda r: (abs(r.wse - z), r.q))
            distance = abs(nearest.wse - z)

            if distance > dz / 2 + _EPS:
                skipped.append(SkippedTarget(q=q, z=z, nearest_wse=nearest.wse,
                                             distance=distance))
                continue

            # The first stage of a discharge has no lower stage to chain from,
            # so it seeds from this reach's own normal-depth run at the same
            # discharge. Every later stage seeds from the one below it.
            seed = (Seed(q=q, bc_type="KWSE", bc_value=previous)
                    if previous is not None
                    else Seed(q=q, bc_type="ND", bc_value=nd_slope))

            scenarios.append(PlannedScenario(q=q, z=z, downstream=nearest, seed=seed))
            previous = z

    return Plan(scenarios=tuple(scenarios), skipped=tuple(skipped), ceiling=ceiling)
