-- reach_network: the modeling (operational) network after network modification
-- Deferred:
--   merged_reaches   modeling-reach <- source-reach merge traceback
--   reach_exclusion  source reaches dropped in modification step (lake/coast)

CREATE TABLE IF NOT EXISTS reach_network (
    -- Modeling reach id, assigned by the network-modification step. Convention:
    -- the most-downstream member's hydrofabric id (see merged_reaches).
    reach_id        BIGINT PRIMARY KEY,

    -- Downstream modeling reach. NULL at terminals. Self FK is DEFERRABLE so a whole
    -- modified network loads in any row order inside one transaction.
    reach_to_id     BIGINT
        REFERENCES reach_network (reach_id)
        ON DELETE SET NULL
        DEFERRABLE INITIALLY DEFERRED,

    is_headwater    BOOLEAN NOT NULL DEFAULT FALSE,   -- no upstream modeling reach
    is_terminal     BOOLEAN NOT NULL DEFAULT FALSE,   -- no downstream modeling reach

    -- Why this reach has no modeling downstream. Drives nothing structurally but
    -- records intent and lets the cascade/QC distinguish a true outlet from a
    -- network break at a lake (DR-037 ALT-B) or coast (DR-038).
    terminal_reason TEXT
        CONSTRAINT reach_network_terminal_reason_chk
        CHECK (terminal_reason IS NULL OR terminal_reason IN ('outlet', 'lake', 'coast')),

    -- Lake adjacency tags (DR-007.3). Independent flags: a short reach between two
    -- lakes can be both. lake_outlet reaches need the special offset inflow BC
    -- because their upstream is the lake, not a mainstem reach (DR-007.4).
    lake_inlet      BOOLEAN NOT NULL DEFAULT FALSE,   -- lake is downstream of the reach
    lake_outlet     BOOLEAN NOT NULL DEFAULT FALSE,   -- lake is upstream of the reach

    -- TRUE if the modeling geometry was clipped at a lake boundary, i.e.
    -- geom differs from the source reach geometry.
    is_trimmed      BOOLEAN NOT NULL DEFAULT FALSE,

    -- Modeling reach centerline, EPSG:5070
    geom            geometry(LineString, 5070) NOT NULL,

    -- A terminal reach has no in-scope downstream link, and carries a reason.
    CONSTRAINT reach_network_terminal_link_chk
        CHECK (NOT is_terminal OR reach_to_id IS NULL),
    CONSTRAINT reach_network_terminal_reason_presence_chk
        CHECK (is_terminal = (terminal_reason IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS reach_network_reach_to_id_idx ON reach_network (reach_to_id);
CREATE INDEX IF NOT EXISTS reach_network_geom_gix        ON reach_network USING GIST (geom);



-- CREATE TABLE IF NOT EXISTS merged_reaches (
--     source_reach_id BIGINT PRIMARY KEY,
--     reach_id        BIGINT NOT NULL
--         REFERENCES reach_network (reach_id) ON DELETE CASCADE
-- );

-- -- For "members of reach X" lookup.
-- CREATE INDEX IF NOT EXISTS merged_reaches_reach_id_idx ON merged_reaches (reach_id);

-- -- Source reaches removed by network modification (not modeling).
-- CREATE TABLE IF NOT EXISTS reach_exclusion (
--     source_reach_id BIGINT PRIMARY KEY,        -- hydrofabric id
--     reason          TEXT NOT NULL
--         CONSTRAINT reach_exclusion_reason_chk
--         CHECK (reason IN ('lake', 'coast')),
--     note            TEXT
-- );
