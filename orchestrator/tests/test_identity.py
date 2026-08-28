"""Identity prediction is a copy of the jobs repo's hashing, so these tests
exist to catch the copy drifting from the original.

The hashes asserted here were produced by running the jobs repo's own code, not
by running this module and writing down what it said. That distinction is the
whole value: a test that records our own output would still pass after the
recipe drifted, and every address the loop predicts would be wrong.
"""

import pytest

from recon import identity

# --- run identity -------------------------------------------------------
# The expected hash is not a guess: it is what the jobs repo's own
# RunIdentity(...).model_dump() hashed to, cross-checked when this was written.
# If it ever changes, this copy has drifted from the job and every run address
# the loop predicts is wrong.

RUN_INTENT = {"sdr_commit": "826a602ddcaf58bf4081dc04b65ba15b82cc8c8a", "solver": "lisflood"}


def test_run_identity_matches_the_jobs_repo():
    obj, digest = identity.run_identity(RUN_INTENT)
    assert obj == {"sdr_commit_id": RUN_INTENT["sdr_commit"], "solver": "lisflood"}
    assert digest == "0c24be7a"


def test_run_identity_says_nothing_about_the_reach():
    """Every reach in a deployment shares one run identity hash. Worth pinning:
    it is a recipe, not an address unique to anything."""
    assert identity.run_identity(RUN_INTENT)[1] == identity.run_identity(dict(RUN_INTENT))[1]


# --- the scenario point -------------------------------------------------

@pytest.mark.parametrize("q,folder", [(100, "q=100"), (100.0, "q=100"), (1250.4, "q=1250")])
def test_q_folder_rounds_to_whole_cms(q, folder):
    assert identity.q_folder(q) == folder


def test_q_folder_round_trips():
    for q in (10, 100, 1250):
        assert identity.parse_q_folder(identity.q_folder(q)) == q


@pytest.mark.parametrize("name", ["nd=1.0E03", "q=", "", "scenario_manifest.json"])
def test_parse_q_folder_ignores_anything_else(name):
    assert identity.parse_q_folder(name) is None


# --- scenario manifest verification -------------------------------------

SCENARIO_DIR = "nd=1.0E03/q=100"


def sound_scenario(**kw) -> dict:
    obj, digest = identity.run_identity(RUN_INTENT)
    return {"reach_id": 5, "identity": obj, "identity_hash": digest,
            "model_id": "abcd1234_N10S10E10W10",
            "scenario_code": "ND1.0E03Q100",
            "properties": {"nominal_wse": 283.2}, **kw}


def test_a_sound_scenario_manifest_is_adopted():
    m = sound_scenario()
    assert identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], SCENARIO_DIR) == []


def test_a_scenario_from_another_model_is_refused():
    """The same reach and solver, but built against a model intent no longer
    asks for — its results are a previous intent's, not this one's."""
    m = sound_scenario()
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], "ffff0000_N10S10E10W10", SCENARIO_DIR)
    assert any("model_id" in p for p in problems)


def test_a_scenario_in_the_wrong_folder_is_refused():
    """The guard's real job: a manifest that does not belong where it sits,
    whether misfiled or copied."""
    m = sound_scenario()
    problems = identity.verify_scenario_manifest(
        m, 5, m["identity_hash"], m["model_id"], "nd=1.0E03/q=90")
    assert any("scenario_code" in p for p in problems)


def test_the_slope_half_is_checked_too():
    """The realization has two halves. Reading a discharge alone left the
    downstream condition unanchored, so a manifest moved between nd= folders
    passed."""
    m = sound_scenario()
    problems = identity.verify_scenario_manifest(
        m, 5, m["identity_hash"], m["model_id"], "nd=9.9E99/q=100")
    assert any("scenario_code" in p for p in problems)


def test_an_unrecognised_scenario_code_is_refused():
    """A code this mirror cannot parse means the jobs repo names scenarios in a
    way the loop does not know; adopting it would mean trusting a location it
    cannot check."""
    m = sound_scenario(scenario_code="WHAT1234")
    problems = identity.verify_scenario_manifest(
        m, 5, m["identity_hash"], m["model_id"], SCENARIO_DIR)
    assert any("scenario_code" in p for p in problems)


def test_a_drifted_run_recipe_is_caught_by_the_self_check():
    m = sound_scenario()
    m["identity"] = {**m["identity"], "solver": "sfincs"}
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], SCENARIO_DIR)
    assert any("drifted" in p for p in problems)


def test_a_non_string_solver_is_refused():
    m = sound_scenario()
    m["identity"] = {**m["identity"], "solver": {"name": "lisflood", "version": "8.1.0"}}
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], SCENARIO_DIR)
    assert any("solver must be a string" in p for p in problems)


def test_an_unknown_run_identity_dimension_is_refused():
    """A new dimension in the job means a hash this copy cannot reproduce.
    Refusing is how it announces itself, instead of a silent network rebuild."""
    m = sound_scenario()
    m["identity"] = {**m["identity"], "gpu_model": "A100"}
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], SCENARIO_DIR)
    assert any("unknown" in p for p in problems)


# --- the realization code mirror ----------------------------------------
# These pairs were produced by running the jobs repo's own get_scenario_code
# and get_scenario_dir_name, not by running this module and writing down what
# it said. That distinction is the whole value: a test recording our own output
# would still pass after the mirror drifted from the naming it mirrors.

@pytest.mark.parametrize("code,directory", [
    ("ND1.0E03Q100", "nd=1.0E03/q=100"),
    ("ND1.5E04Q1000", "nd=1.5E04/q=1000"),
    ("ND1.2E03Q1500", "nd=1.2E03/q=1500"),
    ("KWSE200.2Q200", "kwse=200.2/q=200"),
])
def test_scenario_dir_from_code_matches_the_jobs_repo(code, directory):
    assert identity.scenario_dir_from_code(code) == directory


@pytest.mark.parametrize("code", ["", "ND1.0E03", "Q100", "WHAT1234", "nd=1.0E03/q=100"])
def test_an_uninterpretable_code_yields_no_directory(code):
    """None rather than a guess: a code this mirror cannot read means the loop
    cannot check where the manifest belongs, and unverifiable is refused."""
    assert identity.scenario_dir_from_code(code) is None
