from __future__ import annotations

import math
from pathlib import Path

import pytest
from lola import (
    DEFAULT_MAX_WINDOW_BYTES,
    DEFAULT_ROI,
    LUNAR_SOUTH_POLAR_STEREOGRAPHIC_WKT,
    LolaImportError,
    bounds_contain,
    cache_paths,
    estimate_window_bytes,
    format_bytes,
    parse_source,
    product_catalog_text,
    resolve_center,
    validate_source_crs,
)
from pyproj import CRS
from rasterio.windows import Window


def test_parse_source_https_url() -> None:
    ref = parse_source("https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/dem.tif")
    assert ref.is_remote is True
    assert ref.url == "https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/dem.tif"
    assert ref.path is None
    assert ref.gdal_path.startswith("/vsicurl/https://")


def test_parse_source_vsicurl_url() -> None:
    raw = "/vsicurl/https://example.com/a.tif"
    ref = parse_source(raw)
    assert ref.is_remote is True
    assert ref.url == "https://example.com/a.tif"
    assert ref.gdal_path == raw


def test_parse_source_file_url() -> None:
    ref = parse_source("file:///tmp/lola/dem.tif")
    assert ref.is_remote is False
    assert ref.path == Path("/tmp/lola/dem.tif")
    assert ref.gdal_path == "/tmp/lola/dem.tif"


def test_parse_source_local_path(tmp_path: Path) -> None:
    path = tmp_path / "dem.tif"
    ref = parse_source(str(path))
    assert ref.is_remote is False
    assert ref.path == path
    assert ref.gdal_path == str(path)


def test_parse_source_rejects_unsupported_scheme() -> None:
    with pytest.raises(LolaImportError) as error:
        parse_source("ftp://example.com/dem.tif")
    assert error.value.code == "unsupported_source_scheme"


def test_parse_source_rejects_empty() -> None:
    with pytest.raises(LolaImportError) as error:
        parse_source("   ")
    assert error.value.code == "empty_source"


def test_resolve_center_default() -> None:
    assert resolve_center() == DEFAULT_ROI.center_xy


def test_resolve_center_explicit_xy() -> None:
    assert resolve_center(center_x=-2757.5, center_y=-6342.5) == (-2757.5, -6342.5)


def test_resolve_center_bbox() -> None:
    assert resolve_center(bbox=(-10.0, -20.0, 10.0, 20.0)) == (0.0, 0.0)


def test_resolve_center_rejects_both_xy_and_bbox() -> None:
    with pytest.raises(LolaImportError) as error:
        resolve_center(center_x=1.0, center_y=2.0, bbox=(0.0, 0.0, 1.0, 1.0))
    assert error.value.code == "ambiguous_center"


def test_resolve_center_rejects_incomplete_xy() -> None:
    with pytest.raises(LolaImportError) as error:
        resolve_center(center_x=1.0)
    assert error.value.code == "incomplete_center"


def test_resolve_center_rejects_inverted_bbox() -> None:
    with pytest.raises(LolaImportError) as error:
        resolve_center(bbox=(10.0, 20.0, -10.0, -20.0))
    assert error.value.code == "invalid_bbox"


def test_resolve_center_rejects_nan() -> None:
    with pytest.raises(LolaImportError) as error:
        resolve_center(center_x=math.nan, center_y=1.0)
    assert error.value.code == "invalid_center"


def test_validate_source_crs_accepts_real_lola_crs() -> None:
    warnings = validate_source_crs(
        CRS.from_wkt(LUNAR_SOUTH_POLAR_STEREOGRAPHIC_WKT), "synthetic-test"
    )
    assert warnings == []


def test_validate_source_crs_rejects_geographic() -> None:
    with pytest.raises(LolaImportError) as error:
        validate_source_crs(CRS.from_epsg(4326), "synthetic-test")
    assert error.value.code == "geographic_crs"


def test_validate_source_crs_rejects_non_metric() -> None:
    crs = CRS.from_proj4("+proj=stere +lat_0=-90 +lon_0=0 +R=1737400 +units=ft +no_defs")
    with pytest.raises(LolaImportError) as error:
        validate_source_crs(crs, "synthetic-test")
    assert error.value.code == "non_metric_crs"


def test_validate_source_crs_rejects_non_lunar_radius() -> None:
    with pytest.raises(LolaImportError) as error:
        validate_source_crs(CRS.from_epsg(32633), "synthetic-test")
    assert error.value.code == "not_a_lunar_crs"


def test_validate_source_crs_warns_on_non_polar_projection() -> None:
    crs = CRS.from_proj4("+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +R=1737400 +units=m +no_defs")
    warnings = validate_source_crs(crs, "synthetic-test")
    assert "source_projection_not_polar_stereographic" in warnings


def test_estimate_window_bytes_striped_real_layout() -> None:
    window = Window(col_off=1041, row_off=1261, width=414, height=414)
    estimate = estimate_window_bytes(
        window,
        width=3200,
        height=3200,
        block_shapes=((1, 3200),),
        is_tiled=False,
        dtype="float32",
        source_size_bytes=40_980_806,
    )
    assert estimate == 414 * 3200 * 4 + 128 * 1024


def test_estimate_window_bytes_tiled_layout() -> None:
    window = Window(col_off=1000, row_off=1000, width=300, height=300)
    estimate = estimate_window_bytes(
        window,
        width=3200,
        height=3200,
        block_shapes=((256, 256),),
        is_tiled=True,
        dtype="float32",
        source_size_bytes=40_980_806,
    )
    assert estimate == 9 * 256 * 256 * 4 + 128 * 1024


def test_estimate_window_bytes_is_capped_by_object_size() -> None:
    window = Window(col_off=0, row_off=0, width=100, height=100)
    estimate = estimate_window_bytes(
        window,
        width=3200,
        height=3200,
        block_shapes=((1, 3200),),
        is_tiled=False,
        dtype="float32",
        source_size_bytes=100_000,
    )
    assert estimate == 100_000 + 128 * 1024


def test_estimate_window_bytes_respects_default_budget() -> None:
    assert DEFAULT_MAX_WINDOW_BYTES == 30 * 1024 * 1024


def test_cache_paths_are_deterministic() -> None:
    first = cache_paths(Path("/tmp/cache"), "https://example.com/dem.tif", (1.0, 2.0))
    second = cache_paths(Path("/tmp/cache"), "https://example.com/dem.tif", (1.0, 2.0))
    assert first == second
    assert first[0].suffix == ".tif"
    assert first[1].suffix == ".json"


def test_cache_paths_change_with_centre() -> None:
    first = cache_paths(Path("/tmp/cache"), "https://example.com/dem.tif", (1.0, 2.0))
    second = cache_paths(Path("/tmp/cache"), "https://example.com/dem.tif", (1.1, 2.0))
    assert first != second


def test_bounds_contain() -> None:
    outer = (-10.0, -10.0, 10.0, 10.0)
    assert bounds_contain(outer, (-5.0, -5.0, 5.0, 5.0))
    assert not bounds_contain(outer, (-11.0, -5.0, 5.0, 5.0))


def test_format_bytes() -> None:
    assert format_bytes(None) == "unknown"
    assert "kB" in format_bytes(1234)
    assert "MB" in format_bytes(5_430_272)


def test_product_catalog_mentions_verified_sources() -> None:
    text = product_catalog_text()
    assert "https://pgda.gsfc.nasa.gov/products/78" in text
    assert "Site04_final_adj_5mpp_surf.tif" in text
    assert "40,980,806" in text
    assert "MOON_ME" in text
    assert "10.1016/j.pss.2020.105119" in text
    assert "3.3 GB" in text
