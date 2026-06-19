"""Worker stubs. Replace body of each function with real container call when ready.

The asset layer calls these functions — swapping the implementation
here does not require changes to the orchestrator (process_build_model etc.).
"""

import hashlib
import json
import re
import time

from pydantic import BaseModel, field_validator, model_validator

from orchestrator.storage import put_json

IDENTITY_HASH_RE = re.compile(r"^[0-9a-f]{8}$")
DOMAIN_CODE_RE = re.compile(r"^N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$")


class BuildResult(BaseModel):
    identity_hash: str
    model_id: str

    @field_validator("identity_hash")
    @classmethod
    def validate_identity_hash(cls, v: str) -> str:
        if not IDENTITY_HASH_RE.match(v):
            raise ValueError(f"identity_hash must be 8 lowercase hex chars, got '{v}'")
        return v

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, v: str) -> str:
        if "+" not in v:
            raise ValueError(f"model_id must contain '+' separator, got '{v}'")
        identity, domain = v.split("+", 1)
        if not IDENTITY_HASH_RE.match(identity):
            raise ValueError(f"model_id identity part must be 8 lowercase hex chars, got '{identity}'")
        if not DOMAIN_CODE_RE.match(domain):
            raise ValueError(f"model_id domain part must match N*S*E*W* pattern, got '{domain}'")
        return v

    @model_validator(mode="after")
    def validate_identity_consistency(self) -> "BuildResult":
        expected = self.model_id.split("+", 1)[0]
        if self.identity_hash != expected:
            raise ValueError(
                f"identity_hash '{self.identity_hash}' does not match "
                f"model_id identity '{expected}'"
            )
        return self


def hash_dict(d: dict, algorithm: str = "sha256", role_length: int | None = None) -> str:
    """Compute the canonical hash of a dictionary.

    canonical JSON (sorted keys, no insignificant whitespace).
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
