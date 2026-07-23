# db/schema

This directory holds DDL for the 2D-FIM database.

## Conventions

_Not an exhaustive list_

- `reach_id` `BIGINT` — external hydrofabric identifier, PK of `reach_network`.
- `*_hash` `CHAR(8)` — first-8-hex SHA-256 identity hash (`model.schema.json`).
- `model_id` — `"<identity_hash>+<domain_code>"`; in `current_state` it is a
  **generated** column so it can never drift from its parts.
- Geometry is `EPSG:5070` (CONUS Albers, metres) to match model outputs.
- Vocabularies (`solver`, `bc_type`) are `TEXT` + `CHECK`, not `ENUM`, so the
  methodology can add an engine without an `ALTER TYPE` migration.

## Schema Execution

Files are numbered for dependency order and are re-runnable (`IF NOT EXISTS`,
generated-column / CHECK guards).  Docker entrypoint applies them in lexical order when database is first created.