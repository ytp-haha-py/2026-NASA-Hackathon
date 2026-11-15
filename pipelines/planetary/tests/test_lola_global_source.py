"""Offline tests for the global LOLA source adapter (``lola_global_source``).

No test here touches the network. A clearly named TEST-ONLY lunar geographic
GeoTIFF and a bare (un-georeferenced) SVS-shaped TIFF are written under pytest's
``tmp_path`` and never become observed data.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio
from lola_global_source import (
    BODY_RADIUS_M,
    GLOBAL_LOLA_SOURCES,
    PGDA_LDEM128,
    SVS_LDEM_4,
    SVS_LDEM_16,
    LolaGlobalError,
    estimate_window_bytes,
    inspect_raster,
    main,
    parse_source,
    read_bounded_window,
    source_by_id,
    source_catalog_text,
    window_for_centre,
)
from pyproj import CRS
from rasterio.transform import from_bounds

LUNAR_GEOGRAPHIC_WKT = CRS.from_proj4("+proj=longlat +R=1737400 +no_defs +type=crs").to_wkt()


def write_geographic(
    path: Path,
    values: np.ndarray,
    *,
    crs: str = LUNAR_GEOGRAPHIC_WKT,
    west: float = 0.0,
    south: float = -90.0,
    east: float = 360.0,
    north: float = 90.0,
) -> Path:
    height, width = values.shape
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": values.dtype.name,
        "crs": crs,
        "transform": from_bounds(west, south, east, north, width, height),
        "nodata": float("nan") if values.dtype.kind == "f" else None,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(values, 1)
    return path


def write_bare(path: Path, values: np.ndarray) -> Path:
    """Write an SVS-shaped TIFF: no CRS, no geotransform, no nodata tag."""
    profile = {
        "driver": "GTiff",
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": values.dtype.name,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(values, 1)
    return path


def test_catalogue_keeps_resolution_and_roles_honest() -> None:
    assert len(GLOBAL_LOLA_SOURCES) == len({source.source_id for source in GLOBAL_LOLA_SOURCES})
    assert source_by_id(PGDA_LDEM128.source_id).role == "primary"
    assert source_by_id(SVS_LDEM_4.source_id).role == "fallback"
    assert all(source.native_resolution_m > 90.0 for source in GLOBAL_LOLA_SOURCES)
    assert all(not source.checksum_published for source in GLOBAL_LOLA_SOURCES)
    assert PGDA_LDEM128.native_ppd == 128.0
    assert SVS_LDEM_4.native_ppd == 4.0
    assert SVS_LDEM_4.georeferenced is False
    assert SVS_LDEM_4.longitude_convention == "-180_to_180"
    assert SVS_LDEM_16.vertical_scale_to_m == 1000.0


def test_catalogue_text_is_offline_and_names_fallback_limits() -> None:
    text = source_catalog_text()
    assert "coarser than the 90 m" in text
    assert "no CRS" in text
    assert PGDA_LDEM128.url in text


def test_parse_source_accepts_https_vsicurl_and_local(tmp_path: Path) -> None:
    remote = parse_source(PGDA_LDEM128.url)
    assert remote.is_remote and remote.gdal_path.startswith("/vsicurl/https://")
    vsi = parse_source("/vsicurl/" + PGDA_LDEM128.url)
    assert vsi.is_remote and vsi.gdal_path == "/vsicurl/" + PGDA_LDEM128.url
    local = tmp_path / "dem.tif"
    assert parse_source(str(local)).gdal_path == str(local)
    with pytest.raises(LolaGlobalError) as error:
        parse_source("http://example.com/dem.tif")
    assert error.value.code == "insecure_remote_read"


def test_inspect_geographic_lunar_dem_and_read_metres(tmp_path: Path) -> None:
    width, height = 360, 180
    lat_centres = 90.0 - (np.arange(height) + 0.5)
    values = np.broadcast_to(1000.0 + 10.0 * lat_centres[:, None], (height, width)).astype(
        "float32"
    )
    path = write_geographic(tmp_path / "synthetic-lunar-geographic.tif", values)

    meta = inspect_raster(PGDA_LDEM128, url=str(path), allow_remote=False)
    assert meta.georeferenced is True
    assert meta.origin_lon == 0.0
    assert meta.resolution_deg == 1.0
    assert meta.native_resolution_m == pytest.approx(np.deg2rad(1.0) * BODY_RADIUS_M)
    assert meta.longitude_convention == "0_to_360"
    assert "source_coarser_than_90m" in meta.warnings
    assert "fallback_source_not_primary_archive" not in meta.warnings

    window = read_bounded_window(
        PGDA_LDEM128,
        url=str(path),
        center_lon=10.0,
        center_lat=10.0,
        window_m=500_000.0,
        max_window_bytes=4 * 1024 * 1024,
        allow_remote=False,
    )
    assert window.array is not None
    assert window.array.dtype == np.float32
    assert window.valid_fraction == 1.0
    assert window.window_sha256 is not None
    assert window.plan.window.col_off == 1
    assert window.plan.window.width == 18
    assert window.plan.window.row_off == 71
    assert window.plan.window.height == 18
    assert window.min_elevation_m == pytest.approx(1015.0, abs=1.0)
    assert window.max_elevation_m == pytest.approx(1185.0, abs=1.0)
    assert np.isfinite(window.array).all()
    # No value was zero-filled.
    assert not np.any(window.array == 0.0)


def test_bare_svs_grid_uses_documented_mapping_and_km(tmp_path: Path) -> None:
    width, height = 1440, 720
    lat_centres = 90.0 - ((np.arange(height) + 0.5) / 4.0)
    values_km = (-1.0 + 0.01 * lat_centres[:, None]) * np.ones((1, width), dtype="float32")
    path = write_bare(tmp_path / "synthetic-svs-bare.tif", values_km.astype("float32"))

    meta = inspect_raster(SVS_LDEM_4, url=str(path), allow_remote=False)
    assert meta.georeferenced is False
    assert meta.crs_wkt is None
    assert meta.origin_lon == -180.0
    assert meta.resolution_deg == 0.25
    assert "no_crs_using_documented_equirectangular_mapping" in meta.warnings
    assert "fallback_source_not_primary_archive" in meta.warnings

    window = read_bounded_window(
        SVS_LDEM_4,
        url=str(path),
        center_lon=10.0,
        center_lat=10.0,
        window_m=200_000.0,
        max_window_bytes=4 * 1024 * 1024,
        allow_remote=False,
    )
    # Column (10 + 180) / 0.25 = 760; row (90 - 10) / 0.25 = 320.
    assert window.plan.window.col_off == 746
    assert window.plan.window.row_off == 306
    assert window.plan.window.width == 28
    assert window.plan.window.height == 28
    assert window.min_elevation_m == pytest.approx(-933.75, abs=0.01)
    assert window.max_elevation_m == pytest.approx(-866.25, abs=0.01)


def test_uint16_svs_conversion_is_half_metre_plus_ten_km(tmp_path: Path) -> None:
    width, height = 1440, 720
    raw = np.full((height, width), 21000, dtype="uint16")  # 21000*0.5 - 10000 = 500 m
    path = write_bare(tmp_path / "synthetic-svs-uint.tif", raw)
    uint_source = replace(
        SVS_LDEM_4,
        source_id="svs-test-uint16",
        expected_dtype="uint16",
        vertical_scale_to_m=0.5,
        vertical_offset_m=-10_000.0,
        vertical_unit="half-metre_plus_10km_offset",
    )
    window = read_bounded_window(
        uint_source,
        url=str(path),
        center_lon=0.0,
        center_lat=0.0,
        window_m=200_000.0,
        max_window_bytes=4 * 1024 * 1024,
        allow_remote=False,
    )
    assert window.min_elevation_m == 500.0
    assert window.max_elevation_m == 500.0


def test_rejects_earth_crs(tmp_path: Path) -> None:
    values = np.zeros((18, 36), dtype="float32")
    path = write_geographic(
        tmp_path / "earth.tif",
        values,
        crs="EPSG:4326",
        west=-180.0,
        south=-90.0,
        east=180.0,
        north=90.0,
    )
    with pytest.raises(LolaGlobalError) as error:
        inspect_raster(PGDA_LDEM128, url=str(path), allow_remote=False)
    assert error.value.code == "not_a_lunar_crs"


def test_window_math_refuses_seams_and_poles(tmp_path: Path) -> None:
    values = np.zeros((720, 1440), dtype="float32")
    path = write_bare(tmp_path / "bare.tif", values)
    meta = inspect_raster(SVS_LDEM_4, url=str(path), allow_remote=False)

    with pytest.raises(LolaGlobalError) as error:
        window_for_centre(meta, 179.9, 0.0, window_m=500_000.0)
    assert error.value.code == "antimeridian_crossing"

    with pytest.raises(LolaGlobalError) as error:
        window_for_centre(meta, 0.0, 91.0, window_m=100_000.0)
    assert error.value.code == "invalid_latitude"


def test_dry_run_plans_without_reading_and_budget_is_enforced(tmp_path: Path) -> None:
    values = np.zeros((360, 720), dtype="float32")
    path = write_geographic(tmp_path / "lunar.tif", values)
    meta = inspect_raster(SVS_LDEM_16, url=str(path), allow_remote=False)
    assert meta.resolution_deg == 0.5

    plan = window_for_centre(meta, 10.0, 10.0, window_m=5_000.0)
    assert estimate_window_bytes(meta, plan) > 0

    dry = read_bounded_window(
        SVS_LDEM_16,
        url=str(path),
        center_lon=10.0,
        center_lat=10.0,
        window_m=500_000.0,
        dry_run=True,
        allow_remote=False,
    )
    assert dry.array is None
    assert dry.window_sha256 is None
    assert dry.from_dry_run is True

    with pytest.raises(LolaGlobalError) as error:
        read_bounded_window(
            SVS_LDEM_16,
            url=str(path),
            center_lon=10.0,
            center_lat=10.0,
            window_m=500_000.0,
            max_window_bytes=1024,
            allow_remote=False,
        )
    assert error.value.code == "window_too_large"


def test_cli_lists_sources_without_network(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list-sources"]) == 0
    captured = capsys.readouterr()
    assert PGDA_LDEM128.source_id in captured.out
    assert "fallback" in captured.out
