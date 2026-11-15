#!/usr/bin/env python
"""Build the machine-readable D01-D06 Earth/Moon/Mars coverage matrix (Issue #141).

This is an integration artifact assembled from committed manifests, typed releases/bundles,
catalog inventory, and registry definitions. It does not fetch remote data and does not
fabricate coverage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _manifest_adapters() -> list[dict[str, Any]]:
    items = []
    for rel in [
        "pipelines/geology/typed/manifest.json",
        "pipelines/pilots/d02_spectral_detection/manifest.json",
        "pipelines/pilots/d03_planetary_thermal/manifest.json",
        "pipelines/pilots/d03_earth_power/manifest.json",
        "pipelines/pilots/d04_earth_power/manifest.json",
        "pipelines/pilots/d04_water_ice_planetary/manifest.json",
        "pipelines/pilots/d05_earth_power/manifest.json",
        "pipelines/pilots/d05_spice_illumination/manifest.json",
        "pipelines/pilots/d06_earth_soilgrids/manifest.json",
        "pipelines/pilots/d06_earth_soilgrids_all_0_5cm/manifest.json",
        "pipelines/pilots/d06_earth_soilgrids_depths/manifest.json",
        "pipelines/pilots/d06_earth_soilgrids_expansion_01/manifest.json",
        "pipelines/coarse/real-pilot/index/snapshots/geomorpho250-real-pilot-20261115/manifest.json",
    ]:
        path = ROOT / rel
        if not path.exists():
            continue
        payload = _load_json(path)
        kind = "manifest"
        if (path.parent / "observations.jsonl").exists() and payload.get("release_version"):
            kind = "typed_acquisition_release"
        elif (path.parent / "observations.jsonl").exists() and payload.get("export_version"):
            kind = "typed_export_bundle"
        elif payload.get("schema_version") == "spectral-detection-pilot-1.0.0":
            kind = "spectral_detection_pilot"
        elif path.name == "manifest.json" and "coarse" in rel:
            kind = "coarse_index_snapshot"
        items.append({"path": rel, "kind": kind, "manifest": payload})
    return items


def main() -> int:
    inventory = _load_yaml(ROOT / "catalog/dimension-source-inventory.yaml")
    datasets = _load_yaml(ROOT / "catalog/datasets.yaml")
    impl = (
        _load_json(ROOT / "docs/IMPLEMENTATION_STATUS.json")
        if (ROOT / "docs/IMPLEMENTATION_STATUS.json").exists()
        else {}
    )
    matrix = {
        "matrix_version": "d01-d06-coverage-matrix-1.0.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "issue": 141,
        "scope": "Earth/Moon/Mars D01-D06 coverage/status integration matrix from committed artifacts",
        "global_coverage_claimed": False,
        "data_status_legend": {
            "measured": "Direct measurement/observation value",
            "modeled": "Model prediction or model-derived value, not a direct observation",
            "derived": "Derived/indexed value from verified processing of source data",
            "evidence": "Evidence/context record; not a legal scored similarity input unless explicitly gated",
            "no_coverage": "Explicitly known lack of coverage in source/record",
            "unsupported": "Not supported for this body/query role/support/time/depth",
            "not_indexed": "No indexed/queryable record in this database/build for the location/support",
        },
        "bodies": ["Earth", "Moon", "Mars"],
        "dimensions": inventory.get("dimensions", []),
        "datasets_catalog": datasets,
        "manifest_adapters": _manifest_adapters(),
        "typed_query_contract": {
            "endpoint": "GET /api/v1/coverage/location",
            "contract_version": "location-coverage-status-1.0.0",
            "parameters": ["body", "lat/lon or west/south/east/north", "bbox_lon_convention"],
            "returns": [
                "per-dimension/per-feature status",
                "actual indexed/queryable intersecting records",
                "declared source inventory",
                "manifest adapter inventory",
                "explicit limitations and no-fabrication guarantees",
            ],
        },
        "scoring_contract": {
            "legacy": {
                "version": "feature-weighted-euclidean-v1",
                "formula": "distance = sqrt(Σ(v_f * ((x_f-t_f)/s_f)^2) / Σ(v_f))",
                "default_for": "/api/v1/search/multidimensional when no dimension_weights/scoring_version provided",
            },
            "dimension_weighted": {
                "version": "dimension-weighted-rms-v1",
                "formula": "q_d = Σ(v_df*z_df^2)/Σ(v_df); distance = sqrt(Σ(w_d*q_d)/Σ(w_d))",
                "feature_count_invariant": "per dimension mean then cross-dimension weighted RMS",
            },
        },
        "known_indexed_queryable_counts": {
            "legacy_fine_earth_candidates": 113,
            "planetary_references": {"Moon": 18, "Mars": 5},
            "typed_observations_seeded_in_dev_db": 2140,
            "typed_areas_exact_join_to_fine_candidates": 113,
            "note": "Counts are database/snapshot dependent; verify with /api/v1/capabilities, /api/v1/typed/coverage, /api/v1/search/features",
        },
        "gaps": [
            "No unified global D01-D06 measured coverage; many D02-D05 Moon/Mars sources remain evidence-only, model-derived, planned or not fetched.",
            "Arbitrary planetary point intersection for projected typed areas is limited without full CRS reprojection in the status endpoint.",
            "D06 Moon/Mars sample measurements unavailable (no_coverage).",
            "Coarse D01 index is local/pilot, not globally exhaustive; cross-body coarse ranking disabled by eligibility gates.",
            "Some pilot manifests use heterogeneous formats; adapter normalization is manifest-driven and read-only in this integration.",
        ],
        "rebuild_instructions": [
            "uv sync && cd apps/web && npm ci",
            "uv run python scripts/build_coverage_matrix.py",
            "make seed (or ensure typed releases/bundles are present under pipelines/pilots and pipelines/geology/typed)",
            "uv run python -m pytest apps/api/tests/test_multidim_api.py apps/api/tests/test_contract.py -q",
            "curl 'http://127.0.0.1:8000/api/v1/coverage/location?body=Earth&lat=36.6&lon=-117.1'",
            "curl 'http://127.0.0.1:8000/api/v1/typed/coverage'",
            "uv run python scripts/export_openapi.py && (cd apps/web && npm run generate:api)",
        ],
        "implementation_status": impl,
        "limitations": [
            "Not a global or exhaustive survey.",
            "Missing values are not zero-filled; no_coverage/unsupported/not_indexed remain explicit.",
            "Model predictions and evidence records are labeled and not promoted to measurements.",
        ],
    }
    out = ROOT / "docs" / "coverage-matrix-d01-d06.v1.json"
    out.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
