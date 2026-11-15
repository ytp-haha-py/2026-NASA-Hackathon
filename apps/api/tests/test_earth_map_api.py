from __future__ import annotations

import math
from pathlib import Path

import pytest
from analog_api.adapters.db import Candidate, Dataset, FeatureObservation
from analog_api.config import Settings
from analog_api.domain.earth_map import (
    EARTH_BBOX_CRS,
    EARTH_MAP_FEATURES_VERSION,
    bbox_contains,
    bbox_intersects,
    build_map_features,
    earth_bbox_geometry,
    earth_point_geometry,
    parse_earth_bbox,
)
from analog_api.domain.errors import AppError
from analog_api.main import create_app
from analog_pipelines.coarse_index import CoarseIndexRepository
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
COARSE_QUERY_AVAILABLE = hasattr(CoarseIndexRepository, "query_bbox")
requires_coarse_query = pytest.mark.skipif(
    not COARSE_QUERY_AVAILABLE,
    reason="Agent D's CoarseIndexRepository.query_bbox is not present in this worktree yet.",
)

ATACAMA_BBOX = "-68.42,-23.52,-68.38,-23.48"
DEATH_VALLEY_BBOX = "-117.12,36.24,-117.08,36.26"
GRAND_CANYON_BBOX = "-112.3,36.0,-112.0,36.3"
SAHARA_BBOX = "9.99,23.99,10.40,24.37"
ALPS_BBOX = "10.44,46.46,10.56,46.52"
EMPTY_OCEAN_BBOX = "-140,-40,-138,-38"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("earth-map")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'earth-map.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=True,
        global_index_dir=str(ROOT / "pipelines/coarse/real-pilot/index"),
        aoi_inline=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _response(client: TestClient, **params):
    response = client.get("/api/v1/earth/map-features", params=params)
    return response


def _insert_on_demand_candidate(
    client: TestClient,
    *,
    location_id: str,
    west: float,
    east: float,
    south: float = -7.55,
    north: float = -7.45,
) -> None:
    app = client.app
    lat = (south + north) / 2.0
    lon = (west + east) / 2.0
    with app.state.session_factory() as session:
        dataset = session.get(Dataset, "copernicus-dem-glo90")
        if dataset is None:
            dataset = Dataset(
                dataset_id="copernicus-dem-glo90",
                provider="Test fixture only",
                product_id="fixture-dem",
                title="Fixture DEM for on-demand AOI tests",
                url="https://example.invalid/fixture",
                license="Test fixture",
                license_url="https://example.invalid/licence",
                access_timestamp="2026-11-15T00:00:00Z",
                source_crs="EPSG:4326",
                native_resolution_m=90.0,
                processing_version="terrain-metrics-1.0.0",
                checksum=None,
                source_kind="earth_dem",
                notes="Synthetic on-demand AOI fixture; never presented as observed coverage.",
            )
            session.add(dataset)
        session.add(
            Candidate(
                location_id=location_id,
                display_name=f"On-demand fixture AOI {location_id}",
                lat=lat,
                lon=lon,
                west=west,
                south=south,
                east=east,
                north=north,
                dataset_id="copernicus-dem-glo90",
                window_size_m=1000.0,
                analysis_resolution_m=90.0,
                crs="EPSG:4326",
                valid_fraction=1.0,
                quality_flags=["on_demand_aoi", "test_fixture"],
                notes="Synthetic test AOI, not a scientific observation.",
                data_kind="aoi",
                area_km2=1.0,
                origin="on_demand",
            )
        )
        session.add_all(
            [
                FeatureObservation(
                    candidate_id=location_id,
                    feature_id=feature_id,
                    value=value,
                    unit=unit,
                    method_version="terrain-metrics-1.0.0",
                    extraction_scale_m=90.0,
                    valid_fraction=1.0,
                    quality="verified",
                )
                for feature_id, value, unit in (
                    ("slope_mean_deg", 4.5, "degree"),
                    ("slope_p90_deg", 8.1, "degree"),
                    ("terrain_relief_p95_p05_m", 72.0, "meter"),
                    ("roughness_std_m", 18.0, "meter"),
                    ("flat_terrain_fraction", 0.61, "fraction"),
                )
            ]
        )
        session.commit()


# ---------------------------------------------------------------------------
# bbox parsing and geometry
# ---------------------------------------------------------------------------


def test_parse_earth_bbox_normal_antimeridian_poles_and_world():
    normal = parse_earth_bbox(ATACAMA_BBOX)
    assert (normal.west, normal.south, normal.east, normal.north) == (
        -68.42,
        -23.52,
        -68.38,
        -23.48,
    )
    assert normal.crosses_antimeridian is False
    assert math.isclose(normal.longitude_span(), 0.04)
    assert normal.is_global() is False
    assert normal.as_dict()["crs"] == EARTH_BBOX_CRS
    assert normal.as_dict()["source"] == "query"

    crossing = parse_earth_bbox("120,-10,-150,-5")
    assert crossing.crosses_antimeridian is True
    assert math.isclose(crossing.longitude_span(), 90.0)
    assert [(part.west, part.east) for part in crossing.split()] == [
        (120.0, 180.0),
        (-180.0, -150.0),
    ]

    polar = parse_earth_bbox("-180,-90,180,-85")
    assert polar.is_global() is True
    assert polar.crosses_antimeridian is False
    assert math.isclose(polar.longitude_span(), 360.0)
    assert bbox_contains(polar, -89.5, 123.0)
    assert bbox_contains(polar, -89.5, -45.0)

    world = parse_earth_bbox("-180,-90,180,90")
    assert world.is_global() is True
    assert (world.west, world.east) == (-180.0, 180.0)


def test_parse_earth_bbox_360_convention_preserves_the_180_edge():
    west_side = parse_earth_bbox("300,-10,60,-5", lon_convention="360")
    assert (west_side.west, west_side.east) == (-60.0, 60.0)
    assert west_side.crosses_antimeridian is False

    crossing = parse_earth_bbox("170,-10,190,-5", lon_convention="360")
    assert (crossing.west, crossing.east) == (170.0, -170.0)
    assert crossing.crosses_antimeridian is True
    assert math.isclose(crossing.longitude_span(), 20.0)

    world = parse_earth_bbox("0,-90,360,90", lon_convention="360")
    assert world.is_global() is True
    assert (world.west, world.east) == (-180.0, 180.0)

    east_edge = parse_earth_bbox("10,0,180,10")
    assert east_edge.east == 180.0
    assert east_edge.crosses_antimeridian is False
    assert math.isclose(east_edge.longitude_span(), 170.0)


@pytest.mark.parametrize(
    ("raw", "code", "convention"),
    [
        ("1,2,3", "invalid_bbox", "signed"),
        ("0,10,1,5", "invalid_bbox", "signed"),
        ("0,0,1,0", "invalid_bbox", "signed"),
        ("0,-91,1,0", "invalid_bbox_latitude", "signed"),
        ("0,0,1,91", "invalid_bbox_latitude", "signed"),
        ("200,0,201,1", "invalid_bbox_longitude", "signed"),
        ("0,0,0,1", "invalid_bbox_longitude", "signed"),
        ("nan,0,1,1", "invalid_bbox", "signed"),
        ("0,0,1,1", "invalid_lon_convention", "wrapped"),
        ("-10,0,10,1", "invalid_bbox_longitude", "360"),
    ],
)
def test_parse_earth_bbox_rejects_invalid_input(raw: str, code: str, convention: str):
    with pytest.raises(AppError) as error:
        parse_earth_bbox(raw, lon_convention=convention)
    assert error.value.code == code
    assert error.value.status_code == 422


def test_bbox_intersects_handles_antimeridian_and_poles():
    crossing = parse_earth_bbox("120,-10,-150,-5")
    assert bbox_intersects(crossing, parse_earth_bbox("170,-8,175,-6")) is True
    assert bbox_intersects(crossing, parse_earth_bbox("-160,-8,-155,-6")) is True
    assert bbox_intersects(crossing, parse_earth_bbox("0,-8,5,-6")) is False
    assert bbox_intersects(parse_earth_bbox("10,0,20,10"), parse_earth_bbox("-180,-90,180,90"))

    assert bbox_contains(crossing, -7.0, 170.0)
    assert bbox_contains(crossing, -7.0, -160.0)
    assert bbox_contains(crossing, -7.0, 0.0) is False
    # Latitude is checked independently of the longitude split.
    assert bbox_contains(crossing, -20.0, 170.0) is False


def test_earth_geojson_uses_lon_lat_and_multipolygon_for_antimeridian():
    crossing = parse_earth_bbox("120,-10,-150,-5")
    geometry = earth_bbox_geometry(crossing)
    assert geometry["type"] == "MultiPolygon"
    assert len(geometry["coordinates"]) == 2
    assert geometry["coordinates"][0][0][0] == [120.0, -10.0]
    assert geometry["coordinates"][1][0][0] == [-180.0, -10.0]

    normal = earth_bbox_geometry(parse_earth_bbox(ATACAMA_BBOX))
    assert normal["type"] == "Polygon"
    assert normal["coordinates"][0][0] == [-68.42, -23.52]

    point = earth_point_geometry(180.0, -23.5)
    assert point == {"type": "Point", "coordinates": [-180.0, -23.5]}


# ---------------------------------------------------------------------------
# domain assembly
# ---------------------------------------------------------------------------


def _candidate_fixture(location_id: str = "earth-atacama-desert") -> dict[str, object]:
    return {
        "location_id": location_id,
        "display_name": "Atacama fixture candidate",
        "lat": -23.5,
        "lon": -68.4,
        "west": -68.41,
        "south": -23.51,
        "east": -68.39,
        "north": -23.49,
        "crs": "EPSG:32719",
        "origin": "indexed",
        "dataset_id": "fixture-dem",
        "window_size_m": 1980.0,
        "analysis_resolution_m": 90.0,
        "valid_fraction": 1.0,
        "quality_flags": ["test_fixture"],
        "features": {
            "slope_mean_deg": {"method_version": "terrain-metrics-1.0.0"},
            "slope_p90_deg": {"method_version": "terrain-metrics-1.0.0"},
        },
    }


def _typed_area(area_id: str) -> dict[str, object]:
    return {
        "area_id": area_id,
        "body": "Earth",
        "bbox": {"west": -68.41, "south": -23.51, "east": -68.39, "north": -23.49},
        "crs": "EPSG:4326",
        "coordinate_frame": "earth_wgs84",
    }


def _typed_row(area_id: str, dimension: str = "D02") -> dict[str, object]:
    return {
        "area_id": area_id,
        "body": "Earth",
        "dimension_code": dimension,
        "feature_id": "mineral_spectral_detection",
        "state": "detected",
    }


def test_build_map_features_never_proximity_joins_and_flags_coarse_support():
    candidate = _candidate_fixture()
    area = _typed_area("earth-atacama-desert")
    nearby_other = _typed_area("earth-not-the-same-id")
    rows = [
        _typed_row("earth-atacama-desert"),
        _typed_row("earth-not-the-same-id"),
    ]
    coarse = {
        "candidate_id": "coarse-v1:abc",
        "region_id": "pilot-sahara",
        "crs": "EPSG:32633",
        "west": 10.0,
        "south": 24.0,
        "east": 10.05,
        "north": 24.05,
        "method_version": "coarse-terrain-250m-1.0.0",
        "qa_flags": ["real_geomorpho250"],
        "source_resolution_m": 250.0,
        "window_size_m": 5000.0,
    }
    result = build_map_features(
        query_bbox=parse_earth_bbox("-180,-90,180,90"),
        fine_candidates=[candidate],
        typed_areas=[area, nearby_other],
        typed_observations=rows,
        coarse_rows=[coarse],
        coarse_coverage={
            "status": "ready",
            "snapshot_id": "snap",
            "method_version": "coarse-terrain-250m-1.0.0",
        },
        coarse_total=1,
        limit=50,
        offset=0,
        data_version="dv-test",
    )
    by_id = {feature["feature_id"]: feature for feature in result.features}
    fine = by_id["fine:earth-atacama-desert"]
    typed_linked = by_id["typed:earth-atacama-desert"]
    typed_other = by_id["typed:earth-not-the-same-id"]
    coarse_feature = by_id["coarse:coarse-v1:abc"]

    assert fine["location_id"] == "earth-atacama-desert"
    assert fine["typed_area_id"] == "earth-atacama-desert"
    assert "D01" in fine["available_dimensions"]
    assert "D02" in fine["available_dimensions"]
    assert fine["analysis_crs"] == "EPSG:32719"
    assert fine["crs"] == EARTH_BBOX_CRS

    assert typed_linked["linked_candidate_id"] == "earth-atacama-desert"
    assert typed_other["linked_candidate_id"] is None
    assert "D01" not in typed_other["available_dimensions"]
    assert typed_other["dimension_status"]["D01"]["reason"] == "no_stored_observation"

    assert coarse_feature["data_level"] == "coarse"
    assert coarse_feature["support"] == "shortlist_250m_derived_not_fine"
    assert coarse_feature["support_label"] == "shortlist_250m_derived_not_fine"
    assert coarse_feature["availability"]["shortlist_only"] is True
    assert coarse_feature["crs"] == EARTH_BBOX_CRS
    assert coarse_feature["analysis_crs"] == "EPSG:32633"
    assert coarse_feature["bbox_crs"] == EARTH_BBOX_CRS
    assert coarse_feature["dimension_status"]["D01"]["status"] == "present"
    assert coarse_feature["dimension_status"]["D02"]["reason"] == "no_stored_observation"
    assert coarse_feature["detail_links"]["global_candidate"] == (
        "/api/v1/global/candidates/coarse-v1:abc"
    )

    assert result.counts_by_type == {
        "fine_candidate": 1,
        "on_demand_aoi": 0,
        "typed_area": 2,
        "coarse_candidate": 1,
    }
    assert result.total == 4
    assert result.coverage["coarse"]["status"] == "ready"


def test_build_map_features_dimension_availability_are_inverse():
    candidate = _candidate_fixture()
    area = _typed_area("earth-atacama-desert")
    rows = [_typed_row("earth-atacama-desert")]
    common = {
        "query_bbox": parse_earth_bbox(ATACAMA_BBOX),
        "fine_candidates": [candidate],
        "typed_areas": [area],
        "typed_observations": rows,
        "feature_type": "fine_candidate",
        "limit": 50,
        "offset": 0,
        "data_version": "dv-test",
    }
    present = build_map_features(**common, dimension="D02", availability="present")
    absent = build_map_features(**common, dimension="D02", availability="absent")
    assert present.total == 1
    assert absent.total == 0

    d01_present = build_map_features(**common, dimension="D01", availability="present")
    assert d01_present.total == 1
    d01_absent = build_map_features(**common, dimension="D01", availability="absent")
    assert d01_absent.total == 0


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


def test_fine_bbox_returns_real_candidate_with_contract_headers(client: TestClient):
    response = _response(client, bbox=ATACAMA_BBOX, feature_type="fine_candidate", limit=10)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["contract_version"] == EARTH_MAP_FEATURES_VERSION
    assert payload["coordinate_contract_version"] == "earth-wgs84-1.0.0"
    assert payload["body"] == "Earth"
    assert payload["bbox_crs"] == "EPSG:4326"
    assert payload["coordinate_system"] == "Earth WGS84"
    assert payload["total"] == 1
    assert payload["returned"] == 1
    assert payload["query"]["bbox_source"] == "query"
    assert response.headers["cache-control"] == ("public, max-age=60, stale-while-revalidate=300")
    assert response.headers["x-earth-map-contract-version"] == EARTH_MAP_FEATURES_VERSION
    assert response.headers["x-data-snapshot-id"] == payload["data_version"]

    feature = payload["features"][0]
    assert feature["feature_id"] == "fine:earth-atacama-desert"
    assert feature["feature_type"] == "fine_candidate"
    assert feature["data_level"] == "fine"
    assert feature["support"] == "fine_90m"
    assert feature["support_label"] == "fine_90m"
    assert feature["location_id"] == "earth-atacama-desert"
    assert feature["candidate_id"] == "earth-atacama-desert"
    assert feature["crs"] == "EPSG:4326"
    assert feature["analysis_crs"] == "EPSG:32719"
    assert feature["bbox_crs"] == "EPSG:4326"
    assert feature["bbox"]["crosses_antimeridian"] is False
    assert feature["geometry"]["type"] == "Point"
    assert feature["bbox_geometry"]["type"] == "Polygon"
    assert feature["detail_links"]["location"] == ("/api/v1/locations/earth-atacama-desert")
    assert feature["dimension_status"]["D01"]["status"] == "present"
    assert feature["dimension_status"]["D02"]["status"] == "present"
    assert feature["dimension_status"]["D02"]["value_origin"] == ("stored_typed_observation")
    assert set(feature["available_dimensions"]) == {
        "D01",
        "D02",
        "D03",
        "D04",
        "D05",
        "D06",
    }
    assert feature["missing_dimensions"] == []
    assert feature["availability"]["shortlist_only"] is False


def test_death_valley_bbox_returns_its_real_candidate(client: TestClient):
    response = _response(client, bbox=DEATH_VALLEY_BBOX, feature_type="fine_candidate", limit=10)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["features"][0]["location_id"] == "earth-death-valley"


def test_empty_ocean_bbox_is_an_explicit_empty_result(client: TestClient):
    response = _response(client, bbox=EMPTY_OCEAN_BBOX, limit=10)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 0
    assert payload["returned"] == 0
    assert payload["features"] == []
    assert payload["next_offset"] is None
    assert payload["counts_by_type"] == {
        "fine_candidate": 0,
        "on_demand_aoi": 0,
        "typed_area": 0,
        "coarse_candidate": 0,
    }
    assert payload["coverage"]["fine"]["candidate_count"] == 0
    assert payload["coverage"]["typed"]["area_count"] == 0
    assert any("explicit empty result" in note for note in payload["notes"])


def test_default_bbox_is_the_full_world_with_global_default_source(client: TestClient):
    response = _response(client, feature_type="fine_candidate", limit=1)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["query"]["bbox"] is None
    assert payload["query"]["bbox_source"] == "global_default"
    assert payload["bbox"]["west"] == -180.0
    assert payload["bbox"]["east"] == 180.0
    assert payload["bbox"]["crosses_antimeridian"] is False
    assert payload["bbox"]["longitude_span_deg"] == 360.0
    assert payload["total"] == 113
    assert payload["returned"] == 1
    assert payload["next_offset"] == 1


def test_antimeridian_viewport_combines_both_sides(client: TestClient):
    _insert_on_demand_candidate(
        client,
        location_id="aoi-test-east-side",
        west=179.90,
        east=179.99,
    )
    _insert_on_demand_candidate(
        client,
        location_id="aoi-test-west-side",
        west=-179.99,
        east=-179.90,
    )
    response = _response(
        client,
        bbox="179,-10,-179,-5",
        feature_type="on_demand_aoi",
        limit=10,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["bbox"]["crosses_antimeridian"] is True
    assert math.isclose(payload["bbox"]["longitude_span_deg"], 2.0)
    assert payload["total"] == 2
    assert {feature["location_id"] for feature in payload["features"]} == {
        "aoi-test-east-side",
        "aoi-test-west-side",
    }
    for feature in payload["features"]:
        assert feature["feature_type"] == "on_demand_aoi"
        assert feature["data_level"] == "fine"
        assert feature["area_id"] == feature["location_id"]
        assert feature["detail_links"]["area_features"] == (
            f"/api/v1/areas/{feature['location_id']}/features"
        )
        assert feature["bbox"]["crosses_antimeridian"] is False


def test_dimension_filters_and_availability_are_consistent(client: TestClient):
    d02_present = _response(
        client,
        bbox=ATACAMA_BBOX,
        feature_type="fine_candidate",
        dimension="D02",
        availability="present",
    )
    assert d02_present.status_code == 200, d02_present.text
    assert d02_present.json()["total"] == 1

    d02_absent = _response(
        client,
        bbox=ATACAMA_BBOX,
        feature_type="fine_candidate",
        dimension="D02",
        availability="absent",
    )
    assert d02_absent.status_code == 200, d02_absent.text
    assert d02_absent.json()["total"] == 0

    alps_d02_present = _response(
        client,
        bbox=ALPS_BBOX,
        feature_type="fine_candidate",
        dimension="D02",
        availability="present",
        limit=200,
    )
    assert alps_d02_present.status_code == 200, alps_d02_present.text
    assert alps_d02_present.json()["total"] == 0

    alps_d02_absent = _response(
        client,
        bbox=ALPS_BBOX,
        feature_type="fine_candidate",
        dimension="D02",
        availability="absent",
        limit=200,
    )
    assert alps_d02_absent.status_code == 200, alps_d02_absent.text
    assert alps_d02_absent.json()["total"] > 0

    d06_present = _response(
        client,
        bbox=ATACAMA_BBOX,
        feature_type="fine_candidate",
        dimension="D06",
        availability="present",
    )
    assert d06_present.status_code == 200, d06_present.text
    assert d06_present.json()["total"] == 1
    assert "D06" in d06_present.json()["features"][0]["available_dimensions"]

    d01_present = _response(
        client,
        bbox=ATACAMA_BBOX,
        feature_type="fine_candidate",
        dimension="D01",
        availability="present",
    )
    assert d01_present.status_code == 200, d01_present.text
    assert d01_present.json()["total"] == 1


def test_typed_area_feature_links_only_by_exact_area_id(client: TestClient):
    response = _response(client, bbox=ATACAMA_BBOX, feature_type="typed_area")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    feature = payload["features"][0]
    assert feature["feature_id"] == "typed:earth-atacama-desert"
    assert feature["feature_type"] == "typed_area"
    assert feature["data_level"] == "typed"
    assert feature["support"] == "stored_typed_subset"
    assert feature["support_label"] == "stored_typed_subset"
    assert feature["linked_candidate_id"] == "earth-atacama-desert"
    assert feature["relationship"] == "same_area_id_exact_join"
    assert feature["detail_links"]["typed_area"] == ("/api/v1/typed/areas/earth-atacama-desert")
    assert "D01" in feature["available_dimensions"]
    assert "D02" in feature["available_dimensions"]


def test_limit_offset_and_next_offset_are_bounded(client: TestClient):
    first = _response(client, feature_type="fine_candidate", limit=2)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["total"] == 113
    assert first_body["returned"] == 2
    assert first_body["limit"] == 2
    assert first_body["offset"] == 0
    assert first_body["next_offset"] == 2

    second = _response(client, feature_type="fine_candidate", limit=2, offset=2)
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["returned"] == 2
    assert second_body["next_offset"] == 4
    assert {item["feature_id"] for item in first_body["features"]}.isdisjoint(
        {item["feature_id"] for item in second_body["features"]}
    )

    tail = _response(client, feature_type="fine_candidate", limit=3, offset=111)
    assert tail.status_code == 200, tail.text
    tail_body = tail.json()
    assert tail_body["returned"] == 2
    assert tail_body["next_offset"] is None

    too_large = _response(client, feature_type="fine_candidate", limit=201)
    assert too_large.status_code == 422
    assert too_large.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"bbox": "1,2,3"}, "invalid_bbox"),
        ({"bbox": "0,10,1,5"}, "invalid_bbox"),
        ({"bbox": "0,-91,1,0"}, "invalid_bbox_latitude"),
        ({"bbox": "200,0,201,1"}, "invalid_bbox_longitude"),
        ({"bbox": "0,0,0,1"}, "invalid_bbox_longitude"),
        ({"dimension": "D99"}, "invalid_dimension"),
        ({"body": "Mars"}, "unsupported_body"),
        ({"feature_type": "planetary_reference"}, "validation_error"),
        ({"availability": "sometimes"}, "validation_error"),
        ({"bbox_lon_convention": "wrapped"}, "validation_error"),
    ],
)
def test_invalid_bbox_and_query_arguments_are_structured_422(
    client: TestClient, params: dict[str, str], code: str
):
    response = _response(client, **params)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code


@requires_coarse_query
def test_coarse_region_bbox_returns_shortlist_rows_only(client: TestClient):
    response = _response(
        client,
        bbox=SAHARA_BBOX,
        feature_type="coarse_candidate",
        data_level="coarse",
        limit=5,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 64
    assert payload["returned"] == 5
    assert payload["next_offset"] == 5
    assert payload["counts_by_type"]["coarse_candidate"] == 64
    assert payload["counts_by_type"]["fine_candidate"] == 0
    coarse = payload["coverage"]["coarse"]
    assert coarse["status"] == "ready"
    assert coarse["snapshot_id"] == "geomorpho250-real-pilot-20261115"
    assert coarse["snapshot"] == "geomorpho250-real-pilot-20261115"
    assert coarse["method_version"] == "coarse-terrain-250m-1.0.0"
    assert coarse["method"] == "coarse-terrain-250m-1.0.0"
    assert coarse["matched_count"] == 64
    assert coarse["truncated"] is True
    assert coarse["query_truncated"] is True
    assert coarse["row_count"] == 168
    assert any("coarse_query_limited" in note for note in payload["notes"])

    feature = payload["features"][0]
    assert feature["feature_id"].startswith("coarse:")
    assert feature["feature_type"] == "coarse_candidate"
    assert feature["data_level"] == "coarse"
    assert feature["support"] == "shortlist_250m_derived_not_fine"
    assert feature["support_label"] == "shortlist_250m_derived_not_fine"
    assert feature["shortlist_not_fine"] is True
    assert feature["candidate_id"].startswith("coarse-v1:")
    assert feature["region_id"] == "pilot-sahara"
    assert feature["crs"] == "EPSG:4326"
    assert feature["analysis_crs"] == "EPSG:32632"
    assert feature["bbox_crs"] == "EPSG:4326"
    assert feature["source_resolution_m"] == 250.0
    assert feature["dimension_status"]["D01"]["status"] == "present"
    assert feature["dimension_status"]["D02"]["status"] == "absent"
    assert feature["dimension_status"]["D02"]["reason"] == "no_stored_observation"
    assert feature["detail_links"]["global_candidate"] == (
        f"/api/v1/global/candidates/{feature['candidate_id']}"
    )


@requires_coarse_query
def test_coarse_offset_plus_limit_above_200_is_paged_without_repeats(client: TestClient):
    first = _response(
        client,
        feature_type="coarse_candidate",
        data_level="coarse",
        limit=50,
        offset=100,
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["total"] == 168
    assert first_body["returned"] == 50
    assert first_body["next_offset"] == 150
    assert first_body["coverage"]["coarse"]["offset"] == 100
    assert first_body["coverage"]["coarse"]["limit"] == 50

    second = _response(
        client,
        feature_type="coarse_candidate",
        data_level="coarse",
        limit=50,
        offset=150,
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["total"] == 168
    assert second_body["returned"] == 18
    assert second_body["next_offset"] is None
    first_ids = {feature["feature_id"] for feature in first_body["features"]}
    second_ids = {feature["feature_id"] for feature in second_body["features"]}
    assert first_ids.isdisjoint(second_ids)

    # offset + limit = 210 > the coarse repository's 200-row ceiling.  The
    # request must be clamped to a valid window rather than raising invalid_limit.
    capped = _response(
        client,
        feature_type="coarse_candidate",
        data_level="coarse",
        limit=50,
        offset=160,
    )
    assert capped.status_code == 200, capped.text
    capped_body = capped.json()
    assert capped_body["total"] == 168
    assert capped_body["returned"] == 8
    assert capped_body["next_offset"] is None
    assert capped_body["coverage"]["coarse"]["offset"] == 160
    assert capped_body["coverage"]["coarse"]["limit"] == 8


@requires_coarse_query
def test_mixed_fine_coarse_pages_are_disjoint(client: TestClient):
    def total(feature_type: str) -> int:
        response = _response(
            client,
            bbox=GRAND_CANYON_BBOX,
            feature_type=feature_type,
            limit=1,
        )
        assert response.status_code == 200, response.text
        return response.json()["total"]

    non_coarse_total = total("fine_candidate") + total("on_demand_aoi") + total("typed_area")
    assert non_coarse_total > 5

    first = _response(
        client,
        bbox=GRAND_CANYON_BBOX,
        feature_type="all",
        limit=non_coarse_total + 5,
        offset=0,
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert {"fine_candidate", "coarse_candidate"} <= {
        feature["feature_type"] for feature in first_body["features"]
    }
    assert first_body["next_offset"] == non_coarse_total + 5

    second = _response(
        client,
        bbox=GRAND_CANYON_BBOX,
        feature_type="all",
        limit=10,
        offset=non_coarse_total + 5,
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert all(feature["feature_type"] == "coarse_candidate" for feature in second_body["features"])
    first_ids = {feature["feature_id"] for feature in first_body["features"]}
    second_ids = {feature["feature_id"] for feature in second_body["features"]}
    assert first_ids.isdisjoint(second_ids)


@requires_coarse_query
def test_d01_dimension_includes_both_fine_and_coarse(client: TestClient):
    response = _response(
        client,
        bbox=GRAND_CANYON_BBOX,
        feature_type="all",
        dimension="D01",
        availability="present",
        limit=200,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["counts_by_type"]["fine_candidate"] == 9
    assert payload["counts_by_type"]["coarse_candidate"] == 16
    assert payload["total"] >= 25
    types = {feature["feature_type"] for feature in payload["features"]}
    assert {"fine_candidate", "coarse_candidate"} <= types


@requires_coarse_query
def test_d06_is_explicitly_absent_for_coarse_rows(client: TestClient):
    present = _response(
        client,
        bbox=SAHARA_BBOX,
        feature_type="coarse_candidate",
        dimension="D06",
        availability="present",
    )
    assert present.status_code == 200, present.text
    assert present.json()["total"] == 0
    assert present.json()["features"] == []

    absent = _response(
        client,
        bbox=SAHARA_BBOX,
        feature_type="coarse_candidate",
        dimension="D06",
        availability="absent",
        limit=200,
    )
    assert absent.status_code == 200, absent.text
    assert absent.json()["total"] == 64
    assert all(
        feature["dimension_status"]["D06"]["reason"] == "no_stored_observation"
        for feature in absent.json()["features"]
    )


def test_missing_coarse_index_is_explicit_and_does_not_fail_the_endpoint(
    tmp_path: Path,
):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'no-index.db'}",
        manifest_dir=str(tmp_path / "none-earth"),
        manifest_dir_planetary=str(tmp_path / "none-planetary"),
        manifest_dir_context=str(tmp_path / "none-context"),
        seed_include_synthetic=False,
        global_index_dir=str(tmp_path / "no-coarse-index"),
    )
    with TestClient(create_app(settings)) as test_client:
        response = _response(
            test_client,
            feature_type="coarse_candidate",
            data_level="coarse",
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 0
        assert payload["features"] == []
        coarse = payload["coverage"]["coarse"]
        assert coarse["status"] == "no_index"
        assert coarse["snapshot_id"] is None
        assert coarse["row_count"] == 0
        assert coarse["regions"] == []
        assert coarse["matched_count"] == 0
        assert any("no_index" in note for note in payload["notes"])
