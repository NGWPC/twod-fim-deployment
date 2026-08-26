# db/schema

This directory holds DDL for the 2D-FIM database.

## Conventions

_Not an exhaustive list_

- `reach_id` `BIGINT` — external hydrofabric identifier, PK of `reach_network`.
- `*_hash` `CHAR(8)` — first-8-hex SHA-256 identity hash (`model.schema.json`).
- `model_id` — `"<identity_hash>_<domain_code>"`, the model folder name in
  storage; in `materialized_models` it is a **generated** column so it cannot drift
  from its parts.
- Tables split by who owns them and whether they can be rebuilt:
  `desired_state` + `desired_state_defaults` are authored intent;
  `materialized_*` record whether intent has been materialized (rebuildable by
  looking at storage again); `reach_processing` + `reach_activity` are the
  reconciler's own notes (not rebuildable).
- The `materialized_*` tables are not an inventory of S3. They answer "is the
  thing intent asks for there", at the address intent implies. S3 is the
  inventory, queried when someone wants one.
- Geometry is `EPSG:5070` (CONUS Albers, metres) to match model outputs.
- Vocabularies (`solver`, `bc_type`) are `TEXT` + `CHECK`, not `ENUM`, so the
  methodology can add an engine without an `ALTER TYPE` migration.

## Schema Execution

Files are numbered for dependency order and are re-runnable (`IF NOT EXISTS`,
generated-column / CHECK guards).  Docker entrypoint applies them in lexical order when database is first created.