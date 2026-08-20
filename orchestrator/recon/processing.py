"""The reconciler's notes on a reach: what is in flight, and what has failed.

Every write to `reach_processing` that is not a check request goes through
here, so the rules about when a marker may be cleared or a revision recorded
live in one place.

Work only: whether intent is satisfied lives in the materialized_* tables,
written by observe, so a proof can never outlive the thing it proves.

clear_step stays conditional on the job reference still matching, so a slow
status pass cannot wipe a marker belonging to a newer submission — a decision
made against state that has since moved simply does not take effect.
"""

import psycopg

from recon import db
from recon.config import settings

# How long a failing reach waits before being looked at again. Doubles each
# consecutive failure up to the cap, then stops growing.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600
# Consecutive failures after which a reach is parked for a person.
# Halt threshold lives in settings so it can be tightened while developing.


def start_check(reach_id: int, *, conn: psycopg.Connection | None = None) -> None:
    """Stamp last_checked_at at the moment a check begins.

    At the beginning, never at the end. A check asks for its own next check
    before it finishes, so stamping at the end would cancel that request and a
    reach would never get past its first step.

    clock_timestamp() rather than now(): now() is the transaction's start time,
    so a check that stamped this and then requested its own next check inside
    one transaction would write two identical timestamps, and
    check_requested_at > last_checked_at would be false. The request would
    vanish and the reach would stall until the next sweep.
    """
    db.query(
        """
        INSERT INTO reach_processing (reach_id, last_checked_at)
        VALUES (%s, clock_timestamp())
        ON CONFLICT (reach_id) DO UPDATE SET last_checked_at = clock_timestamp()
        """,
        (reach_id,),
        conn=conn,
    )


def mark_in_flight(
    reach_id: int,
    step: str,
    ref: str,
    revision: int,
    *,
    conn: psycopg.Connection | None = None,
) -> None:
    """Record that a job has been submitted and nothing has been seen of it yet.

    Written immediately after submission. The reference matters as much as the
    step: it is the handle the job status pass uses to ask what became of the
    job, so losing it means losing track of that job entirely.
    """
    db.query(
        """
        INSERT INTO reach_processing
            (reach_id, current_step, current_step_started_at,
             current_step_ref, current_step_revision)
        VALUES (%s, %s, now(), %s, %s)
        ON CONFLICT (reach_id) DO UPDATE SET
            current_step = EXCLUDED.current_step,
            current_step_started_at = now(),
            current_step_ref = EXCLUDED.current_step_ref,
            current_step_revision = EXCLUDED.current_step_revision
        """,
        (reach_id, step, ref, revision),
        conn=conn,
    )


def clear_step(
    reach_id: int, ref: str | None = None, *, conn: psycopg.Connection | None = None
) -> bool:
    """Clear the in-flight marker. Returns whether anything was cleared.

    Pass the reference a job status pass polled, and the clear applies only if
    the marker still refers to that job. Without it a slow pass could wipe out a
    marker belonging to a newer submission, and the newer job would be
    resubmitted for no reason.

    Omit the reference when the caller has just observed the output and knows
    the reach is satisfied whatever was running.
    """
    sql = """
        UPDATE reach_processing SET
            current_step = NULL,
            current_step_started_at = NULL,
            current_step_ref = NULL,
            current_step_revision = NULL
        WHERE reach_id = %s AND current_step IS NOT NULL
    """
    params: tuple = (reach_id,)
    if ref is not None:
        sql += " AND current_step_ref = %s"
        params = (reach_id, ref)
    return bool(db.query(sql + " RETURNING reach_id", params, conn=conn))


def wait_on(
    reach_id: int, downstream_reach_id: int | None, *, conn: psycopg.Connection | None = None
) -> None:
    """Record which reach this one is waiting for; None clears it.

    Purely so a viewer can draw the wait graph without recomputing every gap.
    The waiting reach stays a check candidate regardless; the downstream reach
    requests a check here when it finishes.
    """
    db.query(
        """
        INSERT INTO reach_processing (reach_id, blocked_on_reach_id)
        VALUES (%s, %s)
        ON CONFLICT (reach_id) DO UPDATE SET blocked_on_reach_id = EXCLUDED.blocked_on_reach_id
        """,
        (reach_id, downstream_reach_id),
        conn=conn,
    )


def record_failure(
    reach_id: int, error: str, *, conn: psycopg.Connection | None = None
) -> db.Row:
    """Count a failure, back off, and park the reach if it has failed enough.

    The backoff is what stops a broken reach from consuming the loop: it is
    looked at less and less often. Halting is the end of that line — a reach
    that has failed this many times in a row is not going to fix itself, and
    something is being asked of a person.
    """
    return db.query(
        """
        INSERT INTO reach_processing (reach_id, consecutive_failures, last_error,
                                      next_retry_at, current_step,
                                      current_step_started_at, current_step_ref,
                                      current_step_revision)
        VALUES (%(reach)s, 1, %(error)s, now() + make_interval(secs => %(base)s), NULL, NULL, NULL, NULL)
        ON CONFLICT (reach_id) DO UPDATE SET
            consecutive_failures = reach_processing.consecutive_failures + 1,
            last_error = EXCLUDED.last_error,
            next_retry_at = now() + make_interval(secs => LEAST(
                %(base)s * power(2, reach_processing.consecutive_failures),
                %(cap)s)),
            halted = (reach_processing.consecutive_failures + 1) >= %(halt_after)s,
            halted_at = CASE
                WHEN (reach_processing.consecutive_failures + 1) >= %(halt_after)s
                THEN now() END,
            current_step = NULL,
            current_step_started_at = NULL,
            current_step_ref = NULL,
            current_step_revision = NULL
        RETURNING consecutive_failures, next_retry_at, halted
        """,
        {
            "reach": reach_id,
            "error": error[:2000],
            "base": BACKOFF_BASE_SECONDS,
            "cap": BACKOFF_CAP_SECONDS,
            "halt_after": settings.halt_after_failures,
        },
        conn=conn,
    )[0]


def clear_failures(reach_id: int, *, conn: psycopg.Connection | None = None) -> bool:
    """Forget a failure streak because work has landed. Returns whether it had one.

    "Consecutive" is only true if something ends the run, and success is the only
    honest thing that can. Without this the counter is cumulative for the reach's
    lifetime: four failures spread over a week, a success, then one more failure
    would halt a reach whose failures were never consecutive.

    Deliberately NOT reset on NoGap alone. A reach that failed to build, then
    built, reports RunStep for its next rung rather than NoGap — so keying off
    NoGap would let a streak survive a genuine success. What ends a streak is a
    step's work landing, which is what an adoption is.

    last_error is left alone: it remains a true record of the last thing that
    went wrong, and reading it next to a zero counter says "this recovered".
    """
    return bool(db.query(
        """
        UPDATE reach_processing SET consecutive_failures = 0, next_retry_at = NULL
        WHERE reach_id = %s AND (consecutive_failures > 0 OR next_retry_at IS NOT NULL)
        RETURNING reach_id
        """,
        (reach_id,), conn=conn))


def clear_halt(reach_id: int, *, conn: psycopg.Connection | None = None) -> None:
    """Un-park a reach, after a person has dealt with whatever was wrong.

    Resets the failure count as well: leaving it in place would halt the reach
    again on its very next failure, which is not what "try this again" means.
    """
    db.query(
        """
        UPDATE reach_processing SET
            halted = FALSE, halted_at = NULL,
            consecutive_failures = 0, next_retry_at = NULL,
            check_requested_at = now()
        WHERE reach_id = %s
        """,
        (reach_id,),
        conn=conn,
    )


def in_flight(*, conn: psycopg.Connection | None = None) -> list[db.Row]:
    """Every job the loop is waiting on. The queue for the job status pass.

    Same idea as due_reaches: the work list is a question asked of the database,
    so a reconciler that restarts asks it again and carries on. Nothing about
    which jobs are running lives in a process.
    """
    return db.query(
        """
        SELECT reach_id, current_step, current_step_ref, current_step_started_at,
               current_step_revision,
               now() - current_step_started_at AS elapsed
        FROM reach_processing
        WHERE current_step IS NOT NULL
        ORDER BY current_step_started_at
        """,
        conn=conn,
    )
