"""Independent acceptance tests for Issue #132.

These tests target the *contract* of the Earth bbox endpoint and the planetary on-demand
AOI analysis route. They intentionally fail with an explicit integration message when the
main agent has not wired a route yet (rather than skipping silently).

Run with:

    uv run pytest apps/api/tests/test_issue132_acceptance.py -q
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.domain.aoi import AoiResult
from analog_api.domain.entities import DatasetEntity
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]

EARTH_MAP_PATH = "/api/v1/earth/map-features"
PLANETARY_ANALYZE_PATH = "/api/v1/planetary/areas/analyze"
JOBS_PATH = "/api/v1/jobs"

DIMENSIONS = {f"D0{index}" for index in range(1, 7)}
FEATURE_TYPES = {
    "fine_candidate",
    "on_demand_aoi",
    "typed_area",
    "coarse_candidate",
}
DATA_LEVELS = {"fine", "coarse", "typed"}
SUPPORT_LABELS = {
    "fine_90m",
    "shortlist_250m_derived_not_fine",
    "stored_typed_subset",
}

# Real committed data ranges used by the tests.
GRAND_CANYON_BBOX = "-112.3,36.0,-112.0,36.3"
ATACAMA_BBOX = "-68.5,-23.6,-68.3,-23.4"
MOON_BBOX = {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85}
# The full four-cell Mars Jezero block is ~10.7 km per side and exceeds the 10 km AOI cap.
# A single CTX cell is enough for the deterministic acceptance case.
MARS_BBOX = {"west": 78.08, "south": 18.47, "east": 78.18, "north": 18.56}
OUT_OF_COVERAGE_BBOX = {"west": 0.0, "south": -5.0, "east": 0.05, "north": -4.95}
EMPTY_MARS_BBOX = {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0}

PLANETARY_AOI_METHOD = "planetary-terrain-horn-250m-aoi-1.0.0"
PLANETARY_SOURCE_METHOD = "coarse-planetary-dem-horn-1.0.0"


def _settings(tmp_path: Path, *, seed_typed: bool = True) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'issue132.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=seed_typed,
        aoi_inline=True,
        global_index_dir=str(ROOT / "pipelines/coarse/real-pilot/index"),
        planetary_allow_network=False,
    )


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("issue132-acceptance")
    previous = os.environ.get("ANALOG_AOI_RUNTIME_DIR")
    os.environ["ANALOG_AOI_RUNTIME_DIR"] = str(tmp / "aoi-runtime")
    try:
        with TestClient(create_app(_settings(tmp))) as test_client:
            yield test_client
    finally:
        if previous is None:
            os.environ.pop("ANALOG_AOI_RUNTIME_DIR", None)
        else:
            os.environ["ANALOG_AOI_RUNTIME_DIR"] = previous


def _route_error(response, path: str) -> str:
    return (
        f"Issue #132 integration missing or broken: {path} returned "
        f"HTTP {response.status_code}. The main agent must wire/fix this route before "
        f"acceptance. Response: {response.text[:800]}"
    )


def _require_ok(response, path: str):
    assert response.status_code != 404, _route_error(response, path)
    assert response.status_code == 200, _route_error(response, path)
    return response.json()


def _require_validation_error(response, path: str, *, allowed_codes: set[str] | None = None):
    payload = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    assert response.status_code == 422, (
        f"{path} should reject this query with 422; got HTTP {response.status_code}: "
        f"{response.text[:800]}"
    )
    code = payload.get("error", {}).get("code")
    assert code, f"{path} returned 422 without the shared error.code shape: {response.text}"
    if allowed_codes is not None:
        assert code in allowed_codes, (
            f"{path} returned error code {code!r}; expected one of {sorted(allowed_codes)}. "
            f"Body: {response.text[:800]}"
        )
    return payload


def _assert_bbox_shape(bbox: dict, *, label: str):
    for key in ("west", "south", "east", "north"):
        assert key in bbox, f"{label} bbox is missing {key!r}: {bbox}"
        assert isinstance(bbox[key], (int, float)), f"{label} bbox[{key!r}] is not numeric: {bbox}"
    assert bbox["south"] <= bbox["north"], f"{label} bbox has south > north: {bbox}"
    assert "crosses_antimeridian" in bbox, f"{label} bbox must report crosses_antimeridian: {bbox}"
    assert "longitude_span_deg" in bbox, f"{label} bbox must report longitude_span_deg: {bbox}"


def _earth_support(feature: dict) -> str | None:
    """Return the support label from either the alias or the current primary field."""
    return feature.get("support_label") or feature.get("support")


def _assert_earth_feature(feature: dict, *, data_version: str):
    required = {
        "feature_id",
        "feature_type",
        "body",
        "display_name",
        "location_id",
        "candidate_id",
        "area_id",
        "data_level",
        "bbox",
        "center",
        "geometry",
        "bbox_geometry",
        "bbox_crs",
        "coordinate_frame",
        "dimension_status",
        "available_dimensions",
        "missing_dimensions",
        "availability",
        "data_version",
        "detail_links",
    }
    missing = sorted(required - set(feature))
    assert not missing, (
        "Earth map feature is missing contract field(s) "
        f"{missing}. Current keys: {sorted(feature)}."
    )
    assert _earth_support(feature) is not None, (
        f"Earth map feature has neither support_label nor support. Current keys: {sorted(feature)}"
    )

    assert feature["body"] == "Earth"
    assert feature["feature_type"] in FEATURE_TYPES, feature["feature_type"]
    assert feature["data_level"] in DATA_LEVELS, feature["data_level"]
    support = _earth_support(feature)
    assert support in SUPPORT_LABELS, (
        f"Unexpected support {support!r}; expected one of {sorted(SUPPORT_LABELS)}"
    )
    expected_support = {
        "fine_candidate": "fine_90m",
        "on_demand_aoi": "fine_90m",
        "typed_area": "stored_typed_subset",
        "coarse_candidate": "shortlist_250m_derived_not_fine",
    }[feature["feature_type"]]
    assert support == expected_support, (
        f"{feature['feature_type']} must use support {expected_support!r}, got {support!r}"
    )
    assert feature["data_version"] == data_version, (
        "Feature data_version must match the response data_version; otherwise the client "
        "cannot invalidate caches consistently."
    )

    _assert_bbox_shape(feature["bbox"], label=feature["feature_id"])
    assert feature["geometry"]["type"] == "Point", feature["geometry"]
    coordinates = feature["geometry"].get("coordinates")
    assert isinstance(coordinates, list) and len(coordinates) == 2, feature["geometry"]
    assert coordinates[0] == pytest.approx(feature["center"]["lon"], abs=1e-7)
    assert coordinates[1] == pytest.approx(feature["center"]["lat"], abs=1e-7)
    assert feature["bbox_geometry"]["type"] in {"Polygon", "MultiPolygon"}, feature["bbox_geometry"]
    # Coarse rows retain their projected analysis CRS in ``crs`` until the alias fix lands;
    # the map geometry contract is always the WGS84 bbox + earth_wgs84 coordinate frame.
    assert feature["bbox_crs"] == "EPSG:4326", feature["bbox_crs"]
    assert feature["coordinate_frame"] == "earth_wgs84", feature["coordinate_frame"]
    if feature["feature_type"] != "coarse_candidate":
        assert "4326" in str(feature.get("crs")), (
            f"Non-coarse Earth feature crs must be EPSG:4326, got {feature.get('crs')!r}"
        )

    statuses = feature["dimension_status"]
    assert set(statuses) == DIMENSIONS, (
        f"dimension_status must contain exactly D01..D06, got {sorted(statuses)}"
    )
    for dimension, status in statuses.items():
        assert {"status", "value_origin", "reason"} <= set(status), (
            f"dimension_status[{dimension}] is missing status/value_origin/reason: {status}"
        )
    assert set(feature["available_dimensions"]) <= DIMENSIONS
    assert set(feature["missing_dimensions"]) <= DIMENSIONS
    assert not (set(feature["available_dimensions"]) & set(feature["missing_dimensions"])), (
        "available_dimensions and missing_dimensions must be disjoint"
    )
    assert feature["availability"] is not None
    assert isinstance(feature["detail_links"], dict)


def test_earth_map_route_is_registered_with_earth_openapi_tag(client: TestClient):
    schema = _require_ok(client.get("/openapi.json"), "/openapi.json")
    path_item = schema.get("paths", {}).get(EARTH_MAP_PATH)
    assert path_item, (
        f"Issue #132 integration missing: OpenAPI has no path {EARTH_MAP_PATH}. "
        f"Known paths containing 'map': "
        f"{[path for path in schema.get('paths', {}) if 'map' in path]}"
    )
    operation = path_item.get("get")
    assert operation, f"{EARTH_MAP_PATH} is registered without a GET operation: {path_item}"
    tags = [str(tag).lower() for tag in operation.get("tags", [])]
    assert any("earth" in tag for tag in tags), (
        f"{EARTH_MAP_PATH} must be tagged as an Earth endpoint; got tags={tags!r}"
    )


def test_earth_map_normal_response_and_feature_contract(client: TestClient):
    response = client.get(
        EARTH_MAP_PATH,
        params={"bbox": GRAND_CANYON_BBOX, "limit": 200},
    )
    body = _require_ok(response, EARTH_MAP_PATH)

    required = {
        "contract_version",
        "data_version",
        "snapshot_id",
        "body",
        "bbox",
        "bbox_crs",
        "coordinate_system",
        "query",
        "total",
        "returned",
        "offset",
        "limit",
        "next_offset",
        "counts_by_type",
        "coverage",
        "features",
        "notes",
    }
    missing = sorted(required - set(body))
    assert not missing, (
        f"Earth map response is missing contract field(s) {missing}. Keys: {sorted(body)}"
    )
    assert body["body"] == "Earth"
    assert body["bbox_crs"] == "EPSG:4326", body["bbox_crs"]
    assert "WGS84" in str(body["coordinate_system"]).upper(), body["coordinate_system"]
    assert body["data_version"], "data_version must not be empty"
    assert body["snapshot_id"], "snapshot_id must not be empty"
    assert body["offset"] == 0
    assert body["limit"] == 200
    assert body["returned"] == len(body["features"])
    assert body["returned"] <= body["limit"]
    assert body["total"] >= body["returned"]
    assert body["total"] > 0, (
        f"Expected real fine/typed/coarse features inside {GRAND_CANYON_BBOX}; "
        f"response: {response.text[:800]}"
    )
    _assert_bbox_shape(body["bbox"], label="response")

    expected_next = (
        body["offset"] + body["returned"]
        if body["offset"] + body["returned"] < body["total"]
        else None
    )
    assert body["next_offset"] == expected_next, (
        f"next_offset must be {expected_next!r} for total={body['total']} "
        f"offset={body['offset']} returned={body['returned']}; got {body['next_offset']!r}"
    )
    assert isinstance(body["counts_by_type"], dict)

    coverage = body["coverage"]
    assert {"fine", "on_demand", "typed", "coarse"} <= set(coverage), coverage
    coarse = coverage["coarse"]
    for key in ("status", "regions", "bbox", "matched_count"):
        assert key in coarse, (
            f"coverage.coarse is missing contract field {key!r}; got {sorted(coarse)}."
        )
    snapshot = coarse.get("snapshot") or coarse.get("snapshot_id")
    method = coarse.get("method") or coarse.get("method_version")
    assert snapshot, f"coverage.coarse must expose snapshot (or snapshot_id): {sorted(coarse)}"
    assert method, f"coverage.coarse must expose method (or method_version): {sorted(coarse)}"
    assert coarse["status"] in {"ready", "no_index"}, coarse["status"]
    assert coarse["status"] == "ready", (
        "The acceptance fixture configures pipelines/coarse/real-pilot/index, so coverage."
        f"coarse.status must be ready; got {coarse['status']!r}: {coarse}"
    )
    assert snapshot == "geomorpho250-real-pilot-20261115", snapshot
    assert method == "coarse-terrain-250m-1.0.0", method
    assert coarse["regions"], "coverage.coarse.regions must list the real pilot regions"
    assert coarse["matched_count"] > 0

    for feature in body["features"]:
        _assert_earth_feature(feature, data_version=body["data_version"])

    # The Grand Canyon pilot contains both a real fine candidate and a coarse shortlist.
    types = {feature["feature_type"] for feature in body["features"]}
    assert "fine_candidate" in types, (
        f"Expected fine_candidate features in {GRAND_CANYON_BBOX}; got types={sorted(types)}"
    )
    assert "coarse_candidate" in types, (
        f"Expected coarse_candidate features in {GRAND_CANYON_BBOX}; got types={sorted(types)}"
    )


def test_earth_map_antimeridian_360_and_polar_queries(client: TestClient):
    crossing = _require_ok(
        client.get(EARTH_MAP_PATH, params={"bbox": "170,-90,-170,90"}),
        EARTH_MAP_PATH,
    )
    assert crossing["bbox"]["crosses_antimeridian"] is True, crossing["bbox"]
    assert crossing["bbox"]["longitude_span_deg"] == pytest.approx(20.0), crossing["bbox"]

    crossing_360 = _require_ok(
        client.get(
            EARTH_MAP_PATH,
            params={"bbox": "170,-90,190,90", "bbox_lon_convention": "360"},
        ),
        EARTH_MAP_PATH,
    )
    assert crossing_360["bbox"]["crosses_antimeridian"] is True, crossing_360["bbox"]
    assert crossing_360["bbox"]["longitude_span_deg"] == pytest.approx(20.0)
    assert crossing_360["bbox"]["west"] == pytest.approx(170.0)
    assert crossing_360["bbox"]["east"] == pytest.approx(-170.0)

    # No committed Earth feature lies in that antimeridian strip; this must be an explicit
    # empty result, not an error or a synthetic placeholder.
    assert crossing["total"] == 0, crossing
    assert crossing["features"] == [], crossing["features"]

    polar = _require_ok(
        client.get(EARTH_MAP_PATH, params={"bbox": "-95,75,-85,76", "limit": 50}),
        EARTH_MAP_PATH,
    )
    assert polar["total"] > 0, (
        f"Expected committed Devon Island features inside the polar box; got {polar}"
    )
    assert all(feature["bbox"]["north"] <= 90.0 for feature in polar["features"])
    assert all(feature["bbox"]["south"] >= 75.0 for feature in polar["features"])


def test_earth_map_empty_result_pagination_and_hard_limit(client: TestClient):
    empty = _require_ok(
        client.get(EARTH_MAP_PATH, params={"bbox": "0,0,1,1"}),
        EARTH_MAP_PATH,
    )
    assert empty["total"] == 0, empty
    assert empty["features"] == [], empty["features"]

    first = _require_ok(
        client.get(
            EARTH_MAP_PATH,
            params={"bbox": "-180,-90,180,90", "limit": 3, "offset": 0},
        ),
        EARTH_MAP_PATH,
    )
    assert first["returned"] == 3, first
    assert first["next_offset"] == 3, first
    assert len(first["features"]) == 3

    second = _require_ok(
        client.get(
            EARTH_MAP_PATH,
            params={"bbox": "-180,-90,180,90", "limit": 3, "offset": 3},
        ),
        EARTH_MAP_PATH,
    )
    first_ids = {feature["feature_id"] for feature in first["features"]}
    second_ids = {feature["feature_id"] for feature in second["features"]}
    assert first_ids.isdisjoint(second_ids), (
        f"offset pagination repeated features: {sorted(first_ids & second_ids)}"
    )

    radius = _require_validation_error(
        client.get(EARTH_MAP_PATH, params={"bbox": "-180,-90,180,90", "limit": 201}),
        EARTH_MAP_PATH,
    )
    assert radius["error"]["code"] in {"validation_error", "invalid_pagination"}, radius

    _require_validation_error(
        client.get(EARTH_MAP_PATH, params={"bbox": "-180,-90,180,90", "limit": 0}),
        EARTH_MAP_PATH,
    )
    _require_validation_error(
        client.get(EARTH_MAP_PATH, params={"bbox": "-180,-90,180,90", "offset": -1}),
        EARTH_MAP_PATH,
    )


def test_earth_map_type_level_support_and_dimension_filters(client: TestClient):
    coarse = _require_ok(
        client.get(
            EARTH_MAP_PATH,
            params={
                "bbox": GRAND_CANYON_BBOX,
                "data_level": "coarse",
                "feature_type": "coarse_candidate",
                "limit": 200,
            },
        ),
        EARTH_MAP_PATH,
    )
    assert coarse["total"] > 0, coarse
    assert all(feature["feature_type"] == "coarse_candidate" for feature in coarse["features"])
    assert all(feature["data_level"] == "coarse" for feature in coarse["features"])
    assert all(
        _earth_support(feature) == "shortlist_250m_derived_not_fine"
        for feature in coarse["features"]
    )
    assert all(feature["candidate_id"] for feature in coarse["features"]), (
        "Every coarse feature must expose candidate_id for /global/candidates/{id}"
    )
    assert coarse["coverage"]["coarse"]["matched_count"] > 0

    fine = _require_ok(
        client.get(
            EARTH_MAP_PATH,
            params={
                "bbox": GRAND_CANYON_BBOX,
                "data_level": "fine",
                "feature_type": "fine_candidate",
                "limit": 200,
            },
        ),
        EARTH_MAP_PATH,
    )
    assert fine["total"] > 0, fine
    assert all(feature["feature_type"] == "fine_candidate" for feature in fine["features"])
    assert all(feature["location_id"] for feature in fine["features"])
    assert all(_earth_support(feature) == "fine_90m" for feature in fine["features"])

    typed = _require_ok(
        client.get(
            EARTH_MAP_PATH,
            params={
                "bbox": ATACAMA_BBOX,
                "feature_type": "typed_area",
                "limit": 50,
            },
        ),
        EARTH_MAP_PATH,
    )
    assert typed["total"] > 0, (
        f"Expected the committed Atacama typed area inside {ATACAMA_BBOX}; got {typed}"
    )
    assert all(feature["feature_type"] == "typed_area" for feature in typed["features"])
    assert all(_earth_support(feature) == "stored_typed_subset" for feature in typed["features"])
    assert any(feature["area_id"] == "earth-atacama-desert" for feature in typed["features"]), (
        f"Expected area_id earth-atacama-desert; got "
        f"{[feature['area_id'] for feature in typed['features']]}"
    )

    d06 = _require_ok(
        client.get(
            EARTH_MAP_PATH,
            params={
                "bbox": "-180,-90,180,90",
                "dimension": "D06",
                "availability": "present",
                "limit": 10,
            },
        ),
        EARTH_MAP_PATH,
    )
    assert d06["total"] > 0, (
        "Committed SoilGrids D06 typed observations should be selectable with "
        "dimension=D06&availability=present"
    )
    for feature in d06["features"]:
        assert "D06" in feature["available_dimensions"], feature
        assert feature["dimension_status"]["D06"]["status"] != "absent", feature

    d01 = _require_ok(
        client.get(
            EARTH_MAP_PATH,
            params={
                "bbox": "-180,-90,180,90",
                "dimension": "D01",
                "availability": "present",
                "data_level": "fine",
                "limit": 5,
            },
        ),
        EARTH_MAP_PATH,
    )
    assert d01["total"] > 0, d01
    for feature in d01["features"]:
        assert "D01" in feature["available_dimensions"], feature
        assert feature["dimension_status"]["D01"]["status"] != "absent", feature


def test_earth_map_rejects_invalid_queries(client: TestClient):
    _require_validation_error(
        client.get(EARTH_MAP_PATH, params={"bbox": "0,0,1"}),
        EARTH_MAP_PATH,
        allowed_codes={"invalid_bbox", "validation_error"},
    )
    _require_validation_error(
        client.get(EARTH_MAP_PATH, params={"bbox": "0,0,1,91"}),
        EARTH_MAP_PATH,
        allowed_codes={"invalid_bbox_latitude", "invalid_bbox", "validation_error"},
    )
    _require_validation_error(
        client.get(EARTH_MAP_PATH, params={"bbox": "0,10,1,-10"}),
        EARTH_MAP_PATH,
        allowed_codes={"invalid_bbox", "validation_error"},
    )
    _require_validation_error(
        client.get(EARTH_MAP_PATH, params={"bbox": "10,0,10,1"}),
        EARTH_MAP_PATH,
        allowed_codes={
            "invalid_bbox",
            "invalid_bbox_longitude",
            "validation_error",
        },
    )
    _require_validation_error(
        client.get(
            EARTH_MAP_PATH,
            params={"bbox": "-180,-90,180,90", "dimension": "D07"},
        ),
        EARTH_MAP_PATH,
        allowed_codes={"invalid_dimension", "validation_error"},
    )
    _require_validation_error(
        client.get(
            EARTH_MAP_PATH,
            params={"bbox": "-180,-90,180,90", "feature_type": "planet"},
        ),
        EARTH_MAP_PATH,
        allowed_codes={"invalid_feature_type", "validation_error"},
    )
    _require_validation_error(
        client.get(
            EARTH_MAP_PATH,
            params={"bbox": "-180,-90,180,90", "data_level": "best"},
        ),
        EARTH_MAP_PATH,
        allowed_codes={"invalid_data_level", "validation_error"},
    )
    _require_validation_error(
        client.get(
            EARTH_MAP_PATH,
            params={"bbox": "-180,-90,180,90", "bbox_lon_convention": "wrap"},
        ),
        EARTH_MAP_PATH,
        allowed_codes={
            "invalid_lon_convention",
            "invalid_longitude_convention",
            "validation_error",
        },
    )


class _FakeEarthProcessor:
    """Offline Earth processor used only to verify on-demand map integration."""

    def analyze(self, request) -> AoiResult:
        dataset = DatasetEntity(
            dataset_id="test-issue132-earth-dem",
            provider="Test DEM provider",
            product_id="test-dem-v1",
            title="Test DEM",
            url="https://example.invalid/dem",
            license="Test licence",
            license_url="https://example.invalid/licence",
            access_timestamp="2026-11-15T00:00:00Z",
            source_crs="EPSG:4326",
            native_resolution_m=90.0,
            processing_version="terrain-metrics-1.0.0",
            checksum=None,
            source_kind="earth_dem",
            notes="Synthetic provider used only in tests.",
        )
        return AoiResult(
            area_id=request.area_id,
            bbox=request.bbox,
            geometry=request.geometry,
            area_km2=request.area_km2,
            center_lon=(request.bbox.west + request.bbox.east) / 2,
            center_lat=(request.bbox.south + request.bbox.north) / 2,
            crs="EPSG:32629",
            valid_fraction=0.99,
            window_size_m=request.width_m,
            resolution_m=90.0,
            features={
                "slope_mean_deg": 4.5,
                "slope_p90_deg": 8.1,
                "terrain_relief_p95_p05_m": 72.0,
                "roughness_std_m": 18.0,
            },
            context_observations={},
            datasets=(dataset,),
            provider_status={dataset.dataset_id: {"status": "verified"}},
            quality_flags=("on_demand_aoi",),
            warnings=(),
            checksum="sha256:test",
        )


def test_earth_map_includes_persisted_on_demand_aoi(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANALOG_AOI_RUNTIME_DIR", str(tmp_path / "aoi-runtime"))
    app = create_app(_settings(tmp_path, seed_typed=False), aoi_processor=_FakeEarthProcessor())
    with TestClient(app) as test_client:
        created = test_client.post(
            "/api/v1/areas/analyze",
            json={
                "body": "Earth",
                "bbox": {
                    "west": -6.615,
                    "south": 37.684,
                    "east": -6.602,
                    "north": 37.697,
                },
            },
        )
        assert created.status_code == 200, created.text
        created_body = created.json()
        assert created_body["status"] == "completed", created_body
        area_id = created_body["area_id"]
        assert area_id.startswith("earth-aoi-"), area_id

        mapped = _require_ok(
            test_client.get(
                EARTH_MAP_PATH,
                params={
                    "bbox": "-6.62,37.68,-6.60,37.70",
                    "feature_type": "on_demand_aoi",
                    "limit": 50,
                },
            ),
            EARTH_MAP_PATH,
        )
        assert mapped["total"] >= 1, (
            f"Persisted on-demand AOI {area_id} did not appear in the Earth map response: {mapped}"
        )
        matches = [feature for feature in mapped["features"] if feature["location_id"] == area_id]
        assert matches, (
            f"Expected location_id={area_id!r} in on_demand features; got "
            f"{[(f['feature_id'], f['location_id']) for f in mapped['features']]}"
        )
        feature = matches[0]
        assert feature["feature_type"] == "on_demand_aoi", feature
        assert _earth_support(feature) == "fine_90m", feature
        assert feature["detail_links"].get("area_features") == (
            f"/api/v1/areas/{area_id}/features"
        ), feature["detail_links"]


@pytest.mark.parametrize(
    ("body", "bbox", "expected_frame_fragment"),
    [
        ("Moon", MOON_BBOX, "lunar_planetocentric"),
        ("Mars", MARS_BBOX, "mars_planetocentric"),
    ],
)
def test_planetary_analyze_end_to_end(
    client: TestClient,
    body: str,
    bbox: dict[str, float],
    expected_frame_fragment: str,
):
    response = client.post(
        PLANETARY_ANALYZE_PATH,
        json={
            "body": body,
            "bbox": bbox,
            "requested_features": [
                "coarse_slope_mean_deg",
                "coarse_slope_p90_deg",
                "coarse_flat_fraction_lt5deg",
            ],
        },
    )
    assert response.status_code != 404, _route_error(response, PLANETARY_ANALYZE_PATH)
    assert response.status_code in {200, 202}, _route_error(response, PLANETARY_ANALYZE_PATH)
    created = response.json()

    for key in (
        "job_id",
        "status",
        "area_id",
        "cache_key",
        "cached",
        "body",
        "coordinate_frame",
        "provider_status",
        "result",
        "links",
    ):
        assert key in created, (
            f"Planetary analyze response is missing contract field {key!r}. Keys: {sorted(created)}"
        )
    assert created["body"] == body
    assert created["coordinate_frame"] == expected_frame_fragment, created["coordinate_frame"]
    assert "earth" not in created["coordinate_frame"].lower()
    assert created["status"] in {"completed", "partial"}, (
        f"Inline acceptance run should complete the {body} pilot AOI; got "
        f"status={created['status']!r}: {response.text[:800]}"
    )
    assert created["area_id"], created
    assert created["cached"] is False
    assert created["links"].get("job") == f"/api/v1/jobs/{created['job_id']}", created["links"]
    assert created["links"].get("typed_area") == (f"/api/v1/typed/areas/{created['area_id']}"), (
        created["links"]
    )

    result = created["result"]
    assert isinstance(result, dict), result
    assert result.get("body") == body, result
    assert result.get("coordinate_frame") == expected_frame_fragment, result
    assert result.get("method_version") == PLANETARY_AOI_METHOD, result
    dimension_support = result.get("dimension_support") or {}
    d01_support = dimension_support.get("D01") or {}
    assert d01_support.get("method_version") == PLANETARY_AOI_METHOD, d01_support
    assert d01_support.get("source_method_version") == PLANETARY_SOURCE_METHOD, (
        "The new AOI recomputation method must be distinct from the source coarse-cell "
        f"method. D01 support: {d01_support}"
    )
    assert result.get("analysis_kind") == "committed_reference_fixture", result
    features = result.get("features") or {}
    for feature_id in (
        "coarse_slope_mean_deg",
        "coarse_slope_p90_deg",
        "coarse_flat_fraction_lt5deg",
    ):
        assert feature_id in features, (
            f"{body} analysis result is missing D01 feature {feature_id!r}: {features}"
        )
        value = features[feature_id]
        assert isinstance(value, (int, float)) and math.isfinite(value), (feature_id, value)
    assert 0.0 <= features["coarse_slope_mean_deg"] <= 90.0
    assert 0.0 <= features["coarse_slope_p90_deg"] <= 90.0
    assert 0.0 <= features["coarse_flat_fraction_lt5deg"] <= 1.0
    assert "EPSG:4326" not in str(result), (
        "Planetary result must never carry an Earth EPSG:4326 CRS"
    )

    job = _require_ok(
        client.get(f"{JOBS_PATH}/{created['job_id']}"),
        f"{JOBS_PATH}/{{job_id}}",
    )
    assert job["status"] == created["status"], job
    assert job["area_id"] == created["area_id"], job
    assert job.get("body") == body, job
    assert job.get("coordinate_frame") == expected_frame_fragment, job
    assert job.get("error") is None, job

    typed = _require_ok(
        client.get(f"/api/v1/typed/areas/{created['area_id']}"),
        "/api/v1/typed/areas/{area_id}",
    )
    assert typed["body"] == body, typed
    assert typed.get("coordinate_frame") == expected_frame_fragment, typed
    assert "4326" not in str(typed.get("crs")), typed.get("crs")
    observations = typed.get("observations") or []
    d01 = [
        observation
        for observation in observations
        if observation.get("dimension_code") == "D01" and observation.get("feature_id") in features
    ]
    assert d01, (
        f"Persisted {body} typed area has no D01 observations for the requested features. "
        f"Observations: {[(o.get('dimension_code'), o.get('feature_id')) for o in observations]}"
    )

    overlay = _require_ok(
        client.get(
            "/api/v1/planetary/map-features",
            params={
                "body": body,
                "bbox": (
                    f"{bbox['west'] - 0.05},{bbox['south'] - 0.05},"
                    f"{bbox['east'] + 0.05},{bbox['north'] + 0.05}"
                ),
                "limit": 200,
            },
        ),
        "/api/v1/planetary/map-features",
    )
    matches = [
        feature
        for feature in overlay["features"]
        if feature.get("typed_area_id") == created["area_id"]
        or str(feature.get("feature_id", "")).endswith(created["area_id"])
    ]
    assert matches, (
        f"The new {body} typed area {created['area_id']} is missing from planetary "
        f"map-features. Returned ids: {[f['feature_id'] for f in overlay['features']]}"
    )
    for feature in overlay["features"]:
        assert feature["body"] == body, feature


def test_planetary_out_of_coverage_is_explicit_and_not_faked(client: TestClient):
    # A small valid-size bbox away from every committed pilot reaches coverage validation
    # instead of the separate aoi_too_large guard.
    response = client.post(
        PLANETARY_ANALYZE_PATH,
        json={"body": "Moon", "bbox": OUT_OF_COVERAGE_BBOX},
    )
    assert response.status_code != 404, _route_error(response, PLANETARY_ANALYZE_PATH)

    failed_area_id = None
    if response.status_code in {200, 202}:
        body = response.json()
        assert body["status"] == "failed", (
            "An out-of-coverage planetary bbox must fail explicitly; got "
            f"status={body['status']!r}: {response.text[:800]}"
        )
        assert body["result"] in (None, {}), body
        reasons = [
            str(value.get("reason", ""))
            for value in (body.get("provider_status") or {}).values()
            if isinstance(value, dict)
        ]
        assert any("coverage" in reason.lower() for reason in reasons), (
            "Failed out-of-coverage job must record an explicit coverage reason; got "
            f"provider_status={body.get('provider_status')}"
        )
        failed_area_id = body.get("area_id")
    else:
        assert response.status_code in {422, 503}, (
            "Out-of-coverage must return an explicit 422/503 or a failed job; got "
            f"HTTP {response.status_code}: {response.text[:800]}"
        )
        payload = response.json()
        code = str(payload.get("error", {}).get("code", ""))
        assert any(
            marker in code.lower()
            for marker in (
                "coverage",
                "unsupported",
                "outside",
                "pilot",
                "source",
                "aoi_too_large",
            )
        ), (
            f"Expected an explicit coverage/unsupported error code, got {code!r}: "
            f"{response.text[:800]}"
        )

    # A failed job may allocate an area id for the durable job row, but it must not be
    # persisted as a typed area with fabricated observations.
    if failed_area_id:
        detail = client.get(f"/api/v1/typed/areas/{failed_area_id}")
        assert detail.status_code == 404, (
            f"Failed out-of-coverage area {failed_area_id} must not be persisted; "
            f"GET /typed/areas returned HTTP {detail.status_code}: {detail.text[:500]}"
        )

    overlay = _require_ok(
        client.get(
            "/api/v1/planetary/map-features",
            params={"body": "Moon", "bbox": "0,-5.1,0.1,-4.9", "limit": 200},
        ),
        "/api/v1/planetary/map-features",
    )
    assert overlay["total"] == 0, (
        f"Out-of-coverage analysis must not create a fake Moon feature: {overlay}"
    )


def test_planetary_idempotency_cache_and_job_recovery(client: TestClient):
    bbox = {"west": 19.86, "south": -10.14, "east": 20.14, "north": -9.86}
    payload = {
        "body": "Moon",
        "bbox": bbox,
        "requested_features": ["coarse_slope_mean_deg"],
        "idempotency_key": "issue132-moon-retry-acceptance",
    }
    first_response = client.post(PLANETARY_ANALYZE_PATH, json=payload)
    first = _require_ok(first_response, PLANETARY_ANALYZE_PATH)
    assert first["status"] in {"completed", "partial"}, first
    assert first["cached"] is False, first

    second = _require_ok(
        client.post(PLANETARY_ANALYZE_PATH, json=payload),
        PLANETARY_ANALYZE_PATH,
    )
    assert second["cached"] is True, (
        f"Repeating the same idempotency_key must return cached=true: {second}"
    )
    assert second["job_id"] == first["job_id"], (first["job_id"], second["job_id"])
    assert second["area_id"] == first["area_id"], (first["area_id"], second["area_id"])

    cached_without_key = _require_ok(
        client.post(
            PLANETARY_ANALYZE_PATH,
            json={
                "body": "Moon",
                "bbox": bbox,
                "requested_features": ["coarse_slope_mean_deg"],
            },
        ),
        PLANETARY_ANALYZE_PATH,
    )
    assert cached_without_key["cached"] is True, cached_without_key
    assert cached_without_key["job_id"] == first["job_id"], cached_without_key

    job = _require_ok(
        client.get(f"{JOBS_PATH}/{first['job_id']}"),
        f"{JOBS_PATH}/{{job_id}}",
    )
    assert job["status"] == first["status"], job
    assert job["area_id"] == first["area_id"], job
    assert job["provider_status"], (
        f"Completed planetary jobs must record source/provider status; got {job}"
    )


def test_body_isolation_and_existing_global_search_still_work(client: TestClient):
    earth = _require_ok(
        client.get(
            EARTH_MAP_PATH,
            params={"bbox": "-180,-90,180,90", "limit": 200},
        ),
        EARTH_MAP_PATH,
    )
    assert earth["body"] == "Earth"
    assert all(feature["body"] == "Earth" for feature in earth["features"])
    assert all(feature["bbox_crs"] == "EPSG:4326" for feature in earth["features"]), (
        "Earth map bboxes must be WGS84"
    )
    assert all(feature["coordinate_frame"] == "earth_wgs84" for feature in earth["features"]), (
        "Earth map features must declare the Earth WGS84 coordinate frame"
    )

    mars = _require_ok(
        client.get(
            "/api/v1/planetary/map-features",
            params={"body": "Mars", "bbox": "76,17,79,20", "limit": 200},
        ),
        "/api/v1/planetary/map-features",
    )
    assert mars["body"]["body"] == "Mars"
    assert all(feature["body"] == "Mars" for feature in mars["features"])
    assert not any(feature["body"] == "Earth" for feature in mars["features"])

    health = _require_ok(client.get("/api/v1/health"), "/api/v1/health")
    assert health["status"] == "ok", health

    global_search = client.post(
        "/api/v1/global/search",
        json={
            "target": {"coarse_slope_mean_deg": {"value": 10.0}},
            "limit": 3,
        },
    )
    assert global_search.status_code == 200, _route_error(global_search, "/api/v1/global/search")
    global_body = global_search.json()
    assert global_body["results"], global_body
    assert all(result["body"] == "Earth" for result in global_body["results"]), global_body
