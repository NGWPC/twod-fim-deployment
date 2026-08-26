"""The job status pass: ask what became of the jobs the loop is waiting on.

The second sweep, and it needs no more state than the first one. The jobs being
waited on are the reach_processing rows with a marker set, so the work list is a
question put to the database. A reconciler that dies mid-pass loses nothing —
the next one asks again and gets the same list.

It writes nothing about what exists. That stays the check's job, so there is
exactly one path by which current_state is ever written. All this pass does is
clear markers, count failures, and ask for checks.

Losing this pass entirely would not make the system wrong, only slower: the
sweep checks in-flight reaches anyway, and observe records whatever storage
holds. It exists to make the answer arrive sooner.
"""

import logging

import psycopg

from recon import db, processing, queue
from recon.workers import ContainerRunner, JobStatus

logger = logging.getLogger(__name__)

# How long a job the execution system cannot account for is left alone before
# the loop gives up on it. This is not a limit on how long a job may run — the
# reconciler has no opinion on that, because wall time is queue time plus run
# time and there is no honest number to guess. It only bounds how long we wait
# on a job nobody can find, and being wrong costs one duplicate submission.
UNKNOWN_GRACE_SECONDS = 900


def status_pass(
    runner: ContainerRunner, *, conn: psycopg.Connection | None = None
) -> list[dict]:
    """Poll every in-flight job once and act on the ones that have finished.

    Returns one row per job looked at, so a notebook can show the pass rather
    than just its effects.
    """
    outcomes = []
    for job in processing.in_flight(conn=conn):
        reach_id, ref, step = job["reach_id"], job["current_step_ref"], job["current_step"]
        elapsed = job["elapsed"].total_seconds() if job["elapsed"] else 0.0
        status = runner.poll(ref) if ref else JobStatus.UNKNOWN

        outcome = {"reach_id": reach_id, "step": step, "ref": ref,
                   "status": status.value, "elapsed_s": round(elapsed), "action": "left alone"}

        if status is JobStatus.SUCCEEDED:
            # Say nothing about what was produced. The check will look at
            # storage and decide; a job reporting success is not evidence.
            processing.clear_step(reach_id, ref, conn=conn)
            queue.request_check(reach_id, conn=conn)
            _reap(runner, ref)
            outcome["action"] = "cleared, check requested"

        elif status is JobStatus.FAILED:
            # Recorded here rather than left for the check, because a check
            # cannot tell a failed job from one that never ran — it would
            # resubmit immediately, forever, with no backoff.
            detail = getattr(runner, "logs", lambda _r: "")(ref) or "job reported failure"
            result = processing.record_failure(reach_id, detail, conn=conn)
            queue.request_check(reach_id, conn=conn)
            _reap(runner, ref)
            outcome["action"] = (
                f"failed ({result['consecutive_failures']}x)"
                + (", halted" if result["halted"] else f", retry at {result['next_retry_at']:%H:%M:%S}")
            )

        elif status is JobStatus.UNKNOWN and elapsed > UNKNOWN_GRACE_SECONDS:
            # No failure recorded: we do not know that it failed. Clear the
            # marker and let the next check look at storage — if the output is
            # there the job succeeded and we simply lost sight of it, and if it
            # is not, the gap reopens and the work is submitted again.
            processing.clear_step(reach_id, ref, conn=conn)
            queue.request_check(reach_id, conn=conn)
            outcome["action"] = "lost track, marker cleared"

        outcomes.append(outcome)
        logger.info("reach %s %s: %s -> %s", reach_id, step, status.value, outcome["action"])

    return outcomes


def _reap(runner: ContainerRunner, ref: str | None) -> None:
    """Discard the execution record once its outcome has been acted on.

    Only after, never before: reaping is what turns a job into an UNKNOWN one.
    """
    if ref and hasattr(runner, "reap"):
        runner.reap(ref)
