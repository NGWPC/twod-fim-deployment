"""flow_statistics.py: which flow table an AOI reads, and reading any shape of it.

Authoring and the AEP export both read through here, so the fallback from an
AOI config to the system-wide settings, and the tolerance for a table's shape,
are checked once for both.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import flow_statistics  # noqa: E402
from recon.config import settings  # noqa: E402


def test_an_aoi_without_flow_keys_reads_the_settings():
    flows = flow_statistics.for_aoi({"_location": "aoi.json"})
    assert flows.is_default
    assert flows.location.endswith(settings.flow_statistics.removeprefix("{source_data}"))
    assert flows.reach_id_column == settings.flow_reach_id_column
    assert flows.q_lower_column == settings.flow_q_lower_column
    assert flows.q_upper_column == settings.flow_q_upper_column
    assert flows.aep_columns == settings.flow_aep_columns


def test_an_aoi_names_its_own_table_and_columns():
    flows = flow_statistics.for_aoi({
        "flow_statistics": "/data/flows.csv",
        "flow_reach_id_column": "ID",
        "flow_q_lower_column": "low",
        "flow_q_upper_column": "high",
        "flow_aep_columns": ["q10"],
    })
    assert not flows.is_default
    assert (flows.location, flows.reach_id_column, flows.q_lower_column, flows.q_upper_column, flows.aep_columns) == (
        "/data/flows.csv", "ID", "low", "high", ["q10"]
    )


@pytest.mark.parametrize("columns", ["f5year", [], [5]])
def test_aep_columns_must_be_a_list_of_names(columns):
    with pytest.raises(SystemExit, match="flow_aep_columns"):
        flow_statistics.for_aoi({"flow_aep_columns": columns})


def write(tmp_path: Path, table: pd.DataFrame, name: str) -> Path:
    path = tmp_path / name
    table.to_csv(path, index=False) if name.endswith(".csv") else table.to_parquet(path)
    return path


def test_reach_id_as_the_index_or_a_column(tmp_path):
    table = pd.DataFrame({"reach_id": [1, 2], "f5year": [10.0, 20.0], "other": ["a", "b"]})
    as_index = write(tmp_path, table.set_index("reach_id"), "index.parquet")
    as_column = write(tmp_path, table, "column.csv")
    for path in (as_index, as_column):
        read = flow_statistics.read(path, "reach_id", {"f5year": "flow_aep_columns"})
        assert list(read.columns) == ["f5year"]
        assert read.loc[2, "f5year"] == 20.0


def test_a_missing_column_names_the_key_to_fix(tmp_path):
    path = write(tmp_path, pd.DataFrame({"reach_id": [1], "f5year": [1.0]}), "flows.parquet")
    with pytest.raises(SystemExit, match=r"\['f50year'\].*flow_aep_columns"):
        flow_statistics.read(path, "reach_id", {"f5year": "flow_aep_columns", "f50year": "flow_aep_columns"})
    with pytest.raises(SystemExit, match="flow_reach_id_column"):
        flow_statistics.read(path, "ID", {"f5year": "flow_aep_columns"})


def test_reach_ids_must_be_integers(tmp_path):
    path = write(tmp_path, pd.DataFrame({"reach_id": ["a"], "f5year": [1.0]}), "flows.csv")
    with pytest.raises(SystemExit, match="integers"):
        flow_statistics.read(path, "reach_id", {"f5year": "flow_aep_columns"})
