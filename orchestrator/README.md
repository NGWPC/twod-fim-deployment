# Orchestrator

Dagster-based reconciliation loop for 2D flood inundation model building. Polls
the DB for stale reaches and processes them downstream-first (terminals before
upstream).

Design references: [`twod-fim-knowledge-base/system-design/`](https://github.com/NGWPC/twod-fim-knowledge-base/tree/main/system-design)
(`guide.md`, `triggers-and-propagation.md`).

## Layout

| | |
|---|---|
| `recon/` | the reconciliation loop. Imports no Dagster, ever — the notebooks, a script and (later) a sensor all call the same code |
| `dagster_app/` | Dagster wiring only. **Does not currently load — see below** |
| `notebooks/` | how the loop works, by running it |
| `scripts/` | host-side dev tooling; not shipped in the image |

Start reading at [`recon/gap.py`](recon/gap.py) — it is pure, short, and is the
decision the rest of the package exists to serve — then `recon/check.py`, which
is `reconciliation-loop.md`'s "what one check does" in code.

### Dagster needs rewiring

`dagster_app/` still calls `recon.state_store` and `recon.reconciliation`, which
have been deleted. Resolving definitions fails with
`ModuleNotFoundError: No module named 'recon.state_store'`.

Whoever picks this up needs four calls:

| Need | Call |
|---|---|
| which reaches need a check | `queue.due_reaches()` |
| check one reach | `check.run_check(reach_id, runner)` |
| hear back about submitted jobs | `jobs.status_pass(runner)` |
| a runner | `workers.LocalDockerRunner` (any `ContainerRunner`) |

Three constraints that change how the sensor should be written:

- **Do not use `run_key` dedup as the double-submission guard.** The in-flight
  marker in the database is the guard, and it holds against notebooks and
  scripts too, which `run_key` cannot.
- **Do not configure Dagster retries.** Retries are loop-owned
  (`consecutive_failures`, `next_retry_at`, `halted`). Two retry mechanisms
  fight; the old arrangement left exhausted reaches stale with nothing
  recording why.
- **A check never waits for its job.** It submits and returns in milliseconds;
  the result is picked up by a later check. An asset that blocks is the shape
  this design removed.

## Key schema contracts

See [`db/schema/`](../db/schema/) for full definitions.

- `current_state.model_id` is `GENERATED ALWAYS` from `identity_hash _ domain_code` — never written directly ([`04_current_state.sql`](../db/schema/04_current_state.sql))
- `desired_state.revision` is DB-owned and per reach: 0 on insert, +1 on any real change ([`09_triggers.sql`](../db/schema/09_triggers.sql))
- Work tracking lives in `reach_processing`, which stores only `halted`; every other state is derived by the `reach_status` view ([`07_reach_processing.sql`](../db/schema/07_reach_processing.sql))
- `applied_revision` is set only when the gap is empty, never per step, and is retracted the moment a gap reappears
- Nothing is recorded that storage has not been seen to hold; a job's return value is not evidence

## Prerequisites

- Docker
- [uv](https://docs.astral.sh/uv/) for maintenance commands (e.g. regenerating `uv.lock`)
- `twod-fim-jobs:build_model` Docker image built locally (the orchestrator launches it via `docker run`):
  ```bash
  cd ../twod-fim-jobs
  docker build --platform linux/amd64 --target build_model -t twod-fim-jobs:build_model .
  ```

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
- **PostGIS** (`localhost:5432`) - applies `db/schema/*.sql` on first boot, creates the `dagster` database via `00_create_dagster_db.sh`
- **MinIO** (`localhost:9000`, console at `localhost:9001`) - creates `dagster-logs` and artifact buckets on first boot
- **Dagster UI** (`localhost:3000`) - runs the orchestrator in Docker

The orchestrator container has the Docker CLI and the host Docker socket mounted.
It spawns `twod-fim-jobs` worker containers as siblings on the `twodfim` compose network, so they can reach `db` and `minio` by service name.

To reset from scratch: `docker compose down && rm -rf .data/ && docker compose up -d --build`

### 3. Endpoints

| Service | URL |
|---|---|
| Dagster UI | http://localhost:3000 |
| MinIO Console | http://localhost:9001 |
| MinIO S3 API | http://localhost:9000 |
| PostgreSQL | localhost:5432 |

Credentials are in `.env` / `example.env`.

### 4. Verify the stack (smoke check)

End-to-end check: loads a reach network from a GeoPackage, seeds the DB, polls
until eligible reaches reconcile, then verifies DB and storage state.
Best run on a fresh stack.

In the Dagster UI: **Automation** -> toggle `reconciliation_sensor` ON, then:

```bash
cd orchestrator
uv run python scripts/smoke_check.py
```

Options:
- `--gpkg /path/to/network.gpkg` - use a custom GeoPackage (default: `testdata/network.gpkg`)
- `--seed-only` - seed the DB and exit without waiting for reconciliation

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

See `example.env` for additional optional variables (Docker platform, local raster overrides, AWS session tokens).

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
