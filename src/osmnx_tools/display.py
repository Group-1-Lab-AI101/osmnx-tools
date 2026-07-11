"""Display extracted OSM boundary roads & intersections (and optional POIs).

Usage:
    uv run osm-display [OUT_DIR] [--map PATH] [--no-plot] [--show] [--no-pois]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

def _stringify(value):
    """Turn list/array-typed OSM `name`/`highway` values into readable strings."""
    if isinstance(value, (list, tuple, np.ndarray)):
        return ", ".join(str(v) for v in np.atleast_1d(value))
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return str(value)

def load(out_dir: str):
    """Load the GeoJSON/JSON outputs produced by ``osm-extract``.

    Returns ``(roads, nodes, bounds, pois, summary)`` where ``pois`` is an
    empty GeoDataFrame when ``pois.geojson`` does not exist.
    """
    roads = gpd.read_file(os.path.join(out_dir, "roads.geojson"))
    nodes = gpd.read_file(os.path.join(out_dir, "intersections.geojson"))
    bounds = gpd.read_file(os.path.join(out_dir, "boundary.geojson"))
    pois_path = os.path.join(out_dir, "pois.geojson")
    if os.path.exists(pois_path):
        pois = gpd.read_file(pois_path)
    else:
        pois = gpd.GeoDataFrame(geometry=[], crs=roads.crs)
    with open(os.path.join(out_dir, "summary.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    return roads, nodes, bounds, pois, summary

def print_summary(roads: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame,
                  bounds: gpd.GeoDataFrame, pois: gpd.GeoDataFrame,
                  summary: dict) -> None:
    line = "=" * 64
    print(line)
    print("OSM BOUNDARY EXTRACTION SUMMARY")
    print(line)
    print(f"Relations        : {', '.join('R' + r for r in summary.get('relations', [])) or 'n/a'}")
    print(f"Network type     : {summary.get('network_type')}")
    print(f"OSMnx version    : {summary.get('osmnx_version')}")
    print(f"Generated at     : {summary.get('generated_at')}")
    print(f"Total roads      : {summary.get('total_roads')}")
    print(f"Intersections    : {summary.get('total_intersections')}")
    print(f"Road length (km) : {summary.get('total_road_length_km')}")
    print(f"Total POIs       : {summary.get('total_pois', len(pois))}")

    if summary.get("errors"):
        print("\nErrors:")
        for err in summary["errors"]:
            print(f"  - R{err['relation_id']}: {err['error']}")

    if summary.get("poi_errors"):
        print("\nPOI errors:")
        for err in summary["poi_errors"]:
            print(f"  - {err['osm_id']}: {err['error']}")

    if "highway" in roads.columns:
        print("\nRoads by highway class:")
        counts = roads["highway"].apply(_stringify).value_counts()
        for key, val in counts.items():
            print(f"  {key}: {val}")

    if "length" in roads.columns:
        print("\nTop 10 longest roads:")
        top = roads.sort_values("length", ascending=False).head(10)
        for _, row in top.iterrows():
            name = _stringify(row.get("name")) or "(unnamed)"
            hw = _stringify(row.get("highway")) or "?"
            print(f"  {float(row['length']):9.1f} m  {hw:18s}  {name}")

    if "street_count" in nodes.columns:
        print("\nIntersection degree distribution (street_count):")
        deg = nodes["street_count"].value_counts().sort_index()
        for key, val in deg.items():
            print(f"  {key}-way: {val}")

    if len(pois):
        print("\nPoints of interest:")
        for _, row in pois.iterrows():
            osm_type = _stringify(row.get("osm_type")) or "?"
            osm_id = _stringify(row.get("osm_id")) or "?"
            name = _stringify(row.get("name")) or "(unnamed)"
            print(f"  {osm_type}{osm_id}: {name}")

def make_map(roads: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame,
             bounds: gpd.GeoDataFrame, pois: gpd.GeoDataFrame,
             summary: dict, map_path: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 10))

    if len(bounds):
        bounds.plot(ax=ax, color="lightgray", alpha=0.3)
        bounds.boundary.plot(ax=ax, color="black", linewidth=1.2, alpha=0.6)

    if "highway" in roads.columns and len(roads):
        roads = roads.copy()
        roads["_hw"] = roads["highway"].apply(_stringify)
        roads.plot(
            ax=ax,
            column="_hw",
            categorical=True,
            linewidth=0.7,
            legend=True,
            legend_kwds={"title": "highway", "fontsize": 7, "title_fontsize": 8},
        )
    elif len(roads):
        roads.plot(ax=ax, color="gray", linewidth=0.7)

    if len(nodes):
        nodes.plot(ax=ax, color="red", markersize=8, marker="o")

    if len(pois):
        # Project polygon POI geometries to point centroids for uniform plotting.
        pois_pts = pois.copy()
        is_poly = pois_pts.geometry.geom_type.str.contains("Polygon")
        if is_poly.any():
            pois_pts.loc[is_poly, "geometry"] = pois_pts.loc[is_poly, "geometry"].centroid
        pois_pts.plot(ax=ax, color="blue", markersize=40, marker="*", zorder=5)

    ax.set_axis_off()
    rels = summary.get("relations")
    title = "Boundary roads & intersections"
    if rels:
        title += f" (R{', R'.join(rels)})"
    if len(pois):
        title += f" [{len(pois)} POIs]"
    ax.set_title(title, fontsize=12)

    fig.savefig(map_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote map: {map_path}", file=sys.stderr)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Display extracted OSM boundary roads & intersections.",
    )
    parser.add_argument(
        "out",
        nargs="?",
        default="./data",
        help="Output directory from osm-extract (default: ./data).",
    )
    parser.add_argument("--map", default=None, help="Path to save the PNG map.")
    parser.add_argument("--no-plot", action="store_true", help="Skip drawing the map.")
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show the plot interactively (requires a display; ignored on headless hosts).",
    )
    parser.add_argument(
        "--no-pois",
        action="store_true",
        help="Do not load, print, or plot points of interest.",
    )
    args = parser.parse_args(argv)

    roads, nodes, bounds, pois, summary = load(args.out)
    if args.no_pois:
        pois = gpd.GeoDataFrame(geometry=[], crs=roads.crs)
    print_summary(roads, nodes, bounds, pois, summary)

    if not args.no_plot:
        map_path = args.map or os.path.join(args.out, "map.png")
        make_map(roads, nodes, bounds, pois, summary, map_path)
        if args.show:
            try:
                plt.show()
            except Exception:  # noqa: BLE001 - headless hosts cannot show
                pass
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
