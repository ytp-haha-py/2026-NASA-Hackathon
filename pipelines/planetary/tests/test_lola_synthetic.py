from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_pipelines.features import extract_features
from lola import (
    LUNAR_SOUTH_POLAR_STEREOGRAPHIC_WKT,
    DatasetInfo,
    build_manifest,
    default_schema_path,
    prepare_window,
    validate_manifest,
)
from rasterio.transform import from_origin

SYNTHETIC_NAME = "TEST_ONLY_synthetic_lunar_plane_5m.tif"
SYNTHETIC_CENTER = (1500.0, 1500.0)


def write_synthetic_lunar_plane(
    path: Path,
    *,
    width: int = 600,
    height: int = 600,
    cell: float = 5.0,
    grade: float = 0.1,
    base: float = 100.0,
) -> Path:
    """TEST-ONLY local raster with a known plane slope; not a NASA observation."""
    x = (np.arange(width) + 0.5) * cell
    z = np.tile(base + grade * x, (height, 1))
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs=LUNAR_SOUTH_POLAR_STEREOGRAPHIC_WKT,
        transform=from_origin(0.0, height * cell, cell, cell),
        nodata=float("nan"),
        compress="deflate",
        predictor=2,
        tiled=False,
    ) as dst:
        dst.write(z.astype("float32"), 1)
    return path


def _synthetic_dataset(access_timestamp: str, source_crs: str, source_path: Path) -> DatasetInfo:
    return DatasetInfo(
        dataset_id="synthetic-lunar-plane",
        provider="pytest TEST-ONLY fixture (not a NASA dataset)",
        product_id="synthetic-lunar-plane-5m",
        title="TEST-ONLY synthetic lunar-like plane, known 10% grade",
        url=f"file://{source_path}",
        license="TEST-ONLY fixture; no real-world license or data claim",
        license_url="https://example.invalid/test-only",
        access_timestamp=access_timestamp,
        source_crs=source_crs,
        native_resolution_m=5.0,
        processing_version="terrain-metrics-1.0.0",
        checksum=None,
        source_kind="synthetic",
        notes=(
            "TEST-ONLY synthetic lunar-like plane generated inside pytest. "
            "It is not a NASA observation and must never be written to the real "
            "LOLA reference manifest."
        ),
    )


def test_synthetic_lunar_plane_full_code_path(tmp_path: Path) -> None:
    source = write_synthetic_lunar_plane(tmp_path / SYNTHETIC_NAME)
    cache_dir = tmp_path / "cache"
    prepared = prepare_window(str(source), SYNTHETIC_CENTER, cache_dir)

    assert prepared.from_cache is False
    assert prepared.cache_path is not None and prepared.cache_path.is_file()
    assert prepared.meta.native_resolution_m == pytest.approx(5.0)
    assert prepared.meta.ellipsoid_radius_m == pytest.approx(1_737_400.0)
    assert prepared.meta.is_tiled is False
    assert prepared.crop_shape == (414, 414)
    assert prepared.estimated_range_bytes > 0
    assert prepared.source_valid_fraction == pytest.approx(1.0)

    result = extract_features(
        str(prepared.cache_path),
        center_xy=SYNTHETIC_CENTER,
        source_label="pytest-synthetic-lunar-plane",
        body="Moon",
    )
    expected_slope = math.degrees(math.atan(0.1))
    assert result.features["slope_mean_deg"] == pytest.approx(expected_slope, abs=0.02)
    assert result.features["slope_p90_deg"] == pytest.approx(expected_slope, abs=0.02)

    xs = 1500.0 - 990.0 + 45.0 + 90.0 * np.arange(22)
    plane_grid = np.tile(100.0 + 0.1 * xs, (22, 1))
    expected_relief = float(np.percentile(plane_grid, 95) - np.percentile(plane_grid, 5))
    assert result.features["terrain_relief_p95_p05_m"] == pytest.approx(expected_relief, abs=0.5)
    # Bilinear/resampling round-off on a 10% plane is sub-metre; the metric should
    # be near zero, not exactly zero.
    assert result.features["roughness_std_m"] == pytest.approx(0.0, abs=0.5)
    assert result.valid_fraction == pytest.approx(1.0)
    assert result.bbox_wgs84 is None

    dataset = _synthetic_dataset(
        prepared.access_timestamp or "2026-01-01T00:00:00Z",
        prepared.meta.crs_wkt,
        source,
    )
    document = build_manifest(
        prepared,
        result,
        dataset,
        reference_id="moon-synthetic-plane",
        display_name="TEST-ONLY synthetic lunar plane",
        roi_name="synthetic-plane",
        roi_provenance="pytest tmp_path only; not a product ROI",
        data_kind="synthetic",
        observation_quality="verified",
    )
    assert document["data_kind"] == "synthetic"
    assert document["datasets"][0]["source_kind"] == "synthetic"
    assert document["references"][0]["reference_id"] == "moon-synthetic-plane"
    assert document["references"][0]["bbox"] is None
    assert all(
        observation["quality"] == "synthetic"
        for observation in document["references"][0]["features"].values()
    )
    flags = document["references"][0]["quality_flags"]
    assert "synthetic_test_fixture" in flags
    assert "official_pgda_lola_site04" not in flags
    assert "TEST-ONLY" in document["datasets"][0]["notes"]
    validate_manifest(document, default_schema_path())

    out = tmp_path / "synthetic_manifest.json"
    out.write_text(json.dumps(document), encoding="utf-8")
    assert out.is_file()
    assert "pipelines/planetary/manifests" not in str(out)


def test_synthetic_window_cache_reuse(tmp_path: Path) -> None:
    source = write_synthetic_lunar_plane(tmp_path / SYNTHETIC_NAME)
    cache_dir = tmp_path / "cache"
    first = prepare_window(str(source), SYNTHETIC_CENTER, cache_dir)
    second = prepare_window(str(source), SYNTHETIC_CENTER, cache_dir)
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.cache_sha256 == first.cache_sha256
    assert second.access_timestamp == first.access_timestamp
    assert second.cache_path == first.cache_path


def test_synthetic_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = write_synthetic_lunar_plane(tmp_path / SYNTHETIC_NAME)
    cache_dir = tmp_path / "cache"
    plan = prepare_window(str(source), SYNTHETIC_CENTER, cache_dir, dry_run=True)
    assert plan.cache_path is None
    assert plan.cache_sha256 is None
    assert not cache_dir.exists()


def test_cli_dry_run_writes_no_manifest(tmp_path: Path, capsys) -> None:
    from lola_import import main

    source = write_synthetic_lunar_plane(tmp_path / SYNTHETIC_NAME)
    output = tmp_path / "manifest.json"
    cache_dir = tmp_path / "cache"
    code = main(
        [
            "--source",
            str(source),
            "--center-x",
            "1500",
            "--center-y",
            "1500",
            "--cache-dir",
            str(cache_dir),
            "--output",
            str(output),
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "TEST_ONLY" not in captured.out or "DRY RUN" in captured.out
    assert "DRY RUN" in captured.out
    assert "no manifest was written" in captured.out
    assert not output.exists()
    assert not cache_dir.exists()


def test_cli_list_products(capsys) -> None:
    from lola_import import main

    assert main(["--list-products"]) == 0
    captured = capsys.readouterr()
    assert "https://pgda.gsfc.nasa.gov/products/78" in captured.out
    assert "Site04_final_adj_5mpp_surf.tif" in captured.out
    assert "MOON_ME" in captured.out
