"""Scenario planning.

Works out which scenarios a reach should run and what seeds each one. Pure, so
it can be tested as plain data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

DZ_MENU = (0.25, 0.5, 1.0, 2.0, 5.0)

DAR_EXPONENT = 0.7

_EPS = 1e-9


@dataclass(frozen=True)
class DownstreamRun:
    """One scenario of the DOWNSTREAM reach, as a candidate boundary for ours."""

    q: int
    wse: float
    bc_type: Literal["ND", "KWSE"]
    bc_value: float


@dataclass(frozen=True)
class Seed:
    """A scenario of THIS reach whose depth grid hot-starts another of ours."""

    q: int
    bc_type: Literal["ND", "KWSE"]
    bc_value: float


@dataclass(frozen=True)
class PlannedScenario:
    """One scenario to run: a point on the grid, its boundary, and its seed."""

    q: int
    z: float
    downstream: DownstreamRun
    seed: Seed


@dataclass(frozen=True)
class SkippedTarget:
    """A grid stage with no downstream run near enough to force it."""

    q: int
    z: float
    nearest_wse: float | None
    distance: float | None


@dataclass(frozen=True)
class Ceiling:
    """How high one discharge's stage library goes, and where that came from."""

    q: int
    cap: float
    read_q: int
    wse: float


@dataclass(frozen=True)
class Plan:
    """Everything the payload builder needs, plus what was left out and why."""

    scenarios: tuple[PlannedScenario, ...]
    skipped: tuple[SkippedTarget, ...]
    ceilings: tuple[Ceiling, ...]


def _snap(value: float, dz: float) -> float:
    """The nearest multiple of dz, with the grid anchored at zero."""
    return round(value / dz) * dz


def _floor(downstream: Sequence[DownstreamRun], q: int) -> float:
    """The lowest stage worth modelling at our discharge q."""
    at_or_below = [r.q for r in downstream if r.q <= q]
    q_ds = max(at_or_below) if at_or_below else min(r.q for r in downstream)
    return min(r.wse for r in downstream if r.q == q_ds)


def others(own_da: float, downstream_da: float, downstream_q_upper: float) -> float:
    """The most flow everything ELSE can add to the downstream reach, in cms."""
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
    """The downstream discharge whose runs, and every lower one's, bound a ceiling."""
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
    """The KWSE scenarios this reach should run, in the order they must run."""
    if dz <= 0:
        raise ValueError(f"stage increment must be positive, got {dz}")
    if not downstream:
        raise ValueError("no downstream runs to bound a stage library with")
    if not others >= 0:
        raise ValueError(f"others must be a non-negative flow, got {others}")
    if not downstream_q_set:
        raise ValueError("no downstream library discharges to read a ceiling at")
    unrun = sorted(set(downstream_q_set) - {r.q for r in downstream})
    if unrun:
        raise ValueError(f"downstream library discharges {unrun} have no runs")

    scenarios: list[PlannedScenario] = []
    skipped: list[SkippedTarget] = []
    ceilings: list[Ceiling] = []

    for q in sorted(q_set):
        floor = _floor(downstream, q)

        cap = q + others
        read_q = _read_q(downstream_q_set, cap)
        pool = [r for r in downstream if r.q <= read_q]
        ceiling = max(r.wse for r in pool)
        if kwse_upper_bound is not None:
            ceiling = min(ceiling, kwse_upper_bound)
        ceilings.append(Ceiling(q=q, cap=cap, read_q=read_q, wse=ceiling))

        lo, hi = _snap(floor, dz), _snap(ceiling, dz)
        if lo > hi + _EPS:
            continue

        steps = int(round((hi - lo) / dz))
        previous: float | None = None

        for i in range(steps + 1):
            z = _snap(lo + i * dz, dz)
            nearest = min(pool, key=lambda r: (abs(r.wse - z), r.q))
            distance = abs(nearest.wse - z)

            if distance > dz / 2 + _EPS:
                skipped.append(SkippedTarget(q=q, z=z, nearest_wse=nearest.wse,
                                             distance=distance))
                continue

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
    """The scenarios split into one chain per discharge, each kept in order."""
    by_q: dict[int, list[PlannedScenario]] = {}
    for s in scenarios:
        by_q.setdefault(s.q, []).append(s)
    return tuple(tuple(chain) for _, chain in sorted(by_q.items()))
