"""Container runner for build_model.

The asset layer calls these -- swapping the runner implementation
does not require changes to the orchestrator dispatch code.
"""

import json
import logging
import re
import subprocess
import uuid

from typing import Protocol

from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)

IDENTITY_HASH_RE = re.compile(r"^[0-9a-f]{8}$")
DOMAIN_CODE_RE = re.compile(r"^N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$")


class BuildResult(BaseModel):
    identity_hash: str
    model_id: str
    build_model_version: str

    @field_validator("identity_hash")
    @classmethod
    def validate_identity_hash(cls, v: str) -> str:
        if not IDENTITY_HASH_RE.match(v):
            raise ValueError(f"identity_hash must be 8 lowercase hex chars, got '{v}'")
        return v

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, v: str) -> str:
        if "_" not in v:
            raise ValueError(f"model_id must contain '_' separator, got '{v}'")
        identity, domain = v.split("_", 1)
        if not IDENTITY_HASH_RE.match(identity):
            raise ValueError(f"model_id identity part must be 8 lowercase hex chars, got '{identity}'")
        if not DOMAIN_CODE_RE.match(domain):
            raise ValueError(f"model_id domain part must match N*S*E*W* pattern, got '{domain}'")
        return v

    @model_validator(mode="after")
    def validate_identity_consistency(self) -> "BuildResult":
        expected = self.model_id.split("_", 1)[0]
        if self.identity_hash != expected:
            raise ValueError(
                f"identity_hash '{self.identity_hash}' does not match "
                f"model_id identity '{expected}'"
            )
        return self


class ContainerRunner(Protocol):
    def run_build_model(self, payload: dict) -> BuildResult: ...


class LocalDockerRunner:
    """Runs build_model as a local Docker container via docker run."""

    def __init__(
        self,
        image: str,
        network: str = "twodfim",
        env_vars: dict[str, str] | None = None,
        timeout: int = 3600,
        platform: str | None = None,
        volumes: list[str] | None = None,
    ):
        self.image = image
        self.network = network
        self.env_vars = env_vars or {}
        self.timeout = timeout
        self.platform = platform
        self.volumes = volumes or []

    @property
    def image_tag(self) -> str:
        if ":" in self.image:
            return self.image.rsplit(":", 1)[1]
        return "latest"

    def run_build_model(self, payload: dict) -> BuildResult:
        container_name = f"build-model-{payload.get('reach_id', 'unknown')}-{uuid.uuid4().hex[:8]}"

        cmd = ["docker", "run", "--rm", "--name", container_name, "--network", self.network]
        if self.platform:
            cmd.extend(["--platform", self.platform])
        for vol in self.volumes:
            cmd.extend(["-v", vol])
        for key, val in self.env_vars.items():
            cmd.extend(["--env", f"{key}={val}"])

        payload_json = json.dumps(payload, separators=(",", ":"))
        cmd.extend([self.image, payload_json])

        logger.info("Launching build_model container %s for reach %s", container_name, payload.get("reach_id"))

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", container_name], capture_output=True)
            subprocess.run(["docker", "rm", container_name], capture_output=True)
            raise RuntimeError(
                f"build_model container timed out after {self.timeout}s "
                f"for reach {payload.get('reach_id')}"
            )

        if proc.returncode != 0:
            raise RuntimeError(
                f"build_model container failed (exit {proc.returncode}) "
                f"for reach {payload.get('reach_id')}:\n"
                f"stderr: {proc.stderr[-2000:]}"
            )

        if proc.stderr:
            logger.info(proc.stderr)

        stdout_lines = proc.stdout.strip().splitlines()
        if not stdout_lines:
            raise RuntimeError(
                f"build_model container produced no stdout for reach {payload.get('reach_id')}"
            )

        result_line = stdout_lines[-1]
        try:
            raw = json.loads(result_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Failed to parse container stdout as JSON: {exc}\n"
                f"Last line: {result_line[:500]}"
            ) from exc

        result_data = raw.get("plugin_results", raw)
        return BuildResult(
            identity_hash=result_data["identity_hash"],
            model_id=result_data["model_id"],
            build_model_version=self.image_tag,
        )
