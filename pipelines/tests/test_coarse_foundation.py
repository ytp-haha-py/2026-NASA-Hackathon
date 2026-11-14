from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_pipelines.coarse_features import (
    COARSE_METHOD_VERSION,
    CoarseExtractionError,
    ComparabilityError,
    assert_comparable,
    coarse_similarity_breakdown,
    extract_coarse_features,
)
from analog_pipelines.coarse_grid import GridError, GridRegion, generate_grid, generate_region_grid
from analog_pipelines.coarse_reference import (
    CoarseReference,
    plan_refine_windows,
    reject_coarse_fine_comparison,
    validate_coarse_reference,
)
from analog_pipelines.geomorpho import (
    RasterReadError,
    inspect_raster,
    read_projected_window,
    read_window,
)
from rasterio.transform import from_origin


def make_slope_tif(
    path: Path, *, nodata: float = -9999.0, scale: float = 0.5, offset: float = 1.0
) -> Path:
    rows = cols = 8
    array = np.arange(rows * cols, dtype="int16").reshape(rows, cols)
    array[0, 0] = 0  # valid physical zero after scaling
    array[0, 1] = int(nodata)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype="int16",
        crs="EPSG:4326",
        transform=from_origin(10.0, 45.0, 0.001, 0.001),
        nodata=nodata,
    ) as dst:
        dst.write(array, 1)
        dst.scales = (scale,)
        dst.offsets = (offset,)
    return path


def test_reader_metadata_scale_offset_and_nodata(tmp_path: Path):
    path = make_slope_tif(tmp_path / "slope.tif")
    metadata = inspect_raster(path)
    assert metadata.crs == "EPSG:4326"
    assert metadata.scale == 0.5
    assert metadata.offset == 1.0
    assert metadata.nodata == -9999.0
    window = read_window(path, bbox={"west": 10.0, "south": 44.998, "east": 10.002, "north": 45.0})
    assert window.data.shape[0] > 0
    assert metadata.native_resolution_unit == "degree"
    assert 50 < window.resolution_m[0] < 120
    assert 90 < window.resolution_m[1] < 130
    assert window.valid_mask[0, 0] is np.bool_(True) or bool(window.valid_mask[0, 0]) is True
    # scaled physical zero remains valid
    assert window.data[0, 0] == pytest.approx(1.0)
    # nodata is excluded from the valid mask
    assert not bool(window.valid_mask[0, 1])


def test_reader_rejects_remote_and_out_of_bounds(tmp_path: Path):
    with pytest.raises(RasterReadError, match="Remote"):
        inspect_raster("https://example.invalid/dem.tif")
    path = make_slope_tif(tmp_path / "slope.tif")
    with pytest.raises(RasterReadError):
        read_window(path, bbox={"west": 50, "south": 50, "east": 51, "north": 51})
    with pytest.raises(RasterReadError):
        read_window(
            path, bbox={"west": 9.999, "south": 44.991, "east": 10.005, "north": 44.997}, band=2
        )


def test_grid_is_deterministic_metric_and_in_bounds():
    region = GridRegion("pilot-a", "Pilot A", 10.0, 45.0, 10.2, 45.2)
    cells_a, coverage_a = generate_region_grid(region)
    cells_b, coverage_b = generate_region_grid(region)
    assert [cell.candidate_id for cell in cells_a] == [cell.candidate_id for cell in cells_b]
    assert cells_a[0].candidate_id.startswith("coarse-v1:")
    assert cells_a[0].candidate_id == cells_a[0].candidate_id
    assert coverage_a.candidate_count == coverage_b.candidate_count
    assert coverage_a.candidate_count > 0
    for cell in cells_a:
        assert cell.bbox_metric["east"] - cell.bbox_metric["west"] == pytest.approx(5000)
        assert cell.bbox_metric["south"] < cell.bbox_metric["north"]
        assert cell.bbox_wgs84["west"] < cell.bbox_wgs84["east"]
    with pytest.raises(GridError, match="Antarctica|coverage"):
        generate_region_grid(GridRegion("polar", "Polar", -10, -70, 10, -60))


def test_grid_plan_three_pilot_regions():
    regions = [
        GridRegion("sahara", "Sahara", -2, 22, 2, 26),
        GridRegion("alps", "Alps", 8, 45, 12, 48),
        GridRegion("grand-canyon", "Grand Canyon", -113, 35, -111, 37),
    ]
    plan = generate_grid(regions)
    assert len(plan.regions) == 3
    assert len(plan.coverage) == 3
    assert all(report.candidate_count > 0 for report in plan.coverage)
    assert len({cell.candidate_id for cell in plan.cells}) == len(plan.cells)


def test_coarse_features_use_separate_ids_and_masks():
    z = np.array([[0.0, 0.0, 10.0], [0.0, 10.0, 10.0], [10.0, 10.0, 90.0]])
    features = extract_coarse_features(z)
    assert set(features) == {
        "coarse_slope_mean_deg",
        "coarse_slope_p90_deg",
        "coarse_flat_fraction_lt5deg",
    }
    assert features["coarse_flat_fraction_lt5deg"] == pytest.approx(3 / 9)
    assert features["coarse_slope_p90_deg"] >= features["coarse_slope_mean_deg"]
    with pytest.raises(CoarseExtractionError) as exc:
        extract_coarse_features(np.array([[0.0, 10.0], [np.nan, np.nan]]))
    assert exc.value.code == "insufficient_valid_data"
    with pytest.raises(CoarseExtractionError) as exc:
        extract_coarse_features(np.array([[0.0, 100.0]]))
    assert exc.value.code == "invalid_slope_domain"


def test_coarse_distance_and_cross_scale_rejection():
    target = {"coarse_slope_mean_deg": 10.0, "coarse_slope_p90_deg": 20.0}
    candidate = {"coarse_slope_mean_deg": 10.0, "coarse_slope_p90_deg": 20.0}
    distance, breakdown = coarse_similarity_breakdown(candidate, target, {})
    assert distance == pytest.approx(0.0)
    assert len(breakdown) == 2
    with pytest.raises(ComparabilityError):
        reject_coarse_fine_comparison(COARSE_METHOD_VERSION, "terrain-metrics-1.0.0")
    with pytest.raises(ComparabilityError):
        assert_comparable(
            coarse_method_version=COARSE_METHOD_VERSION,
            fine_method_version="terrain-metrics-1.0.0",
        )


def test_coarse_zero_weight_feature_may_be_missing_but_positive_weight_may_not():
    target = {
        "coarse_slope_mean_deg": 10.0,
        "coarse_flat_fraction_lt5deg": 0.5,
    }
    candidate = {"coarse_slope_mean_deg": 10.0}

    distance, breakdown = coarse_similarity_breakdown(
        candidate, target, {"coarse_flat_fraction_lt5deg": 0.0}
    )
    assert distance == pytest.approx(0.0)
    assert [entry["feature_id"] for entry in breakdown] == ["coarse_slope_mean_deg"]

    with pytest.raises(CoarseExtractionError) as exc:
        coarse_similarity_breakdown(candidate, target, {"coarse_flat_fraction_lt5deg": 1.0})
    assert exc.value.code == "missing_metric"

    # A non-finite zero-weight value is also not compared; a positive-weight one is missing.
    non_finite = {"coarse_slope_mean_deg": 10.0, "coarse_flat_fraction_lt5deg": float("nan")}
    distance, breakdown = coarse_similarity_breakdown(
        non_finite, target, {"coarse_flat_fraction_lt5deg": 0.0}
    )
    assert distance == pytest.approx(0.0)
    assert [entry["feature_id"] for entry in breakdown] == ["coarse_slope_mean_deg"]
    with pytest.raises(CoarseExtractionError) as exc:
        coarse_similarity_breakdown(non_finite, target, {"coarse_flat_fraction_lt5deg": 1.0})
    assert exc.value.code == "missing_metric"

    # Nullable candidate values must not raise TypeError, even at zero weight.
    nullable = {"coarse_slope_mean_deg": 10.0, "coarse_flat_fraction_lt5deg": None}
    distance, breakdown = coarse_similarity_breakdown(
        nullable, target, {"coarse_flat_fraction_lt5deg": 0.0}
    )
    assert distance == pytest.approx(0.0)
    assert [entry["feature_id"] for entry in breakdown] == ["coarse_slope_mean_deg"]
    with pytest.raises(CoarseExtractionError) as exc:
        coarse_similarity_breakdown(nullable, target, {"coarse_flat_fraction_lt5deg": 1.0})
    assert exc.value.code == "missing_metric"


def test_coarse_reference_validation_and_refine_plan():
    reference = CoarseReference(
        reference_id="moon-fixture",
        body="Moon",
        feature_values={"coarse_slope_mean_deg": 10.0},
        method_version=COARSE_METHOD_VERSION,
        spatial_support="5 km coarse window",
        source_dataset_id="fixture",
    )
    assert validate_coarse_reference(reference) is reference
    with pytest.raises(ComparabilityError):
        validate_coarse_reference(
            CoarseReference(
                reference_id="bad",
                body="Moon",
                feature_values={"coarse_slope_mean_deg": 10.0},
                method_version="terrain-metrics-1.0.0",
                spatial_support="fine",
                source_dataset_id="fixture",
            )
        )
    windows = plan_refine_windows(
        parent_candidate_id="coarse-v1:a:0:0",
        parent_bbox_wgs84={"west": 10.0, "south": 45.0, "east": 10.064, "north": 45.045},
    )
    assert len(windows) == 4
    assert all(window.method_version == "terrain-metrics-1.0.0" for window in windows)
    assert all(
        window.bbox_wgs84["west"] >= 10.0 and window.bbox_wgs84["east"] <= 10.064
        for window in windows
    )


def test_end_to_end_grid_cell_through_geographic_reader_to_index(tmp_path: Path):
    """One real GridRegion cell -> local geographic raster -> coarse index query."""
    from analog_pipelines.coarse_grid import generate_region_grid
    from analog_pipelines.coarse_index import CoarseIndexRepository, CoarseRow, build_index

    region = GridRegion("e2e", "E2E", 10.0, 45.0, 10.1, 45.1)
    cells, _ = generate_region_grid(region)
    cell = cells[0]
    cellsize = 0.0023
    size = 80
    array = np.full((size, size), 10.0, dtype="float32")
    array[0, 0] = -9999.0
    path = tmp_path / "geographic_slope.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(
            cell.bbox_wgs84["west"] - 0.01, cell.bbox_wgs84["north"] + 0.01, cellsize, cellsize
        ),
        nodata=-9999.0,
    ) as dst:
        dst.write(array, 1)

    window = read_projected_window(
        path,
        bbox_metric=cell.bbox_metric,
        target_crs=cell.crs,
        resolution_m=250,
    )
    assert window.data.shape == (20, 20)
    assert window.resolution_m == pytest.approx((250.0, 250.0))
    assert window.bbox_metric == cell.bbox_metric
    assert window.crs == cell.crs
    assert 150 < window.resolution_m[0] < 320
    assert 150 < window.resolution_m[1] < 320
    features = extract_coarse_features(window.data, window.valid_mask)
    row = CoarseRow(
        candidate_id=cell.candidate_id,
        grid_id=cell.grid_version,
        region_id=cell.region_id,
        body="Earth",
        **cell.bbox_wgs84,
        crs=cell.crs,
        area_km2=25.0,
        slope_mean_deg=features["coarse_slope_mean_deg"],
        slope_p90_deg=features["coarse_slope_p90_deg"],
        flat_fraction=features["coarse_flat_fraction_lt5deg"],
        valid_fraction=float(window.valid_mask.mean()),
        method_version=COARSE_METHOD_VERSION,
        grid_version=cell.grid_version,
        dataset_id="fixture",
        source_asset_id="fixture",
        qa_flags=["synthetic_fixture"],
    )
    build_index([row], root=tmp_path / "index", snapshot_id="e2e-snapshot")
    repo = CoarseIndexRepository(tmp_path / "index")
    result = repo.search(
        target={"coarse_slope_mean_deg": 10.0},
        weights={"coarse_slope_mean_deg": 1.0},
        limit=1,
    )
    assert result["results"][0]["candidate_id"] == cell.candidate_id
    assert result["results"][0]["distance"] == pytest.approx(0.0)


def test_high_latitude_zone_edge_bbox_encloses_all_corners():
    from pyproj import Transformer

    region = GridRegion("edge", "Edge", 5.8, 60.0, 6.4, 60.35)
    cells, _ = generate_region_grid(region)
    cell = cells[0]
    inverse = Transformer.from_crs(cell.crs, "EPSG:4326", always_xy=True)
    corners = [
        inverse.transform(cell.bbox_metric["west"], cell.bbox_metric["south"]),
        inverse.transform(cell.bbox_metric["east"], cell.bbox_metric["south"]),
        inverse.transform(cell.bbox_metric["west"], cell.bbox_metric["north"]),
        inverse.transform(cell.bbox_metric["east"], cell.bbox_metric["north"]),
    ]
    assert min(lon for lon, _ in corners) == pytest.approx(cell.bbox_wgs84["west"], abs=1e-9)
    assert max(lon for lon, _ in corners) == pytest.approx(cell.bbox_wgs84["east"], abs=1e-9)
    assert min(lat for _, lat in corners) == pytest.approx(cell.bbox_wgs84["south"], abs=1e-9)
    assert max(lat for _, lat in corners) == pytest.approx(cell.bbox_wgs84["north"], abs=1e-9)


def test_analysis_unit_identity_is_physical_not_region_label():
    a = GridRegion("region-a", "A", 10.0, 45.0, 10.2, 45.2)
    b = GridRegion("region-b", "B", 10.0, 45.0, 10.2, 45.2)
    cells_a, _ = generate_region_grid(a)
    cells_b, _ = generate_region_grid(b)
    assert [c.candidate_id for c in cells_a] == [c.candidate_id for c in cells_b]
    shifted = GridRegion("region-a", "A", 10.1, 45.0, 10.3, 45.2)
    cells_shifted, _ = generate_region_grid(shifted)
    assert cells_a[0].candidate_id != cells_shifted[0].candidate_id
    assert cells_a[0].window_size_m == 5000
    assert cells_a[0].stride_m == 5000
    assert cells_a[0].source_resolution_m == 250
    assert cells_a[0].grid_anchor == (
        cells_a[0].bbox_metric["west"],
        cells_a[0].bbox_metric["south"],
    )


def test_projected_reader_at_high_latitude_zone_edge(tmp_path: Path):
    region = GridRegion("edge-projected", "Edge projected", 5.8, 60.0, 6.4, 60.35)
    cells, _ = generate_region_grid(region)
    cell = cells[0]
    size = 80
    cellsize = 0.0023
    path = tmp_path / "edge_slope.tif"
    array = np.full((size, size), 12.0, dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(
            cell.bbox_wgs84["west"] - 0.01,
            cell.bbox_wgs84["north"] + 0.01,
            cellsize,
            cellsize,
        ),
    ) as dst:
        dst.write(array, 1)
    window = read_projected_window(
        path, bbox_metric=cell.bbox_metric, target_crs=cell.crs, resolution_m=250
    )
    assert window.data.shape == (20, 20)
    assert window.valid_mask.sum() > 300
    assert window.resolution_m == pytest.approx((250.0, 250.0))
    assert window.bbox_metric == cell.bbox_metric
