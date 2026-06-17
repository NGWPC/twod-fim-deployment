import os
import platform

import pytest
from testcontainers.postgres import PostgresContainer

from orchestrator.state_store import StateStore

POSTGIS_IMAGE = "imresamu/postgis:16-3.4-alpine"

if not os.environ.get("DOCKER_HOST"):
    if platform.system() == "Darwin":
        sock = os.path.expanduser("~/.docker/run/docker.sock")
    elif platform.system() == "Linux":
        sock = "/var/run/docker.sock"
    else:
        sock = None
    if sock and os.path.exists(sock):
        os.environ["DOCKER_HOST"] = f"unix://{sock}"


@pytest.fixture(scope="session")
def pg_container():
    """Spin up an ephemeral PostGIS container for the entire test session."""
    with PostgresContainer(
        image=POSTGIS_IMAGE,
        username="test",
        password="test",
        dbname="pipeline_test",
    ) as pg:
        yield pg


@pytest.fixture(scope="session")
def connection_string(pg_container):
    """Connection string for the ephemeral test database."""
    return pg_container.get_connection_url().replace("postgresql+psycopg2", "postgresql")


@pytest.fixture
def store(connection_string):
    """Fresh StateStore per test — tables reset before each test."""
    s = StateStore(connection_string)
    s.reset()
    return s
