#!/usr/bin/env python3
"""Bounded, resumable terrain expansion; reuses provider crop caches.

Nine non-overlapping windows per anchor, centres spaced 3 km in its local UTM
frame. Anchor names identify the sampling envelope, not classified landforms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analog_pipelines.paths import data_dir  # noqa: E402
from copernicus_glo90 import (  # noqa: E402
    CANDIDATES,
    Candidate,
    CandidateMeasurement,
    build_manifest,
    default_cache_dir,
    default_schema_path,
    extract_candidate,
    fetch_crop,
    validate_manifest,
    write_manifest,
)
from pyproj import Transformer  # noqa: E402

# Explicit sampling centres, WGS84. These are selection coordinates, never observations.
ANCHORS = (
    ("namib", -24.70, 15.30),
    ("sahara", 24.50, 10.50),
    ("wupatki", 35.56, -111.40),
    ("iceland", 65.70, -16.80),
    ("snake-river", 43.40, -113.50),
    ("pampas", -34.50, -61.50),
    ("altiplano", -22.50, -67.50),
    ("alps", 46.50, 10.50),
    ("haughton", 75.38, -89.67),
    ("pingualuit", 61.276, -73.660),
    ("devon-polar", 75.60, -89.50),
    ("grand-canyon", 36.20, -112.20),
)


def candidates():
    windows = list(CANDIDATES)
    for name, lat, lon in ANCHORS:
        epsg = (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1
        forward = Transformer.from_crs(4326, epsg, always_xy=True)
        inverse = Transformer.from_crs(epsg, 4326, always_xy=True)
        x, y = forward.transform(lon, lat)
        for row in range(3):
            for col in range(3):
                wlon, wlat = inverse.transform(x + (col - 1) * 3000, y + (row - 1) * 3000)
                windows.append(
                    Candidate(
                        f"{name}-r{row + 1}c{col + 1}",
                        f"{name} sampling window R{row + 1} C{col + 1}",
                        wlat,
                        wlon,
                    )
                )
    return windows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=3, help="Maximum windows this run (1..113).")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("pipelines/earth/manifests/copernicus_glo90_earth.json"),
    )
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)
    args.report = args.report or data_dir("processed") / "terrain-earth-status.json"
    all_windows = candidates()
    if not 1 <= args.limit <= len(all_windows) or not 0 <= args.offset < len(all_windows):
        parser.error("limit/offset outside finite sampling plan")
    selected = all_windows[args.offset : args.offset + args.limit]
    if args.dry_run:
        print(
            json.dumps(
                [dict(id=c.location_id, lat=c.lat, lon=c.lon, url=c.tile_url) for c in selected],
                indent=2,
            )
        )
        return 0
    document = json.loads(args.manifest.read_text()) if args.manifest.exists() else None
    rows = {c["location_id"]: c for c in document["candidates"]} if document else {}
    statuses = []
    for c in selected:
        try:
            crop = fetch_crop(c, default_cache_dir(), timeout=25, max_retries=1)
            result = extract_candidate(c, crop)
            fresh = build_manifest([CandidateMeasurement(c, crop, result)])
            rows[c.location_id] = fresh["candidates"][0]
            fresh["candidates"] = sorted(rows.values(), key=lambda row: row["location_id"])
            # Composite of measured records (includes each window checksum in notes).
            fresh["datasets"][0]["checksum"] = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(fresh["candidates"], sort_keys=True).encode()
                ).hexdigest()
            )
            fresh["datasets"][0]["notes"] += (
                " Batch checksum covers canonical candidate records, not full source objects."
            )
            validate_manifest(fresh, default_schema_path())
            write_manifest(fresh, args.manifest)
            statuses.append(dict(id=c.location_id, provider="copernicus", status="verified"))
            print(f"OK {c.location_id} valid={result.valid_fraction:.4f}", flush=True)
        except Exception as exc:
            statuses.append(
                dict(id=c.location_id, provider="copernicus", status="failed", error=str(exc))
            )
            print(f"FAILED {c.location_id}: {exc}", flush=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(statuses, indent=2) + "\n")
    return int(any(s["status"] == "failed" for s in statuses))


if __name__ == "__main__":
    raise SystemExit(main())
