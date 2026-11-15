"""Live-HTTP planetary AOI integration coverage for Issue #170 Goal H.

This is the complementary HTTP/job-boundary suite. It deliberately avoids repeating
the unit-level analysis tests owned by #132/#160 and the broad in-process policy
matrix: it focuses on what an HTTP client can observe and on real damaged committed
fixtures.

What is proven here:
* a committed single-cell AOI returns exactly the committed per-cell D01 values;
* real mid/high-latitude requests return an explicit ``out_of_coverage`` job failure
  with no fabricated numeric features;
* antimeridian and polar requests are accepted as planetocentric geometry, then fail
  explicitly instead of being treated as Earth WGS84;
* oversized/too-wide/tiny/invalid requests map to the documented API error codes;
* deleting or corrupting a committed fixture makes the API fail explicitly
  (``fixture_missing``/``fixture_checksum_mismatch``) instead of returning values.

Offline only: committed manifests/fixtures, isolated temp SQLite, no network.
"""

from __future__ import annotations

import json
import math
import shutil
import time
from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
COARSE = ROOT / "pipelines/planetary/coarse"
FIXTURES = ROOT / "samples/planetary/coarse/fixtures"
FEATURE_IDS = {
    "coarse_slope_mean_deg",
    "coarse_slope_p90_deg",
    "coarse_flat_fraction_lt5deg",
}
FRAMES = {"Moon": "lunar_planetocentric", "Mars": "mars_planetocentric"}
RADII_M = {"Moon": 1_737_400.0, "Mars": 3_396_190.0}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _settings(tmp_path: Path, *, coarse_dir: Path | None = None) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'planetary-http-170.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=False,
        aoi_inline=True,
        planetary_allow_network=False,
        planetary_coarse_dir=(
            str(coarse_dir) if coarse_dir is not None else "pipelines/planetary/coarse"
        ),
    )


def _windows() -> dict[str, dict]:
    """Committed analysis-eligible 250 m cells, derived from the committed manifests."""
    windows: dict[str, dict] = {}
    for pilot in _load(COARSE / "index.json")["pilots"]:
        manifest = _load(ROOT / pilot["manifest"])
        source = manifest["source"]
        region_id = str(manifest["region"]["region_id"])
        if "evidence" in region_id.lower() or source.get("same_support_as_earth") is not True:
            continue
        cells = [
            cell
            for cell in manifest["cells"]
            if math.isclose(float(cell["analysis_resolution_m"]), 250.0)
        ]
        if not cells:
            continue
        windows[source["body"]] = {
            "cells": cells,
            "bbox": {
                "west": min(float(cell["bbox_body_lonlat"]["west"]) for cell in cells),
                "south": min(float(cell["bbox_body_lonlat"]["south"]) for cell in cells),
                "east": max(float(cell["bbox_body_lonlat"]["east"]) for cell in cells),
                "north": max(float(cell["bbox_body_lonlat"]["north"]) for cell in cells),
            },
        }
    return windows


WINDOWS = _windows()


def _bbox_at(body: str, lat: float, lon: float, side_km: float = 6.0) -> dict[str, float]:
    metres_per_degree = math.pi * RADII_M[body] / 180.0
    half_lat = (side_km * 1000.0 / 2.0) / metres_per_degree
    half_lon = (side_km * 1000.0 / 2.0) / (
        metres_per_degree * max(math.cos(math.radians(lat)), 1e-6)
    )
    return {
        "west": round(lon - half_lon, 6),
        "south": round(lat - half_lat, 6),
        "east": round(lon + half_lon, 6),
        "north": round(lat + half_lat, 6),
    }


def _post(client: TestClient, body: str, bbox: dict[str, float]):
    return client.post(
        "/api/v1/planetary/areas/analyze",
        json={"body": body, "bbox": bbox, "requested_features": sorted(FEATURE_IDS)},
    )


def _terminal(client: TestClient, payload: dict, *, timeout_s: float = 30.0) -> dict:
    """Fetch the durable JobOut contract for a POST response (or pass a job through)."""
    if "error" in payload and payload.get("status") in {"completed", "partial", "failed"}:
        return payload
    job_id = payload["job_id"]
    deadline = time.monotonic() + timeout_s
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    while job.get("status") in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.1)
        job = client.get(f"/api/v1/jobs/{job_id}").json()
    return job


def _assert_no_features(payload: dict) -> None:
    assert payload.get("result") is None, payload
    text = json.dumps(payload)
    assert not [feature_id for feature_id in FEATURE_IDS if feature_id in text], text


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("planetary-http-170")
    settings = _settings(tmp)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ANALOG_AOI_RUNTIME_DIR", str(tmp / "aoi-runtime"))
        with TestClient(create_app(settings)) as test_client:
            yield test_client


@pytest.mark.parametrize("body", ["Moon", "Mars"])
def test_committed_cell_returns_exact_committed_values(client: TestClient, body: str):
    """One real committed cell must reproduce its committed D01 manifest values."""
    cell = WINDOWS[body]["cells"][0]
    response = _post(client, body, cell["bbox_body_lonlat"])
    assert response.status_code in {200, 202}, response.text
    job = _terminal(client, response.json())
    assert job["status"] == "completed", job
    result = job["result"]
    assert result["body"] == body
    assert result["coordinate_frame"] == FRAMES[body]
    assert result["analysis_kind"] == "committed_reference_fixture"
    assert set(result["features"]) == FEATURE_IDS
    for feature_id, expected in cell["features"].items():
        assert result["features"][feature_id] == pytest.approx(
            float(expected), rel=0.0, abs=1e-9
        ), feature_id
    for source_id, status in job["provider_status"].items():
        assert status["status"] == "verified_committed_fixture", source_id
        assert status["network_used"] is False, source_id
        assert status["cells"] == 1, source_id
    assert "4326" not in json.dumps(job)

    typed = client.get(f"/api/v1/typed/areas/{job['area_id']}")
    assert typed.status_code == 200, typed.text
    typed_body = typed.json()
    assert typed_body["body"] == body
    assert typed_body["coordinate_frame"] == FRAMES[body]
    rows = typed_body["observations"]
    assert {row["feature_id"] for row in rows} == FEATURE_IDS
    assert all(row["dimension_code"] == "D01" for row in rows)
    assert all(row["score_role"] == "evidence_only" for row in rows)
    assert all("not_earth_wgs84" in row["quality_flags"] for row in rows)


@pytest.mark.parametrize(
    ("body", "lat", "lon"),
    [
        ("Moon", 45.0, -30.0),
        ("Moon", 82.0, 120.0),
        ("Mars", -45.0, 150.0),
        ("Mars", 78.0, -60.0),
    ],
)
def test_dispersed_mid_high_latitude_is_explicit_no_data(
    client: TestClient, body: str, lat: float, lon: float
):
    """Real mid/high-latitude AOIs have no committed coverage; no value is invented."""
    bbox = _bbox_at(body, lat, lon)
    response = _post(client, body, bbox)
    if response.status_code == 422:
        error = response.json()["error"]
        assert error["code"] == "out_of_coverage"
        assert "No committed analysis-eligible" in error["message"]
        _assert_no_features(response.json())
        return
    assert response.status_code in {200, 202}, response.text
    job = _terminal(client, response.json())
    assert job["status"] == "failed", job
    assert job["error"]["code"] == "out_of_coverage"
    _assert_no_features(job)
    if body == "Moon":
        assert "allow_remote=True" in job["error"]["message"]
    assert "4326" not in json.dumps(job)


def test_failed_job_does_not_advertise_an_unpersisted_typed_area(client: TestClient):
    """A failed out-of-coverage job must not link to a typed area that GET would 404.

    The durable job row may carry an ``area_id`` even when no typed area was persisted. The
    analyze response should advertise only the job (and the bbox map query), never a
    guaranteed-404 ``links.typed_area``.
    """
    response = _post(client, "Moon", _bbox_at("Moon", 45.0, -30.0))
    if response.status_code == 422:
        # Validation-time coverage rejection never creates a job or links.
        assert response.json()["error"]["code"] == "out_of_coverage"
        return
    assert response.status_code in {200, 202}, response.text
    payload = response.json()
    assert "typed_area" not in payload["links"], payload["links"]
    if payload.get("area_id"):
        assert client.get(f"/api/v1/typed/areas/{payload['area_id']}").status_code == 404, payload
    if payload["status"] in {"queued", "running"}:
        job = _terminal(client, payload)
        assert job["status"] == "failed", job
        _assert_no_features(job)
        return
    assert payload["status"] == "failed", payload
    assert "map_features" in payload["links"]
    _assert_no_features(payload)


@pytest.mark.parametrize(
    ("body", "bbox", "crossing"),
    [
        ("Moon", {"west": 179.9, "south": 0.0, "east": -179.9, "north": 0.1}, True),
        ("Mars", {"west": 179.95, "south": -10.0, "east": -179.95, "north": -9.85}, True),
        ("Moon", _bbox_at("Moon", 89.5, 0.0, side_km=2.0), False),
        ("Mars", _bbox_at("Mars", -89.5, 0.0, side_km=2.0), False),
    ],
)
def test_antimeridian_and_polar_are_planetocentric_then_explicitly_uncovered(
    client: TestClient, body: str, bbox: dict[str, float], crossing: bool
):
    """Polar/dateline geometry is accepted in the body frame, never as Earth WGS84."""
    response = _post(client, body, bbox)
    assert response.status_code in {200, 202}, response.text
    created = response.json()
    assert created["coordinate_frame"] == FRAMES[body]
    link = created["links"]["map_features"]
    if crossing:
        # The link carries the normalized bbox, so west > east signals the split.
        raw_bbox = link.split("bbox=", 1)[1]
        west, _south, east, _north = (float(value) for value in raw_bbox.split(","))
        assert west > east, raw_bbox
    job = _terminal(client, created)
    assert job["status"] == "failed", job
    assert job["error"]["code"] == "out_of_coverage"
    _assert_no_features(job)


@pytest.mark.parametrize(
    ("bbox", "code"),
    [
        ({"west": 0.0, "south": 0.0, "east": 10.0, "north": 10.0}, "aoi_too_large"),
        ({"west": -170.0, "south": 0.0, "east": 170.0, "north": 0.1}, "bbox_too_wide"),
        ({"west": 0.0, "south": 0.0, "east": 0.001, "north": 0.001}, "aoi_too_small"),
        ({"west": 0.0, "south": 90.5, "east": 0.1, "north": 95.0}, "invalid_bbox"),
    ],
)
def test_invalid_planetary_bbox_maps_to_documented_api_error(
    client: TestClient, bbox: dict[str, float], code: str
):
    response = _post(client, "Moon", bbox)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    assert "4326" not in json.dumps(response.json())


def _damaged_catalog(tmp_path: Path, body: str, mode: str) -> Path:
    root = tmp_path / "broken"
    coarse = root / "pipelines/planetary/coarse"
    fixtures = root / "samples/planetary/coarse/fixtures"
    shutil.copytree(COARSE, coarse)
    shutil.copytree(FIXTURES, fixtures)
    manifest_name = (
        "moon_lola128_window01.json" if body == "Moon" else "mars_ctx_jezero_window01.json"
    )
    manifest = _load(coarse / manifest_name)
    fixture_name = Path(manifest["cells"][0]["fixture"]["path"]).name
    target = fixtures / fixture_name
    if mode == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b"\x00")
    return coarse


@pytest.mark.parametrize(
    ("body", "mode", "expected_code"),
    [
        ("Moon", "missing", "fixture_missing"),
        ("Mars", "corrupt", "fixture_checksum_mismatch"),
    ],
)
def test_damaged_committed_fixture_fails_explicitly(
    tmp_path: Path,
    body: str,
    mode: str,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """A real damaged source fixture must not yield values or a fake success."""
    coarse = _damaged_catalog(tmp_path, body, mode)
    monkeypatch.setenv("ANALOG_AOI_RUNTIME_DIR", str(tmp_path / "aoi-runtime"))
    with TestClient(create_app(_settings(tmp_path, coarse_dir=coarse))) as client:
        response = _post(client, body, WINDOWS[body]["bbox"])
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == expected_code
        assert "fixture" in error["message"].lower()
        _assert_no_features(response.json())
