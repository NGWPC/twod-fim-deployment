#!/usr/bin/env python3
"""Initialize the RDS database for the 2D FIM pipeline.

Reads connection info from .env in the repo root. Resolves the RDS
master password from Secrets Manager (via RDS_SECRET_ARN in .env),
falling back to the PGPASSWORD environment variable when Secrets
Manager is not available.

Requires psycopg, installed with the orchestrator package.

Usage:
  python3 deploy/init_db.py                    # idempotent DB setup
  python3 deploy/init_db.py --reset            # drop and recreate from scratch
  PGPASSWORD=<pw> python3 deploy/init_db.py    # explicit master password
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

MASTER_USER = "twodfim_admin"
TWODFIM_DB = "twodfim"


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


def resolve_master_password(env: dict[str, str]) -> str:
    password = os.environ.get("PGPASSWORD")
    if password:
        return password

    secret_arn = env.get("RDS_SECRET_ARN") or os.environ.get("RDS_SECRET_ARN")
    if secret_arn:
        print("Fetching RDS master password from Secrets Manager...")
        result = subprocess.run(
            ["aws", "secretsmanager", "get-secret-value",
             "--secret-id", secret_arn,
             "--query", "SecretString",
             "--output", "text"],
            capture_output=True, text=True, check=True,
        )
        password = json.loads(result.stdout.strip())["password"]
        print("  Master password retrieved.")
        return password

    sys.exit(
        "Master password not found. Either:\n"
        "  - Set RDS_SECRET_ARN in .env (terraform output -raw rds_master_user_secret_arn)\n"
        "  - Export PGPASSWORD with the RDS master password"
    )


def _connect(host: str, port: int, database: str, password: str) -> psycopg.Connection:
    return psycopg.connect(
        host=host, port=port, dbname=database,
        user=MASTER_USER, password=password,
        autocommit=True,
    )


def reset_database(host: str, port: int, password: str) -> None:
    print("Resetting database...")
    with _connect(host, port, "postgres", password) as conn:
        print(f"  Terminating connections to {TWODFIM_DB}")
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            [TWODFIM_DB],
        )

        print(f"  Taking ownership of {TWODFIM_DB}")
        try:
            conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(TWODFIM_DB), sql.Identifier(MASTER_USER),
            ))
        except psycopg.errors.InvalidCatalogName:
            pass

        print(f"  Dropping {TWODFIM_DB}")
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(
            sql.Identifier(TWODFIM_DB),
        ))
    print("Database dropped.")


def create_user(host: str, port: int, password: str,
                user: str, user_password: str) -> None:
    print("Creating application user...")
    with _connect(host, port, "postgres", password) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", [user],
        ).fetchone()
        if exists:
            conn.execute(sql.SQL("ALTER USER {} WITH PASSWORD {}").format(
                sql.Identifier(user), sql.Literal(user_password),
            ))
        else:
            conn.execute(sql.SQL("CREATE USER {} WITH PASSWORD {}").format(
                sql.Identifier(user), sql.Literal(user_password),
            ))
    print(f"  {user}: ready")


def create_database(host: str, port: int, password: str, user: str) -> None:
    print("Creating database...")
    with _connect(host, port, "postgres", password) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
            [TWODFIM_DB],
        ).fetchone()
        if exists:
            print(f"  {TWODFIM_DB}: already exists, updating owner")
            conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(TWODFIM_DB), sql.Identifier(user),
            ))
        else:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(TWODFIM_DB), sql.Identifier(user),
            ))
            print(f"  {TWODFIM_DB}: created")


def apply_schema(host: str, port: int, password: str,
                 schema_dir: Path) -> None:
    print(f"Applying schema from {schema_dir}...")
    extensions = schema_dir / "00_extensions.sql"
    if not extensions.exists():
        sys.exit(f"Schema file not found: {extensions}")

    schema_files = [str(extensions)]
    schema_files.extend(sorted(glob.glob(str(schema_dir / "0[1-9]_*.sql"))))

    with _connect(host, port, TWODFIM_DB, password) as conn:
        for filepath in schema_files:
            print(f"  Applying {Path(filepath).name}")
            conn.execute(Path(filepath).read_text())
    print("Schema applied.")


def grant_permissions(host: str, port: int, password: str,
                      user: str) -> None:
    print("Granting permissions...")
    uid = sql.Identifier(user)
    grants = [
        sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(uid),
        sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA public TO {}").format(uid),
        sql.SQL("GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {}").format(uid),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {}").format(uid),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {}").format(uid),
        sql.SQL("GRANT USAGE ON SCHEMA twodfim TO {}").format(uid),
        sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA twodfim TO {}").format(uid),
        sql.SQL("GRANT ALL ON ALL SEQUENCES IN SCHEMA twodfim TO {}").format(uid),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA twodfim GRANT ALL ON TABLES TO {}").format(uid),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA twodfim GRANT ALL ON SEQUENCES TO {}").format(uid),
    ]
    with _connect(host, port, TWODFIM_DB, password) as conn:
        for grant in grants:
            conn.execute(grant)
    print(f"  Permissions granted to {user}")


def find_schema_dir() -> Path:
    candidates = [
        REPO_ROOT / "db" / "schema",
        Path("/opt/twod-fim-deployment/db/schema"),
    ]
    for candidate in candidates:
        if candidate.exists() and (candidate / "00_extensions.sql").exists():
            return candidate
    sys.exit("Schema directory not found. Use --schema-dir or clone the repo.")


def run(host: str, port: int, user: str, password: str,
        master_password: str, reset: bool, schema_dir: Path) -> None:
    print(f"RDS endpoint: {host}:{port}")
    print(f"Pipeline user: {user}")
    print(f"Schema dir: {schema_dir}")

    if reset:
        reset_database(host, port, master_password)

    create_user(host, port, master_password, user, password)
    create_database(host, port, master_password, user)
    apply_schema(host, port, master_password, schema_dir)
    grant_permissions(host, port, master_password, user)

    print("\nDone. Database ready.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Drop and recreate database from scratch",
    )
    parser.add_argument(
        "--schema-dir", type=Path, default=None,
        help="Path to db/schema/ directory",
    )
    args = parser.parse_args()

    env = read_env(ENV_FILE)

    host = env.get("POSTGRES_HOST")
    if not host:
        sys.exit("POSTGRES_HOST not found in .env")
    user = env.get("POSTGRES_USER")
    password = env.get("POSTGRES_PASSWORD")
    if not all([user, password]):
        sys.exit("POSTGRES_USER and POSTGRES_PASSWORD must be set in .env")

    master_password = resolve_master_password(env)

    run(
        host=host,
        port=int(env.get("POSTGRES_PORT", "5432")),
        user=user,
        password=password,
        master_password=master_password,
        reset=args.reset,
        schema_dir=args.schema_dir or find_schema_dir(),
    )


if __name__ == "__main__":
    main()
