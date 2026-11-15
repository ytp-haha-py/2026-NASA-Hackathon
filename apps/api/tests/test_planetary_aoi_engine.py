"""Geometry contract tests for the full-AOI planetary engine (Issue #170, Goal D).

These tests cover the slice that is independent of raster providers:
Earth/Moon/Mars body semantics, signed and 0-360 longitude input, antimeridian
splitting, poles, the projected metric envelope, and explicit area/side/pixel
limits. The requested bbox must survive validation unchanged; no test accepts a
silently recentred or clipped centre window.

Run with:

    uv run pytest apps/api/tests/test_planetary_aoi_engine.py -q
"""

from __future__ import annotations

import json
import math

import pytest
from analog_api.domain.entities import BBox
from analog_api.domain.errors import AppError
from analog_api.domain.planetary_aoi_geometry import (
    BODY_DEFAULT_LIMITS,
    AoiLimits,
    validate_aoi_geometry,
)


def _error_code(call) -> str:
    with pytest.raises(AppError) as excinfo:
        call()
    return excinfo.value.code


def _generous_limits(**overrides) -> AoiLimits:
    values = {
        "min_side_m": 1.0,
        "max_side_m": 50_000_000.0,
        "max_area_km2": 1_000_000_000.0,
        "max_span_deg": 360.0,
        "max_pixels": 10_000_000_000,
    }
    values.update(overrides)
    return AoiLimits(**values)


# ---------------------------------------------------------------------------
# Earth: EPSG:4326, geodetic latitude
# ---------------------------------------------------------------------------


def test_earth_uses_wgs84_geodetic_and_keeps_the_full_bbox():
    geometry = validate_aoi_geometry(
        "Earth", {"west": 0.0, "south": 0.0, "east": 0.04, "north": 0.04}
    )

    assert geometry.body == "Earth"
    assert geometry.body_info.body_kind == "earth"
    assert geometry.body_info.coordinate_frame == "earth_wgs84"
    assert geometry.body_info.latitude_kind == "geodetic"
    assert geometry.body_info.earth_wgs84 is True
    assert geometry.body_info.bbox_crs == "EPSG:4326"
    assert geometry.latitude_kind == "geodetic"
    assert geometry.crs == "EPSG:4326"

    assert geometry.requested_bbox == {
        "west": 0.0,
        "south": 0.0,
        "east": 0.04,
        "north": 0.04,
    }
    assert geometry.normalized_bbox == geometry.requested_bbox
    assert geometry.shrink_applied is False
    assert geometry.analysis_extent == "full_requested_aoi"
    assert geometry.crosses_antimeridian is False
    assert geometry.is_full_longitude is False
    assert geometry.longitude_span_deg == pytest.approx(0.04)
    assert geometry.area_km2 == pytest.approx(17.23, abs=0.2)
    assert geometry.width_m == pytest.approx(4453, abs=10)
    assert geometry.height_m == pytest.approx(4423, abs=10)
    assert geometry.pixel_size_m == 90.0
    assert (geometry.pixels_x, geometry.pixels_y) == (50, 50)
    assert geometry.total_pixels == 2500
    assert geometry.geometry["type"] == "Polygon"
    assert geometry.contains(0.02, 0.02) is True
    assert geometry.contains(0.05, 0.02) is False


def test_earth_metric_envelope_is_local_and_ellipsoidal():
    geometry = validate_aoi_geometry("Earth", (0, 0, 0.04, 0.04))

    envelope = geometry.metric_envelope
    assert geometry.body_info.metric_shape == "ellipsoid"
    assert envelope.projection == "azimuthal_equidistant"
    assert envelope.crs.startswith("+proj=aeqd")
    assert "+ellps=WGS84" in envelope.crs
    assert envelope.point_count >= 6
    assert envelope.x_min_m < 0 < envelope.x_max_m
    assert envelope.y_min_m < 0 < envelope.y_max_m
    assert envelope.width_m == pytest.approx(geometry.width_m)
    assert geometry.envelope_area_km2 == pytest.approx(envelope.width_m * envelope.height_m / 1e6)


def test_earth_antimeridian_is_split_and_not_shrunk():
    requested = {"west": 179.98, "south": -0.0125, "east": -179.98, "north": 0.0125}
    geometry = validate_aoi_geometry("Earth", requested)

    assert geometry.crosses_antimeridian is True
    assert geometry.longitude_span_deg == pytest.approx(0.04)
    assert geometry.center_lon == pytest.approx(180.0)
    assert geometry.requested_bbox == requested
    assert geometry.shrink_applied is False
    assert geometry.analysis_extent == "full_requested_aoi"
    assert len(geometry.bbox_pieces) == 2
    assert geometry.bbox_pieces[0]["east"] == 180.0
    assert geometry.bbox_pieces[1]["west"] == -180.0
    assert geometry.bbox_pieces[0]["west"] == pytest.approx(179.98)
    assert geometry.bbox_pieces[1]["east"] == pytest.approx(-179.98)
    assert geometry.geometry["type"] == "MultiPolygon"
    assert len(geometry.geometry["coordinates"]) == 2
    assert geometry.contains(179.99, 0.0) is True
    assert geometry.contains(-179.99, 0.0) is True
    assert geometry.contains(0.0, 0.0) is False
    assert any("antimeridian" in warning for warning in geometry.warnings)


def test_earth_360_longitude_matches_signed_input():
    signed = validate_aoi_geometry(
        "Earth", {"west": -0.02, "south": -0.01, "east": 0.02, "north": 0.01}
    )
    east_360 = validate_aoi_geometry(
        "Earth",
        {"west": 359.98, "south": -0.01, "east": 0.02, "north": 0.01},
        lon_convention="360",
    )

    assert east_360.longitude_convention == "360"
    assert east_360.requested_bbox["west"] == pytest.approx(359.98)
    assert east_360.normalized_bbox == signed.normalized_bbox
    assert east_360.area_km2 == pytest.approx(signed.area_km2)
    assert east_360.width_m == pytest.approx(signed.width_m)
    assert east_360.height_m == pytest.approx(signed.height_m)
    assert east_360.geometry == signed.geometry
    assert east_360.bbox_0_360["west"] == pytest.approx(359.98)
    assert east_360.pixels_x == signed.pixels_x
    assert east_360.pixels_y == signed.pixels_y


def test_earth_pole_wedge_includes_the_pole_without_clamping_longitude():
    geometry = validate_aoi_geometry(
        "Earth", {"west": 0.0, "south": -90.0, "east": 60.0, "north": -89.99}
    )

    assert geometry.includes_south_pole is True
    assert geometry.includes_north_pole is False
    assert geometry.is_full_longitude is False
    assert geometry.longitude_span_deg == pytest.approx(60.0)
    assert geometry.normalized_bbox["west"] == 0.0
    assert geometry.normalized_bbox["east"] == 60.0
    assert geometry.contains(30.0, -89.995) is True
    assert geometry.contains(90.0, -89.995) is False
    # At the exact pole all longitudes coincide, so the pole point is included.
    assert geometry.contains(123.0, -90.0) is True
    assert any("south pole" in warning for warning in geometry.warnings)


def test_earth_full_longitude_polar_band_is_supported_when_span_allows_it():
    limits = AoiLimits(
        min_side_m=90.0,
        max_side_m=10_000.0,
        max_area_km2=25.0,
        max_span_deg=360.0,
        max_pixels=4_000_000,
    )
    geometry = validate_aoi_geometry(
        "Earth",
        {"west": -180.0, "south": -90.0, "east": 180.0, "north": -89.995},
        limits=limits,
    )

    assert geometry.is_full_longitude is True
    assert geometry.longitude_span_deg == pytest.approx(360.0)
    assert geometry.crosses_antimeridian is False
    assert len(geometry.bbox_pieces) == 1
    assert geometry.bbox_pieces[0]["west"] == -180.0
    assert geometry.bbox_pieces[0]["east"] == 180.0
    assert geometry.includes_south_pole is True
    assert geometry.area_km2 == pytest.approx(0.979, abs=0.05)
    assert geometry.width_m == pytest.approx(1117, abs=20)
    assert geometry.geometry["type"] == "Polygon"
    assert any("all longitudes" in warning for warning in geometry.warnings)


def test_global_earth_request_is_explicitly_rejected_not_shrunk():
    # Default span quota rejects the whole-world request before any geometry is fabricated.
    assert (
        _error_code(
            lambda: validate_aoi_geometry(
                "Earth", {"west": -180.0, "south": -90.0, "east": 180.0, "north": 90.0}
            )
        )
        == "bbox_too_wide"
    )
    # Even with a full-longitude allowance, a whole-world bbox cannot be represented in one
    # local metric envelope; the failure is explicit and the request is never clipped.
    assert (
        _error_code(
            lambda: validate_aoi_geometry(
                "Earth",
                {"west": -180.0, "south": -90.0, "east": 180.0, "north": 90.0},
                limits=_generous_limits(),
            )
        )
        == "coordinate_transform_failed"
    )


# ---------------------------------------------------------------------------
# Moon: planetocentric sphere
# ---------------------------------------------------------------------------


def test_moon_uses_planetocentric_body_frame_and_sphere():
    geometry = validate_aoi_geometry("Moon", (0.0, 0.0, 0.5, 0.5))

    assert geometry.body == "Moon"
    assert geometry.body_info.body_kind == "planetary"
    assert geometry.body_info.coordinate_frame == "lunar_planetocentric"
    assert geometry.body_info.latitude_kind == "planetocentric"
    assert geometry.body_info.earth_wgs84 is False
    assert geometry.body_info.equatorial_radius_m == 1_737_400.0
    assert geometry.body_info.polar_radius_m == 1_737_400.0
    assert geometry.body_info.flattening == 0.0
    assert geometry.body_info.metric_shape == "sphere"
    assert geometry.body_info.metric_radius_m == 1_737_400.0
    assert geometry.crs == "+proj=longlat +R=1737400 +no_defs"
    assert "EPSG:4326" not in geometry.metric_envelope.crs
    assert "+R=1737400" in geometry.metric_envelope.crs

    assert geometry.area_km2 == pytest.approx(201.14, abs=1.0)
    assert geometry.width_m == pytest.approx(15162, abs=20)
    assert geometry.height_m == pytest.approx(15162, abs=20)
    assert geometry.pixel_size_m == 250.0
    assert (geometry.pixels_x, geometry.pixels_y) == (61, 61)
    assert geometry.total_pixels == 3721
    assert geometry.contains(0.25, 0.25) is True
    assert geometry.contains(0.6, 0.25) is False


def test_moon_antimeridian_keeps_both_pieces():
    requested = {"west": 179.7, "south": -0.25, "east": -179.7, "north": 0.25}
    geometry = validate_aoi_geometry("Moon", requested)

    assert geometry.crosses_antimeridian is True
    assert geometry.longitude_span_deg == pytest.approx(0.6)
    assert geometry.center_lon == pytest.approx(180.0)
    assert geometry.requested_bbox == requested
    assert len(geometry.bbox_pieces) == 2
    assert geometry.geometry["type"] == "MultiPolygon"
    assert geometry.area_km2 == pytest.approx(241.37, abs=1.5)
    assert geometry.width_m == pytest.approx(18194, abs=30)
    assert geometry.height_m == pytest.approx(15162, abs=30)
    assert geometry.contains(179.8, 0.0) is True
    assert geometry.contains(-179.8, 0.0) is True
    assert geometry.contains(0.0, 0.0) is False


def test_moon_360_longitude_near_the_south_pole_matches_signed_input():
    requested_360 = {"west": 222.0, "south": -89.55, "east": 224.0, "north": -89.45}
    east_360 = validate_aoi_geometry("Moon", requested_360, lon_convention="360")
    signed = validate_aoi_geometry(
        "Moon", {"west": -138.0, "south": -89.55, "east": -136.0, "north": -89.45}
    )

    assert east_360.requested_bbox["west"] == pytest.approx(222.0)
    assert east_360.requested_bbox["east"] == pytest.approx(224.0)
    assert east_360.normalized_bbox == signed.normalized_bbox
    assert east_360.area_km2 == pytest.approx(signed.area_km2)
    assert east_360.geometry == signed.geometry
    assert east_360.includes_south_pole is False
    assert east_360.bbox_0_360["west"] == pytest.approx(222.0)
    assert east_360.bbox_0_360["east"] == pytest.approx(224.0)
    assert east_360.width_m == pytest.approx(556, abs=5)
    assert east_360.height_m == pytest.approx(3032, abs=10)
    assert (east_360.pixels_x, east_360.pixels_y) == (3, 13)


def test_moon_full_longitude_polar_cap_is_a_band_not_a_centre_window():
    limits = AoiLimits(
        min_side_m=250.0,
        max_side_m=25_000.0,
        max_area_km2=400.0,
        max_span_deg=360.0,
        max_pixels=4_000_000,
    )
    geometry = validate_aoi_geometry(
        "Moon",
        {"west": -180.0, "south": -90.0, "east": 180.0, "north": -89.9},
        limits=limits,
    )

    assert geometry.is_full_longitude is True
    assert geometry.includes_south_pole is True
    assert geometry.area_km2 == pytest.approx(28.87, abs=0.2)
    assert geometry.width_m == pytest.approx(6065, abs=20)
    assert geometry.height_m == pytest.approx(6065, abs=20)
    assert len(geometry.bbox_pieces) == 1
    assert geometry.geometry["type"] == "Polygon"
    # The longitude bounds stay full; a pole is not collapsed to a rectangle.
    assert geometry.bbox_0_360 == {
        "west": 0.0,
        "south": -90.0,
        "east": 360.0,
        "north": -89.9,
    }


# ---------------------------------------------------------------------------
# Mars: planetocentric frame, true ellipsoid metadata, spherical metric angles
# ---------------------------------------------------------------------------


def test_mars_declares_ellipsoid_but_keeps_planetocentric_metric_angles():
    geometry = validate_aoi_geometry("Mars", (77.0, 18.0, 77.1, 18.1))

    assert geometry.body == "Mars"
    assert geometry.body_info.coordinate_frame == "mars_planetocentric"
    assert geometry.body_info.latitude_kind == "planetocentric"
    assert geometry.body_info.earth_wgs84 is False
    assert geometry.body_info.equatorial_radius_m == 3_396_190.0
    assert geometry.body_info.polar_radius_m == 3_376_200.0
    assert geometry.body_info.flattening == pytest.approx(0.005886, abs=1e-6)
    assert geometry.body_info.metric_shape == "sphere"
    assert geometry.body_info.metric_radius_m == 3_396_190.0
    assert geometry.body_info.geographic_epsg_is_epsg_registry is False
    assert "EPSG:4326" not in geometry.metric_envelope.crs
    assert "+R=3396190" in geometry.metric_envelope.crs
    assert "ellps=WGS84" not in geometry.metric_envelope.crs

    assert geometry.area_km2 == pytest.approx(29.23, abs=0.5)
    assert geometry.width_m == pytest.approx(5637, abs=20)
    assert geometry.height_m == pytest.approx(5928, abs=20)
    assert (geometry.pixels_x, geometry.pixels_y) == (23, 24)
    assert geometry.total_pixels == 552
    assert geometry.contains(77.05, 18.05) is True


# ---------------------------------------------------------------------------
# Limits: explicit, never a silent shrink
# ---------------------------------------------------------------------------


def test_default_limits_reject_an_oversized_moon_aoi_explicitly():
    with pytest.raises(AppError) as excinfo:
        validate_aoi_geometry("Moon", (0.0, 0.0, 1.0, 1.0))

    error = excinfo.value
    assert error.code == "aoi_too_large"
    assert "not shrunk" in error.message
    assert error.details[0]["field"] == "area_km2"
    assert error.details[0]["observed"] == pytest.approx(890.73, abs=2.0)


def test_area_limit_is_independent_of_the_side_limit():
    area_limited = AoiLimits(
        min_side_m=250.0,
        max_side_m=25_000.0,
        max_area_km2=10.0,
        max_span_deg=180.0,
        max_pixels=4_000_000,
    )
    with pytest.raises(AppError) as excinfo:
        validate_aoi_geometry("Moon", (0.0, 0.0, 0.12, 0.12), limits=area_limited)
    assert excinfo.value.code == "aoi_too_large"
    assert excinfo.value.details[0]["field"] == "area_km2"

    side_limited = AoiLimits(
        min_side_m=250.0,
        max_side_m=2000.0,
        max_area_km2=400.0,
        max_span_deg=180.0,
        max_pixels=4_000_000,
    )
    with pytest.raises(AppError) as excinfo:
        validate_aoi_geometry("Moon", (0.0, 0.0, 0.1, 0.1), limits=side_limited)
    assert excinfo.value.code == "aoi_too_large"
    assert excinfo.value.details[0]["field"] == "max_side_m"


def test_pixel_budget_is_explicit_and_never_silently_downsampled():
    pixel_limited = AoiLimits(
        min_side_m=1.0,
        max_side_m=1_000_000.0,
        max_area_km2=1_000_000.0,
        max_span_deg=180.0,
        max_pixels=10,
    )
    with pytest.raises(AppError) as excinfo:
        validate_aoi_geometry("Moon", (0.0, 0.0, 0.05, 0.05), limits=pixel_limited)

    error = excinfo.value
    assert error.code == "aoi_too_many_pixels"
    assert error.details[0]["field"] == "total_pixels"
    assert error.details[0]["observed"] > 10
    assert "not downsampled" in error.message


def test_too_small_aoi_is_rejected_for_earth_and_moon():
    assert (
        _error_code(lambda: validate_aoi_geometry("Earth", (0.0, 0.0, 0.0001, 0.0001)))
        == "aoi_too_small"
    )
    assert (
        _error_code(lambda: validate_aoi_geometry("Moon", (0.0, 0.0, 0.0001, 0.0001)))
        == "aoi_too_small"
    )


def test_longitude_span_limit_is_explicit():
    with pytest.raises(AppError) as excinfo:
        validate_aoi_geometry("Moon", {"west": -100.0, "south": -1.0, "east": 100.0, "north": 1.0})
    assert excinfo.value.code == "bbox_too_wide"
    assert excinfo.value.details[0]["field"] == "longitude_span_deg"


# ---------------------------------------------------------------------------
# Invalid requests
# ---------------------------------------------------------------------------


def test_invalid_bbox_values_are_explicit():
    assert _error_code(lambda: validate_aoi_geometry("Earth", (0, 0, math.nan, 1))) == (
        "invalid_bbox"
    )
    assert _error_code(lambda: validate_aoi_geometry("Earth", (0, 0, math.inf, 1))) == (
        "invalid_bbox"
    )
    assert _error_code(lambda: validate_aoi_geometry("Earth", (0, 10, 1, -10))) == ("invalid_bbox")
    assert _error_code(lambda: validate_aoi_geometry("Earth", (0, 0, 1, 0))) == ("invalid_bbox")
    assert _error_code(lambda: validate_aoi_geometry("Mars", (0, 0, 1, 91))) == (
        "invalid_bbox_latitude"
    )
    assert _error_code(lambda: validate_aoi_geometry("Mars", (181, 0, 182, 1))) == (
        "invalid_bbox_longitude"
    )
    assert (
        _error_code(lambda: validate_aoi_geometry("Mars", (0, 0, 361, 1), lon_convention="360"))
        == "invalid_bbox_longitude"
    )
    assert _error_code(lambda: validate_aoi_geometry("Mars", (10, 0, 10, 1))) == (
        "invalid_bbox_longitude"
    )
    assert _error_code(
        lambda: validate_aoi_geometry("Mars", (0, 0, 1, 1), lon_convention="bad")
    ) == ("invalid_lon_convention")
    assert _error_code(lambda: validate_aoi_geometry("Mars", "0,0,1,1")) == "invalid_bbox"
    assert _error_code(lambda: validate_aoi_geometry("Venus", (0, 0, 1, 1))) == ("unsupported_body")


def test_latitude_kind_is_body_specific_and_never_silently_converted():
    with pytest.raises(AppError) as excinfo:
        validate_aoi_geometry("Mars", (0, 0, 0.1, 0.1), latitude_kind="planetographic")
    assert excinfo.value.code == "invalid_latitude_convention"
    assert "planetographic" in excinfo.value.message

    with pytest.raises(AppError) as excinfo:
        validate_aoi_geometry("Moon", (0, 0, 0.1, 0.1), latitude_kind="geodetic")
    assert excinfo.value.code == "invalid_latitude_convention"

    with pytest.raises(AppError) as excinfo:
        validate_aoi_geometry("Earth", (0, 0, 0.01, 0.01), latitude_kind="planetocentric")
    assert excinfo.value.code == "invalid_latitude_convention"

    # Explicit correct declarations are accepted.
    assert (
        validate_aoi_geometry(
            "Mars", (0, 0, 0.1, 0.1), latitude_kind="planetocentric"
        ).latitude_kind
        == "planetocentric"
    )
    assert (
        validate_aoi_geometry("Earth", (0, 0, 0.01, 0.01), latitude_kind="wgs84").latitude_kind
        == "geodetic"
    )


def test_positive_west_longitude_is_rejected_not_negated():
    with pytest.raises(AppError) as excinfo:
        validate_aoi_geometry("Moon", (0, 0, 0.1, 0.1), longitude_direction="positive_west")
    assert excinfo.value.code == "invalid_longitude_direction"


def test_invalid_limits_and_resolution_are_explicit():
    bad_side = AoiLimits(
        min_side_m=1000.0,
        max_side_m=1000.0,
        max_area_km2=400.0,
        max_span_deg=180.0,
        max_pixels=4_000_000,
    )
    assert (
        _error_code(lambda: validate_aoi_geometry("Moon", (0, 0, 0.1, 0.1), limits=bad_side))
        == "invalid_aoi_limits"
    )

    bad_span = AoiLimits(
        min_side_m=250.0,
        max_side_m=25_000.0,
        max_area_km2=400.0,
        max_span_deg=0.0,
        max_pixels=4_000_000,
    )
    assert (
        _error_code(lambda: validate_aoi_geometry("Moon", (0, 0, 0.1, 0.1), limits=bad_span))
        == "invalid_aoi_limits"
    )

    assert (
        _error_code(lambda: validate_aoi_geometry("Moon", (0, 0, 0.1, 0.1), pixel_size_m=0))
        == "invalid_analysis_resolution"
    )
    assert (
        _error_code(lambda: validate_aoi_geometry("Moon", (0, 0, 0.1, 0.1), pixel_size_m=-1))
        == "invalid_analysis_resolution"
    )


def test_bbox_object_with_attributes_is_accepted():
    geometry = validate_aoi_geometry("Moon", BBox(west=0.0, south=0.0, east=0.5, north=0.5))
    assert geometry.normalized_bbox == {
        "west": 0.0,
        "south": 0.0,
        "east": 0.5,
        "north": 0.5,
    }


# ---------------------------------------------------------------------------
# Payload contract: full request, no shrink
# ---------------------------------------------------------------------------


def test_payload_keeps_requested_bbox_and_declares_full_requested_aoi():
    geometry = validate_aoi_geometry(
        "Moon", {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85}
    )
    payload = geometry.as_payload()

    assert payload["contract_version"] == "aoi-engine-geometry-1.0.0"
    assert payload["requested_bbox"] == {
        "west": 19.85,
        "south": -10.15,
        "east": 20.15,
        "north": -9.85,
    }
    assert payload["normalized_bbox"] == payload["requested_bbox"]
    assert payload["analysis_bbox"] == payload["normalized_bbox"]
    assert payload["analysis_extent"] == "full_requested_aoi"
    assert payload["shrink_applied"] is False
    assert payload["body_info"]["coordinate_frame"] == "lunar_planetocentric"
    assert "mean-radius sphere" in payload["body_info"]["metric_shape_note"]
    assert payload["body_info"]["earth_wgs84"] is False
    assert payload["metric_envelope"]["crs"].startswith("+proj=aeqd")
    assert payload["limits"] == BODY_DEFAULT_LIMITS["Moon"].to_payload()
    assert any("no centre-window substitution" in note for note in payload["notes"])
    # The payload is JSON-serialisable for the API layer.
    json.dumps(payload)


def test_antimeridian_payload_records_pieces_without_a_centre_window():
    geometry = validate_aoi_geometry(
        "Moon", {"west": 179.7, "south": -0.25, "east": -179.7, "north": 0.25}
    )
    payload = geometry.as_payload()

    assert payload["crosses_antimeridian"] is True
    assert payload["requested_bbox"] == {
        "west": 179.7,
        "south": -0.25,
        "east": -179.7,
        "north": 0.25,
    }
    assert payload["analysis_extent"] == "full_requested_aoi"
    assert payload["shrink_applied"] is False
    assert len(payload["bbox_pieces"]) == 2
    assert payload["bbox_pieces"][0]["east"] == 180.0
    assert payload["bbox_pieces"][1]["west"] == -180.0
    assert any("antimeridian" in warning for warning in payload["warnings"])


def test_body_specific_metric_envelopes_are_not_earth_wgs84():
    earth = validate_aoi_geometry("Earth", (0, 0, 0.04, 0.04))
    moon = validate_aoi_geometry("Moon", (0, 0, 0.5, 0.5))
    mars = validate_aoi_geometry("Mars", (77.0, 18.0, 77.1, 18.1))

    assert "+ellps=WGS84" in earth.metric_envelope.crs
    assert "+R=1737400" in moon.metric_envelope.crs
    assert "+R=3396190" in mars.metric_envelope.crs
    assert earth.metric_envelope.crs != moon.metric_envelope.crs
    assert moon.metric_envelope.crs != mars.metric_envelope.crs
    assert moon.body_info.earth_wgs84 is False
    assert mars.body_info.earth_wgs84 is False
    assert "Earth" not in moon.body_info.coordinate_frame
    assert "Earth" not in mars.body_info.coordinate_frame


def test_geometry_layer_is_reexported_from_the_engine_module():
    """The geometry slice must remain reachable from the named engine module."""
    from analog_api.domain import planetary_aoi_engine

    assert planetary_aoi_engine.validate_aoi_geometry is validate_aoi_geometry
    assert planetary_aoi_engine.AoiGeometry is not None
    assert planetary_aoi_engine.AoiLimits is AoiLimits
