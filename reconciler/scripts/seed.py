"""Seed lakes, coasts or the reach network from an AOI config.

Reads the GeoPackage the AOI config names, writes the rows into the database
and publishes to the workspace what jobs read. Lakes and coasts must be seeded
before the network that references them.

Usage:
    just seed-lakes   <aoi-config-path>
    just seed-coasts  <aoi-config-path>
    just seed-network <aoi-config-path>

    python scripts/seed.py {lakes,coasts,network} <aoi-config-path>

<aoi-config-path> is a local path or an s3:// address. See
example.aoi_config.jsonc for the format.
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

REACH_ID_FIELD = "reach_id"
NETWORK_LAYER = aoi_config.NETWORK_LAYER

WATER_BODIES = {
    "lake": ("lakes", "lakes_polygons", "lake_id", "lakes"),
    "coast": ("coasts", "coastal_influence_polygons", "coast_id", "coasts"),
}

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


def load_water_bodies(gpkg_path: Path, layer: str, id_field: str) -> list[dict]:
    """Every polygon in the layer, one per id."""
    gdf = gpd.read_file(gpkg_path, layer=layer)
    if id_field not in gdf.columns:
        sys.exit(f"{gpkg_path} layer {layer} has no {id_field} column")
    if gdf.crs and gdf.crs.to_epsg() != 5070:
        gdf = gdf.to_crs(epsg=5070)
    gdf[id_field] = gdf[id_field].map(aoi_config.as_id)
    merged = gdf[[id_field, "geometry"]].dissolve(by=id_field)
    return [{"id": body_id, "wkt": geom.wkt, "geom": geom} for body_id, geom in merged.geometry.items()]


def publish_polygons(kind: str, bodies: list[dict]) -> int:
    """Write each body to storage as GeoJSON."""
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


def load_network(gpkg_path: Path, layer: str = NETWORK_LAYER) -> list[dict]:
    """Read the modified network into insertable rows."""
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
            geom = geom.geoms[0]

        reach_id = aoi_config.as_id(r["reach_id"])
        reach_to_id = value("reach_to_id", aoi_config.as_id)
        is_terminal = bool(value("is_terminal", bool, False))
        terminal_reason = value("terminal_reason", str)

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


_REACH = f"""
    INSERT INTO reach_network ({", ".join(NETWORK_COLUMNS)}, geom)
    VALUES ({", ".join(f"%({c})s" for c in NETWORK_COLUMNS)}, ST_GeomFromText(%(geom)s, 5070))
    ON CONFLICT (reach_id) DO UPDATE SET
        {", ".join(f"{c} = EXCLUDED.{c}" for c in NETWORK_COLUMNS if c != REACH_ID_FIELD)},
        geom = EXCLUDED.geom
"""


REACH_ROW_GROUP_SIZE = 8192


def export_reach_network() -> str:
    """Write the database's reach network as GeoParquet, sorted by reach_id."""
    rows = db.query(
        f"SELECT {', '.join(NETWORK_COLUMNS)}, ST_AsBinary(geom) AS geom_wkb "
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
