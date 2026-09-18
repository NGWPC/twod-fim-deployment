"""An AOI config: the payload for one area of interest's seed, author and f2f commands.

Every command is given its path, a local path or an s3:// address, and reads
what it needs from it. Nothing looks an AOI config up, stores it, or assumes
where it is kept. Copying one into provenance afterwards, beside the network it
names, is record keeping by people (RUNBOOK.md), and nothing reads it from
there. The repo keeps only an example (example.aoi_config.jsonc, which lists
every key with what leaving it out does) and the test AOI
(testdata/e2e.aoi_config.json).

Not to be confused with config.py, the system-wide settings: an AOI config says
what to produce for one AOI, and whatever it leaves out comes from the
database's defaults or those settings (below).

JSON with `//` comments (JSONC), like the jobs repo's specs-and-manifests
examples; a plain .json file works too.

Keys:

  name             optional label for people, printed by the commands
  description      optional free text

  read by seed.py
  network          modify_network's network.gpkg (layer reach_network)
  lakes            GeoPackage with layer lakes_polygons
  coasts           GeoPackage with layer coastal_influence_polygons

  read by author_intent.py
  flow_statistics  optional: a table (.parquet or .csv) of per-reach flows; the
                   reaches of `network` it covers are the ones authored
  flow_reach_id_column, flow_q_lower_column, flow_q_upper_column
                   optional: what that table calls the reach id and the two
                   columns the discharge bounds come from
  dem_source       optional: where a job reads elevation for these reaches
  lulc_source      optional: where a job reads land cover
  lulc_lookup      optional: the land-cover to Manning's n JSON, in s3://
  q_bound_factors  optional [lower, upper]: pull the discharge bounds inward,
                   to keep a test run short

  read by f2f.py, which also runs without an AOI config
  network          only its reaches are exported
  flow_statistics, flow_reach_id_column
                   as above
  flow_aep_columns optional: the columns of that table forecast as AEP flows,
                   one set of controls and one depth VRT each

A command reads only the keys it needs, and says so when one is missing. An AOI
config only overrides: dem_source, lulc_source and lulc_lookup left out are
authored as NULL, so those reaches follow the database's own
desired_state_defaults; the flow statistics keys left out fall back to the
system-wide settings of the same name (config.py, .env), in one place:
flow_statistics.py.

dem_source, lulc_source and lulc_lookup are read by jobs, so they must be
addresses a job can open (s3://, https://, /vsi...), never local paths; and
lulc_lookup must be s3://, because the loop reads it too.

Locations inside the file are s3:// addresses or local paths. A relative one is
relative to the AOI config itself. One placeholder keeps an AOI config free of
bucket names:

  {source_data}   TWOD_FIM_SOURCE_DATA_PREFIX, from .env
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

import pyogrio
from botocore.exceptions import ClientError
from recon import storage

KEYS = {
    "name",
    "description",
    "network",
    "lakes",
    "coasts",
    "flow_statistics",
    "flow_reach_id_column",
    "flow_q_lower_column",
    "flow_q_upper_column",
    "flow_aep_columns",
    "dem_source",
    "lulc_source",
    "lulc_lookup",
    "q_bound_factors",
}
# Read by a command on this machine: local paths are fine, relative ones are
# relative to the AOI config.
LOCATIONS = {"network", "lakes", "coasts", "flow_statistics"}
# Read by jobs: only addresses a job can open.
JOB_ADDRESSES = {"dem_source", "lulc_source", "lulc_lookup"}
_JOB_READABLE = re.compile(r"^(s3://|https?://|/vsi)")

# The layer of `network` that holds the reaches, and the column they are keyed by.
NETWORK_LAYER = "reach_network"
NETWORK_REACH_ID = "reach_id"


# --- command line --------------------------------------------------------


def add_argument(parser: argparse.ArgumentParser, optional: str | None = None) -> None:
    """The one argument every AOI command takes, so it reads the same everywhere.

    `optional`, for a command that also runs without one, says what it does then.
    """
    parser.add_argument(
        "aoi_config_path",
        metavar="aoi-config-path",
        nargs="?" if optional else None,
        help=f"the AOI config to read: a local path or an s3:// address{optional or ''}",
    )


# --- reading -------------------------------------------------------------


def fill(value: str) -> str:
    """Replace the placeholder with this deployment's address."""
    return value.replace("{source_data}", storage.source_data_path("").rstrip("/"))


# A JSON string, or a `//` comment. Strings are matched first and kept, so a
# `//` inside one — https:// — is never mistaken for a comment.
_STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*')


def strip_comments(text: str) -> str:
    """JSONC to JSON: drop `//` comments, leave strings alone."""
    return _STRING_OR_COMMENT.sub(
        lambda m: m.group(0) if m.group(0).startswith('"') else "", text
    )


def read_text(location: str) -> str:
    if not location.startswith("s3://"):
        path = Path(location)
        if not path.exists():
            sys.exit(f"No AOI config at {location}")
        return path.read_text()
    bucket, key = storage.parse_s3_path(location)
    try:
        return (
            storage.get_s3_client()
            .get_object(Bucket=bucket, Key=key)["Body"]
            .read()
            .decode()
        )
    except ClientError as exc:
        sys.exit(f"No AOI config at {location}: {exc}")


def load(location: str) -> dict:
    """The AOI config at `location`, with every location in it resolved.

    The file's own location rides along under `_location`, for messages.
    """
    aoi = json.loads(strip_comments(read_text(location)))

    unknown = set(aoi) - KEYS
    if unknown:
        sys.exit(
            f"{location}: unknown key(s) {sorted(unknown)}; an AOI config has {sorted(KEYS)}"
        )

    base = (
        location.rsplit("/", 1)[0]
        if location.startswith("s3://")
        else str(Path(location).resolve().parent)
    )
    resolved = {}
    for key, value in aoi.items():
        if key in LOCATIONS:
            resolved[key] = resolve(value, base)
        elif key in JOB_ADDRESSES:
            resolved[key] = job_address(key, value, location)
        else:
            resolved[key] = value
    return resolved | {"_location": location}


def describe(aoi: dict) -> str:
    """How a command names the AOI config it was given: its location, and its name if it has one."""
    return f"{aoi['_location']} ({aoi['name']})" if "name" in aoi else aoi["_location"]


def job_address(key: str, value: str, where: str = "") -> str:
    """An address a job can open, with the placeholder filled; anything else stops."""
    value = fill(value)
    if not _JOB_READABLE.match(value):
        sys.exit(
            f"{where}: `{key}` is read by jobs, so it must be s3://, https:// or /vsi..., not {value}"
        )
    if key == "lulc_lookup" and not value.startswith("s3://"):
        sys.exit(
            f"{where}: `lulc_lookup` must be s3://, because the loop reads it too; got {value}"
        )
    return value


def local_copy(source: str, tmp_dir: Path) -> Path:
    """A local path to read `source` from, downloading it when it is an s3:// address."""
    if not source.startswith("s3://"):
        path = Path(source)
        if not path.exists():
            sys.exit(f"No such file: {source}")
        return path
    bucket, key = storage.parse_s3_path(source)
    local = tmp_dir / key.replace("/", "_")
    print(f"downloading     {source}")
    try:
        storage.get_s3_client().download_file(bucket, key, str(local))
    except ClientError as exc:
        sys.exit(f"Could not download {source}: {exc}")
    return local


def resolve(value: str, base: str) -> str:
    """An s3:// address or an absolute local path."""
    value = fill(value)
    if value.startswith("s3://") or Path(value).is_absolute():
        return value
    if base.startswith("s3://"):
        return f"{base}/{value}"
    return str(Path(base) / value)


def as_id(value) -> str:
    """An id as text, without the '.0' a float column would put on it."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def network_reach_ids(aoi: dict) -> set[str]:
    """Every reach id in the AOI's own network file."""
    source = require(aoi, "network")
    with tempfile.TemporaryDirectory() as tmp:
        ids = pyogrio.read_dataframe(
            local_copy(source, Path(tmp)),
            layer=NETWORK_LAYER,
            columns=[NETWORK_REACH_ID],
            read_geometry=False,
        )[NETWORK_REACH_ID]
    return {as_id(i) for i in ids}


def require(aoi: dict, key: str) -> str:
    """A location the command cannot run without."""
    if key not in aoi:
        sys.exit(f"{aoi['_location']} has no `{key}`; this command reads it")
    return aoi[key]
