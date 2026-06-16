-- 00_extensions.sql

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS fim;


DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET search_path = fim, public', current_database());
END
$$;
