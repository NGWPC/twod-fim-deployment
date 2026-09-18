"""Work queue.

Which reaches need looking at, and how to ask for one to be looked at.
"""

import psycopg

from recon import db

_DUE = """
    SELECT
        d.reach_id,
        d.revision,
        p.reach_id IS NULL                             AS never_checked,
        (COALESCE(mm.applied_revision,  -1) < d.revision
         OR COALESCE(mnd.applied_revision, -1) < d.revision
         OR (NOT rn.is_terminal
             AND COALESCE(mkw.applied_revision, -1) < d.revision)) AS intent_moved,
        COALESCE(p.check_requested_at > p.last_checked_at, FALSE) AS outstanding_check_request,
        p.current_step
    FROM desired_state d
    JOIN reach_network rn USING (reach_id)
    LEFT JOIN reach_processing p USING (reach_id)
    LEFT JOIN materialized_models    mm  USING (reach_id)
    LEFT JOIN materialized_nd_runs   mnd USING (reach_id)
    LEFT JOIN materialized_kwse_runs mkw USING (reach_id)
    WHERE (p.reach_id IS NULL
           OR COALESCE(mm.applied_revision,  -1) < d.revision
           OR COALESCE(mnd.applied_revision, -1) < d.revision
           OR (NOT rn.is_terminal
               AND COALESCE(mkw.applied_revision, -1) < d.revision)
           OR p.check_requested_at > p.last_checked_at)
      AND NOT COALESCE(p.halted, FALSE)
      AND (p.next_retry_at IS NULL OR p.next_retry_at <= now())
    ORDER BY d.reach_id
"""


def due_reaches(
    limit: int | None = None, *, conn: psycopg.Connection | None = None
) -> list[db.Row]:
    """Reaches that need a check now."""
    sql = _DUE + ("\n    LIMIT %s" if limit is not None else "")
    return db.query(sql, (limit,) if limit is not None else None, conn=conn)


def request_check(reach_id: str, *, conn: psycopg.Connection | None = None) -> None:
    """Ask for a reach to be checked soon."""
    db.query(
        """
        INSERT INTO reach_processing (reach_id, check_requested_at)
        VALUES (%s, clock_timestamp())
        ON CONFLICT (reach_id) DO UPDATE SET check_requested_at = clock_timestamp()
        """,
        (reach_id,),
        conn=conn,
    )


def request_check_upstream(
    reach_id: str, *, conn: psycopg.Connection | None = None
) -> list[int]:
    """Ask for a check on every reach that flows into this one."""
    rows = db.query(
        """
        INSERT INTO reach_processing (reach_id, check_requested_at)
        SELECT reach_id, clock_timestamp() FROM reach_network WHERE reach_to_id = %s
        ON CONFLICT (reach_id) DO UPDATE SET check_requested_at = clock_timestamp()
        RETURNING reach_id
        """,
        (reach_id,),
        conn=conn,
    )
    return [r["reach_id"] for r in rows]
