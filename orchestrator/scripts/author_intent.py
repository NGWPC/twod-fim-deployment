"""Author intent: what this deployment wants, and for which reaches.

Dev scaffolding, and the other half of what seed.py used to do. The real
authoring of intent is a person or an upstream system; this stands in until one
feeds the database directly, so it is written to be thrown away.

Split from seed.py because the two are different things with different
producers. The network is modify_network's output — an observation, rebuildable
from the GeoPackages at any time. Intent is authored, and the schema calls
desired_state "preserved at all cost".

The split is what makes re-scoping cheap. seed.py truncates reach_network, and
everything cascades from it: widening the scope used to mean destroying every
model and every ND library already built. Authoring separately lets the schema
do what it was designed to do — deleting a desired_state row fires
forget_applied_revision (09_triggers.sql), which retracts the claim to -1 and
KEEPS the materialized row, because what it recorded was seen in storage and
still was. The next check re-observes and restores a real revision. Nothing is
rebuilt that does not need to be.

Two tables, both written here:

  desired_state_defaults  the single row every reach falls back to, holding the
                          model identity inputs and the deployment-wide
                          defaults. Written as an UPSERT, never DELETE+INSERT:
                          only an UPDATE fires bump_all_reach_revisions, and
                          without that a changed default would re-check nothing.
  desired_state           one row per reach in scope. Also an upsert, so a
                          reach that is already in scope and unchanged keeps its
                          revision — a rewrite with identical values is not a
                          change, and the trigger's WHEN guard says so.

Requires seed.py to have run: desired_state.reach_id is a foreign key into
reach_network, and the scope is checked against the network that is actually
loaded rather than against a file that might not match it.

Usage:
    uv run python scripts/author_intent.py
    uv run python scripts/author_intent.py --scope all
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from recon import db, storage

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"
DEFAULT_Q_BOUNDS_PARQUET = TESTDATA / "min_max_network_flows.parquet"

# The column the flow-bound table is keyed by.
REACH_ID_FIELD = "reach_id"

# Mirrors what the deployed job images bake in (twod_fim_jobs/consts.py). The
# loop predicts artifact addresses from these, so they must match the images or
# nothing it builds will be found where it looked.
SDR_COMMIT = "826a602ddcaf58bf4081dc04b65ba15b82cc8c8a"
SOLVER = "lisflood"
# Stage increment for the KWSE libraries, in metres. DR-033 ALT-B allows only
# {0.25, 0.5, 1, 2, 5} and a CHECK constraint enforces it, because the grid it
# builds is anchored to zero and nothing derives the value.
#
# Nothing else in the system supplies it either, so leaving it NULL is not a
# neutral default: every non-terminal reach then reports awaiting_inputs and no
# stage library is ever planned. It is set here so a seeded deployment can run
# the whole ladder without anyone having to know that.
#
# 2 m is deliberately coarse. It is the cheapest increment that still produces a
# multi-stage library, which is what you want while confirming the machinery is
# right; tighten it per reach once fidelity rather than correctness is the
# question.
LD_DS_Z_DELTA = 2.0
# Library resolution (DR-030), as the acceptance RANGE of each criterion, per the
# contract agreed with the jobs repo. All three are measured over WET CELLS ONLY
# and describe the increase between consecutive library discharges.
#
# Authored but not yet wired: nothing sends these to a job and nothing checks
# them, pending the jobs-repo side. They are seeded now so the values are in one
# place, under review, when that lands.
LD_Q_MAX_DEPTH_INCREASE_RANGE = "[0.75,1.25]"        # m
LD_Q_MEDIAN_DEPTH_INCREASE_RANGE = "[0.25,0.5]"      # m
LD_Q_FLOODED_AREA_PRCNT_INCREASE_RANGE = "[7.5,12.5]"  # percent, so 7.5 = 7.5%
DEM_SOURCE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/USGS_Seamless_DEM_13.vrt"
# An address, not a mounted path: the raster is uploaded to storage by seed.py,
# so a job reads it wherever it runs without a volume being arranged. It is also
# a model IDENTITY input — the string is hashed — so changing it moves every
# model's address and invalidates what is already built.
LULC_SOURCE = storage.lulc_path()
LULC_LOOKUP = {
    "11": 0.04,
    "21": 0.04,
    "22": 0.1,
    "23": 0.08,
    "24": 0.15,
    "31": 0.025,
    "41": 0.16,
    "42": 0.16,
    "43": 0.16,
    "52": 0.1,
    "71": 0.035,
    "81": 0.03,
    "82": 0.035,
    "90": 0.12,
    "95": 0.07,
}

# Q bounds, derived from the flow statistics fixture rather than authored by
# hand. These are desired_state columns — intent — which is why the formula
# lives here. seed.py imports it only because the reach network parquet carries
# the same three columns; drop them from that file and the import goes with them.
#
Q_LOWER_BOUND_SRC_FIELD = "high_flow_threshold"
Q_LOWER_BOUND_MULTIPLIER = 1.0
Q_UPPER_BOUND_SRC_FIELD = "f100year"
Q_UPPER_BOUND_MULTIPLIER = 1.0
DQ_STEP_FIELD = "initial_dq_step_for_nd"

# How far the e2e scope pulls its discharge bounds inside the DR-029 range.
#
# NOT methodology, and deliberately not in the decision record: DR-029 ALT-D
# says the library runs from the high flow threshold to the 100-year discharge,
# and `--scope all` authors exactly that. This is a fixture concern. The
# end-to-end run exists to prove the machinery works, and what it costs is set
# by how far the adaptive sweep has to travel, so a shorter journey is a shorter
# run of the same shape.
E2E_Q_LOWER_FACTOR = 1.3
E2E_Q_UPPER_FACTOR = 0.7


def narrow_for_e2e(reaches: list[dict]) -> list[dict]:
    """Pull the discharge bounds inward, for the end-to-end scope only.

    The step is deliberately NOT recomputed. It is an absolute increment in cms,
    and leaving it at the value the full range implied is the whole point: the
    sweep then crosses a shorter distance in the same size paces, which is fewer
    runs. Rescaling it to the new range would restore the original count in
    smaller steps and save nothing.

    Both roundings go inward — up at the bottom, down at the top — so the result
    is never wider than the factors ask for.

    A narrow enough reach has no room for both factors, and gives them up one at
    a time rather than all at once. The lower factor goes first: raising the
    floor drops the smallest discharges, which are the cheapest to simulate,
    while lowering the ceiling drops the largest, which wet the most cells and
    cost the most. Whatever room a reach has is worth spending on the ceiling.

    Giving up both leaves the authored range, which is the one outcome that must
    stay reachable: desired_state_flow_bounds_chk requires lower < upper, so
    bounds that crossed would abort the whole authoring run, and an inverted
    range describes no library anyone could build.
    """
    # Tried in order, first fit wins. The last is the authored range itself, so
    # the ladder always lands somewhere.
    concessions = (
        (E2E_Q_LOWER_FACTOR, E2E_Q_UPPER_FACTOR),
        (1.0, E2E_Q_UPPER_FACTOR),
        (1.0, 1.0),
    )
    for r in reaches:
        authored = (r["q_lower_bound"], r["q_upper_bound"])
        for lower_factor, upper_factor in concessions:
            low = max(math.ceil(authored[0] * lower_factor), 1)
            high = max(math.floor(authored[1] * upper_factor), 1)
            if low < high:
                break
        else:  # pragma: no cover - the last concession is the authored range
            low, high = authored
        r["q_lower_bound"], r["q_upper_bound"] = low, high
        # What the report needs to say how far this reach got: the range it came
        # from, and which factors survived.
        r["narrowed"] = None if (low, high) == authored else authored
        r["factors"] = (lower_factor, upper_factor)
    return reaches


def load_q_bounds(q_bound_parquet: Path, reaches: list[dict]) -> list[dict]:
    """Lookup and append flow bounds to the reach dataset."""
    bounds = pd.read_parquet(q_bound_parquet)

    if bounds.index.name != REACH_ID_FIELD:
        raise RuntimeError(
            f"Q bound parquet file is indexed by {bounds.index.name} instead of {REACH_ID_FIELD}"
        )
    if not pd.api.types.is_integer_dtype(bounds.index):
        raise RuntimeError(
            f"Q bound parquet index must be integer, got {bounds.index.dtype}"
        )

    duplicate_ids = list(bounds.index[bounds.index.duplicated()])

    missing_reaches = []
    nan_bounds = []
    for r in reaches:
        reach_id = r["reach_id"]
        reach_id = int(str(reach_id).split("_")[0])
        try:
            row = bounds.loc[reach_id]
        except KeyError:
            missing_reaches.append(reach_id)
            continue
        if isinstance(row, pd.DataFrame):
            # duplicate row
            continue
        low = max(
            np.ceil(row[Q_LOWER_BOUND_SRC_FIELD] * Q_LOWER_BOUND_MULTIPLIER).astype(
                int
            ),
            1,
        )
        high = max(
            np.ceil(row[Q_UPPER_BOUND_SRC_FIELD] * Q_UPPER_BOUND_MULTIPLIER).astype(
                int
            ),
            1,
        )
        if pd.isna(low) or pd.isna(high):
            nan_bounds.append(reach_id)
            continue
        if low > high:
            r["q_lower_bound"] = high
            r["q_upper_bound"] = low
        else:
            r["q_lower_bound"] = low
            r["q_upper_bound"] = high
        rng = high - low
        r[DQ_STEP_FIELD] = max(int(rng / 10), 1)
    if duplicate_ids:
        raise RuntimeError(
            f"Duplicate reach_id entries in Q bound parquet for {len(duplicate_ids)} reaches:\n{duplicate_ids}"
        )
    if missing_reaches:
        raise RuntimeError(
            f"Missing flow bound data for {len(missing_reaches)} reaches:\n{missing_reaches}"
        )
    if nan_bounds:
        raise RuntimeError(
            f"NAN flow values found for {len(nan_bounds)} reaches:\n{nan_bounds}"
        )
    return reaches


# ---------------------------------------------------------------------------
# Scope: which reaches intent is authored for.
#
# A scope is not a smaller network. It is the same network with a smaller ask,
# which is the line the loop itself draws: a reach in the network means nothing
# until intent is authored for it (intent.effective), and the queue puts its
# question to desired_state, not to reach_network. So the reaches in scope still
# see the true topology, the true mainstem, and the true geometry a full run
# would give them — check.py's _upstream reads reach_network, not this table.
#
# One rule constrains any scope, and it is the ladder's: every rung above the
# first waits on the reach DOWNSTREAM. A reach whose downstream has no intent
# waits on a proof nothing will ever write. A scope must therefore be
# DOWNSTREAM-CLOSED — choosing a reach chooses every reach between it and its
# terminal — and verify_scope enforces that rather than trusting the list below.
#
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
#   1269869556169965                      outlet terminal, and a headwater
#
# Chosen small as well as wisely: nothing here drains more than 156 km2, where
# the network's other outlet carries 2607 km2 and would spend the whole run's
# budget on one domain. Depth is kept instead — four rungs from the headwater
# down to the lake, so results have somewhere to travel.
E2E_REACHES = {
    1269876933415184: "drains into lake 120053033; nd gets that polygon",
    1269877024692972: "sits on a terminal, so its kwse has no kwse below to seed from",
    1269877035720873: "two authored branches meet here; a mainstem is chosen between them",
    1269877039396680: "the mainstem branch: full kwse, seeded from the library below it",
    1269877051885631: "the tributary branch: same rung, not the mainstem",
    1269877088730144: "nothing above it, so build_model is given no mainstem reach",
    1269869556169965: "names no water body and has nothing above it: a component of one",
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


def _upstream_of(reaches: list[dict]) -> dict[int, list[int]]:
    """Who flows into whom, derived rather than read off is_headwater.

    Derived because this is the question check.py asks of the database
    (_UPSTREAM, keyed on reach_to_id), and a flag that disagreed with the links
    would report coverage the loop does not have.
    """
    upstream: dict[int, list[int]] = {}
    for r in reaches:
        if r["reach_to_id"] is not None:
            upstream.setdefault(r["reach_to_id"], []).append(r["reach_id"])
    return upstream


def cases_covered(reaches: list[dict], authored: set[int]) -> dict[int, list[str]]:
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


def verify_scope(reaches: list[dict], authored: set[int]) -> dict[int, list[str]]:
    """Refuse to author a scope that cannot finish, or that has stopped covering.

    Both failures are silent otherwise. A dangling scope authors cleanly and
    then sits at awaiting_downstream until someone reads the activity log; a
    scope that has lost a case authors cleanly and passes an end-to-end run that
    no longer proves what it claims to. E2E_REACHES is a claim about the
    network, so it is checked against the network every time it is used.
    """
    by_id = {r["reach_id"]: r for r in reaches}

    unknown = sorted(authored - set(by_id))
    if unknown:
        sys.exit(f"scope names reaches that are not in this network: {unknown}")

    dangling = [
        (reach_id, by_id[reach_id]["reach_to_id"])
        for reach_id in sorted(authored)
        if by_id[reach_id]["reach_to_id"] is not None
        and by_id[reach_id]["reach_to_id"] not in authored
    ]
    if dangling:
        lines = "\n".join(f"    {r} -> {ds}" for r, ds in dangling)
        sys.exit(
            "scope is not downstream-closed; these reaches would wait forever on a\n"
            f"downstream reach nothing is authored for:\n{lines}"
        )

    covered = cases_covered(reaches, authored)
    seen = {case for cases in covered.values() for case in cases}
    missing = [case for case in CASES if case not in seen]
    if missing:
        lines = "\n".join(f"    {case}  {CASES[case]}" for case in missing)
        sys.exit(f"scope no longer covers every case:\n{lines}")
    return covered


# Everything a scope decision depends on. Read from the database rather than the
# GeoPackage so the scope is checked against the network that is actually
# loaded — including load_network's clip rule, which is what turns a reach
# pointing off the edge of the extract into the outlet terminal this scope needs.
_NETWORK = """
    SELECT reach_id, reach_to_id, is_terminal, terminal_reason
    FROM reach_network
    ORDER BY reach_id
"""

# Upsert, not DELETE+INSERT. bump_all_reach_revisions is a BEFORE UPDATE
# trigger, so a delete and re-insert would slip past it: the defaults would
# change and not one reach would be re-checked. Its WHEN guard means re-running
# with the same values bumps nothing.
_DEFAULTS = """
    INSERT INTO desired_state_defaults
        (id, sdr_commit, grid_resolution, epsg_code, dem_source, lulc_source,
         lulc_lookup, solver, ld_ds_z_delta,
         ld_q_max_depth_increase_range, ld_q_median_depth_increase_range,
         ld_q_flooded_area_prcnt_increase_range)
    VALUES (1, %s, 10, 5070, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        sdr_commit      = EXCLUDED.sdr_commit,
        grid_resolution = EXCLUDED.grid_resolution,
        epsg_code       = EXCLUDED.epsg_code,
        dem_source      = EXCLUDED.dem_source,
        lulc_source     = EXCLUDED.lulc_source,
        lulc_lookup     = EXCLUDED.lulc_lookup,
        solver          = EXCLUDED.solver,
        ld_ds_z_delta   = EXCLUDED.ld_ds_z_delta,
        ld_q_max_depth_increase_range = EXCLUDED.ld_q_max_depth_increase_range,
        ld_q_median_depth_increase_range = EXCLUDED.ld_q_median_depth_increase_range,
        ld_q_flooded_area_prcnt_increase_range = EXCLUDED.ld_q_flooded_area_prcnt_increase_range
"""

# Retract intent for anything that has left the scope. The AFTER DELETE trigger
# sets applied_revision to -1 in every materialized_* table for the reach and
# leaves the rows themselves alone, so what was observed in storage survives and
# only the claim about it is withdrawn.
_RETRACT = "DELETE FROM desired_state WHERE reach_id <> ALL(%s) RETURNING reach_id"

# Upsert again, for the same reason: a reach already in scope with the same
# bounds is not a change, so its revision holds and nothing it has built is
# invalidated.
_AUTHOR = """
    INSERT INTO desired_state
        (reach_id, q_lower_bound, q_upper_bound, initial_dq_step_for_nd)
    VALUES (%(reach_id)s, %(q_lower_bound)s, %(q_upper_bound)s, %(initial_dq_step_for_nd)s)
    ON CONFLICT (reach_id) DO UPDATE SET
        q_lower_bound          = EXCLUDED.q_lower_bound,
        q_upper_bound          = EXCLUDED.q_upper_bound,
        initial_dq_step_for_nd = EXCLUDED.initial_dq_step_for_nd
"""


def author(scope: str, q_bound_parquet: Path) -> None:
    """Write the defaults row and one desired_state row per reach in scope.

    The scope decides two things, and only one of them is which reaches: `e2e`
    also pulls the discharge bounds inside what DR-029 asks for, to keep the run
    short. `all` authors the methodology as written.
    """
    with db.connect() as conn:
        reaches = db.query(_NETWORK, conn=conn)
        if not reaches:
            sys.exit("reach_network is empty; run scripts/seed.py first")
        authored = ({r["reach_id"] for r in reaches} if scope == "all"
                    else set(E2E_REACHES))
        covered = verify_scope(reaches, authored)

        in_scope = load_q_bounds(
            q_bound_parquet, [r for r in reaches if r["reach_id"] in authored]
        )
        if scope == "e2e":
            in_scope = narrow_for_e2e(in_scope)

        # Revisions as they stand, so the report can say what actually moved
        # rather than what was written over.
        before = {
            r["reach_id"]: r["revision"]
            for r in db.query("SELECT reach_id, revision FROM desired_state", conn=conn)
        }

        conn.execute(
            _DEFAULTS,
            (
                SDR_COMMIT,
                DEM_SOURCE,
                LULC_SOURCE,
                json.dumps(LULC_LOOKUP),
                SOLVER,
                LD_DS_Z_DELTA,
                LD_Q_MAX_DEPTH_INCREASE_RANGE,
                LD_Q_MEDIAN_DEPTH_INCREASE_RANGE,
                LD_Q_FLOODED_AREA_PRCNT_INCREASE_RANGE,
            ),
        )
        retracted = [
            r["reach_id"] for r in db.query(_RETRACT, (sorted(authored),), conn=conn)
        ]
        for r in in_scope:
            conn.execute(_AUTHOR, r)

        after = {
            r["reach_id"]: r["revision"]
            for r in db.query("SELECT reach_id, revision FROM desired_state", conn=conn)
        }

    new = [r for r in after if r not in before]
    moved = [r for r in after if r in before and after[r] != before[r]]

    print(f"intent authored {len(after)} reach(es)")
    print(f"  new           {len(new)}")
    print(f"  revision moved{len(moved):>3}")
    print(f"  unchanged     {len(after) - len(new) - len(moved)}")
    if retracted:
        print(f"  retracted     {len(retracted)} (claims withdrawn, observations kept)")
    print()
    bounds = {r["reach_id"]: r for r in in_scope}
    for reach_id, cases in covered.items():
        r = bounds[reach_id]
        rng = f"{r['q_lower_bound']}-{r['q_upper_bound']} cms"
        # Say so when the range on the row is not the one DR-029 implies, so
        # nobody reads these bounds back as the methodology — and say which
        # factors a tight reach had to give up to stay a valid range.
        was, factors = r.get("narrowed"), r.get("factors")
        if factors is None:          # not the e2e scope; nothing was narrowed
            note = ""
        elif was is None:
            note = "  (no room to narrow)"
        elif factors[0] == 1.0:
            note = f"  (from {was[0]}-{was[1]}, lower factor given up for room)"
        else:
            note = f"  (from {was[0]}-{was[1]})"
        print(f"  {reach_id}  {rng:>16}, dq {r[DQ_STEP_FIELD]:>3}{note}  {', '.join(cases)}")
    for case, why in UNCOVERABLE.items():
        print(f"  not covered   {case} ({why})")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--scope",
        choices=("e2e", "all"),
        default="e2e",
        help="reaches to author for, and whether the range is narrowed "
             "(default: e2e, seven reaches with bounds pulled inside DR-029)",
    )
    ap.add_argument(
        "--q-bound-parquet",
        type=Path,
        default=DEFAULT_Q_BOUNDS_PARQUET,
        help="flow statistics the discharge bounds are derived from",
    )
    args = ap.parse_args()

    if not args.q_bound_parquet.exists():
        sys.exit(f"No such flow bounds parquet: {args.q_bound_parquet}")

    print(f"scope    {args.scope}")
    print(f"q bounds {args.q_bound_parquet}")
    if args.scope == "e2e":
        print(f"bounds   narrowed to {E2E_Q_LOWER_FACTOR}x lower, "
              f"{E2E_Q_UPPER_FACTOR}x upper (fixture only, not DR-029)")
    print()
    author(args.scope, args.q_bound_parquet)


if __name__ == "__main__":
    main()
