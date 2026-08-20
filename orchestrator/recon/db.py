"""Database access for the reconciliation loop.

The only module that names psycopg, so swapping the driver or the connection
strategy is one file's problem. Functions rather than a class holding a
connection: per guide.md the code is function shaped, with no load-mutate-save
lifecycle. The database is the brain — an object caching parts of it would just
be a second, staler copy.

Every helper takes an optional `conn`. Pass one when several statements have to
land together; leave it out and the statement gets its own connection and
commits on its own.
"""

from collections.abc import Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from recon.config import settings

Row = dict[str, Any]


@contextmanager
def connect(dsn: str | None = None):
    """Open one connection to the twodfim database.

    Commits on a clean exit, rolls back if the block raises.
    """
    with psycopg.connect(
        dsn or settings.pipeline_db_connection_string, row_factory=dict_row
    ) as conn:
        yield conn


def query(
    sql: str,
    params: Sequence[Any] | None = None,
    *,
    conn: psycopg.Connection | None = None,
) -> list[Row]:
    """Run one statement; return its rows, or [] if it returns none."""
    if conn is not None:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []
    with connect() as own:
        cur = own.execute(sql, params)
        return cur.fetchall() if cur.description else []


def one(
    sql: str,
    params: Sequence[Any] | None = None,
    *,
    conn: psycopg.Connection | None = None,
) -> Row | None:
    """Run one statement expected to match at most one row."""
    rows = query(sql, params, conn=conn)
    return rows[0] if rows else None


def table_counts(*, conn: psycopg.Connection | None = None) -> list[Row]:
    """Exact row count of every table the loop touches. A cheap "where am I"."""
    return query(
        """
        SELECT 'reach_network' AS table_name, count(*) AS rows FROM reach_network
        UNION ALL SELECT 'lakes',                 count(*) FROM lakes
        UNION ALL SELECT 'coasts',                count(*) FROM coasts
        UNION ALL SELECT 'desired_state_defaults', count(*) FROM desired_state_defaults
        UNION ALL SELECT 'desired_state',          count(*) FROM desired_state
        UNION ALL SELECT 'materialized_models',    count(*) FROM materialized_models
        UNION ALL SELECT 'materialized_nd_runs',   count(*) FROM materialized_nd_runs
        UNION ALL SELECT 'materialized_kwse_runs', count(*) FROM materialized_kwse_runs
        UNION ALL SELECT 'reach_processing',       count(*) FROM reach_processing
        UNION ALL SELECT 'reach_activity',         count(*) FROM reach_activity
        ORDER BY table_name
        """,
        conn=conn,
    )
