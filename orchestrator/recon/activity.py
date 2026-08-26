"""Append-only history: one row each time something happens to a reach.

The only table with a time dimension, so it is what a timeline, a live feed, or
"why did this reach do that" is built from. Nothing reads it to make a decision
— a check works everything out from current state, never from history — which
is exactly why it is safe for it to be lossy, trimmed, or turned off.

Rows are opened when something starts and stamped when it ends, so an
unfinished row is visible as one with no ended_at.
"""

import json
from typing import Any

import psycopg

from recon import db


def begin(
    reach_id: int,
    action: str,
    revision: int | None = None,
    detail: dict[str, Any] | None = None,
    *,
    conn: psycopg.Connection | None = None,
) -> int:
    """Open a row for something that has just started. Returns its id."""
    row = db.one(
        """
        INSERT INTO reach_activity (reach_id, action, outcome, revision, detail)
        VALUES (%s, %s, 'running', %s, %s)
        RETURNING activity_id
        """,
        (reach_id, action, revision, json.dumps(detail) if detail else None),
        conn=conn,
    )
    return row["activity_id"]


def end(
    activity_id: int,
    outcome: str,
    detail: dict[str, Any] | None = None,
    error: str | None = None,
    *,
    conn: psycopg.Connection | None = None,
) -> None:
    """Close a row opened by begin().

    detail is merged rather than replaced, so what was known at the start
    survives alongside what was learned by the end.
    """
    db.query(
        """
        UPDATE reach_activity SET
            ended_at = now(),
            outcome = %s,
            detail = COALESCE(detail, '{}'::jsonb) || COALESCE(%s::jsonb, '{}'::jsonb),
            error = %s
        WHERE activity_id = %s
        """,
        (outcome, json.dumps(detail) if detail else None, error, activity_id),
        conn=conn,
    )


def recent(limit: int = 20, reach_id: int | None = None,
           *, conn: psycopg.Connection | None = None) -> list[db.Row]:
    """The latest events, newest first. For a notebook or a dashboard."""
    sql = """
        SELECT activity_id, reach_id, action, outcome, revision, detail, error,
               started_at, ended_at - started_at AS took
        FROM reach_activity
    """
    params: tuple = ()
    if reach_id is not None:
        sql += " WHERE reach_id = %s"
        params = (reach_id,)
    sql += " ORDER BY started_at DESC, activity_id DESC LIMIT %s"
    return db.query(sql, params + (limit,), conn=conn)
