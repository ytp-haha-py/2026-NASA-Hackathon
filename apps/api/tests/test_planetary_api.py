from __future__ import annotations

from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.domain.errors import AppError
from analog_api.domain.planetary import (
    bbox_contains,
    bbox_intersects,
    normalize_longitude,
    parse_bbox,
    planetographic_to_planetocentric,
    to_360,
)
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("planetary")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'planetary.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_moon_and_mars_map_catalog_uses_verified_official_layers(client: TestClient):
    for body, expected_layers, expected_epsg in (
        ("Moon", {"imagery", "elevation"}, 104903),
        ("Mars", {"imagery", "elevation"}, 104905),
    ):
        response = client.get("/api/v1/planetary/maps", params={"body": body})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["body"]["body"] == body
        assert payload["body"]["longitude_direction"] == "positive_east"
        assert payload["body"]["latitude_kind"] == "planetocentric"
        assert payload["body"]["earth_wgs84"] is False
        assert payload["body"]["geographic_epsg"] == expected_epsg
        assert payload["body"]["geographic_epsg_is_epsg_registry"] is False
        assert payload["catalog_version"].startswith("planetary-map-catalog-")
        assert {layer["layer_type"] for layer in payload["layers"]} == expected_layers
        for layer in payload["layers"]:
            assert layer["service_type"].startswith("OGC WMTS")
            assert "{TileMatrix}" in layer["tile_template"]
            assert "{TileRow}" in layer["tile_template"]
            assert "{TileCol}" in layer["tile_template"]
            assert layer["epsg"] == expected_epsg
            assert "not an EPSG registry code" in layer["crs_authority"]
            assert layer["zoom_semantics"].startswith("WMTS TileMatrix")
            assert "Do not treat these coordinates as Earth WGS84/OSM" in layer["coordinate_note"]
            assert layer["zoom"]["min"] == 0
            assert layer["zoom"]["max"] >= 5
            assert len(layer["tile_matrix"]) == layer["zoom"]["max"] + 1
            assert layer["bounds"]["west"] == -180.0
            assert layer["bounds"]["east"] == 180.0
            assert layer["verification"]["status"] == "verified"
            assert layer["verification"]["http_status"] == 200
            assert layer["verification"]["capabilities_http_status"] == 200
            assert "NASA" in layer["attribution"]
            assert "public domain" in layer["license"]
            assert layer["license_url"].startswith("https://")
            assert layer["source_url"].endswith("WMTSCapabilities.xml")
        assert payload["science_coverage"]["coverage_label"] == "curated_subset_not_global"
        assert payload["coverage_separation"]["basemap_extent"].startswith("global")
        assert "not a global scientific survey" in payload["science_coverage"]["note"]
        assert response.headers["cache-control"] == "public, max-age=300"
        assert response.headers["x-planetary-catalog-version"] == payload["catalog_version"]
        assert response.headers["x-data-snapshot-id"] == payload["data_version"]


def test_maps_reject_earth_and_unknown_bodies(client: TestClient):
    for body in ("Earth", "Venus", ""):
        response = client.get("/api/v1/planetary/maps", params={"body": body})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_body"


def test_coordinate_contract_documents_planetocentric_antimeridian_and_poles(
    client: TestClient,
):
    response = client.get("/api/v1/planetary/coordinate-contract")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["contract_version"] == "planetary-coordinates-1.0.0"
    bodies = {body["body"]: body for body in payload["bodies"]}
    assert bodies["Moon"]["mean_radius_m"] == 1737400.0
    assert bodies["Mars"]["mean_radius_m"] == 3396190.0
    assert bodies["Mars"]["polar_radius_m"] == 3376200.0
    assert bodies["Mars"]["latitude_kind"] == "planetocentric"
    assert payload["geojson"]["crs"].endswith("not Earth EPSG:4326")
    assert payload["antimeridian"]["response_flag"] == "crosses_antimeridian=true"
    assert payload["poles"]["lat_range"] == [-90.0, 90.0]
    mars_example = payload["planetocentric_to_planetographic"]["mars_example"]
    assert mars_example["planetographic_deg"] != mars_example["planetocentric_deg"]
    assert (
        abs(
            planetographic_to_planetocentric(mars_example["planetographic_deg"], "Mars")
            - mars_example["planetocentric_deg"]
        )
        < 1e-9
    )


def test_mars_jezero_bbox_returns_reference_and_typed_overlay(client: TestClient):
    response = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Mars", "bbox": "76,17,79,20", "limit": 200},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["bbox"]["crosses_antimeridian"] is False
    assert payload["bbox_crs"].startswith("planetocentric")
    features = {feature["feature_id"]: feature for feature in payload["features"]}
    reference = features["reference:mars-jezero-ctx-window-01"]
    typed = features["typed:mars-jezero-ctx-window-01"]
    assert reference["relationship"] == "same_area_id_exact_join"
    assert typed["relationship"] == "same_area_id_exact_join"
    assert reference["linked_feature_id"] == "typed:mars-jezero-ctx-window-01"
    assert typed["linked_feature_id"] == "reference:mars-jezero-ctx-window-01"
    assert reference["science_coverage"]["d01_feature_count"] == 5
    assert "D05" in typed["science_coverage"]["typed_dimensions"]
    assert typed["science_coverage"]["dimension_status"]["D05"]["status"] == ("present_evidence")
    assert typed["science_coverage"]["dimension_status"]["D06"]["status"] == "absent"
    assert typed["science_coverage"]["dimension_status"]["D06"]["record_count"] == 0
    assert typed["center"]["lon"] == pytest.approx(77.37, abs=0.05)
    assert typed["center"]["lat"] == pytest.approx(18.22, abs=0.05)
    assert all(feature["body"] == "Mars" for feature in payload["features"])
    assert all("Earth" not in feature["display_name"] for feature in payload["features"])
    assert payload["unlocatable_typed_area_count"] == 0


def test_moon_reference_by_bbox_and_antimeridian_crossing(client: TestClient):
    direct = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Moon", "bbox": "122,-89,125,-88"},
    )
    assert direct.status_code == 200, direct.text
    ids = {feature["feature_id"] for feature in direct.json()["features"]}
    assert "reference:moon-lola-site07-roi1" in ids

    crossing = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Moon", "bbox": "120,-90,-150,-85", "limit": 200},
    )
    assert crossing.status_code == 200, crossing.text
    body = crossing.json()
    assert body["bbox"]["crosses_antimeridian"] is True
    assert body["bbox"]["longitude_span_deg"] == 90.0
    crossing_ids = {feature["feature_id"] for feature in body["features"]}
    assert "reference:moon-lola-site07-roi1" in crossing_ids
    assert "reference:moon-shackleton-rim-site04-roi1" in crossing_ids


def test_moon_360_longitude_convention_and_polar_bounds(client: TestClient):
    signed = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Moon", "bbox": "-137.2,-89.5,-136.9,-89.4"},
    )
    assert signed.status_code == 200, signed.text
    signed_ids = {feature["feature_id"] for feature in signed.json()["features"]}
    assert "reference:moon-lola-site01-roi1" in signed_ids

    east_360 = client.get(
        "/api/v1/planetary/map-features",
        params={
            "body": "Moon",
            "bbox": "222.8,-89.5,223.1,-89.4",
            "bbox_lon_convention": "360",
        },
    )
    assert east_360.status_code == 200, east_360.text
    assert {feature["feature_id"] for feature in east_360.json()["features"]} == signed_ids

    polar = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Moon", "bbox": "-180,-90,180,-89", "limit": 200},
    )
    assert polar.status_code == 200, polar.text
    assert polar.json()["total"] > 10
    assert all(
        feature["bbox"]["north"] >= -90.0 and feature["bbox"]["south"] <= -89.0
        for feature in polar.json()["features"]
    )


def test_empty_result_invalid_bounds_and_body_are_explicit(client: TestClient):
    empty = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Mars", "bbox": "0,0,1,1"},
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["total"] == 0
    assert empty.json()["features"] == []

    invalid_lat = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Mars", "bbox": "0,0,1,91"},
    )
    assert invalid_lat.status_code == 422
    assert invalid_lat.json()["error"]["code"] == "invalid_bbox_latitude"

    invalid_order = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Mars", "bbox": "0,10,1,-10"},
    )
    assert invalid_order.status_code == 422
    assert invalid_order.json()["error"]["code"] == "invalid_bbox"

    earth = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Earth", "bbox": "-180,-90,180,90"},
    )
    assert earth.status_code == 422
    assert earth.json()["error"]["code"] == "unsupported_body"


def test_pagination_type_and_dimension_filters(client: TestClient):
    first = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Mars", "bbox": "76,17,79,20", "limit": 2, "offset": 0},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total"] > 2
    assert body["returned"] == 2
    assert body["next_offset"] == 2

    typed_only = client.get(
        "/api/v1/planetary/map-features",
        params={
            "body": "Mars",
            "bbox": "76,17,79,20",
            "feature_type": "typed_area",
            "limit": 200,
        },
    )
    assert typed_only.status_code == 200, typed_only.text
    assert all(feature["feature_type"] == "typed_area" for feature in typed_only.json()["features"])

    no_d06 = client.get(
        "/api/v1/planetary/map-features",
        params={
            "body": "Mars",
            "bbox": "76,17,79,20",
            "dimension": "D06",
            "limit": 200,
        },
    )
    assert no_d06.status_code == 200, no_d06.text
    assert no_d06.json()["total"] == 0

    invalid_dimension = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Mars", "bbox": "76,17,79,20", "dimension": "D07"},
    )
    assert invalid_dimension.status_code == 422
    assert invalid_dimension.json()["error"]["code"] == "invalid_dimension"

    too_large = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Mars", "bbox": "76,17,79,20", "limit": 201},
    )
    assert too_large.status_code == 422

    cached = client.get(
        "/api/v1/planetary/map-features",
        params={"body": "Mars", "bbox": "76,17,79,20", "limit": 2},
    )
    assert cached.status_code == 200, cached.text
    assert cached.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=300"
    assert cached.headers["x-data-snapshot-id"] == cached.json()["data_version"]


def test_coordinate_helpers_handle_wrapping_and_antimeridian():
    assert normalize_longitude(-137.09597807) == pytest.approx(-137.09597807)
    assert normalize_longitude(123.54628783) == pytest.approx(123.54628783)
    assert normalize_longitude(180.0) == pytest.approx(-180.0)
    assert normalize_longitude(360.0) == pytest.approx(0.0)
    assert to_360(-137.09597807) == pytest.approx(222.90402193)
    assert to_360(-0.23578377) == pytest.approx(359.76421623)

    crossing = parse_bbox("120,-90,-150,-85")
    assert crossing.crosses_antimeridian is True
    assert crossing.longitude_span() == 90.0
    assert bbox_contains(crossing, -88.0, 123.0) is True
    assert bbox_contains(crossing, -88.0, -157.0) is True
    assert bbox_contains(crossing, -88.0, 0.0) is False
    assert bbox_intersects(crossing, parse_bbox("123,-89,124,-88")) is True
    assert bbox_intersects(crossing, parse_bbox("0,-89,1,-88")) is False

    full = parse_bbox("-180,-90,180,90")
    assert full.is_global() is True
    assert full.longitude_span() == 360.0
    assert bbox_intersects(full, crossing) is True

    with pytest.raises(AppError):
        parse_bbox("0,0,1")
    with pytest.raises(AppError):
        parse_bbox("0,0,1,91")
