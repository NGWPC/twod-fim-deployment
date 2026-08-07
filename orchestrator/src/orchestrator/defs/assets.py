import dagster as dg

from orchestrator.config import settings
from orchestrator.defs.resources import StateStoreResource
from orchestrator.storage import model_artifact_path, model_base_path, object_exists
from orchestrator.workers import build_model

reaches_partitions = dg.DynamicPartitionsDefinition(name="reaches")


@dg.asset(partitions_def=reaches_partitions)
def process_build_model(
    context: dg.AssetExecutionContext,
    state_store: StateStoreResource,
) -> dg.MaterializeResult:
    """Orchestrator wrapper for build_model worker.

    1. Read desired_state for the reach.
    2. Construct base_output_path (model_base_path) and submit build_model.
    3. Verify model.json exists at the expected S3 path (model_artifact_path).
    4. Update current_state with worker response.

    Does not manage the processing flag — that is reserved for the future
    cascade coordinator (build → nd → kwse). On error: re-raises;
    run_key dedup prevents the sensor from resubmitting the same revision.
    """
    reach_id = int(context.partition_key)
    store = state_store.get_store()

    desired = store.get_desired(reach_id)
    if desired is None:
        raise ValueError(f"No desired_state for reach {reach_id}")
    revision = desired["revision"]

    base_output_path = model_base_path(reach_id)

    context.log.info(f"Processing build_model for reach {reach_id} (revision {revision})")

    result = build_model(
        reach_id=reach_id,
        db_uri=settings.pipeline_db_connection_string,
        base_output_path=base_output_path,
    )

    artifact_path = model_artifact_path(reach_id, result.model_id)
    if not object_exists(artifact_path):
        raise RuntimeError(f"model.json not found at {artifact_path}")

    domain_code = result.model_id.split("+", 1)[1]

    store.update_current(
        reach_id=reach_id,
        identity_hash=result.identity_hash,
        domain_code=domain_code,
        applied_revision=revision,
    )
    context.log.info(f"Reach {reach_id} complete: {result.model_id}")

    return dg.MaterializeResult(
        metadata={
            "reach_id": dg.MetadataValue.int(reach_id),
            "revision": dg.MetadataValue.int(revision),
            "model_id": dg.MetadataValue.text(result.model_id),
            "identity_hash": dg.MetadataValue.text(result.identity_hash),
            "domain_code": dg.MetadataValue.text(domain_code),
            "artifact_path": dg.MetadataValue.text(artifact_path),
        }
    )
