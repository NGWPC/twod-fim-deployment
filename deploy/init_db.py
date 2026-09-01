#!/usr/bin/env python3
"""Initialize the RDS database for the 2D FIM pipeline.

Reads connection info and credentials from .env in the repo root.
"""

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

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


def psql(endpoint: str, database: str, command: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["psql", "-h", endpoint, "-U", MASTER_USER, "-d", database, "-c", command],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if check and result.returncode != 0:
        if "already exists" in result.stderr:
            return result
        print(f"  psql error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result


def psql_file(endpoint: str, database: str, filepath: str) -> None:
    result = subprocess.run(
        ["psql", "-h", endpoint, "-U", MASTER_USER, "-d", database, "-f", filepath],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if result.returncode != 0:
        print(f"  psql error applying {filepath}: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


def reset_database(endpoint: str) -> None:
    print("Resetting database...")

    print(f"  Terminating connections to {TWODFIM_DB}")
    psql(
        endpoint,
        "postgres",
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{TWODFIM_DB}' AND pid <> pg_backend_pid();",
        check=False,
    )

    print(f"  Taking ownership of {TWODFIM_DB}")
    psql(endpoint, "postgres", f"ALTER DATABASE {TWODFIM_DB} OWNER TO {MASTER_USER};", check=False)

    print(f"  Dropping {TWODFIM_DB}")
    psql(endpoint, "postgres", f"DROP DATABASE IF EXISTS {TWODFIM_DB};", check=False)

    print("Database dropped.")


def create_user(endpoint: str, user: str, password: str) -> None:
    print("Creating application user...")
    psql(
        endpoint,
        "postgres",
        f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{user}') THEN
                CREATE USER {user} WITH PASSWORD '{password}';
            ELSE
                ALTER USER {user} WITH PASSWORD '{password}';
            END IF;
        END $$;""",
    )
    print(f"  {user}: ready")


def create_database(endpoint: str, user: str) -> None:
    print("Creating database...")
    result = psql(endpoint, "postgres", f"CREATE DATABASE {TWODFIM_DB} OWNER {user};", check=False)
    if result.returncode != 0 and "already exists" in result.stderr:
        print(f"  {TWODFIM_DB}: already exists, updating owner")
        psql(endpoint, "postgres", f"ALTER DATABASE {TWODFIM_DB} OWNER TO {user};")
    else:
        print(f"  {TWODFIM_DB}: created")


def apply_schema(endpoint: str, schema_dir: Path) -> None:
    print(f"Applying schema from {schema_dir}...")

    extensions = schema_dir / "00_extensions.sql"
    if not extensions.exists():
        print(f"  Schema file not found: {extensions}", file=sys.stderr)
        sys.exit(1)

    schema_files = [str(extensions)]
    schema_files.extend(sorted(glob.glob(str(schema_dir / "0[1-9]_*.sql"))))

    for filepath in schema_files:
        print(f"  Applying {Path(filepath).name}")
        psql_file(endpoint, TWODFIM_DB, filepath)

    print("Schema applied.")


def grant_permissions(endpoint: str, user: str) -> None:
    print("Granting permissions...")

    grants = [
        f"GRANT ALL ON SCHEMA public TO {user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {user};",
        f"GRANT USAGE ON SCHEMA twodfim TO {user};",
        f"GRANT ALL ON ALL TABLES IN SCHEMA twodfim TO {user};",
        f"GRANT ALL ON ALL SEQUENCES IN SCHEMA twodfim TO {user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA twodfim GRANT ALL ON TABLES TO {user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA twodfim GRANT ALL ON SEQUENCES TO {user};",
    ]

    for grant in grants:
        psql(endpoint, TWODFIM_DB, grant)

    print(f"  Permissions granted to {user}")


def find_schema_dir() -> Path:
    candidates = [
        REPO_ROOT / "db" / "schema",
        Path("/opt/twod-fim-deployment/db/schema"),
    ]
    for candidate in candidates:
        if candidate.exists() and (candidate / "00_extensions.sql").exists():
            return candidate

    print("Schema directory not found. Use --schema-dir or clone the repo.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize RDS database for the 2D FIM pipeline")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate database from scratch")
    parser.add_argument("--schema-dir", type=Path, default=None, help="Path to db/schema/ directory")
    args = parser.parse_args()

    env = read_env(ENV_FILE)

    endpoint = env.get("POSTGRES_HOST")
    if not endpoint:
        sys.exit("POSTGRES_HOST not found in .env")

    user = env.get("POSTGRES_USER")
    password = env.get("POSTGRES_PASSWORD")

    if not all([user, password]):
        sys.exit("POSTGRES_USER and POSTGRES_PASSWORD must be set in .env")

    schema_dir = args.schema_dir or find_schema_dir()

    print(f"RDS endpoint: {endpoint}")
    print(f"Pipeline user: {user}")
    print(f"Schema dir: {schema_dir}")

    if args.reset:
        reset_database(endpoint)

    create_user(endpoint, user, password)
    create_database(endpoint, user)
    apply_schema(endpoint, schema_dir)
    grant_permissions(endpoint, user)

    print("\nDone. Database ready.")


if __name__ == "__main__":
    main()
