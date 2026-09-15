# Orchestrator

Reconciliation loop for 2D flood inundation model building.
Polls the DB for stale reaches and processes them downstream-first (terminals before upstream).
Jobs are submitted through SEPEX (Docker for build_model, AWS Batch for nd_scenarios).

Design references: [`twod-fim-knowledge-base/system-design/`](https://github.com/NGWPC/twod-fim-knowledge-base/tree/main/system-design)
(`guide.md`, `triggers-and-propagation.md`).

## Layout

|              |                                                                                                      |
| ------------ | ---------------------------------------------------------------------------------------------------- |
| `recon/`     | the reconciliation loop: gap calculation, checks, job submission, storage observation                |
| `notebooks/` | how the loop works, by running it                                                                    |
| `scripts/`   | `reconcile.py` (the loop), `seed.py` (load a network) and `author_intent.py` (say what is wanted of it), `f2f.py` (publish an AOI for flows2fim) |

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
- Job images for local SEPEX (not needed for cloud): nothing to do by default.
  `register-sepex-processes-local` (part of `just up-local`) registers each
  docker process against its published `ghcr.io/ngwpc/twod-fim-jobs/<name>:dev`
  image, and SEPEX pulls it when the process is registered.

  Only if you need a locally built or otherwise unpublished image, set
  `USE_LOCAL_IMAGES=true` in `.env` and build or tag it `:local` yourself first:

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

  (kwse images are not covered above; pull and tag `run_kwse_scenarios-lisflood-{cpu,gpu}` the same way if you need `:local` for those too)

  Only the process for `GPU_AVAILABLE` (`false` unless set) is registered: the
  loop only ever asks SEPEX for that one variant of `run_nd_scenarios` /
  `run_kwse_scenarios` (`recon/check.py`), so the other hardware variant's
  image is never needed on this machine.

## Local dev setup

### 1. Environment

Copy `example.env` to `.env` at the repo root:

```bash
cp example.env .env
```

### 2. Start the local stack

```bash
just up-local
```

This brings up:

- **PostGIS** (`localhost:5432`) - applies `db/schema/*.sql` on first boot; `just setup-db` then writes `desired_state_defaults` from `.env`
- **MinIO** (`localhost:9000`, console at `localhost:9001`) - creates artifact buckets on first boot
- **SEPEX** (`localhost:5050`) - container execution server, with `sepex/local/plugins` registered through its API. With `GPU_AVAILABLE=true` in `.env` it is started as `sepex-gpu` (profile `local-gpu`), which gives it the host's NVIDIA GPUs (needs the NVIDIA Container Toolkit)

The reconciler runs on the host (not in a container):

```bash
cd orchestrator
uv run python scripts/reconcile.py --forever
```

To reset from scratch: `just wipe && just up-local`

### 3. Endpoints

| Service       | URL                   |
| ------------- | --------------------- |
| MinIO Console | http://localhost:9001 |
| MinIO S3 API  | http://localhost:9000 |
| SEPEX API     | http://localhost:5050 |
| PostgreSQL    | localhost:5432        |

Credentials are in `.env` / `example.env`.

### 4. Seed and run

Seeding is two steps, because the network and what is wanted of it are two
different things. `seed.py` loads the network; `author_intent.py` says which of
its reaches to build. With the test network, small enough to run end to end:

```bash
just stage-source-data orchestrator/testdata/lulc.tif e2e/lulc.tif
just stage-source-data orchestrator/testdata/lulc_lookup.json e2e/lulc_lookup.json
just seed-lakes orchestrator/testdata/e2e.aoi_config.json
just seed-network orchestrator/testdata/e2e.aoi_config.json
just author-intent orchestrator/testdata/e2e.aoi_config.json
just reconcile
```

That is `just test-e2e`. A real network is in [RUNBOOK.md](../RUNBOOK.md).

Re-scoping needs only the second. `seed.py` never deletes: seeding adds or
updates rows, and a clean database is `just wipe-db`.

Options for `reconcile.py`:

- `--once` - a single pass, then exit
- `--forever` - keep going after the network settles
- `--interval N` - seconds between passes (default 20)
- `-v` / `--verbose` - log every check, not just the ones that act

`seed.py` takes what to seed and the path of an AOI config (`scripts/aoi_config.py`) naming its
source, a local path or an `s3://` address:

- `seed.py lakes` - every lake in the AOI config's `lakes` GeoPackage (layer `lakes_polygons`)
- `seed.py coasts` - every polygon in its `coasts` GeoPackage (layer `coastal_influence_polygons`)
- `seed.py network` - its `network` (`modify_network`'s `network.gpkg`); the lakes and coasts it names must be seeded first

`author_intent.py` has two commands:

- `defaults [--yes]` writes `desired_state_defaults` from the system-wide settings below (`SDR_COMMIT`, `SOLVER`, `DEM_SOURCE`, `LULC_SOURCE`, `LULC_LOOKUP`, `LD_*`, plus `GRID_RESOLUTION`, `EPSG_CODE`). `just setup-db` runs it when the stack starts, and it changes nothing once written; a change to the row in force re-checks every reach, so it is shown and written only with `--yes`
- `aoi <aoi-config-path>` writes `desired_state` for the reaches of the AOI's own `network` that the flow statistics cover (the AOI config's `flow_statistics`, or the `FLOW_STATISTICS` default): discharge bounds from those statistics, and the AOI's `dem_source`, `lulc_source`, `lulc_lookup` when it names them
- never touches the defaults row; adds or updates, never deletes; `q_bound_factors` narrows the bounds for a test AOI

### 5. Publish for flows2fim

One command per AOI, into a local folder or an `s3://` address:

```bash
just f2f <out-dir> orchestrator/testdata/e2e.aoi_config.json
```

Without an AOI config (`just f2f <out-dir>`) it exports every materialized reach
in the database's network, forecast with the system-wide flow statistics.

It runs the three steps of `scripts/f2f.py` in order, each also runnable on its own:

```bash
uv run --project orchestrator python orchestrator/scripts/f2f.py scenarios [aoi-config-path] <out-dir>
uv run --project orchestrator python orchestrator/scripts/f2f.py library <out-dir>
uv run --project orchestrator python orchestrator/scripts/f2f.py aep [aoi-config-path] <out-dir> [--image IMAGE]
```

- `scenarios` writes `<out-dir>/scenarios.db` for the reaches of the AOI config's `network` (or of the database's network) that are materialized, and `<out-dir>/start_reaches.csv`, the reaches controls start from
- `library` copies the depth grids it names from the results tree into `<out-dir>/library/`
- `aep` forecasts each of the AOI's AEP columns (`flow_aep_columns`, from its `flow_statistics`, falling back to the settings) and runs flows2fim `controls` and `fim -fmt VRT` into `<out-dir>/aep/<column>/`

Each export goes into a new, empty out-dir, and `scenarios` stops otherwise. An
export is a snapshot of what is materialized when it runs; exporting again, for
more reaches or other ones, is a new out-dir. Running `library` or `aep` again
within one export is fine: `library` skips grids an interrupted run already
copied. A depth grid a materialized scenario names but storage does not hold
gets `map_exists = 0` in `scenarios.db`, which flows2fim `controls` honours by
not choosing that scenario.

f2f is read only. It reads the database through a connection that refuses
writes, and storage by reading and copying from it; the only thing it writes is
`<out-dir>`, which it refuses inside the storage root or the source data root.

sqlite and flows2fim only work on local files, so when `<out-dir>` is in storage
every file is written in a temporary folder and uploaded from there. The library
is the exception: it is copied object to object, and flows2fim reads it where
it is through GDAL's `/vsis3/`, with this
machine's AWS credentials handed to the container. A VRT in storage names its
grids by `/vsis3/` path, since S3 keys do not resolve `../`; a local one names
them relative to itself.

The first step reads `materialized_nd_runs` and `materialized_kwse_runs`, not
the results tree, and that is the whole point of the split. A reach's adopted
library is its `q_set`; storage may also hold runs from an earlier sweep that
the loop passed over, and those have a normal-depth grid but no stage library.
Since flows2fim matches a forecast on flow before stage, adopting the surplus
would quietly map backwater-controlled reaches at normal depth. Only the
database tells the two apart, so the library is downloaded from the database's
list rather than by walking the tree.

The one thing the database cannot supply is the `nd=<slope>` folder, because
the job computes the slope from the reach's own DEM. It is discovered in
storage, as the loop does, and every grid's full `s3://` address is recorded in
a `scenario_sources` table so the library step needs no database connection.

flows2fim (0.5.0) parses reach ids as integers, and reach ids here are text: a
reach `modify_network` split out of one flowpath is `<flowpath id>_<n>`. So
everything flows2fim reads (`scenarios`, `network`, `start_reaches.csv`,
`flows.csv`, `library/<n>/`) names a reach by a number, and the `reach_ids`
table in `scenarios.db` maps each number to its reach id. The numbers belong to
one export, 1, 2, ... in reach id order, and go once flows2fim takes text ids.
Every piece of a split flowpath is forecast with the flowpath's flows.

Controls are traced upstream from the reaches with nowhere left to drain in
the export, listed in `start_reaches.csv` and handed to `controls -scsv`, each at
normal depth. For a true terminal that is the only start there is, since it has
no stage library. A reach whose downstream neighbour is not exported (not
materialized yet, say) is a start too, as a fallback: it and everything above it
are mapped as if it drained freely, until that neighbour is exported.

Forecast discharges are **cms**, the unit the whole system is authored in.
flows2fim's help says cfs, but it never converts -- it matches the value
against `us_flow` in the scenarios table.

flows2fim runs in docker, since it shells out to GDAL, pulling
`ghcr.io/ngwpc/flows2fim:0.5.0` if it is not already local. It mounts only
`<out-dir>`, which is why everything is written under it.

## Env vars

| Variable                                                             | Used by                         | Purpose                                                                                              |
| -------------------------------------------------------------------- | ------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `POSTGRES_USER`                                                      | docker-compose, config.py       | DB username                                                                                          |
| `POSTGRES_PASSWORD`                                                  | docker-compose, config.py       | DB password                                                                                          |
| `POSTGRES_HOST`                                                      | docker-compose, config.py       | DB host (`localhost` host / `db` compose)                                                            |
| `POSTGRES_PORT`                                                      | docker-compose, config.py       | DB port                                                                                              |
| `POSTGRES_DB`                                                        | docker-compose, config.py       | Pipeline database name                                                                               |
| `AWS_ACCESS_KEY_ID`                                                  | docker-compose, boto3           | S3/MinIO access key                                                                                  |
| `AWS_SECRET_ACCESS_KEY`                                              | docker-compose, boto3           | S3/MinIO secret key                                                                                  |
| `AWS_ENDPOINT_URL`                                                   | docker-compose, config.py       | MinIO endpoint (`localhost` host / `minio` compose; omit for real S3)                                |
| `TWOD_FIM_DATA_ROOT_PREFIX`                                          | config.py, docker-compose       | `s3://` address everything the system writes lives under, e.g. `s3://<bucket>/version=2026.09`; a new value is a new, empty area (required) |
| `TWOD_FIM_SOURCE_DATA_PREFIX`                                        | config.py, docker-compose       | `s3://` address `{source_data}` stands for, e.g. `s3://<bucket>/source_data` (required)              |
| `SEPEX_URL`                                                          | config.py                       | SEPEX API base URL                                                                                   |
| `GPU_AVAILABLE`                                                      | check.py, register_processes.py | Select the GPU variant of `run_nd_scenarios` / `run_kwse_scenarios` -- both which the loop submits to and, for local SEPEX, which one is registered; set to `true` for cloud Batch (default `false`) |
| `USE_LOCAL_IMAGES`                                                   | register_processes.py           | Register local docker processes with their `:local` image instead of the published GHCR one (default `false`) |
| `VOLUME_CONVERGENCE_TOLERANCE`                                       | config.py                       | Steady-state threshold for normal-depth runs (default `1e-3`)                                        |
| `HALT_AFTER_FAILURES`                                                | config.py                       | Consecutive failures before a reach is parked (default `1`)                                          |
| `ALLOW_WATER_ON_EDGES`                                               | config.py                       | Continue when water hits an invalid domain edge (default `true`)                                     |
| `SDR_COMMIT`, `SOLVER`                                               | config.py                       | Methodology pin and solver in `desired_state_defaults` (defaults in config.py)                       |
| `DEM_SOURCE`, `LULC_SOURCE`, `LULC_LOOKUP`                           | config.py                       | Default sources every reach falls back to; `{source_data}` is filled in; `LULC_LOOKUP` must be `s3://` |
| `LD_DS_Z_DELTA`, `LD_Q_*_RANGE`                                      | config.py                       | Library resolution defaults (DR-033, DR-030)                                                         |
| `FLOW_STATISTICS`                                                    | config.py                       | Default flow statistics for authoring: `bound_flows.py`'s CONUS output (default `{source_data}/flows/nhf_v1.2.3_aep_flows.parquet`) |
| `FLOW_REACH_ID_COLUMN`, `FLOW_Q_LOWER_COLUMN`, `FLOW_Q_UPPER_COLUMN` | config.py                       | What that table calls the reach id and the bound columns (`reach_id`, `high_flow_threshold`, `f100year`) |
| `FLOW_AEP_COLUMNS`                                                   | config.py                       | The columns of that table `f2f.py` forecasts as AEP flows, a JSON list (default `["f5year","f50year","f100year"]`) |

See `example.env` for additional optional variables (Docker platform, AWS session tokens).

## Operational notes

### Schema ownership

The DB schema in [`db/schema/`](../db/schema/) is the source of truth. It is
applied by docker-compose on first boot via `docker-entrypoint-initdb.d`. The
orchestrator does not create or modify tables - it only reads and writes data.

### Retry behavior

Retries are loop-owned (`consecutive_failures`, `next_retry_at`, `halted` in `reach_processing`).
After `halt_after_failures` consecutive failures, the reach is parked for a person.
To clear: `processing.clear_halt(reach_id)` or update `desired_state` to bump the revision.