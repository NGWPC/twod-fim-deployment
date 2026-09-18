"""Tests for job routing."""

import re
from pathlib import Path

import pytest

from recon import gap
from recon.check import (BUILD_MODEL_PROCESS, RUN_KWSE_PROCESSES,
                         RUN_ND_PROCESSES, _process_id, gpu_available)

ALL_PROCESSES = frozenset(
    {BUILD_MODEL_PROCESS, *RUN_ND_PROCESSES.values(), *RUN_KWSE_PROCESSES.values()})

DEPLOYMENT = Path(__file__).resolve().parents[2]
SCHEMA = DEPLOYMENT / "db" / "schema"
PLUGINS = DEPLOYMENT / "sepex" / "local" / "plugins"


def plugin_process_ids() -> set[str]:
    ids = set()
    for yml in PLUGINS.glob("*/*.yml"):
        found = re.search(r"^\s*id:\s*(\S+)", yml.read_text(), re.M)
        if found:
            ids.add(found.group(1))
    return ids


@pytest.mark.parametrize("step,env_value,expected", [
    (gap.RUN_ND, "false", "runNdScenariosLisfloodCpu"),
    (gap.RUN_ND, "true", "runNdScenariosLisfloodGpu"),
    (gap.RUN_KWSE, "false", "runKwseScenariosLisfloodCpu"),
    (gap.RUN_KWSE, "true", "runKwseScenariosLisfloodGpu"),
])
def test_gpu_available_picks_the_process(monkeypatch, step, env_value, expected):
    monkeypatch.setattr("recon.check.intent.effective", lambda _r: {"solver": "lisflood"})
    monkeypatch.setenv("GPU_AVAILABLE", env_value)
    assert _process_id(step, 1) == expected


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("False", False), ("0", False), ("no", False), ("", False),
])
def test_gpu_available_parses_the_variable_rather_than_its_truthiness(
    monkeypatch, value, expected
):
    monkeypatch.setenv("GPU_AVAILABLE", value)
    assert gpu_available() is expected


def test_gpu_is_off_when_the_variable_is_absent(monkeypatch):
    monkeypatch.delenv("GPU_AVAILABLE", raising=False)
    assert gpu_available() is False


def test_build_model_has_one_process_and_never_reads_intent(monkeypatch):
    def fail(_r):
        raise AssertionError("build_model routing must not read intent")

    monkeypatch.setattr("recon.check.intent.effective", fail)
    assert _process_id(gap.BUILD_MODEL, 1) == BUILD_MODEL_PROCESS


def test_an_unbuilt_solver_is_refused_where_the_reason_is_legible(monkeypatch):
    monkeypatch.setattr("recon.check.intent.effective", lambda _r: {"solver": "sfincs"})
    monkeypatch.setenv("GPU_AVAILABLE", "false")
    with pytest.raises(RuntimeError, match="sfincs"):
        _process_id(gap.RUN_ND, 1)


def test_every_process_the_loop_can_name_exists_in_the_local_definitions():
    registered = plugin_process_ids()
    if not registered:
        pytest.skip(f"no local process definitions under {PLUGINS}")
    for pid in ALL_PROCESSES:
        assert pid in registered, (
            f"{pid} is not the info.id of any local process definition in {PLUGINS}; "
            f"found: {sorted(registered)}")


def test_processes_are_not_step_names():
    allowed = set(
        re.findall(r"'([a-z_]+)'",
                   re.search(r"current_step text CONSTRAINT.*?\)\),",
                             (SCHEMA / "07_reach_processing.sql").read_text(),
                             re.S).group(0))
    )
    assert {gap.BUILD_MODEL, gap.RUN_ND, gap.RUN_KWSE} <= allowed
    for pid in ALL_PROCESSES:
        assert pid not in allowed, (
            f"{pid} is a SEPEX process, not a step; it must never reach "
            "reach_processing.current_step")
