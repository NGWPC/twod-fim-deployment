"""The folder-naming mirror, checked against the jobs repo's own functions.

identity.py copies the job's rendering rules so the loop can name a folder
without launching a container. A copy can drift, so where the real thing is
importable these tests compare against it directly rather than against a
hand-written expectation; where it is not, they fall back to the literals the
job's own tests pin.
"""

import pytest

from recon import identity, storage

# The jobs repo is a sibling checkout, not a dependency. When it is importable,
# every case below is checked against the real renderer as well.
try:
    from twod_fim_jobs.utils.naming import format_downstream_string
except ImportError:  # pragma: no cover - depends on the developer's layout
    format_downstream_string = None

needs_jobs = pytest.mark.skipif(
    format_downstream_string is None, reason="twod-fim-jobs not importable")


# --- kwse folders --------------------------------------------------------

@pytest.mark.parametrize("z, expected", [
    (226.0, "kwse=226.0"),
    (224.25, "kwse=224.2"),   # a 0.25 grid is rendered lossily, but consistently
    (224.75, "kwse=224.8"),
    (0.0, "kwse=0.0"),
])
def test_kwse_folder(z, expected):
    assert identity.kwse_folder(z) == expected


@needs_jobs
@pytest.mark.parametrize("z", [226.0, 224.25, 224.75, 585.5, 1203.05])
def test_kwse_folder_matches_the_job(z):
    assert identity.kwse_folder(z) == format_downstream_string(kwse_value=z, nd_value=None)


def test_a_quarter_metre_grid_still_yields_distinct_folders():
    """Rendering at one decimal is lossy; it must not be ambiguous."""
    stages = [224.0, 224.25, 224.5, 224.75, 225.0]
    assert len({identity.kwse_folder(z) for z in stages}) == len(stages)


# --- nd folders ----------------------------------------------------------

@pytest.mark.parametrize("slope, expected", [
    (12000.0, "nd=1.2E04"),
    (50.0, "nd=5.0E01"),
    (9.9, "nd=9.9E00"),
])
def test_nd_folder(slope, expected):
    assert identity.nd_folder(slope) == expected


@needs_jobs
@pytest.mark.parametrize("slope", [12000.0, 50.0, 9.9, 3700.0, 0.00012])
def test_nd_folder_matches_the_job(slope):
    assert identity.nd_folder(slope) == format_downstream_string(
        kwse_value=None, nd_value=slope)


def test_nd_folder_loses_the_sign():
    """Documented, not desired: a folder name cannot tell you a slope's sign."""
    assert identity.nd_folder(1.2e4) == identity.nd_folder(1.2e-4) == "nd=1.2E04"


@pytest.mark.parametrize("folder", ["nd=1.2E04", "nd=5.0E01", "nd=3.7E03", "nd=9.9E00"])
def test_nd_folder_round_trips(folder):
    """Parse then re-render must reproduce the folder exactly.

    This is what lets a hotstart name the reach's own normal-depth run: the slope
    is emergent, so the folder is the only place the loop can read it, and it
    only has to be good enough to name that same folder again.
    """
    assert identity.nd_folder(identity.parse_nd_folder(folder)) == folder


@pytest.mark.parametrize("name", ["q=200", "kwse=224.0", "nd=", "nd=abc", ""])
def test_parse_nd_folder_refuses_what_is_not_one(name):
    assert identity.parse_nd_folder(name) is None


# --- assembled paths -----------------------------------------------------

REACH, MODEL_ID, RUN_HASH = 12345, "5f14368c_N350S296E449W355", "af1436c4"


def test_nd_and_kwse_scenarios_are_siblings_under_one_run_identity():
    """A run identity is solver plus methodology pin, so both kinds share it."""
    base = storage.run_base_path(REACH, MODEL_ID, RUN_HASH)
    nd = storage.scenario_manifest_path(
        REACH, MODEL_ID, RUN_HASH, f"{identity.nd_folder(12000.0)}/{identity.q_folder(900)}")
    kwse = storage.scenario_manifest_path(
        REACH, MODEL_ID, RUN_HASH, f"{identity.kwse_folder(226.0)}/{identity.q_folder(900)}")
    assert nd == f"{base}/nd=1.2E04/q=900/scenario_manifest.json"
    assert kwse == f"{base}/kwse=226.0/q=900/scenario_manifest.json"


def test_scenario_dir_from_code_agrees_with_the_folder_builders():
    """The two renderings of one realization must not drift apart."""
    built = f"{identity.kwse_folder(200.2)}/{identity.q_folder(200)}"
    assert identity.scenario_dir_from_code("KWSE200.2Q200") == built
