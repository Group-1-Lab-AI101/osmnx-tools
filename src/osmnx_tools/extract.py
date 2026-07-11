"""Extract roads and intersections within OSM boundary relations using OSMnx.

Usage:
    uv run osm-extract ID [ID ...] [--out DIR] [--network-type TYPE]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import geopandas as gpd
import osmnx as ox
import pandas as pd

# Be a polite client and keep a local cache of downloaded OSM data.
ox.settings.use_cache = True
ox.settings.log_console = False


def normalize_rel_id(raw: str) -> str:
    """Normalize a relation id.

    Accepts forms like ``12345``, ``R12345`` or ``relation/12345`` and always
    returns the Nominatim-style id ``R12345`` (with the relation prefix).
    """
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        msg = f"Invalid relation id: {raw!r}"
        raise ValueError(msg)
    return f"R{digits}"


def resolve_boundary(rel_id: str) -> gpd.GeoDataFrame:
    """Resolve an OSM boundary relation id to its boundary GeoDataFrame.

    ``rel_id`` must be a Nominatim-style id such as ``R12345``.
    """
    gdf = ox.geocode_to_gdf(rel_id, by_osmid=True)
    if gdf is None or len(gdf) == 0:
        msg = f"No geometry found for relation {rel_id}"
        raise ValueError(msg)
    return gdf


def extract_relation(rel_id: str, network_type: str):
    """Extract roads and intersections for one boundary relation.

    Returns ``(roads_gdf, intersections_gdf, boundary_gdf, meta)`` where each
    GeoDataFrame carries a ``relation_id`` column (without the ``R`` prefix).
    """
    rid = rel_id.lstrip("R")
    boundary = resolve_boundary(rel_id)
    polygon = boundary.geometry.union_all()

    G = ox.graph_from_polygon(
        polygon,
        network_type=network_type,
        simplify=True,
        retain_all=True,
    )
    nodes, edges = ox.graph_to_gdfs(G)

    edges = edges.copy()
    nodes = nodes.copy()
    edges["relation_id"] = rid
    nodes["relation_id"] = rid

    boundary = boundary.copy()
    boundary["relation_id"] = rid

    total_length = float(edges["length"].sum()) if "length" in edges else 0.0
    meta = {
        "relation_id": rid,
        "roads": int(len(edges)),
        "intersections": int(len(nodes)),
        "total_road_length_m": round(total_length, 2),
    }
    return edges, nodes, boundary, meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract roads & intersections inside OSM boundary relations.",
    )
    parser.add_argument(
        "relation_ids",
        nargs="+",
        help="One or more OSM boundary relation IDs (with or without the 'R' prefix).",
    )
    parser.add_argument(
        "--out",
        default="./data",
        help="Output directory for the extracted GeoJSON/JSON files (default: ./data).",
    )
    parser.add_argument(
        "--network-type",
        default="drive",
        choices=["drive", "drive_service", "all", "bike", "walk", "rail"],
        help="OSMnx network type to extract (default: drive).",
    )
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)

    all_roads: list[gpd.GeoDataFrame] = []
    all_nodes: list[gpd.GeoDataFrame] = []
    all_bounds: list[gpd.GeoDataFrame] = []
    per_relation: list[dict] = []
    errors: list[dict] = []

    for raw in args.relation_ids:
        rel_id = normalize_rel_id(raw)
        try:
            edges, nodes, boundary, meta = extract_relation(rel_id, args.network_type)
            all_roads.append(edges)
            all_nodes.append(nodes)
            all_bounds.append(boundary)
            per_relation.append(meta)
            print(
                f"[ok] {rel_id}: {meta['roads']} roads, "
                f"{meta['intersections']} intersections, "
                f"{meta['total_road_length_m'] / 1000:.2f} km",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 - report and continue with other IDs
            errors.append({"relation_id": rel_id.lstrip("R"), "error": str(exc)})
            print(f"[error] {rel_id}: {exc}", file=sys.stderr)

    if not all_roads:
        print("No data extracted for any relation.", file=sys.stderr)
        return 1

    # Reference frames from the last successful extraction (guaranteed present).
    last_edges = all_roads[-1]
    last_nodes = all_nodes[-1]
    last_bounds = all_bounds[-1]

    roads = gpd.GeoDataFrame(pd.concat(all_roads, ignore_index=True), crs=last_edges.crs)
    nodes = gpd.GeoDataFrame(pd.concat(all_nodes, ignore_index=True), crs=last_nodes.crs)
    bounds = gpd.GeoDataFrame(pd.concat(all_bounds, ignore_index=True), crs=last_bounds.crs)

    roads_path = os.path.join(args.out, "roads.geojson")
    nodes_path = os.path.join(args.out, "intersections.geojson")
    bounds_path = os.path.join(args.out, "boundary.geojson")

    roads.to_file(roads_path, driver="GeoJSON")
    nodes.to_file(nodes_path, driver="GeoJSON")
    bounds.to_file(bounds_path, driver="GeoJSON")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "osmnx_version": ox.__version__,
        "network_type": args.network_type,
        "relations": [m["relation_id"] for m in per_relation],
        "total_roads": int(len(roads)),
        "total_intersections": int(len(nodes)),
        "total_road_length_km": round(roads["length"].sum() / 1000, 3)
        if "length" in roads
        else 0.0,
        "per_relation": per_relation,
        "errors": errors,
    }
    summary_path = os.path.join(args.out, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(
        f"Wrote {roads_path}\nWrote {nodes_path}\n"
        f"Wrote {bounds_path}\nWrote {summary_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
