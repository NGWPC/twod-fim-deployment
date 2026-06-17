"""Worker stubs. Replace body of each function with real container call when ready.

The asset and sensor layers call these functions — swapping the implementation
here does not require changes to the orchestrator (process_build_model etc.).
"""

import hashlib
import json
import time

from pydantic import BaseModel

from orchestrator.storage import put_json


class BuildResult(BaseModel):
    identity_hash: str
    model_id: str


def hash_dict(d: dict, algorithm: str = "sha256", role_length: int | None = None) -> str:
    """Compute the canonical hash of a dictionary.

    From tooling team — canonical JSON (sorted keys, no insignificant whitespace).
    """
    hasher = hashlib.new(algorithm)
    d_str = json.dumps(d, sort_keys=True, separators=(",", ":"))
    hasher.update(d_str.encode())
    hash_str = hasher.hexdigest().lower()
    if role_length:
        hash_str = hash_str[:role_length]
    return hash_str


def build_model(reach_id: int, db_uri: str, base_output_path: str, **kwargs) -> BuildResult:
    """Stub: simulates build_model worker.

    Real worker: generates terrain, roughness, geometry, boundary-condition artifacts.
    Stub: sleeps, computes a deterministic hash, writes model.json to S3.
    """
    time.sleep(3)

    identity = {"reach_id": reach_id, "db_uri": db_uri}
    identity_hash = hash_dict(identity, role_length=8)
    domain_code = "N200S200E300W200"
    model_id = f"{identity_hash}+{domain_code}"

    model_json = json.dumps({
        "reach_id": reach_id,
        "identity_hash": identity_hash,
        "model_id": model_id,
        "domain_code": domain_code,
        "stub": True,
    })

    artifact_path = f"{base_output_path}/{model_id}/model.json"
    put_json(artifact_path, model_json)

    return BuildResult(identity_hash=identity_hash, model_id=model_id)
