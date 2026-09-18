"""Tests for path construction."""

import pytest

from recon import storage

REACH = 1257410937935512
MODEL_ID = "fceb20c6_N164S214E230W107"
RUN_HASH = "0c24be7a"
ND_FOLDER = "nd=1.0E03"
Q_FOLDER = "q=1000"


IDENTITY_HASH, _, DOMAIN_CODE = MODEL_ID.partition("_")


def job_scenario_out_dir(base_out_dir: str) -> str:
    return (f"{base_out_dir}/reach={REACH}/{IDENTITY_HASH}/{RUN_HASH}"
            f"/{ND_FOLDER}/{Q_FOLDER}")


def test_the_loop_looks_where_the_job_writes():
    written = job_scenario_out_dir(storage.results_root())
    looked_at = (f"{storage.run_base_path(REACH, MODEL_ID, RUN_HASH)}"
                 f"/{ND_FOLDER}/{Q_FOLDER}")
    assert written == looked_at


def test_the_base_handed_to_the_job_adds_nothing_of_its_own():
    root = storage.results_root()
    assert "reach=" not in root
    assert MODEL_ID not in root
    assert root.endswith("/results")


def test_runs_are_filed_under_the_model_identity_hash_alone():
    base = storage.run_base_path(REACH, MODEL_ID, RUN_HASH)
    assert f"/{IDENTITY_HASH}/{RUN_HASH}" in base
    assert DOMAIN_CODE not in base, "domain code leaked into the results address"
    assert f"/{MODEL_ID}/" not in base


def test_reach_appears_exactly_once():
    written = job_scenario_out_dir(storage.results_root())
    assert written.count("reach=") == 1, written
