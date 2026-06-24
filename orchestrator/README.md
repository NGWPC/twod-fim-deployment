# Orchestrator

Dagster-based reconciliation loop for 2D flood inundation model building. Polls
the DB for stale reaches and processes them downstream-first (terminals before
upstream).

Design references: [`twod-fim-knowledge-base/system-design/`](https://github.com/NGWPC/twod-fim-knowledge-base/tree/main/system-design)
(`guide.md`, `triggers-and-propagation.md`).

## Key schema contracts

See [`db/schema/`](../db/schema/) for full definitions.

- `current_state.model_id` is `GENERATED ALWAYS` from `identity_hash + domain_code` — never written directly ([`04_current_state.sql`](../db/schema/04_current_state.sql))
- `desired_state.revision` is auto-incremented by a `BEFORE UPDATE` trigger — the app never writes it ([`09_triggers.sql`](../db/schema/09_triggers.sql))
- `runs` table has a composite FK to `current_state(reach_id, identity_hash)` — old runs are cleared before identity changes ([`05_runs.sql`](../db/schema/05_runs.sql))
- `processing` flag is reserved for the future cascade coordinator (build -> nd -> kwse); individual workers do not manage it ([`04_current_state.sql`](../db/schema/04_current_state.sql))
- Double-submission guard: Dagster `run_key` dedup (primary), not the processing flag

## Prerequisites

- Docker
- [uv](https://docs.astral.sh/uv/) for maintenance commands (e.g. regenerating `uv.lock`)

## Local dev setup

### 1. Environment

Copy `example.env` to `.env` at the repo root:

```bash
cp example.env .env
```

`orchestrator/.env` is a symlink to `../.env` - one file serves both docker-compose and host-local orchestrator commands. Git preserves the symlink on clone. If deleted, recreate with `ln -s ../.env orchestrator/.env`.

The compose `orchestrator` service overrides container-only values such as
`POSTGRES_HOST`, `AWS_ENDPOINT_URL`, and `DAGSTER_HOME`.

### 2. Start the local stack

```bash
docker compose up -d --build
```

This brings up:
- **PostGIS** (`localhost:5432`) - applies `db schema/*.sql` on first boot, creates the `dagster` database via `00_create_dagster_db.sh`
- **MinIO** (`localhost:9000`, console at `localhost:9001`) - creates `dagster-logs` and artifact buckets on first boot
- **Dagster UI** (`localhost:3000`) - runs the orchestrator in Docker

To reset from scratch: `docker compose down -v && rm -rf .data/ && docker compose up -d --build`

### 3. Endpoints

| Service | URL |
|---|---|
| Dagster UI | http://localhost:3000 |
| MinIO Console | http://localhost:9001 |
| MinIO S3 API | http://localhost:9000 |
| PostgreSQL | localhost:5432 |

Credentials are in `.env` / `example.env`.

### 4. Verify the stack (smoke check)

End-to-end check: seeds the 20-reach demo network, polls until every reach
reconciles, then verifies final DB and storage state. Best run on a fresh stack.

In the Dagster UI: **Automation** -> toggle `reconciliation_sensor` ON, then:

```bash
docker compose exec orchestrator python scripts/smoke_check.py
```

## Env vars

| Variable | Used by | Purpose |
|---|---|---|
| `POSTGRES_USER` | docker-compose, config.py, dagster.yaml | DB username |
| `POSTGRES_PASSWORD` | docker-compose, config.py, dagster.yaml | DB password |
| `POSTGRES_HOST` | docker-compose, config.py, dagster.yaml | DB host (`localhost` host / `db` compose) |
| `POSTGRES_PORT` | docker-compose, config.py, dagster.yaml | DB port |
| `POSTGRES_DB` | docker-compose, config.py | Pipeline database name |
| `DAGSTER_PG_DB` | dagster.yaml, 00_create_dagster_db.sh | Dagster metadata database |
| `DAGSTER_HOME` | docker-compose, Dockerfile, dagster | Dagster instance directory |
| `AWS_ACCESS_KEY_ID` | docker-compose, boto3 | S3/MinIO access key |
| `AWS_SECRET_ACCESS_KEY` | docker-compose, boto3 | S3/MinIO secret key |
| `AWS_ENDPOINT_URL` | docker-compose, dagster.yaml, storage.py | MinIO endpoint (`localhost` host / `minio` compose; omit for real S3) |
| `DAGSTER_S3_BUCKET` | dagster.yaml, docker-compose | Dagster compute logs bucket |
| `ARTIFACTS_S3_BUCKET` | config.py, docker-compose | Model artifacts bucket |
| `MAJOR_VERSION` | config.py | Artifact path versioning |

## Dagster infrastructure

Dagster uses PostgreSQL for run/event metadata (`DAGSTER_PG_DB`) and S3 for
compute log storage (`DAGSTER_S3_BUCKET`). See
[Dagster deployment docs](https://docs.dagster.io/deployment/dagster-instance).

## Operational notes

### Retry-exhaustion behavior

After Dagster retries are exhausted (1 attempt + 3 retries) for a given
`run_key`, the reach stays stale but the sensor will not resubmit it (same
`run_key`). To retry: update any field in `desired_state` for that reach — the
DB trigger increments `revision`, which produces a new `run_key`.

### Schema ownership

The DB schema in [`db/schema/`](../db/schema/) is the source of truth. It is
applied by docker-compose on first boot via `docker-entrypoint-initdb.d`. The
orchestrator does not create or modify tables — it only reads and writes data.

### Processing flag

The `current_state.processing` flag is reserved for the future cascade
coordinator that will manage the full build -> nd -> kwse chain. Current workers
do not set it. `update_current()` resets it to `FALSE` on completion; this will
change when the cascade coordinator is built.
