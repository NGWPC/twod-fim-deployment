"""Activity history.

Append-only log: one row each time something happens to a reach.
"""

import json
from typing import Any

import psycopg

from recon import db


def begin(
    reach_id: str,
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
    """Close a row opened by begin()."""
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


def recent(limit: int = 20, reach_id: str | None = None,
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
