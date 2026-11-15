"""Offline tests for the shared raster source adapter (Issue #170 Goal C).

Every raster in this module is written to ``tmp_path`` by the test itself. The values
are a synthetic fixture used only to prove window/scale/nodata bookkeeping; they are not
an observation and are never written to a manifest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_pipelines.raster_source import (
    DEFAULT_MAX_PIXELS,
    RASTER_SOURCE_SCHEMA_VERSION,
    RasterAccessKind,
    RasterSource,
    RasterSourceError,
    RasterWindowError,
    RemoteReadDisabledError,
    UnsupportedSourceError,
    inspect_source,
    open_raster_source,
    read_source_window,
)
from rasterio.transform import from_origin
from rasterio.windows import Window

SYNTHETIC_LABEL = "synthetic_raster_source_fixture_not_an_observation"
SCALE = 2.0
OFFSET = 100.0
NODATA = -9999.0
TRANSFORM = from_origin(10.0, 50.0, 0.01, 0.01)
WIDTH = 32
HEIGHT = 16


@pytest.fixture()
def synthetic_raster(tmp_path: Path) -> Path:
    path = tmp_path / f"{SYNTHETIC_LABEL}.tif"
    stored = np.arange(WIDTH * HEIGHT, dtype="float32").reshape(HEIGHT, WIDTH)
    stored[0, 0] = 0.0  # a valid zero must stay distinct from nodata
    stored[2, 5] = NODATA
    profile = {
        "driver": "GTiff",
        "width": WIDTH,
        "height": HEIGHT,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": TRANSFORM,
        "nodata": NODATA,
        "tiled": True,
        "blockxsize": 16,
        "blockysize": 16,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(stored, 1)
        dataset.scales = (SCALE,)
        dataset.offsets = (OFFSET,)
        dataset.units = ("m",)
    return path


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_local_metadata_reports_crs_resolution_nodata_and_declared_provenance(
    synthetic_raster: Path,
) -> None:
    source = RasterSource(
        synthetic_raster,
        dataset_id="synthetic-fixture-only",
        version="fixture-1.0.0",
        license="not an observation",
        license_url=None,
        declared_checksum=_sha256(synthetic_raster),
    )
    metadata = inspect_source(source)

    assert metadata.schema_version == RASTER_SOURCE_SCHEMA_VERSION
    assert metadata.access_kind is RasterAccessKind.LOCAL_FILE
    assert metadata.dataset_id == "synthetic-fixture-only"
    assert metadata.version == "fixture-1.0.0"
    assert metadata.license == "not an observation"
    assert metadata.checksum == _sha256(synthetic_raster)
    assert metadata.checksum_verified is False
    assert metadata.provenance_complete is True
    assert metadata.crs_epsg == 4326
    assert metadata.crs is not None
    assert metadata.is_geographic is True
    assert metadata.resolution == pytest.approx((0.01, 0.01))
    assert metadata.resolution_unit.lower() == "degree"
    assert metadata.resolution_m is None
    assert metadata.nodata == pytest.approx(NODATA)
    assert metadata.dtype == "float32"
    assert metadata.count == 1
    assert metadata.scale == pytest.approx(SCALE)
    assert metadata.offset == pytest.approx(OFFSET)
    assert metadata.tiled is True
    assert metadata.block_shape == (16, 16)
    assert metadata.size_bytes == synthetic_raster.stat().st_size
    assert metadata.bounds == pytest.approx((10.0, 49.84, 10.32, 50.0))
    payload = json.loads(json.dumps(metadata.to_payload()))
    assert payload["access_kind"] == "local_file"
    assert payload["crs_epsg"] == 4326


def test_missing_provenance_is_reported_as_missing_not_invented(synthetic_raster: Path) -> None:
    metadata = inspect_source(RasterSource(synthetic_raster))
    assert metadata.version is None
    assert metadata.license is None
    assert metadata.checksum is None
    assert metadata.provenance_complete is False


def test_local_checksum_can_be_verified_and_mismatch_is_rejected(
    synthetic_raster: Path,
) -> None:
    expected = _sha256(synthetic_raster)
    source = RasterSource(
        synthetic_raster,
        version="fixture-1.0.0",
        license="not an observation",
        declared_checksum=expected.removeprefix("sha256:"),
    )
    assert source.checksum() == expected
    metadata = source.inspect(verify_checksum=True)
    assert metadata.checksum == expected
    assert metadata.checksum_verified is True

    wrong = RasterSource(
        synthetic_raster,
        version="fixture-1.0.0",
        license="not an observation",
        declared_checksum="sha256:" + "0" * 64,
    )
    with pytest.raises(RasterSourceError) as error:
        wrong.inspect(verify_checksum=True)
    assert error.value.code == "checksum_mismatch"
    assert error.value.details["observed"] == expected


def test_invalid_declared_checksum_is_rejected_without_reading(synthetic_raster: Path) -> None:
    source = RasterSource(synthetic_raster, declared_checksum="md5:abc")
    with pytest.raises(UnsupportedSourceError) as error:
        source.inspect()
    assert error.value.code == "unsupported_checksum"


def test_window_read_applies_scale_offset_keeps_valid_zero_and_keeps_nodata_missing(
    synthetic_raster: Path,
) -> None:
    source = RasterSource(synthetic_raster)
    result = read_source_window(source, window=Window(1, 1, 3, 2))

    assert result.window == (1, 1, 3, 2)
    assert result.clipped is False
    assert result.data == pytest.approx(np.array([[166.0, 168.0, 170.0], [230.0, 232.0, 234.0]]))
    assert result.valid_mask.all()

    nodata_window = read_source_window(source, window=Window(4, 2, 3, 1))
    assert np.isnan(nodata_window.data[0, 1])
    assert nodata_window.valid_mask.tolist() == [[True, False, True]]
    assert nodata_window.valid_fraction == pytest.approx(2.0 / 3.0)

    zero_window = read_source_window(source, window=Window(0, 0, 1, 1))
    assert zero_window.data[0, 0] == pytest.approx(OFFSET)
    assert bool(zero_window.valid_mask[0, 0]) is True


def test_bbox_read_returns_the_expected_source_window_and_crs_bounds(
    synthetic_raster: Path,
) -> None:
    source = RasterSource(synthetic_raster)
    result = read_source_window(
        source,
        bbox={"west": 10.05, "south": 49.95, "east": 10.10, "north": 49.98},
        bbox_crs="EPSG:4326",
    )
    assert result.window == (5, 2, 5, 3)
    assert result.clipped is False
    assert result.bounds == pytest.approx((10.05, 49.95, 10.10, 49.98))
    assert result.crs_name == "WGS 84"


def test_pixel_window_and_bbox_are_mutually_exclusive(synthetic_raster: Path) -> None:
    with pytest.raises(RasterWindowError) as error:
        read_source_window(
            RasterSource(synthetic_raster),
            window=Window(0, 0, 1, 1),
            bbox=(10.0, 49.9, 10.1, 50.0),
        )
    assert error.value.code == "ambiguous_window"

    with pytest.raises(RasterWindowError) as error:
        read_source_window(RasterSource(synthetic_raster))
    assert error.value.code == "missing_window"


def test_window_is_clipped_to_the_source_and_can_be_rejected_instead(
    synthetic_raster: Path,
) -> None:
    source = RasterSource(synthetic_raster)
    clipped = read_source_window(source, window=Window(-5, -5, 10, 10))
    assert clipped.window == (0, 0, 5, 5)
    assert clipped.clipped is True
    assert clipped.requested_window == (-5, -5, 10, 10)

    with pytest.raises(RasterWindowError) as error:
        read_source_window(source, window=Window(-5, -5, 10, 10), clip=False)
    assert error.value.code == "window_clipped"


def test_window_outside_the_source_is_rejected(synthetic_raster: Path) -> None:
    with pytest.raises(RasterWindowError) as error:
        read_source_window(RasterSource(synthetic_raster), window=Window(100, 100, 2, 2))
    assert error.value.code == "window_outside_source"


def test_window_pixel_cap_is_enforced_before_reading(synthetic_raster: Path) -> None:
    with pytest.raises(RasterWindowError) as error:
        read_source_window(RasterSource(synthetic_raster), window=Window(0, 0, 3, 2), max_pixels=4)
    assert error.value.code == "window_too_large"
    assert error.value.details["pixels"] == 6

    with pytest.raises(RasterWindowError) as error:
        read_source_window(RasterSource(synthetic_raster, max_pixels=2), window=Window(0, 0, 2, 2))
    assert error.value.code == "window_too_large"
    assert DEFAULT_MAX_PIXELS > 2


def test_band_and_bbox_validation_are_explicit(synthetic_raster: Path) -> None:
    source = RasterSource(synthetic_raster)
    with pytest.raises(RasterWindowError) as error:
        read_source_window(source, window=Window(0, 0, 1, 1), band=2)
    assert error.value.code == "invalid_band"

    with pytest.raises(RasterWindowError) as error:
        read_source_window(source, bbox=(10.1, 49.9, 10.0, 50.0))
    assert error.value.code == "invalid_bbox"

    with pytest.raises(RasterWindowError) as error:
        read_source_window(source, bbox=(10.0, 49.9, 10.1, 50.0), bbox_crs="not-a-crs")
    assert error.value.code == "invalid_bbox_crs"


def test_reader_serves_multiple_windows_and_reports_a_conservative_byte_bound(
    synthetic_raster: Path,
) -> None:
    with open_raster_source(RasterSource(synthetic_raster)) as reader:
        first = reader.read_window(window=Window(0, 0, 16, 16))
        second = reader.read_window(window=Window(16, 0, 16, 16))
        assert reader.metadata.width == WIDTH
        assert first.data.shape == (16, 16)
        assert second.data.shape == (16, 16)
        assert first.read_bytes_upper_bound == 16 * 16 * 4 + 64 * 1024
        assert first.read_bytes_upper_bound >= first.data.nbytes
        assert first.metadata.checksum is None


def test_reader_cannot_be_used_before_it_is_open(synthetic_raster: Path) -> None:
    reader = RasterSource(synthetic_raster).open()
    with pytest.raises(RasterSourceError) as error:
        _ = reader.metadata
    assert error.value.code == "source_not_open"
    with pytest.raises(RasterSourceError) as error:
        reader.read_window(window=Window(0, 0, 1, 1))
    assert error.value.code == "source_not_open"


def test_staged_tile_is_a_local_read_with_staged_provenance(synthetic_raster: Path) -> None:
    source = RasterSource(synthetic_raster, kind=RasterAccessKind.STAGED_TILE)
    metadata = inspect_source(source)
    assert metadata.access_kind is RasterAccessKind.STAGED_TILE
    assert metadata.reference == str(synthetic_raster.resolve())
    result = read_source_window(source, window=Window(0, 0, 2, 2))
    assert result.valid_mask.all()


def test_missing_local_file_is_reported_as_not_found(tmp_path: Path) -> None:
    source = RasterSource(tmp_path / "absent.tif")
    with pytest.raises(RasterSourceError) as error:
        source.inspect()
    assert error.value.code == "source_not_found"


def test_plain_https_reads_are_disabled_until_explicitly_enabled() -> None:
    source = RasterSource("https://example.invalid/dem.tif")
    assert source.access_kind() is RasterAccessKind.HTTP_RANGE
    assert source.is_remote is True
    with pytest.raises(RemoteReadDisabledError) as error:
        source.reference()
    assert error.value.code == "remote_read_disabled"

    enabled = RasterSource("https://example.invalid/dem.tif", allow_remote=True)
    assert enabled.reference() == "/vsicurl/https://example.invalid/dem.tif"


def test_explicit_vsicurl_paths_are_recognised_and_guarded() -> None:
    source = RasterSource("/vsicurl/https://example.invalid/dem.tif", allow_remote=True)
    assert source.access_kind() is RasterAccessKind.VSICURL
    assert source.reference() == "/vsicurl/https://example.invalid/dem.tif"

    disabled = RasterSource("/vsicurl/https://example.invalid/dem.tif")
    with pytest.raises(RemoteReadDisabledError):
        disabled.reference()


def test_plain_http_and_unknown_schemes_are_rejected() -> None:
    for uri in (
        "http://example.invalid/dem.tif",
        "/vsicurl/http://example.invalid/dem.tif",
    ):
        with pytest.raises(UnsupportedSourceError) as error:
            RasterSource(uri, allow_remote=True).reference()
        assert error.value.code == "insecure_remote"

    for uri in ("s3://bucket/dem.tif", "gs://bucket/dem.tif", "ftp://host/dem.tif"):
        with pytest.raises(UnsupportedSourceError) as error:
            RasterSource(uri, allow_remote=True).access_kind()
        assert error.value.code == "unsupported_scheme"


def test_declared_source_kind_must_match_the_uri() -> None:
    with pytest.raises(UnsupportedSourceError) as error:
        RasterSource(
            "https://example.invalid/dem.tif",
            kind=RasterAccessKind.STAGED_TILE,
            allow_remote=True,
        ).access_kind()
    assert error.value.code == "source_kind_mismatch"

    with pytest.raises(UnsupportedSourceError) as error:
        RasterSource(
            "https://example.invalid/dem.tif",
            kind=RasterAccessKind.LOCAL_FILE,
            allow_remote=True,
        ).access_kind()
    assert error.value.code == "source_kind_mismatch"


def test_remote_checksums_must_be_declared_not_downloaded() -> None:
    source = RasterSource("https://example.invalid/dem.tif", allow_remote=True)
    with pytest.raises(RasterSourceError) as error:
        source.checksum()
    assert error.value.code == "checksum_unavailable_remote"
