# twod-fim development tasks. Run `just` to list everything available.

# List available recipes
default:
    @just --list

# Create the external docker network shared by the stack and job containers
network:
    @docker network inspect twodfim_net >/dev/null 2>&1 || docker network create twodfim_net

# Start the stack, register the local processes, set up the database
up-local: network
    #!/usr/bin/env bash
    set -euo pipefail
    unset AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
    set -a
    source .env
    set +a
    # GPU_AVAILABLE is read as true, 1, yes, y, or on.
    gpu="${GPU_AVAILABLE:-}"
    gpu="$(printf '%s' "${gpu//[\"\']/}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$gpu" in
      true|1|yes|y|on) hardware=local-gpu; echo "GPU_AVAILABLE=true: SEPEX with the host's GPUs" ;;
      *) hardware=local-cpu; echo "GPU_AVAILABLE=false: SEPEX without GPUs" ;;
    esac
    docker compose --profile local --profile "$hardware" up -d
    just register-sepex-processes-local
    just setup-db

# Stop the stack, whichever SEPEX variant it started
down-local:
    docker compose --profile local --profile local-cpu down
    docker compose --profile local --profile local-gpu down

# Start the hybrid stack (local DB, cloud SEPEX + S3), register the cloud processes, set up the database
up-hybrid: network
    #!/usr/bin/env bash
    set -euo pipefail
    unset AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
    set -a
    source .env
    set +a
    docker compose --profile hybrid up -d
    just register-sepex-processes-cloud
    just setup-db

# Stop hybrid stack
down-hybrid:
    docker compose --profile hybrid down

# Wipe sepex only
wipe-sepex: down-local
    -docker run --rm -v {{justfile_directory()}}/.data/:/data alpine rm -rf /data/sepex

# Wipe db only
wipe-db: down-local
    -docker run --rm -v {{justfile_directory()}}/.data/:/data alpine rm -rf /data/db

# Delete ALL local data: database, bucket, SEPEX state (asks first)
wipe confirm="":
    #!/usr/bin/env bash
    set -uo pipefail
    DATA="{{justfile_directory()}}/.data"
    if [ "{{confirm}}" != "force" ]; then
      echo "About to permanently delete:"
      for d in db minio sepex; do
        [ -e "$DATA/$d" ] && echo "  .data/$d   $(du -sh "$DATA/$d" 2>/dev/null | cut -f1)"
      done
      if docker exec twodfim-db pg_isready -U twodfim -d twodfim >/dev/null 2>&1; then
        docker exec twodfim-db psql -U twodfim -d twodfim -tAc \
          "SELECT '  holding: '||(SELECT count(*) FROM materialized_models)||' model(s), '
                  ||(SELECT count(*) FROM materialized_nd_runs)||' nd, '
                  ||(SELECT count(*) FROM materialized_kwse_runs)||' kwse'" 2>/dev/null
      fi
      echo
      read -r -p "Type 'wipe' to confirm: " reply
      if [ "$reply" != "wipe" ]; then
        echo "Aborted. Nothing deleted, stack untouched."
        exit 1
      fi
    fi
    just down-local
    docker run --rm -v "$DATA":/data alpine rm -rf /data/db /data/minio /data/sepex

# Register sepex/local/plugins (rerun after editing a yml)
register-sepex-processes-local:
    uv run --script sepex/register_processes.py sepex/local/plugins

# Register sepex/cloud/plugins (rerun after editing a yml)
register-sepex-processes-cloud:
    uv run --script sepex/register_processes.py sepex/cloud/plugins

# Seed the lakes an AOI config names into the database and workspace/lakes/
seed-lakes aoi_config_path:
    uv run --project reconciler python reconciler/scripts/seed.py lakes {{aoi_config_path}}

# Seed the coasts an AOI config names into the database and workspace/coasts/
seed-coasts aoi_config_path:
    uv run --project reconciler python reconciler/scripts/seed.py coasts {{aoi_config_path}}

# Seed the network an AOI config names (lakes and coasts must be seeded first)
seed-network aoi_config_path:
    uv run --project reconciler python reconciler/scripts/seed.py network {{aoi_config_path}}

# Stage a local file as source data at <TWOD_FIM_SOURCE_DATA_PREFIX>/<name>
stage-source-data file name:
    uv run --project reconciler python reconciler/scripts/stage_source_data.py {{file}} {{name}}

# Wait for the database, then write its defaults
setup-db:
    docker exec twodfim-db sh -c 'for i in $(seq 60); do pg_isready -q -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" && exit 0; sleep 1; done; echo "database not accepting connections after 60s"; exit 1'
    just author-defaults

# Write desired_state_defaults from the system-wide settings (a change needs --yes)
author-defaults *flags:
    uv run --project reconciler python reconciler/scripts/author_intent.py defaults {{flags}}

# Author intent for the network an AOI config names (run setup-db first)
author-intent aoi_config_path:
    uv run --project reconciler python reconciler/scripts/author_intent.py aoi {{aoi_config_path}}

# Run the reconciliation loop until the network settles
reconcile:
    cd reconciler && uv run python scripts/reconcile.py


# Publish materialized reaches for flows2fim into a local folder or s3:// address
# (an AOI's reaches, or every materialized reach when no AOI config is given)
f2f-snapshot out_dir aoi_config_path="":
    uv run --project reconciler python reconciler/scripts/f2f.py scenarios {{aoi_config_path}} {{out_dir}}
    uv run --project reconciler python reconciler/scripts/f2f.py library {{out_dir}}
    uv run --project reconciler python reconciler/scripts/f2f.py aep {{aoi_config_path}} {{out_dir}}


# Seed the test network and author the small end-to-end scope
test-e2e:
    just stage-source-data reconciler/testdata/lulc.tif e2e/lulc.tif
    just stage-source-data reconciler/testdata/lulc_lookup.json e2e/lulc_lookup.json
    just seed-lakes reconciler/testdata/e2e.aoi_config.json
    just seed-network reconciler/testdata/e2e.aoi_config.json
    just author-intent reconciler/testdata/e2e.aoi_config.json
    just reconcile
