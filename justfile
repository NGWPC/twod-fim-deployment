# twod-fim development tasks. Run `just` to list everything available.

# List available recipes
default:
    @just --list

# Create the external docker network shared by the stack and spawned job containers
network:
    @docker network inspect twodfim_net >/dev/null 2>&1 || docker network create twodfim_net

# Start the stack
up-local: network
    docker compose -f docker-compose-local.yml up -d

# Stop the stack
down-local:
    docker compose -f docker-compose-local.yml down

# Start hybrid stack (local DB only, cloud SEPEX + S3 via .env)
up-hybrid: network
    docker compose -f docker-compose-local.yml up -d db

# Stop hybrid stack
down-hybrid:
    docker compose -f docker-compose-local.yml down

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

# Re-register plugin definitions after editing a plugin yml (keeps db, bucket, job history)
reload-plugins:
    #!/usr/bin/env bash
    set -euo pipefail
    # SEPEX imports the mounted definitions ONCE, into its own data folder, and
    # thereafter serves from that copy. Reimporting needs BOTH halves: the copy
    # gone, and PLUGINS_LOAD_DIR set to say where to read from. Miss the second
    # and it starts with no processes at all; miss the first and it exits fatal.
    # sepex_local.env keeps the variable commented for exactly that reason, so
    # this turns it on for the reload boot and puts it back afterwards.
    ENV="{{justfile_directory()}}/sepex_local.env"
    restore() { sed -i "s|^PLUGINS_LOAD_DIR=|# PLUGINS_LOAD_DIR=|" "$ENV"; }
    trap restore EXIT
    sed -i "s|^# PLUGINS_LOAD_DIR=|PLUGINS_LOAD_DIR=|" "$ENV"
    docker stop sepex >/dev/null
    docker run --rm -v {{justfile_directory()}}/.data/:/data alpine rm -rf /data/sepex/plugins
    docker compose -f docker-compose-local.yml up -d sepex >/dev/null
    for _ in $(seq 60); do
      curl -sf localhost:5050/processes >/dev/null && break
      sleep 2
    done
    curl -s localhost:5050/processes | grep -o '"id":"[^"]*"'

# Load the network into the database and storage (truncates reach_network)
seed:
    cd orchestrator && uv run python scripts/seed.py

# Author intent for the seven-reach end-to-end scope
author-intent:
    cd orchestrator && uv run python scripts/author_intent.py

# Author intent for every reach in the network
author-intent-all:
    cd orchestrator && uv run python scripts/author_intent.py --scope all

# Author intent for the seven-reach end-to-end scope
reconcile:
    cd orchestrator && uv run python scripts/reconcile.py


# Publish everything materialized for flows2fim: scenarios db, library, AEP VRTs
f2f:
    cd orchestrator && uv run python scripts/export_f2f_db.py
    cd orchestrator && uv run python scripts/export_f2f_library.py
    cd orchestrator && uv run python scripts/create_aep_f2f_vrts.py


# Seed the network and author the small end-to-end scope
test-e2e: seed author-intent reconcile
