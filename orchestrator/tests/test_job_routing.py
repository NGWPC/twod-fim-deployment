"""Which SEPEX process carries out a step, and the line between the two.

A normal-depth run is not one job: the solver picks the model and
$GPU_AVAILABLE picks the hardware, and only their product is a registered SEPEX process. These tests
pin three things that are easy to get wrong and expensive to discover at
runtime:

  - the routing itself
  - that every process the loop can name is actually registered as a plugin,
    so a submission cannot 404 against SEPEX
  - that the routing never leaks into the ladder, because reach_processing
    refuses that only at write time
"""

import re
from pathlib import Path

import pytest

from recon import gap
from recon.check import (BUILD_MODEL_PROCESS, RUN_KWSE_PROCESSES,
                         RUN_ND_PROCESSES, _process_id, gpu_available)

# Every process id the loop is capable of naming.
ALL_PROCESSES = frozenset(
    {BUILD_MODEL_PROCESS, *RUN_ND_PROCESSES.values(), *RUN_KWSE_PROCESSES.values()})

DEPLOYMENT = Path(__file__).resolve().parents[2]
SCHEMA = DEPLOYMENT / "db" / "schema"
PLUGINS = DEPLOYMENT / "sepex" / "local" / "plugins"


def plugin_process_ids() -> set[str]:
    """Every `info.id` SEPEX would register from the local plugin directory."""
    ids = set()
    for yml in PLUGINS.glob("*/*.yml"):
        found = re.search(r"^\s*id:\s*(\S+)", yml.read_text(), re.M)
        if found:
            ids.add(found.group(1))
    return ids


# --- routing ------------------------------------------------------------

@pytest.mark.parametrize("step,env_value,expected", [
    (gap.RUN_ND, "false", "runNdScenariosLisfloodCpu"),
    (gap.RUN_ND, "true", "runNdScenariosLisfloodGpu"),
    (gap.RUN_KWSE, "false", "runKwseScenariosLisfloodCpu"),
    (gap.RUN_KWSE, "true", "runKwseScenariosLisfloodGpu"),
])
def test_gpu_available_picks_the_process(monkeypatch, step, env_value, expected):
    """Which hardware variant is asked for comes from the environment, not from
    an argument: the host either has a device or it does not."""
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
    """bool() on a string is true for every non-empty value, so GPU_AVAILABLE
    =false would select the GPU process. That mistake shipped in the job images
    once already, where it made a CPU build ask for CUDA."""
    monkeypatch.setenv("GPU_AVAILABLE", value)
    assert gpu_available() is expected


def test_gpu_is_off_when_the_variable_is_absent(monkeypatch):
    """A host with no GPU must work without being told anything."""
    monkeypatch.delenv("GPU_AVAILABLE", raising=False)
    assert gpu_available() is False


def test_build_model_has_one_process_and_never_reads_intent(monkeypatch):
    """A step that does not vary must not pay for a query to discover that."""
    def fail(_r):
        raise AssertionError("build_model routing must not read intent")

    monkeypatch.setattr("recon.check.intent.effective", fail)
    assert _process_id(gap.BUILD_MODEL, 1) == BUILD_MODEL_PROCESS


def test_an_unbuilt_solver_is_refused_where_the_reason_is_legible(monkeypatch):
    """sfincs is a valid thing to ask for (the schema allows it) and has no
    process. Refusing here names the solver; letting it through reaches SEPEX
    as a 404 that names only the process id."""
    monkeypatch.setattr("recon.check.intent.effective", lambda _r: {"solver": "sfincs"})
    monkeypatch.setenv("GPU_AVAILABLE", "false")
    with pytest.raises(RuntimeError, match="sfincs"):
        _process_id(gap.RUN_ND, 1)


# --- the loop can only name processes that exist ------------------------

def test_every_process_the_loop_can_name_exists_in_the_local_definitions():
    """The loop's whole vocabulary for execution is SEPEX process ids. One it
    can produce but SEPEX does not have is a submission that fails at the last
    possible moment, for a reason visible nowhere in this repo.

    Scoped to the LOCAL process definitions on purpose. SEPEX can be pointed at
    definitions anywhere (PLUGINS_LOAD_DIR), and a deployment that does so is
    not misconfigured — so this skips rather than fails when the local set is
    absent. Where it does apply, GET /processes is the only authority at
    runtime; this is the cheap version of that question, asked without a
    running SEPEX.
    """
    registered = plugin_process_ids()
    if not registered:
        pytest.skip(f"no local process definitions under {PLUGINS}")
    for pid in ALL_PROCESSES:
        assert pid in registered, (
            f"{pid} is not the info.id of any local process definition in {PLUGINS}; "
            f"found: {sorted(registered)}")


# --- the step / process line -------------------------------------------

def test_processes_are_not_step_names():
    """reach_processing.current_step is CHECK-constrained to the three step
    names, so writing a process id there fails the insert. This is the test
    that catches the conflation before the database does — the marker records
    the rung of the ladder, and the process is recorded in the activity log."""
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
