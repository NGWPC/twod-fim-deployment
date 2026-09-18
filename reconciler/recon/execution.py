"""Execution backend.

The seam between deciding work and running it: submits jobs and groups to
SEPEX, polls them, and fetches logs.
"""

import json
import logging
import urllib.error
import urllib.request

from enum import Enum
from typing import Protocol

logger = logging.getLogger(__name__)


class SepexUnavailable(RuntimeError):
    """SEPEX could not be reached at all."""


def reach_of(payload: dict) -> str:
    """The reach a payload is for, whichever job's payload it is."""
    if payload.get("reach_id") is not None:
        return str(payload["reach_id"])
    path = payload.get("model_manifest_path", "")
    if "/reach=" in path:
        return path.split("/reach=")[1].split("/")[0]
    return "unknown"


class JobStatus(str, Enum):
    """What the execution system says about a job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED)


GROUP_REF_PREFIX = "group:"

GROUP_PAGE_LIMIT = 100


def group_id(ref: str) -> str | None:
    """The SEPEX groupID a reference names, or None for a single job."""
    return ref[len(GROUP_REF_PREFIX):] if ref.startswith(GROUP_REF_PREFIX) else None


def member_tags(member: dict) -> list[str]:
    """A group member's own tags, without the `group:` tag every member shares."""
    return [t for t in member.get("tags") or [] if not t.startswith(GROUP_REF_PREFIX)]


class ExecutionService(Protocol):
    """The seam between deciding work and running it."""

    def submit(self, process_id: str, payload: dict, tags: list[str] | None = None) -> str:
        """Start a job and return a reference to it, without waiting."""
        ...

    def submit_group(
        self, process_id: str, members: list[dict], tags: list[str] | None = None
    ) -> str:
        """Start one job per member as a single group, and return a reference."""
        ...

    def poll(self, ref: str) -> JobStatus:
        """Ask what became of a job or a group. Never records anything."""
        ...


SEPEX_STATUS_MAP = {
    "accepted": JobStatus.QUEUED,
    "running": JobStatus.RUNNING,
    "successful": JobStatus.SUCCEEDED,
    "failed": JobStatus.FAILED,
    "dismissed": JobStatus.FAILED,
}


class SepexClient:
    """Submits jobs to SEPEX and reads back their status."""

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
        """The response body, or None when SEPEX answers 404."""
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

    def submit(self, process_id: str, payload: dict, tags: list[str] | None = None) -> str:
        """Ask SEPEX to run one job."""
        logger.info("submitting %s for reach %s", process_id, reach_of(payload))
        body: dict = {"inputs": payload}
        if tags:
            body["tags"] = tags
        result = self._request(
            "POST",
            f"/processes/{process_id}/execution",
            data=body,
            headers={"Prefer": "respond-async"},
        )
        if result is None:
            raise RuntimeError(
                f"SEPEX has no process {process_id!r}. "
                f"GET {self.base_url}/processes lists the ones it does have."
            )
        return result["jobID"]

    def submit_group(
        self, process_id: str, members: list[dict], tags: list[str] | None = None
    ) -> str:
        """Start one job per member as a single group, and return a reference."""
        logger.info("submitting %d %s jobs as a group for reach %s", len(members),
                    process_id, reach_of(members[0]["inputs"]) if members else "unknown")
        body: dict = {"jobs": members}
        if tags:
            body["tags"] = tags
        result = self._request(
            "POST", f"/processes/{process_id}/group-execution", data=body)
        if result is None:
            raise RuntimeError(
                f"SEPEX has no process {process_id!r}. "
                f"GET {self.base_url}/processes lists the ones it does have."
            )
        return f"{GROUP_REF_PREFIX}{result['groupID']}"

    def poll(self, ref: str) -> JobStatus:
        """What SEPEX says about a job, or about a group as one unit."""
        group = group_id(ref)
        path = f"/job-groups/{group}?limit=1" if group else f"/jobs/{ref}"
        try:
            result = self._request("GET", path)
        except SepexUnavailable as exc:
            logger.warning("could not poll %s: %s", ref, exc)
            return JobStatus.UNKNOWN
        if not isinstance(result, dict):
            return JobStatus.UNKNOWN
        return SEPEX_STATUS_MAP.get(result.get("status", ""), JobStatus.UNKNOWN)

    def logs(self, ref: str, tail: int = 50) -> str:
        """Recent output from a job or group."""
        group = group_id(ref)
        return self._group_logs(group, tail) if group else self._job_logs(ref, tail)

    def _group_logs(self, group: str, tail: int) -> str:
        """The logs of a group's failed members, then the group's summary."""
        try:
            result = self._request(
                "GET", f"/job-groups/{group}?status=failed&limit={GROUP_PAGE_LIMIT}")
        except (SepexUnavailable, RuntimeError) as exc:
            return f"(could not read group {group}: {exc})"
        if not isinstance(result, dict):
            return f"(SEPEX has no group {group})"

        sections = [
            f"--- job {m['jobID']} ({', '.join(member_tags(m))}) ---\n"
            + self._job_logs(m["jobID"], tail)
            for m in result.get("jobs") or [] if m.get("jobID")
        ]
        counts = ", ".join(f"{n} {status}"
                           for status, n in (result.get("summary") or {}).items() if n)
        summary = (f"group {group} {result.get('status')}: {counts} "
                   f"of {result.get('requested')} requested")
        if result.get("message"):
            summary += f" ({result['message']})"
        return "\n".join([*sections, summary])

    def _job_logs(self, ref: str, tail: int) -> str:
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
