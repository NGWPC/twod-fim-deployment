"""Effective intent.

What is actually wanted for a reach, with per-reach values and the
deployment-wide defaults resolved into one row.
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
        rn.lake_outlet,
        rn.total_da_sqkm,
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
        d.initial_dq_step_for_nd,
        d.q_grid_resolution,
        d.q_set,
        d.model_domain,
        COALESCE(d.ld_q_max_depth_increase_range,
                 f.ld_q_max_depth_increase_range) AS ld_q_max_depth_increase_range,
        COALESCE(d.ld_q_median_depth_increase_range,
                 f.ld_q_median_depth_increase_range) AS ld_q_median_depth_increase_range,
        COALESCE(d.ld_q_flooded_area_prcnt_increase_range,
                 f.ld_q_flooded_area_prcnt_increase_range)
                 AS ld_q_flooded_area_prcnt_increase_range,
        COALESCE(d.ld_ds_z_delta,    f.ld_ds_z_delta)    AS ld_ds_z_delta,
        COALESCE(d.kwse_upper_bound, f.kwse_upper_bound) AS kwse_upper_bound
    FROM desired_state d
    JOIN reach_network rn USING (reach_id)
    CROSS JOIN desired_state_defaults f
    WHERE d.reach_id = %s
"""


def effective(reach_id: str, *, conn: psycopg.Connection | None = None) -> db.Row | None:
    """This reach's effective intent, or None if nothing is wanted for it."""
    return db.one(_EFFECTIVE, (reach_id,), conn=conn)


def defaults_missing(*, conn: psycopg.Connection | None = None) -> bool:
    """True when the singleton defaults row has not been seeded."""
    return db.one("SELECT 1 AS x FROM desired_state_defaults", conn=conn) is None
