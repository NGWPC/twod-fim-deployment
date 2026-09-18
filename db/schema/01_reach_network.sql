CREATE TABLE IF NOT EXISTS lakes(
    lake_id text PRIMARY KEY,
    geom geometry(MultiPolygon, 5070) NOT NULL
);

CREATE INDEX IF NOT EXISTS lakes_geom_gix ON lakes USING GIST(geom);

CREATE TABLE IF NOT EXISTS coasts(
    coast_id text PRIMARY KEY,
    geom geometry(MultiPolygon, 5070) NOT NULL
);

CREATE INDEX IF NOT EXISTS coasts_geom_gix ON coasts USING GIST(geom);

CREATE TABLE IF NOT EXISTS reach_network(
    reach_id text PRIMARY KEY,

    reach_to_id text REFERENCES reach_network(reach_id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    is_headwater boolean NOT NULL DEFAULT FALSE,
    is_terminal boolean NOT NULL DEFAULT FALSE,

    terminal_reason text CONSTRAINT reach_network_terminal_reason_chk CHECK (terminal_reason IS NULL OR terminal_reason
	IN ('outlet', 'lake', 'coast')),

    lake_inlet boolean NOT NULL DEFAULT FALSE,
    lake_outlet boolean NOT NULL DEFAULT FALSE,

    is_trimmed boolean NOT NULL DEFAULT FALSE,

    total_da_sqkm double precision NOT NULL CONSTRAINT reach_network_da_positive_chk CHECK (total_da_sqkm > 0),
    stream_order integer,
    length_km double precision,

    lake_to_id text REFERENCES lakes(lake_id),
    coast_to_id text REFERENCES coasts(coast_id),

    geom geometry(LineString, 5070) NOT NULL,
    CONSTRAINT reach_network_terminal_link_chk CHECK (NOT is_terminal OR reach_to_id IS NULL),
    CONSTRAINT reach_network_terminal_reason_presence_chk CHECK (is_terminal =(terminal_reason IS NOT NULL)),

    CONSTRAINT reach_network_lake_terminal_chk CHECK (terminal_reason IS DISTINCT FROM 'lake' OR lake_to_id IS NOT NULL),
    CONSTRAINT reach_network_coast_terminal_chk CHECK (terminal_reason IS DISTINCT FROM 'coast' OR coast_to_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS reach_network_reach_to_id_idx ON reach_network(reach_to_id);

CREATE INDEX IF NOT EXISTS reach_network_geom_gix ON reach_network USING GIST(geom);
