"""Extract points of interest (POIs) by OSM ID and add them to a data folder.

Usage:
    uv run osm-pois ID [ID ...] [--out DIR] [--replace]
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


def normalize_poi_id(raw: str) -> str:
    """Normalize a POI id.

    Accepts forms like ``12345``, ``N12345``, ``W12345`` or ``R12345``. If no
    type prefix is given, defaults to node (``N``) since most POIs are nodes.
    Returns the Nominatim-style id with its type prefix (e.g. ``N12345``).
    """
    raw = str(raw).strip()
    if raw and raw[0].upper() in ("N", "W", "R") and len(raw) > 1 and raw[1:].isdigit():
        return f"{raw[0].upper()}{raw[1:]}"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        msg = f"Invalid POI id: {raw!r}"
        raise ValueError(msg)
    return f"N{digits}"


def _first(gdf: gpd.GeoDataFrame, col: str) -> str | None:
    """Return the first non-null value of a column, or None."""
    if col in gdf.columns:
        val = gdf[col].iloc[0]
        if val is not None and str(val) != "nan":
            return str(val)
    return None


def _classify_poi(gdf: gpd.GeoDataFrame) -> str:
    """Classify a POI by its main OSM tag (amenity, shop, tourism, ...)."""
    for tag in ("amenity", "shop", "tourism", "leisure", "office",
                "healthcare", "sport", "historic", "natural"):
        val = _first(gdf, tag)
        if val:
            return f"{tag}={val}"
    return "unknown"


def extract_poi(poi_id: str) -> gpd.GeoDataFrame:
    """Resolve a single OSM POI id to its GeoDataFrame via Nominatim lookup.

    ``poi_id`` must be a Nominatim-style id such as ``N12345``.
    """
    gdf = ox.geocode_to_gdf(poi_id, by_osmid=True)
    if gdf is None or len(gdf) == 0:
        msg = f"No POI found for id {poi_id}"
        raise ValueError(msg)
    return gdf


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract OSM points of interest by ID and add them to a data folder.",
    )
    parser.add_argument(
        "poi_ids",
        nargs="+",
        help="One or more OSM POI IDs (node/way/relation, with or without N/W/R prefix).",
    )
    parser.add_argument(
        "--out",
        default="./data",
        help="Output directory to add POIs to (default: ./data).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace the existing pois.geojson instead of merging with it.",
    )
    args = parser.parse_args(argv)

    if not os.path.isdir(args.out):
        print(f"Output directory does not exist: {args.out}", file=sys.stderr)
        return 1

    pois_path = os.path.join(args.out, "pois.geojson")
    summary_path = os.path.join(args.out, "summary.json")

    new_pois: list[gpd.GeoDataFrame] = []
    errors: list[dict] = []
    extracted: list[dict] = []

    for raw in args.poi_ids:
        poi_id = normalize_poi_id(raw)
        try:
            gdf = extract_poi(poi_id)
            osm_type = poi_id[0]
            osm_id = poi_id[1:]
            gdf = gdf.copy()
            gdf["osm_id"] = osm_id
            gdf["osm_type"] = osm_type
            new_pois.append(gdf)
            name = _first(gdf, "name") or _first(gdf, "name:en") or "(unnamed)"
            extracted.append({
                "osm_id": osm_id,
                "osm_type": osm_type,
                "name": name,
                "poi_type": _classify_poi(gdf),
            })
            print(f"[ok] {poi_id}: {name}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - report and continue
            errors.append({"osm_id": poi_id, "error": str(exc)})
            print(f"[error] {poi_id}: {exc}", file=sys.stderr)

    if not new_pois:
        print("No POIs extracted.", file=sys.stderr)
        return 1

    # Merge with existing POIs unless --replace was given.
    if not args.replace and os.path.exists(pois_path):
        try:
            existing = gpd.read_file(pois_path)
            if len(existing):
                new_pois.insert(0, existing)
                print(
                    f"Loaded {len(existing)} existing POIs from {pois_path}",
                    file=sys.stderr,
                )
        except Exception as exc:  # noqa: BLE001 - corrupt file should not stop us
            print(f"[warn] could not read existing pois.geojson: {exc}", file=sys.stderr)

    last = new_pois[-1]
    pois = gpd.GeoDataFrame(
        pd.concat(new_pois, ignore_index=True), crs=last.crs
    )

    # Deduplicate by osm_id + osm_type (keep last occurrence).
    if "osm_id" in pois.columns and "osm_type" in pois.columns:
        pois["osm_id"] = pois["osm_id"].astype(str)
        pois["osm_type"] = pois["osm_type"].astype(str)
        before = len(pois)
        pois = pois.drop_duplicates(subset=["osm_id", "osm_type"], keep="last")
        if len(pois) < before:
            print(f"Deduplicated {before - len(pois)} POIs", file=sys.stderr)

    pois.to_file(pois_path, driver="GeoJSON")
    print(
        f"Wrote {pois_path} ({len(pois)} POIs at "
        f"{datetime.now(timezone.utc).isoformat()})",
        file=sys.stderr,
    )

    # Update summary.json with POI information.
    summary: dict = {}
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as fh:
            summary = json.load(fh)

    summary["total_pois"] = int(len(pois))
    summary["pois"] = extracted
    summary["poi_errors"] = errors

    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"Updated {summary_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
