import os

import dagster as dg
from orchestrator.config import settings
from orchestrator.state_store import StateStore
from orchestrator.workers import ContainerRunner, LocalDockerRunner


class StateStoreResource(dg.ConfigurableResource):
    connection_string: str = settings.pipeline_db_connection_string

    def get_store(self) -> StateStore:
        return StateStore(self.connection_string)


class RunnerResource(dg.ConfigurableResource):
    image: str = settings.build_model_image
    network: str = settings.docker_network

    def get_runner(self) -> ContainerRunner:
        env_vars: dict[str, str] = {}
        for key in ("AWS_ENDPOINT_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REQUEST_PAYER"):
            val = os.environ.get(key)
            if val:
                env_vars[key] = val
        volumes = []
        if settings.docker_data_dir:
            volumes.append(f"{settings.docker_data_dir}:/data:ro")
        return LocalDockerRunner(
            image=self.image,
            network=self.network,
            env_vars=env_vars,
            timeout=settings.build_model_timeout,
            platform=settings.docker_platform,
            volumes=volumes,
        )


@dg.definitions
def defs() -> dg.Definitions:
    return dg.Definitions(
        resources={"state_store": StateStoreResource(), "runner": RunnerResource()},
    )
