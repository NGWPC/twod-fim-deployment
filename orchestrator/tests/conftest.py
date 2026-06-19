import glob
import os
import platform
from pathlib import Path

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from orchestrator.state_store import StateStore

POSTGIS_IMAGE = "postgis/postgis:16-3.4"
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "db" / "schema"

if not os.environ.get("DOCKER_HOST"):
    if platform.system() == "Darwin":
        sock = os.path.expanduser("~/.docker/run/docker.sock")
    elif platform.system() == "Linux":
        sock = "/var/run/docker.sock"
    else:
        sock = None
    if sock and os.path.exists(sock):
        os.environ["DOCKER_HOST"] = f"unix://{sock}"


def _init_schema(connection_string: str):
    """Apply db/schema/*.sql in order, each in its own connection.

    Mimics docker-entrypoint-initdb.d: separate connections so
    ALTER DATABASE SET search_path takes effect for subsequent files.
    """
    sql_files = sorted(glob.glob(str(SCHEMA_DIR / "*.sql")))
    for sql_file in sql_files:
        sql = Path(sql_file).read_text()
        with psycopg.connect(connection_string) as conn:
            conn.execute(sql)
            conn.commit()


@pytest.fixture(scope="session")
def pg_container():
    """Spin up an ephemeral PostGIS container for the entire test session."""
    with PostgresContainer(
        image=POSTGIS_IMAGE,
        username="test",
        password="test",
        dbname="twodfim_test",
    ) as pg:
        yield pg


@pytest.fixture(scope="session")
def connection_string(pg_container):
    """Connection string for the ephemeral test database."""
    url = pg_container.get_connection_url().replace("postgresql+psycopg2", "postgresql")
    _init_schema(url)
    return url


@pytest.fixture
def store(connection_string):
    """Fresh StateStore per test — tables reset before each test."""
    s = StateStore(connection_string)
    s.reset()
    return s
