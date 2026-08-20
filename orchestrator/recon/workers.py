"""Container runners for the job images.

The loop calls these through the ContainerRunner protocol, so swapping the
runner — local Docker now, AWS Batch or SEPEX later — changes nothing above it.
"""

import json
import logging
import os
import re
import subprocess
import uuid

from enum import Enum
from typing import Protocol

from pydantic import BaseModel, field_validator, model_validator

from recon.config import settings

logger = logging.getLogger(__name__)


def job_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment every job container needs to reach this deployment.

    Two S3 clients live inside these images and they are configured separately,
    which is not obvious and costs an afternoon to discover:

      boto3  reads AWS_ENDPOINT_URL. Used for copying artifacts in and out.
      GDAL   ignores it entirely. run_nd_scenarios loads the inflow line,
             centerline and outflow polygon with geopandas, which goes through
             pyogrio to GDAL, so those reads need GDAL's own variables. Without
             them GDAL resolves an s3:// path against real AWS and returns 403
             — an authentication error whose real cause is the endpoint.

    AWS_VIRTUAL_HOSTING=FALSE forces path-style addressing (MinIO does not serve
    bucket-as-subdomain), and AWS_HTTPS=NO allows the plain-http endpoint.

    Credentials are passed through from the environment when present, so the
    same code works against real S3 with a role and no keys.

    AWS_REQUEST_PAYER is deliberately NOT passed. It is only needed to read a
    requester-pays source bucket — the /vsis3 land-cover mosaic the job images
    default to — and sending it at an object store that does not implement
    request payment can fail the reads that matter here. A deployment whose
    dem_source or lulc_source lives in a requester-pays bucket should add it
    through `extra` for build_model.
    """
    endpoint = settings.aws_endpoint_url_for_jobs
    host = endpoint.split("://", 1)[-1]
    env = {
        "AWS_ENDPOINT_URL": endpoint,
        "AWS_S3_ENDPOINT": host,
        "AWS_VIRTUAL_HOSTING": "FALSE",
        "AWS_HTTPS": "NO" if endpoint.startswith("http://") else "YES",
    }
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                "AWS_REGION", "AWS_DEFAULT_REGION"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return {**env, **(extra or {})}


def reach_of(payload: dict) -> str:
    """The reach a payload is for, whichever job's payload it is.

    build_model takes a reach id outright; run_nd_scenarios only ever sees
    paths. Both are addresses the loop built, so the reach is recoverable
    either way — and it belongs in the container name, because "which reach is
    this?" is the first question anyone asks of a running job.
    """
    if payload.get("reach_id") is not None:
        return str(payload["reach_id"])
    path = payload.get("model_manifest_path", "")
    if "/reach=" in path:
        return path.split("/reach=")[1].split("/")[0]
    return "unknown"

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


class JobStatus(str, Enum):
    """What the execution system says about a job.

    QUEUED and RUNNING are both "alive, leave it alone"; they are distinguished
    so a viewer can say why nothing is happening. UNKNOWN is the interesting
    one: the job cannot be accounted for, which is not the same as failed. It
    happens when a container is reaped, or a batch system ages a finished job
    out of its history, and it is the only case the loop has to guess about.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED)


class ContainerRunner(Protocol):
    def run_build_model(self, payload: dict) -> BuildResult: ...

    def submit(self, job: str, payload: dict) -> str:
        """Start a job and return a reference to it, without waiting."""
        ...

    def poll(self, ref: str) -> JobStatus:
        """Ask what became of a job. Never records anything."""
        ...

    def reap(self, ref: str) -> None:
        """Discard a finished job's execution record."""
        ...


class LocalDockerRunner:
    """Runs build_model as a local Docker container via docker run."""

    def __init__(
        self,
        image: str | None = None,
        images: dict[str, str] | None = None,
        network: str = "twodfim",
        env_vars: dict[str, str] | None = None,
        timeout: int = 3600,
        platform: str | None = None,
        volumes: list[str] | None = None,
    ):
        self.image = image or settings.build_model_image
        # One image per job: the job name is baked into each image's ENTRYPOINT
        # (twod_fim_jobs <job>), so only the payload is passed at run time.
        # `images` overrides per step, for trying a candidate build of one job
        # without disturbing the rest.
        self.images = {
            "build_model": self.image,
            "run_nd_scenarios": settings.run_nd_scenarios_image,
            **(images or {}),
        }
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


    def _run_args(self, name: str) -> list[str]:
        """Flags shared by every way of starting a container."""
        args = ["--name", name, "--network", self.network]
        if self.platform:
            args.extend(["--platform", self.platform])
        for vol in self.volumes:
            args.extend(["-v", vol])
        for key, val in self.env_vars.items():
            args.extend(["--env", f"{key}={val}"])
        return args

    def submit(self, job: str, payload: dict) -> str:
        """Start a job detached and return its container id.

        Deliberately not --rm. A container that deletes itself on exit cannot be
        asked how it went, so every finished job would look UNKNOWN and the loop
        would fall back to guessing. reap() removes them once their outcome has
        been recorded.
        """
        image = self.images.get(job)
        if image is None:
            raise ValueError(f"no image configured for job {job!r}")

        name = f"{job.replace('_', '-')}-{reach_of(payload)}-{uuid.uuid4().hex[:8]}"
        cmd = ["docker", "run", "-d", *self._run_args(name), image,
               json.dumps(payload, separators=(",", ":"))]

        logger.info("Submitting %s for reach %s", job, reach_of(payload))
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"could not start {job} for reach "
                               f"{reach_of(payload)}: {proc.stderr[-2000:]}")
        return proc.stdout.strip()

    def poll(self, ref: str) -> JobStatus:
        """What became of a submitted job.

        Docker has no queue, so QUEUED never appears here — a batch backend
        would return it. A container we cannot find is UNKNOWN rather than
        failed: it may have been reaped after succeeding, and storage is what
        settles that question.
        """
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", ref],
            capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return JobStatus.UNKNOWN

        status, _, exit_code = proc.stdout.strip().partition(" ")
        if status in ("created", "running", "paused", "restarting"):
            return JobStatus.RUNNING
        if status in ("exited", "dead"):
            return JobStatus.SUCCEEDED if exit_code == "0" else JobStatus.FAILED
        return JobStatus.UNKNOWN

    def logs(self, ref: str, tail: int = 50) -> str:
        """Recent output from a job, for recording why a failure happened."""
        proc = subprocess.run(["docker", "logs", "--tail", str(tail), ref],
                              capture_output=True, text=True, check=False)
        return (proc.stderr or proc.stdout).strip()

    def reap(self, ref: str) -> None:
        """Remove a finished container. Never fails the caller."""
        subprocess.run(["docker", "rm", "-f", ref], capture_output=True, check=False)
