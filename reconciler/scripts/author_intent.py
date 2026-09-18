"""Author desired state: the system-wide defaults, and one AOI's own network.

"defaults" writes desired_state_defaults from the system-wide settings; run it
once before authoring any AOI. "aoi" authors the reaches of the network an AOI
config names, leaving anything it does not override on the defaults.

Usage:
    just author-defaults                     # first time
    just author-defaults --yes               # change the row in force
    just author-intent <aoi-config-path>

    python scripts/author_intent.py defaults [--yes]
    python scripts/author_intent.py aoi <aoi-config-path>

--yes is required to change defaults already in force, because every reach is
re-checked afterwards.
"""

import argparse
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import aoi_config
import flow_statistics
from recon import db, storage
from recon.config import settings

Q_LOWER_BOUND_MULTIPLIER = 1.0
Q_UPPER_BOUND_MULTIPLIER = 1.0
DQ_STEP_FIELD = "initial_dq_step_for_nd"
Q_GRID_FIELD = "q_grid_resolution"
Q_GRID_MENU = (2, 5, 10, 50, 100)
Q_GRID_SEED_CEILING = 10
Q_GRID_MIN_LINES = 10

REPORT_EACH_REACH_UP_TO = 50


def read_q_bounds(path: Path, flows: flow_statistics.FlowStatistics) -> pd.DataFrame:
    """The flow table indexed by reach id, holding just the two bound columns as q_lower and q_upper."""
    table = flow_statistics.read(
        path,
        flows.reach_id_column,
        {flows.q_lower_column: "flow_q_lower_column", flows.q_upper_column: "flow_q_upper_column"},
    )
    return table.rename(columns={flows.q_lower_column: "q_lower", flows.q_upper_column: "q_upper"})


def load_q_bounds(bounds: pd.DataFrame, reaches: list[dict]) -> list[dict]:
    """Look up and append flow bounds to each reach, from read_q_bounds()."""
    duplicate_ids = list(bounds.index[bounds.index.duplicated()])

    missing_reaches = []
    nan_bounds = []
    for r in reaches:
        reach_id = r["reach_id"]
        try:
            row = bounds.loc[flow_statistics.flow_id(reach_id)]
        except KeyError:
            missing_reaches.append(reach_id)
            continue
        if isinstance(row, pd.DataFrame):
            continue
        low = max(
            np.ceil(row["q_lower"] * Q_LOWER_BOUND_MULTIPLIER).astype(
                int
            ),
            1,
        )
        high = max(
            np.ceil(row["q_upper"] * Q_UPPER_BOUND_MULTIPLIER).astype(
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
            f"Duplicate reach_id entries in the flow statistics for {len(duplicate_ids)} reaches:\n{duplicate_ids}"
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


def narrow(reaches: list[dict], lower_factor: float, upper_factor: float) -> list[dict]:
    """Pull the discharge bounds inward by an AOI's q_bound_factors."""
    concessions = ((lower_factor, upper_factor), (1.0, upper_factor), (1.0, 1.0))
    for r in reaches:
        authored = (r["q_lower_bound"], r["q_upper_bound"])
        for lower, upper in concessions:
            low = max(math.ceil(authored[0] * lower), 1)
            high = max(math.floor(authored[1] * upper), 1)
            if low < high:
                break
        else:  # pragma: no cover - the last concession is the authored range
            low, high = authored
        r["q_lower_bound"], r["q_upper_bound"] = low, high
        r["narrowed"] = None if (low, high) == authored else authored
        r["factors"] = (lower, upper)
    return reaches


def choose_q_grid(low: int, high: int) -> int:
    """The discharge grid this reach's range can carry."""
    for grid in sorted(
        (g for g in Q_GRID_MENU if g <= Q_GRID_SEED_CEILING), reverse=True
    ):
        if (high - low) // grid >= Q_GRID_MIN_LINES:
            return grid
    return min(Q_GRID_MENU)


def snap_to_grid(value: int, grid: int) -> int:
    """The nearest grid value, never zero."""
    return max(round(value / grid) * grid, grid)


def place_on_q_grid(reaches: list[dict]) -> list[dict]:
    """Put every reach's bounds and opening step on its own discharge grid."""
    for r in reaches:
        low, high = r.get("q_lower_bound"), r.get("q_upper_bound")
        if low is None or high is None:
            continue
        grid = choose_q_grid(low, high)
        r[Q_GRID_FIELD] = grid
        r["q_lower_bound"] = snap_to_grid(low, grid)
        r["q_upper_bound"] = snap_to_grid(high, grid)
        if r["q_upper_bound"] <= r["q_lower_bound"]:
            r["q_upper_bound"] = r["q_lower_bound"] + grid
        if r.get(DQ_STEP_FIELD):
            r[DQ_STEP_FIELD] = snap_to_grid(r[DQ_STEP_FIELD], grid)
    return reaches


def reaches_to_author(aoi: dict, own: set[str], seeded: set[str], covered: set[str], flows: str) -> set[str]:
    """The reaches of the AOI's own network that its flow statistics cover."""
    unseeded = sorted(own - seeded)
    if unseeded:
        sys.exit(
            f"{len(unseeded)} reach(es) of this AOI's network are not in reach_network: {unseeded[:20]}\n"
            f"Seed the network first: seed.py network {aoi['_location']}"
        )
    wanted = {r for r in own if flow_statistics.flow_id(r) in covered}
    if not wanted:
        sys.exit(f"{flows} covers no reach of this AOI's network")
    return wanted


def check_downstream_closed(reaches: list[dict], authored: set[str], intended: set[str]) -> None:
    """Refuse to author reaches that cannot finish."""
    satisfied = authored | intended
    by_id = {r["reach_id"]: r for r in reaches}
    dangling = [
        (reach_id, by_id[reach_id]["reach_to_id"])
        for reach_id in sorted(authored)
        if by_id[reach_id]["reach_to_id"] is not None
        and by_id[reach_id]["reach_to_id"] not in satisfied
    ]
    if dangling:
        lines = "\n".join(f"    {r} -> {ds}" for r, ds in dangling[:20])
        sys.exit(
            "not downstream-closed; these reaches would wait forever on a\n"
            f"downstream reach nothing is authored for:\n{lines}"
        )


def defaults_from_settings() -> dict:
    """The defaults row, from the system-wide settings."""
    return {
        "sdr_commit": settings.sdr_commit,
        "grid_resolution": settings.grid_resolution,
        "epsg_code": settings.epsg_code,
        "dem_source": aoi_config.job_address("dem_source", settings.dem_source, "settings"),
        "lulc_source": aoi_config.job_address("lulc_source", settings.lulc_source, "settings"),
        "lulc_lookup": aoi_config.job_address("lulc_lookup", settings.lulc_lookup, "settings"),
        "solver": settings.solver,
        "ld_ds_z_delta": settings.ld_ds_z_delta,
        "ld_q_max_depth_increase_range": settings.ld_q_max_depth_increase_range,
        "ld_q_median_depth_increase_range": settings.ld_q_median_depth_increase_range,
        "ld_q_flooded_area_prcnt_increase_range": settings.ld_q_flooded_area_prcnt_increase_range,
    }


def lookup_readable(address: str) -> bool:
    """Whether the lookup can be read."""
    return storage.read_json(address) is not None


_NETWORK = "SELECT reach_id, reach_to_id FROM reach_network ORDER BY reach_id"

_DEFAULTS = """
    INSERT INTO desired_state_defaults
        (id, sdr_commit, grid_resolution, epsg_code, dem_source, lulc_source,
         lulc_lookup, solver, ld_ds_z_delta,
         ld_q_max_depth_increase_range, ld_q_median_depth_increase_range,
         ld_q_flooded_area_prcnt_increase_range)
    VALUES (1, %(sdr_commit)s, %(grid_resolution)s, %(epsg_code)s, %(dem_source)s,
            %(lulc_source)s, %(lulc_lookup)s, %(solver)s, %(ld_ds_z_delta)s,
            %(ld_q_max_depth_increase_range)s, %(ld_q_median_depth_increase_range)s,
            %(ld_q_flooded_area_prcnt_increase_range)s)
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

_DEFAULT_TYPES = {
    "sdr_commit": "text",
    "grid_resolution": "double precision",
    "epsg_code": "integer",
    "dem_source": "text",
    "lulc_source": "text",
    "lulc_lookup": "text",
    "solver": "text",
    "ld_ds_z_delta": "double precision",
    "ld_q_max_depth_increase_range": "numrange",
    "ld_q_median_depth_increase_range": "numrange",
    "ld_q_flooded_area_prcnt_increase_range": "numrange",
}


def defaults_changes(proposed: dict, conn) -> list[tuple[str, str, str]] | None:
    """(column, in force, from settings) for each default that differs; None when no row exists yet."""
    select = ", ".join(
        f"{column}::text AS {column}, {column} IS DISTINCT FROM %({column})s::{kind} AS {column}__changed"
        for column, kind in _DEFAULT_TYPES.items()
    )
    row = db.one(f"SELECT {select} FROM desired_state_defaults WHERE id = 1", proposed, conn=conn)
    if row is None:
        return None
    return [(c, row[c], str(proposed[c])) for c in _DEFAULT_TYPES if row[f"{c}__changed"]]


def author_defaults(yes: bool) -> None:
    """Write desired_state_defaults from the system-wide settings."""
    proposed = defaults_from_settings()
    with db.connect() as conn:
        changes = defaults_changes(proposed, conn)
        reaches = db.one("SELECT count(*) AS n FROM desired_state", conn=conn)["n"]
        if changes is None:
            conn.execute(_DEFAULTS, proposed)
            print("defaults        written (no row existed)")
        elif not changes:
            print("defaults        unchanged: the settings match the row in force")
        else:
            print("defaults        the settings differ from the row in force:")
            for column, current, new in changes:
                print(f"  {column:<40} {current}  ->  {new}")
            if not yes:
                sys.exit(
                    f"\nWriting this re-checks all {reaches} reach(es) with intent, in every AOI.\n"
                    "Nothing written. Rerun with --yes to write it."
                )
            conn.execute(_DEFAULTS, proposed)
            print(f"written         {reaches} reach(es) will be re-checked")

    for column in _DEFAULT_TYPES:
        print(f"  {column:<40} {proposed[column]}")
    if not lookup_readable(proposed["lulc_lookup"]):
        print(
            f"\nNote: no land-cover lookup at {proposed['lulc_lookup']} yet. Reaches that fall back\n"
            "      to it cannot be authored until it is staged in source_data."
        )


_AUTHOR = """
    INSERT INTO desired_state
        (reach_id, q_lower_bound, q_upper_bound, initial_dq_step_for_nd,
         q_grid_resolution, dem_source, lulc_source, lulc_lookup)
    VALUES (%(reach_id)s, %(q_lower_bound)s, %(q_upper_bound)s, %(initial_dq_step_for_nd)s,
            %(q_grid_resolution)s, %(dem_source)s, %(lulc_source)s, %(lulc_lookup)s)
    ON CONFLICT (reach_id) DO UPDATE SET
        q_lower_bound          = EXCLUDED.q_lower_bound,
        q_upper_bound          = EXCLUDED.q_upper_bound,
        initial_dq_step_for_nd = EXCLUDED.initial_dq_step_for_nd,
        q_grid_resolution      = EXCLUDED.q_grid_resolution,
        dem_source             = EXCLUDED.dem_source,
        lulc_source            = EXCLUDED.lulc_source,
        lulc_lookup            = EXCLUDED.lulc_lookup
"""


def author(aoi: dict) -> None:
    """Write one desired_state row per reach the AOI authors."""
    defaults = db.one("SELECT lulc_lookup FROM desired_state_defaults WHERE id = 1")
    if defaults is None:
        sys.exit("desired_state_defaults has no row yet, so the database is not set up; run `just setup-db`")
    sources = {key: aoi.get(key) for key in ("dem_source", "lulc_source", "lulc_lookup")}
    lookup = sources["lulc_lookup"] or defaults["lulc_lookup"]
    if not lookup_readable(lookup):
        whose = "this AOI's" if sources["lulc_lookup"] else "the default in force"
        sys.exit(f"No land-cover lookup at {lookup} ({whose}); stage it in source_data first")

    factors = aoi.get("q_bound_factors")
    if factors is not None and not (
        isinstance(factors, list) and len(factors) == 2 and factors[0] >= 1 and 0 < factors[1] <= 1
    ):
        sys.exit("`q_bound_factors` must be [lower >= 1, 0 < upper <= 1]")

    own = aoi_config.network_reach_ids(aoi)
    flows = flow_statistics.for_aoi(aoi)
    with tempfile.TemporaryDirectory() as tmp, db.connect() as conn:
        bounds = read_q_bounds(aoi_config.local_copy(flows.location, Path(tmp)), flows)
        covered = set(bounds.index)
        reaches = db.query(_NETWORK, conn=conn)
        intended = {r["reach_id"] for r in db.query("SELECT reach_id FROM desired_state", conn=conn)}
        authored = reaches_to_author(aoi, own, {r["reach_id"] for r in reaches}, covered, flows.location)
        check_downstream_closed(reaches, authored, intended)
        authoring = load_q_bounds(
            bounds,
            [{"reach_id": r["reach_id"]} for r in reaches if r["reach_id"] in authored],
        )
        if factors is not None:
            authoring = narrow(authoring, *factors)
        authoring = place_on_q_grid(authoring)

        before = {
            r["reach_id"]: r["revision"]
            for r in db.query("SELECT reach_id, revision FROM desired_state", conn=conn)
        }
        with conn.cursor() as cur:
            cur.executemany(_AUTHOR, [r | sources for r in authoring])
        after = {
            r["reach_id"]: r["revision"]
            for r in db.query(
                "SELECT reach_id, revision FROM desired_state WHERE reach_id = ANY(%s)",
                (sorted(authored),),
                conn=conn,
            )
        }

    new = [r for r in after if r not in before]
    moved = [r for r in after if r in before and after[r] != before[r]]

    print("\ndefaults        as in force in the database (`just author-defaults` changes them)")
    for key, value in sources.items():
        print(f"{key:<16}{value or 'default'}")
    print(
        f"flows           {flow_statistics.describe(flows)} "
        f"[{flows.reach_id_column}: {flows.q_lower_column} .. {flows.q_upper_column}]"
    )
    print(f"authored        {len(authored)} of {len(own)} reach(es) in this AOI's network")
    if len(own) > len(authored):
        print(f"  no flows      {len(own) - len(authored)} (not covered by {flows.location})")
    print(f"  new           {len(new)}")
    print(f"  revision moved{len(moved):>3}")
    print(f"  unchanged     {len(after) - len(new) - len(moved)}")
    if factors is not None:
        print(f"bounds          narrowed by {factors[0]}x lower, {factors[1]}x upper (q_bound_factors)")

    if len(authoring) > REPORT_EACH_REACH_UP_TO:
        return
    print()
    for r in sorted(authoring, key=lambda r: r["reach_id"]):
        rng = f"{r['q_lower_bound']}-{r['q_upper_bound']} cms"
        was, kept = r.get("narrowed"), r.get("factors")
        if kept is None:
            note = ""
        elif was is None:
            note = "  (no room to narrow)"
        elif kept[0] == 1.0 and factors[0] != 1.0:
            note = f"  (from {was[0]}-{was[1]}, lower factor given up for room)"
        else:
            note = f"  (from {was[0]}-{was[1]})"
        print(f"  {r['reach_id']}  {rng:>16}, dq {r[DQ_STEP_FIELD]:>3}, grid {r[Q_GRID_FIELD]:>3}{note}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="what", required=True)
    defaults = sub.add_parser("defaults", help="write desired_state_defaults from the system-wide settings")
    defaults.add_argument(
        "--yes", action="store_true", help="write a change to the row in force (re-checks every reach)"
    )
    one = sub.add_parser("aoi", help="author one AOI's own network")
    aoi_config.add_argument(one)
    args = ap.parse_args()

    if args.what == "defaults":
        author_defaults(args.yes)
        return

    aoi = aoi_config.load(args.aoi_config_path)
    print(f"aoi config      {aoi_config.describe(aoi)}")
    author(aoi)


if __name__ == "__main__":
    main()
