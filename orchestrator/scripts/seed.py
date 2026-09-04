"""Seed the database and storage from hydrofabric GeoPackages.

Dev scaffolding. The real producer of the modelling network is the
modify_network job in twod-fim-jobs; this stands in until it feeds the database
directly, so it is written to be thrown away.

The network only. What is WANTED of it is authored by author_intent.py, which
is a different thing with a different producer and a different lifecycle — and
keeping them apart is what lets intent be re-scoped without truncating
reach_network and destroying every model cascading from it.

Two sources, because they are two different things:

  network.gpkg  the MODIFIED network — reach_id / reach_to_id, terminal flags,
                lake_to_id. This is modify_network's output shape.
  nhf.gpkg      the raw hydrofabric, which is where the lake polygons live
                (layer `lakes_polygons`).

Lake polygons are written to storage as well as to the database. The hydraulic
jobs take a *path* to an outflow-area polygon, not geometry, so a terminal
reach's boundary condition needs its lake to exist as a file the job can read.

Nothing here is usable on its own: a network with no intent authored against it
leaves the loop idle, by design. Run author_intent.py next.

Usage:
    uv run python scripts/seed.py
    uv run python scripts/seed.py --nhf-gpkg testdata/nhf.gpkg
"""

import argparse
import sys
import tempfile
from pathlib import Path
import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
from author_intent import DEFAULT_Q_BOUNDS_PARQUET, load_q_bounds
from recon import db, storage
from recon.config import settings

# The column the network is keyed and sorted by. Named once because the parquet
# writer, the sort, and the row-group statistics all have to agree on it.
REACH_ID_FIELD = "reach_id"

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"
DEFAULT_NETWORK_GPKG = TESTDATA / "network.gpkg"
DEFAULT_NHF_GPKG = TESTDATA / "nhf.gpkg"
# Land cover for the test network, clipped from the NLCD CONUS mosaic to the
# network's extent plus a margin. Half a megabyte instead of 1.4 GB, which is
# what makes it a fixture that can live beside the GeoPackages rather than a
# download every machine has to arrange for itself.
DEFAULT_LULC_TIF = TESTDATA / "lulc.tif"
NETWORK_LAYER = "reach_network"
LAKES_LAYER = "lakes_polygons"


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
            geom = geom.geoms[
                0
            ]  # the column is LineString; a 1-part multi is the same line

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

        rows.append(
            {
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
                "geom": geom.wkt,
            }
        )

    if clipped:
        print(
            f"  {len(clipped)} reach(es) point outside this network; treated as outlet terminals:"
        )
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
    return [
        {"lake_id": str(r["lake_id"]), "geom": r.geometry, "wkt": r.geometry.wkt}
        for _, r in gdf.iterrows()
    ]

def lake_polygon_uri(lake_id: str) -> str:
    """Where a lake's polygon lives in storage.

    Under `shared/` rather than a reach folder: one lake bounds many reaches, so
    it belongs to none of them.
    """
    return (
        f"s3://{settings.artifacts_s3_bucket}/version=v{settings.major_version}"
        f"/shared/lakes/{lake_id}.geojson"
    )


# How many reaches share a row group. Point queries are the only access
# pattern: a job wants ONE reach and reads whichever row group holds it, so this
# is the granularity of that read. Small groups mean less wasted I/O per lookup
# and more metadata to parse; the default (~1M rows) would mean fetching the
# whole network to answer one question.
#
# 8k rows is small enough that a lookup transfers a few hundred KB rather than
# the whole file, and large enough that the footer stays cheap to parse even for
# a continental network. Row groups are only skippable because the file is
# SORTED by reach_id — that is what makes each group's min/max a usable index.
REACH_ROW_GROUP_SIZE = 8192


def export_reach_network(reaches: list[dict]) -> str:
    """Write the reach network as GeoParquet, sorted by reach_id.

    This is what build_model reads INSTEAD OF connecting to the database. A job
    that can open a file needs no credentials, no network route to Postgres, and
    no schema coupling to a table it does not own — the reason the db_uri input
    is gone.

    Sorted by reach_id, with the sort recorded in the file metadata, so a reader
    can use each row group's min/max to skip straight to the group holding a
    reach. Unsorted, those statistics overlap and every group has to be read.
    """
    gdf = gpd.GeoDataFrame(
        [{k: v for k, v in r.items() if k != "geom"} for r in reaches],
        geometry=gpd.GeoSeries.from_wkt([r["geom"] for r in reaches]),
        crs=5070,
    ).sort_values(REACH_ID_FIELD, ignore_index=True)

    uri = storage.reach_network_path()
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / storage.REACH_NETWORK_FILENAME
        gdf.to_parquet(
            local,
            index=False,
            row_group_size=REACH_ROW_GROUP_SIZE,
            # Declares the file sorted so a reader can trust the row-group
            # statistics rather than rediscovering the order.
            sorting_columns=[pq.SortingColumn(gdf.columns.get_loc(REACH_ID_FIELD))],
        )
        bucket, key = storage.parse_s3_path(uri)
        storage.get_s3_client().put_object(
            Bucket=bucket, Key=key, Body=local.read_bytes()
        )
    return uri


def export_lulc(lulc_tif: Path) -> str:
    """Publish the land-cover raster to storage, where jobs can address it.

    Uploaded rather than mounted. A mount has to be arranged by whatever starts
    the container — which under SEPEX means every process definition declaring
    the same volume, and a cloud deployment needing a different answer
    entirely. An object in the bucket is reachable from all of them with the
    credentials jobs already carry.
    """
    if not lulc_tif.exists():
        sys.exit(f"No such land cover raster: {lulc_tif}")
    uri = storage.lulc_path()
    bucket, key = storage.parse_s3_path(uri)
    storage.get_s3_client().put_object(
        Bucket=bucket, Key=key, Body=lulc_tif.read_bytes()
    )
    return uri


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


def seed(network_gpkg: Path, nhf_gpkg: Path, lulc_tif: Path, q_bound_parquet: Path) -> None:
    reaches = load_network(network_gpkg)
    # Bounds are intent, and author_intent.py is what writes them to
    # desired_state. They are resolved here too because the reach network
    # parquet carries the same three columns; drop them from that file and this
    # call, and the import above, go with them.
    reaches = load_q_bounds(q_bound_parquet, reaches)
    lakes = load_lakes(nhf_gpkg)

    # CASCADE reaches desired_state and every materialized_* table. That is the
    # cost of reloading the network, and the reason authoring is a separate
    # script: re-scoping intent must not come through here.
    with db.connect() as conn:
        conn.execute("TRUNCATE reach_network, lakes, coasts CASCADE")

        for lake in lakes:
            conn.execute(
                "INSERT INTO lakes (lake_id, geom) VALUES (%s, ST_GeomFromText(%s, 5070))",
                (lake["lake_id"], lake["wkt"]),
            )

        # One transaction, any order: the self FK is deferred to commit.
        for r in reaches:
            conn.execute(
                """INSERT INTO reach_network
                       (reach_id, reach_to_id, is_terminal, is_headwater, terminal_reason,
                        lake_to_id, coast_to_id, lake_inlet, lake_outlet, is_trimmed,
                        total_da_sqkm, stream_order, length_km, geom)
                   VALUES (%(reach_id)s, %(reach_to_id)s, %(is_terminal)s, %(is_headwater)s,
                           %(terminal_reason)s, %(lake_to_id)s, %(coast_to_id)s, %(lake_inlet)s,
                           %(lake_outlet)s, %(is_trimmed)s, %(total_da_sqkm)s, %(stream_order)s,
                           %(length_km)s, ST_GeomFromText(%(geom)s, 5070))""",
                r,
            )

    written = export_lake_polygons(lakes)
    network_uri = export_reach_network(reaches)
    lulc_uri = export_lulc(lulc_tif)

    summary = db.one("""
        SELECT count(*) AS reaches,
               count(*) FILTER (WHERE is_terminal) AS terminals,
               count(*) FILTER (WHERE terminal_reason = 'lake')  AS lake_terminals,
               count(*) FILTER (WHERE terminal_reason = 'coast') AS coast_terminals,
               count(*) FILTER (WHERE terminal_reason = 'outlet') AS outlet_terminals
        FROM reach_network""")
    print(f"reaches         {summary['reaches']}")
    print(
        f"terminals       {summary['terminals']} "
        f"(lake {summary['lake_terminals']}, coast {summary['coast_terminals']}, "
        f"outlet {summary['outlet_terminals']})"
    )
    print(f"lakes           {len(lakes)}")
    for uri in written:
        print(f"  exported      {uri}")
    print(f"  network       {network_uri}")
    print(f"  land cover    {lulc_uri}")
    if summary["outlet_terminals"]:
        # Not a warning any more. An outlet names no lake or coast, and needs
        # none: the outflow polygon input is optional and the run job derives an
        # area from the model's own domain and centreline when it is absent.
        print(
            f"\nNote: {summary['outlet_terminals']} outlet terminal(s) name no water body.\n"
            "      Their outflow area is derived by the run job from the model itself."
        )
    print("\nNo intent is authored yet. Run scripts/author_intent.py next.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--network-gpkg",
        type=Path,
        default=DEFAULT_NETWORK_GPKG,
        help="modified network (modify_network output)",
    )
    ap.add_argument(
        "--nhf-gpkg",
        type=Path,
        default=DEFAULT_NHF_GPKG,
        help="hydrofabric holding the lakes_polygons layer",
    )
    ap.add_argument(
        "--lulc-tif",
        type=Path,
        default=DEFAULT_LULC_TIF,
        help="land cover raster to publish to storage",
    )
    ap.add_argument(
        "--q-bound-parquet",
        type=Path,
        default=DEFAULT_Q_BOUNDS_PARQUET,
        help="flow statistics written into the reach network parquet",
    )
    args = ap.parse_args()

    for path in (args.network_gpkg, args.nhf_gpkg):
        if not path.exists():
            sys.exit(f"No such GeoPackage: {path}")
    if not args.lulc_tif.exists():
        sys.exit(f"No such land cover raster: {args.lulc_tif}")

    print(f"network  {args.network_gpkg}")
    print(f"nhf      {args.nhf_gpkg}")
    print(f"lulc     {args.lulc_tif}\n")
    seed(args.network_gpkg, args.nhf_gpkg, args.lulc_tif, args.q_bound_parquet)


if __name__ == "__main__":
    main()
