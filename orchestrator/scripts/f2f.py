#!/usr/bin/env python
"""Publish an AOI's materialized reaches for flows2fim, into a local folder.

Three steps, each a command, run in order by `just f2f`:

  scenarios <aoi-config-path> <out-dir>
            <out-dir>/scenarios.db: the `scenarios` and `network` tables
            flows2fim reads, for the reaches of the AOI's `network` that are
            materialized, and where each depth grid is in storage
            <out-dir>/start_reaches.csv: the reaches flows2fim controls start
            from, and the stage each starts at
  library   <out-dir> [--prune]
            <out-dir>/library/<reach>/z_<stage>/f_<flow>.tif: the depth grids
            scenarios.db names, downloaded
  aep       <aoi-config-path> <out-dir>
            <out-dir>/aep/<column>/: for each AEP column of the AOI's flow
            statistics, a forecast, flows2fim controls for it from
            start_reaches.csv, and a depth VRT

Everything lands under <out-dir>, because flows2fim runs in a container that
mounts exactly that one directory. Several AOIs are several out-dirs.

The database, not storage, says what a reach's library is. Its adopted
discharges are `materialized_nd_runs.q_set`, and the schema says outright that
storage may hold more runs left behind by an earlier sweep, which the loop
ignores; the KWSE stage libraries are built at the adopted discharges and no
others. So walking the results tree would adopt the surplus as well, and every
surplus discharge is one with a normal-depth run and no stage library. flows2fim
matches a forecast on flow before stage, so a forecast landing nearest a
surplus discharge would find only the nd row and map a backwater-controlled
reach at normal depth. Measured on a 41-reach network: 24 reaches held surplus
runs, and 16 of 31 interior reaches degraded that way at the 50yr forecast --
while passing `flows2fim validate` 1:1, because the surplus was self-consistent.

The one part of a grid's address the database cannot supply is the `nd=<slope>`
folder, because the job computes the slope from the reach's own DEM. It is
discovered in storage, exactly as the loop does (storage.nd_library_path).

Discharges are cms, the unit the whole system is authored in -- desired_state
bounds, q_set, the q= folders. flows2fim's help says cfs, but it never converts:
it matches a forecast value against `us_flow` in the scenarios table, so a cms
forecast against a cms library is what agrees.

Usage:
    uv run python scripts/f2f.py scenarios <aoi-config-path> <out-dir>
    uv run python scripts/f2f.py library <out-dir> [--prune]
    uv run python scripts/f2f.py aep <aoi-config-path> <out-dir> [--image IMAGE]
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
from botocore.exceptions import ClientError

import aoi_config
import flow_statistics
from recon import db, identity, storage
from recon.config import settings

SCENARIOS_DB = "scenarios.db"
START_REACHES = "start_reaches.csv"
LIBRARY_DIR = "library"
AEP_DIR = "aep"
DEPTH_GRID_FILENAME = "depth.tif"

IMAGE = "ghcr.io/ngwpc/flows2fim:0.5.0"
# The stage flows2fim controls gives a start reach: normal depth.
START_STAGE = "nd"
CONTAINER_OUT = Path("/out")


# --- scenarios -----------------------------------------------------------


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


def nd_scenarios(reach_ids: set[int]) -> tuple[list[tuple], list[tuple]]:
    """Scenario rows and grid addresses for each of these reaches with an ND library.

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
        WHERE reach_id = ANY(%s)
        ORDER BY reach_id
        """,
        (sorted(reach_ids),),
    ):
        reach_id = reach["reach_id"]
        library = storage.nd_library_path(reach_id, reach["model_id"], reach["run_identity_hash"])
        if library is None:
            base = storage.run_base_path(reach_id, reach["model_id"], reach["run_identity_hash"])
            sys.exit(f"reach {reach_id} is materialized, but {base} does not hold exactly one nd= folder")
        wse_at = {int(point["q"]): float(point["wse"]) for point in reach["us_min_wse_curve"]}
        for q in reach["q_set"]:
            if q not in wse_at:
                sys.exit(f"reach {reach_id} adopted q={q} but its us_min_wse_curve has no entry")
            rows.append((reach_id, q, None, wse_at[q], None, 0.0, "nd", 1))
            sources.append(
                (reach_id, q, 0.0, "nd", f"{library}/{identity.q_folder(q)}/{DEPTH_GRID_FILENAME}")
            )
    return rows, sources


def kwse_scenarios(reach_ids: set[int]) -> tuple[list[tuple], list[tuple]]:
    """Scenario rows and grid addresses for each of these reaches with a stage library.

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
        WHERE reach_id = ANY(%s)
        ORDER BY reach_id
        """,
        (sorted(reach_ids),),
    ):
        reach_id = reach["reach_id"]
        base = storage.run_base_path(reach_id, reach["model_id"], reach["run_identity_hash"])
        for discharge in reach["scenario_index"]:
            q = int(discharge["q"])
            for run in discharge["runs"]:
                imposed, achieved = float(run["bc"]), float(run["wse"])
                rows.append((reach_id, q, None, achieved, None, imposed, "kwse", 1))
                sources.append(
                    (
                        reach_id, q, imposed, "kwse",
                        f"{base}/{identity.kwse_folder(imposed)}/{identity.q_folder(q)}/{DEPTH_GRID_FILENAME}",
                    )
                )
    return rows, sources


def network_rows(links: list[tuple[int, int | None]], reach_ids: set[int]) -> tuple[list[tuple], list[tuple]]:
    """Downstream links for the reaches being exported, and the ones cut.

    A link is kept only if its downstream reach is also in the export. A reach
    can be materialized before the one below it is, and the AOI's network can
    end at a reach that drains out of it; pointing flows2fim at a reach with no
    scenarios would leave it looking for controls that cannot exist. Treating
    it as an outlet is what the loop does with a clipped network edge, and an
    outlet is where a traversal legitimately starts.
    """
    rows, cut = [], []
    for reach_id, downstream_id in sorted(links):
        if reach_id not in reach_ids:
            continue
        if downstream_id is not None and downstream_id not in reach_ids:
            cut.append((reach_id, downstream_id))
            downstream_id = None
        rows.append((reach_id, downstream_id))
    return rows, cut


def write_start_reaches(path: Path, links: list[tuple[int, int | None]]) -> list[int]:
    """Write the reaches controls start from, each at normal depth, for `controls -scsv`.

    Controls are traced upstream from the reaches with nowhere left to drain in
    the export: the true terminals, and the reaches network_rows cut off from a
    downstream neighbour that is not exported. Normal depth is the only start a
    terminal has, since it has no stage library. For a cut reach it is the
    fallback: the stage its real downstream neighbour would hold is unknown
    until that neighbour is exported, so the reach and everything above it are
    mapped as if it drained freely.

    A file rather than a list on the command line, so a large AOI's starts do not
    run into argument limits, and so what a map started from is kept beside it
    and can be edited before aep runs.
    """
    starts = [reach_id for reach_id, downstream_id in links if downstream_id is None]
    pd.DataFrame({"reach_id": starts, "control_stage": [START_STAGE] * len(starts)}).to_csv(path, index=False)
    return starts


def write_scenarios_db(
    path: Path,
    scenario_rows: list[tuple],
    source_rows: list[tuple],
    links: list[tuple[int, int | None]],
    aoi: dict,
) -> None:
    """Write the two tables flows2fim reads, plus where each grid is and what was exported."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
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
        connection.execute("CREATE TABLE network (reach_id INTEGER PRIMARY KEY, updated_to_id INTEGER)")
        # Not part of the flows2fim contract. Keyed exactly like the scenarios
        # UNIQUE so the library step can join the two without guessing, and
        # holding full s3:// addresses so it needs no database connection.
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
                aoi_config TEXT NOT NULL,
                source_database TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO scenarios (
                reach_id, us_flow, us_depth, us_wse, ds_depth, ds_wse, boundary_condition, map_exists
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            scenario_rows,
        )
        connection.executemany("INSERT INTO network (reach_id, updated_to_id) VALUES (?, ?)", links)
        connection.executemany(
            """
            INSERT INTO scenario_sources (reach_id, us_flow, ds_wse, boundary_condition, depth_grid)
            VALUES (?, ?, ?, ?, ?)
            """,
            source_rows,
        )
        connection.execute(
            "INSERT INTO export_provenance (exported_at, aoi_config, source_database) VALUES (?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                aoi_config.describe(aoi),
                f"{settings.postgres_db} on {settings.postgres_host}",
            ),
        )


def export_scenarios(aoi: dict, out_dir: Path) -> None:
    own = aoi_config.network_reach_ids(aoi)
    nd_rows, nd_sources = nd_scenarios(own)
    kwse_rows, kwse_sources = kwse_scenarios(own)
    scenario_rows = nd_rows + kwse_rows
    if not scenario_rows:
        sys.exit(f"None of the {len(own)} reach(es) in this AOI's network is materialized yet")

    exported = {row[0] for row in scenario_rows}
    links = [
        (r["reach_id"], r["reach_to_id"])
        for r in db.query(
            "SELECT reach_id, reach_to_id FROM reach_network WHERE reach_id = ANY(%s)", (sorted(exported),)
        )
    ]
    links, cut = network_rows(links, exported)

    path = out_dir / SCENARIOS_DB
    write_scenarios_db(path, scenario_rows, nd_sources + kwse_sources, links, aoi)
    starts = write_start_reaches(out_dir / START_REACHES, links)

    print(f"\nexported        {len(exported)} of {len(own)} reach(es) in this AOI's network")
    if len(own) > len(exported):
        print(f"  not materialized {len(own) - len(exported)}")
    print(f"scenarios       {len(scenario_rows)} ({len(nd_rows)} nd, {len(kwse_rows)} kwse)")
    print(f"network         {len(links)} reach(es)")
    print(f"start reaches   {len(starts)} at normal depth, {len(cut)} of them cut from a downstream reach not exported")
    for reach_id, downstream_id in cut[:20]:
        print(f"  {reach_id} -> {downstream_id}")
    if len(cut) > 20:
        print(f"  ... and {len(cut) - 20} more")
    print(f"wrote           {path}")
    print(f"                {out_dir / START_REACHES}")


# --- library -------------------------------------------------------------


def read_sources(scenarios_db: Path) -> list[dict]:
    """Every scenario's grid address, as the scenarios step wrote them."""
    with sqlite3.connect(scenarios_db) as connection:
        connection.row_factory = sqlite3.Row
        provenance = connection.execute("SELECT exported_at, aoi_config FROM export_provenance").fetchone()
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
    print(f"scenarios.db    {len(sources)} scenarios, exported {provenance['exported_at']} from {provenance['aoi_config']}")
    return sources


def library_path(library_dir: Path, source: dict) -> Path:
    """Where one scenario's grid goes in the library."""
    return (
        library_dir
        / str(source["reach_id"])
        / library_stage_dir(source["boundary_condition"], source["ds_wse"])
        / library_grid_name(source["us_flow"])
    )


def sync_grid(s3, source: dict, destination: Path) -> str:
    """Download one grid unless the library copy already matches it: 'copied', 'current' or 'missing'.

    A sync, not a fill: a grid whose object has changed size or modification
    time is downloaded again, so re-running after a reach was re-simulated
    updates the library rather than leaving a stale raster behind. The local
    copy takes the object's modification time, which is what makes the next
    comparison meaningful.
    """
    bucket, key = storage.parse_s3_path(source["depth_grid"])
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return "missing"
        raise
    modified = int(head["LastModified"].timestamp())
    if destination.exists():
        stat = destination.stat()
        if stat.st_size == head["ContentLength"] and int(stat.st_mtime) == modified:
            return "current"
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".tif.part")
    s3.download_file(bucket, key, str(partial))
    os.utime(partial, (modified, modified))
    partial.replace(destination)
    return "copied"


def report_strays(library_dir: Path, current: set[Path], prune: bool) -> None:
    """Grids in the library the scenarios database does not name.

    Left in place unless asked otherwise: they are usually the remains of an
    earlier export, but a library is also a thing people put grids into by
    hand, and deleting those unasked would be the wrong default.
    """
    strays = sorted(set(library_dir.glob("*/z_*/f_*.tif")) - current)
    if not strays:
        return
    print(f"{'removed' if prune else 'found':<16}{len(strays)} library grid(s) scenarios.db does not name:")
    for stray in strays[:10]:
        print(f"  {stray.relative_to(library_dir)}")
    if len(strays) > 10:
        print(f"  ... and {len(strays) - 10} more")
    if prune:
        for stray in strays:
            stray.unlink()
    else:
        print("  run again with --prune to remove them")


def mark_missing(scenarios_db: Path, missing: list[dict]) -> None:
    """Set map_exists = 0 for scenarios whose depth grid is not in storage.

    That is the column flows2fim controls consults before choosing a scenario,
    so a missing grid narrows the choice instead of producing a controls row
    pointing at nothing.
    """
    if not missing:
        return
    print(f"missing         {len(missing)} depth grid(s); their scenarios get map_exists = 0:")
    for source in missing[:10]:
        print(f"  {source['depth_grid']}")
    if len(missing) > 10:
        print(f"  ... and {len(missing) - 10} more")
    with sqlite3.connect(scenarios_db) as connection:
        connection.executemany(
            """
            UPDATE scenarios SET map_exists = 0
            WHERE reach_id = ? AND us_flow = ? AND ds_wse = ? AND boundary_condition = ?
            """,
            [(s["reach_id"], s["us_flow"], s["ds_wse"], s["boundary_condition"]) for s in missing],
        )


def export_library(out_dir: Path, prune: bool) -> None:
    scenarios_db = out_dir / SCENARIOS_DB
    if not scenarios_db.exists():
        sys.exit(f"No {scenarios_db}; run the scenarios step first")
    sources = read_sources(scenarios_db)
    library_dir = out_dir / LIBRARY_DIR
    library_dir.mkdir(parents=True, exist_ok=True)

    s3 = storage.get_s3_client()
    destinations = [library_path(library_dir, source) for source in sources]
    # Concurrent, because a library is thousands of small objects.
    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(lambda pair: sync_grid(s3, *pair), zip(sources, destinations)))

    current = {d for d, outcome in zip(destinations, outcomes) if outcome != "missing"}
    missing = [s for s, outcome in zip(sources, outcomes) if outcome == "missing"]
    print(f"library         {library_dir}")
    print(f"  downloaded    {outcomes.count('copied')}")
    print(f"  current       {outcomes.count('current')}")
    report_strays(library_dir, current, prune)
    mark_missing(scenarios_db, missing)


# --- aep -----------------------------------------------------------------


def forecast(flows: pd.DataFrame, column: str, reach_ids: set[int]) -> pd.DataFrame:
    """One AEP column as a flows2fim forecast (feature_id, discharge) for these reaches, in cms.

    Reaches the table has no value for are left out rather than stopping the
    run; the caller counts them.
    """
    values = flows[column].reindex(sorted(reach_ids)).dropna()
    return values.rename_axis("feature_id").rename("discharge").reset_index()


def read_reaches(scenarios_db: Path) -> set[int]:
    """The reaches with usable scenarios."""
    with sqlite3.connect(scenarios_db) as connection:
        reach_ids = {
            row[0] for row in connection.execute("SELECT DISTINCT reach_id FROM scenarios WHERE map_exists = 1")
        }
    if not reach_ids:
        sys.exit(f"{scenarios_db} has no scenarios with map_exists = 1; run the library step first")
    return reach_ids


class Flows2Fim:
    """flows2fim in docker, with <out-dir> mounted as its one directory.

    It runs in docker because it shells out to GDAL, and the published image is
    where both are known to be present. The container runs as the calling user,
    so the controls, VRTs and any GDAL sidecars come back owned by whoever ran
    this rather than by root.
    """

    def __init__(self, image: str, out_dir: Path) -> None:
        self.image = image
        self.out_dir = out_dir.resolve()

    def ensure_image(self) -> None:
        """Make sure the image is on this machine, pulling it if not."""
        if not shutil.which("docker"):
            sys.exit("docker is not on PATH; flows2fim runs in a container")
        present = subprocess.run(
            ["docker", "image", "inspect", self.image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if present.returncode != 0:
            print(f"pulling         {self.image}")
            subprocess.run(["docker", "pull", self.image], check=True)

    def container_path(self, path: Path) -> Path:
        return CONTAINER_OUT / path.resolve().relative_to(self.out_dir)

    def host_path(self, path: Path) -> Path:
        return self.out_dir / path.relative_to(CONTAINER_OUT) if path.is_relative_to(CONTAINER_OUT) else path

    def run(self, *args) -> None:
        """Run one flows2fim command. Path arguments are mapped into the mount."""
        subprocess.run(
            [
                "docker", "run", "--rm",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{self.out_dir}:{CONTAINER_OUT}",
                self.image,
                *(str(self.container_path(a)) if isinstance(a, Path) else str(a) for a in args),
            ],
            check=True,
        )


def post_process_vrt(vrt_path: Path, flows2fim: Flows2Fim) -> None:
    """Make the VRT's source paths relative, and composite overlaps by maximum.

    flows2fim writes the paths it saw, which are the container's. Rewriting them
    relative to the VRT is what lets the output be moved, or handed to someone
    else, and still open.
    """
    tree = ElementTree.parse(vrt_path)
    root = tree.getroot()
    for source_filename in root.findall(".//SourceFilename"):
        source_path = Path(source_filename.text)
        if source_path.is_absolute():
            source_path = flows2fim.host_path(source_path)
        else:
            source_path = vrt_path.parent / source_path
        source_filename.text = os.path.relpath(source_path, vrt_path.parent)
        source_filename.set("relativeToVRT", "1")
    for raster_band in root.findall("VRTRasterBand"):
        raster_band.set("subClass", "VRTDerivedRasterBand")
        pixel_function = raster_band.find("PixelFunctionType")
        if pixel_function is None:
            pixel_function = ElementTree.Element("PixelFunctionType")
            raster_band.insert(0, pixel_function)
        pixel_function.text = "max"
    ElementTree.indent(tree, space="  ")
    tree.write(vrt_path, encoding="UTF-8", xml_declaration=True)
    # flows2fim creates the file owner-only; give it the permissions any other
    # output here gets, so it can be handed on like the grids it points at.
    umask = os.umask(0)
    os.umask(umask)
    vrt_path.chmod(0o666 & ~umask)


def export_aep(aoi: dict, out_dir: Path, image: str) -> None:
    scenarios_db, start_reaches = out_dir / SCENARIOS_DB, out_dir / START_REACHES
    library_dir = out_dir / LIBRARY_DIR
    for needed in (scenarios_db, start_reaches):
        if not needed.exists():
            sys.exit(f"No {needed}; run the scenarios step first")
    if not library_dir.is_dir():
        sys.exit(f"No {library_dir}; run the library step first")

    reach_ids = read_reaches(scenarios_db)
    starts = pd.read_csv(start_reaches)
    if starts.empty:
        sys.exit(f"{start_reaches} names no reach to start controls from")
    flows = flow_statistics.for_aoi(aoi)
    print(f"flows           {flow_statistics.describe(flows)} [{flows.reach_id_column}: {', '.join(flows.aep_columns)}]")
    with tempfile.TemporaryDirectory() as tmp:
        table = flow_statistics.read(
            aoi_config.local_copy(flows.location, Path(tmp)),
            flows.reach_id_column,
            {column: "flow_aep_columns" for column in flows.aep_columns},
        )

    flows2fim = Flows2Fim(image, out_dir)
    flows2fim.ensure_image()
    print(f"reaches         {len(reach_ids)} with depth grids, controls start from {len(starts)} reach(es) in {start_reaches}")

    for column in flows.aep_columns:
        rows = forecast(table, column, reach_ids)
        if rows.empty:
            print(f"{column:<16}skipped: no flows for any exported reach")
            continue
        folder = out_dir / AEP_DIR / column
        folder.mkdir(parents=True, exist_ok=True)
        flows_csv, controls_csv, vrt = folder / "flows.csv", folder / "controls.csv", folder / "depth.vrt"
        rows.to_csv(flows_csv, index=False)

        flows2fim.run("controls", "-db", scenarios_db, "-f", flows_csv, "-o", controls_csv, "-scsv", start_reaches)
        # controls still visits a reach the forecast has no flow for, and picks
        # its lowest discharge. Mapping that would show water nobody forecast,
        # so such reaches are dropped before the depth VRT is built.
        controls = pd.read_csv(controls_csv)
        forecast_controls = controls[controls["reach_id"].isin(rows["feature_id"])]
        if len(forecast_controls) < len(controls):
            forecast_controls.to_csv(controls_csv, index=False)
        flows2fim.run("fim", "-lib", library_dir, "-c", controls_csv, "-o", vrt, "-fmt", "VRT")
        post_process_vrt(vrt, flows2fim)

        without = len(reach_ids) - len(rows)
        print(
            f"{column:<16}{vrt}  ({len(rows)} reach(es) mapped"
            f"{f', {without} without flows not mapped' if without else ''})"
        )


# --- command line --------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="step", required=True)

    scenarios = sub.add_parser("scenarios", help="write <out-dir>/scenarios.db for the AOI's materialized reaches")
    aoi_config.add_argument(scenarios)
    library = sub.add_parser("library", help="download the depth grids scenarios.db names into <out-dir>/library")
    library.add_argument("--prune", action="store_true", help="delete library grids scenarios.db does not name")
    aep = sub.add_parser("aep", help="AEP forecasts, flows2fim controls and depth VRTs into <out-dir>/aep")
    aoi_config.add_argument(aep)
    aep.add_argument("--image", default=IMAGE, help=f"flows2fim image, pulled if absent (default: {IMAGE})")
    for step in (scenarios, library, aep):
        step.add_argument("out_dir", metavar="out-dir", type=Path, help="the local folder everything is written to")
    args = ap.parse_args()
    # Line by line, so this script's report and flows2fim's own output appear in order.
    sys.stdout.reconfigure(line_buffering=True)

    print(f"out-dir         {args.out_dir}")
    if args.step == "library":
        export_library(args.out_dir, args.prune)
        return
    aoi = aoi_config.load(args.aoi_config_path)
    print(f"aoi config      {aoi_config.describe(aoi)}")
    if args.step == "scenarios":
        export_scenarios(aoi, args.out_dir)
    else:
        export_aep(aoi, args.out_dir, args.image)


if __name__ == "__main__":
    main()
