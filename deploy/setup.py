#!/usr/bin/env python3
"""One-command setup: initialize databases and deploy Dagster services.

Orchestrates the common steps (psql install, master password fetch)
then calls init_db.py and deploy.py.

Usage:
  python deploy/setup.py                    # init db + deploy
  python deploy/setup.py --reset            # clean slate + deploy
  python deploy/setup.py --skip-db          # redeploy without touching DB
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_DIR = Path(__file__).resolve().parent
ENV_FILE = REPO_ROOT / ".env"


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        sys.exit(f".env not found at {path}\nCreate it from example.cloud.env before running.")
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key and value:
            env[key.strip()] = value.strip()
    return env


def ensure_psql() -> None:
    if shutil.which("psql"):
        return
    print("Installing postgresql-client...")
    subprocess.run(["sudo", "apt-get", "update", "-qq"], capture_output=True)
    subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "postgresql-client"], check=True, capture_output=True)
    print("  postgresql-client installed.")


def fetch_master_password(env: dict[str, str]) -> str:
    secret_arn = env.get("RDS_SECRET_ARN") or os.environ.get("RDS_SECRET_ARN")
    if not secret_arn:
        sys.exit("RDS_SECRET_ARN must be set in .env. Get it from: terraform output -raw rds_master_user_secret_arn")

    result = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value",
         "--secret-id", secret_arn,
         "--query", "SecretString",
         "--output", "text"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout.strip())["password"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="Drop and recreate databases from scratch")
    parser.add_argument("--skip-db", action="store_true", help="Skip database setup, only deploy services")
    args, remaining = parser.parse_known_args()

    if not args.skip_db:
        ensure_psql()

        print("\nFetching RDS master password from Secrets Manager...")
        env = read_env(ENV_FILE)
        master_password = fetch_master_password(env)
        print("  Master password retrieved.")

        env = dict(os.environ, PGPASSWORD=master_password)

        init_cmd = [sys.executable, str(DEPLOY_DIR / "init_db.py")]
        if args.reset:
            init_cmd.append("--reset")

        print("\n--- Initializing databases ---")
        result = subprocess.run(init_cmd, env=env)
        if result.returncode != 0:
            sys.exit(1)

    print("\n--- Deploying services ---")
    deploy_cmd = [sys.executable, str(DEPLOY_DIR / "deploy.py"), *remaining]
    result = subprocess.run(deploy_cmd)
    if result.returncode != 0:
        sys.exit(1)

    print("\n--- Setup complete ---")


if __name__ == "__main__":
    main()
