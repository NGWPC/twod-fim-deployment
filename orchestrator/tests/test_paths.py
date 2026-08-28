"""Where the loop looks must be where the job writes.

The run job builds its own output path from the base it is handed:

    RunScenarioInputs.scenario_out_dir
      = f"{base_out_dir}/reach={reach_id}/{model_id}/{run_identity_hash}/{scenario_dir_name}"

with base_out_dir = inputs.model_results_base_path. Every segment the loop adds
to that base is therefore written into the path TWICE, and every segment it
leaves out is one it will not look under. Sending a per-reach prefix produced
exactly that: `.../results/reach=5/<hash>/reach=5/<model_id>/...`, and nothing
the loop predicted was ever found.

These tests reproduce the job's formula from the outside and assert the two
agree. They are the check that a base path is not silently re-prefixed again.
"""

import pytest

from recon import storage

REACH = 1257410937935512
MODEL_ID = "fceb20c6_N164S214E230W107"
RUN_HASH = "0c24be7a"
ND_FOLDER = "nd=1.0E03"
Q_FOLDER = "q=1000"


def job_scenario_out_dir(base_out_dir: str) -> str:
    """RunScenarioInputs.scenario_out_dir, reproduced from the jobs repo."""
    return f"{base_out_dir}/reach={REACH}/{MODEL_ID}/{RUN_HASH}/{ND_FOLDER}/{Q_FOLDER}"


def test_the_loop_looks_where_the_job_writes():
    """The whole point: one path, built two ways, must be the same string."""
    written = job_scenario_out_dir(storage.results_root())
    looked_at = (f"{storage.nd_run_base_path(REACH, MODEL_ID, RUN_HASH)}"
                 f"/{ND_FOLDER}/{Q_FOLDER}")
    assert written == looked_at


def test_the_base_handed_to_the_job_adds_nothing_of_its_own():
    """results_root is what goes in the payload. It must carry no reach and no
    model, because the job appends both — that doubling was the bug."""
    root = storage.results_root()
    assert "reach=" not in root
    assert MODEL_ID not in root
    assert root.endswith("/results")


def test_runs_are_filed_under_the_whole_model_id():
    """Domain code included. A run is only ever verified against the exact
    model_id materialized now, so the address has to carry the same thing the
    verification compares."""
    base = storage.nd_run_base_path(REACH, MODEL_ID, RUN_HASH)
    assert f"/{MODEL_ID}/" in base
    identity_hash, _, domain_code = MODEL_ID.partition("_")
    assert f"/{identity_hash}/{RUN_HASH}" not in base, (
        "filed by identity hash alone; the job files by full model_id")
    assert domain_code in base


def test_reach_appears_exactly_once():
    written = job_scenario_out_dir(storage.results_root())
    assert written.count("reach=") == 1, written
