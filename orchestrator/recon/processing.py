"""The reconciler's notes on a reach: what is in flight, and what has failed.

Every write to `reach_processing` that is not a check request goes through
here, so the rules about when a marker may be cleared or a revision recorded
live in one place.

Two of these writes are conditional, and that is the whole reason the loop can
run without marking reaches as taken. `finish` records a revision only if the
snapshot it came from is still the current one; `clear_step` clears a marker
only if it still refers to the job that was polled. Both are compare-and-set,
so a decision made against state that has since moved simply does not take
effect, rather than overwriting something newer.
"""

import psycopg

from recon import db

# How long a failing reach waits before being looked at again. Doubles each
# consecutive failure up to the cap, then stops growing.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600
# Consecutive failures after which a reach is parked for a person.
HALT_AFTER_FAILURES = 5


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


def finish(
    reach_id: int, revision: int, *, conn: psycopg.Connection | None = None
) -> bool:
    """Record that the gap was empty at this revision. Returns whether it took.

    Only ever called when there is no gap — never per step — so this column
    means "everything desired at this revision exists", not "some work
    happened".

    The write is conditional on the revision still being current. A check reads
    desired_state, spends time observing storage, and only then records; if
    intent moved during that window, recording the older revision would claim a
    reach is satisfied at a revision whose changes it never saw, and nothing
    would look at it again.
    """
    rows = db.query(
        """
        INSERT INTO reach_processing (reach_id, applied_revision, consecutive_failures,
                                      next_retry_at, last_error, blocked_on_reach_id)
        SELECT %s, %s, 0, NULL, NULL, NULL
        FROM desired_state d WHERE d.reach_id = %s AND d.revision = %s
        ON CONFLICT (reach_id) DO UPDATE SET
            applied_revision = EXCLUDED.applied_revision,
            consecutive_failures = 0,
            next_retry_at = NULL,
            last_error = NULL,
            blocked_on_reach_id = NULL
        RETURNING reach_id
        """,
        (reach_id, revision, reach_id, revision),
        conn=conn,
    )
    return bool(rows)


def mark_unsatisfied(reach_id: int, *, conn: psycopg.Connection | None = None) -> None:
    """Retract a claim that this reach is satisfied.

    applied_revision means "the gap was empty at this revision". The moment a
    check finds a gap, that is no longer true — the model may have been deleted,
    or work may simply not be finished — and leaving the old value there makes
    reach_status report a reach as caught up while it is being rebuilt.

    Called on every decision that is not NoGap, so the claim is only ever as old
    as the last check that found nothing missing.
    """
    db.query(
        "UPDATE reach_processing SET applied_revision = -1 WHERE reach_id = %s AND applied_revision <> -1",
        (reach_id,),
        conn=conn,
    )


def wait_on(
    reach_id: int, downstream_reach_id: int, *, conn: psycopg.Connection | None = None
) -> None:
    """Record which reach this one is waiting for.

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
            "halt_after": HALT_AFTER_FAILURES,
        },
        conn=conn,
    )[0]


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
