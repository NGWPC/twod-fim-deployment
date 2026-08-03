import dagster as dg

from orchestrator.config import settings
from orchestrator.defs.resources import RunnerResource, StateStoreResource
from orchestrator.storage import model_artifact_path, model_base_path, object_exists

reaches_partitions = dg.DynamicPartitionsDefinition(name="reaches")


@dg.asset(partitions_def=reaches_partitions)
def process_build_model(
    context: dg.AssetExecutionContext,
    state_store: StateStoreResource,
    runner: RunnerResource,
) -> dg.MaterializeResult:
    """Orchestrator wrapper: launches build_model container and updates DB.

    1. Read desired_state for the reach.
    2. Launch build_model container via the runner.
    3. Verify model_manifest.json exists at the expected S3 path.
    4. Update current_state with the container's result.

    Does not manage the processing flag -- that is reserved for the future
    cascade coordinator (build -> nd -> kwse). On error: re-raises;
    run_key dedup prevents the sensor from resubmitting the same revision.
    """
    reach_id = int(context.partition_key)
    store = state_store.get_store()

    desired = store.get_desired(reach_id)
    if desired is None:
        raise ValueError(f"No desired_state for reach {reach_id}")
    revision = desired["revision"]

    base_output_path = model_base_path(reach_id)

    payload = {
        "reach_id": reach_id,
        "db_uri": settings.pipeline_db_connection_string,
        "base_output_path": base_output_path,
    }
    if settings.lulc_source:
        payload["lulc_source"] = settings.lulc_source

    context.log.info(f"Submitting build_model for reach {reach_id} (revision {revision})")

    result = runner.get_runner().run_build_model(payload)

    artifact_path = model_artifact_path(reach_id, result.model_id)
    if not object_exists(artifact_path):
        raise RuntimeError(f"model_manifest.json not found at {artifact_path}")

    domain_code = result.model_id.split("_", 1)[1]

    store.update_current(
        reach_id=reach_id,
        identity_hash=result.identity_hash,
        domain_code=domain_code,
        applied_revision=revision,
        build_model_version=result.build_model_version,
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
            "build_model_version": dg.MetadataValue.text(result.build_model_version),
        }
    )
