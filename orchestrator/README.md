# Orchestrator

Reconciliation loop for 2D flood inundation model building.
Polls the DB for stale reaches and processes them downstream-first (terminals before upstream).
Jobs are submitted through SEPEX (Docker for build_model, AWS Batch for nd_scenarios).

Design references: [`twod-fim-knowledge-base/system-design/`](https://github.com/NGWPC/twod-fim-knowledge-base/tree/main/system-design)
(`guide.md`, `triggers-and-propagation.md`).

## Layout

| | |
|---|---|
| `recon/` | the reconciliation loop: gap calculation, checks, job submission, storage observation |
| `notebooks/` | how the loop works, by running it |
| `scripts/` | `reconcile.py` (the loop), `seed.py` (dev scaffolding) |

Reading order: `recon/gap.py` (gap calculation) then `recon/check.py` (one check) then `recon/execution.py` (job submission).

## Key schema contracts

See [`db/schema/`](../db/schema/) for full definitions.

- `materialized_models.model_id` is `GENERATED ALWAYS` from `identity_hash _ domain_code` - never written directly ([`04_materialized_models.sql`](../db/schema/04_materialized_models.sql))
- `desired_state.revision` is DB-owned and per reach: 0 on insert, +1 on any real change ([`09_triggers.sql`](../db/schema/09_triggers.sql))
- Work tracking lives in `reach_processing`, which stores only `halted`; every other state is derived by the `reach_status` view ([`07_reach_processing.sql`](../db/schema/07_reach_processing.sql))
- `applied_revision` is set only when the gap is empty, never per step, and is retracted the moment a gap reappears
- Nothing is recorded that storage has not been seen to hold; a job's return value is not evidence

## Prerequisites

- Docker
- [uv](https://docs.astral.sh/uv/) for running scripts and managing dependencies
- Job images for local SEPEX (not needed for cloud):
  ```bash
  # Option A: build from twod-fim-jobs
  cd ../twod-fim-jobs
  docker build --platform linux/amd64 --target build_model -t build_model:local .
  docker build --platform linux/amd64 --target run_nd_scenarios-lisflood-cpu -t run_nd_scenarios-lisflood-cpu:local .
  # GPU (requires NVIDIA Container Toolkit):
  docker build --platform linux/amd64 --target run_nd_scenarios-lisflood-gpu -t run_nd_scenarios-lisflood-gpu:local .

  # Option B: pull pre-built from GHCR (faster, no build)
  docker pull --platform linux/amd64 ghcr.io/ngwpc/twod-fim-jobs/build_model:dev
  docker tag ghcr.io/ngwpc/twod-fim-jobs/build_model:dev build_model:local
  # GPU image falls back to CPU without NVIDIA, so it works for both
  docker pull --platform linux/amd64 ghcr.io/ngwpc/twod-fim-jobs/run_nd_scenarios-lisflood-gpu:dev
  docker tag ghcr.io/ngwpc/twod-fim-jobs/run_nd_scenarios-lisflood-gpu:dev run_nd_scenarios-lisflood-cpu:local
  docker tag ghcr.io/ngwpc/twod-fim-jobs/run_nd_scenarios-lisflood-gpu:dev run_nd_scenarios-lisflood-gpu:local
  ```

## Local dev setup

### 1. Environment

Copy `example.env` to `.env` at the repo root:

```bash
cp example.env .env
```

### 2. Start the local stack

```bash
docker compose -f docker-compose-local.yml up -d
```

This brings up:
- **PostGIS** (`localhost:5432`) - applies `db/schema/*.sql` on first boot
- **MinIO** (`localhost:9000`, console at `localhost:9001`) - creates artifact buckets on first boot
- **SEPEX** (`localhost:5050`) - container execution server

The reconciler runs on the host (not in a container):

```bash
cd orchestrator
uv run python scripts/reconcile.py --forever
```

To reset from scratch: `docker compose -f docker-compose-local.yml down && rm -rf .data/ && docker compose -f docker-compose-local.yml up -d`

### 3. Endpoints

| Service | URL |
|---|---|
| MinIO Console | http://localhost:9001 |
| MinIO S3 API | http://localhost:9000 |
| SEPEX API | http://localhost:5050 |
| PostgreSQL | localhost:5432 |

Credentials are in `.env` / `example.env`.

### 4. Seed and run

Seed the network and start the reconciler:

```bash
cd orchestrator
uv run python scripts/seed.py
uv run python scripts/reconcile.py --forever
```

Options for `reconcile.py`:
- `--once` - a single pass, then exit
- `--forever` - keep going after the network settles
- `--interval N` - seconds between passes (default 20)
- `-v` / `--verbose` - log every check, not just the ones that act

Options for `seed.py`:
- `--network-gpkg PATH` - use a custom GeoPackage (default: `testdata/network.gpkg`)
- `--nhf-gpkg PATH` - hydrofabric with lake polygons (default: `testdata/nhf.gpkg`)

## Env vars

| Variable | Used by | Purpose |
|---|---|---|
| `POSTGRES_USER` | docker-compose, config.py | DB username |
| `POSTGRES_PASSWORD` | docker-compose, config.py | DB password |
| `POSTGRES_HOST` | docker-compose, config.py | DB host (`localhost` host / `db` compose) |
| `POSTGRES_PORT` | docker-compose, config.py | DB port |
| `POSTGRES_DB` | docker-compose, config.py | Pipeline database name |
| `AWS_ACCESS_KEY_ID` | docker-compose, boto3 | S3/MinIO access key |
| `AWS_SECRET_ACCESS_KEY` | docker-compose, boto3 | S3/MinIO secret key |
| `AWS_ENDPOINT_URL` | docker-compose, config.py | MinIO endpoint (`localhost` host / `minio` compose; omit for real S3) |
| `ARTIFACTS_S3_BUCKET` | config.py, docker-compose | Model artifacts bucket |
| `MAJOR_VERSION` | config.py | Artifact path versioning |
| `SEPEX_URL` | config.py | SEPEX API base URL |
| `GPU_AVAILABLE` | check.py | Select GPU ND process variant; set to `true` for cloud Batch (default `false`) |
| `VOLUME_CONVERGENCE_TOLERANCE` | config.py | Steady-state threshold for normal-depth runs (default `1e-3`) |
| `HALT_AFTER_FAILURES` | config.py | Consecutive failures before a reach is parked (default `1`) |
| `ALLOW_WATER_ON_EDGES` | config.py | Continue when water hits an invalid domain edge (default `true`) |

See `example.env` for additional optional variables (Docker platform, local raster overrides, AWS session tokens).

## Operational notes

### Schema ownership

The DB schema in [`db/schema/`](../db/schema/) is the source of truth. It is
applied by docker-compose on first boot via `docker-entrypoint-initdb.d`. The
orchestrator does not create or modify tables - it only reads and writes data.

### Retry behavior

Retries are loop-owned (`consecutive_failures`, `next_retry_at`, `halted` in `reach_processing`).
After `halt_after_failures` consecutive failures, the reach is parked for a person.
To clear: `processing.clear_halt(reach_id)` or update `desired_state` to bump the revision.
