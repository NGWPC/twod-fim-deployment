"""Tests for ND payload assembly."""

from types import SimpleNamespace

import pytest
from psycopg.types.range import Range

from recon import check

REACH = "100"
MODEL = "5f14368c_N350S296E449W355"

BANDS = {
    "ld_q_max_depth_increase_range": Range(0.75, 1.25),
    "ld_q_median_depth_increase_range": Range(0.25, 0.75),
    "ld_q_flooded_area_prcnt_increase_range": Range(10, 30),
}


def intent_for(**override):
    return {
        "reach_id": REACH, "is_terminal": True, "reach_to_id": None,
        "lake_to_id": None, "coast_to_id": None,
        "q_lower_bound": 90, "q_upper_bound": 1210,
        "initial_dq_step_for_nd": 110, "q_grid_resolution": 10,
        "sdr_commit": "abc", "solver": "lisflood", **BANDS, **override,
    }


@pytest.fixture
def wired(monkeypatch):
    state = SimpleNamespace(intent=intent_for())
    monkeypatch.setattr(check.intent, "effective", lambda reach_id, **kw: state.intent)
    monkeypatch.setattr(check.db, "one", lambda sql, params=None, **kw: {"model_id": MODEL})
    monkeypatch.setattr(check, "_library_scenarios", lambda *a, **kw: [])
    monkeypatch.setattr(check.identity, "run_identity", lambda wanted: ({}, "af1436c4"))
    return state


def test_authored_ranges_are_sent_as_two_element_arrays(wired):
    payload = check._run_nd_payload(REACH)
    assert payload["ld_q_max_depth_increase_range"] == [0.75, 1.25]
    assert payload["ld_q_median_depth_increase_range"] == [0.25, 0.75]
    assert payload["ld_q_flooded_area_prcnt_increase_range"] == [10.0, 30.0]


def test_the_sweep_aims_at_what_adopt_will_judge(wired):
    from recon.observe import _bands

    column = {"max_depth": "ld_q_max_depth_increase_range",
              "median_depth": "ld_q_median_depth_increase_range",
              "flooded_area": "ld_q_flooded_area_prcnt_increase_range"}

    payload = check._run_nd_payload(REACH)
    judged = _bands(wired.intent)
    assert len(judged) == 3
    for label, key, floor, ceiling, _ in judged:
        assert payload[column[key]] == [floor, ceiling], label


def test_an_unauthored_range_is_not_sent(wired):
    wired.intent = intent_for(ld_q_median_depth_increase_range=None)
    payload = check._run_nd_payload(REACH)
    assert "ld_q_median_depth_increase_range" not in payload
    assert "ld_q_max_depth_increase_range" in payload


def test_a_half_open_range_is_not_sent(wired):
    wired.intent = intent_for(ld_q_max_depth_increase_range=Range(0.75, None))
    assert "ld_q_max_depth_increase_range" not in check._run_nd_payload(REACH)
