"""Goal H (#170) planetary AOI end-to-end QA.

This file is integration-owned and covers reproducible offline Moon/Mars on-demand AOI
analysis, negative cases, API/OpenAPI/type-surface compatibility checks and cache/resume
behaviour. It uses only committed manifests/fixtures and isolated temporary databases.
No network access, no raw raster downloads, no synthetic "observed" values and no claim
of global or exhaustive six-dimension coverage.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pytest
from analog_api.adapters.jobs import JobRunner
from analog_api.config import Settings
from analog_api.domain.errors import AppError
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]

# Committed Moon LOLA128 pilot window cells (pipelines/planetary/coarse/moon_lola128_window01.json).
# Three dispersed/independent real AOIs inside the verified pilot: SW cell, NE cell, full 4-cell block.
MOON_SW_CELL = {"west": 19.837481, "south": -10.159889, "east": 19.995, "north": -10.004958}
MOON_NE_CELL = {"west": 20.005, "south": -9.995, "east": 20.162433, "north": -9.840069}
MOON_FULL_BLOCK = {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85}

# Committed Mars Jezero CTX pilot cells (pipelines/planetary/coarse/mars_ctx_jezero_window01.json).
# Three dispersed/independent real AOIs inside the verified pilot: SW cell, NE cell, full 4-cell block.
MARS_SW_CELL = {"west": 78.087409, "south": 18.478312, "east": 78.166341, "north": 18.552665}
MARS_NE_CELL = {"west": 78.176341, "south": 18.562665, "east": 78.255273, "north": 18.637018}
MARS_FULL_BLOCK = {"west": 78.08, "south": 18.47, "east": 78.27, "north": 18.65}

PLANETARY_AOI_METHOD = "planetary-terrain-horn-250m-aoi-1.0.0"
PLANETARY_SOURCE_METHOD = "coarse-planetary-dem-horn-1.0.0"
D01_FEATURES = [
    "coarse_slope_mean_deg",
    "coarse_slope_p90_deg",
    "coarse_flat_fraction_lt5deg",
]
DIMENSIONS = {f"D0{i}" for i in range(1, 7)}


def _settings(tmp_path: Path, *, inline: bool = True) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'goal170.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=True,
        aoi_inline=inline,
        global_index_dir=str(ROOT / "pipelines/coarse/real-pilot/index"),
        planetary_allow_network=False,
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANALOG_AOI_RUNTIME_DIR", str(tmp_path / "aoi-runtime"))
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        yield test_client


def _create_planetary(
    client: TestClient, body: str, bbox: dict[str, float], *, idempotency_key: str | None = None
):
    payload: dict[str, object] = {"body": body, "bbox": bbox, "requested_features": D01_FEATURES}
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    return client.post("/api/v1/planetary/areas/analyze", json=payload)


def _wait_terminal(client: TestClient, job_id: str, *, timeout_s: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout_s
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    while job.get("status") in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.05)
        job = client.get(f"/api/v1/jobs/{job_id}").json()
    return job


def _assert_planetary_success(
    client: TestClient, body: str, bbox: dict[str, float], *, expected_frame: str
):
    response = _create_planetary(client, body, bbox)
    assert response.status_code in {200, 202}, response.text
    created = response.json()
    assert created["body"] == body
    assert created["coordinate_frame"] == expected_frame
    assert "EPSG:4326" not in json.dumps(created)
    assert created["status"] in {"completed", "partial"}, created
    assert created["area_id"], created
    assert created["method_version"] == PLANETARY_AOI_METHOD
    assert created["links"]["job"] == f"/api/v1/jobs/{created['job_id']}"
    assert created["links"]["typed_area"] == f"/api/v1/typed/areas/{created['area_id']}"
    assert "bbox=" in created["links"]["map_features"]

    result = created["result"]
    assert isinstance(result, dict) and result
    assert result["body"] == body
    assert result["coordinate_frame"] == expected_frame
    assert result["method_version"] == PLANETARY_AOI_METHOD
    assert result["analysis_kind"] == "committed_reference_fixture"
    features = result["features"]
    for feature_id in D01_FEATURES:
        value = features[feature_id]
        assert isinstance(value, (int, float)) and math.isfinite(value)
    assert 0.0 <= features["coarse_slope_mean_deg"] <= 90.0
    assert 0.0 <= features["coarse_slope_p90_deg"] <= 90.0
    assert 0.0 <= features["coarse_flat_fraction_lt5deg"] <= 1.0

    support = result["dimension_support"]
    assert set(support) == DIMENSIONS
    assert support["D01"]["status"] == "present"
    assert support["D01"]["method_version"] == PLANETARY_AOI_METHOD
    assert support["D01"]["source_method_version"] == PLANETARY_SOURCE_METHOD
    for dim in ("D02", "D03", "D04", "D05", "D06"):
        assert support[dim]["status"] == "unsupported_for_on_demand_analysis"
        assert support[dim]["value"] is None
        assert support[dim]["reason"]

    # Durable job and typed area persistence.
    job = client.get(f"/api/v1/jobs/{created['job_id']}")
    assert job.status_code == 200, job.text
    job_body = job.json()
    assert job_body["status"] in {"completed", "partial"}
    assert job_body["area_id"] == created["area_id"]
    assert job_body["body"] == body
    assert job_body["coordinate_frame"] == expected_frame
    assert job_body["error"] is None

    typed = client.get(f"/api/v1/typed/areas/{created['area_id']}")
    assert typed.status_code == 200, typed.text
    typed_body = typed.json()
    assert typed_body["body"] == body
    assert str(typed_body.get("crs") or "") != "EPSG:4326"
    assert "4326" not in str(typed_body.get("crs") or "")
    assert any(
        obs.get("dimension_code") == "D01" and obs.get("feature_id") in D01_FEATURES
        for obs in (typed_body.get("observations") or [])
    )

    overlay = client.get(
        "/api/v1/planetary/map-features",
        params={
            "body": body,
            "bbox": f"{bbox['west'] - 0.05},{bbox['south'] - 0.05},{bbox['east'] + 0.05},{bbox['north'] + 0.05}",
            "limit": 200,
        },
    )
    assert overlay.status_code == 200, overlay.text
    overlay_body = overlay.json()
    assert any(
        feature.get("typed_area_id") == created["area_id"]
        or str(feature.get("feature_id", "")).endswith(created["area_id"])
        for feature in overlay_body.get("features", [])
    )

    # Repeated identical request must be cache/idempotent friendly.
    cached = _create_planetary(client, body, bbox)
    assert cached.status_code == 200, cached.text
    assert cached.json()["cached"] is True
    assert cached.json()["job_id"] == created["job_id"]
    return created


@pytest.mark.parametrize(
    ("label", "bbox"),
    [
        ("moon_sw_cell", MOON_SW_CELL),
        ("moon_ne_cell", MOON_NE_CELL),
        ("moon_full_committed_block", MOON_FULL_BLOCK),
    ],
)
def test_moon_real_aoi_e2e_three_committed_cases(
    client: TestClient, label: str, bbox: dict[str, float]
):
    """Three distinct real Moon AOIs from the committed LOLA128 pilot (bounded, not global)."""
    created = _assert_planetary_success(client, "Moon", bbox, expected_frame="lunar_planetocentric")
    assert created["provider_status"], (label, created)
    assert all(
        status.get("network_used") is False
        for status in created["provider_status"].values()
        if isinstance(status, dict)
    ), (label, created["provider_status"])


@pytest.mark.parametrize(
    ("label", "bbox"),
    [
        ("mars_sw_cell", MARS_SW_CELL),
        ("mars_ne_cell", MARS_NE_CELL),
        ("mars_full_committed_block", MARS_FULL_BLOCK),
    ],
)
def test_mars_real_aoi_e2e_three_committed_cases(
    client: TestClient, label: str, bbox: dict[str, float]
):
    """Three distinct real Mars AOIs from the committed Jezero CTX pilot (bounded, not global)."""
    created = _assert_planetary_success(client, "Mars", bbox, expected_frame="mars_planetocentric")
    assert created["provider_status"], (label, created)


@pytest.mark.parametrize("body", ["Moon", "Mars"])
def test_planetary_negative_out_of_coverage_nodata(client: TestClient, body: str):
    """Out-of-coverage AOIs are explicit nodata/coverage failures, never fabricated."""
    bbox = {"west": 0.0, "south": -5.0, "east": 0.05, "north": -4.95}
    response = _create_planetary(client, body, bbox)
    assert response.status_code != 404, response.text
    failed_area_id = None
    if response.status_code in {200, 202}:
        body_json = response.json()
        assert body_json["status"] == "failed", body_json
        assert body_json["result"] in (None, {}), body_json
        reasons = json.dumps(body_json.get("provider_status") or {})
        assert "coverage" in reasons.lower() or "out_of" in reasons.lower(), body_json
        failed_area_id = body_json.get("area_id")
    else:
        assert response.status_code in {422, 503}, response.text
        code = str(response.json().get("error", {}).get("code", "")).lower()
        assert any(
            token in code for token in ("coverage", "outside", "pilot", "source", "unsupported")
        ), response.text

    if failed_area_id:
        detail = client.get(f"/api/v1/typed/areas/{failed_area_id}")
        assert detail.status_code == 404, detail.text


@pytest.mark.parametrize("body", ["Moon", "Mars"])
def test_planetary_negative_oversized_aoi(client: TestClient, body: str):
    response = _create_planetary(
        client, body, {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0}
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "aoi_too_large"


@pytest.mark.parametrize(
    "bbox",
    [
        {"west": 179.9, "south": -0.05, "east": -179.9, "north": 0.05},  # dateline crossing
        {"west": -10.0, "south": 89.5, "east": 10.0, "north": 89.55},  # polar
    ],
)
def test_planetary_negative_dateline_and_polar_are_not_silently_accepted(client: TestClient, bbox):
    """Valid Moon geometry cases that are outside committed coverage must be explicit."""
    response = _create_planetary(client, "Moon", bbox)
    assert response.status_code in {200, 202, 422, 503}, response.text
    if response.status_code in {200, 202}:
        payload = response.json()
        assert payload["status"] == "failed", payload
        assert payload["result"] in (None, {}), payload
    else:
        assert response.json().get("error", {}).get("code") in {
            "out_of_coverage",
            "invalid_bbox",
            "aoi_too_large",
            "validation_error",
        }


def test_planetary_negative_source_outage_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ANALOG_AOI_RUNTIME_DIR", str(tmp_path / "aoi-runtime"))

    def _boom(self, request):  # noqa: ANN001, ARG001
        raise AppError(
            "source_extraction_failed",
            "Simulated planetary source outage while reading committed fixtures.",
            status_code=503,
        )

    app = create_app(_settings(tmp_path))
    # Simulate an upstream/provider断線 at the provider boundary (no network involved).
    monkeypatch.setattr(type(app.state.planetary_aoi_provider), "analyze", _boom)
    with TestClient(app) as test_client:
        response = _create_planetary(test_client, "Moon", MOON_FULL_BLOCK)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "failed", payload
        assert payload["cached"] is False
        job = test_client.get(f"/api/v1/jobs/{payload['job_id']}").json()
        assert job["status"] == "failed"
        assert job["error"]["code"] == "source_extraction_failed"
        assert "outage" in (job["error"]["message"] or "").lower()
        if payload.get("area_id"):
            assert test_client.get(f"/api/v1/typed/areas/{payload['area_id']}").status_code == 404


def test_planetary_cache_resume_and_idempotency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANALOG_AOI_RUNTIME_DIR", str(tmp_path / "aoi-runtime"))
    # Async runner to exercise queued/running states and recover().
    app = create_app(_settings(tmp_path, inline=False))
    with TestClient(app) as test_client:
        first = _create_planetary(
            test_client,
            "Moon",
            MOON_SW_CELL,
            idempotency_key="goal170-moon-resume",
        )
        assert first.status_code in {200, 202}, first.text
        first_body = first.json()
        job_id = first_body["job_id"]
        job = _wait_terminal(test_client, job_id)
        assert job["status"] in {"completed", "partial"}, job
        assert job["error"] is None

        # Idempotency key returns same durable job/result.
        repeat = _create_planetary(
            test_client,
            "Moon",
            MOON_SW_CELL,
            idempotency_key="goal170-moon-resume",
        )
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["cached"] is True
        assert repeat.json()["job_id"] == job_id
        assert repeat.json()["area_id"] == job["area_id"]

        # Same AOI without key reuses cache.
        no_key = _create_planetary(test_client, "Moon", MOON_SW_CELL)
        assert no_key.status_code == 200, no_key.text
        assert no_key.json()["cached"] is True
        assert no_key.json()["job_id"] == job_id

    # Simulate restart: running job should be recovered and completed.
    with app.state.session_factory() as session:
        from analog_api.adapters.db import AnalysisJob

        recovered_job = session.get(AnalysisJob, job_id)
        assert recovered_job is not None
        recovered_job.status = "running"
        session.commit()

    runner = JobRunner(
        session_factory=app.state.session_factory,
        processor=app.state.planetary_aoi_provider,
        inline=True,
    )
    assert runner.recover() >= 1
    with app.state.session_factory() as session:
        from analog_api.adapters.db import AnalysisJob

        recovered_job = session.get(AnalysisJob, job_id)
        assert recovered_job is not None
        assert recovered_job.status in {"completed", "partial"}


def test_openapi_and_types_contract_compatibility_170(client: TestClient):
    """API/OpenAPI compatibility for #132/#160 surfaces consumed by this integration."""
    schema = client.get("/openapi.json")
    assert schema.status_code == 200, schema.text
    spec = schema.json()
    paths = spec.get("paths", {})
    for path in (
        "/api/v1/planetary/areas/analyze",
        "/api/v1/jobs/{job_id}",
        "/api/v1/typed/areas/{area_id}",
        "/api/v1/planetary/map-features",
        "/api/v1/earth/map-features",
    ):
        assert path in paths, f"missing OpenAPI path {path}"

    analyze_op = paths["/api/v1/planetary/areas/analyze"]["post"]
    tags = [str(t).lower() for t in analyze_op.get("tags", [])]
    assert any("planetary" in tag for tag in tags), tags
    req_ref = (
        analyze_op.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    assert "PlanetaryAoiAnalyzeRequest" in json.dumps(req_ref)
    resp_schema = json.dumps(analyze_op.get("responses", {}))
    assert "AoiAnalyzeResponse" in resp_schema

    schemas = spec.get("components", {}).get("schemas", {})
    for name in (
        "PlanetaryAoiAnalyzeRequest",
        "AoiAnalyzeResponse",
        "JobOut",
        "GeoBBoxIn",
        "ErrorOut",
    ):
        assert name in schemas, f"missing schema {name}"
    planetary_req = schemas["PlanetaryAoiAnalyzeRequest"]
    props = planetary_req.get("properties", {})
    assert set(props).issuperset({"body", "bbox", "requested_features", "idempotency_key"})
    assert props["body"]["enum"] == ["Moon", "Mars"]

    # Committed schema file must stay aligned with generated frontend types contract.
    committed_openapi = json.loads((ROOT / "contracts/openapi.json").read_text())
    assert "/api/v1/planetary/areas/analyze" in committed_openapi.get("paths", {})
    assert "PlanetaryAoiAnalyzeRequest" in committed_openapi.get("components", {}).get(
        "schemas", {}
    )

    schema_dts = (ROOT / "apps/web/src/api/schema.d.ts").read_text()
    assert "/api/v1/planetary/areas/analyze" in schema_dts
    assert "PlanetaryAoiAnalyzeRequest" in schema_dts
    assert "AoiAnalyzeResponse" in schema_dts
    assert "requested_features" in schema_dts  # planetary D01 feature selector surface
    assert "JobOut" in schema_dts

    # Live response shape sanity for frontend handoff.
    created = _assert_planetary_success(
        client, "Moon", MOON_NE_CELL, expected_frame="lunar_planetocentric"
    )
    for key in (
        "job_id",
        "status",
        "area_id",
        "cache_key",
        "cached",
        "body",
        "coordinate_frame",
        "method_version",
        "provider_status",
        "result",
        "links",
        "dimension_support",
        "coverage_note",
    ):
        assert key in created, key
