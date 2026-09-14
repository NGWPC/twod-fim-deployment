"""The test AOI still proves what it claims to.

testdata/e2e.aoi_config.json authors eight reaches of the test network for `just test-e2e`:
its flow statistics, testdata/min_max_network_flows_e2e.parquet, cover only
those, while the whole network is seeded.
The scope is a claim about the network — that these reaches exercise every fork
the loop takes — and a claim like that goes stale silently: the run still
authors and still passes, while no longer proving anything. So it is checked
here, against the network as seed.py loads it (clip rule included), instead of
inside author_intent.py, which authors any AOI and knows nothing of tests.
"""

import json
import sys
from pathlib import Path

import pandas as pd

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import seed  # noqa: E402

# The e2e scope. Two components, seven reaches, drawn downstream-first with each
# indent a step upstream, the direction results travel:
#
#   1269876933415184                      lake terminal
#   └── 1269877024692972                  one above a terminal
#       └── 1269877035720873              confluence
#           ├── 1269877039396680          mainstem branch (DA 101)
#           │   └── 1269877088730144      headwater
#           └── 1269877051885631          tributary branch (DA 19)
#
#   1269869447554114                      outlet terminal
#
#   1269874503448786                      lake terminal, nothing above it
#
# Chosen for the shape of the work, not the size of the river. The ladder is
# four rungs from a headwater down to a lake, so results have somewhere to
# travel, and every reach in it is small.
#
# The outlet terminal is the exception, and deliberately so. The extract has
# exactly two, and the other — 1269869556169965 — is a 13.7 km single-reach
# component whose centerline bounding box is 22 km2, three and a half times the
# largest here, before any bankfull buffer. This one drains far more area, 2607
# km2 against its 68, but its centerline is short and compact: 6 km2 of bounding
# box, in line with the rest of the scope. Domain extent is what costs, and
# drainage area only reaches it through the buffer.
#
# It earns its place twice over: it is the only reach here whose nd job is sent
# no outflow polygon at all, and the only terminal with reaches above it, so
# build_model picks a mainstem for a reach that has nothing below it.
#
# That last property is why the third component exists. The reach it replaced
# was isolated — nothing above, nothing below — which is the shortest ladder the
# loop can be asked to walk, and a case worth keeping. 1269874503448786 covers
# it at a fifth of the cost: 2 km2 of bounding box, the second smallest here.
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

# The forks a scope has to keep alive. Each is a branch the loop actually takes
# — a different payload, a different rung, or a different reason to wait — not a
# property of the data collected for its own sake.
CASES = {
    "terminal:lake": "nd is given the lake's polygon as its outflow area",
    "terminal:outlet": "nd is given no polygon at all; the job derives one",
    "above:terminal": "kwse over a terminal: nothing below has a stage library",
    "above:non-terminal": "kwse waits on all three below, and seeds from their kwse",
    "confluence": "two authored upstreams: a mainstem is picked, and both are woken",
    "headwater": "no upstream at all, so build_model gets no mainstem",
    "isolated": "no upstream and no downstream: the shortest ladder there is",
}

# In the loop, absent from this network. Nothing in testdata sets coast_to_id,
# so the coast arm of _nd_boundary is unreachable from any scope of it — the
# full network included. Named so its absence is a known gap and not a silence.
UNCOVERABLE = {"terminal:coast": "no reach in testdata names a coast"}


def _upstream_of(reaches: list[dict]) -> dict[str, list[str]]:
    """Who flows into whom, derived rather than read off is_headwater.

    Derived because this is the question check.py asks of the database
    (_UPSTREAM, keyed on reach_to_id), and a flag that disagreed with the links
    would report coverage the loop does not have.
    """
    upstream: dict[str, list[str]] = {}
    for r in reaches:
        if r["reach_to_id"] is not None:
            upstream.setdefault(r["reach_to_id"], []).append(r["reach_id"])
    return upstream


def cases_covered(reaches: list[dict], authored: set[str]) -> dict[str, list[str]]:
    """Which cases each authored reach exercises, judged on the loaded network."""
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
    """The reaches e2e.aoi_config.json authors: those its flow statistics cover."""
    flows = json.loads((TESTDATA / "e2e.aoi_config.json").read_text())["flow_statistics"]
    return {str(i) for i in pd.read_parquet(TESTDATA / flows, columns=[]).index}


def test_the_aoi_authors_exactly_the_reaches_documented_here():
    assert _authored() == set(E2E_REACHES)


def test_the_scope_is_downstream_closed():
    """Every reach's downstream is in scope, or nothing will ever let it run."""
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
