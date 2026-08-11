"""Load a reach network from a GeoPackage and seed the DB.

Shared by smoke_check.py and the notebook.
Requires geopandas (dev dependency).
"""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

from orchestrator.state_store import StateStore


def load_network(gpkg_path: Path) -> list[dict]:
    """Read reaches from a GeoPackage and derive topology flags."""
    gdf = gpd.read_file(gpkg_path, layer="reach_network")

    required = {"reach_id", "reach_to_id", "total_da_sqkm", "stream_order", "slope"}
    missing = required - set(gdf.columns)
    if missing:
        sys.exit(f"GeoPackage missing required columns: {sorted(missing)}")

    if gdf.crs and gdf.crs.to_epsg() != 5070:
        print(f"  Reprojecting from {gdf.crs} to EPSG:5070")
        gdf = gdf.to_crs(epsg=5070)

    reach_ids = set(gdf["reach_id"].dropna().astype(int))

    network = []
    for _, row in gdf.iterrows():
        reach_id = int(row["reach_id"])
        reach_to_id_raw = row["reach_to_id"]
        reach_to_id = int(reach_to_id_raw) if reach_to_id_raw is not None and not pd.isna(reach_to_id_raw) else None

        is_terminal = reach_to_id is None or reach_to_id not in reach_ids
        is_headwater = reach_id not in {int(r) for r in gdf["reach_to_id"].dropna()}

        geom = row.geometry
        if geom.geom_type == "MultiLineString" and len(geom.geoms) == 1:
            geom = geom.geoms[0]

        if is_terminal:
            reach_to_id = None

        network.append({
            "reach_id": reach_id,
            "reach_to_id": reach_to_id,
            "is_terminal": is_terminal,
            "is_headwater": is_headwater,
            "total_da_sqkm": float(row["total_da_sqkm"]),
            "stream_order": int(row["stream_order"]) if row["stream_order"] is not None else None,
            "slope": float(row["slope"]) if row["slope"] is not None else None,
            "geom": geom.wkt,
        })

    network.sort(key=lambda r: (not r["is_terminal"], r["reach_id"]))
    return network


def seed(store: StateStore, network: list[dict]) -> None:
    """Insert reaches and desired_state from a loaded network."""
    for r in network:
        store.insert_reach(
            reach_id=r["reach_id"],
            reach_to_id=r["reach_to_id"],
            is_terminal=r["is_terminal"],
            is_headwater=r["is_headwater"],
            geom=r["geom"],
            total_da_sqkm=r["total_da_sqkm"],
            stream_order=r["stream_order"],
            slope=r["slope"],
        )
        store.upsert_desired(reach_id=r["reach_id"])
