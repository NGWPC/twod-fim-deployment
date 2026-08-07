import dagster as dg
from orchestrator.config import settings
from orchestrator.state_store import StateStore


class StateStoreResource(dg.ConfigurableResource):
    connection_string: str = settings.pipeline_db_connection_string

    def get_store(self) -> StateStore:
        return StateStore(self.connection_string)


@dg.definitions
def defs() -> dg.Definitions:
    return dg.Definitions(
        resources={"state_store": StateStoreResource()},
    )
