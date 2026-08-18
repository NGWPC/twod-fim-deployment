"""Parse a reach network out of a GeoPackage.

Parsing only — it touches no database. Writing the rows is the caller's job,
which keeps geopandas (a dev dependency, absent from the job image) out of
anything the loop imports.

Dev scaffolding: the real producer of the modelling network is the
modify_network job in twod-fim-jobs, and this stands in until its output feeds
a load step directly.
"""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd


def load_network(gpkg_path: Path) -> list[dict]:
    """Read reaches from a GeoPackage and derive topology flags."""
    gdf = gpd.read_file(gpkg_path, layer="reach_network")

    DEFAULT_SLOPE = 0.001

    required = {"reach_id", "reach_to_id", "total_da_sqkm", "stream_order"}
    missing = required - set(gdf.columns)
    if missing:
        sys.exit(f"GeoPackage missing required columns: {sorted(missing)}")

    has_slope = "slope" in gdf.columns
    if not has_slope:
        print(f"  slope column missing, using default {DEFAULT_SLOPE}")

    if gdf.crs and gdf.crs.to_epsg() != 5070:
        print(f"  Reprojecting from {gdf.crs} to EPSG:5070")
        gdf = gdf.to_crs(epsg=5070)

    gdf["reach_id"] = gdf["reach_id"].astype(int)
    gdf["reach_to_id"] = pd.to_numeric(gdf["reach_to_id"], errors="coerce").astype("Int64")

    reach_ids = set(gdf["reach_id"])

    # Check which optional columns exist
    has_is_terminal = "is_terminal" in gdf.columns
    has_is_headwater = "is_headwater" in gdf.columns
    has_terminal_reason = "terminal_reason" in gdf.columns
    has_lake_inlet = "lake_inlet" in gdf.columns
    has_lake_outlet = "lake_outlet" in gdf.columns
    has_is_trimmed = "is_trimmed" in gdf.columns
    downstream_ids = set(gdf["reach_to_id"].dropna().astype(int))

    network = []
    for _, row in gdf.iterrows():
        reach_id = int(row["reach_id"])
        reach_to_id = int(row["reach_to_id"]) if not pd.isna(row["reach_to_id"]) else None

        # Use GeoPackage flags when available, derive otherwise
        is_terminal = bool(row["is_terminal"]) if has_is_terminal else (reach_to_id is None or reach_to_id not in reach_ids)
        is_headwater = bool(row["is_headwater"]) if has_is_headwater else (reach_id not in downstream_ids)
        terminal_reason = str(row["terminal_reason"]) if has_terminal_reason and not pd.isna(row.get("terminal_reason")) else None
        lake_inlet = bool(row["lake_inlet"]) if has_lake_inlet else False
        lake_outlet = bool(row["lake_outlet"]) if has_lake_outlet else False
        is_trimmed = bool(row["is_trimmed"]) if has_is_trimmed else False

        # Override: if downstream is outside this subset, treat as terminal
        if reach_to_id is not None and reach_to_id not in reach_ids:
            is_terminal = True
            reach_to_id = None

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
            "terminal_reason": terminal_reason,
            "lake_inlet": lake_inlet,
            "lake_outlet": lake_outlet,
            "is_trimmed": is_trimmed,
            "total_da_sqkm": float(row["total_da_sqkm"]),
            "stream_order": int(row["stream_order"]) if row["stream_order"] is not None else None,
            "slope": float(row["slope"]) if has_slope and not pd.isna(row["slope"]) else DEFAULT_SLOPE,
            "geom": geom.wkt,
        })

    # Topological sort: each reach inserted after its downstream (reach_to_id)
    by_id = {r["reach_id"]: r for r in network}
    ordered = []
    visited = set()

    def visit(reach_id: int) -> None:
        if reach_id in visited:
            return
        visited.add(reach_id)
        r = by_id[reach_id]
        if r["reach_to_id"] is not None and r["reach_to_id"] in by_id:
            visit(r["reach_to_id"])
        ordered.append(r)

    for r in network:
        visit(r["reach_id"])

    return ordered
