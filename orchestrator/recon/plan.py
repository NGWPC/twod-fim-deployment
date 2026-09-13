"""Which KWSE scenarios a reach should run, and what seeds each one.

Pure, like gap.py: it takes what was read in one pass and returns a decision,
with no database, storage, clock or logging of its own. That is what makes the
decision records below testable against their own worked examples.

Several decisions meet here.

DR-042 and DR-043 set the envelope, one bound each, and both answer the same
question: while this reach carries q, what can the downstream reach be carrying?

  floor    DR-042 ALT-D. About q — everything else draining into it brings
           nothing. The downstream reach's lowest upstream-end stage at its
           nearest discharge at or below q. Deliberately NOT floored by this
           reach's own normal depth; that was DR-042 ALT-C, dropped in July 2026
           after the Ohio Ripple1D work showed a too-flat slope pushing
           normal-depth stages above the downstream reach's own, which stitches
           into an artificial bump once Flows2FIM joins the network up.
  ceiling  DR-043 ALT-F. q + others — everything else is in flood. `others`
           comes from drainage area alone (DR-044 ALT-G, see others()), with an
           exponent DR-045 settles, and the ceiling is the highest stage the
           downstream reach reached at any discharge up to that flow.

Until DR-043 ALT-F the ceiling was a single value for every discharge, its
ALT-A: the downstream reach's highest stage EVER, which a trickle here cannot
produce.

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
but a RANGE of downstream discharges is the candidate pool for BINDING — every
one up to the discharge the ceiling was read at. The downstream reach reaches its
highest stages only at its higher discharges, so restricting the pool to the
floor's discharge would put the top of the envelope permanently out of reach.
It fails hardest on a small tributary joining a large mainstem — the case where
DR-033 says backwater runs matter most, and where a low flow in the tributary
genuinely does coincide with the mainstem in flood. The range stops where the
ceiling stops for the same reason the ceiling does: above it, the downstream
reach is carrying a flow that cannot coincide with ours.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

# The stage increments DR-033 ALT-B allows. Mirrored by a CHECK constraint on
# desired_state.ld_ds_z_delta, so a value off the menu never reaches this far.
DZ_MENU = (0.25, 0.5, 1.0, 2.0, 5.0)

# The exponent turning a drainage-area share into a flood-flow share (DR-045
# ALT-F, used by DR-044 ALT-G): the drainage-area-ratio method's exponent, also
# called the flood scaling exponent. Small catchments yield more flood flow per
# km² than large ones, so the flow share of an area share is larger than the
# share itself.
#
# Fitted as ln(Q100) = a + b·ln(DA) over the 41-reach test network: 0.62, or
# 0.66 without the lake-trimmed and lake-terminal reaches. One network-wide
# constant rather than a per-reach tunable, and LOWER IS SAFER — a smaller
# exponent credits the rest of the basin with more flow, which raises ceilings.
# At 0.8 and above planned runs start falling fast, and the runs lost are
# reachable ones; 1.0 (plain area ratio) is the uniform-yield assumption the fit
# rejects. Do not "simplify" this away.
DAR_EXPONENT = 0.7

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
class Ceiling:
    """How high one discharge's stage library goes, and where that came from.

    Kept for every discharge, including one whose envelope closed, because a
    ceiling is the one bound here that is estimated rather than read — so it is
    the one a person will want to retrace.
    """

    q: int
    cap: float  # the most the downstream reach can carry while we carry q
    read_q: int  # the downstream library discharge its runs were read up to, inclusive
    wse: float  # the highest stage among them, after kwse_upper_bound


@dataclass(frozen=True)
class Plan:
    """Everything the payload builder needs, plus what was left out and why."""

    scenarios: tuple[PlannedScenario, ...]
    skipped: tuple[SkippedTarget, ...]
    ceilings: tuple[Ceiling, ...]


def _snap(value: float, dz: float) -> float:
    """The nearest multiple of dz, with the grid anchored at zero.

    Anchored absolutely, not to the reach's own bounds: DR-033 is explicit that
    a dz of 1 lands on 585, 586 rather than 585.5, 586.5, which is what makes a
    stage mean the same thing on every reach in the network.
    """
    return round(value / dz) * dz


def _floor(downstream: Sequence[DownstreamRun], q: int) -> float:
    """The lowest stage worth modelling at our discharge q.

    DR-042 ALT-D reads the downstream reach's minimum at the nearest downstream
    discharge AT OR BELOW ours. That reach drains more area, so its discharges
    are generally higher and there may be none at or below; the curve is then
    clamped to its lowest, as the DR describes. It is also nearly free of
    consequence: stage rises with discharge, so a reach's overall minimum
    normally sits at its lowest discharge anyway.

    The floor is read at ONE downstream discharge. The ceiling is read across a
    range of them, and binding shares that range — see _read_q() and plan().
    """
    at_or_below = [r.q for r in downstream if r.q <= q]
    q_ds = max(at_or_below) if at_or_below else min(r.q for r in downstream)
    return min(r.wse for r in downstream if r.q == q_ds)


def others(own_da: float, downstream_da: float, downstream_q_upper: float) -> float:
    """The most flow everything ELSE can add to the downstream reach, in cms.

    "Everything else" is every other reach draining into it plus its own local
    area, including tributaries the modelled network dropped — all of it is the
    downstream reach's drainage area minus ours, so none of it is read reach by
    reach:

        (1 − own_da / downstream_da) ^ DAR_EXPONENT × downstream_q_upper

    The drainage-area-ratio method (DR-044 ALT-G), applied to the catchment
    that is not ours: the downstream reach's upper discharge, transferred to
    that catchment's area. The method is normally used between sites of
    comparable size and here it is not, which is acceptable only because the
    result is used as a conservative bound rather than an estimate, and the
    exponent keeps its error on the high side.

    Equal areas add nothing — the downstream reach carries exactly what we send.
    More area here than downstream is refused rather than clamped: drainage area
    only grows downstream, so it means the inputs are wrong, and a silent zero
    would lower ceilings on a reach nobody knows is broken.
    """
    if own_da <= 0 or downstream_da <= 0:
        raise ValueError(
            f"drainage areas must be positive, got {own_da} and {downstream_da} km²")
    if own_da > downstream_da:
        raise ValueError(
            f"drainage area {own_da} km² exceeds the downstream reach's "
            f"{downstream_da} km², but drainage area only grows downstream")
    if downstream_q_upper <= 0:
        raise ValueError(
            f"downstream upper discharge must be positive, got {downstream_q_upper}")
    return (1.0 - own_da / downstream_da) ** DAR_EXPONENT * downstream_q_upper


def _read_q(downstream_q_set: Sequence[int], cap: float) -> int:
    """The downstream discharge whose runs, and every lower one's, bound a ceiling.

    DR-043 ALT-F. The first LIBRARY discharge at or above the cap — one the
    downstream reach adopted, not merely one it has a run at. The downstream
    reach really can be carrying the cap, but its library rarely has a run at
    exactly that flow, and the library discharge above is the nearest flow at
    which it modelled every downstream condition. Rounding up errs toward one
    extra stage rather than a missing one — the mirror of the floor, which
    rounds down — and overshoots by at most one step of the downstream library,
    which DR-030's resolution bands keep small.

    Why adopted discharges only. Storage also holds normal-depth runs an older
    sweep left at discharges the library never adopted, and the downstream
    reach's stage libraries exist only at adopted ones. Rounding onto a leftover
    would read backwater only up to the adopted discharge BELOW the cap, and the
    downstream reach's real highest stage at the cap lies above that — the
    ceiling would come out low, which is the unsafe direction. It would also tie
    the plan to runs that exist only until someone cleans them up. That was
    DR-043 ALT-E, and it did both on the test network.

    A cap above the largest library discharge reads everything: the downstream
    library stops there, and so does anything we could impose from it.
    """
    at_or_above = [q for q in downstream_q_set if q >= cap - _EPS]
    return min(at_or_above) if at_or_above else max(downstream_q_set)


def plan(
    q_set: Sequence[int],
    dz: float,
    downstream: Sequence[DownstreamRun],
    nd_slope: float,
    kwse_upper_bound: float | None = None,
    *,
    others: float,
    downstream_q_set: Sequence[int],
) -> Plan:
    """The KWSE scenarios this reach should run, in the order they must run.

    `q_set` is this reach's own normal-depth discharges, read back from its
    materialization because the adaptive sweep chose them. `nd_slope` is the
    slope naming this reach's own `nd=` folder, which roots every chain.
    `others` is what everything else can add to the downstream reach, from
    others(); it has no default, because a ceiling with nothing conditioning it
    is DR-043 ALT-A's, and falling back to that would hide whatever made it missing.
    `downstream_q_set` is the downstream reach's adopted library discharges,
    the only ones a cap rounds up onto (see _read_q()).

    Order is load-bearing. The job runs scenarios serially and a seed must
    already exist when it is named, so each discharge forms its own chain rooted
    in this reach's normal-depth run at that same discharge — the closest
    starting point available, and the reason no scenario starts dry.
    """
    if dz <= 0:
        raise ValueError(f"stage increment must be positive, got {dz}")
    if not downstream:
        raise ValueError("no downstream runs to bound a stage library with")
    if not others >= 0:  # also refuses NaN
        raise ValueError(f"others must be a non-negative flow, got {others}")
    if not downstream_q_set:
        raise ValueError("no downstream library discharges to read a ceiling at")
    unrun = sorted(set(downstream_q_set) - {r.q for r in downstream})
    if unrun:
        # A library discharge is one the downstream reach proved, so it always
        # has at least its normal-depth run. One without is inputs out of step.
        raise ValueError(f"downstream library discharges {unrun} have no runs")

    scenarios: list[PlannedScenario] = []
    skipped: list[SkippedTarget] = []
    ceilings: list[Ceiling] = []

    for q in sorted(q_set):
        floor = _floor(downstream, q)

        # A ceiling for THIS discharge (DR-043 ALT-F): the highest stage the
        # downstream reach reached at any discharge it can carry while we carry
        # q. The highest across the whole range, not the value at read_q alone:
        # a reach's highest stage need not sit at its highest discharge, and it
        # keeps ceilings from falling as q rises. The range holds every run up
        # to read_q, leftovers included — below the cap they only add candidates.
        # The same runs are the binding pool below.
        cap = q + others
        read_q = _read_q(downstream_q_set, cap)
        pool = [r for r in downstream if r.q <= read_q]
        ceiling = max(r.wse for r in pool)
        # Authored intent can only lower it: kwse_upper_bound is a cap on what to
        # model, never a licence to model above what the downstream reach reached.
        if kwse_upper_bound is not None:
            ceiling = min(ceiling, kwse_upper_bound)
        ceilings.append(Ceiling(q=q, cap=cap, read_q=read_q, wse=ceiling))

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
            # Every run in the POOL is a candidate, not just those at the
            # discharge that set the floor. What a target needs is a water
            # surface at that stage, and the downstream reach reaches its
            # highest stages only at its higher discharges — so restricting the
            # pool to one discharge would make the top of the envelope
            # unreachable, and would hurt worst exactly where DR-033 says
            # backwater runs matter most: a small tributary joining a large
            # mainstem. Runs above read_q are left out: they are floods that
            # cannot coincide with ours, and the run that set the ceiling is
            # always inside, so the top stays reachable.
            #
            # Ties go to the lower discharge, which is the smaller footprint and
            # keeps the answer deterministic. DR-033 does not settle ties.
            nearest = min(pool, key=lambda r: (abs(r.wse - z), r.q))
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

    return Plan(scenarios=tuple(scenarios), skipped=tuple(skipped),
                ceilings=tuple(ceilings))


def chains(
    scenarios: Sequence[PlannedScenario],
) -> tuple[tuple[PlannedScenario, ...], ...]:
    """The scenarios split into one chain per discharge, each kept in order.

    A chain is the unit that can run on its own. Every seed plan() names is
    either this reach's normal-depth run or the stage below at the SAME
    discharge, so nothing in one chain waits on another — which is what lets
    each chain be its own job, run in parallel with the rest.

    Order within a chain is load-bearing for the same reason it is in plan():
    a seed must exist before the scenario naming it runs.
    """
    by_q: dict[int, list[PlannedScenario]] = {}
    for s in scenarios:
        by_q.setdefault(s.q, []).append(s)
    return tuple(tuple(chain) for _, chain in sorted(by_q.items()))
