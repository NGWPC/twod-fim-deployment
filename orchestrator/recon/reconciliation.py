"""Core reconciliation logic. No Dagster dependency.

Used by the Dagster asset, the notebook, and the smoke check.
"""

from recon.state_store import StateStore
from recon.storage import model_artifact_path, model_base_path, object_exists
from recon.workers import ContainerRunner


def run_and_update(
    reach_id: int,
    revision: int,
    runner: ContainerRunner,
    store: StateStore,
    db_uri: str,
    lulc_source: str | None = None,
) -> dict:
    """Run build_model for a reach, verify the artifact, and update state.

    Returns a dict with reach_id, model_id, identity_hash, domain_code,
    artifact_path, and build_model_version.
    """
    payload: dict = {
        "reach_id": reach_id,
        "db_uri": db_uri,
        "base_output_path": model_base_path(reach_id),
    }
    if lulc_source:
        payload["lulc_source"] = lulc_source

    result = runner.run_build_model(payload)

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

    return {
        "reach_id": reach_id,
        "revision": revision,
        "model_id": result.model_id,
        "identity_hash": result.identity_hash,
        "domain_code": domain_code,
        "artifact_path": artifact_path,
        "build_model_version": result.build_model_version,
    }
