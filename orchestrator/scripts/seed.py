"""Seed the database and storage from hydrofabric GeoPackages.

Dev scaffolding. The real producer of the modelling network is the
modify_network job in twod-fim-jobs, and the real authoring of intent is a
person or an upstream system; this stands in until both feed the database
directly, so it is written to be thrown away.

Two sources, because they are two different things:

  network.gpkg  the MODIFIED network — reach_id / reach_to_id, terminal flags,
                lake_to_id. This is modify_network's output shape.
  nhf.gpkg      the raw hydrofabric, which is where the lake polygons live
                (layer `lakes_polygons`).

Lake polygons are written to storage as well as to the database. The hydraulic
jobs take a *path* to an outflow-area polygon, not geometry, so a terminal
reach's boundary condition needs its lake to exist as a file the job can read.

Usage:
    uv run python scripts/seed.py
    uv run python scripts/seed.py --nhf-gpkg testdata/nhf.gpkg --keep-storage
"""

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

from recon import db, storage
from recon.config import settings

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"
DEFAULT_NETWORK_GPKG = TESTDATA / "network.gpkg"
DEFAULT_NHF_GPKG = TESTDATA / "nhf.gpkg"
NETWORK_LAYER = "reach_network"
LAKES_LAYER = "lakes_polygons"

# The hydrofabric no longer carries slope, but build_model still reads it from
# reach_network (REACH_FIELDS in twod-fim-jobs) and records it as the model's
# properties.slope — which becomes the normal-depth boundary condition and the
# `nd=` path component. Placeholder until the job stops asking.
PLACEHOLDER_SLOPE = 0.001

# Mirrors what the deployed job images bake in (twod_fim_jobs/consts.py). The
# loop predicts artifact addresses from these, so they must match the images or
# nothing it builds will be found where it looked.
SDR_COMMIT = "826a602ddcaf58bf4081dc04b65ba15b82cc8c8a"
SOLVER = "lisflood"
SOLVER_VERSION = "8.1.0"  # LISFLOOD-FP version reported by the run image
DEM_SOURCE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/USGS_Seamless_DEM_13.vrt"
LULC_SOURCE = "/data/Annual_NLCD_LndCov_2023_CU_C1V0.tif"
LULC_LOOKUP = {"11": 0.04, "21": 0.04, "22": 0.1, "23": 0.08, "24": 0.15,
               "31": 0.025, "41": 0.16, "42": 0.16, "43": 0.16, "52": 0.1,
               "71": 0.035, "81": 0.03, "82": 0.035, "90": 0.12, "95": 0.07}


def load_network(gpkg_path: Path, layer: str = NETWORK_LAYER) -> list[dict]:
    """Read the modified network into insertable rows. No database contact.

    Deliberately does NOT order the rows: reach_to_id's self FK is DEFERRABLE
    INITIALLY DEFERRED, so the whole network loads in any order inside one
    transaction. Nor does it validate terminal_reason — a CHECK constraint owns
    that rule, and re-checking it here would be a second copy free to drift.
    """
    gdf = gpd.read_file(gpkg_path, layer=layer)

    missing = {"reach_id", "reach_to_id"} - set(gdf.columns)
    if missing:
        sys.exit(f"{gpkg_path} layer {layer} is missing: {sorted(missing)}")

    if gdf.crs and gdf.crs.to_epsg() != 5070:
        print(f"  reprojecting {gdf.crs.to_string()} -> EPSG:5070")
        gdf = gdf.to_crs(epsg=5070)

    known = set(gdf.columns)
    in_file = set(gdf["reach_id"].astype("int64"))
    rows, clipped = [], []
    for _, r in gdf.iterrows():
        def value(col, cast, fallback=None):
            if col not in known or pd.isna(r[col]):
                return fallback
            return cast(r[col])

        geom = r.geometry
        if geom.geom_type == "MultiLineString" and len(geom.geoms) == 1:
            geom = geom.geoms[0]  # the column is LineString; a 1-part multi is the same line

        reach_id = int(r["reach_id"])
        reach_to_id = value("reach_to_id", int)
        is_terminal = bool(value("is_terminal", bool, False))
        terminal_reason = value("terminal_reason", str)

        # This file is a clip of a larger network, so a reach at its edge can
        # point at a downstream neighbour that was not included. The FK cannot
        # hold a dangling reference, and leaving the link NULL while the reach
        # claims to be non-terminal would make it wait forever on a reach that
        # does not exist. Treating the clip edge as an outlet is true as far as
        # this deployment is concerned — but note such reaches can build a model
        # and can never run ND, because an outlet has no outflow polygon.
        if reach_to_id is not None and reach_to_id not in in_file:
            clipped.append((reach_id, reach_to_id))
            reach_to_id, is_terminal, terminal_reason = None, True, "outlet"

        rows.append({
            "reach_id": reach_id,
            "reach_to_id": reach_to_id,
            "is_terminal": is_terminal,
            "is_headwater": bool(value("is_headwater", bool, False)),
            "terminal_reason": terminal_reason,
            "lake_to_id": value("lake_to_id", str),
            "coast_to_id": value("coast_to_id", str),
            "lake_inlet": bool(value("lake_inlet", bool, False)),
            "lake_outlet": bool(value("lake_outlet", bool, False)),
            "is_trimmed": bool(value("is_trimmed", bool, False)),
            "total_da_sqkm": value("total_da_sqkm", float),
            "stream_order": value("stream_order", int),
            "length_km": value("length_km", float),
            "slope": value("slope", float, PLACEHOLDER_SLOPE),
            "geom": geom.wkt,
        })

    if clipped:
        print(f"  {len(clipped)} reach(es) point outside this file; treated as outlet terminals:")
        for reach_id, missing in clipped:
            print(f"    {reach_id} -> {missing} (not in file)")
    return rows


def load_lakes(gpkg_path: Path, layer: str = LAKES_LAYER) -> list[dict]:
    """Read lake polygons. Multipart geometry is kept as-is."""
    gdf = gpd.read_file(gpkg_path, layer=layer)
    if "lake_id" not in gdf.columns:
        sys.exit(f"{gpkg_path} layer {layer} has no lake_id column")
    if gdf.crs and gdf.crs.to_epsg() != 5070:
        gdf = gdf.to_crs(epsg=5070)
    return [{"lake_id": str(r["lake_id"]), "geom": r.geometry, "wkt": r.geometry.wkt}
            for _, r in gdf.iterrows()]


def lake_polygon_uri(lake_id: str) -> str:
    """Where a lake's polygon lives in storage.

    Under `shared/` rather than a reach folder: one lake bounds many reaches, so
    it belongs to none of them.
    """
    return (f"s3://{settings.artifacts_s3_bucket}/version=v{settings.major_version}"
            f"/shared/lakes/{lake_id}.geojson")


def export_lake_polygons(lakes: list[dict]) -> list[str]:
    """Write each lake to storage as GeoJSON, and return the paths written."""
    s3 = storage.get_s3_client()
    written = []
    for lake in lakes:
        uri = lake_polygon_uri(lake["lake_id"])
        bucket, key = storage.parse_s3_path(uri)
        body = gpd.GeoSeries([lake["geom"]], crs=5070).to_json()
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode())
        written.append(uri)
    return written


def seed(network_gpkg: Path, nhf_gpkg: Path) -> None:
    reaches = load_network(network_gpkg)
    lakes = load_lakes(nhf_gpkg)

    with db.connect() as conn:
        conn.execute("TRUNCATE reach_network, lakes, coasts CASCADE")
        conn.execute("DELETE FROM desired_state_defaults")
        conn.execute(
            """INSERT INTO desired_state_defaults
                   (sdr_commit, grid_resolution, epsg_code, dem_source, lulc_source,
                    lulc_lookup, solver, solver_version)
               VALUES (%s, 10, 5070, %s, %s, %s, %s, %s)""",
            (SDR_COMMIT, DEM_SOURCE, LULC_SOURCE, json.dumps(LULC_LOOKUP),
             SOLVER, SOLVER_VERSION))

        for lake in lakes:
            conn.execute("INSERT INTO lakes (lake_id, geom) VALUES (%s, ST_GeomFromText(%s, 5070))",
                         (lake["lake_id"], lake["wkt"]))

        # One transaction, any order: the self FK is deferred to commit.
        for r in reaches:
            conn.execute(
                """INSERT INTO reach_network
                       (reach_id, reach_to_id, is_terminal, is_headwater, terminal_reason,
                        lake_to_id, coast_to_id, lake_inlet, lake_outlet, is_trimmed,
                        total_da_sqkm, stream_order, length_km, slope, geom)
                   VALUES (%(reach_id)s, %(reach_to_id)s, %(is_terminal)s, %(is_headwater)s,
                           %(terminal_reason)s, %(lake_to_id)s, %(coast_to_id)s, %(lake_inlet)s,
                           %(lake_outlet)s, %(is_trimmed)s, %(total_da_sqkm)s, %(stream_order)s,
                           %(length_km)s, %(slope)s, ST_GeomFromText(%(geom)s, 5070))""", r)

        # Intent: every reach, everything defaulted.
        conn.execute("INSERT INTO desired_state (reach_id) SELECT reach_id FROM reach_network")

    written = export_lake_polygons(lakes)

    summary = db.one("""
        SELECT count(*) AS reaches,
               count(*) FILTER (WHERE is_terminal) AS terminals,
               count(*) FILTER (WHERE terminal_reason = 'lake')  AS lake_terminals,
               count(*) FILTER (WHERE terminal_reason = 'coast') AS coast_terminals,
               count(*) FILTER (WHERE terminal_reason = 'outlet') AS outlet_terminals
        FROM reach_network""")
    print(f"reaches         {summary['reaches']}")
    print(f"terminals       {summary['terminals']} "
          f"(lake {summary['lake_terminals']}, coast {summary['coast_terminals']}, "
          f"outlet {summary['outlet_terminals']})")
    print(f"lakes           {len(lakes)}")
    for uri in written:
        print(f"  exported      {uri}")
    if summary["outlet_terminals"]:
        print("\nWARNING: outlet terminals have no boundary polygon source, so ND\n"
              "         cannot be submitted for them. See reconciliation-loop.md.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network-gpkg", type=Path, default=DEFAULT_NETWORK_GPKG,
                    help="modified network (modify_network output)")
    ap.add_argument("--nhf-gpkg", type=Path, default=DEFAULT_NHF_GPKG,
                    help="hydrofabric holding the lakes_polygons layer")
    args = ap.parse_args()

    for path in (args.network_gpkg, args.nhf_gpkg):
        if not path.exists():
            sys.exit(f"No such GeoPackage: {path}")

    print(f"network  {args.network_gpkg}")
    print(f"nhf      {args.nhf_gpkg}\n")
    seed(args.network_gpkg, args.nhf_gpkg)


if __name__ == "__main__":
    main()
