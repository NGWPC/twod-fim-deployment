#!/usr/bin/env bash
set -euo pipefail

DAGSTER_PG_DB="${DAGSTER_PG_DB:-dagster}"

psql \
  -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=dagster_db="$DAGSTER_PG_DB" <<'EOSQL'
SELECT format('CREATE DATABASE %I', :'dagster_db')
WHERE NOT EXISTS (
  SELECT FROM pg_database WHERE datname = :'dagster_db'
)\gexec
EOSQL
