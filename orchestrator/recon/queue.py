"""Which reaches need looking at, and how to ask for one to be looked at.

There is no in-process queue. The queue is a question put to the database, and
every reach the answer names gets checked. That is what makes the loop
restartable: a reconciler that dies mid-sweep loses nothing, because the next
one asks the same question and gets the same list minus whatever finished.

See reconciliation-loop.md, "No In Process Queue - DB is the Queue".
"""

import psycopg

from recon import db

# The candidate query. A reach is due when something has changed or someone has
# asked, and it is neither parked nor resting.
#
# Note what is NOT excluded: a reach with a job in flight stays a candidate.
# That is deliberate and load-bearing — checking it is how a finished job gets
# noticed at all. Suppressing resubmission is the gap calculation's job, not
# this query's.
_DUE = """
    SELECT
        d.reach_id,
        d.revision,
        p.reach_id IS NULL                            AS never_checked,
        COALESCE(p.applied_revision, -1) < d.revision AS intent_moved,
        COALESCE(p.check_requested_at > p.last_checked_at, FALSE) AS was_asked,
        p.current_step
    FROM desired_state d
    LEFT JOIN reach_processing p USING (reach_id)
    WHERE (p.reach_id IS NULL
           OR COALESCE(p.applied_revision, -1) < d.revision
           OR p.check_requested_at > p.last_checked_at)
      AND NOT COALESCE(p.halted, FALSE)
      AND (p.next_retry_at IS NULL OR p.next_retry_at <= now())
    ORDER BY d.reach_id
"""


def due_reaches(
    limit: int | None = None, *, conn: psycopg.Connection | None = None
) -> list[db.Row]:
    """Reaches that need a check now.

    Each row carries why it is here — never_checked, intent_moved, was_asked —
    because a queue that cannot explain itself is hard to trust, and the
    notebook shows those columns directly.
    """
    sql = _DUE + ("\n    LIMIT %s" if limit is not None else "")
    return db.query(sql, (limit,) if limit is not None else None, conn=conn)


def request_check(reach_id: int, *, conn: psycopg.Connection | None = None) -> None:
    """Ask for a reach to be checked soon.

    One write, and requests collapse: several things asking before the next
    check all set the same column, so the reach gets one check rather than
    several. Carries no instructions — a check works everything out for itself,
    which is why losing one of these is survivable.

    Creates the processing row if the reach has never been touched, so callers
    never have to care whether one exists.
    """
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
    reach_id: int, *, conn: psycopg.Connection | None = None
) -> list[int]:
    """Ask for a check on every reach that flows into this one.

    Called after nd or kwse work, whose results are the boundary condition for
    the reaches above. Returns the reaches asked, for the activity log.
    """
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
