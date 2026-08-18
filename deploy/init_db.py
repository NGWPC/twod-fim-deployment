#!/usr/bin/env python3
"""Initialize RDS databases for the 2D FIM pipeline.

Reads connection info and credentials from .env in the repo root
(same file used by docker-compose.cloud.yaml).
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
DAGSTER_DB = "dagster"
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
    # PGPASSWORD from environment (set by setup.py or manually)
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


def reset_databases(endpoint: str) -> None:
    print("Resetting databases...")

    for db in [DAGSTER_DB, TWODFIM_DB]:
        print(f"  Terminating connections to {db}")
        psql(
            endpoint,
            "postgres",
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db}' AND pid <> pg_backend_pid();",
            check=False,
        )

        print(f"  Taking ownership of {db}")
        psql(endpoint, "postgres", f"ALTER DATABASE {db} OWNER TO {MASTER_USER};", check=False)

        print(f"  Dropping {db}")
        psql(endpoint, "postgres", f"DROP DATABASE IF EXISTS {db};", check=False)

    print("Databases dropped.")


def create_users(endpoint: str, dagster_user: str, dagster_password: str, twodfim_user: str, twodfim_password: str) -> None:
    print("Creating application users...")

    for user, password in [(dagster_user, dagster_password), (twodfim_user, twodfim_password)]:
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


def create_databases(endpoint: str, dagster_user: str, twodfim_user: str) -> None:
    print("Creating databases...")

    result = psql(endpoint, "postgres", f"CREATE DATABASE {DAGSTER_DB} OWNER {dagster_user};", check=False)
    if result.returncode != 0 and "already exists" in result.stderr:
        print(f"  {DAGSTER_DB}: already exists, updating owner")
        psql(endpoint, "postgres", f"ALTER DATABASE {DAGSTER_DB} OWNER TO {dagster_user};")
    else:
        print(f"  {DAGSTER_DB}: created")

    result = psql(endpoint, "postgres", f"CREATE DATABASE {TWODFIM_DB} OWNER {twodfim_user};", check=False)
    if result.returncode != 0 and "already exists" in result.stderr:
        print(f"  {TWODFIM_DB}: already exists, updating owner")
        psql(endpoint, "postgres", f"ALTER DATABASE {TWODFIM_DB} OWNER TO {twodfim_user};")
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


def grant_permissions(endpoint: str, dagster_user: str, twodfim_user: str) -> None:
    print("Granting permissions...")

    dagster_grants = [
        f"GRANT ALL ON SCHEMA public TO {dagster_user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {dagster_user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {dagster_user};",
    ]

    for grant in dagster_grants:
        psql(endpoint, DAGSTER_DB, grant)

    print(f"  Permissions granted to {dagster_user}")

    twodfim_grants = [
        f"GRANT ALL ON SCHEMA public TO {twodfim_user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {twodfim_user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {twodfim_user};",
        f"GRANT USAGE ON SCHEMA twodfim TO {twodfim_user};",
        f"GRANT ALL ON ALL TABLES IN SCHEMA twodfim TO {twodfim_user};",
        f"GRANT ALL ON ALL SEQUENCES IN SCHEMA twodfim TO {twodfim_user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA twodfim GRANT ALL ON TABLES TO {twodfim_user};",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA twodfim GRANT ALL ON SEQUENCES TO {twodfim_user};",
    ]

    for grant in twodfim_grants:
        psql(endpoint, TWODFIM_DB, grant)

    print(f"  Permissions granted to {twodfim_user}")


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
    parser = argparse.ArgumentParser(description="Initialize RDS databases for the 2D FIM pipeline")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate databases from scratch")
    parser.add_argument("--schema-dir", type=Path, default=None, help="Path to db/schema/ directory")
    args = parser.parse_args()

    env = read_env(ENV_FILE)

    endpoint = env.get("POSTGRES_HOST") or env.get("DAGSTER_PG_HOST")
    if not endpoint:
        sys.exit("POSTGRES_HOST or DAGSTER_PG_HOST not found in .env")

    dagster_user = env.get("DAGSTER_PG_USER")
    dagster_password = env.get("DAGSTER_PG_PASSWORD")
    twodfim_user = env.get("POSTGRES_USER")
    twodfim_password = env.get("POSTGRES_PASSWORD")

    if not all([dagster_user, dagster_password, twodfim_user, twodfim_password]):
        sys.exit("DAGSTER_PG_USER, DAGSTER_PG_PASSWORD, POSTGRES_USER, POSTGRES_PASSWORD must all be set in .env")

    schema_dir = args.schema_dir or find_schema_dir()

    print(f"RDS endpoint: {endpoint}")
    print(f"Dagster user: {dagster_user}")
    print(f"Pipeline user: {twodfim_user}")
    print(f"Schema dir: {schema_dir}")

    if args.reset:
        reset_databases(endpoint)

    create_users(endpoint, dagster_user, dagster_password, twodfim_user, twodfim_password)
    create_databases(endpoint, dagster_user, twodfim_user)
    apply_schema(endpoint, schema_dir)
    grant_permissions(endpoint, dagster_user, twodfim_user)

    print("\nDone. Databases ready.")


if __name__ == "__main__":
    main()
