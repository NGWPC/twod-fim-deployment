"""AOI config loading.

Reads an AOI config from a local path or an s3:// address, fills the
{source_data} placeholder from the environment, and resolves relative paths
against the config's own location. See example.aoi_config.jsonc for the format.
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
LOCATIONS = {"network", "lakes", "coasts", "flow_statistics"}
JOB_ADDRESSES = {"dem_source", "lulc_source", "lulc_lookup"}
_JOB_READABLE = re.compile(r"^(s3://|https?://|/vsi)")

NETWORK_LAYER = "reach_network"
NETWORK_REACH_ID = "reach_id"


def add_argument(parser: argparse.ArgumentParser, optional: str | None = None) -> None:
    """The one argument every AOI command takes, so it reads the same everywhere."""
    parser.add_argument(
        "aoi_config_path",
        metavar="aoi-config-path",
        nargs="?" if optional else None,
        help=f"the AOI config to read: a local path or an s3:// address{optional or ''}",
    )


def fill(value: str) -> str:
    """Replace the placeholder with this deployment's address."""
    return value.replace("{source_data}", storage.source_data_path("").rstrip("/"))


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
    """The AOI config at `location`, with every location in it resolved."""
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
