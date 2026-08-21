# Start the stack
up:
    docker compose up -d

# Stop the stack
down:
    docker compose down

# Wipe all data (delete container-owned db files via a root container first)
wipe: down
    -docker run --rm -v {{justfile_directory()}}/.data/:/data alpine rm -rf /data/db
    -docker run --rm -v {{justfile_directory()}}/.data/:/data alpine rm -rf /data/minio
