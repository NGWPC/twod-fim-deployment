"""What a check does when the gap is a KWSE step.

The step is run as a group: one SEPEX submission, one job per discharge chain
still missing scenarios, and one marker holding the group. The subject is that
decision and its bookkeeping — what is submitted, what is marked in flight, and
what happens when there turns out to be nothing to submit. Everything the check
touches outside itself is stubbed; the members' contents are tested in
test_kwse_payload.
"""

from types import SimpleNamespace

import pytest

from recon import check, gap
from recon.execution import JobStatus

REACH, REVISION = "100", 7


class RecordingExecution:
    """Records submissions without running anything."""

    def __init__(self, fail_with=None):
        self.jobs, self.groups = [], []
        self.fail_with = fail_with

    def submit(self, process_id, payload, tags=None):
        self.jobs.append((process_id, payload, tags))
        return "5a0e2f7c-job"

    def submit_group(self, process_id, members, tags=None):
        if self.fail_with:
            raise self.fail_with
        self.groups.append((process_id, members, tags))
        return "group:0d8f6c2e-4b1a-4f7e-9a53-2c6e1b8f4d10"

    def poll(self, ref):
        return JobStatus.UNKNOWN


@pytest.fixture
def wired(monkeypatch):
    """A reach whose gap is whatever `state.step` says, on a GPU host."""
    state = SimpleNamespace(step=gap.RUN_KWSE, members=[], marked=[], checks=[],
                            failures=[])

    for name in ("observe_reach", "observe_nd_runs", "observe_kwse_runs"):
        monkeypatch.setattr(check.observe, name, lambda r: {})
    for name in ("start_check", "clear_failures", "wait_on", "clear_step"):
        monkeypatch.setattr(check.processing, name, lambda *a, **kw: None)
    monkeypatch.setattr(check.processing, "mark_in_flight",
                        lambda r, step, ref, rev: state.marked.append((step, ref, rev)))
    monkeypatch.setattr(check.processing, "record_failure",
                        lambda r, err: state.failures.append(err) or
                        {"consecutive_failures": 1, "halted": True})
    monkeypatch.setattr(check.queue, "request_check", lambda r: state.checks.append(r))
    monkeypatch.setattr(check.activity, "begin", lambda *a, **kw: object())
    monkeypatch.setattr(check.activity, "end", lambda *a, **kw: None)
    monkeypatch.setattr(check, "load_snapshot",
                        lambda r: SimpleNamespace(revision=REVISION))
    monkeypatch.setattr(check.gap, "calculate", lambda s: gap.RunStep(step=state.step))
    monkeypatch.setattr(check.intent, "effective", lambda r: {"solver": "lisflood"})
    monkeypatch.setenv("GPU_AVAILABLE", "true")
    monkeypatch.setitem(check.GROUPS, gap.RUN_KWSE, lambda r: state.members)
    monkeypatch.setitem(check.PAYLOADS, gap.RUN_ND, lambda r: {"reach_id": r})
    return state


def members(*qs):
    return [{"inputs": {"scenarios": [{"upstream_discharge": q}]}, "tags": [f"q:{q}"]}
            for q in qs]


def test_a_kwse_gap_submits_one_group_and_marks_the_group_in_flight(wired):
    wired.members = members(200, 900)
    execution = RecordingExecution()

    result = check.run_check(REACH, execution)

    assert execution.jobs == []
    [(process, sent, tags)] = execution.groups
    assert process == "runKwseScenariosLisfloodGpu"
    assert sent == members(200, 900)
    assert tags == [f"reach:{REACH}"]
    # The marker holds the group, under the step's name, so the poll pass
    # follows the group and the gap calculation still sees one rung in flight.
    assert wired.marked == [(gap.RUN_KWSE, result.submitted_ref, REVISION)]
    assert result.submitted_ref.startswith("group:")
    assert wired.checks == [REACH]
    assert "2 runKwseScenariosLisfloodGpu jobs" in result.note


def test_cpu_hosts_submit_the_same_group_to_the_cpu_process(wired, monkeypatch):
    """Chains run in parallel on either hardware; SEPEX admits as many as its
    CPUs and memory hold."""
    monkeypatch.setenv("GPU_AVAILABLE", "false")
    wired.members = members(200, 900)
    execution = RecordingExecution()

    check.run_check(REACH, execution)

    [(process, sent, _)] = execution.groups
    assert process == "runKwseScenariosLisfloodCpu"
    assert len(sent) == 2


def test_nothing_missing_submits_nothing_and_marks_nothing(wired):
    """Every scenario is already in storage. An empty group is not a job to
    wait on: no marker, and the requested check adopts the library."""
    wired.members = []
    execution = RecordingExecution()

    result = check.run_check(REACH, execution)

    assert execution.groups == [] and execution.jobs == []
    assert wired.marked == []
    assert result.submitted_ref is None
    assert wired.checks == [REACH]
    assert wired.failures == []
    assert "nothing missing" in result.note


def test_a_single_job_step_is_still_one_job(wired):
    wired.step = gap.RUN_ND
    execution = RecordingExecution()

    result = check.run_check(REACH, execution)

    assert execution.groups == []
    assert [process for process, _, _ in execution.jobs] == ["runNdScenariosLisfloodGpu"]
    assert wired.marked == [(gap.RUN_ND, "5a0e2f7c-job", REVISION)]
    assert result.submitted_ref == "5a0e2f7c-job"


def test_a_refused_group_is_a_failure_and_nothing_is_marked(wired):
    """SEPEX validates the whole group before creating any job, so a refusal
    means nothing is running. Recording it is what backs the reach off instead
    of resubmitting the same refused request on every pass."""
    wired.members = members(200)
    execution = RecordingExecution(
        fail_with=RuntimeError("SEPEX POST /processes/x/group-execution -> 400: bad input"))

    result = check.run_check(REACH, execution)

    assert result.decision == "Failed"
    assert wired.marked == []
    assert wired.failures and "400" in wired.failures[0]
