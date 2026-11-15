"""Goal H (#170) planetary AOI end-to-end QA (integration-owned).

Reproducible offline evidence for Moon/Mars on-demand AOI using only committed manifests/
fixtures and isolated temporary databases. No network access, no raw raster downloads, no
synthetic "observed" values, and no claim of global or exhaustive six-dimension coverage.

Committed pilot coverage (real, analysis-eligible D01 coarse terrain only):
- Moon: LOLA128 committed pilot window `moon-lola128-eqc-window-01` (~20E, ~10S, low latitude).
  4 cells, source `moon-lola128-pa-pixel-202405`, fixtures under `samples/planetary/coarse/fixtures/`.
- Mars: CTX Jezero committed pilot window `mars-ctx-jezero-window-01` (~78.17E, ~18.56N,
  low/mid northern latitude). 4 comparable cells, source `mars2020-jezero-ctx-dem-20m`.
  MOLA cells in the same area are evidence-only and never merged into comparable analysis.

Because committed offline coverage is bounded to these pilot windows, successful real D01
analyses cannot be produced for arbitrary mid/high-latitude, polar, or antimeridian locations
in this environment. Those required cases are exercised as explicit, non-fabricated outcomes
(out-of-coverage/nodata-equivalent, validation, or explicit failure) with no persisted fake
typed area and no invented values.
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

# Committed Moon LOLA128 pilot cells (pipelines/planetary/coarse/moon_lola128_window01.json).
MOON_SW_CELL = {"west": 19.837481, "south": -10.159889, "east": 19.995, "north": -10.004958}
MOON_NE_CELL = {"west": 20.005, "south": -9.995, "east": 20.162433, "north": -9.840069}
MOON_FULL_BLOCK = {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85}

# Committed Mars Jezero CTX pilot cells (pipelines/planetary/coarse/mars_ctx_jezero_window01.json).
MARS_SW_CELL = {"west": 78.087409, "south": 18.478312, "east": 78.166341, "north": 18.552665}
MARS_NE_CELL = {"west": 78.176341, "south": 18.562665, "east": 78.255273, "north": 18.637018}
MARS_FULL_BLOCK = {"west": 78.08, "south": 18.47, "east": 78.27, "north": 18.65}

# Required-case AOIs outside committed pilot coverage (explicit non-fabricated outcomes only).
# Moon committed pilot is low latitude (~10S); mid/high-lat requests below are out of coverage.
MOON_MIDLAT_OUTSIDE = {"west": 30.0, "south": 44.95, "east": 30.1, "north": 45.05}
MOON_HIGHLAT_OUTSIDE = {"west": 120.0, "south": 59.95, "east": 120.1, "north": 60.05}
MOON_POLAR = {"west": -10.0, "south": 89.5, "east": 10.0, "north": 89.55}
MOON_DATELINE_CROSSING = {"west": 179.9, "south": -0.05, "east": -179.9, "north": 0.05}
MOON_NODATA_EQUIVALENT = {"west": 0.0, "south": -5.0, "east": 0.05, "north": -4.95}

# Mars committed pilot is low/mid latitude (~18.5N); low-south/high-lat requests below are out of coverage.
MARS_LOWLAT_OUTSIDE = {"west": 150.0, "south": -0.05, "east": 150.05, "north": 0.0}
MARS_HIGHLAT_OUTSIDE = {"west": -60.0, "south": 59.95, "east": -59.9, "north": 60.05}
MARS_POLAR = {"west": -10.0, "south": 89.5, "east": 10.0, "north": 89.55}
MARS_DATELINE_CROSSING = {"west": 179.9, "south": 18.5, "east": -179.9, "north": 18.6}
MARS_NODATA_EQUIVALENT = {"west": 0.0, "south": 0.0, "east": 0.05, "north": 0.05}

PLANETARY_AOI_METHOD = "planetary-terrain-horn-250m-aoi-1.0.0"
PLANETARY_SOURCE_METHOD = "coarse-planetary-dem-horn-1.0.0"
D01_FEATURES = [
    "coarse_slope_mean_deg",
    "coarse_slope_p90_deg",
    "coarse_flat_fraction_lt5deg",
]
DIMENSIONS = {f"D0{i}" for i in range(1, 7)}
COVERAGE_CODES = {
    "out_of_coverage",
    "outside_coverage",
    "aoi_too_large",
    "invalid_bbox",
    "bbox_too_wide",
    "validation_error",
    "unsupported_body",
    "unsupported_feature_on_body",
    "source_extraction_failed",
}


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

    cached = _create_planetary(client, body, bbox)
    assert cached.status_code == 200, cached.text
    assert cached.json()["cached"] is True
    assert cached.json()["job_id"] == created["job_id"]
    return created


def _assert_explicit_non_fabricated_failure(
    client: TestClient, body: str, bbox: dict[str, float], *, allowed_codes: set[str] | None = None
) -> dict:
    """Required-case evidence when real analysis cannot succeed: explicit and non-fabricated."""
    response = _create_planetary(client, body, bbox)
    assert response.status_code != 404, response.text
    failed_area_id = None
    observed: dict[str, object] = {
        "http_status": response.status_code,
        "body": response.json() if response.headers.get("content-type", "").startswith("application/json") else {},
    }
    if response.status_code in {200, 202}:
        payload = response.json()
        assert payload["status"] == "failed", payload
        assert payload["result"] in (None, {}), payload
        status_blob = json.dumps(payload.get("provider_status") or {}).lower()
        err = payload.get("error") or {}
        err_blob = json.dumps(err).lower()
        assert (
            "coverage" in status_blob
            or "out_of" in status_blob
            or "nodata" in status_blob
            or "no_data" in status_blob
            or "coverage" in err_blob
        ), payload
        failed_area_id = payload.get("area_id")
    else:
        assert response.status_code in {422, 503}, response.text
        err = response.json().get("error", {}) or {}
        code = str(err.get("code", "")).lower()
        codes = allowed_codes or COVERAGE_CODES
        assert code in codes or any(token in code for token in ("coverage", "outside", "nodata", "source")), (
            code,
            response.text,
        )
    if failed_area_id:
        detail = client.get(f"/api/v1/typed/areas/{failed_area_id}")
        assert detail.status_code == 404, detail.text
    return observed


@pytest.mark.parametrize(
    ("case_id", "label", "bbox", "lat_band_note"),
    [
        ("moon_committed_real_1", "moon_sw_cell_low_lat", MOON_SW_CELL, "low latitude (~10S), committed pilot"),
        ("moon_committed_real_2", "moon_ne_cell_low_lat", MOON_NE_CELL, "low latitude (~10S), committed pilot"),
        ("moon_committed_real_3", "moon_full_block_low_lat", MOON_FULL_BLOCK, "low latitude (~10S), committed pilot"),
    ],
)
def test_moon_real_aoi_e2e_three_committed_cases(
    client: TestClient, case_id: str, label: str, bbox: dict[str, float], lat_band_note: str
):
    """Three real Moon AOIs from committed LOLA128 pilot (successful D01 analysis evidence)."""
    created = _assert_planetary_success(client, "Moon", bbox, expected_frame="lunar_planetocentric")
    assert created["provider_status"], (case_id, label, lat_band_note)
    assert all(
        status.get("network_used") is False
        for status in created["provider_status"].values()
        if isinstance(status, dict)
    ), created["provider_status"]


@pytest.mark.parametrize(
    ("case_id", "label", "bbox", "lat_band_note"),
    [
        ("mars_committed_real_1", "mars_sw_cell_low_mid_lat", MARS_SW_CELL, "low/mid latitude (~18.5N), committed pilot"),
        ("mars_committed_real_2", "mars_ne_cell_low_mid_lat", MARS_NE_CELL, "low/mid latitude (~18.6N), committed pilot"),
        ("mars_committed_real_3", "mars_full_block_low_mid_lat", MARS_FULL_BLOCK, "low/mid latitude (~18.5N), committed pilot"),
    ],
)
def test_mars_real_aoi_e2e_three_committed_cases(
    client: TestClient, case_id: str, label: str, bbox: dict[str, float], lat_band_note: str
):
    """Three real Mars AOIs from committed Jezero CTX pilot (successful D01 analysis evidence)."""
    created = _assert_planetary_success(client, "Mars", bbox, expected_frame="mars_planetocentric")
    assert created["provider_status"], (case_id, label, lat_band_note)


@pytest.mark.parametrize(
    ("case_id", "body", "bbox", "note"),
    [
        ("moon_nodata_out_of_coverage", "Moon", MOON_NODATA_EQUIVALENT, "nodata/out-of-coverage outside committed pilots"),
        ("moon_midlat_outside_committed", "Moon", MOON_MIDLAT_OUTSIDE, "mid-latitude request; committed Moon pilot is low-lat (~10S) only"),
        ("moon_highlat_outside_committed", "Moon", MOON_HIGHLAT_OUTSIDE, "high-latitude request; outside committed pilot coverage"),
        ("mars_nodata_out_of_coverage", "Mars", MARS_NODATA_EQUIVALENT, "nodata/out-of-coverage outside committed pilots"),
        ("mars_lowlat_outside_committed", "Mars", MARS_LOWLAT_OUTSIDE, "low-latitude request outside committed Mars pilot (~18.5N)"),
        ("mars_highlat_outside_committed", "Mars", MARS_HIGHLAT_OUTSIDE, "high-latitude request; outside committed pilot coverage"),
    ],
)
def test_planetary_required_nodata_and_lat_band_explicit_non_fabricated(
    client: TestClient, case_id: str, body: str, bbox: dict[str, float], note: str
):
    """Required nodata/lat-band cases: explicit, non-fabricated outcomes per committed coverage."""
    _assert_explicit_non_fabricated_failure(client, body, bbox)


@pytest.mark.parametrize(
    ("case_id", "body", "bbox"),
    [
        ("moon_polar", "Moon", MOON_POLAR),
        ("mars_polar", "Mars", MARS_POLAR),
    ],
)
def test_planetary_required_polar_explicit_non_fabricated(
    client: TestClient, case_id: str, body: str, bbox: dict[str, float]
):
    """Polar AOIs: explicit outcome; outside committed pilot coverage => no fabricated values."""
    _assert_explicit_non_fabricated_failure(client, body, bbox)


@pytest.mark.parametrize(
    ("case_id", "body", "bbox"),
    [
        ("moon_dateline_crossing", "Moon", MOON_DATELINE_CROSSING),
        ("mars_dateline_crossing", "Mars", MARS_DATELINE_CROSSING),
    ],
)
def test_planetary_required_dateline_crossing_explicit_non_fabricated(
    client: TestClient, case_id: str, body: str, bbox: dict[str, float]
):
    """Dateline/antimeridian-crossing AOIs: explicit outcome, never silent acceptance/fabrication."""
    _assert_explicit_non_fabricated_failure(client, body, bbox)


@pytest.mark.parametrize("body", ["Moon", "Mars"])
def test_planetary_required_oversized_aoi_rejected(client: TestClient, body: str):
    """Oversized AOI must be rejected explicitly (bounded analysis cap)."""
    response = _create_planetary(
        client, body, {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0}
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "aoi_too_large"


@pytest.mark.parametrize("body", ["Moon", "Mars"])
def test_planetary_required_source_outage_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
):
    """Source/provider outage simulation: explicit failed job, no fake success or persisted area."""
    monkeypatch.setenv("ANALOG_AOI_RUNTIME_DIR", str(tmp_path / "aoi-runtime"))

    def _boom(self, request):  # noqa: ANN001, ARG001
        raise AppError(
            "source_extraction_failed",
            f"Simulated {body} source outage while reading committed fixtures.",
            status_code=503,
        )

    app = create_app(_settings(tmp_path))
    monkeypatch.setattr(type(app.state.planetary_aoi_provider), "analyze", _boom)
    bbox = MOON_FULL_BLOCK if body == "Moon" else MARS_FULL_BLOCK
    with TestClient(app) as test_client:
        response = _create_planetary(test_client, body, bbox)
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

        no_key = _create_planetary(test_client, "Moon", MOON_SW_CELL)
        assert no_key.status_code == 200, no_key.text
        assert no_key.json()["cached"] is True
        assert no_key.json()["job_id"] == job_id

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
    """API/OpenAPI/TS compatibility for #132/#160 surfaces consumed by this integration."""
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
    for name in ("PlanetaryAoiAnalyzeRequest", "AoiAnalyzeResponse", "JobOut", "GeoBBoxIn", "ErrorOut"):
        assert name in schemas, f"missing schema {name}"
    planetary_req = schemas["PlanetaryAoiAnalyzeRequest"]
    props = planetary_req.get("properties", {})
    assert set(props).issuperset({"body", "bbox", "requested_features", "idempotency_key"})
    assert props["body"]["enum"] == ["Moon", "Mars"]

    committed_openapi = json.loads((ROOT / "contracts/openapi.json").read_text())
    assert "/api/v1/planetary/areas/analyze" in committed_openapi.get("paths", {})
    assert "PlanetaryAoiAnalyzeRequest" in committed_openapi.get("components", {}).get("schemas", {})

    schema_dts = (ROOT / "apps/web/src/api/schema.d.ts").read_text()
    assert "/api/v1/planetary/areas/analyze" in schema_dts
    assert "PlanetaryAoiAnalyzeRequest" in schema_dts
    assert "AoiAnalyzeResponse" in schema_dts
    assert "requested_features" in schema_dts
    assert "JobOut" in schema_dts

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
