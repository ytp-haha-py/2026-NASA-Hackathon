from __future__ import annotations

import math

import pytest
from analog_api.domain.errors import AppError
from analog_api.domain.planetary_aoi_engine import (
    CONTRACT_VERSION,
    DIMENSION_CODES,
    bbox_area_km2,
    bbox_side_lengths_m,
    build_engine_report,
    compute_cache_key,
    enforce_quota,
    normalize_bbox,
)


def test_normalize_bbox_accepts_360_convention_and_signed_output():
    # 350°E -> -10° in signed convention; span 350..10 in 0-360 is a 20° window
    # crossing the prime meridian, which is a normal non-antimeridian bbox once
    # normalized to signed coordinates (-10, 10).
    normalized = normalize_bbox(
        {"west": 350.0, "south": -10.0, "east": 10.0, "north": 10.0},
        body="Moon",
        lon_convention="360",
    )
    assert normalized["west"] == -10.0
    assert normalized["east"] == 10.0
    assert normalized["crosses_antimeridian"] is False
    assert normalized["input_lon_convention"] == "360"
    assert normalized["lon_convention"] == "signed"
    assert normalized["coordinate_frame"] == "lunar_planetocentric"
    assert normalized["crs"] == "+proj=longlat +R=1737400.0 +no_defs"
    assert len(normalized["pieces"]) == 1

    # A true antimeridian-crossing bbox in signed form remains crossing.
    crossing = normalize_bbox(
        {"west": 170.0, "south": -5.0, "east": 190.0, "north": 5.0},
        body="Moon",
        lon_convention="360",
    )
    assert crossing["west"] == 170.0
    assert crossing["east"] == -170.0
    assert crossing["crosses_antimeridian"] is True
    assert len(crossing["pieces"]) == 2


def test_normalize_bbox_supports_polar_latitudes_and_body_frames():
    moon = normalize_bbox(
        {"west": -20.0, "south": -89.9, "east": 20.0, "north": -89.0}, body="Moon"
    )
    mars = normalize_bbox({"west": 10.0, "south": 80.0, "east": 40.0, "north": 89.5}, body="Mars")
    earth = normalize_bbox({"west": 20.0, "south": 10.0, "east": 21.0, "north": 11.0}, body="Earth")
    assert moon["coordinate_frame"] == "lunar_planetocentric"
    assert mars["coordinate_frame"] == "mars_planetocentric"
    assert earth["coordinate_frame"] == "wgs84_geographic"
    assert earth["crs"] == "EPSG:4326"


def test_normalize_bbox_rejects_invalid_contracts():
    with pytest.raises(AppError) as bad_lat:
        normalize_bbox({"west": 0, "south": 10, "east": 1, "north": 10}, body="Moon")
    assert bad_lat.value.code == "invalid_bbox"

    with pytest.raises(AppError) as bad_conv:
        normalize_bbox(
            {"west": 0, "south": 0, "east": 1, "north": 1}, body="Moon", lon_convention="bad"
        )
    assert bad_conv.value.code == "invalid_longitude_convention"

    with pytest.raises(AppError) as bad_span:
        normalize_bbox({"west": -170, "south": 0, "east": 170, "north": 1}, body="Moon")
    assert bad_span.value.code == "bbox_too_wide"


def test_footprint_area_and_quota_are_body_aware_and_explicit():
    bbox = {"west": 10.0, "south": 0.0, "east": 11.0, "north": 1.0}
    moon_area = bbox_area_km2("Moon", bbox, False)
    mars_area = bbox_area_km2("Mars", bbox, False)
    earth_area = bbox_area_km2("Earth", bbox, False)
    assert moon_area > 0 and mars_area > 0 and earth_area > 0
    assert mars_area > moon_area  # larger body radius

    # Small ~5 km bbox for quota/pixel accounting (whole-bbox analysis contract).
    small = {"west": 20.0, "south": 0.0, "east": 20.165, "north": 0.165}
    small_area = bbox_area_km2("Moon", small, False)
    width_m, height_m = bbox_side_lengths_m("Moon", small, False, 0.165)
    quota = enforce_quota(
        body="Moon",
        bbox=small,
        crosses_antimeridian=False,
        span_deg=0.165,
        area_km2=small_area,
        resolution_m=250.0,
    )
    assert quota["width_m"] == pytest.approx(width_m)
    assert quota["height_m"] == pytest.approx(height_m)
    assert quota["pixel_estimate"] == math.ceil(width_m / 250.0) * math.ceil(height_m / 250.0)

    with pytest.raises(AppError) as too_large:
        enforce_quota(
            body="Moon",
            bbox={"west": 0.0, "south": 0.0, "east": 50.0, "north": 50.0},
            crosses_antimeridian=False,
            span_deg=50.0,
            area_km2=2_000_000.0,
            resolution_m=250.0,
        )
    assert too_large.value.code == "aoi_too_large"


def test_cache_key_is_deterministic_and_normalization_sensitive():
    a = compute_cache_key(
        body="Moon",
        bbox={"west": -10.0, "south": -10.0, "east": 10.0, "north": 10.0},
        requested_features=["coarse_slope_mean_deg"],
        lon_convention="360",
    )
    b = compute_cache_key(
        body="Moon",
        bbox={"west": -10.0, "south": -10.0, "east": 10.0, "north": 10.0},
        requested_features=["coarse_slope_mean_deg"],
        lon_convention="360",
    )
    c = compute_cache_key(
        body="Moon",
        bbox={"west": -11.0, "south": -10.0, "east": 10.0, "north": 10.0},
        requested_features=["coarse_slope_mean_deg"],
        lon_convention="360",
    )
    assert a == b
    assert a != c


def test_build_engine_report_never_silently_shrinks_and_marks_d02_d06_explicit():
    requested = {"west": 10.0, "south": 0.0, "east": 11.0, "north": 1.0}
    analysis = {"west": 10.1, "south": 0.1, "east": 10.9, "north": 0.9}
    report = build_engine_report(
        job_status="completed",
        error_code=None,
        error_message=None,
        request={"body": "Moon", "bbox": requested, "coordinate_frame": "lunar_planetocentric"},
        result={
            "body": "Moon",
            "coordinate_frame": "lunar_planetocentric",
            "bbox": analysis,
            "requested_bbox": requested,
            "analysis_kind": "selected_cell_union",
            "method_version": "coarse-planetary-dem-horn-1.0.0",
            "features": {"coarse_slope_mean_deg": 1.0},
            "dimension_support": {
                "D02": {"status": "unsupported_for_on_demand_analysis", "reason": "no source"}
            },
            "sources": [{"dataset_id": "ds1"}],
            "coverage_note": "partial",
        },
        area=None,
        observations=[],
    )
    assert report["requested_bbox"] == requested
    assert report["analysis_bbox"] == analysis
    assert report["full_aoi"] is False
    assert report["analysis_scope"] == "selected_cell_union"
    assert report["dimensions"]["D01"]["status"] == "available"
    for dim in DIMENSION_CODES:
        assert dim in report["dimensions"]
    for dim in ("D02", "D03", "D04", "D05", "D06"):
        assert report["dimensions"][dim]["status"] in {
            "no_coverage",
            "not_indexed",
            "unsupported",
            "evidence",
        }
        assert (
            report["dimensions"][dim]["features"] == []
            or report["dimensions"][dim]["status"] == "evidence"
        )
        assert (
            "0" not in str(report["dimensions"][dim].get("value_origin"))
            or report["dimensions"][dim]["value_origin"] != "0"
        )


def test_build_engine_report_failed_and_pending_are_explicit():
    failed = build_engine_report(
        job_status="failed",
        error_code="out_of_coverage",
        error_message="No committed analysis coverage",
        request={"body": "Moon", "bbox": {"west": 0, "south": 0, "east": 1, "north": 1}},
        result=None,
        area=None,
        observations=[],
    )
    assert failed["analysis_scope"] == "failed"
    assert failed["full_aoi"] is None
    assert failed["coverage"]["status"] == "no_coverage"
    assert failed["dimensions"]["D01"]["status"] == "no_coverage"
    assert CONTRACT_VERSION == "planetary-aoi-engine-1.0.0"

    pending = build_engine_report(
        job_status="queued",
        error_code=None,
        error_message=None,
        request={"body": "Moon", "bbox": {"west": 0, "south": 0, "east": 1, "north": 1}},
        result=None,
        area=None,
        observations=[],
    )
    assert pending["analysis_scope"] == "pending"
    assert pending["coverage"]["status"] == "pending"
    assert pending["dimensions"]["D01"]["status"] == "pending"
