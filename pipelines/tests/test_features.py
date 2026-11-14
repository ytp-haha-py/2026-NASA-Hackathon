from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_api.domain.features import METHOD_VERSION
from analog_pipelines.features import (
    ExtractionError,
    angle_from_gradient,
    extract_features,
    horn_slope_degrees,
    make_observations,
    terrain_metrics,
)
from rasterio.transform import from_origin

N = 100
CELL = 90.0


def write_dem(
    path: Path,
    *,
    crs: str,
    transform,
    z: np.ndarray,
    nodata: float | None = None,
) -> Path:
    profile = {
        "driver": "GTiff",
        "height": z.shape[0],
        "width": z.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(z.astype("float32"), 1)
    return path


def plane_z(grade: float = 0.1, size: int = N, cell: float = CELL) -> np.ndarray:
    return np.tile(np.arange(size)[None, :] * cell * grade, (size, 1))


def test_metrics_on_known_plane():
    metrics = terrain_metrics(plane_z(0.1), CELL)
    assert metrics["slope_mean_deg"] == pytest.approx(angle_from_gradient(0.1), abs=0.01)
    assert metrics["slope_p90_deg"] == pytest.approx(angle_from_gradient(0.1), abs=0.01)
    assert metrics["terrain_relief_p95_p05_m"] == pytest.approx(801.9, abs=3.0)
    assert metrics["roughness_std_m"] == pytest.approx(0.0, abs=1e-9)


def test_metrics_reject_insufficient_valid_data():
    z = plane_z()
    z[: int(N * 0.5), :] = np.nan
    with pytest.raises(ExtractionError) as error:
        terrain_metrics(z, CELL)
    assert error.value.code == "insufficient_valid_data"


def test_horn_slope_marks_incomplete_neighbourhoods_nan():
    z = plane_z()
    z[0, 0] = np.nan
    slope = horn_slope_degrees(z, CELL)
    assert np.isnan(slope[0, 0])
    assert np.isnan(slope[0, 1])
    assert np.isnan(slope[1, 1])
    assert np.isfinite(slope[2, 2])


def test_roughness_increases_with_noise():
    z = plane_z() + 4.0 * np.sin(np.arange(N)[None, :] / 3.0)
    metrics = terrain_metrics(z, CELL)
    assert metrics["roughness_std_m"] > 1.0
    assert metrics["slope_mean_deg"] > angle_from_gradient(0.1)


def test_extract_features_projected_metric_source(tmp_path: Path):
    z = plane_z()
    path = write_dem(
        tmp_path / "plane.tif",
        crs="EPSG:32633",
        transform=from_origin(500000, 4000000, CELL, CELL),
        z=z,
    )
    result = extract_features(
        path,
        center_xy=(500000 + CELL * N / 2, 4000000 - CELL * N / 2),
        source_label="unit-test-plane",
        body="Earth",
    )
    assert result.window_size_m == 1980.0
    assert result.resolution_m == 90.0
    assert result.analysis_crs == "EPSG:32633"
    assert result.features["slope_mean_deg"] == pytest.approx(angle_from_gradient(0.1), abs=0.05)
    assert result.valid_fraction == 1.0
    assert result.window_checksum.startswith("sha256:")
    assert result.bbox_wgs84 is not None
    assert set(result.features) == {
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
    }


def test_extract_features_geographic_source_is_reprojected_to_metric_grid(tmp_path: Path):
    col = np.arange(120)[None, :]
    z = np.tile(col * 0.1, (120, 1))  # absolute values are arbitrary metres
    path = write_dem(
        tmp_path / "geo.tif",
        crs="EPSG:4326",
        transform=from_origin(15.0, 45.2, 0.001, 0.001),
        z=z,
    )
    result = extract_features(
        path,
        center_lonlat=(15.06, 45.14),
        source_label="unit-test-geographic",
        body="Earth",
    )
    assert result.analysis_crs.startswith("EPSG:326")
    assert result.center_lon == pytest.approx(15.06)
    assert result.center_lat == pytest.approx(45.14)
    assert result.features["slope_mean_deg"] > 0
    assert result.valid_fraction == 1.0


def test_extract_features_rejects_missing_crs(tmp_path: Path):
    path = tmp_path / "no_crs.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="float32",
    ) as dst:
        dst.write(np.zeros((10, 10), dtype="float32"), 1)
    with pytest.raises(ExtractionError) as error:
        extract_features(path, center_lonlat=(0.0, 0.0))
    assert error.value.code == "missing_crs"


def test_extract_features_honours_nodata(tmp_path: Path):
    z = plane_z()
    z[:, : int(N * 0.4)] = -9999.0
    path = write_dem(
        tmp_path / "nodata.tif",
        crs="EPSG:32633",
        transform=from_origin(500000, 4000000, CELL, CELL),
        z=z,
        nodata=-9999.0,
    )
    result = extract_features(
        path,
        center_xy=(500000 + CELL * N / 2, 4000000 - CELL * N / 2),
        source_label="unit-test-nodata",
    )
    assert 0.5 < result.valid_fraction < 1.0
    assert "source_nodata_undefined" not in result.warnings


def test_extract_rejects_bad_centre_argument(tmp_path: Path):
    path = write_dem(
        tmp_path / "plane2.tif",
        crs="EPSG:32633",
        transform=from_origin(500000, 4000000, CELL, CELL),
        z=plane_z(),
    )
    with pytest.raises(ExtractionError) as error:
        extract_features(path)
    assert error.value.code == "invalid_centre"


def test_make_observations_matches_contract_units():
    observations = make_observations(
        terrain_metrics(plane_z(), CELL), valid_fraction=1.0, quality="verified"
    )
    assert set(observations) == {
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
    }
    for observation in observations.values():
        assert observation["method_version"] == METHOD_VERSION
        assert observation["quality"] == "verified"
        assert observation["unit"] in {"degree", "meter"}
    assert math.isfinite(observations["slope_mean_deg"]["value"])


def test_extract_features_for_explicit_bbox(tmp_path: Path):
    from analog_pipelines.features import extract_features_for_bounds

    path = write_dem(
        tmp_path / "aoi_plane.tif",
        crs="EPSG:32633",
        transform=from_origin(500000, 4000000, CELL, CELL),
        z=plane_z(),
    )
    result = extract_features_for_bounds(
        path,
        bbox={"west": 15.04, "south": 36.09, "east": 15.06, "north": 36.11},
        source_label="unit-test-aoi",
    )
    assert result.features["slope_mean_deg"] == pytest.approx(angle_from_gradient(0.1), abs=0.2)
    assert result.width_m and 1500 < result.width_m < 1900
    assert result.height_m and 2000 < result.height_m < 2400
    assert result.area_km2 and 3.0 < result.area_km2 < 4.5
    assert result.valid_fraction > 0.95


def test_extract_features_for_polygon_masks_outside(tmp_path: Path):
    from analog_pipelines.features import extract_features_for_bounds

    path = write_dem(
        tmp_path / "aoi_poly.tif",
        crs="EPSG:32633",
        transform=from_origin(500000, 4000000, CELL, CELL),
        z=plane_z(),
    )
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [15.04, 36.09],
                [15.05, 36.09],
                [15.05, 36.11],
                [15.04, 36.11],
                [15.04, 36.09],
            ]
        ],
    }
    result = extract_features_for_bounds(
        path,
        bbox={"west": 15.04, "south": 36.09, "east": 15.06, "north": 36.11},
        geometry=polygon,
        source_label="unit-test-aoi-polygon",
    )
    assert result.area_km2 and 1.5 < result.area_km2 < 2.5
    assert result.valid_fraction > 0.95
    assert result.geometry_wgs84 == polygon
    assert result.features["terrain_relief_p95_p05_m"] < 140.0
