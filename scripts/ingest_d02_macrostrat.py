#!/usr/bin/env python
"""Bounded D02 Earth ingest from the Macrostrat geologic-units API.

Adds three existing Earth fine candidates as *evidence-only* map-unit records. Macrostrat is a
third-party, partially global map compilation; values are point-query map units, not a local
mineral measurement and not a validated taxonomy crosswalk.

    uv run python scripts/ingest_d02_macrostrat.py
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
TYPED = ROOT / "pipelines/geology/typed"
RAW = ROOT / "pipelines/geology/macrostrat_expansion_01"
AREAS = ("earth-grand-canyon-r2c2", "earth-namib-r2c2", "earth-iceland-r2c2")
ENDPOINT = "https://macrostrat.org/api/v2/geologic_units/map"
DATASET_ID = "macrostrat-api"
METHOD_VERSION = "macrostrat-geologic-units-1.0.0"
MAX_UNITS = 5
SCALE_RANK = {"large": 0, "medium": 1, "small": 2, "tiny": 3}


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _area_configs() -> dict[str, dict[str, Any]]:
    return {
        str(item["area_id"]): item
        for item in _load_yaml(ROOT / "configs/areas.yaml").get("areas", [])
        if str(item.get("area_id")) in AREAS
    }


def _fetch(area_id: str, lat: float, lon: float) -> tuple[dict[str, Any], bytes]:
    url = f"{ENDPOINT}?lat={lat:.6f}&lng={lon:.6f}"
    request = urllib.request.Request(url, headers={"User-Agent": "AnalogX-D02-ingest/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    return json.loads(raw), raw


def _select_units(payload: dict[str, Any]) -> list[dict[str, Any]]:
    success = payload.get("success") or {}
    refs = success.get("refs") or {}
    units = []
    for item in success.get("data") or []:
        source_id = item.get("source_id")
        units.append(
            {
                "map_id": item.get("map_id"),
                "source_id": source_id,
                "name": item.get("name") or "",
                "lith": item.get("lith") or "",
                "scale": item.get("scale") or "unknown",
                "best_interval": item.get("best_int_name") or "",
                "source_reference": refs.get(str(source_id)) or "",
                "description": (item.get("descrip") or "")[:300],
            }
        )
    units.sort(key=lambda item: (SCALE_RANK.get(item["scale"], 9), item["map_id"] or 0))
    return units[:MAX_UNITS]


def _area(area_id: str, config: dict[str, Any]) -> dict[str, Any]:
    bbox = config["bbox"]
    west, south, east, north = (
        float(bbox["west"]),
        float(bbox["south"]),
        float(bbox["east"]),
        float(bbox["north"]),
    )
    return {
        "analysis_crs": "EPSG:4326",
        "area_id": area_id,
        "area_km2": None,
        "bbox": {"west": west, "south": south, "east": east, "north": north},
        "body": "Earth",
        "coordinate_frame": "earth_wgs84",
        "crs": "EPSG:4326",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ]
            ],
        },
    }


def main() -> int:
    configs = _area_configs()
    if set(configs) != set(AREAS):
        raise SystemExit(f"missing area configs: {set(AREAS) - set(configs)}")
    RAW.mkdir(parents=True, exist_ok=True)
    areas = json.loads((TYPED / "areas.json").read_text(encoding="utf-8"))
    assets = json.loads((TYPED / "assets.json").read_text(encoding="utf-8"))
    datasets = json.loads((TYPED / "datasets.json").read_text(encoding="utf-8"))
    observations_path = TYPED / "observations.jsonl"
    existing_lines = [
        line for line in observations_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    existing_ids = {json.loads(line)["observation_id"] for line in existing_lines}
    existing_area_ids = {item["area_id"] for item in areas}

    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    new_observations: list[dict[str, Any]] = []
    for area_id in AREAS:
        config = configs[area_id]
        lat = float(config["center"]["lat"])
        lon = float(config["center"]["lon"])
        payload, raw = _fetch(area_id, lat, lon)
        (RAW / f"{area_id}.json").write_bytes(raw)
        checksum = hashlib.sha256(raw).hexdigest()
        asset_id = f"geology-asset:{DATASET_ID}:{checksum[:16]}"
        uri = f"{ENDPOINT}?lat={lat:.6f}&lng={lon:.6f}"
        units = _select_units(payload)
        if not units:
            raise SystemExit(f"no Macrostrat units returned for {area_id}")
        if area_id not in existing_area_ids:
            areas.append(_area(area_id, config))
        if not any(item["asset_id"] == asset_id for item in assets):
            assets.append(
                {
                    "asset_id": asset_id,
                    "bytes": len(raw),
                    "checksum": checksum,
                    "checksum_algorithm": "sha256",
                    "checksum_scope": "source",
                    "dataset_id": DATASET_ID,
                    "fetched_at": now,
                    "native_crs": "EPSG:4326",
                    "native_units": None,
                    "nodata": None,
                    "offset": None,
                    "redistribution_allowed": "yes",
                    "scale": None,
                    "schema_version": "2.0.0",
                    "uri": uri,
                }
            )
        observation_id = f"macrostrat:{area_id}:geologic_unit"
        if observation_id in existing_ids:
            continue
        new_observations.append(
            {
                "area_id": area_id,
                "body": "Earth",
                "dataset_ids": [DATASET_ID],
                "depth_support": {"kind": "surface", "max_m": None, "min_m": None},
                "dimension_code": "D02",
                "evidence": None,
                "feature_id": "geologic_unit",
                "legacy_provenance": None,
                "method_version": METHOD_VERSION,
                "notes": (
                    "Macrostrat API v2 point query. Map units are a third-party compilation with "
                    "partial global coverage; unit names/codes are not crosswalked and cannot be "
                    "used for similarity or a category filter."
                ),
                "observation_id": observation_id,
                "quality_flags": [
                    "third_party_map_compilation",
                    "partial_global_coverage",
                    "no_taxonomy_crosswalk",
                    "point_query_not_area_fraction",
                    "evidence_only",
                ],
                "schema_version": "2.0.0",
                "score_role": "evidence_only",
                "source_asset_ids": [asset_id],
                "spatial_support": {
                    "area_km2": None,
                    "bbox": {
                        "west": float(config["bbox"]["west"]),
                        "south": float(config["bbox"]["south"]),
                        "east": float(config["bbox"]["east"]),
                        "north": float(config["bbox"]["north"]),
                    },
                    "bbox_crs": "EPSG:4326",
                    "crs": "EPSG:4326",
                    "kind": "point",
                    "native_resolution_m": None,
                    "notes": "Macrostrat point query at the AOI centre; not a 1.98 km area fraction.",
                    "resolution_m": None,
                    "source_resolution_m": None,
                    "valid_fraction": None,
                    "window_size_m": None,
                },
                "state": "detected",
                "temporal_support": {
                    "end": None,
                    "kind": "static",
                    "local_solar_time": None,
                    "notes": "Compilation age is source-map dependent.",
                    "start": None,
                },
                "uncertainty": None,
                "unit": None,
                "value": {
                    "code": "macrostrat_point_query_units",
                    "scheme": "macrostrat-geologic-units-v2",
                    "license": "CC-BY 4.0",
                    "source": "Macrostrat API v2 geologic_units/map",
                    "query_lat": lat,
                    "query_lon": lon,
                    "selection_rule": (
                        f"point query; all returned map units sorted by scale rank then map_id, "
                        f"max {MAX_UNITS}; no area fraction; no taxonomy crosswalk"
                    ),
                    "units": units,
                },
                "value_kind": "category",
            }
        )

    if not any(item["dataset_id"] == DATASET_ID for item in datasets):
        datasets.append(
            {
                "body": "Earth",
                "dataset_id": DATASET_ID,
                "depth_support": "surface",
                "license": "CC-BY 4.0",
                "license_url": "https://macrostrat.org/api/v2",
                "native_crs": "EPSG:4326",
                "native_resolution_m": None,
                "product_id": "Macrostrat geologic_units/map API",
                "provider": "Macrostrat / University of Wisconsin-Madison",
                "redistribution_allowed": "yes",
                "schema_version": "2.0.0",
                "temporal_support": "source-map compilation dependent",
                "url": ENDPOINT,
                "verification_status": "verified",
                "version": "API v2",
            }
        )

    (TYPED / "areas.json").write_text(json.dumps(areas, indent=2) + "\n", encoding="utf-8")
    (TYPED / "assets.json").write_text(json.dumps(assets, indent=2) + "\n", encoding="utf-8")
    (TYPED / "datasets.json").write_text(json.dumps(datasets, indent=2) + "\n", encoding="utf-8")
    if new_observations:
        with observations_path.open("a", encoding="utf-8") as handle:
            for observation in new_observations:
                handle.write(json.dumps(observation, sort_keys=True) + "\n")

    observations = [
        json.loads(line)
        for line in observations_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    manifest = json.loads((TYPED / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(
        {
            "areas": len(areas),
            "assets": len(assets),
            "datasets": len(datasets),
            "observations": len(observations),
            "observations_sha256": hashlib.sha256(observations_path.read_bytes()).hexdigest(),
            "generated_at": now,
            "macrostrat_expansion": {
                "areas": list(AREAS),
                "observations": len(new_observations),
                "method_version": METHOD_VERSION,
                "license": "CC-BY 4.0",
                "role": "evidence_only; no taxonomy crosswalk",
            },
        }
    )
    (TYPED / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "areas": len(areas),
                "assets": len(assets),
                "datasets": len(datasets),
                "observations": len(observations),
                "new_observations": len(new_observations),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
