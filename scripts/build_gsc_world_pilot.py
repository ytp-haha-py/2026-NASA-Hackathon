#!/usr/bin/env python
"""Build the bounded GSC Open File 5529 Earth-lithology evidence pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analog_pipelines.geology.gsc_world import (
    SOURCE,
    GscWorldError,
    verify_artifact,
    write_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
AREAS = ROOT / "pipelines/geology/areas.json"
DEFAULT_OUTPUT = ROOT / "pipelines/geology/gsc-world"
DEFAULT_AREAS = ("earth-atacama-desert", "earth-lanzarote")


def _bbox(geometry: dict) -> dict[str, float]:
    coordinates = geometry["coordinates"]
    points = [point for ring in coordinates for point in ring]
    return {
        "west": min(point[0] for point in points),
        "south": min(point[1] for point in points),
        "east": max(point[0] for point in points),
        "north": max(point[1] for point in points),
    }


def _selected_areas(area_ids: tuple[str, ...]) -> list[dict]:
    payload = json.loads(AREAS.read_text(encoding="utf-8"))["areas"]
    by_id = {area["area_id"]: area for area in payload}
    selected = []
    for area_id in area_ids:
        area = by_id.get(area_id)
        if area is None:
            raise SystemExit(f"unknown AOI: {area_id}")
        if area["body"] != "Earth":
            raise SystemExit(f"not an Earth AOI: {area_id}")
        selected.append(
            {
                "area_id": area_id,
                "body": "Earth",
                "bbox": _bbox(area["geometry"]),
                "crs": area["crs"],
            }
        )
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--area", action="append", dest="areas", default=None)
    parser.add_argument("--side", type=int, default=5, help="point-grid side (default 5)")
    parser.add_argument("--generated-at", default="2026-11-15T00:00:00Z")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; no network")
    parser.add_argument("--check", action="store_true", help="verify committed artifact only")
    args = parser.parse_args()

    if args.check:
        try:
            summary = verify_artifact(args.output)
        except (OSError, KeyError, ValueError, GscWorldError) as exc:
            print(f"ERROR: {exc}")
            return 2
        print(
            f"OK release={summary['release_version']} areas={summary['areas']} "
            f"observations={summary['observations']} categories={summary['by_category']}"
        )
        return 0

    area_ids = tuple(args.areas or DEFAULT_AREAS)
    areas = _selected_areas(area_ids)
    if args.dry_run:
        print(f"source: {SOURCE['product']} ({SOURCE['version']})")
        print(f"license: {SOURCE['license']}")
        print(f"method: bounded WMS {args.side}x{args.side} point grid, no polygon download")
        for area in areas:
            print(f"  {area['area_id']}: bbox={area['bbox']} samples={args.side * args.side}")
        print(f"output: {args.output}")
        return 0

    try:
        manifest = write_artifact(
            root=args.output,
            areas=areas,
            generated_at=args.generated_at,
            side=args.side,
        )
    except (OSError, KeyError, ValueError, GscWorldError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(
        f"release={manifest['release_version']} areas={manifest['counts']['areas']} "
        f"observations={manifest['counts']['observations']} "
        f"categories={manifest['counts']['by_category']} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
