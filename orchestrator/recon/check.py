"""One check: observe, work out the gap, act on it.

This is the loop. Everything else is a part of it — queue.py says which reach,
observe.py records what the address intent implies holds, gap.py decides,
processing.py writes the work notes, jobs.py hears back from the execution
system. Nothing here knows about Dagster, notebooks, or any other caller.

A check is short. It never waits for a job: it submits, writes down that it
did, and ends. What the job produced is discovered by a later check looking at
storage, which is why a crash at any point costs at most some repeated work.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import psycopg

from recon import (activity, db, gap, identity, intent, observe, processing,
                   queue, storage)
from recon.config import settings
from recon.workers import ContainerRunner

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """What a check saw and did. Returned so a notebook can narrate it."""

    reach_id: int
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


# One read, one point in time. Each proof is reduced to a boolean here — a
# step's row exists AND proves that reach's current revision — so the gap
# calculation never sees another reach's revision. The downstream reach's
# proofs are judged against the downstream reach's own intent.
_SNAPSHOT = """
    SELECT
        d.reach_id,
        d.revision,
        rn.is_terminal,
        rn.reach_to_id AS downstream_reach_id,
        (rn.lake_to_id IS NOT NULL OR rn.coast_to_id IS NOT NULL) AS has_outflow_polygon,
        COALESCE(mm.applied_revision  >= d.revision,  FALSE) AS model_ok,
        COALESCE(mnd.applied_revision >= d.revision,  FALSE) AS nd_ok,
        COALESCE(mkw.applied_revision >= d.revision,  FALSE) AS kwse_ok,
        COALESCE(dmm.applied_revision  >= dd.revision, FALSE) AS ds_model_ok,
        COALESCE(dnd.applied_revision  >= dd.revision, FALSE) AS ds_nd_ok,
        COALESCE(dkw.applied_revision  >= dd.revision, FALSE) AS ds_kwse_ok,
        p.current_step
    FROM desired_state d
    JOIN reach_network rn USING (reach_id)
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
    reach_id: int, *, conn: psycopg.Connection | None = None
) -> gap.Snapshot | None:
    """Everything a decision depends on, read in one query.

    None when the reach has no desired_state row: a reach in the network means
    nothing until intent is authored for it.
    """
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
        has_outflow_polygon=row["has_outflow_polygon"],
        in_flight_step=row["current_step"],
    )


def _build_model_payload(reach_id: int) -> dict:
    """What build_model needs, with every identity input pinned.

    Pinned rather than left to the job's defaults, so the job builds exactly
    the identity the loop predicted — a default drifting inside the job image
    would otherwise produce models at addresses the loop never looks at.

    sdr_commit cannot be pinned: it is baked into the image. desired_state's
    value must therefore match the deployed image, and the observe self-check
    is what catches it when it does not.
    """
    wanted = intent.effective(reach_id)
    if wanted is None:
        raise RuntimeError(f"no effective intent for reach {reach_id}")
    return {
        "reach_id": reach_id,
        "db_uri": settings.job_db_connection_string,
        "base_output_path": storage.model_base_path(reach_id),
        "grid_resolution": float(wanted["grid_resolution"]),
        "epsg_code": int(wanted["epsg_code"]),
        "dem_source": wanted["dem_source"],
        "lulc_source": wanted["lulc_source"],
        "lulc_lookup": wanted["lulc_lookup"],
    }


def _nd_boundary(reach_id: int, wanted: dict) -> dict:
    """The downstream boundary condition for a normal-depth run: where it is
    applied, and at what slope.

    Both halves come from the SAME place, which is the point — the boundary is
    a statement about what this reach drains INTO, never about the reach
    itself. Which place depends on the terminal/non-terminal split the whole
    ladder turns on:

      non-terminal  the downstream reach's inundated area at the HIGHEST
                    discharge of its library (the largest wetted footprint it
                    produces, so it bounds every scenario this reach will run),
                    at the DOWNSTREAM reach's centerline slope
                    — DR-039 ALT-D, selected via ALT-F
      terminal      the lake or coast it drains into, published once per water
                    body and shared by every reach ending there
                    — DR-006 ALT-E

    The downstream address is read from that reach's proof rather than
    predicted, because the discharge in it is emergent: the adaptive step
    algorithm chose it, and only that reach's materialization knows what it was.
    """
    if wanted["is_terminal"]:
        for kind in ("lake", "coast"):
            feature_id = wanted[f"{kind}_to_id"]
            if feature_id is not None:
                # DR-006 ALT-E asks for a FREEFALL here — water leaving the
                # domain without resistance, so it does not pool in the
                # transition zone — and the job can only express that as a
                # slope. No steep value has been decided, so this passes the
                # reach's own slope and is knowingly wrong for a terminal:
                # a flat slope behaves like a nearly closed boundary, which is
                # what ALT-B was rejected for.
                return {"outflow_area_polygon_path": storage.boundary_polygon_path(kind, feature_id),
                        "ds_slope": intent.boundary_slope(reach_id)}
        raise RuntimeError(
            f"reach {reach_id} is a {wanted['terminal_reason']} terminal and names no "
            "lake or coast, so it has no outflow boundary")

    downstream = wanted["reach_to_id"]
    proof = db.one(
        "SELECT model_identity_hash, run_identity_hash, q_set FROM materialized_nd_runs"
        " WHERE reach_id = %s", (downstream,))
    if proof is None:
        raise RuntimeError(f"downstream reach {downstream} has no materialized nd library")
    max_q = max(proof["q_set"])
    # Two different slopes, and confusing them puts the loop in the wrong
    # folder. The downstream reach's library sits at ITS boundary slope, which
    # is its own downstream neighbour's. The slope THIS run is performed at is
    # the downstream reach's centerline slope.
    library = storage.nd_library_path(
        downstream, proof["model_identity_hash"], proof["run_identity_hash"],
        intent.boundary_slope(downstream))
    return {
        "outflow_area_polygon_path":
            f"{library}/{identity.q_folder(max_q)}/{storage.INUNDATED_AREA_FILENAME}",
        "ds_slope": intent.boundary_slope(reach_id),
    }


def _run_nd_payload(reach_id: int) -> dict:
    """What run_nd_scenarios needs to produce the library intent asks for.

    The discharge range is authored intent passed straight through. The step is
    only a STARTING increment — the job grows and shrinks it as the reach's
    response curve demands — which is why the loop cannot predict the resulting
    discharges and reads them back instead.
    """
    wanted = intent.effective(reach_id)
    if wanted is None:
        raise RuntimeError(f"no effective intent for reach {reach_id}")
    model = db.one("SELECT identity_hash, model_id FROM materialized_models WHERE reach_id = %s",
                   (reach_id,))
    if model is None:
        raise RuntimeError(f"reach {reach_id} has no materialized model to run against")
    for field in ("q_lower_bound", "q_upper_bound", "initial_dq_step_for_nd"):
        if wanted[field] is None:
            raise RuntimeError(f"reach {reach_id} has no {field}; nd cannot be submitted")
    return {
        "model_manifest_path": storage.model_artifact_path(reach_id, model["model_id"]),
        "model_results_base_path": storage.results_base_path(reach_id, model["identity_hash"]),
        "min_upstream_inflow": float(wanted["q_lower_bound"]),
        "max_upstream_inflow": float(wanted["q_upper_bound"]),
        "delta_upstream_inflow": float(wanted["initial_dq_step_for_nd"]),
        **_nd_boundary(reach_id, wanted),
        "volume_convergence_tolerance": settings.volume_convergence_tolerance,
        "solver": wanted["solver"],
    }


PAYLOADS = {gap.BUILD_MODEL: _build_model_payload, gap.RUN_ND: _run_nd_payload}


def run_check(reach_id: int, runner: ContainerRunner) -> CheckResult:
    """Check one reach, and act on what the gap turns out to be."""
    processing.start_check(reach_id)  # stamped now, at the start, never at the end
    seen = observe.observe_reach(reach_id)
    seen_nd = observe.observe_nd_runs(reach_id)
    # A change to this reach's nd proof changes what the reaches above it can
    # do: it is their outflow boundary, so one appearing unblocks them and one
    # being retracted invalidates work they may already have started. Told here
    # rather than at submission because this is the moment it becomes true —
    # and unlike submission, it also covers a library that has gone away.
    if seen_nd.get("changed"):
        queue.request_check_upstream(reach_id)

    # Work landing is what ends a failure streak. Adoption — a step's proof that
    # was not there before — is that signal; a retraction is not, which is why
    # `found` is tested alongside `changed`.
    if any(o.get("changed") and o.get("found") for o in (seen, seen_nd)):
        processing.clear_failures(reach_id)

    snapshot = load_snapshot(reach_id)
    if snapshot is None:
        note = ("desired_state_defaults not seeded" if intent.defaults_missing()
                else "no desired_state")
        return CheckResult(reach_id, -1, "skipped", seen, note=note)

    event = activity.begin(reach_id, "check", snapshot.revision,
                           {"model": seen.get("found"), "nd": seen_nd.get("found")})
    decision = gap.calculate(snapshot)
    result = CheckResult(reach_id, snapshot.revision, type(decision).__name__,
                         {"model": seen, "nd": seen_nd})

    try:
        if isinstance(decision, gap.NoGap):
            # Whatever was running produced this; the marker has served its
            # purpose. Also stop saying we are waiting on anyone.
            processing.clear_step(reach_id)
            processing.wait_on(reach_id, None)
            result.note = "satisfied"

        elif isinstance(decision, gap.InFlight):
            result.note = f"{decision.step} already running, left alone"

        elif isinstance(decision, gap.WaitingDownstream):
            processing.wait_on(reach_id, decision.reach_id)
            result.note = f"{decision.step} waits on reach {decision.reach_id}"

        elif isinstance(decision, gap.AwaitingInputs):
            # Not a failure: nothing was attempted, so there is nothing to back
            # off from. It simply stays here until the missing data is authored.
            processing.wait_on(reach_id, None)
            result.note = f"{decision.step} awaiting inputs: {decision.reason}"

        elif isinstance(decision, gap.RunStep):
            processing.wait_on(reach_id, None)
            ref = runner.submit(decision.step, PAYLOADS[decision.step](reach_id))
            processing.mark_in_flight(reach_id, decision.step, ref, snapshot.revision)
            # Ask to be looked at again, so the result gets noticed without
            # waiting for the next sweep.
            queue.request_check(reach_id)
            result.submitted_ref = ref
            result.note = f"submitted {decision.step} ({ref[:12]})"

    except Exception as exc:  # submission failed; the reach must not stall
        failure = processing.record_failure(reach_id, str(exc))
        activity.end(event, "failed", error=str(exc)[:2000])
        result.decision, result.note = "Failed", (
            f"{exc} (failure {failure['consecutive_failures']}"
            + (", halted)" if failure["halted"] else ")"))
        logger.exception("check failed for reach %s", reach_id)
        return result

    # One activity outcome for both kinds of not-proceeding; the decision name
    # in the detail says which, and reach_status keeps them apart as states.
    outcome = ("blocked" if isinstance(decision, (gap.WaitingDownstream, gap.AwaitingInputs))
               else "ok")
    activity.end(event, outcome, {"decision": result.decision, "note": result.note})
    logger.info("%s", result)
    return result


def sweep(runner: ContainerRunner, limit: int | None = None) -> list[CheckResult]:
    """Check every reach that is currently due, once.

    The list is read once at the start rather than re-queried as it goes, so a
    sweep terminates: checks request further checks, and a sweep that kept
    re-reading its own queue would chase them forever. Call it again to pick
    those up — which is what the loop does anyway.
    """
    return [run_check(r["reach_id"], runner) for r in queue.due_reaches(limit)]
