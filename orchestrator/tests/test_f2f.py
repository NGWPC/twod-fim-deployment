"""f2f.py: the parts of a flows2fim export that do not need a database or docker.

Library names have to agree with what the controls table says, the network has
to end where the export does, and a forecast has to leave out what it cannot
forecast rather than stop. The full run is exercised against a stack.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import f2f  # noqa: E402


def test_library_folders_follow_the_imposed_stage():
    assert f2f.library_stage_dir("nd", 0.0) == "z_nd"
    assert f2f.library_stage_dir("kwse", 224.25) == "z_224_2"
    assert f2f.library_stage_dir("kwse", 101.0) == "z_101_0"


def test_library_grids_are_named_by_integral_flow():
    assert f2f.library_grid_name(150.0) == "f_150.tif"


def test_the_library_path_of_a_scenario():
    source = {"reach_id": 7, "boundary_condition": "kwse", "ds_wse": 12.5, "us_flow": 40.0}
    assert f2f.library_path(Path("/lib"), source) == Path("/lib/7/z_12_5/f_40.tif")


def test_a_link_out_of_the_export_becomes_an_outlet():
    links = [("1", "2"), ("2", "3_1"), ("3_1", None), ("4", "9")]
    rows, cut = f2f.network_rows(links, {"1", "2", "4"})
    assert rows == [("1", "2"), ("2", None), ("4", None)]
    assert cut == [("2", "3_1"), ("4", "9")]


def test_start_reaches_are_the_reaches_with_nowhere_to_drain_at_normal_depth(tmp_path):
    path = tmp_path / "start_reaches.csv"
    numbers = {"1": 1, "2_1": 2, "4": 3}
    starts = f2f.write_start_reaches(path, [("1", "2_1"), ("2_1", None), ("4", None)], numbers)
    assert starts == ["2_1", "4"]
    assert path.read_text().splitlines() == ["reach_id,control_stage", "2,nd", "3,nd"]


def test_flows2fim_numbers_are_kept_across_exports(tmp_path):
    path = tmp_path / "scenarios.db"
    first = f2f.flows2fim_numbers(path, {"20", "10_2"})
    assert first == {"10_2": 1, "20": 2}
    f2f.write_scenarios_db(path, [], [], [], first, {"_location": "aoi.json"})
    second = f2f.flows2fim_numbers(path, {"20", "10_1", "10_2"})
    assert second == {"10_2": 1, "20": 2, "10_1": 3}


def test_a_forecast_leaves_out_reaches_without_flows():
    flows = pd.DataFrame({"f5year": [10.0, None, 30.0]}, index=pd.Index(["1", "2", "3"], name="reach_id"))
    rows = f2f.forecast(flows, "f5year", {7: "1", 8: "2", 9: "5"})
    assert list(rows.columns) == ["feature_id", "discharge"]
    assert rows.to_dict("records") == [{"feature_id": 7, "discharge": 10.0}]


def test_every_piece_of_a_split_reach_is_forecast_with_its_flowpaths_flow():
    flows = pd.DataFrame({"f5year": [30.0]}, index=pd.Index(["3"], name="reach_id"))
    rows = f2f.forecast(flows, "f5year", {1: "3_1", 2: "3_2"})
    assert rows.to_dict("records") == [{"feature_id": 1, "discharge": 30.0}, {"feature_id": 2, "discharge": 30.0}]


def test_vrt_sources_become_relative_to_the_vrt(tmp_path):
    out = tmp_path / "out"
    vrt = out / "aep" / "f5year" / "depth.vrt"
    vrt.parent.mkdir(parents=True)
    vrt.write_text(
        '<VRTDataset rasterXSize="1" rasterYSize="1"><VRTRasterBand dataType="Float32" band="1">'
        "<SimpleSource><SourceFilename>/out/library/7/z_nd/f_40.tif</SourceFilename></SimpleSource>"
        "</VRTRasterBand></VRTDataset>"
    )
    f2f.post_process_vrt(vrt, f2f.Flows2Fim(f2f.IMAGE, out))
    text = vrt.read_text()
    assert '<SourceFilename relativeToVRT="1">../../library/7/z_nd/f_40.tif</SourceFilename>' in text
    assert "<PixelFunctionType>max</PixelFunctionType>" in text
    assert 'subClass="VRTDerivedRasterBand"' in text
