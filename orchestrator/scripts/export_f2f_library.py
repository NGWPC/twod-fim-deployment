#!/usr/bin/env python
"""Copy the depth grids a scenarios database names into a flows2fim library.

Reads what export_f2f_db.py wrote and copies only that. The database is the
list of grids the loop adopted; the results tree also holds runs it discarded,
so anything that walked the tree instead would build a library the controls
table cannot be trusted against -- see export_f2f_db.py for what that costs.

The layout is flows2fim's: `<library>/<reach>/z_<stage>/f_<flow>.tif`, with
`z_nd` for the normal-depth grids. Grids are copied rather than linked because
the library is mounted into the flows2fim container on its own, without the
results tree behind it.

Copying is a sync, not a fill: a grid whose source has changed size or mtime is
re-copied, so re-running after a reach was re-simulated updates the library
rather than leaving a stale raster behind. Grids in the library that the
database does not name are reported, and removed only with --prune.

A scenario whose grid is missing has its `map_exists` set to 0. That is the
column flows2fim controls consults before choosing a scenario, so a missing
grid narrows the choice instead of producing a controls row pointing at
nothing.

Usage:
    uv run python scripts/export_f2f_library.py
    uv run python scripts/export_f2f_library.py --prune
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

from export_f2f_db import (
    DEFAULT_F2F_DIR,
    DEFAULT_SCENARIOS_DB,
    library_grid_name,
    library_stage_dir,
)

DEFAULT_LIBRARY_DIR = DEFAULT_F2F_DIR / "library"


def read_sources(scenarios_db: Path) -> tuple[list[dict], Path]:
    """Every scenario's grid location, and the results root they are under."""
    with sqlite3.connect(scenarios_db) as connection:
        connection.row_factory = sqlite3.Row
        provenance = connection.execute(
            "SELECT results_root, exported_at FROM export_provenance"
        ).fetchone()
        sources = [
            dict(row)
            for row in connection.execute(
                """
                SELECT reach_id, us_flow, ds_wse, boundary_condition, depth_grid
                FROM scenario_sources
                ORDER BY reach_id, boundary_condition, ds_wse, us_flow
                """
            )
        ]
    print(f"Read {len(sources)} scenarios exported at {provenance['exported_at']}.")
    return sources, Path(provenance["results_root"])


def needs_copy(source_path: Path, destination: Path) -> bool:
    """Whether the library copy is absent or no longer matches its source."""
    if not destination.exists():
        return True
    source_stat, destination_stat = source_path.stat(), destination.stat()
    return (
        source_stat.st_size != destination_stat.st_size
        or int(source_stat.st_mtime) != int(destination_stat.st_mtime)
    )


def copy_library(
    sources: list[dict], results_dir: Path, library_dir: Path
) -> tuple[list[dict], set[Path]]:
    """Copy each named grid into place. Returns the ones that were missing."""
    copied, current, missing = 0, set(), []
    for source in sources:
        source_path = results_dir / source["depth_grid"]
        destination = (
            library_dir
            / str(source["reach_id"])
            / library_stage_dir(source["boundary_condition"], source["ds_wse"])
            / library_grid_name(source["us_flow"])
        )
        if not source_path.exists():
            missing.append(source)
            continue

        current.add(destination)
        if needs_copy(source_path, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            # copy2 keeps the mtime, which is what makes the next run's
            # comparison meaningful rather than a full re-copy every time.
            shutil.copy2(source_path, destination)
            copied += 1

    print(f"Copied {copied} depth grids to {library_dir}; {len(current) - copied} already current.")
    return missing, current


def report_strays(library_dir: Path, current: set[Path], prune: bool) -> int:
    """Grids in the library the database does not name.

    Left in place unless asked otherwise: they are usually the remains of an
    earlier export, but a library is also a thing people put grids into by
    hand, and deleting those unasked would be the wrong default.
    """
    strays = sorted(set(library_dir.glob("*/z_*/f_*.tif")) - current)
    if not strays:
        return 0

    verb = "Removed" if prune else "Found"
    print(f"{verb} {len(strays)} library grids the database does not name:")
    for stray in strays[:10]:
        print(f"  {stray.relative_to(library_dir)}")
    if len(strays) > 10:
        print(f"  ... and {len(strays) - 10} more")
    if prune:
        for stray in strays:
            stray.unlink()
    else:
        print("  re-run with --prune to remove them")
    return len(strays)


def mark_missing(scenarios_db: Path, missing: list[dict]) -> None:
    """Set map_exists = 0 for scenarios whose depth grid did not turn up."""
    if not missing:
        return
    print(f"{len(missing)} scenarios have no depth grid; setting map_exists = 0:")
    for source in missing[:10]:
        print(f"  reach {source['reach_id']} {source['boundary_condition']} "
              f"q={source['us_flow']:g} z={source['ds_wse']:g}: {source['depth_grid']}")
    if len(missing) > 10:
        print(f"  ... and {len(missing) - 10} more")

    with sqlite3.connect(scenarios_db) as connection:
        connection.executemany(
            """
            UPDATE scenarios SET map_exists = 0
            WHERE reach_id = ? AND us_flow = ? AND ds_wse = ? AND boundary_condition = ?
            """,
            [
                (s["reach_id"], s["us_flow"], s["ds_wse"], s["boundary_condition"])
                for s in missing
            ],
        )


def export(scenarios_db: Path, results_dir: Path | None, library_dir: Path, prune: bool) -> None:
    sources, recorded_results_dir = read_sources(scenarios_db)
    results_dir = results_dir or recorded_results_dir
    if not results_dir.is_dir():
        sys.exit(f"No such results directory: {results_dir}")
    print(f"Copying from {results_dir}...")

    library_dir.mkdir(parents=True, exist_ok=True)
    missing, current = copy_library(sources, results_dir, library_dir)
    report_strays(library_dir, current, prune)
    mark_missing(scenarios_db, missing)
    print("Run create_aep_f2f_vrts.py next to build controls and VRTs from this library.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_SCENARIOS_DB,
        help="scenarios database export_f2f_db.py wrote "
        "(default: testdata/outputs/f2f/scenarios.db)",
    )
    ap.add_argument(
        "--lib",
        type=Path,
        default=DEFAULT_LIBRARY_DIR,
        help="library to write (default: testdata/outputs/f2f/library)",
    )
    ap.add_argument(
        "--results-dir",
        type=Path,
        help="results tree to copy from (default: the one recorded in the database)",
    )
    ap.add_argument(
        "--prune",
        action="store_true",
        help="delete library grids the database does not name",
    )
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"No such scenarios database: {args.db}; run export_f2f_db.py first")

    print(f"db       {args.db}")
    print(f"library  {args.lib}\n")
    export(args.db, args.results_dir, args.lib, args.prune)


if __name__ == "__main__":
    main()
