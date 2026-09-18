"""Seed the database from an AOI config's sources, and publish to workspace/ what jobs read.

Three separate commands, each reading its source from an AOI config
(aoi_config.py):

  lakes    every lake polygon in `lakes` (layer lakes_polygons, keyed by lake_id)
           into the lakes table, and each to workspace/lakes/<lake_id>.geojson
  coasts   every coastal influence polygon in `coasts` (layer
           coastal_influence_polygons, keyed by coast_id) into the coasts table,
           and each to workspace/coasts/<coast_id>.geojson
  network  modify_network's network.gpkg in `network` (layer reach_network) into
           reach_network, then the whole table to
           workspace/reach_network.parquet

Lakes and coasts are whole datasets, usually seeded once per storage root
however many networks follow. A network names the lakes and coasts its terminal
reaches drain into, so those must already be seeded; `network` checks, and says
which are missing.

The polygons go to storage because the jobs take a PATH to a terminal reach's
outflow area, not geometry. The network goes to storage because build_model
reads that file instead of connecting to the database.

Adds and updates, never deletes. A row already present is updated in place, so
seeding the same data twice changes nothing. A clean slate is `just wipe-db`.

Intent is not authored here. Run author_intent.py after the network.

Usage:
    uv run python scripts/seed.py lakes|coasts|network <aoi-config-path>

<aoi-config-path> is a local path or an s3:// address.
"""

import argparse
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import shapely

import aoi_config
from recon import db, storage

# The column the network is keyed and sorted by. Named once because the parquet
# writer, the sort, and the row-group statistics all have to agree on it.
REACH_ID_FIELD = "reach_id"
NETWORK_LAYER = aoi_config.NETWORK_LAYER

# kind -> (AOI config key, layer, id field, table). A kind's table is keyed by its id
# field, and boundary_polygon_path files it under workspace/<kind>s/.
WATER_BODIES = {
    "lake": ("lakes", "lakes_polygons", "lake_id", "lakes"),
    "coast": ("coasts", "coastal_influence_polygons", "coast_id", "coasts"),
}

# Every reach_network column but the geometry, in the order the parquet carries.
NETWORK_COLUMNS = (
    "reach_id",
    "reach_to_id",
    "is_terminal",
    "is_headwater",
    "terminal_reason",
    "lake_to_id",
    "coast_to_id",
    "lake_inlet",
    "lake_outlet",
    "is_trimmed",
    "total_da_sqkm",
    "stream_order",
    "length_km",
)


# --- lakes and coasts ----------------------------------------------------


def load_water_bodies(gpkg_path: Path, layer: str, id_field: str) -> list[dict]:
    """Every polygon in the layer, one per id.

    Parts that share an id are unioned, because the table holds one polygon per
    body and a job reads one file for it.
    """
    gdf = gpd.read_file(gpkg_path, layer=layer)
    if id_field not in gdf.columns:
        sys.exit(f"{gpkg_path} layer {layer} has no {id_field} column")
    if gdf.crs and gdf.crs.to_epsg() != 5070:
        gdf = gdf.to_crs(epsg=5070)
    gdf[id_field] = gdf[id_field].map(aoi_config.as_id)
    merged = gdf[[id_field, "geometry"]].dissolve(by=id_field)
    return [{"id": body_id, "wkt": geom.wkt, "geom": geom} for body_id, geom in merged.geometry.items()]


def publish_polygons(kind: str, bodies: list[dict]) -> int:
    """Write each body to storage as GeoJSON. Concurrent, because a national
    dataset is tens of thousands of small objects."""
    s3 = storage.get_s3_client()

    def put(body: dict) -> None:
        bucket, key = storage.parse_s3_path(storage.boundary_polygon_path(kind, body["id"]))
        geojson = gpd.GeoSeries([body["geom"]], crs=5070).to_json()
        s3.put_object(Bucket=bucket, Key=key, Body=geojson.encode())

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(put, bodies))
    return len(bodies)


def seed_water_bodies(kind: str, aoi: dict) -> None:
    key, layer, id_field, table = WATER_BODIES[kind]
    source = aoi_config.require(aoi, key)
    with tempfile.TemporaryDirectory() as tmp:
        bodies = load_water_bodies(aoi_config.local_copy(source, Path(tmp)), layer, id_field)

    with db.connect() as conn, conn.cursor() as cur:
        cur.executemany(
            f"""INSERT INTO {table} ({id_field}, geom)
                VALUES (%(id)s, ST_Multi(ST_GeomFromText(%(wkt)s, 5070)))
                ON CONFLICT ({id_field}) DO UPDATE SET geom = EXCLUDED.geom""",
            bodies,
        )
    published = publish_polygons(kind, bodies)
    total = db.one(f"SELECT count(*) AS n FROM {table}")["n"]

    print(f"\nloaded          {len(bodies)} {kind}(s) from {source}")
    print(f"{table:<16}{total} in the database")
    print(f"published       {published} to {storage.workspace_path(kind + 's')}/")


# --- network -------------------------------------------------------------


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
        print(f"reprojecting    {gdf.crs.to_string()} -> EPSG:5070")
        gdf = gdf.to_crs(epsg=5070)

    known = set(gdf.columns)
    in_file = {aoi_config.as_id(i) for i in gdf["reach_id"]}
    rows, clipped = [], []
    for _, r in gdf.iterrows():

        def value(col, cast, fallback=None):
            if col not in known or pd.isna(r[col]):
                return fallback
            return cast(r[col])

        geom = r.geometry
        if geom.geom_type == "MultiLineString" and len(geom.geoms) == 1:
            # the column is LineString; a 1-part multi is the same line
            geom = geom.geoms[0]

        reach_id = aoi_config.as_id(r["reach_id"])
        reach_to_id = value("reach_to_id", aoi_config.as_id)
        is_terminal = bool(value("is_terminal", bool, False))
        terminal_reason = value("terminal_reason", str)

        # The file can be a clip of a larger network, so a reach at its edge can
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
                "lake_to_id": value("lake_to_id", aoi_config.as_id),
                "coast_to_id": value("coast_to_id", aoi_config.as_id),
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
        print(f"{len(clipped)} reach(es) point outside this network; treated as outlet terminals:")
        for reach_id, missing in clipped:
            print(f"    {reach_id} -> {missing} (not in file)")
    return rows


def check_water_bodies_seeded(reaches: list[dict]) -> None:
    """Stop before writing anything if the network names a lake or coast that is not seeded."""
    for kind, (key, _, id_field, table) in WATER_BODIES.items():
        named = sorted({r[f"{kind}_to_id"] for r in reaches if r[f"{kind}_to_id"] is not None})
        if not named:
            continue
        present = {
            r[id_field]
            for r in db.query(f"SELECT {id_field} FROM {table} WHERE {id_field} = ANY(%s)", (named,))
        }
        missing = [i for i in named if i not in present]
        if missing:
            sys.exit(
                f"The network names {len(missing)} {kind}(s) not in the {table} table: "
                f"{missing[:20]}\nSeed them first: seed.py {key} <aoi-config-path>"
            )


# An upsert rather than a plain insert, so seeding a network that is already
# there updates it instead of failing, and nothing is deleted to make room.
# reach_network has no triggers; an update here changes the row and nothing else.
_REACH = f"""
    INSERT INTO reach_network ({", ".join(NETWORK_COLUMNS)}, geom)
    VALUES ({", ".join(f"%({c})s" for c in NETWORK_COLUMNS)}, ST_GeomFromText(%(geom)s, 5070))
    ON CONFLICT (reach_id) DO UPDATE SET
        {", ".join(f"{c} = EXCLUDED.{c}" for c in NETWORK_COLUMNS if c != REACH_ID_FIELD)},
        geom = EXCLUDED.geom
"""


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


def export_reach_network() -> str:
    """Write the database's reach network as GeoParquet, sorted by reach_id.

    Exported from the table rather than from the file just loaded, so it is the
    network the loop reasons over, every AOI seeded into this database.

    Sorted by reach_id, with the sort recorded in the file metadata, so a reader
    can use each row group's min/max to skip straight to the group holding a
    reach. Unsorted, those statistics overlap and every group has to be read.
    """
    rows = db.query(
        f"SELECT {', '.join(NETWORK_COLUMNS)}, ST_AsBinary(geom) AS geom_wkb "
        # Byte order, not the database's collation: the parquet row-group
        # min/max a reader skips by are compared bytewise.
        f'FROM reach_network ORDER BY {REACH_ID_FIELD} COLLATE "C"'
    )
    gdf = gpd.GeoDataFrame(
        [{c: r[c] for c in NETWORK_COLUMNS} for r in rows],
        geometry=shapely.from_wkb([bytes(r["geom_wkb"]) for r in rows]),
        crs=5070,
    )

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
        storage.get_s3_client().put_object(Bucket=bucket, Key=key, Body=local.read_bytes())
    return uri


def seed_network(aoi: dict) -> None:
    source = aoi_config.require(aoi, "network")
    with tempfile.TemporaryDirectory() as tmp:
        reaches = load_network(aoi_config.local_copy(source, Path(tmp)))

    check_water_bodies_seeded(reaches)
    with db.connect() as conn, conn.cursor() as cur:
        cur.executemany(_REACH, reaches)
    network_uri = export_reach_network()

    summary = db.one("""
        SELECT count(*) AS reaches,
               count(*) FILTER (WHERE is_terminal) AS terminals,
               count(*) FILTER (WHERE terminal_reason = 'lake')  AS lake_terminals,
               count(*) FILTER (WHERE terminal_reason = 'coast') AS coast_terminals,
               count(*) FILTER (WHERE terminal_reason = 'outlet') AS outlet_terminals
        FROM reach_network""")
    print(f"\nloaded          {len(reaches)} reach(es) from {source}")
    print(f"reach_network   {summary['reaches']} reach(es) in the database")
    print(
        f"terminals       {summary['terminals']} "
        f"(lake {summary['lake_terminals']}, coast {summary['coast_terminals']}, "
        f"outlet {summary['outlet_terminals']})"
    )
    print(f"published       {network_uri}")
    if summary["outlet_terminals"]:
        # Not a warning. An outlet names no lake or coast, and needs none: the
        # outflow polygon input is optional and the run job derives an area from
        # the model's own domain and centreline when it is absent.
        print(
            f"\nNote: {summary['outlet_terminals']} outlet terminal(s) name no water body.\n"
            "      Their outflow area is derived by the run job from the model itself."
        )
    print("\nNo intent is authored yet. Run scripts/author_intent.py next.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("what", choices=("lakes", "coasts", "network"), help="what to seed")
    aoi_config.add_argument(ap)
    args = ap.parse_args()

    aoi = aoi_config.load(args.aoi_config_path)
    print(f"aoi config      {aoi_config.describe(aoi)}")
    if args.what == "network":
        seed_network(aoi)
    else:
        seed_water_bodies(args.what.removesuffix("s"), aoi)


if __name__ == "__main__":
    main()
