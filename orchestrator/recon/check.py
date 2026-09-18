"""One check for one reach: observe, work out the gap, then act on it.

The unit of work the loop repeats. Returns a result describing what was seen
and what was done, so a caller can log or narrate it.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import psycopg

from recon import (
    activity,
    db,
    gap,
    identity,
    intent,
    observe,
    plan,
    processing,
    queue,
    scenarios,
    storage,
)
from recon.config import settings
from recon.execution import ExecutionService

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """What a check saw and did. Returned so a notebook can narrate it."""

    reach_id: str
    revision: int
    decision: str
    observed: dict[str, Any] = field(default_factory=dict)
    submitted_ref: str | None = None
    note: str = ""

    def __str__(self) -> str:
        bits = [f"reach {self.reach_id}", f"rev {self.revision}", self.decision]
        if self.note:
            bits.append(self.note)
        return " | ".join(bits)


_SNAPSHOT = """
    SELECT
        d.reach_id,
        d.revision,
        rn.is_terminal,
        rn.reach_to_id AS downstream_reach_id,
        COALESCE(mm.applied_revision  >= d.revision,  FALSE) AS model_ok,
        COALESCE(mnd.applied_revision >= d.revision,  FALSE) AS nd_ok,
        COALESCE(mkw.applied_revision >= d.revision,  FALSE) AS kwse_ok,
        COALESCE(dmm.applied_revision  >= dd.revision, FALSE) AS ds_model_ok,
        COALESCE(dnd.applied_revision  >= dd.revision, FALSE) AS ds_nd_ok,
        COALESCE(dkw.applied_revision  >= dd.revision, FALSE) AS ds_kwse_ok,
        COALESCE(drn.is_terminal, FALSE) AS ds_is_terminal,
        (COALESCE(d.ld_ds_z_delta, f.ld_ds_z_delta) IS NOT NULL) AS has_stage_increment,
        p.current_step
    FROM desired_state d
    JOIN reach_network rn USING (reach_id)
    CROSS JOIN desired_state_defaults f
    LEFT JOIN reach_network drn          ON drn.reach_id = rn.reach_to_id
    LEFT JOIN materialized_models    mm  ON mm.reach_id  = d.reach_id
    LEFT JOIN materialized_nd_runs   mnd ON mnd.reach_id = d.reach_id
    LEFT JOIN materialized_kwse_runs mkw ON mkw.reach_id = d.reach_id
    LEFT JOIN reach_processing p ON p.reach_id = d.reach_id
    LEFT JOIN desired_state dd           ON dd.reach_id  = rn.reach_to_id
    LEFT JOIN materialized_models    dmm ON dmm.reach_id = rn.reach_to_id
    LEFT JOIN materialized_nd_runs   dnd ON dnd.reach_id = rn.reach_to_id
    LEFT JOIN materialized_kwse_runs dkw ON dkw.reach_id = rn.reach_to_id
    WHERE d.reach_id = %s
"""


def load_snapshot(
    reach_id: str, *, conn: psycopg.Connection | None = None
) -> gap.Snapshot | None:
    """Everything a decision depends on, read in one query."""
    row = db.one(_SNAPSHOT, (reach_id,), conn=conn)
    if row is None:
        return None
    return gap.Snapshot(
        reach_id=row["reach_id"],
        revision=row["revision"],
        is_terminal=row["is_terminal"],
        downstream_reach_id=row["downstream_reach_id"],
        model_ok=row["model_ok"],
        nd_ok=row["nd_ok"],
        kwse_ok=row["kwse_ok"],
        ds_model_ok=row["ds_model_ok"],
        ds_nd_ok=row["ds_nd_ok"],
        ds_kwse_ok=row["ds_kwse_ok"],
        ds_is_terminal=row["ds_is_terminal"],
        has_stage_increment=row["has_stage_increment"],
        in_flight_step=row["current_step"],
    )


def _downstream_max_q_dir(downstream: str) -> str:
    """The downstream reach's scenario folder at its HIGHEST discharge."""
    proof = db.one(
        "SELECT model_id, run_identity_hash, q_set FROM materialized_nd_runs"
        " WHERE reach_id = %s",
        (downstream,),
    )
    if proof is None:
        raise RuntimeError(
            f"downstream reach {downstream} has no materialized nd library"
        )
    library = storage.nd_library_path(
        downstream, proof["model_id"], proof["run_identity_hash"]
    )
    if library is None:
        raise RuntimeError(
            f"downstream reach {downstream} is materialized but its nd=<slope> "
            "folder cannot be found"
        )
    return f"{library}/{identity.q_folder(max(proof['q_set']))}"


def _model_geometries(reach_id: str, wanted: dict) -> list[str]:
    """Geometry the model domain must contain besides the reach itself."""
    if wanted["is_terminal"]:
        return []
    return [f"{_downstream_max_q_dir(wanted['reach_to_id'])}/{storage.STL_FILENAME}"]


_UPSTREAM = """
    SELECT reach_id, total_da_sqkm
    FROM reach_network
    WHERE reach_to_id = %s
    ORDER BY total_da_sqkm DESC NULLS LAST, reach_id
"""


def _upstream(reach_id: str) -> dict:
    """Upstream reach ids for this reach, and the mainstem among them."""
    rows = db.query(_UPSTREAM, (reach_id,))
    return {
        "reach_ids": [r["reach_id"] for r in rows],
        "mainstem_reach_id": rows[0]["reach_id"] if rows else None,
    }


def job_tags(reach_id: str) -> list[str]:
    """The tags every submission for this reach carries."""
    return [f"reach:{reach_id}"]


def _build_model_payload(reach_id: str) -> dict:
    """What build_model needs, with every identity input pinned."""
    wanted = intent.effective(reach_id)
    if wanted is None:
        raise RuntimeError(f"no effective intent for reach {reach_id}")
    upstream = _upstream(reach_id)
    payload = {
        "reach_id": reach_id,
        "reach_network_path": storage.reach_network_path(),
        "upstream_reach_ids": upstream["reach_ids"],
        "upstream_mainstem_reach_id": upstream["mainstem_reach_id"],
        "base_output_path": storage.model_base_path(reach_id),
        "grid_resolution": float(wanted["grid_resolution"]),
        "epsg_code": int(wanted["epsg_code"]),
        "dem_source": wanted["dem_source"],
        "lulc_source": wanted["lulc_source"],
        "lulc_lookup": wanted["lulc_lookup"],
        "ds_of_lake": bool(wanted["lake_outlet"]),
        "other_geometries": _model_geometries(reach_id, wanted),
    }
    if wanted["model_domain"] is not None:
        payload["domain"] = identity.snap_bbox(wanted["model_domain"], wanted["grid_resolution"])
    return payload


def _nd_boundary(reach_id: str, wanted: dict) -> dict:
    """The downstream boundary condition for a normal-depth run."""
    if wanted["is_terminal"]:
        for kind in ("lake", "coast"):
            feature_id = wanted[f"{kind}_to_id"]
            if feature_id is not None:
                return {
                    "outflow_area_polygon_path": storage.boundary_polygon_path(
                        kind, feature_id
                    )
                }
        return {}

    downstream = wanted["reach_to_id"]
    return {
        "outflow_area_polygon_path": f"{_downstream_max_q_dir(downstream)}/{storage.INUNDATED_AREA_FILENAME}",
    }


def _library_scenarios(reach_id: str, model_id: str, wanted: db.Row) -> list[str]:
    """Scenario manifests already published under this reach's run identity."""
    _, run_hash = identity.run_identity(wanted)
    library = storage.nd_library_path(reach_id, model_id, run_hash)
    if library is None:
        return []
    return [
        f"{library}/{identity.q_folder(q)}/{storage.SCENARIO_MANIFEST_FILENAME}"
        for q in sorted(
            q
            for q in (
                identity.parse_q_folder(name)
                for name in storage.list_subfolders(library)
            )
            if q is not None
        )
    ]


def _authored_bands(wanted: db.Row) -> dict:
    """The authored resolution ranges, in the shape the job takes them."""
    payload = {}
    for field in ("ld_q_max_depth_increase_range",
                  "ld_q_median_depth_increase_range",
                  "ld_q_flooded_area_prcnt_increase_range"):
        band = wanted[field]
        if band is None or band.lower is None or band.upper is None:
            continue
        payload[field] = [float(band.lower), float(band.upper)]
    return payload


def _run_nd_payload(reach_id: str) -> dict:
    """What run_nd_scenarios needs to produce the library intent asks for."""
    wanted = intent.effective(reach_id)
    if wanted is None:
        raise RuntimeError(f"no effective intent for reach {reach_id}")
    model = db.one(
        "SELECT model_id FROM materialized_models WHERE reach_id = %s", (reach_id,)
    )
    if model is None:
        raise RuntimeError(f"reach {reach_id} has no materialized model to run against")
    for field in (
        "q_lower_bound",
        "q_upper_bound",
        "initial_dq_step_for_nd",
        "q_grid_resolution",
    ):
        if wanted[field] is None:
            raise RuntimeError(
                f"reach {reach_id} has no {field}; nd cannot be submitted"
            )
    return {
        "model_manifest_path": storage.model_artifact_path(reach_id, model["model_id"]),
        "model_results_base_path": storage.results_root(),
        "min_upstream_inflow": int(wanted["q_lower_bound"]),
        "max_upstream_inflow": int(wanted["q_upper_bound"]),
        "delta_upstream_inflow": int(wanted["initial_dq_step_for_nd"]),
        "q_grid_resolution": int(wanted["q_grid_resolution"]),
        "existing_scenarios": _library_scenarios(reach_id, model["model_id"], wanted),
        **_authored_bands(wanted),
        **_nd_boundary(reach_id, wanted),
        "volume_convergence_tolerance": settings.volume_convergence_tolerance,
        "allow_water_on_edges": settings.allow_water_on_edges,
    }


def _kwse_inputs(
    reach_id: str, context: scenarios.Planned, chain: tuple[plan.PlannedScenario, ...]
) -> dict:
    """What run_kwse_scenarios needs to run one chain of scenarios."""
    payload_scenarios = [
        {
            "upstream_discharge": s.q,
            "bc_value": s.z,
            "downstream_Scenario": storage.scenario_manifest_path(
                context.downstream_id,
                context.ds_model_id,
                context.ds_run_identity_hash,
                scenarios.scenario_dir(
                    s.downstream.bc_type, s.downstream.bc_value, s.downstream.q
                ),
            ),
            "hotstart": {
                "upstream_discharge": s.seed.q,
                "bc_type": s.seed.bc_type,
                "bc_value": s.seed.bc_value,
                "identity_hash": context.run_identity_hash,
            },
        }
        for s in chain
    ]

    return {
        "model_manifest_path": storage.model_artifact_path(reach_id, context.model_id),
        "model_results_base_path": storage.results_root(),
        "scenarios": payload_scenarios,
        "volume_convergence_tolerance": settings.volume_convergence_tolerance,
        "allow_water_on_edges": settings.allow_water_on_edges,
    }


def _run_kwse_group(reach_id: str) -> list[dict]:
    """One run_kwse_scenarios job per discharge chain still missing scenarios."""
    try:
        context = scenarios.planned(reach_id)
    except scenarios.NotPlannable as why:
        raise RuntimeError(str(why)) from why

    return [
        {"inputs": _kwse_inputs(reach_id, context, chain), "tags": [f"q:{chain[0].q}"]}
        for chain in scenarios.pending(reach_id, context)
    ]


PAYLOADS = {
    gap.BUILD_MODEL: _build_model_payload,
    gap.RUN_ND: _run_nd_payload,
}

GROUPS = {
    gap.RUN_KWSE: _run_kwse_group,
}

BUILD_MODEL_PROCESS = "buildModel"
RUN_ND_PROCESSES = {
    ("lisflood", False): "runNdScenariosLisfloodCpu",
    ("lisflood", True): "runNdScenariosLisfloodGpu",
}
RUN_KWSE_PROCESSES = {
    ("lisflood", False): "runKwseScenariosLisfloodCpu",
    ("lisflood", True): "runKwseScenariosLisfloodGpu",
}

SOLVER_PROCESSES = {gap.RUN_ND: RUN_ND_PROCESSES, gap.RUN_KWSE: RUN_KWSE_PROCESSES}


GPU_AVAILABLE_ENV = "GPU_AVAILABLE"
_TRUE = {"true", "1", "yes", "y", "on"}


def gpu_available() -> bool:
    """True when $GPU_AVAILABLE says this host has a usable GPU."""
    return os.environ.get(GPU_AVAILABLE_ENV, "").strip().lower() in _TRUE


def _process_id(step: str, reach_id: str) -> str:
    """The SEPEX process that carries out a step for this reach."""
    if step == gap.BUILD_MODEL:
        return BUILD_MODEL_PROCESS
    processes = SOLVER_PROCESSES.get(step)
    if processes is None:
        raise RuntimeError(f"no SEPEX process for step {step!r}")
    wanted = intent.effective(reach_id)
    if wanted is None:
        raise RuntimeError(f"no effective intent for reach {reach_id}")
    key = (wanted["solver"], gpu_available())
    if key not in processes:
        raise RuntimeError(
            f"no {step} process for solver {wanted['solver']!r} "
            f"({GPU_AVAILABLE_ENV}={gpu_available()}); "
            f"built variants are {sorted(processes)}"
        )
    return processes[key]


def run_check(reach_id: str, execution: ExecutionService) -> CheckResult:
    """Check one reach, and act on what the gap turns out to be."""
    processing.start_check(reach_id)
    seen = observe.observe_reach(reach_id)
    seen_nd = observe.observe_nd_runs(reach_id)
    seen_kwse = observe.observe_kwse_runs(reach_id)
    if seen_nd.get("changed") or seen_kwse.get("changed"):
        queue.request_check_upstream(reach_id)

    if any(o.get("changed") and o.get("found") for o in (seen, seen_nd, seen_kwse)):
        processing.clear_failures(reach_id)

    snapshot = load_snapshot(reach_id)
    if snapshot is None:
        note = (
            "desired_state_defaults not seeded"
            if intent.defaults_missing()
            else "no desired_state"
        )
        return CheckResult(reach_id, -1, "skipped", seen, note=note)

    event = activity.begin(
        reach_id,
        "check",
        snapshot.revision,
        {
            "model": seen.get("found"),
            "nd": seen_nd.get("found"),
            "kwse": seen_kwse.get("found"),
        },
    )
    decision = gap.calculate(snapshot)
    result = CheckResult(
        reach_id,
        snapshot.revision,
        type(decision).__name__,
        {"model": seen, "nd": seen_nd, "kwse": seen_kwse},
    )

    try:
        if isinstance(decision, gap.NoGap):
            processing.clear_step(reach_id)
            processing.wait_on(reach_id, None)
            result.note = "satisfied"

        elif isinstance(decision, gap.InFlight):
            result.note = f"{decision.step} already running, left alone"

        elif isinstance(decision, gap.AwaitingDownstream):
            processing.wait_on(reach_id, decision.reach_id)
            result.note = f"{decision.step} waits on reach {decision.reach_id}"

        elif isinstance(decision, gap.AwaitingInputs):
            processing.wait_on(reach_id, None)
            result.note = f"{decision.step} awaiting inputs: {decision.reason}"

        elif isinstance(decision, gap.RunStep):
            processing.wait_on(reach_id, None)
            process_id = _process_id(decision.step, reach_id)
            if decision.step in GROUPS:
                members = GROUPS[decision.step](reach_id)
                ref = (execution.submit_group(process_id, members, tags=job_tags(reach_id))
                       if members else None)
                submitted = f"{len(members)} {process_id} jobs"
            else:
                ref = execution.submit(
                    process_id, PAYLOADS[decision.step](reach_id), tags=job_tags(reach_id)
                )
                submitted = process_id

            if ref is None:
                result.note = f"{decision.step}: nothing missing, nothing submitted"
            else:
                processing.mark_in_flight(reach_id, decision.step, ref, snapshot.revision)
                result.submitted_ref = ref
                result.note = f"submitted {submitted} ({ref[:18]})"
            queue.request_check(reach_id)

    except Exception as exc:
        failure = processing.record_failure(reach_id, str(exc))
        activity.end(event, "failed", error=str(exc)[:2000])
        result.decision, result.note = (
            "Failed",
            (
                f"{exc} (failure {failure['consecutive_failures']}"
                + (", halted)" if failure["halted"] else ")")
            ),
        )
        logger.exception("check failed for reach %s", reach_id)
        return result

    outcome = (
        "blocked"
        if isinstance(decision, (gap.AwaitingDownstream, gap.AwaitingInputs))
        else "ok"
    )
    activity.end(event, outcome, {"decision": result.decision, "note": result.note})
    logger.info("%s", result)
    return result


def sweep(execution: ExecutionService, limit: int | None = None) -> list[CheckResult]:
    """Check every reach that is currently due, once."""
    return [run_check(r["reach_id"], execution) for r in queue.due_reaches(limit)]
