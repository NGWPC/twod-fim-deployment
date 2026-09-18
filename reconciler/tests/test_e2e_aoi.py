"""End-to-end tests for the test AOI."""

import json
import sys
from pathlib import Path

import pandas as pd

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import seed  # noqa: E402

E2E_REACHES = {
    "1269876933415184": "drains into lake 120053033; nd gets that polygon",
    "1269877024692972": "sits on a terminal, so its kwse has no kwse below to seed from",
    "1269877035720873": "two authored branches meet here; a mainstem is chosen between them",
    "1269877039396680": "the mainstem branch: full kwse, seeded from the library below it",
    "1269877051885631": "the tributary branch: same rung, not the mainstem",
    "1269877088730144": "nothing above it, so build_model is given no mainstem reach",
    "1269869447554114": "names no water body, so nd is sent no outflow polygon at all",
    "1269874503448786": "nothing above and nothing below: the shortest ladder there is",
}

CASES = {
    "terminal:lake": "nd is given the lake's polygon as its outflow area",
    "terminal:outlet": "nd is given no polygon at all; the job derives one",
    "above:terminal": "kwse over a terminal: nothing below has a stage library",
    "above:non-terminal": "kwse waits on all three below, and seeds from their kwse",
    "confluence": "two authored upstreams: a mainstem is picked, and both are woken",
    "headwater": "no upstream at all, so build_model gets no mainstem",
    "isolated": "no upstream and no downstream: the shortest ladder there is",
}

UNCOVERABLE = {"terminal:coast": "no reach in testdata names a coast"}


def _upstream_of(reaches: list[dict]) -> dict[str, list[str]]:
    upstream: dict[str, list[str]] = {}
    for r in reaches:
        if r["reach_to_id"] is not None:
            upstream.setdefault(r["reach_to_id"], []).append(r["reach_id"])
    return upstream


def cases_covered(reaches: list[dict], authored: set[str]) -> dict[str, list[str]]:
    by_id = {r["reach_id"]: r for r in reaches}
    upstream = _upstream_of(reaches)
    covered = {}
    for reach_id in sorted(authored):
        r = by_id[reach_id]
        ups = upstream.get(reach_id, [])
        cases = []
        if r["is_terminal"]:
            cases.append(f"terminal:{r['terminal_reason']}")
        elif by_id[r["reach_to_id"]]["is_terminal"]:
            cases.append("above:terminal")
        else:
            cases.append("above:non-terminal")
        if sum(1 for u in ups if u in authored) >= 2:
            cases.append("confluence")
        if not ups:
            cases.append("headwater")
            if r["is_terminal"]:
                cases.append("isolated")
        covered[reach_id] = cases
    return covered


def _network() -> list[dict]:
    return seed.load_network(TESTDATA / "network.gpkg")


def _authored() -> set[str]:
    flows = json.loads((TESTDATA / "e2e.aoi_config.json").read_text())["flow_statistics"]
    return {str(i) for i in pd.read_parquet(TESTDATA / flows, columns=[]).index}


def test_the_aoi_authors_exactly_the_reaches_documented_here():
    assert _authored() == set(E2E_REACHES)


def test_the_scope_is_downstream_closed():
    reaches = _network()
    authored = _authored()
    by_id = {r["reach_id"]: r for r in reaches}
    assert authored <= set(by_id), "scope names reaches not in the test network"
    dangling = [
        (r, by_id[r]["reach_to_id"])
        for r in authored
        if by_id[r]["reach_to_id"] is not None and by_id[r]["reach_to_id"] not in authored
    ]
    assert not dangling


def test_the_scope_covers_every_case():
    reaches = _network()
    covered = cases_covered(reaches, _authored())
    seen = {case for cases in covered.values() for case in cases}
    missing = [case for case in CASES if case not in seen]
    assert not missing, f"scope no longer covers: {missing}"
