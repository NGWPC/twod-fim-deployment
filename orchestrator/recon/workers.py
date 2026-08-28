"""Container runners for the job images.

The loop calls these through the ContainerRunner protocol, so swapping the
runner -- local Docker now, SEPEX for cloud -- changes nothing above it.
"""

import json
import logging
import os
import subprocess
import urllib.request
import uuid

from enum import Enum
from typing import Protocol

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
    endpoint = settings.effective_aws_endpoint_url_for_jobs
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
    """Runs any job as a detached local Docker container.

    Job-agnostic: which image to start is looked up by step name, and the
    payload is passed through untouched. The runner never inspects what a job
    produced — that is observe's business, and reading a job's output here is
    what the level-triggered design deliberately gave up.
    """

    def __init__(
        self,
        image: str | None = None,
        images: dict[str, str] | None = None,
        network: str = "twodfim",
        env_vars: dict[str, str] | None = None,
        platform: str | None = None,
        volumes: list[str] | None = None,
        gpus: str | None = None,
    ):
        self.image = image or settings.build_model_image
        # One image per job (job-only for build_model; job+solver+hardware for
        # run_nd_scenarios): the job name is baked into each image's ENTRYPOINT
        # (twod_fim_jobs <job>), so only the payload is passed at run time.
        # `images` overrides per key, for trying a candidate build of one job
        # without disturbing the rest.
        self.images = {
            "build_model": self.image,
            "run_nd_scenarios-lisflood-cpu": settings.run_nd_scenarios_lisflood_cpu_image,
            "run_nd_scenarios-lisflood-gpu": settings.run_nd_scenarios_lisflood_gpu_image,
            **(images or {}),
        }
        self.network = network
        self.env_vars = env_vars or {}
        self.platform = platform
        self.volumes = volumes or []
        # Without this the container cannot see the device, so a CUDA-enabled
        # job fails at solver start rather than falling back to CPU.
        self.gpus = gpus if gpus is not None else settings.docker_gpus

    def _run_args(self, name: str) -> list[str]:
        """Flags shared by every way of starting a container."""
        args = ["--name", name, "--network", self.network]
        if self.gpus:
            args.extend(["--gpus", self.gpus])
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


SEPEX_PROCESS_IDS = {
    "build_model": "buildModel",
    "run_nd_scenarios-lisflood-cpu": "runNdScenariosLisfloodCpu",
    "run_nd_scenarios-lisflood-gpu": "runNdScenariosLisfloodGpu",
}

SEPEX_STATUS_MAP = {
    "accepted": JobStatus.QUEUED,
    "running": JobStatus.RUNNING,
    "successful": JobStatus.SUCCEEDED,
    "failed": JobStatus.FAILED,
    "dismissed": JobStatus.FAILED,
}


class SepexRunner:
    """Runs jobs through the SEPEX API instead of local Docker.

    SEPEX manages container execution (Docker for build_model, AWS Batch for
    nd_scenarios) and tracks job status. The Lambda callback updates SEPEX
    when Batch jobs complete, so poll() only reads SEPEX's view of the job.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str,
                 data: dict | None = None,
                 headers: dict[str, str] | None = None,
                 timeout: int = 30) -> dict | None:
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode() if data is not None else None
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        except (urllib.error.URLError, TimeoutError):
            return None

    def submit(self, job: str, payload: dict) -> str:
        process_id = SEPEX_PROCESS_IDS.get(job)
        if process_id is None:
            raise ValueError(f"no SEPEX process configured for job {job!r}")

        logger.info("Submitting %s to SEPEX as %s for reach %s",
                     job, process_id, reach_of(payload))
        result = self._request(
            "POST", f"/processes/{process_id}/execution",
            data={"inputs": payload},
            headers={"Prefer": "respond-async"},
        )
        if result is None:
            raise RuntimeError(f"SEPEX unreachable submitting {job} for reach {reach_of(payload)}")
        return result["jobID"]

    def poll(self, ref: str) -> JobStatus:
        result = self._request("GET", f"/jobs/{ref}")
        if result is None:
            return JobStatus.UNKNOWN
        return SEPEX_STATUS_MAP.get(result.get("status", ""), JobStatus.UNKNOWN)

    def logs(self, ref: str, tail: int = 50) -> str:
        result = self._request("GET", f"/jobs/{ref}/logs")
        if result is None:
            return ""
        if isinstance(result, list):
            return "\n".join(str(entry) for entry in result[-tail:])
        return str(result)

    def reap(self, ref: str) -> None:
        try:
            self._request("DELETE", f"/jobs/{ref}")
        except Exception:
            pass
