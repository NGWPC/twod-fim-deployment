"""A check observe, work out the gap, act on it.
A check is short. It does not waits for a job. It submits, writes down that it did,
and ends.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import psycopg

from recon import activity, db, gap, observe, processing, queue, storage
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


def load_snapshot(
    reach_id: int, *, conn: psycopg.Connection | None = None
) -> gap.Snapshot | None:
    """Read everything a decision depends on, in one query.
    So that the facts a decision is made from should come from a
    single point in time, not be gathered one by one as things can move.

    Returns None when the reach has no desired_state, nothing is wanted for it,
    so there is nothing to decide.
    """
    row = db.one(
        """
        SELECT d.reach_id,
               d.revision,
               cs.identity_hash IS NOT NULL AS has_model,
               p.current_step
        FROM desired_state d
        LEFT JOIN current_state cs USING (reach_id)
        LEFT JOIN reach_processing p USING (reach_id)
        WHERE d.reach_id = %s
        """,
        (reach_id,),
        conn=conn,
    )
    if row is None:
        return None
    return gap.Snapshot(
        reach_id=row["reach_id"],
        revision=row["revision"],
        has_model=row["has_model"],
        in_flight_step=row["current_step"],
    )


def _build_model_payload(reach_id: int) -> dict:
    """What build_model needs to do its work.

    The job talks to the database itself for reach geometry and parameters, so
    it gets a connection string that resolves from inside a container rather
    than the one the loop uses.
    """
    payload = {
        "reach_id": reach_id,
        "db_uri": settings.job_db_connection_string,
        "base_output_path": storage.model_base_path(reach_id),
    }
    if settings.lulc_source:
        payload["lulc_source"] = settings.lulc_source
    return payload


def run_check(reach_id: int, runner: ContainerRunner) -> CheckResult:
    """Check one reach, and act on what the gap turns out to be."""
    processing.start_check(reach_id)  # stamped now, at the start, not at the end
    seen = observe.observe_reach(reach_id)

    snapshot = load_snapshot(reach_id)
    if snapshot is None:
        return CheckResult(reach_id, -1, "skipped", seen, note="no desired_state")

    event = activity.begin(
        reach_id,
        "check",
        snapshot.revision,
        {"observed": seen.get("model"), "changed": seen.get("changed")},
    )
    decision = gap.calculate(snapshot)
    result = CheckResult(reach_id, snapshot.revision, type(decision).__name__, seen)

    try:
        if isinstance(decision, gap.NoGap):
            # Clear any marker left by the job that produced this: the output is
            # here, so whatever was running is done with, whatever it reported.
            processing.clear_step(reach_id)
            recorded = processing.finish(reach_id, snapshot.revision)
            result.note = (
                "satisfied"
                if recorded
                else "not recorded: intent moved while this check ran"
            )

        elif isinstance(decision, gap.InFlight):
            processing.mark_unsatisfied(reach_id)
            result.note = f"{decision.step} already running, left alone"

        elif isinstance(decision, gap.RunStep):
            processing.mark_unsatisfied(reach_id)
            ref = runner.submit(decision.step, _build_model_payload(reach_id))
            processing.mark_in_flight(reach_id, decision.step, ref, snapshot.revision)
            # Ask to be looked at again, so the result gets noticed without
            # waiting for the next sweep.
            queue.request_check(reach_id)
            result.submitted_ref = ref
            result.note = f"submitted {decision.step} ({ref[:12]})"

    except Exception as exc:  # submission failed; the reach must not stall
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

    activity.end(event, "ok", {"decision": result.decision, "note": result.note})
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
