#!/usr/bin/env python3
"""Deploy SEPEX alongside the twod-fim orchestrator on EC2.

Handles database creation, configuration, and startup using a pre-built container image.
Run on the SEPEX EC2 instance after infrastructure provisioning (see deploy/sepex.md steps 1-3).

Usage:
  python3 deploy/setup_sepex.py \
    --rds-address <rds-address> \
    --rds-secret-arn <rds-secret-arn> \
    --sepex-password <password> \
    --s3-bucket <bucket-name>

Where:
  --rds-address     RDS hostname (terraform output -raw rds_address)
  --rds-secret-arn  RDS master secret ARN (terraform output -raw rds_master_user_secret_arn)
  --sepex-password  Password for the sepex_app database user (choose one)
  --s3-bucket       S3 bucket for SEPEX storage (prod_bucket_name or test_bucket_name from terraform.tfvars)
  --image           Container image (default: ghcr.io/dewberry/sepex:dev)
  --install-dir     Install directory (default: /opt/sepex)
  --reset           Drop and recreate the sepex database
  --skip-db         Skip database setup, only deploy
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

SEPEX_DB = "sepex"
SEPEX_USER = "sepex_app"
MASTER_USER = "twodfim_admin"

COMPOSE_CLOUD = """\
services:
  api:
    image: {image}
    container_name: sepex-api
    ports:
      - '80:5050'
    env_file:
      - .env
    volumes:
      - ./.data/api:/.data
      - /var/run/docker.sock:/var/run/docker.sock
    networks:
      - process_api_net

networks:
  process_api_net:
    external: true
"""

DEFAULT_IMAGE = "ghcr.io/dewberry/sepex:dev"

ENV_TEMPLATE = """\
# --- Core
API_NAME='sepex'
API_PORT='5050'

# --- File & Logging
LOG_LEVEL='INFO'
LOG_FILE='/.data/logs/api.jsonl'
TMP_JOB_LOGS_DIR='/.data/tmp/job_logs'

# --- Database (RDS)
DB_SERVICE='postgres'
POSTGRES_CONN_STRING='postgres://{db_user}:{db_password}@{rds_address}:5432/{db_name}?sslmode=require'

# --- Policies
EXPIRY_DAYS='7'

# --- Storage (S3, credentials via instance profile)
STORAGE_SERVICE='aws-s3'
STORAGE_BUCKET='{s3_bucket}'
STORAGE_METADATA_PREFIX='metadata'
STORAGE_RESULTS_PREFIX='results'
STORAGE_LOGS_PREFIX='logs'

# --- AWS
AWS_REGION='us-east-1'
BATCH_LOG_STREAM_GROUP='/aws/batch/job'

# --- Auth (disabled for testing)
AUTH_SERVICE=''
AUTH_LEVEL='0'

# --- Plugins
PLUGINS_LOAD_DIR=''
PLUGINS_DIR='/.data/plugins'

# --- Queue Resource Limits
MAX_LOCAL_CPUS=''
MAX_LOCAL_MEMORY_MB=''

# --- Docker networking (host = containers use EC2 network, can access instance profile credentials)
DOCKER_NETWORK='host'

# --- build_model container env vars (BUILDMODEL_ prefix stripped before passing to container)
BUILDMODEL_AWS_REQUEST_PAYER='requester'
BUILDMODEL_ARTIFACTS_S3_BUCKET='{s3_bucket}'
BUILDMODEL_MAJOR_VERSION='1'
"""


def run(cmd, check=True, capture=False, env=None):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, env=env)


def ensure_psql():
    if shutil.which("psql"):
        return
    print("Installing postgresql-client...")
    subprocess.run(["sudo", "apt-get", "update", "-qq"], capture_output=True)
    subprocess.run(
        ["sudo", "apt-get", "install", "-y", "-qq", "postgresql-client"],
        check=True, capture_output=True,
    )
    print("  postgresql-client installed.")


def fetch_master_password(secret_arn):
    result = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value",
         "--secret-id", secret_arn,
         "--query", "SecretString",
         "--output", "text"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout.strip())["password"]


def psql(rds_address, database, sql, env, check=True):
    cmd = ["psql", "-h", rds_address, "-U", MASTER_USER, "-d", database, "-c", sql]
    return subprocess.run(cmd, env=env, check=check, capture_output=True, text=True)


def setup_database(rds_address, sepex_password, pg_env, reset=False):
    """Create the sepex database and user on RDS."""
    if reset:
        print("Dropping existing database...")
        psql(rds_address, "postgres", f"DROP DATABASE IF EXISTS {SEPEX_DB};", pg_env, check=False)
        psql(rds_address, "postgres", f"DROP USER IF EXISTS {SEPEX_USER};", pg_env, check=False)
        print("  Dropped.")

    print("Creating database user...")
    psql(
        rds_address, "postgres",
        f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{SEPEX_USER}') THEN
                CREATE USER {SEPEX_USER} WITH PASSWORD '{sepex_password}';
            ELSE
                ALTER USER {SEPEX_USER} WITH PASSWORD '{sepex_password}';
            END IF;
        END $$;""",
        pg_env,
    )
    print(f"  {SEPEX_USER}: ready")

    print("Creating database...")
    result = psql(rds_address, "postgres", f"CREATE DATABASE {SEPEX_DB} OWNER {SEPEX_USER};", pg_env, check=False)
    if result.returncode != 0 and "already exists" in result.stderr:
        print(f"  {SEPEX_DB}: already exists, updating owner")
        psql(rds_address, "postgres", f"ALTER DATABASE {SEPEX_DB} OWNER TO {SEPEX_USER};", pg_env)
    else:
        print(f"  {SEPEX_DB}: created")

    # PG 16: transfer public schema ownership so sepex_app can create tables
    print("Setting schema ownership...")
    psql(rds_address, SEPEX_DB, f"ALTER SCHEMA public OWNER TO {SEPEX_USER};", pg_env)
    print(f"  public schema owned by {SEPEX_USER}")


def prepare_install_dir(install_dir):
    """Create the install directory if it doesn't exist."""
    if install_dir.exists():
        print(f"  {install_dir} already exists")
        return

    run(["sudo", "mkdir", "-p", str(install_dir)])
    run(["sudo", "chown", "-R", "ssm-user:ssm-user", str(install_dir)])
    print(f"  Created {install_dir}")


def write_config(install_dir, rds_address, sepex_password, s3_bucket, image):
    """Generate docker-compose.cloud.yaml and .env."""
    compose_path = install_dir / "docker-compose.cloud.yaml"
    compose_path.write_text(COMPOSE_CLOUD.format(image=image))
    print(f"  Wrote {compose_path} (image: {image})")

    encoded_password = quote(sepex_password, safe="")
    env_content = ENV_TEMPLATE.format(
        db_user=SEPEX_USER,
        db_password=encoded_password,
        rds_address=rds_address,
        db_name=SEPEX_DB,
        s3_bucket=s3_bucket,
    )
    # The .env contains the DB password in POSTGRES_CONN_STRING because SEPEX
    # reads it as a single connection string. File permissions restrict access.
    env_path = install_dir / ".env"
    env_path.touch(mode=0o600, exist_ok=True)
    env_path.write_text(env_content)
    print(f"  Wrote {env_path}")


def pull_and_start(install_dir):
    """Create network, pull image, start services."""
    compose_file = str(install_dir / "docker-compose.cloud.yaml")

    print("Creating docker network...")
    subprocess.run(
        ["docker", "network", "create", "process_api_net"],
        capture_output=True, check=False,
    )

    print("\nPulling SEPEX image...")
    run(["docker", "compose", "-f", compose_file, "pull"])

    print("\nStopping existing services...")
    run(["docker", "compose", "-f", compose_file, "down"], check=False)

    print("\nStarting services...")
    run(["docker", "compose", "-f", compose_file, "up", "-d"])

    print("\nWaiting for API to respond (30s timeout)...")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["curl", "-sf", "http://localhost/"],
            capture_output=True,
        )
        if result.returncode == 0:
            print("  SEPEX API is up.")
            break
        time.sleep(2)
    else:
        print("  WARNING: API did not respond within timeout.")
        print("  Check logs: docker compose -f docker-compose.cloud.yaml logs api")

    print("\nService status:")
    run(["docker", "compose", "-f", compose_file, "ps"])


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--rds-address", required=True, help="RDS hostname (terraform output -raw rds_address)")
    parser.add_argument("--rds-secret-arn", required=True, help="RDS master secret ARN (terraform output -raw rds_master_user_secret_arn)")
    parser.add_argument("--sepex-password", required=True, help="Password for the sepex_app database user")
    parser.add_argument("--s3-bucket", required=True, help="S3 bucket for SEPEX storage (prod or test bucket from terraform.tfvars)")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help=f"Container image (default: {DEFAULT_IMAGE})")
    parser.add_argument("--install-dir", default="/opt/sepex", help="Install directory (default: /opt/sepex)")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate the sepex database")
    parser.add_argument("--skip-db", action="store_true", help="Skip database setup, only deploy")
    args = parser.parse_args()

    install_dir = Path(args.install_dir)

    if not args.skip_db:
        ensure_psql()

        print("\nFetching RDS master password from Secrets Manager...")
        master_password = fetch_master_password(args.rds_secret_arn)
        print("  Master password retrieved.")

        pg_env = dict(os.environ, PGPASSWORD=master_password)

        print(f"\n--- Initializing database ---")
        print(f"RDS address: {args.rds_address}")
        print(f"SEPEX user: {SEPEX_USER}")
        setup_database(args.rds_address, args.sepex_password, pg_env, reset=args.reset)
        print("\nDone. Database ready.")

    print(f"\n--- Deploying SEPEX ---")
    prepare_install_dir(install_dir)

    print("\nWriting configuration...")
    write_config(install_dir, args.rds_address, args.sepex_password, args.s3_bucket, args.image)

    pull_and_start(install_dir)

    print("\n--- Setup complete ---")


if __name__ == "__main__":
    main()
