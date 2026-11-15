#!/usr/bin/env python
"""Reproduce the Issue #116 non-D01 ranking demonstration from the local database.

Offline: starts the real FastAPI app against the configured SQLite database, computes D01-only and
D01+D06 rankings, and asserts that the D06 clay feature flips a real pair.

    make seed
    uv run python scripts/multidim_sample_query.py
"""

from __future__ import annotations

import math

from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient


def _post(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/search/multidimensional", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def main() -> int:
    app = create_app(Settings())
    with TestClient(app) as client:
        d01_only = _post(
            client,
            {
                "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
                "filters": {"soil_clay_0_5cm_mean": {"min": 0, "max": 1000}},
                "limit": 10,
            },
        )
        combined = _post(
            client,
            {
                "targets": {
                    "slope_mean_deg": {"value": 10, "weight": 1},
                    "soil_clay_0_5cm_mean": {"value": 200, "weight": 1},
                },
                "limit": 10,
            },
        )

    d01_order = [row["location_id"] for row in d01_only["results"]]
    combined_order = [row["location_id"] for row in combined["results"]]
    assert d01_order != combined_order, "D06 did not affect the ranking"
    assert combined["eligible_candidate_count"] == 113
    assert combined["excluded_candidate_count"] == len(combined["excluded"])
    assert combined["coverage"]["features"]["soil_clay_0_5cm_mean"]["scored_result_count"] == 113

    first = combined["results"][0]
    clay = next(
        item for item in first["contributions"] if item["feature_id"] == "soil_clay_0_5cm_mean"
    )
    expected = ((clay["candidate"] - clay["target"]) / clay["scale"]) ** 2 / 2.0
    assert math.isclose(clay["contribution"], expected, rel_tol=0.0, abs_tol=1e-15)
    assert clay["observation_id"].startswith("soilgrids:")
    assert clay["uncertainty"] is not None

    print("D01-only (restricted to D06-joined candidates):")
    for rank, item in enumerate(d01_only["results"], start=1):
        print(f"  {rank}. {item['location_id']:24} distance={item['distance']:.6f}")
    print("\nD01 + D06 soil clay 0-5 cm mean:")
    for rank, item in enumerate(combined["results"], start=1):
        print(f"  {rank}. {item['location_id']:24} distance={item['distance']:.6f}")
    print(f"\nD06 contribution for rank 1 ({first['location_id']}): {clay['contribution']:.12f}")
    print(f"  candidate={clay['candidate']} {clay['unit']}, target={clay['target']}")
    print(f"  observation={clay['observation_id']}")
    print(f"  q05={clay['uncertainty']['q05']}, q95={clay['uncertainty']['q95']}")
    print("\nPASS: a real non-D01 feature changes the ranking; contribution is recomputable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
