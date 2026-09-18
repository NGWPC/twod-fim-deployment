"""Job polling.

Polls the jobs the loop is waiting on and acts on the ones that have finished.
"""

import logging

import psycopg

from recon import processing, queue
from recon.execution import ExecutionService, JobStatus

logger = logging.getLogger(__name__)

UNKNOWN_GRACE_SECONDS = 900


def poll_in_flight(
    execution: ExecutionService, *, conn: psycopg.Connection | None = None
) -> list[dict]:
    """Poll every in-flight job once and act on the ones that have finished."""
    outcomes = []
    for job in processing.in_flight(conn=conn):
        reach_id, ref, step = job["reach_id"], job["current_step_ref"], job["current_step"]
        elapsed = job["elapsed"].total_seconds() if job["elapsed"] else 0.0
        status = execution.poll(ref) if ref else JobStatus.UNKNOWN

        outcome = {"reach_id": reach_id, "step": step, "ref": ref,
                   "status": status.value, "elapsed_s": round(elapsed), "action": "left alone"}

        if status is JobStatus.SUCCEEDED:
            processing.clear_step(reach_id, ref, conn=conn)
            queue.request_check(reach_id, conn=conn)
            outcome["action"] = "cleared, check requested"

        elif status is JobStatus.FAILED:
            detail = getattr(execution, "logs", lambda _r: "")(ref) or "job reported failure"
            result = processing.record_failure(reach_id, detail, conn=conn)
            queue.request_check(reach_id, conn=conn)
            outcome["action"] = (
                f"failed ({result['consecutive_failures']}x)"
                + (", halted" if result["halted"] else f", retry at {result['next_retry_at']:%H:%M:%S}")
            )

        elif status is JobStatus.UNKNOWN and elapsed > UNKNOWN_GRACE_SECONDS:
            processing.clear_step(reach_id, ref, conn=conn)
            queue.request_check(reach_id, conn=conn)
            outcome["action"] = "lost track, marker cleared"

        outcomes.append(outcome)
        logger.info("reach %s %s: %s -> %s", reach_id, step, status.value, outcome["action"])

    return outcomes
