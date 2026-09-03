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

# Wipe all data (delete container-owned db files via a root container first)
wipe: down-local
    -docker run --rm -v {{justfile_directory()}}/.data/:/data alpine rm -rf /data/db
    -docker run --rm -v {{justfile_directory()}}/.data/:/data alpine rm -rf /data/minio
    -docker run --rm -v {{justfile_directory()}}/.data/:/data alpine rm -rf /data/sepex
