"""Synthetic known-plane tests only; fixtures are never persisted as real observations."""

import math

import numpy as np
import pytest
import rasterio
from analog_pipelines.features import ExtractionError, extract_features
from ctx_import import validate_source
from rasterio.transform import from_origin

MARS_CRS = "+proj=eqc +lat_ts=18.4663 +R=3396190 +units=m +type=crs"


def write_plane(path, crs=MARS_CRS):
    n = 150
    z = np.tile(np.arange(n, dtype="float32") * 2, (n, 1))
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=n,
        height=n,
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(-1500, 1500, 20, 20),
        nodata=-9999,
    ) as dst:
        dst.write(z, 1)
    return path


def test_mars_plane_uses_own_geographic_frame(tmp_path, monkeypatch):
    monkeypatch.delenv("PROJ_IGNORE_CELESTIAL_BODY", raising=False)
    source = write_plane(tmp_path / "TEST_ONLY_mars_plane.tif")
    with rasterio.open(source) as src:
        assert validate_source(src).ellipsoid.semi_major_metre == 3396190
    result = extract_features(source, center_xy=(0, 0), body="Mars")
    assert result.center_lon == pytest.approx(0)
    assert result.center_lat == pytest.approx(0)
    assert result.bbox_wgs84 is None
    assert result.valid_fraction == 1
    assert result.features["slope_mean_deg"] == pytest.approx(
        math.degrees(math.atan(0.1)), abs=0.01
    )
    # GDAL bilinear downsampling has subpixel kernel rounding at the source boundary.
    # Require residuals below 5 cm on this 300 m relief plane.
    assert result.features["roughness_std_m"] < 0.05
    with pytest.raises(ExtractionError, match="projected X/Y"):
        extract_features(source, center_lonlat=(0, 0), body="Mars")


def test_ctx_rejects_earth_crs(tmp_path):
    source = write_plane(tmp_path / "TEST_ONLY_earth.tif", "EPSG:32631")
    with rasterio.open(source) as src, pytest.raises(ValueError, match="not a Mars"):
        validate_source(src)
