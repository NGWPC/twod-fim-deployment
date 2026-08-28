"""The execution layer: SEPEX, and nothing else.

The loop does not run jobs. It asks SEPEX to run one and later asks what became
of it. Everything about HOW a job runs — which image, on what hardware, with
which environment and mounts — is declared in that job's SEPEX plugin, not
here. The loop names a process and hands over a payload.

WHEN a job runs belongs to SEPEX too, and that is the part worth being explicit
about: SEPEX keeps a queue and admits work against its own resource pool
(ResourcePool/QueueWorker, sized from the plugin's maxResources). So there is
no concurrency limit in this file, and none above it. A second limit in the
loop could only hold back work SEPEX had room for, while doing nothing to
protect it from work it did not — the loop cannot see the pool, and SEPEX
cannot see the loop's opinion of it. One scheduler, and it is the one holding
the resources.

What the loop keeps instead is a per-reach in-flight marker, which is a
different thing: not "how much work may run" but "this reach already has a job,
do not ask for a second one". Idempotency, not scheduling.
"""

import json
import logging
import urllib.error
import urllib.request

from enum import Enum
from typing import Protocol

logger = logging.getLogger(__name__)


class SepexUnavailable(RuntimeError):
    """SEPEX could not be reached at all.

    Distinct from SEPEX answering with a refusal, because the two call for
    opposite responses: a refusal is about this job and will happen again, an
    outage is about the deployment and will not.
    """


def reach_of(payload: dict) -> str:
    """The reach a payload is for, whichever job's payload it is.

    build_model takes a reach id outright; run_nd_scenarios only ever sees
    paths. Both are addresses the loop built, so the reach is recoverable
    either way — and it belongs in every log line, because "which reach is
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
    so a viewer can say why nothing is happening. QUEUED is the ordinary state
    under SEPEX rather than an exceptional one — it is what admission control
    looks like from outside, and it is not a problem to be solved by submitting
    less.

    UNKNOWN is the interesting one: the job cannot be accounted for, which is
    not the same as failed. It happens when SEPEX cannot be reached, or when a
    job has aged out of its history, and it is the only case the loop has to
    guess about.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED)


class ExecutionService(Protocol):
    """The seam between deciding work and running it.

    One implementation, SepexClient. It stays a protocol so a test can stand in
    something that records submissions without running anything — the loop is
    worth testing without an execution system attached.
    """

    def submit(self, process_id: str, payload: dict) -> str:
        """Start a job and return a reference to it, without waiting."""
        ...

    def poll(self, ref: str) -> JobStatus:
        """Ask what became of a job. Never records anything."""
        ...


SEPEX_STATUS_MAP = {
    "accepted": JobStatus.QUEUED,
    "running": JobStatus.RUNNING,
    "successful": JobStatus.SUCCEEDED,
    "failed": JobStatus.FAILED,
    "dismissed": JobStatus.FAILED,
}


class SepexClient:
    """Submits jobs to SEPEX and reads back their status.

    Deliberately thin. It knows how to speak the API and nothing about what the
    jobs mean, which reach they belong to, or whether their output is any good
    — storage answers that last question, and observe is what asks it.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> dict | list | None:
        """The response body, or None when SEPEX answers 404.

        Raises SepexUnavailable when SEPEX cannot be reached. The two are kept
        apart on purpose: 404 is an answer ("no such job", "no such process")
        and a connection failure is the absence of one.
        """
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
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"SEPEX {method} {path} -> {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise SepexUnavailable(f"SEPEX unreachable at {self.base_url}: {e}") from e

    def submit(self, process_id: str, payload: dict) -> str:
        """Ask SEPEX to run one job. Returns its jobID.

        Returning means SEPEX accepted the job, not that it started it: with a
        busy resource pool the job sits queued, which is the normal case and
        not something the caller should try to avoid.
        """
        logger.info("submitting %s for reach %s", process_id, reach_of(payload))
        result = self._request(
            "POST",
            f"/processes/{process_id}/execution",
            data={"inputs": payload},
            headers={"Prefer": "respond-async"},
        )
        if result is None:
            raise RuntimeError(
                f"SEPEX has no process {process_id!r}. "
                f"GET {self.base_url}/processes lists the ones it does have."
            )
        return result["jobID"]

    def poll(self, ref: str) -> JobStatus:
        """What SEPEX says about a job.

        UNKNOWN covers both "SEPEX cannot be reached" and "SEPEX has never
        heard of this job". Neither is evidence of failure, and the loop treats
        them the same way: wait, then look at storage.
        """
        try:
            result = self._request("GET", f"/jobs/{ref}")
        except SepexUnavailable as exc:
            logger.warning("could not poll job %s: %s", ref, exc)
            return JobStatus.UNKNOWN
        if not isinstance(result, dict):
            return JobStatus.UNKNOWN
        return SEPEX_STATUS_MAP.get(result.get("status", ""), JobStatus.UNKNOWN)

    def logs(self, ref: str, tail: int = 50) -> str:
        """Recent output from a job, for recording why a failure happened.

        Best effort: a failure is worth recording even when its logs are not
        available, so this never raises.
        """
        try:
            result = self._request("GET", f"/jobs/{ref}/logs")
        except (SepexUnavailable, RuntimeError) as exc:
            return f"(could not fetch logs: {exc})"
        if result is None:
            return ""
        entries = result.get("process_logs", result) if isinstance(result, dict) else result
        if isinstance(entries, list):
            return "\n".join(
                e.get("msg", str(e)) if isinstance(e, dict) else str(e)
                for e in entries[-tail:]
            )
        return str(entries)
