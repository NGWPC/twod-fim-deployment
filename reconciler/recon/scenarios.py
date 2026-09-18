"""Scenario collection.

Gathers the scenarios a reach should have, from the database and from storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from recon import db, identity, intent, plan, storage


@dataclass(frozen=True)
class Planned:
    """A reach's KWSE plan, plus what is needed to address the files involved."""

    plan: plan.Plan
    model_id: str
    run_identity_hash: str
    nd_slope: float
    downstream_id: str
    ds_model_id: str
    ds_run_identity_hash: str


class NotPlannable(Exception):
    """Why no plan can be produced for this reach yet."""


def nd_slope(reach_id: str, model_id: str, run_hash: str) -> float:
    """The slope naming this reach's `nd=<slope>` folder."""
    library = storage.nd_library_path(reach_id, model_id, run_hash)
    if library is None:
        raise NotPlannable(f"reach {reach_id} has no single nd=<slope> folder")
    slope = identity.parse_nd_folder(library.rsplit("/", 1)[-1])
    if slope is None:
        raise NotPlannable(f"reach {reach_id} nd folder {library} names no slope")
    return slope


def downstream_runs(
    downstream_id: str, *, conn: psycopg.Connection | None = None
) -> list[plan.DownstreamRun]:
    """Every scenario the downstream reach has, as candidate boundaries."""
    nd = db.one("SELECT model_id, run_identity_hash, us_min_wse_curve"
                " FROM materialized_nd_runs WHERE reach_id = %s",
                (downstream_id,), conn=conn)
    if nd is None:
        raise NotPlannable(f"downstream reach {downstream_id} has no nd library")

    slope = nd_slope(downstream_id, nd["model_id"], nd["run_identity_hash"])
    runs = [plan.DownstreamRun(q=int(p["q"]), wse=float(p["wse"]),
                               bc_type="ND", bc_value=slope)
            for p in nd["us_min_wse_curve"]]

    kwse = db.one("SELECT scenario_index FROM materialized_kwse_runs WHERE reach_id = %s",
                  (downstream_id,), conn=conn)
    if kwse is not None:
        runs += [plan.DownstreamRun(q=int(g["q"]), wse=float(r["wse"]),
                                    bc_type="KWSE", bc_value=float(r["bc"]))
                 for g in kwse["scenario_index"] for r in g["runs"]]
    return runs


def others(
    reach_id: str, wanted: db.Row, downstream_id: str,
    *, conn: psycopg.Connection | None = None,
) -> float:
    """What everything else can add to the downstream reach."""
    below = intent.effective(downstream_id, conn=conn)
    if below is None:
        raise NotPlannable(f"downstream reach {downstream_id} has no effective intent")
    if below["q_upper_bound"] is None:
        raise NotPlannable(
            f"downstream reach {downstream_id} has no q_upper_bound authored, "
            "which the KWSE ceiling scales by")
    if wanted["total_da_sqkm"] is None or below["total_da_sqkm"] is None:
        raise NotPlannable(
            f"reach {reach_id} or downstream reach {downstream_id} has no drainage "
            "area, which the KWSE ceiling scales by")
    try:
        return plan.others(float(wanted["total_da_sqkm"]),
                           float(below["total_da_sqkm"]),
                           float(below["q_upper_bound"]))
    except ValueError as why:
        raise NotPlannable(f"reach {reach_id} -> {downstream_id}: {why}") from why


def planned(reach_id: str, *, conn: psycopg.Connection | None = None) -> Planned:
    """This reach's KWSE plan, or NotPlannable saying what is missing."""
    wanted = intent.effective(reach_id, conn=conn)
    if wanted is None:
        raise NotPlannable(f"reach {reach_id} has no effective intent")
    if wanted["is_terminal"]:
        raise NotPlannable(f"reach {reach_id} is terminal, so it has no downstream stages")
    if wanted["ld_ds_z_delta"] is None:
        raise NotPlannable(
            f"reach {reach_id} has no stage increment (ld_ds_z_delta) authored")

    model = db.one("SELECT model_id FROM materialized_models WHERE reach_id = %s",
                   (reach_id,), conn=conn)
    own_nd = db.one("SELECT model_id, run_identity_hash, q_set FROM materialized_nd_runs"
                    " WHERE reach_id = %s", (reach_id,), conn=conn)
    if model is None or own_nd is None:
        raise NotPlannable(f"reach {reach_id} has no materialized model and nd library")

    downstream_id = wanted["reach_to_id"]
    ds_nd = db.one("SELECT model_id, run_identity_hash, q_set FROM materialized_nd_runs"
                   " WHERE reach_id = %s", (downstream_id,), conn=conn)
    if ds_nd is None:
        raise NotPlannable(f"downstream reach {downstream_id} has no nd library")

    extra = others(reach_id, wanted, downstream_id, conn=conn)

    slope = nd_slope(reach_id, own_nd["model_id"], own_nd["run_identity_hash"])

    return Planned(
        plan=plan.plan(
            q_set=list(own_nd["q_set"]),
            dz=float(wanted["ld_ds_z_delta"]),
            downstream=downstream_runs(downstream_id, conn=conn),
            nd_slope=slope,
            kwse_upper_bound=(None if wanted["kwse_upper_bound"] is None
                              else float(wanted["kwse_upper_bound"])),
            others=extra,
            downstream_q_set=list(ds_nd["q_set"]),
        ),
        model_id=model["model_id"],
        run_identity_hash=own_nd["run_identity_hash"],
        nd_slope=slope,
        downstream_id=downstream_id,
        ds_model_id=ds_nd["model_id"],
        ds_run_identity_hash=ds_nd["run_identity_hash"],
    )


def scenario_dir(bc_type: str, bc_value: float, q: int) -> str:
    """The `<nd=…|kwse=…>/q=…` folder one scenario point implies."""
    downstream = (identity.nd_folder(bc_value) if bc_type == "ND"
                  else identity.kwse_folder(bc_value))
    return f"{downstream}/{identity.q_folder(q)}"


@dataclass(frozen=True)
class Lookup:
    """One planned scenario, as storage has it."""

    folder: str
    path: str
    manifest: dict | None
    problems: list[str] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        """Published and accepted. A refused manifest is not a scenario."""
        return self.manifest is not None and not self.problems


def look_up(reach_id: str, context: Planned, scenario: plan.PlannedScenario) -> Lookup:
    """Read one planned scenario's manifest at the folder the plan names."""
    folder = scenario_dir("KWSE", scenario.z, scenario.q)
    path = storage.scenario_manifest_path(
        reach_id, context.model_id, context.run_identity_hash, folder)
    manifest = storage.read_json(path)
    if manifest is None:
        return Lookup(folder, path, None)
    problems = identity.verify_scenario_manifest(
        manifest, reach_id, context.run_identity_hash, context.model_id, folder)
    return Lookup(folder, path, manifest, list(problems))


def pending(reach_id: str, context: Planned) -> list[tuple[plan.PlannedScenario, ...]]:
    """The planned scenarios that do not exist yet, one chain per discharge."""
    return list(plan.chains([s for s in context.plan.scenarios
                             if not look_up(reach_id, context, s).exists]))
