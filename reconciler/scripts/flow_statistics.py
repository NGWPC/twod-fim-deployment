"""Flow statistics lookup.

Resolves which per-reach flow table an AOI reads and which columns it uses,
falling back to the system-wide settings, and reads the table.
"""

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import aoi_config
from recon.config import settings


@dataclass(frozen=True)
class FlowStatistics:
    """The flow table an AOI reads, and what its columns are called."""

    location: str
    is_default: bool
    reach_id_column: str
    q_lower_column: str
    q_upper_column: str
    aep_columns: list[str]


def for_aoi(aoi: dict) -> FlowStatistics:
    """The AOI's own table and column names, each falling back to the setting of the same name."""
    aep_columns = aoi["flow_aep_columns"] if "flow_aep_columns" in aoi else settings.flow_aep_columns
    if not (isinstance(aep_columns, list) and aep_columns and all(isinstance(c, str) for c in aep_columns)):
        sys.exit(f"`flow_aep_columns` must be a non-empty list of column names, not {aep_columns!r}")
    return FlowStatistics(
        location=aoi.get("flow_statistics") or aoi_config.fill(settings.flow_statistics),
        is_default="flow_statistics" not in aoi,
        reach_id_column=aoi.get("flow_reach_id_column") or settings.flow_reach_id_column,
        q_lower_column=aoi.get("flow_q_lower_column") or settings.flow_q_lower_column,
        q_upper_column=aoi.get("flow_q_upper_column") or settings.flow_q_upper_column,
        aep_columns=aep_columns,
    )


def describe(flows: FlowStatistics) -> str:
    """How a command names the table it read."""
    return f"{flows.location}{' (system default)' if flows.is_default else ''}"


def flow_id(reach_id: str) -> str:
    """The id a reach's flows are listed under: its flowpath, without a split suffix."""
    return reach_id.split("_")[0]


def read(path: Path, reach_id_column: str, columns: Mapping[str, str]) -> pd.DataFrame:
    """The table at `path`, indexed by reach id as text, holding just `columns`."""
    if path.suffix.lower() == ".csv":
        table = pd.read_csv(path, dtype={reach_id_column: str})
    else:
        table = pd.read_parquet(path)
    if table.index.name != reach_id_column:
        if reach_id_column not in table.columns:
            sys.exit(f"{path} has no reach id column or index named {reach_id_column!r}; name it with flow_reach_id_column")
        table = table.set_index(reach_id_column)
    missing = {column: key for column, key in columns.items() if column not in table.columns}
    if missing:
        keys = sorted(set(missing.values()))
        sys.exit(f"{path} has no column(s) {sorted(missing)}; name them with {' / '.join(keys)}")
    if table.index.hasnans:
        sys.exit(f"{path}: {int(table.index.isna().sum())} row(s) have no reach id")
    table.index = table.index.map(aoi_config.as_id)
    return table[list(columns)]
