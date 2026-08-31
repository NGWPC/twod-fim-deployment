"""Effective intent: what is actually wanted for a reach, defaults resolved.

Intent lives in two tables. desired_state holds what is authored per reach,
nullable throughout; desired_state_defaults holds the single row everything
falls back to. Effective intent is the COALESCE of the two, and this module is
the one place that resolution happens, so nothing else ever reads the tables
half-resolved.

The geometry rides along as WKB because identity prediction hashes it, and
prediction must use the same bytes the job will read.
"""

import psycopg

from recon import db

_EFFECTIVE = """
    SELECT
        d.reach_id,
        d.revision,
        rn.reach_to_id,
        rn.is_terminal,
        rn.terminal_reason,
        rn.lake_to_id,
        rn.coast_to_id,
        ST_AsBinary(rn.geom) AS geom_wkb,
        f.sdr_commit,
        COALESCE(d.grid_resolution, f.grid_resolution) AS grid_resolution,
        COALESCE(d.epsg_code,       f.epsg_code)       AS epsg_code,
        COALESCE(d.dem_source,      f.dem_source)      AS dem_source,
        COALESCE(d.lulc_source,     f.lulc_source)     AS lulc_source,
        COALESCE(d.lulc_lookup,     f.lulc_lookup)     AS lulc_lookup,
        COALESCE(d.solver,          f.solver)          AS solver,
        d.q_lower_bound,
        d.q_upper_bound,
        d.initial_dq_step_for_nd
    FROM desired_state d
    JOIN reach_network rn USING (reach_id)
    CROSS JOIN desired_state_defaults f
    WHERE d.reach_id = %s
"""


def effective(reach_id: int, *, conn: psycopg.Connection | None = None) -> db.Row | None:
    """This reach's effective intent, or None if nothing is wanted for it.

    None has two causes worth telling apart when it surprises you: no
    desired_state row (a reach in the network means nothing until intent is
    authored), or an empty desired_state_defaults table (the deployment's
    fallback row has not been seeded, so no reach can resolve its intent).
    """
    return db.one(_EFFECTIVE, (reach_id,), conn=conn)


def defaults_missing(*, conn: psycopg.Connection | None = None) -> bool:
    """True when the singleton defaults row has not been seeded."""
    return db.one("SELECT 1 AS x FROM desired_state_defaults", conn=conn) is None
