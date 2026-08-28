"""Which image carries out a step, and the line between a step and its variant.

A normal-depth run is no longer one job: the solver picks the model and --gpu
picks the hardware, and only their product is a published image. These tests
pin both halves of that — the routing itself, and the rule that the routing
must not leak into the ladder, because the two are easy to conflate and the
database refuses the conflation only at write time.
"""

import re
from pathlib import Path

import pytest

from recon import gap
from recon.check import RUN_ND_JOBS, _job_key
from recon.workers import SEPEX_PROCESS_IDS, LocalDockerRunner

SCHEMA = Path(__file__).resolve().parents[2] / "db" / "schema"


# --- routing ------------------------------------------------------------

@pytest.mark.parametrize("gpu,expected", [
    (False, "run_nd_scenarios-lisflood-cpu"),
    (True, "run_nd_scenarios-lisflood-gpu"),
])
def test_the_hardware_flag_picks_the_variant(monkeypatch, gpu, expected):
    monkeypatch.setattr("recon.check.intent.effective", lambda _r: {"solver": "lisflood"})
    assert _job_key(gap.RUN_ND, 1, gpu=gpu) == expected


def test_build_model_has_one_image_and_never_reads_intent(monkeypatch):
    """A step that does not vary must not pay for a query to discover that."""
    def fail(_r):
        raise AssertionError("build_model routing must not read intent")

    monkeypatch.setattr("recon.check.intent.effective", fail)
    assert _job_key(gap.BUILD_MODEL, 1, gpu=True) == gap.BUILD_MODEL


def test_an_unbuilt_solver_is_refused_where_the_reason_is_legible(monkeypatch):
    """sfincs is a valid thing to ask for (the schema allows it) and has no
    image. Refusing here names the solver; letting it through surfaces as an
    image pull failure with nothing pointing at the cause."""
    monkeypatch.setattr("recon.check.intent.effective", lambda _r: {"solver": "sfincs"})
    with pytest.raises(RuntimeError, match="sfincs"):
        _job_key(gap.RUN_ND, 1, gpu=False)


# --- the step / variant line -------------------------------------------

def test_every_variant_is_runnable_by_both_runners():
    """A routing key that no runner can turn into an image or a process is a
    submission that fails at the last moment, for a reason visible nowhere in
    this repo."""
    for key in RUN_ND_JOBS.values():
        assert key in LocalDockerRunner().images, f"{key} has no docker image"
        assert key in SEPEX_PROCESS_IDS, f"{key} has no SEPEX process"


def test_variants_are_not_step_names():
    """reach_processing.current_step is CHECK-constrained to the three step
    names, so writing a variant there fails the insert. This is the test that
    catches the conflation before the database does — the marker records the
    rung of the ladder, and the variant lives in the activity log instead."""
    allowed = set(
        re.findall(r"'([a-z_]+)'",
                   re.search(r"current_step text CONSTRAINT.*?\)\),",
                             (SCHEMA / "07_reach_processing.sql").read_text(),
                             re.S).group(0))
    )
    assert gap.BUILD_MODEL in allowed and gap.RUN_ND in allowed
    for key in RUN_ND_JOBS.values():
        assert key not in allowed, (
            f"{key} is a runner variant, not a step; it must never reach "
            "reach_processing.current_step")
