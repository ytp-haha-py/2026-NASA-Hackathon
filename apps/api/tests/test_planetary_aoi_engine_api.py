"""Engine-shaped planetary AOI lifecycle tests (Issue #170, Goal D).

These tests cover the additive ``/api/v1/planetary/aoi`` routes:

* create/status/result lifecycle with durable persistence and cache reuse;
* full-AOI reporting when an injected Goal A/B style provider covers the request;
* explicit D02-D06 ``no_coverage``/``not_indexed``/``unsupported`` statuses;
* explicit failure/not-ready responses instead of fabricated values.

Run with:

    uv run pytest apps/api/tests/test_planetary_aoi_engine_api.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from analog_api.adapters.db import AnalysisJob
from analog_api.config import Settings
from analog_api.domain.aoi import AoiResult, ValidatedAoi
from analog_api.domain.entities import BBox, DatasetEntity
from analog_api.domain.errors import AppError
from analog_api.domain.planetary_aoi_limits import (
    EARTH_PROVIDER_LIMITS,
    PLANETARY_PROVIDER_LIMITS,
    validate_provider_geometry,
)
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]

ENGINE_CREATE_PATH = "/api/v1/planetary/aoi"
MOON_BBOX = {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85}
MOON_SMALL_BBOX = {"west": 19.86, "south": -10.14, "east": 20.14, "north": -9.86}
OUT_OF_COVERAGE_BBOX = {"west": 0.0, "south": -5.0, "east": 0.05, "north": -4.95}

DIMENSION_CODES = ("D01", "D02", "D03", "D04", "D05", "D06")
EXPLICIT_UNAVAILABLE = {"no_coverage", "not_indexed", "unsupported"}

FAKE_DATASET = DatasetEntity(
    dataset_id="fake-full-aoi-lola",
    provider="Test full-AOI provider",
    product_id="fake-lola-global-v1",
    title="Synthetic full-AOI planetary DEM used only in tests",
    url="https://example.invalid/fake-lola.tif",
    license="Test licence",
    license_url="https://example.invalid/licence",
    access_timestamp="2026-11-15T00:00:00Z",
    source_crs="MOON_PA (DE421), body-fixed",
    native_resolution_m=236.9,
    processing_version="coarse-planetary-dem-horn-1.0.0",
    checksum=None,
    source_kind="planetary_dem",
    notes="Synthetic provider used only for route-contract tests.",
)


class FakeFullAoiProvider:
    """Goal A/B style provider stub: analyses the complete bounded request bbox."""

    def __init__(self) -> None:
        self.validate_calls: list[dict[str, Any]] = []
        self.analyze_calls: list[str] = []

    def validate(
        self,
        *,
        body: str,
        bbox: dict[str, float],
        requested_features: list[str] | None,
        idempotency_key: str | None,
    ) -> ValidatedAoi:
        values = {key: float(bbox[key]) for key in ("west", "south", "east", "north")}
        self.validate_calls.append({"body": body, "bbox": values})
        digest = hashlib.sha256(
            json.dumps({"body": body, "bbox": values}, sort_keys=True).encode()
        ).hexdigest()[:12]
        cache_key = hashlib.sha256(
            json.dumps(
                {"body": body, "bbox": values, "features": requested_features},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return ValidatedAoi(
            body=body,
            bbox=BBox(**values),
            geometry=None,
            area_km2=100.0,
            requested_features=tuple(
                requested_features
                or (
                    "coarse_slope_mean_deg",
                    "coarse_slope_p90_deg",
                    "coarse_flat_fraction_lt5deg",
                )
            ),
            reference_id=None,
            idempotency_key=idempotency_key,
            area_id=f"planet-aoi-{body.lower()}-{digest}",
            cache_key=cache_key,
            width_m=10_000.0,
            height_m=10_000.0,
        )

    def analyze(self, request: ValidatedAoi) -> AoiResult:
        self.analyze_calls.append(request.area_id)
        return AoiResult(
            area_id=request.area_id,
            bbox=request.bbox,
            geometry=None,
            area_km2=request.area_km2,
            center_lon=(request.bbox.west + request.bbox.east) / 2.0,
            center_lat=(request.bbox.south + request.bbox.north) / 2.0,
            crs="+proj=longlat +R=1737400 +no_defs",
            valid_fraction=0.97,
            window_size_m=request.width_m,
            resolution_m=250.0,
            features={
                "coarse_slope_mean_deg": 12.5,
                "coarse_slope_p90_deg": 30.1,
                "coarse_flat_fraction_lt5deg": 0.25,
            },
            context_observations={},
            datasets=(FAKE_DATASET,),
            provider_status={
                FAKE_DATASET.dataset_id: {
                    "status": "verified",
                    "network_used": False,
                    "full_aoi_read": True,
                }
            },
            quality_flags=("on_demand_planetary_aoi", "not_a_cross_body_score"),
            warnings=("Synthetic route-contract provider; not a real lunar measurement.",),
            checksum="sha256:fake-full-aoi",
            body=request.body,
            coordinate_frame=(
                "lunar_planetocentric" if request.body == "Moon" else "mars_planetocentric"
            ),
            method_version="fake-full-aoi-1.0.0",
            dimension_support={
                "D01": {
                    "status": "present",
                    "method_version": "fake-full-aoi-1.0.0",
                    "source_method_version": "coarse-planetary-dem-horn-1.0.0",
                    "cell_count": 1,
                    "feature_count": 3,
                },
                "D02": {
                    "status": "no_coverage",
                    "reason": "The fake provider has no D02 footprint over this AOI.",
                },
            },
            source_assets=(
                {
                    "uri": "fixtures/fake-lola-window.tif",
                    "sha256": "0123456789abcdef",
                    "bytes": 4096,
                },
            ),
            coverage_note="Synthetic full-AOI provider covered the complete requested bbox.",
            analysis_kind="full_aoi_fake_engine",
        )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'engine.db'}",
        manifest_dir=str(tmp_path / "none-earth"),
        manifest_dir_planetary=str(tmp_path / "none-planetary"),
        manifest_dir_context=str(tmp_path / "none-context"),
        seed_include_synthetic=False,
        seed_include_typed=False,
        aoi_inline=True,
        planetary_allow_network=False,
    )


def _make_client(tmp_path: Path, *, planetary_processor=None) -> TestClient:
    runtime = tmp_path / "aoi-runtime"
    os.environ["ANALOG_AOI_RUNTIME_DIR"] = str(runtime)
    return TestClient(create_app(_settings(tmp_path), planetary_processor=planetary_processor))


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    previous = os.environ.get("ANALOG_AOI_RUNTIME_DIR")
    tmp = tmp_path_factory.mktemp("planetary-engine")
    test_client = _make_client(tmp)
    try:
        with test_client:
            yield test_client
    finally:
        if previous is None:
            os.environ.pop("ANALOG_AOI_RUNTIME_DIR", None)
        else:
            os.environ["ANALOG_AOI_RUNTIME_DIR"] = previous


@pytest.fixture(scope="module")
def full_aoi_client(tmp_path_factory: pytest.TempPathFactory):
    previous = os.environ.get("ANALOG_AOI_RUNTIME_DIR")
    tmp = tmp_path_factory.mktemp("planetary-engine-full")
    provider = FakeFullAoiProvider()
    test_client = _make_client(tmp, planetary_processor=provider)
    test_client.fake_full_aoi_provider = provider  # type: ignore[attr-defined]
    try:
        with test_client:
            yield test_client
    finally:
        if previous is None:
            os.environ.pop("ANALOG_AOI_RUNTIME_DIR", None)
        else:
            os.environ["ANALOG_AOI_RUNTIME_DIR"] = previous


def _assert_engine_contract(payload: dict[str, Any]) -> None:
    for key in (
        "contract_version",
        "job_id",
        "status",
        "area_id",
        "cache_key",
        "cached",
        "body",
        "coordinate_frame",
        "requested_bbox",
        "analysis_bbox",
        "full_aoi",
        "analysis_scope",
        "coverage",
        "dimensions",
        "dimension_status",
        "provenance",
        "support",
        "provider_status",
        "warnings",
        "result",
        "links",
    ):
        assert key in payload, f"engine response is missing {key!r}: {sorted(payload)}"
    assert set(payload["dimensions"]) == set(DIMENSION_CODES)
    assert set(payload["dimension_status"]) == set(DIMENSION_CODES)


def _assert_bbox_close(
    actual: dict[str, float] | None, expected: dict[str, float], *, tolerance: float = 1e-9
) -> None:
    assert actual is not None, expected
    for key in ("west", "south", "east", "north"):
        assert actual[key] == pytest.approx(expected[key], abs=tolerance), (key, actual)


def test_create_status_result_lifecycle_and_persistence(client: TestClient):
    response = client.post(
        ENGINE_CREATE_PATH,
        json={"body": "Moon", "bbox": MOON_BBOX, "idempotency_key": "engine-moon-lifecycle"},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    _assert_engine_contract(created)

    assert created["contract_version"] == "planetary-aoi-engine-1.0.0"
    assert created["status"] in {"completed", "partial"}, created
    assert created["cached"] is False
    assert created["body"] == "Moon"
    assert created["coordinate_frame"] == "lunar_planetocentric"
    assert created["area_id"].startswith("planet-aoi-moon-")
    assert created["links"]["job"] == f"/api/v1/planetary/aoi/{created['job_id']}"
    assert created["links"]["result"] == (f"/api/v1/planetary/aoi/{created['job_id']}/result")
    assert created["links"]["shared_job"] == f"/api/v1/jobs/{created['job_id']}"
    assert created["links"]["typed_area"] == f"/api/v1/typed/areas/{created['area_id']}"

    # The committed-fixture fallback cannot claim the whole requested envelope.
    assert created["full_aoi"] is False, created["coverage"]
    assert created["analysis_scope"] == "selected_cell_union"
    assert created["coverage"]["status"] == "partial_aoi"
    _assert_bbox_close(created["requested_bbox"], MOON_BBOX)
    assert created["analysis_bbox"] is not None
    assert created["analysis_bbox"] != created["requested_bbox"]

    dimensions = created["dimensions"]
    d01 = dimensions["D01"]
    assert d01["status"] == "available"
    assert d01["value_origin"] == "observed_derived"
    assert {feature["feature_id"] for feature in d01["features"]} == {
        "coarse_slope_mean_deg",
        "coarse_slope_p90_deg",
        "coarse_flat_fraction_lt5deg",
    }
    assert all(feature["value"] is not None for feature in d01["features"])
    assert d01["provenance"]["dataset_ids"], d01["provenance"]
    assert d01["provenance"]["method_version"]
    assert d01["support"]["analysis_bbox"] == created["analysis_bbox"]
    assert created["support"]["kind"] == "selected_cell_union"

    for code in ("D02", "D03", "D04", "D05", "D06"):
        entry = dimensions[code]
        assert entry["status"] in EXPLICIT_UNAVAILABLE, entry
        assert entry["features"] == []
        assert entry["reason"]
        assert entry["provenance"]["declared_sources"], entry["provenance"]
        assert entry["support"]["kind"] == "declared_source_inventory"

    status = client.get(f"/api/v1/planetary/aoi/{created['job_id']}")
    assert status.status_code == 200, status.text
    assert status.headers["cache-control"] == "no-store"
    current = status.json()
    _assert_engine_contract(current)
    assert current["job_id"] == created["job_id"]
    assert current["area_id"] == created["area_id"]
    assert current["status"] == created["status"]
    assert current["cached"] is True
    assert current["dimensions"]["D01"]["status"] == "available"

    result = client.get(f"/api/v1/planetary/aoi/{created['job_id']}/result")
    assert result.status_code == 200, result.text
    persisted = result.json()
    _assert_engine_contract(persisted)
    assert persisted["job_id"] == created["job_id"]
    assert persisted["provenance"]["dataset_ids"] == d01["provenance"]["dataset_ids"]
    assert persisted["result"] is not None

    typed = client.get(f"/api/v1/typed/areas/{created['area_id']}")
    assert typed.status_code == 200, typed.text
    typed_body = typed.json()
    assert typed_body["body"] == "Moon"
    assert typed_body["coordinate_frame"] == "lunar_planetocentric"
    assert "EPSG:4326" not in str(typed_body)
    d01_observations = [row for row in typed_body["observations"] if row["dimension_code"] == "D01"]
    assert len(d01_observations) == 3


def test_same_cache_key_reuses_job(client: TestClient):
    payload = {
        "body": "Moon",
        "bbox": MOON_SMALL_BBOX,
        "requested_features": ["coarse_slope_mean_deg"],
        "idempotency_key": "engine-moon-cache",
    }
    first = client.post(ENGINE_CREATE_PATH, json=payload)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["cached"] is False
    assert first_body["dimensions"]["D01"]["status"] == "available"

    second = client.post(ENGINE_CREATE_PATH, json=payload)
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["cached"] is True
    assert second_body["job_id"] == first_body["job_id"]
    assert second_body["area_id"] == first_body["area_id"]
    assert second_body["cache_key"] == first_body["cache_key"]


def test_out_of_coverage_is_explicit_and_result_is_not_ready(client: TestClient):
    response = client.post(
        ENGINE_CREATE_PATH,
        json={"body": "Moon", "bbox": OUT_OF_COVERAGE_BBOX},
    )
    assert response.status_code == 200, response.text
    failed = response.json()
    _assert_engine_contract(failed)
    assert failed["status"] == "failed", failed
    assert failed["result"] is None
    assert failed["full_aoi"] is None
    assert failed["analysis_scope"] == "failed"
    assert failed["coverage"]["status"] == "no_coverage"
    assert failed["dimensions"]["D01"]["status"] == "no_coverage"
    assert failed["dimensions"]["D01"]["features"] == []
    assert failed["provider_status"]["analysis"]["reason"] == "out_of_coverage"

    result = client.get(f"/api/v1/planetary/aoi/{failed['job_id']}/result")
    assert result.status_code == 409, result.text
    error = result.json()["error"]
    assert error["code"] == "job_failed"
    assert error["details"][0]["job_id"] == failed["job_id"]

    typed = client.get(f"/api/v1/typed/areas/{failed['area_id']}")
    assert typed.status_code == 404, typed.text


def test_queued_job_reports_pending_dimensions_and_result_409(client: TestClient):
    request_payload = {
        "body": "Moon",
        "bbox": {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85},
        "coordinate_frame": "lunar_planetocentric",
        "area_id": "planet-aoi-moon-queued",
    }
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        session.add(
            AnalysisJob(
                job_id="queued-engine-job",
                status="queued",
                cache_key="queued-engine-cache",
                request_json=request_payload,
                provider_status={},
            )
        )
        session.commit()

    status = client.get("/api/v1/planetary/aoi/queued-engine-job")
    assert status.status_code == 200, status.text
    payload = status.json()
    _assert_engine_contract(payload)
    assert payload["status"] == "queued"
    assert payload["full_aoi"] is None
    assert payload["analysis_scope"] == "pending"
    assert payload["coverage"]["status"] == "pending"
    assert payload["dimensions"]["D01"]["status"] == "pending"
    assert payload["dimensions"]["D01"]["features"] == []
    assert payload["result"] is None
    for code in ("D02", "D03", "D04", "D05", "D06"):
        assert payload["dimensions"][code]["status"] in EXPLICIT_UNAVAILABLE

    result = client.get("/api/v1/planetary/aoi/queued-engine-job/result")
    assert result.status_code == 409, result.text
    assert result.json()["error"]["code"] == "job_not_ready"


def test_unknown_and_earth_jobs_are_not_found(client: TestClient):
    missing = client.get("/api/v1/planetary/aoi/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        session.add(
            AnalysisJob(
                job_id="earth-engine-job",
                status="completed",
                cache_key="earth-engine-cache",
                request_json={"body": "Earth", "bbox": MOON_BBOX},
                provider_status={},
                result_json={},
            )
        )
        session.commit()
    earth = client.get("/api/v1/planetary/aoi/earth-engine-job")
    assert earth.status_code == 404
    assert earth.json()["error"]["code"] == "not_found"


def test_validation_is_explicit_before_a_job_exists(client: TestClient):
    earth = client.post(
        ENGINE_CREATE_PATH,
        json={"body": "Earth", "bbox": MOON_BBOX},
    )
    assert earth.status_code == 422

    unsupported = client.post(
        ENGINE_CREATE_PATH,
        json={
            "body": "Moon",
            "bbox": MOON_BBOX,
            "requested_features": ["soil_organic_carbon_0_5cm"],
        },
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "unsupported_feature_on_body"


def test_legacy_132_job_is_readable_through_engine_status(client: TestClient):
    """The new status route must not break jobs created by the #132 route."""
    legacy_bbox = {"west": 19.87, "south": -10.13, "east": 20.13, "north": -9.87}
    created = client.post(
        "/api/v1/planetary/areas/analyze",
        json={
            "body": "Moon",
            "bbox": legacy_bbox,
            "requested_features": ["coarse_slope_mean_deg"],
            "idempotency_key": "engine-legacy-132",
        },
    )
    assert created.status_code == 200, created.text
    legacy = created.json()
    assert legacy["status"] in {"completed", "partial"}

    status = client.get(f"/api/v1/planetary/aoi/{legacy['job_id']}")
    assert status.status_code == 200, status.text
    payload = status.json()
    _assert_engine_contract(payload)
    assert payload["job_id"] == legacy["job_id"]
    assert payload["area_id"] == legacy["area_id"]
    assert payload["dimensions"]["D01"]["status"] == "available"
    assert payload["coordinate_frame"] == "lunar_planetocentric"


def test_antimeridian_request_keeps_two_pieces_and_is_not_recentered(client: TestClient):
    bbox = {"west": 179.95, "south": -0.05, "east": -179.95, "north": 0.05}
    response = client.post(
        ENGINE_CREATE_PATH,
        json={"body": "Moon", "bbox": bbox, "idempotency_key": "engine-antimeridian"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    _assert_engine_contract(payload)
    # There is no committed lunar coverage at 180 E, so the honest outcome is an
    # explicit out_of_coverage failure rather than a wrapped/shrunk Earth-like bbox.
    assert payload["status"] == "failed", payload
    assert payload["coverage"]["status"] == "no_coverage"
    support = payload["support"]
    assert support["crosses_antimeridian"] is True
    assert support["shrink_applied"] is False
    assert support["analysis_extent"] == "full_requested_aoi"
    pieces = support["bbox_pieces"]
    assert len(pieces) == 2, pieces
    assert pieces[0]["west"] == pytest.approx(179.95)
    assert pieces[0]["east"] == pytest.approx(180.0)
    assert pieces[1]["west"] == pytest.approx(-180.0)
    assert pieces[1]["east"] == pytest.approx(-179.95)
    _assert_bbox_close(payload["requested_bbox"], bbox)


def test_oversized_request_is_rejected_not_silently_shrunk(client: TestClient):
    # 0.5 degrees of lunar longitude is ~15.2 km, above the explicit 12 km cap.
    oversized = {"west": 19.5, "south": -10.05, "east": 20.0, "north": -9.85}
    response = client.post(
        ENGINE_CREATE_PATH,
        json={"body": "Moon", "bbox": oversized},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "aoi_too_large", error
    assert "not shrunk" in error["message"].lower() or "exceeds" in error["message"].lower()

    details = error.get("details") or []
    assert details, error
    assert details[0]["field"] in {"area_km2", "max_side_m"}, details


def test_invalid_latitude_convention_is_explicit(client: TestClient):
    response = client.post(
        ENGINE_CREATE_PATH,
        json={
            "body": "Moon",
            "bbox": MOON_BBOX,
            "latitude_kind": "planetographic",
        },
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_latitude_convention", error
    assert "planetocentric" in error["message"].lower()


def test_provider_limits_match_legacy_earth_and_planetary_contracts():
    assert EARTH_PROVIDER_LIMITS.min_side_m == 600.0
    assert EARTH_PROVIDER_LIMITS.max_side_m == 2_000.0
    assert EARTH_PROVIDER_LIMITS.max_area_km2 == 4.0
    assert PLANETARY_PROVIDER_LIMITS.min_side_m == 1_000.0
    assert PLANETARY_PROVIDER_LIMITS.max_side_m == 12_000.0
    assert PLANETARY_PROVIDER_LIMITS.max_area_km2 == 144.0

    earth_ok = validate_provider_geometry(
        "Earth", {"west": 0.0, "south": 0.0, "east": 0.01, "north": 0.01}
    )
    assert earth_ok.limits.max_side_m == 2_000.0
    assert earth_ok.latitude_kind == "geodetic"
    assert earth_ok.analysis_extent == "full_requested_aoi"
    assert earth_ok.shrink_applied is False

    with pytest.raises(AppError) as earth_error:
        validate_provider_geometry(
            "Earth", {"west": 0.0, "south": 0.0, "east": 0.03, "north": 0.03}
        )
    assert earth_error.value.code == "aoi_too_large"

    moon_ok = validate_provider_geometry("Moon", MOON_BBOX)
    assert moon_ok.limits.max_side_m == 12_000.0
    assert moon_ok.latitude_kind == "planetocentric"
    assert moon_ok.analysis_extent == "full_requested_aoi"
    assert moon_ok.shrink_applied is False

    with pytest.raises(AppError) as moon_error:
        validate_provider_geometry(
            "Moon", {"west": 19.5, "south": -10.05, "east": 20.0, "north": -9.85}
        )
    assert moon_error.value.code == "aoi_too_large"


def test_injected_full_aoi_provider_reports_complete_coverage(full_aoi_client: TestClient):
    response = full_aoi_client.post(
        ENGINE_CREATE_PATH,
        json={"body": "Moon", "bbox": MOON_BBOX, "idempotency_key": "engine-full-aoi"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    _assert_engine_contract(payload)

    assert payload["status"] == "completed", payload
    assert payload["full_aoi"] is True
    assert payload["analysis_scope"] == "full_requested_aoi"
    assert payload["coverage"]["status"] == "full_aoi"
    _assert_bbox_close(payload["requested_bbox"], MOON_BBOX)
    _assert_bbox_close(payload["analysis_bbox"], MOON_BBOX)
    assert payload["support"]["kind"] == "requested_aoi"
    assert payload["support"]["full_aoi"] is True
    assert payload["support"]["analysis_extent"] == "full_requested_aoi"
    assert payload["support"]["shrink_applied"] is False
    assert payload["support"]["metric_envelope"]["width_m"] > 0
    pieces = payload["support"]["bbox_pieces"]
    assert len(pieces) == 1, pieces
    _assert_bbox_close(pieces[0], MOON_BBOX)
    assert payload["method_version"] == "fake-full-aoi-1.0.0"

    d01 = payload["dimensions"]["D01"]
    assert d01["status"] == "available"
    assert d01["support"]["kind"] == "requested_aoi"
    _assert_bbox_close(d01["support"]["analysis_bbox"], MOON_BBOX)
    assert d01["provenance"]["dataset_ids"] == [FAKE_DATASET.dataset_id]
    assert d01["provenance"]["source_method_version"] == "coarse-planetary-dem-horn-1.0.0"

    assert payload["dimensions"]["D02"]["status"] == "no_coverage"
    assert payload["dimensions"]["D02"]["features"] == []
    assert payload["dimensions"]["D02"]["reason"]
    assert payload["dimensions"]["D06"]["status"] in EXPLICIT_UNAVAILABLE
    assert payload["dimensions"]["D06"]["features"] == []

    provider = full_aoi_client.fake_full_aoi_provider  # type: ignore[attr-defined]
    assert provider.analyze_calls == [payload["area_id"]]
    _assert_bbox_close(provider.validate_calls[0]["bbox"], MOON_BBOX)

    typed = full_aoi_client.get(f"/api/v1/typed/areas/{payload['area_id']}")
    assert typed.status_code == 200, typed.text
    area = typed.json()
    _assert_bbox_close(area["bbox"], MOON_BBOX)
    assert area["body"] == "Moon"
    assert len([row for row in area["observations"] if row["dimension_code"] == "D01"]) == 3

    result = full_aoi_client.get(f"/api/v1/planetary/aoi/{payload['job_id']}/result")
    assert result.status_code == 200, result.text
    assert result.json()["full_aoi"] is True
    assert result.json()["dimensions"]["D02"]["status"] == "no_coverage"


MOON_UNION_BBOX = {
    "west": 19.832481429354193,
    "south": -10.16488943108404,
    "east": 20.167518570645804,
    "north": -9.835068981384325,
}


def test_whole_requested_bbox_tiled_over_committed_cells_reports_full_aoi(client: TestClient):
    """Whole requested bbox is analyzed by tiling over all intersecting committed cells.

    This exercises the real committed-fixture D01 path end-to-end (no center-window
    shortcut): requested bbox == analyzed union => full_aoi with explicit pixel/valid
    accounting from aggregated cell results.
    """
    response = client.post(
        ENGINE_CREATE_PATH,
        json={
            "body": "Moon",
            "bbox": MOON_UNION_BBOX,
            "idempotency_key": "engine-moon-full-union",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    _assert_engine_contract(payload)

    assert payload["status"] == "completed", payload
    assert payload["full_aoi"] is True
    assert payload["analysis_scope"] == "full_requested_aoi"
    assert payload["coverage"]["status"] == "full_aoi"
    for key in ("west", "south", "east", "north"):
        assert payload["requested_bbox"][key] == pytest.approx(MOON_UNION_BBOX[key], abs=1e-9)
        assert payload["analysis_bbox"][key] == pytest.approx(MOON_UNION_BBOX[key], abs=1e-9)
        assert payload["requested_bbox"][key] == pytest.approx(
            payload["analysis_bbox"][key], abs=1e-9
        )
    assert payload["support"]["full_aoi"] is True

    d01 = payload["dimensions"]["D01"]
    assert d01["status"] == "available"
    assert d01["value_origin"] == "observed_derived"
    assert {feature["feature_id"] for feature in d01["features"]} == {
        "coarse_slope_mean_deg",
        "coarse_slope_p90_deg",
        "coarse_flat_fraction_lt5deg",
    }
    assert all(isinstance(feature["value"], (int, float)) for feature in d01["features"])
    assert d01["provenance"]["dataset_ids"], d01["provenance"]

    # Real multi-cell tiling and pixel accounting (4 committed 5 km cells, 20x20 each).
    accounting = d01["support"].get("pixel_accounting") or payload["coverage"].get(
        "pixel_accounting"
    )
    assert accounting is not None, payload["coverage"]
    assert accounting["cell_count"] == 4
    assert accounting["valid_fraction"] == 1.0
    assert accounting["total_pixels"] == 1600
    assert accounting["valid_pixels"] == 1600
    assert accounting["invalid_or_missing_pixels"] == 0
    assert accounting["resolution_m"] == 250.0

    result = client.get(f"/api/v1/planetary/aoi/{payload['job_id']}/result")
    assert result.status_code == 200, result.text
    persisted = result.json()
    assert persisted["full_aoi"] is True
    assert persisted["analysis_bbox"] == persisted["requested_bbox"]
    assert persisted["dimensions"]["D01"]["support"]["pixel_accounting"]["total_pixels"] == 1600


def test_d02_d06_returns_real_stored_evidence_when_present(client: TestClient):
    """D02-D06 report real stored typed observations when present; others remain explicit."""
    response = client.post(
        ENGINE_CREATE_PATH,
        json={
            "body": "Moon",
            "bbox": MOON_SMALL_BBOX,
            "idempotency_key": "engine-moon-d02-evidence",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] in {"completed", "partial"}
    area_id = payload["area_id"]
    job_id = payload["job_id"]

    # Insert a real stored typed observation for D02 on the analyzed area.
    from analog_api.adapters.typed_db import TypedObservationStore
    from analog_pipelines.typed_contracts import (
        DepthSupport,
        SourceAssetV2,
        SpatialSupport,
        TemporalSupport,
        TypedObservation,
    )

    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        store = TypedObservationStore(session)
        area = store.get_area(area_id)
        assert area is not None
        store.upsert_asset(
            SourceAssetV2(
                asset_id="asset-test-d02-real",
                dataset_id="ds-test-d02-real",
                uri="test://d02-real",
                checksum_algorithm="sha256",
                checksum="0" * 64,
                checksum_scope="derived",
                redistribution_allowed="unknown",
            )
        )
        store.upsert_observation(
            TypedObservation(
                observation_id=f"{area_id}:d02:stored-mineral",
                area_id=area_id,
                body="Moon",
                dimension_code="D02",
                feature_id="test_mineral_presence",
                value_kind="category",
                state="detected",
                value="olivine",
                unit="class",
                spatial_support=SpatialSupport(kind="window", crs=area["crs"], bbox=area["bbox"]),
                temporal_support=TemporalSupport(kind="static"),
                depth_support=DepthSupport(kind="surface"),
                method_version="test-d02-real-stored-1.0.0",
                dataset_ids=["ds-test-d02-real"],
                source_asset_ids=["asset-test-d02-real"],
                quality_flags=["stored_typed_evidence"],
                score_role="evidence_only",
                notes="real stored D02 evidence for this area",
            )
        )
        session.commit()

    status = client.get(f"/api/v1/planetary/aoi/{job_id}")
    assert status.status_code == 200, status.text
    body = status.json()
    _assert_engine_contract(body)

    d02 = body["dimensions"]["D02"]
    assert d02["status"] == "evidence"
    assert d02["value_origin"] == "stored_evidence"
    assert len(d02["features"]) == 1
    assert d02["features"][0]["feature_id"] == "test_mineral_presence"
    assert d02["features"][0]["value"] == "olivine"
    assert d02["provenance"]["observation_ids"] == [f"{area_id}:d02:stored-mineral"]

    # Dimensions without real stored data remain explicit (not fabricated).
    for code in ("D03", "D04", "D05", "D06"):
        assert body["dimensions"][code]["status"] in EXPLICIT_UNAVAILABLE
        assert body["dimensions"][code]["features"] == []
        assert body["dimensions"][code]["reason"]


def test_polar_band_is_analyzed_as_full_latitude_band(full_aoi_client: TestClient):
    """A pole-including band keeps the full requested extent (no centre window)."""
    bbox = {"west": -60.0, "south": -90.0, "east": 60.0, "north": -89.85}
    response = full_aoi_client.post(
        ENGINE_CREATE_PATH,
        json={"body": "Moon", "bbox": bbox, "idempotency_key": "engine-polar-band"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    _assert_engine_contract(payload)
    assert payload["status"] == "completed", payload
    assert payload["full_aoi"] is True
    assert payload["analysis_scope"] == "full_requested_aoi"
    _assert_bbox_close(payload["requested_bbox"], bbox)
    _assert_bbox_close(payload["analysis_bbox"], bbox)
    support = payload["support"]
    assert support["includes_south_pole"] is True
    assert support["includes_north_pole"] is False
    assert support["is_full_longitude"] is False
    assert support["analysis_extent"] == "full_requested_aoi"
    assert support["shrink_applied"] is False
    assert len(support["bbox_pieces"]) == 1
    assert support["pixel_accounting"]["cell_count"] == 1
