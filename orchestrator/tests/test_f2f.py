"""f2f.py: the parts of a flows2fim export that do not need a database or docker.

Library names have to agree with what the controls table says, the network has
to end where the export does, and a forecast has to leave out what it cannot
forecast rather than stop. The full run is exercised against a stack.
"""

import sqlite3
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import f2f  # noqa: E402


def test_library_folders_follow_the_imposed_stage():
    assert f2f.library_stage_dir("nd", 0.0) == "z_nd"
    assert f2f.library_stage_dir("kwse", 224.25) == "z_224_2"
    assert f2f.library_stage_dir("kwse", 101.0) == "z_101_0"


def test_library_grids_are_named_by_integral_flow():
    assert f2f.library_grid_name(150.0) == "f_150.tif"


def test_the_library_name_of_a_scenario():
    source = {"reach_id": 7, "boundary_condition": "kwse", "ds_wse": 12.5, "us_flow": 40.0}
    assert f2f.library_name(source) == "library/7/z_12_5/f_40.tif"


def test_an_out_dir_in_storage_is_worked_on_locally_and_opened_through_vsis3(tmp_path):
    out = f2f.OutDir("s3://bucket/exports/huc/", tmp_path)
    assert out.local("scenarios.db") == tmp_path / "scenarios.db"
    assert out.address("scenarios.db") == "s3://bucket/exports/huc/scenarios.db"
    assert out.gdal_path("library") == "/vsis3/bucket/exports/huc/library"


def test_a_local_out_dir_is_its_own_working_folder(tmp_path):
    out = f2f.OutDir(str(tmp_path / "out"), tmp_path / "unused")
    assert out.local("scenarios.db") == tmp_path / "out" / "scenarios.db"


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


def test_flows2fim_numbers_follow_reach_id_order():
    assert f2f.flows2fim_numbers({"20", "10_2", "10_1"}) == {"10_1": 1, "10_2": 2, "20": 3}


def test_an_export_goes_into_an_empty_out_dir(tmp_path):
    out = f2f.OutDir(str(tmp_path / "out"), tmp_path / "unused")
    assert not out.holds()
    (tmp_path / "out").mkdir()
    assert not out.holds()
    (tmp_path / "out" / "scenarios.db").touch()
    assert out.holds()
    with pytest.raises(SystemExit, match="not empty"):
        f2f.export_scenarios(None, out)


def test_an_interrupted_download_is_not_taken_for_a_whole_grid(tmp_path):
    destination = tmp_path / "library" / "1" / "z_nd" / "f_5.tif"
    destination.parent.mkdir(parents=True)
    destination.with_suffix(".tif.part").write_bytes(b"half")

    class S3:
        def download_file(self, bucket, key, path):
            Path(path).write_bytes(b"whole")

    assert f2f.download_grid(S3(), {"depth_grid": "s3://b/k.tif"}, destination) == "copied"
    assert destination.read_bytes() == b"whole"
    assert f2f.download_grid(S3(), {"depth_grid": "s3://b/k.tif"}, destination) == "present"


def test_an_export_of_every_materialized_reach_names_no_aoi_config(tmp_path):
    path = tmp_path / "scenarios.db"
    f2f.write_scenarios_db(path, [], [], [], {}, None)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT aoi_config FROM export_provenance").fetchone() == (None,)


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
    f2f.post_process_vrt(vrt, f2f.OutDir(str(out), tmp_path / "unused"))
    text = vrt.read_text()
    assert '<SourceFilename relativeToVRT="1">../../library/7/z_nd/f_40.tif</SourceFilename>' in text
    assert "<PixelFunctionType>max</PixelFunctionType>" in text
    assert 'subClass="VRTDerivedRasterBand"' in text


def test_vrt_sources_in_storage_stay_vsis3_paths(tmp_path):
    out = f2f.OutDir("s3://bucket/exports/huc", tmp_path)
    vrt = out.local("aep/f5year/depth.vrt")
    vrt.parent.mkdir(parents=True)
    source = "/vsis3/bucket/exports/huc/library/7/z_nd/f_40.tif"
    vrt.write_text(
        '<VRTDataset rasterXSize="1" rasterYSize="1"><VRTRasterBand dataType="Float32" band="1">'
        f"<SimpleSource><SourceFilename>{source}</SourceFilename></SimpleSource>"
        "</VRTRasterBand></VRTDataset>"
    )
    f2f.post_process_vrt(vrt, out)
    text = vrt.read_text()
    assert f"<SourceFilename>{source}</SourceFilename>" in text
    assert "<PixelFunctionType>max</PixelFunctionType>" in text


def test_an_out_dir_inside_the_storage_or_source_data_root_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(f2f.settings, "twod_fim_data_root_prefix", "s3://bucket/version=1")
    monkeypatch.setattr(f2f.settings, "twod_fim_source_data_prefix", "s3://bucket/source_data")
    for location in ("s3://bucket/version=1", "s3://bucket/version=1/f2f", "s3://bucket/source_data/x"):
        with pytest.raises(SystemExit):
            f2f.OutDir(location, tmp_path)
    assert f2f.OutDir("s3://bucket/version=10/f2f", tmp_path).in_storage


def test_a_model_asset_is_its_href_or_the_file_beside_the_manifest():
    manifest = "s3://b/root/models/reach=1/abc_N1S1E1W1/model_manifest.json"
    assert f2f.model_asset_address(manifest, "s3://b/elsewhere/domain.geojson") == "s3://b/elsewhere/domain.geojson"
    assert f2f.model_asset_address(manifest, "tests/x/domain.geojson") == "s3://b/root/models/reach=1/abc_N1S1E1W1/domain.geojson"


def test_model_layers_are_one_layer_each_with_the_reach_id_first(tmp_path):
    from shapely.geometry import LineString, box

    def model(reach_id, x):
        return {
            "domains": f2f.tag_reach(gpd.GeoDataFrame({"offset_str": ["N1"]}, geometry=[box(x, 0, x + 1, 1)], crs=5070), reach_id),
            "inflows": f2f.tag_reach(gpd.GeoDataFrame({"ind": [1]}, geometry=[LineString([(x, 0), (x, 1)])], crs=5070), reach_id),
            # A centerline carries the reach id the network gave it, as a float in older models.
            "reaches": f2f.tag_reach(
                gpd.GeoDataFrame({"reach_id": [1.0], "stream_order": [3]}, geometry=[LineString([(x, 0), (x + 1, 1)])], crs=5070),
                reach_id,
            ),
        }

    path = tmp_path / "models.gpkg"
    counts = f2f.write_model_layers(path, [model("10_1", 0), model("20", 5)])
    assert counts == {"domains": 2, "inflows": 2, "reaches": 2}
    reaches = gpd.read_file(path, layer="reaches")
    assert list(reaches.columns[:2]) == ["reach_id", "stream_order"]
    assert reaches["reach_id"].tolist() == ["10_1", "20"]
    assert gpd.read_file(path, layer="domains").crs.to_epsg() == 5070
