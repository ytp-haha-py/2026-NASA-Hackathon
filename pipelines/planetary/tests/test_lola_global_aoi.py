"""Offline tests for global Moon LOLA AOI D01 extraction.

No test in this file may touch the public network. Synthetic lunar-like rasters are
created in pytest's temporary directory and labelled TEST-ONLY.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("PROJ_IGNORE_CELESTIAL_BODY", "YES")

from lola_global import (  # noqa: E402
    LUNAR_RADIUS_M,
    LolaGlobalError,
    LolaSource,
    _analysis_crs_for_bbox,
    _features_from_single_array,
    analyze_lola_aoi,
    validate_aoi,
)

MOON_GEOG_WKT = (
    'GEOGCS["Moon 2000",DATUM["D_Moon_2000",SPHEROID["Moon_2000_IAU_IAG",1737400,0]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
)


def write_synthetic_moon_geotiff(
    path: Path,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    width: int = 200,
    height: int = 200,
    base: float = 1000.0,
    grade_x: float = 0.0,
    grade_y: float = 0.0,
    nodata: float | None = float("nan"),
    lon_0_360: bool = True,
) -> Path:
    """TEST-ONLY synthetic lunar geographic DEM (not a NASA observation)."""
    res_x = (east - west) / width
    res_y = (north - south) / height
    transform = from_origin(west, north, res_x, res_y)
    xs = (np.arange(width) + 0.5) * res_x
    ys = (np.arange(height) + 0.5) * res_y
    # metres-like elevation plane in pixel space scaled to approximate degrees->metres effect
    # using simple linear gradient for deterministic features.
    z = base + grade_x * xs[None, :] + grade_y * ys[:, None]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs=MOON_GEOG_WKT,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(z.astype("float32"), 1)
    return path


def make_local_source(
    path: Path, *, west=0.0, south=-90.0, east=360.0, north=90.0, lon_convention="0_360"
) -> LolaSource:
    return LolaSource(
        source_id="test-moon-global",
        provider="TEST",
        title="TEST-ONLY synthetic lunar DEM",
        url=str(path),
        raster_url=str(path),
        product_page="test://local",
        license="test-only",
        license_url=None,
        citation="TEST-ONLY",
        version="test",
        checksum=None,
        crs_wkt=None,
        crs_proj4=f"+proj=longlat +R={LUNAR_RADIUS_M} +no_defs",
        native_resolution_m=None,
        native_ppd=None,
        nodata=float("nan"),
        extent={"west": west, "south": south, "east": east, "north": north},
        lon_convention=lon_convention,
        coverage_kind="global",
        is_cog=None,
        tiled=False,
        size_bytes=None,
        notes="synthetic",
    )


def test_validate_aoi_accepts_midlat_bbox():
    req = validate_aoi({"west": 20.0, "south": 10.0, "east": 21.0, "north": 11.0})
    assert req.body == "Moon"
    assert req.crossing_antimeridian is False
    assert len(req.pieces) == 1


def test_validate_aoi_antimeridian_splits():
    req = validate_aoi({"west": 179.5, "south": -0.5, "east": -179.5, "north": 0.5})
    assert req.crossing_antimeridian is True
    assert len(req.pieces) == 2
    assert req.pieces[0]["east"] == 180.0
    assert req.pieces[1]["west"] == -180.0


def test_validate_aoi_rejects_too_large():
    with pytest.raises(LolaGlobalError) as err:
        validate_aoi({"west": 0.0, "south": 0.0, "east": 30.0, "north": 30.0}, max_side_m=100_000)
    assert err.value.code == "aoi_too_large"


def test_validate_aoi_rejects_invalid_latitude():
    with pytest.raises(LolaGlobalError):
        validate_aoi({"west": 0.0, "south": -91.0, "east": 1.0, "north": 1.0})


def test_analysis_crs_polar_north():
    crs = _analysis_crs_for_bbox({"west": 10.0, "south": 70.0, "east": 12.0, "north": 75.0})
    assert "stere" in crs.to_proj4()
    assert "+lat_0=90" in crs.to_proj4()


def test_analysis_crs_polar_south():
    crs = _analysis_crs_for_bbox({"west": 10.0, "south": -80.0, "east": 12.0, "north": -70.0})
    assert "stere" in crs.to_proj4()
    assert "+lat_0=-90" in crs.to_proj4()


def test_features_from_single_array_all_nodata_returns_no_data():
    arr = np.full((20, 20), np.nan)
    out = _features_from_single_array(arr, 90.0)
    assert out["no_data"] is True
    assert out["features"] is None
    assert out["pixel_count"] == 400
    assert out["valid_pixel_count"] == 0


def test_analyze_aoi_oversized_pixel_budget(tmp_path: Path, monkeypatch):
    dem = write_synthetic_moon_geotiff(
        tmp_path / "moon.tif", west=20.0, south=10.0, east=21.0, north=11.0
    )
    source = make_local_source(
        dem, west=20.0, south=10.0, east=21.0, north=11.0, lon_convention="0_360"
    )
    monkeypatch.setattr("lola_global._choose_source_for_bbox", lambda bbox: source)
    monkeypatch.setattr("lola_global.DEFAULT_SOURCES", (source,))
    monkeypatch.setattr("lola_global.SOURCE_BY_ID", {source.source_id: source})
    result = analyze_lola_aoi(
        {"west": 20.0, "south": 10.0, "east": 21.0, "north": 11.0}, max_pixels=100
    )
    assert result.status in ("error", "no_data")
    assert result.errors
    assert result.errors[0]["code"] == "aoi_too_large"


def test_analyze_aoi_source_down_returns_explicit_error(tmp_path: Path, monkeypatch):
    missing = tmp_path / "missing.tif"
    source = make_local_source(missing)
    monkeypatch.setattr("lola_global._choose_source_for_bbox", lambda bbox: source)
    monkeypatch.setattr("lola_global.DEFAULT_SOURCES", (source,))
    monkeypatch.setattr("lola_global.SOURCE_BY_ID", {source.source_id: source})
    result = analyze_lola_aoi({"west": 20.0, "south": 10.0, "east": 21.0, "north": 11.0})
    assert result.no_data is True
    assert result.errors
    assert result.source is not None
    assert result.features is None


# ---------------------------------------------------------------------------
# Scientific consistency: source support vs analysis grid
# ---------------------------------------------------------------------------


def test_resolution_gate_classifies_lola128_polar_and_svs():
    from dataclasses import replace as dc_replace

    from lola_global import (
        MOON_LOLA128_GLOBAL,
        MOON_LOLA_SOUTH_80S_20MPP,
        MOON_SVS_LDEM_16,
        _resolution_assessment,
    )

    coarse_class, coarse_comparable, coarse_reason = _resolution_assessment(
        MOON_LOLA128_GLOBAL.native_resolution_m, 90.0
    )
    assert coarse_class == "upsampled_from_coarse_source"
    assert coarse_comparable is False
    assert "coarser" in coarse_reason
    assert "Earth D01" in coarse_reason

    polar_class, polar_comparable, _ = _resolution_assessment(
        MOON_LOLA_SOUTH_80S_20MPP.native_resolution_m, 90.0
    )
    assert polar_class == "native_or_downsampled"
    assert polar_comparable is False

    fallback_class, fallback_comparable, _ = _resolution_assessment(
        MOON_SVS_LDEM_16.native_resolution_m, 90.0
    )
    assert fallback_class == "upsampled_from_coarse_source"
    assert fallback_comparable is False

    # The SVS fallback is catalogued but is not in the enabled source selection until
    # its bare-array mapping and km->m conversion are exercised end to end.
    from lola_global import DEFAULT_SOURCES

    assert MOON_SVS_LDEM_16.source_id not in {source.source_id for source in DEFAULT_SOURCES}
    assert dc_replace(MOON_LOLA128_GLOBAL).source_id == "pgda-lola-ldem128-pa-pixel-202405"


def test_analyze_result_records_source_and_analysis_resolution(tmp_path: Path, monkeypatch):
    from dataclasses import replace as dc_replace

    dem = write_synthetic_moon_geotiff(
        tmp_path / "moon.tif", west=20.0, south=10.0, east=21.0, north=11.0
    )
    source = dc_replace(
        make_local_source(dem, west=20.0, south=10.0, east=21.0, north=11.0),
        native_resolution_m=473.802,
    )
    monkeypatch.setattr("lola_global._choose_source_for_bbox", lambda bbox: source)
    monkeypatch.setattr("lola_global.DEFAULT_SOURCES", (source,))
    monkeypatch.setattr("lola_global.SOURCE_BY_ID", {source.source_id: source})
    result = analyze_lola_aoi(
        {"west": 20.0, "south": 10.0, "east": 21.0, "north": 11.0},
        analysis_resolution_m=90.0,
    )
    payload = result.to_payload()
    assert payload["source_resolution_m"] == pytest.approx(473.802)
    assert payload["analysis_resolution_m"] == pytest.approx(90.0)
    assert payload["resolution_class"] == "upsampled_from_coarse_source"
    assert payload["cross_body_comparable"] is False
    assert "coarser" in payload["comparability_reason"]


def test_analyze_partial_coverage_keeps_real_features(tmp_path: Path, monkeypatch):
    dem = write_synthetic_moon_geotiff(
        tmp_path / "moon_partial.tif", west=20.0, south=10.0, east=21.0, north=11.0
    )
    with rasterio.open(dem, "r+") as src:
        data = src.read(1)
        data[:, :100] = np.nan
        src.write(data, 1)
    source = make_local_source(dem, west=20.0, south=10.0, east=21.0, north=11.0)
    monkeypatch.setattr("lola_global._choose_source_for_bbox", lambda bbox: source)
    monkeypatch.setattr("lola_global.DEFAULT_SOURCES", (source,))
    monkeypatch.setattr("lola_global.SOURCE_BY_ID", {source.source_id: source})
    result = analyze_lola_aoi(
        {"west": 20.0, "south": 10.0, "east": 21.0, "north": 11.0},
        analysis_resolution_m=90.0,
    )
    assert result.partial is True
    assert 0.0 < result.valid_fraction < 1.0
    assert result.invalid_pixel_count > 0
    assert result.features is not None
    assert set(result.features) == {
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
        "flat_terrain_fraction",
    }


def test_analyze_uses_requested_bbox_not_fixed_centre_window(tmp_path: Path, monkeypatch):
    dem = write_synthetic_moon_geotiff(
        tmp_path / "moon_wide.tif", west=20.0, south=10.0, east=22.0, north=12.0
    )
    source = make_local_source(dem, west=20.0, south=10.0, east=22.0, north=12.0)
    monkeypatch.setattr("lola_global._choose_source_for_bbox", lambda bbox: source)
    monkeypatch.setattr("lola_global.DEFAULT_SOURCES", (source,))
    monkeypatch.setattr("lola_global.SOURCE_BY_ID", {source.source_id: source})
    small = analyze_lola_aoi(
        {"west": 20.5, "south": 10.5, "east": 21.0, "north": 11.0},
        analysis_resolution_m=90.0,
    )
    large = analyze_lola_aoi(
        {"west": 20.0, "south": 10.0, "east": 22.0, "north": 12.0},
        analysis_resolution_m=90.0,
    )
    assert large.pixel_count > small.pixel_count * 3.0
    assert large.pixel_count > 1_000
    assert large.requested_bbox != small.requested_bbox
