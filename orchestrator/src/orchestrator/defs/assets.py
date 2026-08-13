import dagster as dg

from orchestrator.config import settings
from orchestrator.defs.resources import RunnerResource, StateStoreResource
from orchestrator.reconciliation import run_and_update

reaches_partitions = dg.DynamicPartitionsDefinition(name="reaches")


@dg.asset(partitions_def=reaches_partitions)
def process_build_model(
    context: dg.AssetExecutionContext,
    state_store: StateStoreResource,
    runner: RunnerResource,
) -> dg.MaterializeResult:
    """Dagster wrapper around the core reconciliation logic.

    Reads desired_state, delegates to run_and_update() for container
    launch + DB update, and returns a MaterializeResult with metadata.
    The core logic lives in reconciliation.py (no Dagster dependency).
    """
    reach_id = int(context.partition_key)
    store = state_store.get_store()

    desired = store.get_desired(reach_id)
    if desired is None:
        raise ValueError(f"No desired_state for reach {reach_id}")
    revision = desired["revision"]

    context.log.info(f"Submitting build_model for reach {reach_id} (revision {revision})")

    result = run_and_update(
        reach_id=reach_id,
        revision=revision,
        runner=runner.get_runner(),
        store=store,
        db_uri=settings.pipeline_db_connection_string,
        lulc_source=settings.lulc_source,
    )

    context.log.info(f"Reach {reach_id} complete: {result['model_id']}")

    return dg.MaterializeResult(
        metadata={
            "reach_id": dg.MetadataValue.int(result["reach_id"]),
            "revision": dg.MetadataValue.int(result["revision"]),
            "model_id": dg.MetadataValue.text(result["model_id"]),
            "identity_hash": dg.MetadataValue.text(result["identity_hash"]),
            "domain_code": dg.MetadataValue.text(result["domain_code"]),
            "artifact_path": dg.MetadataValue.text(result["artifact_path"]),
            "build_model_version": dg.MetadataValue.text(result["build_model_version"]),
        }
    )
