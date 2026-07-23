-- 00_extensions.sql
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS twodfim;

DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET search_path = twodfim, public', current_database());
END
$$;
