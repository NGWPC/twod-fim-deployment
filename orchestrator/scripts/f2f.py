#!/usr/bin/env python
"""Publish materialized reaches for flows2fim, into a local folder or storage.

Three steps, each a command, run in order by `just f2f`:

  scenarios [aoi-config-path] <out-dir>
            <out-dir>/scenarios.db: the `scenarios` and `network` tables
            flows2fim reads, for the reaches that are materialized, where each
            depth grid is in storage, and `reach_ids`, the number flows2fim
            knows each reach by
            <out-dir>/start_reaches.csv: the reaches flows2fim controls start
            from, and the stage each starts at
  library   <out-dir>
            <out-dir>/library/<number>/z_<stage>/f_<flow>.tif: the depth
            grids scenarios.db names, copied from the results tree
  aep       [aoi-config-path] <out-dir>
            <out-dir>/aep/<column>/: for each AEP column of the flow
            statistics, a forecast, flows2fim controls for it from
            start_reaches.csv, and a depth VRT

With an AOI config, the reaches are those of its `network`, and its flow
statistics are forecast. Without one, they are every reach in the database's
network, and the system-wide flow statistics are forecast.

Read only: the database through a connection it refuses writes on, storage
by reading and copying from. The one thing written is <out-dir>, which is
refused inside the storage root or the source data root.

<out-dir> is a local folder or an s3:// address, and each export goes into a
new, empty one: an export is a snapshot of what is materialized when it runs,
so exporting again, for more reaches or other ones, is a new out-dir. sqlite and flows2fim work on local files only, so every file is written
in a local folder first -- <out-dir> itself, or a temporary one when <out-dir> is
in storage -- and uploaded from there. The library is the exception: into
storage it is copied object to object and never passes through this machine,
and flows2fim reads it where it is, through GDAL's /vsis3/.

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

flows2fim parses reach ids as integers, while a reach id here is text: a reach
modify_network split out of one flowpath is named <flowpath id>_<n>. So every
table, file and library folder flows2fim reads names a reach by a number, and
`reach_ids` in scenarios.db maps each number to its reach id. The numbers
belong to one export only. Once flows2fim takes text ids, they go.

Discharges are cms, the unit the whole system is authored in -- desired_state
bounds, q_set, the q= folders. flows2fim's help says cfs, but it never converts:
it matches a forecast value against `us_flow` in the scenarios table, so a cms
forecast against a cms library is what agrees.

Usage:
    uv run python scripts/f2f.py scenarios [aoi-config-path] <out-dir>
    uv run python scripts/f2f.py library <out-dir>
    uv run python scripts/f2f.py aep [aoi-config-path] <out-dir> [--image IMAGE]
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
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from xml.etree import ElementTree

import boto3
import pandas as pd
from botocore.exceptions import ClientError

import aoi_config
import flow_statistics
from recon import db, identity, storage
from recon.config import settings

SCENARIOS_DB = "scenarios.db"
START_REACHES = "start_reaches.csv"
REACH_IDS = "reach_ids"
LIBRARY_DIR = "library"
AEP_DIR = "aep"
DEPTH_GRID_FILENAME = "depth.tif"

IMAGE = "ghcr.io/ngwpc/flows2fim:0.5.0"
# The stage flows2fim controls gives a start reach: normal depth.
START_STAGE = "nd"
CONTAINER_OUT = Path("/out")


def is_missing(exc: ClientError) -> bool:
    return exc.response["Error"]["Code"] in ("404", "NoSuchKey")


# --- out-dir -------------------------------------------------------------


class OutDir:
    """Where an export is published: a local folder, or an s3:// address.

    Files are named relative to <out-dir>, with `/`. Each is written at
    `local(name)`: <out-dir> itself when it is a folder, `work` when it is in
    storage, from where `publish` uploads it. `fetch` is the other direction, so
    a step can build on what the one before it published.
    """

    def __init__(self, location: str, work: Path) -> None:
        self.location = location.rstrip("/")
        self.in_storage = location.startswith("s3://")
        self.work = work if self.in_storage else Path(location)
        for name, root in (
            ("storage root", settings.twod_fim_data_root_prefix),
            ("source data root", settings.twod_fim_source_data_prefix),
        ):
            if f"{self.location}/".startswith(f"{root}/"):
                sys.exit(f"out-dir {self.location} is inside the {name} {root}; f2f only reads from there")

    def local(self, name: str) -> Path:
        return self.work / name

    def address(self, name: str) -> str:
        """Where a file is published, for reports and for copying into."""
        return f"{self.location}/{name}"

    def gdal_path(self, name: str) -> str:
        """The path GDAL opens a published file by."""
        if self.in_storage:
            return "/vsis3/" + self.address(name).removeprefix("s3://")
        return str(self.local(name).resolve())

    def fetch(self, name: str) -> Path | None:
        """The local copy of a published file, downloaded when in storage; None if it is not published."""
        path = self.local(name)
        if self.in_storage:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                storage.get_s3_client().download_file(*storage.parse_s3_path(self.address(name)), str(path))
            except ClientError as exc:
                if is_missing(exc):
                    return None
                raise
        return path if path.exists() else None

    def publish(self, *names: str) -> None:
        """Upload these files from the local folder, when <out-dir> is in storage."""
        if not self.in_storage:
            return
        s3 = storage.get_s3_client()
        for name in names:
            s3.upload_file(str(self.local(name)), *storage.parse_s3_path(self.address(name)))

    def holds(self, folder: str = "") -> bool:
        """Whether anything is published under this folder, or in <out-dir> at all."""
        if not self.in_storage:
            path = self.local(folder)
            return path.is_dir() and any(path.iterdir())
        bucket, key = storage.parse_s3_path(self.address(folder) if folder else self.location)
        prefix = f"{key}/" if key else ""
        return storage.get_s3_client().list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)["KeyCount"] > 0


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


def nd_scenarios(conn, reach_ids: set[str]) -> tuple[list[tuple], list[tuple]]:
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
        conn=conn,
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


def kwse_scenarios(conn, reach_ids: set[str]) -> tuple[list[tuple], list[tuple]]:
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
        conn=conn,
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


def network_rows(links: list[tuple[str, str | None]], reach_ids: set[str]) -> tuple[list[tuple], list[tuple]]:
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


def write_start_reaches(path: Path, links: list[tuple[str, str | None]], numbers: dict[str, int]) -> list[str]:
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
    pd.DataFrame(
        {"reach_id": [numbers[r] for r in starts], "control_stage": [START_STAGE] * len(starts)}
    ).to_csv(path, index=False)
    return starts


def flows2fim_numbers(reach_ids: set[str]) -> dict[str, int]:
    """The number flows2fim knows each reach by in this export: 1, 2, ... in reach id order."""
    return {reach_id: number for number, reach_id in enumerate(sorted(reach_ids), start=1)}


def write_scenarios_db(
    path: Path,
    scenario_rows: list[tuple],
    source_rows: list[tuple],
    links: list[tuple[str, str | None]],
    numbers: dict[str, int],
    aoi_config_described: str | None,
) -> None:
    """Write the two tables flows2fim reads, plus the reach numbers, where each grid is and what was exported.

    `aoi_config_described` is the AOI config the export was scoped by, or None
    for every materialized reach.
    """
    number = numbers.__getitem__
    scenario_rows = [(number(row[0]), *row[1:]) for row in scenario_rows]
    source_rows = [(number(row[0]), *row[1:]) for row in source_rows]
    links = [(number(reach_id), None if downstream_id is None else number(downstream_id)) for reach_id, downstream_id in links]
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
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
        # Not part of the flows2fim contract either: the reach id behind each
        # number every other table uses.
        connection.execute(f"CREATE TABLE {REACH_IDS} (reach_id INTEGER PRIMARY KEY, twodfim_reach_id TEXT NOT NULL UNIQUE)")
        connection.executemany(
            f"INSERT INTO {REACH_IDS} (reach_id, twodfim_reach_id) VALUES (?, ?)",
            sorted((n, r) for r, n in numbers.items()),
        )
        connection.execute(
            """
            CREATE TABLE export_provenance (
                exported_at TEXT NOT NULL,
                aoi_config TEXT,
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
                aoi_config_described,
                f"{settings.postgres_db} on {settings.postgres_host}",
            ),
        )


def reaches_in_scope(conn, aoi: dict | None) -> tuple[set[str], str]:
    """The reaches an export may include, and what the report calls them."""
    if aoi is None:
        return {r["reach_id"] for r in db.query("SELECT reach_id FROM reach_network", conn=conn)}, "the database's network"
    return aoi_config.network_reach_ids(aoi), "this AOI's network"


def export_scenarios(aoi: dict | None, out: OutDir) -> None:
    if out.holds():
        sys.exit(f"{out.location} is not empty; each export goes into a new, empty out-dir")
    with db.connect(read_only=True) as conn:
        own, scope = reaches_in_scope(conn, aoi)
        nd_rows, nd_sources = nd_scenarios(conn, own)
        kwse_rows, kwse_sources = kwse_scenarios(conn, own)
        scenario_rows = nd_rows + kwse_rows
        if not scenario_rows:
            sys.exit(f"None of the {len(own)} reach(es) in {scope} is materialized yet")

        exported = {row[0] for row in scenario_rows}
        links = [
            (r["reach_id"], r["reach_to_id"])
            for r in db.query(
                "SELECT reach_id, reach_to_id FROM reach_network WHERE reach_id = ANY(%s)",
                (sorted(exported),),
                conn=conn,
            )
        ]
    links, cut = network_rows(links, exported)

    path = out.local(SCENARIOS_DB)
    numbers = flows2fim_numbers(exported)
    described = None if aoi is None else aoi_config.describe(aoi)
    write_scenarios_db(path, scenario_rows, nd_sources + kwse_sources, links, numbers, described)
    starts = write_start_reaches(out.local(START_REACHES), links, numbers)
    out.publish(SCENARIOS_DB, START_REACHES)

    print(f"\nexported        {len(exported)} of {len(own)} reach(es) in {scope}")
    if len(own) > len(exported):
        print(f"  not materialized {len(own) - len(exported)}")
    print(f"scenarios       {len(scenario_rows)} ({len(nd_rows)} nd, {len(kwse_rows)} kwse)")
    print(f"network         {len(links)} reach(es)")
    print(f"start reaches   {len(starts)} at normal depth, {len(cut)} of them cut from a downstream reach not exported")
    for reach_id, downstream_id in cut[:20]:
        print(f"  {reach_id} -> {downstream_id}")
    if len(cut) > 20:
        print(f"  ... and {len(cut) - 20} more")
    print(f"wrote           {out.address(SCENARIOS_DB)}")
    print(f"                {out.address(START_REACHES)}")


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
    scope = provenance["aoi_config"] or "every materialized reach"
    print(f"scenarios.db    {len(sources)} scenarios, exported {provenance['exported_at']} from {scope}")
    return sources


def library_name(source: dict) -> str:
    """Where one scenario's grid goes in the library, relative to <out-dir>."""
    return "/".join(
        (
            LIBRARY_DIR,
            str(source["reach_id"]),
            library_stage_dir(source["boundary_condition"], source["ds_wse"]),
            library_grid_name(source["us_flow"]),
        )
    )


def download_grid(s3, source: dict, destination: Path) -> str:
    """Download one grid into a local library: 'copied', 'present' or 'missing'.

    'present' is a grid an interrupted run already downloaded: it is written
    under another name and renamed when complete, so one that exists is whole.
    """
    if destination.exists():
        return "present"
    bucket, key = storage.parse_s3_path(source["depth_grid"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".tif.part")
    try:
        s3.download_file(bucket, key, str(partial))
    except ClientError as exc:
        if is_missing(exc):
            return "missing"
        raise
    partial.replace(destination)
    return "copied"


def copy_grid(s3, source: dict, destination: str) -> str:
    """Copy one grid into a library in storage, object to object: as download_grid.

    A copy lands whole or not at all, so one that exists is whole.
    """
    destination_bucket, destination_key = storage.parse_s3_path(destination)
    try:
        s3.head_object(Bucket=destination_bucket, Key=destination_key)
        return "present"
    except ClientError as exc:
        if not is_missing(exc):
            raise
    bucket, key = storage.parse_s3_path(source["depth_grid"])
    try:
        s3.copy({"Bucket": bucket, "Key": key}, destination_bucket, destination_key)
    except ClientError as exc:
        if is_missing(exc):
            return "missing"
        raise
    return "copied"


def mark_missing(scenarios_db: Path, missing: list[dict]) -> bool:
    """Set map_exists = 0 for scenarios whose depth grid is not in storage.

    That is the column flows2fim controls consults before choosing a scenario,
    so a missing grid narrows the choice instead of producing a controls row
    pointing at nothing.
    """
    if not missing:
        return False
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
    return True


def export_library(out: OutDir) -> None:
    scenarios_db = out.fetch(SCENARIOS_DB)
    if scenarios_db is None:
        sys.exit(f"No {out.address(SCENARIOS_DB)}; run the scenarios step first")
    sources = read_sources(scenarios_db)

    if not out.in_storage:
        out.local(LIBRARY_DIR).mkdir(parents=True, exist_ok=True)
    s3 = storage.get_s3_client()
    names = [library_name(source) for source in sources]

    def place(source: dict, name: str) -> str:
        if out.in_storage:
            return copy_grid(s3, source, out.address(name))
        return download_grid(s3, source, out.local(name))

    # Concurrent, because a library is thousands of small objects.
    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(place, sources, names))

    missing = [s for s, outcome in zip(sources, outcomes) if outcome == "missing"]
    print(f"library         {out.address(LIBRARY_DIR)}")
    print(f"  copied        {outcomes.count('copied')}")
    if outcomes.count("present"):
        print(f"  present       {outcomes.count('present')}, from an earlier run of this step")
    if mark_missing(scenarios_db, missing):
        out.publish(SCENARIOS_DB)


# --- aep -----------------------------------------------------------------


def forecast(flows: pd.DataFrame, column: str, reaches: dict[int, str]) -> pd.DataFrame:
    """One AEP column as a flows2fim forecast (feature_id, discharge) for these reaches, in cms.

    `reaches` maps each reach's flows2fim number, the feature_id, to its reach
    id. Each reach takes the flow listed under its flow id, so every piece of a
    split flowpath is forecast with the flowpath's discharge. Reaches the table
    has no value for are left out rather than stopping the run; the caller
    counts them.
    """
    numbers = sorted(reaches)
    values = flows[column].reindex([flow_statistics.flow_id(reaches[n]) for n in numbers])
    values.index = pd.Index(numbers, name="feature_id")
    return values.dropna().rename("discharge").reset_index()


def read_reaches(scenarios_db: Path) -> dict[int, str]:
    """The reaches with usable scenarios: flows2fim number -> reach id."""
    with sqlite3.connect(scenarios_db) as connection:
        reach_ids = dict(
            connection.execute(
                f"""
                SELECT DISTINCT s.reach_id, r.twodfim_reach_id
                FROM scenarios s JOIN {REACH_IDS} r USING (reach_id)
                WHERE s.map_exists = 1
                """
            )
        )
    if not reach_ids:
        sys.exit(f"{scenarios_db} has no scenarios with map_exists = 1; run the library step first")
    return reach_ids


def gdal_storage_access() -> tuple[dict[str, str], list[str]]:
    """The environment GDAL in a container reads storage with, and the docker arguments it needs.

    The credentials are this machine's, resolved as boto3 resolves them -- keys,
    a profile, SSO or a role -- and handed over as keys, since the container
    can see none of those. Against an endpoint other than AWS (MinIO), GDAL is
    pointed at it; one on this machine is reached through the host gateway.
    """
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        sys.exit("No AWS credentials; flows2fim reads the library from storage with them")
    frozen = credentials.get_frozen_credentials()
    environment = {
        "AWS_ACCESS_KEY_ID": frozen.access_key,
        "AWS_SECRET_ACCESS_KEY": frozen.secret_key,
        # Every grid is opened by its path, so listing the folder first is wasted requests.
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    }
    if frozen.token:
        environment["AWS_SESSION_TOKEN"] = frozen.token
    if session.region_name:
        environment["AWS_REGION"] = session.region_name
    if os.environ.get("AWS_REQUEST_PAYER"):
        environment["AWS_REQUEST_PAYER"] = os.environ["AWS_REQUEST_PAYER"]
    docker_args = []
    if settings.aws_endpoint_url:
        endpoint = urlparse(settings.aws_endpoint_url)
        host = endpoint.netloc
        if endpoint.hostname in ("localhost", "127.0.0.1"):
            host = host.replace(endpoint.hostname, "host.docker.internal", 1)
            docker_args = ["--add-host", "host.docker.internal:host-gateway"]
        environment |= {
            "AWS_S3_ENDPOINT": host,
            "AWS_HTTPS": "YES" if endpoint.scheme == "https" else "NO",
            "AWS_VIRTUAL_HOSTING": "FALSE",
        }
    return environment, docker_args


class Flows2Fim:
    """flows2fim in docker, with <out-dir>'s local folder mounted as its one directory.

    It runs in docker because it shells out to GDAL, and the published image is
    where both are known to be present. The container runs as the calling user,
    so the controls, VRTs and any GDAL sidecars come back owned by whoever ran
    this rather than by root. When <out-dir> is in storage, the container is
    also given what GDAL needs to read the library from there.
    """

    def __init__(self, image: str, out: OutDir) -> None:
        self.image = image
        self.out = out
        self.out_dir = out.work.resolve()
        self.environment, self.docker_args = gdal_storage_access() if out.in_storage else ({}, [])

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

    def run(self, *args) -> None:
        """Run one flows2fim command. Path arguments are mapped into the mount; strings are passed as they are."""
        subprocess.run(
            [
                "docker", "run", "--rm",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{self.out_dir}:{CONTAINER_OUT}",
                # Named only, so the values are not on a command line.
                *(arg for name in self.environment for arg in ("-e", name)),
                *self.docker_args,
                self.image,
                *(str(self.container_path(a)) if isinstance(a, Path) else str(a) for a in args),
            ],
            env=os.environ | self.environment,
            check=True,
        )


def post_process_vrt(vrt_path: Path, out: OutDir) -> None:
    """Make a local VRT's source paths relative, and composite overlaps by maximum.

    flows2fim writes the paths it saw. In a local out-dir those are the
    container's, and rewriting them relative to the VRT is what lets the output
    be moved, or handed to someone else, and still open. In storage they are
    the /vsis3/ paths of the library and stay so: GDAL joins a relative path to
    the VRT's own key without resolving `..`, and S3 keys are literal, so
    `../../library` would name no object. A /vsis3/ path opens from anywhere with
    access to the bucket.
    """
    tree = ElementTree.parse(vrt_path)
    root = tree.getroot()
    for source_filename in root.findall(".//SourceFilename") if not out.in_storage else []:
        source = PurePosixPath(source_filename.text)
        if source_filename.get("relativeToVRT") != "1" and source.is_relative_to(CONTAINER_OUT):
            local = out.local(source.relative_to(CONTAINER_OUT).as_posix())
            source_filename.text = os.path.relpath(local.resolve(), vrt_path.parent.resolve())
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


def export_aep(aoi: dict | None, out: OutDir, image: str) -> None:
    scenarios_db, start_reaches = out.fetch(SCENARIOS_DB), out.fetch(START_REACHES)
    for name, needed in ((SCENARIOS_DB, scenarios_db), (START_REACHES, start_reaches)):
        if needed is None:
            sys.exit(f"No {out.address(name)}; run the scenarios step first")
    if not out.holds(LIBRARY_DIR):
        sys.exit(f"No {out.address(LIBRARY_DIR)}; run the library step first")
    # flows2fim reads a library in storage where it is, not a copy of it.
    library = out.gdal_path(LIBRARY_DIR) if out.in_storage else out.local(LIBRARY_DIR)

    reach_ids = read_reaches(scenarios_db)
    starts = pd.read_csv(start_reaches)
    if starts.empty:
        sys.exit(f"{out.address(START_REACHES)} names no reach to start controls from")
    flows = flow_statistics.for_aoi(aoi or {})
    print(f"flows           {flow_statistics.describe(flows)} [{flows.reach_id_column}: {', '.join(flows.aep_columns)}]")
    with tempfile.TemporaryDirectory() as tmp:
        table = flow_statistics.read(
            aoi_config.local_copy(flows.location, Path(tmp)),
            flows.reach_id_column,
            {column: "flow_aep_columns" for column in flows.aep_columns},
        )

    flows2fim = Flows2Fim(image, out)
    flows2fim.ensure_image()
    print(f"reaches         {len(reach_ids)} with depth grids, controls start from {len(starts)} reach(es) in {START_REACHES}")

    for column in flows.aep_columns:
        rows = forecast(table, column, reach_ids)
        if rows.empty:
            print(f"{column:<16}skipped: no flows for any exported reach")
            continue
        folder = f"{AEP_DIR}/{column}"
        names = [f"{folder}/flows.csv", f"{folder}/controls.csv", f"{folder}/depth.vrt"]
        flows_csv, controls_csv, vrt = (out.local(name) for name in names)
        vrt.parent.mkdir(parents=True, exist_ok=True)
        rows.to_csv(flows_csv, index=False)

        flows2fim.run("controls", "-db", scenarios_db, "-f", flows_csv, "-o", controls_csv, "-scsv", start_reaches)
        # controls still visits a reach the forecast has no flow for, and picks
        # its lowest discharge. Mapping that would show water nobody forecast,
        # so such reaches are dropped before the depth VRT is built.
        controls = pd.read_csv(controls_csv)
        forecast_controls = controls[controls["reach_id"].isin(rows["feature_id"])]
        if len(forecast_controls) < len(controls):
            forecast_controls.to_csv(controls_csv, index=False)
        flows2fim.run("fim", "-lib", library, "-c", controls_csv, "-o", vrt, "-fmt", "VRT")
        post_process_vrt(vrt, out)
        out.publish(*names)

        without = len(reach_ids) - len(rows)
        print(
            f"{column:<16}{out.address(names[2])}  ({len(rows)} reach(es) mapped"
            f"{f', {without} without flows not mapped' if without else ''})"
        )


# --- command line --------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="step", required=True)

    every_reach = "; left out, every materialized reach in the database, with the system-wide flow statistics"
    scenarios = sub.add_parser("scenarios", help="write <out-dir>/scenarios.db for the materialized reaches")
    aoi_config.add_argument(scenarios, optional=every_reach)
    library = sub.add_parser("library", help="copy the depth grids scenarios.db names into <out-dir>/library")
    aep = sub.add_parser("aep", help="AEP forecasts, flows2fim controls and depth VRTs into <out-dir>/aep")
    aoi_config.add_argument(aep, optional=every_reach)
    aep.add_argument("--image", default=IMAGE, help=f"flows2fim image, pulled if absent (default: {IMAGE})")
    for step in (scenarios, library, aep):
        step.add_argument("out_dir", metavar="out-dir", help="where everything is written: a local folder or an s3:// address")
    args = ap.parse_args()
    # Line by line, so this script's report and flows2fim's own output appear in order.
    sys.stdout.reconfigure(line_buffering=True)

    print(f"out-dir         {args.out_dir}")
    with tempfile.TemporaryDirectory() as work:
        out = OutDir(args.out_dir, Path(work))
        if args.step == "library":
            export_library(out)
            return
        aoi = None
        if args.aoi_config_path is None:
            print("aoi config      none: every materialized reach")
        else:
            aoi = aoi_config.load(args.aoi_config_path)
            print(f"aoi config      {aoi_config.describe(aoi)}")
        if args.step == "scenarios":
            export_scenarios(aoi, out)
        else:
            export_aep(aoi, out, args.image)


if __name__ == "__main__":
    main()
