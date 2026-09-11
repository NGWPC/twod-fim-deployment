#!/usr/bin/env python
"""Build AEP controls and depth VRTs from an exported library and database.

Given the two things export_f2f_db.py and export_f2f_library.py produced, this
runs flows2fim once per recurrence interval: `controls` to choose a scenario
per reach, then `fim -fmt VRT` to mosaic the chosen depth grids.

Discharges are cms, the unit the whole system is authored in -- `desired_state`
bounds, `q_set`, the `q=` folders. flows2fim's help says cfs, but it never
converts: it matches a forecast value against `us_flow` in the scenarios table,
so a cms forecast against a cms library is what agrees.

Controls are traced upstream from the reaches with nowhere left to drain. That
is what the network table is for, and naming anything else as a start would be
wrong rather than merely wasteful: `controls -scs` defaults to `nd`, so a
mid-network reach named as a start is told to sit at normal depth instead of at
the stage its downstream neighbour actually holds.

flows2fim runs in docker because it shells out to GDAL and the published image
is where both are known to be present. The library, the database and the output
directory are mounted separately, so they do not have to live under one root.

Usage:
    uv run python scripts/create_aep_f2f_vrts.py
    uv run python scripts/create_aep_f2f_vrts.py --recurrence-intervals 10 25
    uv run python scripts/create_aep_f2f_vrts.py --lib /path/to/library
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd

from export_f2f_db import DEFAULT_F2F_DIR, DEFAULT_SCENARIOS_DB, TESTDATA
from export_f2f_library import DEFAULT_LIBRARY_DIR

DEFAULT_Q_BOUNDS_PARQUET = TESTDATA / "min_max_network_flows.parquet"
DEFAULT_RECURRENCE_INTERVALS = [5, 50, 100]
DEFAULT_IMAGE = "ghcr.io/ngwpc/flows2fim:0.5.0"

CONTAINER_LIBRARY = Path("/lib")
CONTAINER_DB = Path("/db")
CONTAINER_OUT = Path("/out")


class Flows2Fim:
    """flows2fim in docker, with the library, database and output mounted apart.

    The container runs as the calling user so the controls, VRTs and any GDAL
    sidecars come back owned by whoever ran the script rather than by root. The
    library and the database go in read-only: this step only reads them, and
    saying so keeps a flows2fim bug from rewriting an export.
    """

    def __init__(self, image: str, library_dir: Path, scenarios_db: Path, out_dir: Path) -> None:
        self.image = image
        self.scenarios_db = scenarios_db.resolve()
        out = out_dir.resolve()

        # The output mount is writable and always present. The library and the
        # database are mounted only when they sit outside it: by default they
        # are inside, and mounting the same directory a second time read-only
        # would shadow the writable one and make the outputs unwritable.
        self.mounts = [(out, CONTAINER_OUT, False)]
        for host_path, container_path in (
            (library_dir.resolve(), CONTAINER_LIBRARY),
            (self.scenarios_db.parent, CONTAINER_DB),
        ):
            if not host_path.is_relative_to(out):
                self.mounts.append((host_path, container_path, True))

    def run(self, *args) -> None:
        """Run one flows2fim command. Path arguments are mapped into the mounts."""
        volumes = []
        for host_path, container_path, read_only in self.mounts:
            volumes += ["-v", f"{host_path}:{container_path}{':ro' if read_only else ''}"]

        subprocess.run(
            [
                "docker", "run", "--rm",
                "--user", f"{os.getuid()}:{os.getgid()}",
                *volumes,
                self.image,
                *(self._argument(argument) for argument in args),
            ],
            check=True,
        )

    def _argument(self, argument) -> str:
        """Map one host path into the container. Longest matching mount wins.

        Mounts can nest -- a library inside the output directory is reachable
        by two names -- so the most specific one is the answer.
        """
        if not isinstance(argument, Path):
            return str(argument)
        resolved = argument.resolve()
        matches = [
            (host_path, container_path)
            for host_path, container_path, _ in self.mounts
            if resolved.is_relative_to(host_path)
        ]
        if not matches:
            sys.exit(f"{resolved} is under none of the mounted directories")
        host_path, container_path = max(matches, key=lambda mount: len(str(mount[0])))
        return str(container_path / resolved.relative_to(host_path))

    def host_path(self, path: Path) -> Path:
        """Translate a path flows2fim wrote back to where it is on this host."""
        matches = [
            (host_path, container_path)
            for host_path, container_path, _ in self.mounts
            if path.is_relative_to(container_path)
        ]
        if not matches:
            return path
        host_path, container_path = max(matches, key=lambda mount: len(str(mount[1])))
        return host_path / path.relative_to(container_path)


def ensure_image(image: str) -> None:
    """Make sure the flows2fim image is on this machine, pulling it if not."""
    if not shutil.which("docker"):
        sys.exit("docker is not on PATH; flows2fim runs in a container")
    present = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if present.returncode == 0:
        return
    print(f"Pulling {image}...")
    subprocess.run(["docker", "pull", image], check=True)


def read_reaches(scenarios_db: Path) -> tuple[set[int], list[int]]:
    """The reaches with usable scenarios, and the outlets to start controls from."""
    with sqlite3.connect(scenarios_db) as connection:
        reach_ids = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT reach_id FROM scenarios WHERE map_exists = 1"
            )
        }
        outlets = [
            row[0]
            for row in connection.execute(
                "SELECT reach_id FROM network WHERE updated_to_id IS NULL ORDER BY reach_id"
            )
        ]

    if not reach_ids:
        sys.exit(f"{scenarios_db} has no scenarios with map_exists = 1")
    if not outlets:
        sys.exit(f"{scenarios_db} has no outlet reaches to start controls from")
    return reach_ids, outlets


def make_flows_files(
    reach_ids: set[int],
    recurrence_intervals: list[int],
    q_bound_parquet: Path,
    flows_dir: Path,
) -> list[tuple[int, Path]]:
    """Write one forecast CSV per recurrence interval, in cms."""
    columns = [f"f{interval}year" for interval in recurrence_intervals]
    flow_data = pd.read_parquet(q_bound_parquet)
    missing_columns = set(columns).difference(flow_data.columns)
    if missing_columns:
        sys.exit(f"{q_bound_parquet} has no columns {sorted(missing_columns)}")

    missing_reaches = reach_ids.difference(flow_data.index)
    if missing_reaches:
        sys.exit(f"Reaches missing from {q_bound_parquet}: {sorted(missing_reaches)}")
    flow_data = flow_data.loc[sorted(reach_ids)]

    flows_dir.mkdir(parents=True, exist_ok=True)
    forecasts = []
    for interval, column in zip(recurrence_intervals, columns):
        forecast_path = flows_dir / f"flows_{interval}yr_cms.csv"
        forecast = (
            flow_data[[column]]
            .dropna()
            .rename_axis("feature_id")
            .reset_index()
            .rename(columns={column: "discharge"})
        )
        forecast.to_csv(forecast_path, index=False)
        forecasts.append((interval, forecast_path))
        print(f"Wrote {len(forecast)} rows to {forecast_path}.")

    return forecasts


def run_f2f(
    flows2fim: Flows2Fim,
    forecasts: list[tuple[int, Path]],
    outlets: list[int],
    library_dir: Path,
    controls_dir: Path,
    vrt_dir: Path,
) -> None:
    """Build a controls CSV and a depth VRT for each forecast."""
    start_reach_ids = ",".join(str(reach_id) for reach_id in outlets)
    print(f"Starting controls from {len(outlets)} outlet reaches.")
    controls_dir.mkdir(parents=True, exist_ok=True)
    vrt_dir.mkdir(parents=True, exist_ok=True)

    for interval, forecast_path in forecasts:
        controls_path = controls_dir / f"flows_{interval}yr_controls.csv"
        vrt_path = vrt_dir / f"flows_{interval}yr.vrt"

        print(f"Creating controls for the {interval}yr forecast...")
        flows2fim.run(
            "controls",
            "-db", flows2fim.scenarios_db,
            "-f", forecast_path,
            "-o", controls_path,
            "-sids", start_reach_ids,
        )
        print(f"Creating VRT for the {interval}yr forecast...")
        flows2fim.run(
            "fim",
            "-lib", library_dir,
            "-c", controls_path,
            "-o", vrt_path,
            "-fmt", "VRT",
        )
        print(f"Wrote {controls_path} and {vrt_path}.")


def post_process_vrt(flows2fim: Flows2Fim, vrt_dir: Path) -> None:
    """Make the VRT source paths relative, and composite overlaps by maximum.

    flows2fim writes the paths it saw, which are the container's. Rewriting them
    relative to the VRT is what lets the output be moved, or handed to someone
    else, and still open.
    """
    vrt_paths = sorted(vrt_dir.glob("*.vrt"))
    if not vrt_paths:
        sys.exit(f"No VRT files found under {vrt_dir}")

    for vrt_path in vrt_paths:
        tree = ElementTree.parse(vrt_path)
        root = tree.getroot()

        for source_filename in root.findall(".//SourceFilename"):
            source_path = Path(source_filename.text)
            if not source_path.is_absolute():
                source_path = (vrt_path.parent / source_path).resolve()
            source_path = flows2fim.host_path(source_path)
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
        print(f"Rewrote {vrt_path} with relative sources and a max pixel function.")


def create(
    scenarios_db: Path,
    library_dir: Path,
    out_dir: Path,
    q_bound_parquet: Path,
    recurrence_intervals: list[int],
    image: str,
) -> None:
    ensure_image(image)
    out_dir.mkdir(parents=True, exist_ok=True)
    flows2fim = Flows2Fim(image, library_dir, scenarios_db, out_dir)

    reach_ids, outlets = read_reaches(scenarios_db)
    print(f"{len(reach_ids)} reaches have depth grids; {len(outlets)} are outlets.")
    forecasts = make_flows_files(
        reach_ids, recurrence_intervals, q_bound_parquet, out_dir / "flows"
    )
    run_f2f(
        flows2fim, forecasts, outlets, library_dir, out_dir / "controls", out_dir / "vrts"
    )
    post_process_vrt(flows2fim, out_dir / "vrts")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_SCENARIOS_DB,
        help="scenarios database (default: testdata/outputs/f2f/scenarios.db)",
    )
    ap.add_argument(
        "--lib",
        type=Path,
        default=DEFAULT_LIBRARY_DIR,
        help="FIM library (default: testdata/outputs/f2f/library)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_F2F_DIR,
        help="where flows, controls and vrts are written "
        "(default: testdata/outputs/f2f)",
    )
    ap.add_argument(
        "--q-bound-parquet",
        type=Path,
        default=DEFAULT_Q_BOUNDS_PARQUET,
        help="flow statistics the forecasts are read from",
    )
    ap.add_argument(
        "--recurrence-intervals",
        type=int,
        nargs="+",
        default=DEFAULT_RECURRENCE_INTERVALS,
        metavar="YEARS",
        help="recurrence intervals to build, as f<N>year columns (default: 5 50 100)",
    )
    ap.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help=f"flows2fim image, pulled if absent (default: {DEFAULT_IMAGE})",
    )
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"No such scenarios database: {args.db}; run export_f2f_db.py first")
    if not args.lib.is_dir():
        sys.exit(f"No such library: {args.lib}; run export_f2f_library.py first")
    if not args.q_bound_parquet.exists():
        sys.exit(f"No such flow bounds parquet: {args.q_bound_parquet}")

    print(f"db        {args.db}")
    print(f"library   {args.lib}")
    print(f"out       {args.out_dir}")
    print(f"intervals {', '.join(f'{i}yr' for i in args.recurrence_intervals)}")
    print(f"image     {args.image}\n")
    create(
        args.db,
        args.lib,
        args.out_dir,
        args.q_bound_parquet,
        args.recurrence_intervals,
        args.image,
    )


if __name__ == "__main__":
    main()
