#!/usr/bin/env python
"""Export the materialized reaches as a flows2fim scenarios database.

flows2fim reads two tables: `scenarios`, one row per depth grid it may choose
from, and `network`, how the reaches drain into each other. This writes both
from the loop's own record of what is materialized.

The database is the source, and the reason is that storage is not. A reach's
adopted library is `materialized_nd_runs.q_set` -- "the smallest set meeting
the authored ld_q_* resolution" -- and the schema says outright that storage
may hold more runs left behind by an earlier sweep, which the loop ignores. The
KWSE stage libraries are then built at those adopted discharges and no others.
So walking the results tree for `scenario_manifest.json` would adopt the
surplus as well, and every surplus discharge is one with a normal-depth run and
no stage library. flows2fim matches a forecast on flow before stage, so a
forecast landing nearest a surplus discharge would find only the nd row and map
a backwater-controlled reach at normal depth. Measured on a 41-reach network:
24 reaches held surplus runs, and 16 of 31 interior reaches degraded that way at
the 50yr forecast -- while passing `flows2fim validate` 1:1, because the
surplus was self-consistent. Only the database tells the two apart.

Alongside the two tables flows2fim requires, this writes `scenario_sources`:
where each scenario's depth grid lives, relative to the results root. That is
what lets export_f2f_library.py copy the library without consulting Postgres
again, and it is the reason the export needs a results root at all -- the
`nd=<slope>` folder is the one part of a scenario's address the database cannot
supply, because the job computes the slope from the reach's own DEM. It is
discovered by listing, exactly as `storage.nd_library_path` does.

Usage:
    uv run python scripts/export_f2f_db.py
    uv run python scripts/export_f2f_db.py --results-dir /path/to/results
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from recon import db, identity
from recon.config import settings

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"
DEFAULT_RESULTS_DIR = TESTDATA / "outputs" / "results"
# Everything flows2fim reads or writes lives under one directory, because the
# container mounts exactly one and every path it is handed has to be inside it.
DEFAULT_F2F_DIR = TESTDATA / "outputs" / "f2f"
DEFAULT_SCENARIOS_DB = DEFAULT_F2F_DIR / "scenarios.db"

DEPTH_GRID_FILENAME = "depth.tif"


def library_stage_dir(boundary_condition: str, ds_wse: float) -> str:
    """The `z_…` folder a scenario's depth grid belongs in.

    flows2fim keys a library on the stage IMPOSED downstream, spelled with the
    decimal point as an underscore. Rendered through identity.kwse_folder so
    the library and the results tree round a stage identically -- the folder
    names have to agree for a grid to be found where the controls table says.
    """
    if boundary_condition == "nd":
        return "z_nd"
    return "z_" + identity.kwse_folder(ds_wse).removeprefix("kwse=").replace(".", "_")


def library_grid_name(us_flow: float) -> str:
    """The `f_….tif` name a scenario's depth grid takes in the library."""
    return f"f_{identity.q_folder(int(us_flow)).removeprefix('q=')}.tif"


def nd_slope_folder(results_dir: Path, reach_id: int, model_id: str, run_hash: str) -> str:
    """The single `nd=<slope>` folder under one reach's run base.

    Discovered rather than predicted, for the reason `storage.nd_library_path`
    gives: the job computes the slope from the reach's own DEM, so nothing here
    can know it in advance.
    """
    base = results_dir / f"reach={reach_id}" / identity_hash(model_id) / run_hash
    found = sorted(path.name for path in base.glob("nd=*") if path.is_dir())
    if len(found) != 1:
        raise FileNotFoundError(
            f"expected exactly one nd= folder under {base}, found {found}"
        )
    return found[0]


def identity_hash(model_id: str) -> str:
    """The identity half of a model_id, which is what names the results folder."""
    return model_id.partition("_")[0]


def get_nd_scenarios(results_dir: Path) -> tuple[list[tuple], list[tuple]]:
    """Scenario rows and grid locations for every reach with an ND library.

    Iterates `q_set`, the adopted discharges, and reads the upstream-end stage
    for each from `us_min_wse_curve`. The curve is observed from storage and so
    may carry surplus discharges too; indexing it by the adopted q is what
    leaves those behind.
    """
    rows, sources = [], []
    for reach in db.query(
        """
        SELECT reach_id, model_id, run_identity_hash, q_set, us_min_wse_curve
        FROM materialized_nd_runs
        ORDER BY reach_id
        """
    ):
        reach_id = reach["reach_id"]
        wse_at = {int(point["q"]): float(point["wse"]) for point in reach["us_min_wse_curve"]}
        slope_folder = nd_slope_folder(
            results_dir, reach_id, reach["model_id"], reach["run_identity_hash"]
        )

        for q in reach["q_set"]:
            if q not in wse_at:
                raise ValueError(
                    f"reach {reach_id} adopted q={q} but its us_min_wse_curve has no entry"
                )
            rows.append((reach_id, q, None, wse_at[q], None, 0.0, "nd", 1))
            sources.append(
                (
                    reach_id,
                    q,
                    0.0,
                    "nd",
                    str(
                        Path(f"reach={reach_id}")
                        / identity_hash(reach["model_id"])
                        / reach["run_identity_hash"]
                        / slope_folder
                        / identity.q_folder(q)
                        / DEPTH_GRID_FILENAME
                    ),
                )
            )

    return rows, sources


def get_kwse_scenarios() -> tuple[list[tuple], list[tuple]]:
    """Scenario rows and grid locations for every reach with a stage library.

    A KWSE run has two stages and they are not interchangeable: `bc` is the one
    imposed at this reach's downstream end, which names the folder and is what
    flows2fim controls on; `wse` is the one achieved at its upstream end, which
    the reach above matches against. The scenario index is the only record that
    pairs them.
    """
    rows, sources = [], []
    for reach in db.query(
        """
        SELECT reach_id, model_id, run_identity_hash, scenario_index
        FROM materialized_kwse_runs
        ORDER BY reach_id
        """
    ):
        reach_id = reach["reach_id"]
        for discharge in reach["scenario_index"]:
            q = int(discharge["q"])
            for run in discharge["runs"]:
                imposed, achieved = float(run["bc"]), float(run["wse"])
                rows.append((reach_id, q, None, achieved, None, imposed, "kwse", 1))
                sources.append(
                    (
                        reach_id,
                        q,
                        imposed,
                        "kwse",
                        str(
                            Path(f"reach={reach_id}")
                            / identity_hash(reach["model_id"])
                            / reach["run_identity_hash"]
                            / identity.kwse_folder(imposed)
                            / identity.q_folder(q)
                            / DEPTH_GRID_FILENAME
                        ),
                    )
                )

    return rows, sources


def get_network_rows(reach_ids: set[int]) -> list[tuple[int, int | None]]:
    """Downstream links for the reaches being exported.

    A link is kept only if its downstream reach is also in the export. A reach
    can be materialized before the one below it is, and pointing flows2fim at a
    reach with no scenarios would leave it looking for controls that cannot
    exist; treating it as an outlet is what the loop does with a clipped network
    edge, and an outlet is where a traversal legitimately starts.
    """
    rows, clipped = [], []
    for link in db.query(
        "SELECT reach_id, reach_to_id FROM reach_network ORDER BY reach_id"
    ):
        reach_id = link["reach_id"]
        if reach_id not in reach_ids:
            continue
        downstream_id = link["reach_to_id"]
        if downstream_id is not None and downstream_id not in reach_ids:
            clipped.append((reach_id, downstream_id))
            downstream_id = None
        rows.append((reach_id, downstream_id))

    for reach_id, downstream_id in clipped:
        print(f"  {reach_id} -> {downstream_id} is not materialized; written as an outlet")
    return rows


def write_db(
    scenarios_db: Path,
    scenario_rows: list[tuple],
    source_rows: list[tuple],
    network_rows: list[tuple[int, int | None]],
    results_dir: Path,
) -> None:
    """Write the two tables flows2fim reads, plus where each grid came from."""
    scenarios_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(scenarios_db) as connection:
        for table in ("scenarios", "network", "scenario_sources", "export_provenance"):
            connection.execute(f"DROP TABLE IF EXISTS {table}")

        connection.execute(
            """
            CREATE TABLE scenarios (
                reach_id INTEGER NOT NULL,
                us_flow REAL NOT NULL,
                us_depth REAL,
                us_wse REAL NOT NULL,
                ds_depth REAL,
                ds_wse REAL NOT NULL,
                boundary_condition TEXT NOT NULL
                    CHECK(boundary_condition IN ('nd', 'kwse')),
                map_exists INTEGER NOT NULL CHECK(map_exists IN (0, 1)),
                UNIQUE(reach_id, us_flow, ds_wse, boundary_condition)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE network (
                reach_id INTEGER PRIMARY KEY,
                updated_to_id INTEGER
            )
            """
        )
        # Not part of the flows2fim contract. Keyed exactly like the scenarios
        # UNIQUE so the library export can join the two without guessing.
        connection.execute(
            """
            CREATE TABLE scenario_sources (
                reach_id INTEGER NOT NULL,
                us_flow REAL NOT NULL,
                ds_wse REAL NOT NULL,
                boundary_condition TEXT NOT NULL,
                depth_grid TEXT NOT NULL,
                UNIQUE(reach_id, us_flow, ds_wse, boundary_condition)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE export_provenance (
                exported_at TEXT NOT NULL,
                results_root TEXT NOT NULL,
                source_database TEXT NOT NULL
            )
            """
        )

        connection.executemany(
            """
            INSERT INTO scenarios (
                reach_id, us_flow, us_depth, us_wse, ds_depth, ds_wse,
                boundary_condition, map_exists
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            scenario_rows,
        )
        connection.executemany(
            "INSERT INTO network (reach_id, updated_to_id) VALUES (?, ?)",
            network_rows,
        )
        connection.executemany(
            """
            INSERT INTO scenario_sources (
                reach_id, us_flow, ds_wse, boundary_condition, depth_grid
            ) VALUES (?, ?, ?, ?, ?)
            """,
            source_rows,
        )
        connection.execute(
            "INSERT INTO export_provenance (exported_at, results_root, source_database)"
            " VALUES (?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                str(results_dir.resolve()),
                settings.postgres_db,
            ),
        )


def export(results_dir: Path, scenarios_db: Path) -> None:
    print("Reading materialized reaches...")
    nd_rows, nd_sources = get_nd_scenarios(results_dir)
    kwse_rows, kwse_sources = get_kwse_scenarios()
    scenario_rows = nd_rows + kwse_rows
    if not scenario_rows:
        sys.exit("No materialized runs to export; has the loop run?")

    reach_ids = {row[0] for row in scenario_rows}
    network_rows = get_network_rows(reach_ids)
    outlets = sum(1 for _, downstream_id in network_rows if downstream_id is None)

    write_db(scenarios_db, scenario_rows, nd_sources + kwse_sources, network_rows, results_dir)

    print(
        f"Wrote {len(scenario_rows)} scenarios ({len(nd_rows)} nd, {len(kwse_rows)} kwse) "
        f"across {len(reach_ids)} reaches to {scenarios_db}."
    )
    print(f"Wrote {len(network_rows)} network rows, {outlets} of them outlets.")
    print("Run export_f2f_library.py next to copy the depth grids this names.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="results tree the depth grids live in, recorded in the database "
        "and read only to discover each reach's nd=<slope> folder "
        "(default: testdata/outputs/results)",
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_SCENARIOS_DB,
        help="scenarios database to write (default: testdata/outputs/f2f/scenarios.db)",
    )
    args = ap.parse_args()

    if not args.results_dir.is_dir():
        sys.exit(f"No such results directory: {args.results_dir}")

    print(f"results  {args.results_dir}")
    print(f"db       {args.db}")
    print(f"source   {settings.postgres_db} on {settings.postgres_host}\n")
    export(args.results_dir, args.db)


if __name__ == "__main__":
    main()
