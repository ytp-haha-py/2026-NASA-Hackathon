"""Deterministic TEST-ONLY fixture data.

This module is never used to make scientific claims. Every observation is marked
`synthetic`, and the API exposes `data_kind=synthetic` so the UI can label it loudly.
Real Copernicus DEM-derived candidates come from pipeline manifests.
"""

from __future__ import annotations

from analog_api.domain.features import METHOD_VERSION

DEMO_DATASET = {
    "dataset_id": "synthetic-test-fixture",
    "provider": "Planetary Analog Explorer test fixture",
    "product_id": "synthetic-v1",
    "title": "Synthetic rectangular terrain fixture (not a source dataset)",
    "url": "repo://apps/api/src/analog_api/data/synthetic.py",
    "license": "CC0-1.0",
    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    "access_timestamp": "2026-11-14T00:00:00Z",
    "source_crs": "EPSG:3857",
    "native_resolution_m": 90.0,
    "processing_version": "synthetic-v1",
    "checksum": None,
    "source_kind": "synthetic",
    "notes": "Deterministic values for tests and UI development only.",
}

DEMO_WINDOWS = [
    {
        "location_id": "earth-synthetic-alpha",
        "display_name": "SYNTHETIC window Alpha (not an observation)",
        "lat": 24.2,
        "lon": 12.5,
        "values": {
            "slope_mean_deg": 2.5,
            "slope_p90_deg": 4.0,
            "terrain_relief_p95_p05_m": 35.0,
            "roughness_std_m": 3.2,
        },
    },
    {
        "location_id": "earth-synthetic-beta",
        "display_name": "SYNTHETIC window Beta (not an observation)",
        "lat": -24.5,
        "lon": 133.5,
        "values": {
            "slope_mean_deg": 6.0,
            "slope_p90_deg": 10.0,
            "terrain_relief_p95_p05_m": 95.0,
            "roughness_std_m": 9.0,
        },
    },
    {
        "location_id": "earth-synthetic-gamma",
        "display_name": "SYNTHETIC window Gamma (not an observation)",
        "lat": 44.7,
        "lon": -110.6,
        "values": {
            "slope_mean_deg": 12.5,
            "slope_p90_deg": 22.0,
            "terrain_relief_p95_p05_m": 310.0,
            "roughness_std_m": 28.0,
        },
    },
    {
        "location_id": "earth-synthetic-delta",
        "display_name": "SYNTHETIC window Delta (not an observation)",
        "lat": -45.0,
        "lon": -70.2,
        "values": {
            "slope_mean_deg": 18.0,
            "slope_p90_deg": 33.0,
            "terrain_relief_p95_p05_m": 620.0,
            "roughness_std_m": 45.0,
        },
    },
]

UNITS = {
    "slope_mean_deg": "degree",
    "slope_p90_deg": "degree",
    "terrain_relief_p95_p05_m": "meter",
    "roughness_std_m": "meter",
}


def synthetic_manifest() -> dict:
    candidates = []
    for window in DEMO_WINDOWS:
        lat, lon = window["lat"], window["lon"]
        candidates.append(
            {
                "location_id": window["location_id"],
                "display_name": window["display_name"],
                "lat": lat,
                "lon": lon,
                "bbox": {
                    "west": round(lon - 0.01, 6),
                    "south": round(lat - 0.01, 6),
                    "east": round(lon + 0.01, 6),
                    "north": round(lat + 0.01, 6),
                },
                "dataset_id": DEMO_DATASET["dataset_id"],
                "window_size_m": 2000,
                "analysis_resolution_m": 90,
                "crs": "EPSG:3857",
                "valid_fraction": 1.0,
                "quality_flags": ["synthetic_fixture", "not_a_real_observation"],
                "notes": "Deterministic fixture; do not use as scientific evidence.",
                "features": {
                    feature_id: {
                        "value": value,
                        "unit": UNITS[feature_id],
                        "method_version": METHOD_VERSION,
                        "extraction_scale_m": 90,
                        "valid_fraction": 1.0,
                        "quality": "synthetic",
                    }
                    for feature_id, value in window["values"].items()
                },
            }
        )
    return {
        "manifest_version": "1.0.0",
        "contract_version": "1.0.0",
        "generated_by": "analog_api.data.synthetic",
        "generated_at": "2026-11-14T00:00:00Z",
        "data_kind": "synthetic",
        "datasets": [DEMO_DATASET],
        "candidates": candidates,
        "references": [],
    }
