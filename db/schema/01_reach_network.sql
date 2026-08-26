-- 01_reach_network.sql
-- 
-- ---------------------------------------------------------------------------
-- Lakes and coastal polygons the network drains into.
-- 
-- They exist because a reach's downstream boundary condition needs a polygon
-- saying where the outflow applies. For an ordinary reach that polygon is the
-- inundated area of the reach below it. For a terminal reach there is no reach
-- below, and the polygon comes from whatever the reach terminates into — a lake
-- or the coast.
CREATE TABLE IF NOT EXISTS lakes(
    lake_id text PRIMARY KEY,
    geom geometry(MultiPolygon, 5070) NOT NULL
);

CREATE INDEX IF NOT EXISTS lakes_geom_gix ON lakes USING GIST(geom);

COMMENT ON TABLE lakes IS 'Lakes the network drains into. A reach terminating at a lake uses its polygon as the outflow area for the nd job.';

CREATE TABLE IF NOT EXISTS coasts(
    coast_id text PRIMARY KEY,
    geom geometry(MultiPolygon, 5070) NOT NULL
);

CREATE INDEX IF NOT EXISTS coasts_geom_gix ON coasts USING GIST(geom);

COMMENT ON TABLE coasts IS 'Coastal polygons the network drains into. Same role as lakes for reaches terminating at the coast.';

-- ---------------------------------------------------------------------------
-- reach_network: the modeling (operational) network after network modification
-- Deferred:
-- merged_reaches   modeling-reach <- source-reach merge traceback
-- reach_exclusion  source reaches dropped in modification step (lake/coast)
CREATE TABLE IF NOT EXISTS reach_network(
    reach_id bigint PRIMARY KEY,
    -- Downstream modeling reach. NULL at terminals. Self FK is DEFERRABLE so a whole
    -- modified network loads in any row order inside one transaction.
    reach_to_id bigint REFERENCES reach_network(reach_id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    is_headwater boolean NOT NULL DEFAULT FALSE, -- no upstream modeling reach
    is_terminal boolean NOT NULL DEFAULT FALSE, -- no downstream modeling reach
    -- Why this reach has no modeling downstream. Drives nothing structurally but
    -- records intent and lets the gap calculation / QC distinguish a true outlet
    -- from a network break at a lake (DR-037 ALT-B) or coast (DR-038).
    terminal_reason text CONSTRAINT reach_network_terminal_reason_chk CHECK (terminal_reason IS NULL OR terminal_reason
	IN ('outlet', 'lake', 'coast')),
    -- Lake adjacency tags (DR-007.3). Independent flags: a short reach between two
    -- lakes can be both. lake_outlet reaches need the special offset inflow BC
    -- because their upstream is the lake, not a mainstem reach (DR-007.4).
    lake_inlet boolean NOT NULL DEFAULT FALSE, -- lake is downstream of the reach
    lake_outlet boolean NOT NULL DEFAULT FALSE, -- lake is upstream of the reach
    -- TRUE if the modeling geometry was clipped at a lake boundary, i.e.
    -- geom differs from the source reach geometry.
    is_trimmed boolean NOT NULL DEFAULT FALSE,
    total_da_sqkm double precision,
    stream_order integer,
    length_km double precision,
    -- Reach centerline slope. Still required because build_model reads it
    -- (REACH_FIELDS in twod-fim-jobs) and records it as the model's
    -- properties.slope, which becomes the normal-depth boundary condition. The
    -- hydrofabric no longer supplies it, so the loader writes a placeholder;
    -- this column will be deleted in future.
    slope double precision,
    -- What this reach terminates into, when it terminates. The polygon behind
    -- one of these is the outflow area for the nd job.
    -- An ordinary reach uses the inundated area of the reach below it, a
    -- terminal reach has no reach below and uses this instead.
    lake_to_id text REFERENCES lakes(lake_id),
    coast_to_id text REFERENCES coasts(coast_id),
    -- Modeling reach centerline, EPSG:5070
    geom geometry(LineString, 5070) NOT NULL,
    CONSTRAINT reach_network_terminal_link_chk CHECK (NOT is_terminal OR reach_to_id IS NULL),
    CONSTRAINT reach_network_terminal_reason_presence_chk CHECK (is_terminal =(terminal_reason IS NOT NULL)),
    -- A reach that terminates at a lake or coast must say which one, or nothing
    -- downstream of it will ever run.
    CONSTRAINT reach_network_lake_terminal_chk CHECK (terminal_reason IS DISTINCT FROM 'lake' OR lake_to_id IS NOT NULL),
    CONSTRAINT reach_network_coast_terminal_chk CHECK (terminal_reason IS DISTINCT FROM 'coast' OR coast_to_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS reach_network_reach_to_id_idx ON reach_network(reach_to_id);

CREATE INDEX IF NOT EXISTS reach_network_geom_gix ON reach_network USING GIST(geom);

COMMENT ON TABLE reach_network IS 'Modeling (operational) network derived from Hydrofabric after network modification per SDR';

COMMENT ON COLUMN reach_network.reach_to_id IS 'NULL at terminals.';

COMMENT ON COLUMN reach_network.terminal_reason IS 'Why this reach has no downstream reach: outlet | lake | coast.';

COMMENT ON COLUMN reach_network.lake_inlet IS 'Outlet of this reach is lake.';

COMMENT ON COLUMN reach_network.lake_outlet IS 'Lake is upstream of this reach.';

COMMENT ON COLUMN reach_network.is_trimmed IS 'Geometry was clipped at a lake boundary, so geom differs from the hydrofabric geometry.';

COMMENT ON COLUMN reach_network.total_da_sqkm IS 'Total drainage area (km2); used by build_model for bankfull width estimation.';

COMMENT ON COLUMN reach_network.stream_order IS 'Strahler stream order from the hydrofabric.';

COMMENT ON COLUMN reach_network.slope IS 'Reach centerline slope from the hydrofabric.';

COMMENT ON COLUMN reach_network.geom IS 'Reach centerline, EPSG:5070; basis of model identity reach_geom_hash.';

-- CREATE TABLE IF NOT EXISTS merged_reaches (
-- source_reach_id BIGINT PRIMARY KEY,
-- reach_id		   BIGINT NOT NULL
--	       REFERENCES reach_network (reach_id) ON DELETE CASCADE
-- );
-- -- For "members of reach X" lookup.
-- CREATE INDEX IF NOT EXISTS merged_reaches_reach_id_idx ON merged_reaches (reach_id);
-- -- Source reaches removed by network modification (not modeling).
-- CREATE TABLE IF NOT EXISTS reach_exclusion (
-- source_reach_id BIGINT PRIMARY KEY,	      -- hydrofabric id
-- reason       TEXT NOT NULL
--	       CONSTRAINT reach_exclusion_reason_chk
--	       CHECK (reason IN ('lake', 'coast')),
-- note		   TEXT
-- );
