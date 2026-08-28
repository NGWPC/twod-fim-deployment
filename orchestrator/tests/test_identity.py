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

def sound_scenario(**kw) -> dict:
    obj, digest = identity.run_identity(RUN_INTENT)
    return {"reach_id": 5, "identity": obj, "identity_hash": digest,
            "model_id": "abcd1234_N10S10E10W10",
            "us_discharge": 100.0,
            "properties": {"nominal_wse": 283.2}, **kw}


def test_a_sound_scenario_manifest_is_adopted():
    m = sound_scenario()
    assert identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], 100) == []


def test_a_scenario_from_another_model_is_refused():
    """The same reach and solver, but built against a model intent no longer
    asks for — its results are a previous intent's, not this one's."""
    m = sound_scenario()
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], "ffff0000_N10S10E10W10", 100)
    assert any("model_id" in p for p in problems)


def test_a_scenario_in_the_wrong_q_folder_is_refused():
    m = sound_scenario()
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], 90)
    assert any("us_discharge" in p for p in problems)


def test_a_drifted_run_recipe_is_caught_by_the_self_check():
    m = sound_scenario()
    m["identity"] = {**m["identity"], "solver": "sfincs"}
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], 100)
    assert any("drifted" in p for p in problems)


def test_a_non_string_solver_is_refused():
    m = sound_scenario()
    m["identity"] = {**m["identity"], "solver": {"name": "lisflood", "version": "8.1.0"}}
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], 100)
    assert any("solver must be a string" in p for p in problems)


def test_an_unknown_run_identity_dimension_is_refused():
    """A new dimension in the job means a hash this copy cannot reproduce.
    Refusing is how it announces itself, instead of a silent network rebuild."""
    m = sound_scenario()
    m["identity"] = {**m["identity"], "gpu_model": "A100"}
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], 100)
    assert any("unknown" in p for p in problems)


def test_fractional_discharge_matches_its_rounded_folder():
    """The adaptive stepper emits fractional discharges and the job rounds them
    into the folder name. Truncating instead of rounding refused a valid
    12-scenario library and cost a 2.5-hour re-run, so this is pinned."""
    m = sound_scenario(us_discharge=1171.875)
    assert identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], 1172) == []


def test_a_discharge_rounding_to_a_different_folder_is_still_refused():
    """The guard must still catch a manifest in the wrong folder."""
    m = sound_scenario(us_discharge=1171.875)
    problems = identity.verify_scenario_manifest(m, 5, m["identity_hash"], m["model_id"], 1171)
    assert any("us_discharge" in p for p in problems)
