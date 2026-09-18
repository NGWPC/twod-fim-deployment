"""Processing state.

The loop's own notes on a reach: what is in flight, what has failed, and what
is parked.
"""

import psycopg

from recon import db
from recon.config import settings

BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600


def start_check(reach_id: str, *, conn: psycopg.Connection | None = None) -> None:
    """Stamp last_checked_at at the moment a check begins."""
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
    reach_id: str,
    step: str,
    ref: str,
    revision: int,
    *,
    conn: psycopg.Connection | None = None,
) -> None:
    """Record that a job has been submitted and nothing has been seen of it yet."""
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
    reach_id: str, ref: str | None = None, *, conn: psycopg.Connection | None = None
) -> bool:
    """Clear the in-flight marker."""
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
    reach_id: str, downstream_reach_id: str | None, *, conn: psycopg.Connection | None = None
) -> None:
    """Record which reach this one is waiting for; None clears it."""
    db.query(
        """
        INSERT INTO reach_processing (reach_id, blocked_on_reach_id)
        VALUES (%s, %s)
        ON CONFLICT (reach_id) DO UPDATE SET blocked_on_reach_id = EXCLUDED.blocked_on_reach_id
        """,
        (reach_id, downstream_reach_id),
        conn=conn,
    )


ERROR_KEEP_CHARS = 4000


def _tail(error: str) -> str:
    """The last ERROR_KEEP_CHARS of an error, marked if anything was dropped."""
    if len(error) <= ERROR_KEEP_CHARS:
        return error
    return f"[... {len(error) - ERROR_KEEP_CHARS} earlier characters dropped ...]\n" + \
        error[-ERROR_KEEP_CHARS:]


def record_failure(
    reach_id: str, error: str, *, conn: psycopg.Connection | None = None
) -> db.Row:
    """Count a failure, back off, and park the reach if it has failed enough."""
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
            "error": _tail(error),
            "base": BACKOFF_BASE_SECONDS,
            "cap": BACKOFF_CAP_SECONDS,
            "halt_after": settings.halt_after_failures,
        },
        conn=conn,
    )[0]


def clear_failures(reach_id: str, *, conn: psycopg.Connection | None = None) -> bool:
    """Forget a failure streak because work has landed."""
    return bool(db.query(
        """
        UPDATE reach_processing SET consecutive_failures = 0, next_retry_at = NULL
        WHERE reach_id = %s AND (consecutive_failures > 0 OR next_retry_at IS NOT NULL)
        RETURNING reach_id
        """,
        (reach_id,), conn=conn))


def clear_halt(reach_id: str, *, conn: psycopg.Connection | None = None) -> None:
    """Un-park a reach, after a person has dealt with whatever was wrong."""
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
    """Every job the loop is waiting on."""
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
