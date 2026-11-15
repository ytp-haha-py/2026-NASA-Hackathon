"""Opt-in live SoilGrids depth contract test (never run in CI by default)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from analog_pipelines.acquisition.models import PlanArea
from analog_pipelines.acquisition.planner import build_plan
from analog_pipelines.acquisition.providers_soilgrids import (
    SOILGRIDS_DEPTHS,
    SoilGridsProvider,
    depth_interval_m,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("ANALOG_LIVE_NETWORK") != "1",
        reason="set ANALOG_LIVE_NETWORK=1 to call the public ISRIC SoilGrids WCS endpoint",
    ),
]


def test_live_soilgrids_serves_all_registered_depth_intervals(tmp_path: Path):
    area = PlanArea(
        area_id="earth-live-tiny",
        body="Earth",
        bbox={"west": 10.0, "south": 45.0, "east": 10.01, "north": 45.01},
        crs="EPSG:4326",
        coordinate_frame="earth_wgs84",
    )
    plan = build_plan(
        scope="live",
        areas=[area],
        dimensions=["D06"],
        bodies=["Earth"],
        provider_ids=["soilgrids-wcs"],
        parameters={
            "properties": "clay",
            "depths": ",".join(SOILGRIDS_DEPTHS),
            "statistics": "mean",
        },
    )
    assert len(plan.tasks) == len(SOILGRIDS_DEPTHS)
    provider = SoilGridsProvider()
    for task in plan.tasks:
        asset = provider.acquire(task, tmp_path)
        assert asset.http_status == 200
        assert Path(asset.path).read_bytes()[:2] in (b"II", b"MM")
        observation = provider.process(asset, task)[0]
        assert observation.state == "detected"
        assert observation.depth_support.min_m == depth_interval_m(task.parameters["depth"])[0]
        assert observation.depth_support.max_m == depth_interval_m(task.parameters["depth"])[1]
        assert observation.spatial_support.native_resolution_m == 250.0
        assert provider.validate([observation]).ok
