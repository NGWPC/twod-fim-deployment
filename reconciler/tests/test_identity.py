"""Tests for identity."""

import pytest

from recon import identity


RUN_INTENT = {"sdr_commit": "826a602ddcaf58bf4081dc04b65ba15b82cc8c8a", "solver": "lisflood"}


def test_run_identity_matches_the_jobs_repo():
    obj, digest = identity.run_identity(RUN_INTENT)
    assert obj == {"sdr_commit_id": RUN_INTENT["sdr_commit"], "solver": "lisflood"}
    assert digest == "0c24be7a"


def test_run_identity_says_nothing_about_the_reach():
    assert identity.run_identity(RUN_INTENT)[1] == identity.run_identity(dict(RUN_INTENT))[1]


@pytest.mark.parametrize("q,folder", [(100, "q=100"), (100.0, "q=100"), (1250.4, "q=1250")])
def test_q_folder_rounds_to_whole_cms(q, folder):
    assert identity.q_folder(q) == folder


def test_q_folder_round_trips():
    for q in (10, 100, 1250):
        assert identity.parse_q_folder(identity.q_folder(q)) == q


@pytest.mark.parametrize("name", ["nd=1.0E03", "q=", "", "scenario_manifest.json"])
def test_parse_q_folder_ignores_anything_else(name):
    assert identity.parse_q_folder(name) is None


SCENARIO_DIR = "nd=1.0E03/q=100"


def sound_scenario(**kw) -> dict:
    obj, digest = identity.run_identity(RUN_INTENT)
    return {"reach_id": "5", "identity": obj, "identity_hash": digest,
            "model_id": "abcd1234_N10S10E10W10",
            "scenario_code": "ND1.0E03Q100",
            "properties": {"nominal_wse": 283.2}, **kw}


def test_a_sound_scenario_manifest_is_adopted():
    m = sound_scenario()
    assert identity.verify_scenario_manifest(m, "5", m["identity_hash"], m["model_id"], SCENARIO_DIR) == []


def test_a_scenario_from_another_model_is_refused():
    m = sound_scenario()
    problems = identity.verify_scenario_manifest(m, "5", m["identity_hash"], "ffff0000_N10S10E10W10", SCENARIO_DIR)
    assert any("model_id" in p for p in problems)


def test_a_scenario_in_the_wrong_folder_is_refused():
    m = sound_scenario()
    problems = identity.verify_scenario_manifest(
        m, "5", m["identity_hash"], m["model_id"], "nd=1.0E03/q=90")
    assert any("scenario_code" in p for p in problems)


def test_the_slope_half_is_checked_too():
    m = sound_scenario()
    problems = identity.verify_scenario_manifest(
        m, "5", m["identity_hash"], m["model_id"], "nd=9.9E99/q=100")
    assert any("scenario_code" in p for p in problems)


def test_an_unrecognised_scenario_code_is_refused():
    m = sound_scenario(scenario_code="WHAT1234")
    problems = identity.verify_scenario_manifest(
        m, "5", m["identity_hash"], m["model_id"], SCENARIO_DIR)
    assert any("scenario_code" in p for p in problems)


def test_a_drifted_run_recipe_is_caught_by_the_self_check():
    m = sound_scenario()
    m["identity"] = {**m["identity"], "solver": "sfincs"}
    problems = identity.verify_scenario_manifest(m, "5", m["identity_hash"], m["model_id"], SCENARIO_DIR)
    assert any("drifted" in p for p in problems)


def test_a_non_string_solver_is_refused():
    m = sound_scenario()
    m["identity"] = {**m["identity"], "solver": {"name": "lisflood", "version": "8.1.0"}}
    problems = identity.verify_scenario_manifest(m, "5", m["identity_hash"], m["model_id"], SCENARIO_DIR)
    assert any("solver must be a string" in p for p in problems)


def test_an_unknown_run_identity_dimension_is_refused():
    m = sound_scenario()
    m["identity"] = {**m["identity"], "gpu_model": "A100"}
    problems = identity.verify_scenario_manifest(m, "5", m["identity_hash"], m["model_id"], SCENARIO_DIR)
    assert any("unknown" in p for p in problems)


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
    assert identity.scenario_dir_from_code(code) is None


from shapely import wkb as shapely_wkb
from shapely.geometry import LineString


@pytest.mark.parametrize("coords,resolution,bbox,code", [
    ([(11234.7, 759001.3), (12001.9, 759877.2), (12410.1, 760011.8)], 10.0,
     [11160.0, 758920.0, 12490.0, 760090.0], "N52S65E72W61"),
    ([(-2058171.3, 2809120.9), (-2057000.2, 2809999.1)], 3.0,
     [-2058195.0, 2809098.0, -2056977.0, 2810022.0], "N154S154E203W203"),
    ([(0.31, 0.77), (1.02, 2.44)], 0.1, [-0.5, 0.0, 1.8, 3.2], "N16S16E11W11"),
])
def test_domain_code_matches_the_jobs_repo(coords, resolution, bbox, code):
    geom_wkb = shapely_wkb.dumps(LineString(coords))
    assert identity.domain_code(bbox, geom_wkb, resolution) == code


REACH = [(11234.7, 759001.3), (12001.9, 759877.2), (12410.1, 760011.8)]


@pytest.mark.parametrize("coords,resolution,raw,snapped,code", [
    (REACH, 10.0, [11075.0, 758852.0, 12660.0, 760320.0],
     [11070.0, 758850.0, 12660.0, 760320.0], "N75S72E89W70"),
    (REACH, 30.0, [11070.0, 758850.0, 12660.0, 760320.0],
     [11070.0, 758850.0, 12660.0, 760320.0], "N25S24E30W23"),
    ([(0.31, 0.77), (1.02, 2.44)], 0.1, [0.03, 0.51, 1.37, 2.96],
     [0.0, 0.5, 1.4000000000000001, 3.0], "N13S11E8W6"),
])
def test_an_off_grid_authored_domain_snaps_as_the_jobs_repo_does(coords, resolution, raw, snapped, code):
    assert identity.snap_bbox(raw, resolution) == snapped
    geom_wkb = shapely_wkb.dumps(LineString(coords))
    assert identity.domain_code(snapped, geom_wkb, resolution) == code


MODEL_BBOX = [11160.0, 758920.0, 12490.0, 760090.0]


def sound_model(**kw) -> dict:
    ident = {k: "x" for k in identity.IDENTITY_KEYS}
    digest = identity.hash_dict(ident)
    return {"reach_id": "5", "identity": ident, "identity_hash": digest,
            "model_id": f"{digest}_N52S65E72W61",
            "domain": {"bbox": MODEL_BBOX, "anchor": [11220.0, 759440.0],
                       "offsets": [65.0, 52.0, 127.0, 6.0]}, **kw}


def test_an_unauthored_domain_accepts_whatever_the_job_built():
    m = sound_model()
    assert identity.verify_manifest(m, "5", m["model_id"]) == []


def test_a_model_built_over_the_authored_domain_is_adopted():
    m = sound_model()
    assert identity.verify_manifest(m, "5", m["model_id"], MODEL_BBOX) == []


def test_a_model_built_over_another_domain_is_refused():
    m = sound_model()
    problems = identity.verify_manifest(m, "5", m["model_id"], [11170.0, 758920.0, 12490.0, 760090.0])
    assert any("authored model_domain" in p for p in problems)


def test_a_manifest_without_a_domain_is_refused_when_one_is_authored():
    m = sound_model()
    del m["domain"]
    assert any("authored model_domain" in p for p in identity.verify_manifest(m, "5", m["model_id"], MODEL_BBOX))
