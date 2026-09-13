"""A step run as a SEPEX group, from the loop's side.

What SEPEX does with a group is SEPEX's to test. What is tested here is what the
loop does with one: which request it sends, how it reads a group back as one
unit, what it records when a group fails, and that the poll pass acts on a
group reference exactly as it acts on a job. SEPEX's HTTP API is stubbed at
the one method that speaks it.
"""

import pytest

from recon import jobs
from recon.execution import JobStatus, SepexClient, SepexUnavailable

GROUP = "0d8f6c2e-4b1a-4f7e-9a53-2c6e1b8f4d10"
REF = f"group:{GROUP}"
PROCESS = "runKwseScenariosLisfloodGpu"


class FakeSepex:
    """Answers requests by (method, path) and records every one."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, method, path, data=None, headers=None, timeout=30):
        self.calls.append((method, path, data))
        answer = self.answers.get((method, path))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def paths(self):
        return [path for _, path, _ in self.calls]


@pytest.fixture
def client(monkeypatch):
    c = SepexClient("http://sepex:5050")
    c.fake = FakeSepex()
    monkeypatch.setattr(c, "_request", c.fake)
    return c


def members(*qs):
    return [{"inputs": {"model_manifest_path": "s3://b/models/reach=100/m/model_manifest.json",
                        "scenarios": []},
             "tags": [f"q:{q}"]} for q in qs]


def group_doc(status, summary=None, jobs_=(), requested=3, message=""):
    return {"groupID": GROUP, "status": status, "requested": requested, "message": message,
            "summary": {"accepted": 0, "running": 0, "successful": 0, "failed": 0,
                        "dismissed": 0, "lost": 0, "notCreated": 0, **(summary or {})},
            "jobs": list(jobs_)}


def failed_member(job_id, q):
    return {"position": 0, "jobID": job_id, "processID": PROCESS, "status": "failed",
            "tags": [f"group:{GROUP}", "reach:100", f"q:{q}"]}


# --- submitting --------------------------------------------------------------

def test_a_group_is_one_request_carrying_every_member_and_the_reach_tag(client):
    client.fake.answers[("POST", f"/processes/{PROCESS}/group-execution")] = {
        "groupID": GROUP, "status": "accepted"}

    ref = client.submit_group(PROCESS, members(200, 900), tags=["reach:100"])

    assert ref == REF
    [(method, path, body)] = client.fake.calls
    assert body == {"jobs": members(200, 900), "tags": ["reach:100"]}


def test_a_process_sepex_does_not_have_is_refused_by_name(client):
    with pytest.raises(RuntimeError, match=PROCESS):
        client.submit_group(PROCESS, members(200))


# --- polling: the group is one unit ------------------------------------------

@pytest.mark.parametrize("sepex_status, expected", [
    ("accepted", JobStatus.QUEUED),
    ("running", JobStatus.RUNNING),
    ("successful", JobStatus.SUCCEEDED),
    ("failed", JobStatus.FAILED),
    # SEPEX's restart recovery dismisses queued members on its own, so a
    # dismissed group is not only ever one a person cancelled. Either way the
    # work did not land, and the retry runs what is missing.
    ("dismissed", JobStatus.FAILED),
])
def test_a_group_polls_as_its_combined_status(client, sepex_status, expected):
    client.fake.answers[("GET", f"/job-groups/{GROUP}?limit=1")] = group_doc(sepex_status)
    assert client.poll(REF) is expected


def test_a_bare_job_id_still_polls_as_a_job(client):
    """Build and nd steps are single jobs, and a marker written before groups
    existed must keep resolving after the upgrade."""
    client.fake.answers[("GET", "/jobs/5a0e2f7c")] = {"status": "running"}
    assert client.poll("5a0e2f7c") is JobStatus.RUNNING
    assert client.fake.paths() == ["/jobs/5a0e2f7c"]


def test_a_group_sepex_cannot_find_or_reach_is_unknown_not_failed(client):
    assert client.poll(REF) is JobStatus.UNKNOWN                      # 404
    client.fake.answers[("GET", f"/job-groups/{GROUP}?limit=1")] = SepexUnavailable("down")
    assert client.poll(REF) is JobStatus.UNKNOWN


# --- failure logs: the failed members, then the group ------------------------

def test_a_failed_group_records_the_logs_of_its_failed_members(client):
    client.fake.answers.update({
        ("GET", f"/job-groups/{GROUP}?status=failed&limit=100"): group_doc(
            "failed", {"successful": 1, "failed": 2},
            [failed_member("job-a", 200), failed_member("job-b", 900)]),
        ("GET", "/jobs/job-a/logs"): {"process_logs": [{"msg": "q=200 diverged"}]},
        ("GET", "/jobs/job-b/logs"): {"process_logs": [{"msg": "q=900 out of memory"}]},
    })

    detail = client.logs(REF)

    assert client.fake.paths() == [f"/job-groups/{GROUP}?status=failed&limit=100",
                                   "/jobs/job-a/logs", "/jobs/job-b/logs"]
    assert "q=200 diverged" in detail and "q=900 out of memory" in detail
    # Which chain each tail came from, without repeating the group on every job.
    assert "job job-a (reach:100, q:200)" in detail
    # Last, because a recorded failure keeps the end of what it is given.
    assert detail.splitlines()[-1] == f"group {GROUP} failed: 1 successful, 2 failed of 3 requested"


def test_a_group_with_no_failed_members_still_says_what_became_of_it(client):
    """Dismissed, lost and never-created members have no logs to fetch; the
    summary is what accounts for them."""
    client.fake.answers[("GET", f"/job-groups/{GROUP}?status=failed&limit=100")] = group_doc(
        "failed", {"successful": 2, "notCreated": 1}, message="submission stopped")

    detail = client.logs(REF)

    assert client.fake.paths() == [f"/job-groups/{GROUP}?status=failed&limit=100"]
    assert detail == (f"group {GROUP} failed: 2 successful, 1 notCreated of 3 requested"
                      " (submission stopped)")


def test_failure_logs_never_raise(client):
    """A failure is worth recording even when nothing more can be said about it."""
    client.fake.answers[("GET", f"/job-groups/{GROUP}?status=failed&limit=100")] = \
        SepexUnavailable("down")
    assert "could not read group" in client.logs(REF)


# --- the poll pass acts on a group like any job -------------------------------

@pytest.fixture
def in_flight(monkeypatch):
    """One reach whose marker holds a group, as the database would hand it back
    to a reconciler that has just restarted."""
    from datetime import timedelta
    recorded = {"failures": [], "cleared": [], "checks": []}
    monkeypatch.setattr(jobs.processing, "in_flight", lambda **kw: [{
        "reach_id": 100, "current_step": "run_kwse_scenarios", "current_step_ref": REF,
        "elapsed": timedelta(minutes=5)}])
    monkeypatch.setattr(jobs.processing, "record_failure",
                        lambda r, detail, **kw: recorded["failures"].append(detail) or
                        {"consecutive_failures": 1, "halted": True, "next_retry_at": None})
    monkeypatch.setattr(jobs.processing, "clear_step",
                        lambda r, ref=None, **kw: recorded["cleared"].append(ref))
    monkeypatch.setattr(jobs.queue, "request_check",
                        lambda r, **kw: recorded["checks"].append(r))
    return recorded


def test_a_failed_group_is_recorded_with_its_members_logs_and_halts(client, in_flight):
    client.fake.answers.update({
        ("GET", f"/job-groups/{GROUP}?limit=1"): group_doc("failed"),
        ("GET", f"/job-groups/{GROUP}?status=failed&limit=100"): group_doc(
            "failed", {"failed": 1}, [failed_member("job-a", 200)]),
        ("GET", "/jobs/job-a/logs"): {"process_logs": [{"msg": "q=200 diverged"}]},
    })

    [outcome] = jobs.poll_in_flight(client)

    assert outcome["action"] == "failed (1x), halted"
    [detail] = in_flight["failures"]
    assert "q=200 diverged" in detail
    assert in_flight["checks"] == [100]
    # Nothing is submitted from here: a retry is the check's to make, and it
    # submits only what storage is still missing.
    assert not any(method == "POST" for method, _, _ in client.fake.calls)


def test_a_finished_group_clears_its_own_marker_and_asks_for_a_check(client, in_flight):
    client.fake.answers[("GET", f"/job-groups/{GROUP}?limit=1")] = group_doc("successful")

    jobs.poll_in_flight(client)

    assert in_flight["cleared"] == [REF]
    assert in_flight["checks"] == [100]
    assert in_flight["failures"] == []
