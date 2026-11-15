"""Offline B2 tests for full-bbox Mars MOLA AOI terrain analysis (#170 Goal B).

These tests cover ``pipelines/planetary/mola_aoi.py`` independently of the concurrent
B1 global reader. The shared required-path file
``test_mola_global_aoi.py`` also contains this B2 section; this mirror keeps the B2
slice verifiable while B1 is still editing its module.

No test here reaches the network. Real observed values live in the committed
``pipelines/planetary/evidence/mars_mola_aoi_evidence.json`` document; the reader-seam
tests use in-memory arrays only and never write a scientific artifact.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from analog_pipelines.planetary_coarse import PlanetaryCoarseError, body_geographic_crs
from mola_aoi import (
    ANALYSIS_RESOLUTION_M,
    AOI_MAX_AREA_KM2,
    AOI_MAX_SIDE_M,
    D01_FEATURE_IDS,
    PRESETS,
    MolaAoiError,
    MolaWindow,
    analyze_mola_aoi,
    compare_with_evidence,
    tiles_for_bbox,
    validate_mola_aoi,
)
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "pipelines/planetary/evidence/mars_mola_aoi_evidence.json"
JEZERO = PRESETS[0].bbox
HELLAS = PRESETS[1].bbox
OLYMPIA = PRESETS[2].bbox


def _fake_window(request, data: np.ndarray) -> MolaWindow:
    return MolaWindow(
        data=data,
        sources=(),
        source_crs_wkt='PROJCS["SIMPLE_CYLINDRICAL MARS"]',
        source_crs_name="SIMPLE_CYLINDRICAL MARS",
        source_resolution_m=463.0,
    )


def _reader_for(data_factory):
    def reader(request, *, allow_remote=False):
        return _fake_window(request, data_factory(request))

    return reader


def test_latitude_outside_megdr_coverage_is_rejected():
    with pytest.raises(MolaAoiError) as captured:
        validate_mola_aoi({"west": 0.0, "south": 88.5, "east": 1.0, "north": 89.0})
    assert captured.value.code == "unsupported_latitude"
    assert "88" in captured.value.message

    with pytest.raises(MolaAoiError) as captured:
        validate_mola_aoi({"west": 0.0, "south": -89.0, "east": 1.0, "north": -88.5})
    assert captured.value.code == "unsupported_latitude"


def test_antimeridian_crossing_is_rejected_not_silently_split():
    with pytest.raises(MolaAoiError) as captured:
        validate_mola_aoi({"west": 179.9, "south": 1.0, "east": -179.9, "north": 2.0})
    assert captured.value.code == "antimeridian_not_supported"

    with pytest.raises(MolaAoiError) as captured:
        validate_mola_aoi({"west": 350.0, "south": 1.0, "east": 10.0, "north": 2.0})
    assert captured.value.code == "antimeridian_not_supported"


def test_over_large_aoi_is_rejected_before_any_read():
    with pytest.raises(MolaAoiError) as captured:
        validate_mola_aoi({"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0})
    assert captured.value.code == "aoi_too_large"
    assert "59" in captured.value.message  # ~59 km per degree at the equator

    with pytest.raises(MolaAoiError) as captured:
        validate_mola_aoi(
            {
                "west": 0.0,
                "south": 0.0,
                "east": (AOI_MAX_SIDE_M / 1000.0) * 0.5 / 59.0,
                "north": 1.2,
            }
        )
    assert captured.value.code == "aoi_too_large"


def test_nodata_returns_explicit_no_data_without_zero_fill():
    request = validate_mola_aoi(HELLAS)
    payload = analyze_mola_aoi(
        HELLAS,
        label="all-nodata",
        window_reader=_reader_for(lambda req: np.full(req.grid_shape, np.nan, dtype="float64")),
    )
    assert payload["status"] == "no_data"
    assert payload["features"] is None
    assert payload["elevation_stats"] is None
    assert payload["partial"] is False
    assert payload["valid_fraction"] == 0.0
    assert payload["valid_pixels"] == 0
    assert payload["invalid_pixels"] == payload["pixel_count"] == request.nx * request.ny
    assert payload["valid_slope_pixels"] == 0
    assert "no_data" in payload["qa_flags"]
    assert any("D01 features are explicitly null" in item for item in payload["warnings"])


def test_partial_valid_fraction_is_reported_with_features_on_valid_cells():
    def factory(request):
        rows, cols = request.grid_shape
        elevation = np.tile(np.arange(cols, dtype="float64"), (rows, 1))
        elevation[:, :] *= ANALYSIS_RESOLUTION_M * 0.1
        elevation[0, :] = np.nan
        return elevation

    payload = analyze_mola_aoi(HELLAS, label="one-row-nodata", window_reader=_reader_for(factory))
    assert payload["status"] == "partial"
    assert payload["partial"] is True
    assert 0.0 < payload["valid_fraction"] < 1.0
    assert payload["invalid_pixels"] > 0
    assert payload["invalid_slope_pixels"] > 0
    assert "partial_valid_fraction" in payload["qa_flags"]
    assert set(payload["features"]) == set(D01_FEATURE_IDS)
    assert payload["features"]["slope_mean_deg"]["value"] == pytest.approx(
        math.degrees(math.atan(0.1)), rel=1e-6
    )
    assert payload["features"]["flat_terrain_fraction"]["value"] == 0.0


def test_source_failure_is_explicit_and_never_filled(monkeypatch):
    def broken(request, *, allow_remote=False):
        raise PlanetaryCoarseError("source_read_failed", "simulated GDAL timeout")

    monkeypatch.setattr("mola_aoi._read_mola_window", broken)
    with pytest.raises(MolaAoiError) as captured:
        analyze_mola_aoi(HELLAS, allow_remote=True)
    assert captured.value.code == "source_read_failed"
    assert "simulated GDAL timeout" in captured.value.message

    def generic(request, *, allow_remote=False):
        raise RuntimeError("connection reset")

    with pytest.raises(MolaAoiError) as captured:
        analyze_mola_aoi(HELLAS, allow_remote=True, window_reader=generic)
    assert captured.value.code == "source_read_failed"
    assert "RuntimeError" in captured.value.message


def test_remote_is_opt_in():
    with pytest.raises(MolaAoiError) as captured:
        analyze_mola_aoi(HELLAS)
    assert captured.value.code == "remote_read_disabled"
    assert "--allow-remote" in captured.value.message


def test_request_geometry_uses_full_bbox_and_dispersed_tiles():
    for bbox, expected_tile, band in (
        (JEZERO, "megt44n000hb", "low"),
        (HELLAS, "megt00n000hb", "mid"),
        (OLYMPIA, "megt88n180hb", "high"),
    ):
        request = validate_mola_aoi(bbox)
        assert request.latitude_band == band
        assert [tile.tile_id for tile in request.tiles] == [expected_tile]
        assert request.requested_bbox_lonlat == {
            "west": bbox["west"],
            "south": bbox["south"],
            "east": bbox["east"],
            "north": bbox["north"],
        }
        width = request.analysis_bbox_metric["east"] - request.analysis_bbox_metric["west"]
        height = request.analysis_bbox_metric["north"] - request.analysis_bbox_metric["south"]
        assert width >= request.width_m - 1e-6
        assert height >= request.height_m - 1e-6
        assert width == pytest.approx(request.nx * ANALYSIS_RESOLUTION_M)
        assert height == pytest.approx(request.ny * ANALYSIS_RESOLUTION_M)
        assert request.nx >= 3 and request.ny >= 3
        assert "3396000" in request.crs.to_string()
        assert "4326" not in request.crs.to_string()

        to_metric = Transformer.from_crs(body_geographic_crs("Mars"), request.crs, always_xy=True)
        xs, ys = to_metric.transform(
            [bbox["west"], bbox["east"], bbox["west"], bbox["east"]],
            [bbox["south"], bbox["south"], bbox["north"], bbox["north"]],
        )
        metric = request.analysis_bbox_metric
        assert min(xs) >= metric["west"] - 1e-6
        assert max(xs) <= metric["east"] + 1e-6
        assert min(ys) >= metric["south"] - 1e-6
        assert max(ys) <= metric["north"] + 1e-6


def test_tile_selection_handles_tile_and_band_edges():
    assert [tile.tile_id for tile in tiles_for_bbox(89.95, 10.0, 90.05, 10.1)] == [
        "megt44n000hb",
        "megt44n090hb",
    ]
    assert [tile.tile_id for tile in tiles_for_bbox(80.0, 43.95, 80.1, 44.05)] == [
        "megt44n000hb",
        "megt88n000hb",
    ]
    assert [tile.tile_id for tile in tiles_for_bbox(80.0, 10.0, 80.1, 10.1)] == ["megt44n000hb"]


def test_committed_evidence_has_three_dispersed_real_aois():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["body"] == "Mars"
    assert evidence["dimension"] == "D01"
    assert evidence["data_kind"] == "observed"
    assert evidence["synthetic"] is False
    assert evidence["limits"]["analysis_resolution_m"] == pytest.approx(463.0576672)
    assert len(evidence["aois"]) == 3
    assert [item["latitude_band"] for item in evidence["aois"]] == ["low", "mid", "high"]
    assert len({tuple(item["requested_bbox_lonlat"].values()) for item in evidence["aois"]}) == 3
    for item in evidence["aois"]:
        assert item["status"] == "complete"
        assert item["valid_fraction"] > 0.0
        assert item["body"] == "Mars"
        assert item["data_kind"] == "observed"
        assert item["synthetic"] is False
        assert item["coordinate_frame"] == "mars_planetocentric"
        assert item["support_quality"]["d01_90m_comparable"] is False
        assert item["support_quality"]["interpolated_bins_present"] is True
        assert item["resolution_m"] == pytest.approx(ANALYSIS_RESOLUTION_M)
        assert item["resolution"]["analysis_resolution_m"] == pytest.approx(463.0576672)
        assert "4326" not in item["crs"]["analysis"]["wkt"]
        assert "MOLA" in item["source"]["provider"]
        assert item["source"]["product_ids"]
        assert item["source"]["source_ids"] == [
            f"mars-mola-megdr-meg128-{product.removeprefix('meg').removesuffix('hb')}"
            for product in item["source"]["product_ids"]
        ]
        assert item["source_windows"]
        assert item["read_bytes_upper_bound"] > 0
        assert set(item["features"]) == set(D01_FEATURE_IDS)
        for feature in item["features"].values():
            assert math.isfinite(feature["value"])
            assert feature["method_version"] == "mars-mola-aoi-horn-463m-1.0.0"
            assert feature["comparability"] == "not_comparable_to_90m_d01"
        assert "not_90m_d01_comparable" in item["qa_flags"]
        assert item["checksum"].startswith("sha256:")
    assert "not global coverage" in evidence["scope"]
    assert "not a Jezero-only claim" in evidence["scope"]
    assert "not a 90 m same-support D01 reference" in evidence["scope"]


def test_result_checksum_is_deterministic_for_the_same_grid():
    def factory(request):
        rows, cols = request.grid_shape
        return np.tile(np.arange(cols, dtype="float64"), (rows, 1))

    first = analyze_mola_aoi(HELLAS, window_reader=_reader_for(factory))
    second = analyze_mola_aoi(HELLAS, window_reader=_reader_for(factory))
    assert first["checksum"] == second["checksum"]
    assert compare_with_evidence(first, second)


def test_area_limit_constant_is_a_bounded_aoi_not_a_survey():
    assert AOI_MAX_AREA_KM2 == 160.0
    assert AOI_MAX_SIDE_M == 12_500.0
