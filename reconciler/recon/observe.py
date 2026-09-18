"""Observation.

Looks where intent implies a reach's artifacts should be, judges what is there,
and records the result.
"""

import json
import logging
import math
from typing import NamedTuple

import psycopg

from recon import db, identity, intent, scenarios, storage
from recon.config import settings

logger = logging.getLogger(__name__)


def observe_reach(reach_id: str, *, conn: psycopg.Connection | None = None) -> dict:
    """Reconcile materialized_models for one reach against storage."""
    wanted = intent.effective(reach_id, conn=conn)
    if wanted is None:
        removed = bool(db.query(
            "DELETE FROM materialized_models WHERE reach_id = %s RETURNING reach_id",
            (reach_id,), conn=conn))
        return {"reach_id": reach_id, "predicted": None, "found": None,
                "changed": removed, "note": "no effective intent"}

    _, predicted = identity.model_identity(wanted)
    base = storage.model_base_path(reach_id)

    authored = wanted["model_domain"]
    if authored is None:
        candidates = storage.list_subfolders(base, prefix=f"{predicted}_")
    else:
        authored = identity.snap_bbox(authored, wanted["grid_resolution"])
        code = identity.domain_code(authored, wanted["geom_wkb"], wanted["grid_resolution"])
        candidates = [f"{predicted}_{code}"]

    found_model_id, refused = None, []
    for name in candidates:
        manifest = storage.read_json(f"{base}/{name}/{storage.MANIFEST_FILENAME}")
        if manifest is None:
            continue
        problems = identity.verify_manifest(manifest, reach_id, name, authored)
        if problems:
            refused.append({"folder": name, "problems": problems})
            logger.warning("refused manifest at %s/%s: %s", base, name, problems)
            continue
        found_model_id = name
        break

    before = db.one("SELECT model_id, applied_revision FROM materialized_models WHERE reach_id = %s",
                    (reach_id,), conn=conn)

    if found_model_id is None:
        removed = bool(db.query(
            "DELETE FROM materialized_models WHERE reach_id = %s RETURNING reach_id",
            (reach_id,), conn=conn))
        return {"reach_id": reach_id, "predicted": predicted, "found": None,
                "changed": removed, "refused": refused,
                "was": before["model_id"] if before else None}

    identity_hash, _, domain_code = found_model_id.partition("_")
    db.query(
        """
        INSERT INTO materialized_models (reach_id, identity_hash, domain_code, applied_revision, confirmed_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (reach_id) DO UPDATE SET
            identity_hash = EXCLUDED.identity_hash,
            domain_code = EXCLUDED.domain_code,
            applied_revision = EXCLUDED.applied_revision,
            confirmed_at = now()
        """,
        (reach_id, identity_hash, domain_code, wanted["revision"]),
        conn=conn,
    )
    changed = before is None or before["model_id"] != found_model_id \
        or before["applied_revision"] != wanted["revision"]
    return {"reach_id": reach_id, "predicted": predicted, "found": found_model_id,
            "changed": changed, "refused": refused,
            "was": before["model_id"] if before else None}


class Adoption(NamedTuple):
    """The library adopted from what storage holds, and what it cost to say so."""

    q_set: list[int]
    holes: list[str]
    expected: list[str]
    passed_over: int


def _bands(wanted: db.Row) -> list[tuple[str, str, float, float, bool]]:
    """The authored resolution, as (label, manifest key, floor, ceiling, relative)."""
    authored = (
        ("max depth", "max_depth", wanted["ld_q_max_depth_increase_range"], False),
        ("median depth", "median_depth",
         wanted["ld_q_median_depth_increase_range"], False),
        ("flooded area", "flooded_area",
         wanted["ld_q_flooded_area_prcnt_increase_range"], True),
    )
    return [(label, key, float(band.lower), float(band.upper), relative)
            for label, key, band, relative in authored
            if band is not None and band.lower is not None and band.upper is not None]


def _change(earlier: dict, later: dict, key: str, relative: bool) -> float:
    if not relative:
        return later[key] - earlier[key]
    base = earlier[key]
    return (later[key] - base) / base * 100 if base > 0 else 0.0


def _verdict(earlier: dict, later: dict, bands: list) -> tuple[str, str]:
    """Apply the acceptance rule to two scenarios."""
    over = [f"{label} {_change(earlier, later, key, rel):+.3g} over {ceiling:.3g}"
            for label, key, _, ceiling, rel in bands
            if _change(earlier, later, key, rel) > ceiling]
    if over:
        return "reject_high", "; ".join(over)
    inside = [label for label, key, floor, ceiling, rel in bands
              if floor <= _change(earlier, later, key, rel) <= ceiling]
    if inside:
        return "accept", ", ".join(inside)
    return "reject_low", ""


def _off_centre(earlier: dict, later: dict, bands: list) -> float:
    """How far a step lands from the middle of the bands, summed over all three."""
    total = 0.0
    for _, key, floor, ceiling, rel in bands:
        middle = (floor + ceiling) / 2
        total += ((_change(earlier, later, key, rel) - middle) / middle) ** 2
    return total


def _nothing_finer(earlier: int, later: int, grid: int | None, apart: int) -> bool:
    """Whether any discharge could have gone between these two."""
    if grid is None:
        return apart == 1
    return later - earlier <= grid


def adopt(metrics: list[dict], wanted: db.Row) -> Adoption:
    """The cheapest library, out of everything present, that satisfies intent."""
    bands = _bands(wanted)
    order = [entry["q"] for entry in metrics]
    if not bands or len(metrics) < 2:
        return Adoption(order, [], [], 0)
    grid = wanted["q_grid_resolution"]

    infinite = (math.inf, math.inf, math.inf)
    best: list[tuple[float, float, float]] = [infinite] * len(metrics)
    parent = [-1] * len(metrics)
    best[0] = (0.0, 0.0, 0.0)
    for j in range(1, len(metrics)):
        for i in range(j):
            if best[i] == infinite:
                continue
            verdict, _ = _verdict(metrics[i], metrics[j], bands)
            if verdict == "reject_high" and not _nothing_finer(
                    metrics[i]["q"], metrics[j]["q"], grid, j - i):
                continue
            step = (0.0 if verdict == "accept" else 1.0, 1.0,
                    _off_centre(metrics[i], metrics[j], bands))
            through = tuple(a + b for a, b in zip(best[i], step))
            if through < best[j]:
                best[j], parent[j] = through, i  # type: ignore[assignment]

    if best[-1] == infinite:
        return Adoption(order, ["no route through the library satisfies intent"], [], 0)

    path: list[int] = []
    node = len(metrics) - 1
    while node != -1:
        path.append(node)
        node = parent[node]
    path.reverse()

    holes, expected = [], []
    for i, j in zip(path, path[1:]):
        verdict, why = _verdict(metrics[i], metrics[j], bands)
        gap = metrics[j]["q"] - metrics[i]["q"]
        where = f"q={metrics[i]['q']} to q={metrics[j]['q']}"
        if verdict == "reject_high":
            expected.append(f"{where} is a single {grid} cms grid step and still "
                            f"moved {why}; the reach changes faster than its "
                            f"discharge grid can follow")
        elif verdict == "reject_low":
            expected.append(f"{where} moved less than the bands ask; no line "
                            f"clears a floor, so the sweep took the nearest one")

    return Adoption([metrics[i]["q"] for i in path], holes, expected,
                    len(metrics) - len(path))


def observe_nd_runs(reach_id: str, *, conn: psycopg.Connection | None = None) -> dict:
    """Reconcile materialized_nd_runs for one reach against storage."""
    out: dict = {"reach_id": reach_id, "step": "nd", "found": None, "changed": False}
    before = db.one("SELECT run_identity_hash, q_set, applied_revision FROM materialized_nd_runs"
                    " WHERE reach_id = %s", (reach_id,), conn=conn)

    def retract(note: str) -> dict:
        removed = bool(db.query(
            "DELETE FROM materialized_nd_runs WHERE reach_id = %s RETURNING reach_id",
            (reach_id,), conn=conn))
        return {**out, "changed": removed, "note": note}

    wanted = intent.effective(reach_id, conn=conn)
    if wanted is None:
        return retract("no effective intent")

    _, predicted_model = identity.model_identity(wanted)
    model = db.one("SELECT identity_hash, model_id FROM materialized_models WHERE reach_id = %s",
                   (reach_id,), conn=conn)
    if model is None:
        return retract("no materialized model")
    if model["identity_hash"] != predicted_model:
        return retract(f"materialized model {model['identity_hash']} is not the "
                       f"{predicted_model} intent now implies")

    _, run_hash = identity.run_identity(wanted)
    library = storage.nd_library_path(reach_id, model["model_id"], run_hash)
    if library is None:
        return retract("no single nd=<slope> folder to read")
    out.update({"predicted": run_hash, "library": library})

    discharges = sorted(
        q for q in (identity.parse_q_folder(n) for n in storage.list_subfolders(library))
        if q is not None
    )
    if not discharges:
        return retract("no scenarios in library")

    lower, upper = wanted["q_lower_bound"], wanted["q_upper_bound"]
    if lower is None or upper is None:
        return retract("discharge range is unauthored, so nothing can satisfy it")
    if min(discharges) > lower or max(discharges) < upper:
        return retract(f"library spans {min(discharges)}-{max(discharges)}, "
                       f"intent asks for {lower}-{upper}")

    curve, metrics, refused = [], [], []
    nd_folder = library.rsplit("/", 1)[-1]
    for q in discharges:
        scenario_dir = f"{nd_folder}/{identity.q_folder(q)}"
        path = f"{library}/{identity.q_folder(q)}/{storage.SCENARIO_MANIFEST_FILENAME}"
        manifest = storage.read_json(path)
        if manifest is None:
            return {**retract(f"scenario q={q} has no manifest yet"), "refused": refused}
        problems = identity.verify_scenario_manifest(
            manifest, reach_id, run_hash, model["model_id"], scenario_dir)
        if problems:
            refused.append({"q": q, "problems": problems})
            logger.warning("refused scenario manifest at %s: %s", path, problems)
            return {**retract(f"scenario q={q} refused"), "refused": refused}
        props = manifest["properties"]
        curve.append({"q": q, "wse": float(props["nominal_wse"])})
        metrics.append({"q": q, "max_depth": float(props["max_depth"]),
                        "median_depth": float(props["median_depth"]),
                        "flooded_area": float(props["flooded_area"])})

    adoption = adopt(metrics, wanted)
    for note in adoption.expected:
        logger.debug("reach %s nd library: %s", reach_id, note)
    for hole in adoption.holes:
        logger.error("reach %s nd library: %s", reach_id, hole)
    if adoption.holes:
        return {**retract(adoption.holes[0]), "refused": refused}
    adopted = adoption.q_set

    us_wse_max = max(point["wse"] for point in curve)

    db.query(
        """
        INSERT INTO materialized_nd_runs
            (reach_id, model_id, run_identity_hash, q_set,
             us_wse_max, us_min_wse_curve, applied_revision, confirmed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (reach_id) DO UPDATE SET
            model_id            = EXCLUDED.model_id,
            run_identity_hash   = EXCLUDED.run_identity_hash,
            q_set               = EXCLUDED.q_set,
            us_wse_max          = EXCLUDED.us_wse_max,
            us_min_wse_curve    = EXCLUDED.us_min_wse_curve,
            applied_revision    = EXCLUDED.applied_revision,
            confirmed_at        = now()
        """,
        (reach_id, model["model_id"], run_hash, adopted,
         us_wse_max, json.dumps(curve), wanted["revision"]),
        conn=conn,
    )
    changed = (before is None
               or before["run_identity_hash"] != run_hash
               or list(before["q_set"]) != adopted
               or before["applied_revision"] != wanted["revision"])
    return {**out, "found": f"{len(adopted)} discharges adopted "
                            f"of {len(discharges)} in storage",
            "q_set": adopted, "expected": adoption.expected,
            "passed_over": adoption.passed_over,
            "us_wse_max": us_wse_max, "changed": changed, "refused": refused}


def observe_kwse_runs(reach_id: str, *, conn: psycopg.Connection | None = None) -> dict:
    """Reconcile materialized_kwse_runs for one reach against storage."""
    out: dict = {"reach_id": reach_id, "step": "kwse", "found": None, "changed": False}
    before = db.one("SELECT run_identity_hash, scenario_index, applied_revision"
                    " FROM materialized_kwse_runs WHERE reach_id = %s",
                    (reach_id,), conn=conn)

    def retract(note: str) -> dict:
        removed = bool(db.query(
            "DELETE FROM materialized_kwse_runs WHERE reach_id = %s RETURNING reach_id",
            (reach_id,), conn=conn))
        return {**out, "changed": removed, "note": note}

    wanted = intent.effective(reach_id, conn=conn)
    if wanted is None:
        return retract("no effective intent")

    _, predicted_model = identity.model_identity(wanted)
    model = db.one("SELECT identity_hash FROM materialized_models WHERE reach_id = %s",
                   (reach_id,), conn=conn)
    if model is None:
        return retract("no materialized model")
    if model["identity_hash"] != predicted_model:
        return retract(f"materialized model {model['identity_hash']} is not the "
                       f"{predicted_model} intent now implies")

    try:
        context = scenarios.planned(reach_id, conn=conn)
    except scenarios.NotPlannable as why:
        return retract(str(why))

    out["planned"] = len(context.plan.scenarios)
    out["skipped"] = len(context.plan.skipped)

    index: dict[int, list[dict]] = {}
    refused = []
    for scenario in context.plan.scenarios:
        found = scenarios.look_up(reach_id, context, scenario)
        if found.manifest is None:
            return {**retract(f"scenario kwse={scenario.z:g}/q={scenario.q} has no "
                              "manifest yet"), "refused": refused}
        if found.problems:
            refused.append({"scenario": found.folder, "problems": found.problems})
            logger.warning("refused scenario manifest at %s: %s", found.path, found.problems)
            return {**retract(f"scenario {found.folder} refused"), "refused": refused}
        manifest = found.manifest
        index.setdefault(scenario.q, []).append(
            {"wse": float(manifest["properties"]["nominal_wse"]), "bc": scenario.z})

    scenario_index = [{"q": q, "runs": sorted(runs, key=lambda r: r["wse"])}
                      for q, runs in sorted(index.items())]

    db.query(
        """
        INSERT INTO materialized_kwse_runs
            (reach_id, model_id, run_identity_hash, scenario_index,
             applied_revision, confirmed_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (reach_id) DO UPDATE SET
            model_id          = EXCLUDED.model_id,
            run_identity_hash = EXCLUDED.run_identity_hash,
            scenario_index    = EXCLUDED.scenario_index,
            applied_revision  = EXCLUDED.applied_revision,
            confirmed_at      = now()
        """,
        (reach_id, context.model_id, context.run_identity_hash,
         json.dumps(scenario_index), wanted["revision"]),
        conn=conn,
    )
    changed = (before is None
               or before["run_identity_hash"] != context.run_identity_hash
               or before["scenario_index"] != scenario_index
               or before["applied_revision"] != wanted["revision"])
    return {**out, "found": f"{len(context.plan.scenarios)} scenarios",
            "changed": changed, "refused": refused}
