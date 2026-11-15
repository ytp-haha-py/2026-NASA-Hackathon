"""Offline tests for buffer requirements, edge validity and analytic terrain math."""

from __future__ import annotations

import math

import numpy as np
import pytest
import rasterio
from analog_pipelines.terrain_buffer import (
    BUFFER_REQUIREMENTS,
    CORE_CELLS,
    buffer_summary_rows,
    compare_feature,
    compare_fields,
    compare_support,
    core_support_cells,
    core_support_fraction,
    edge_validity,
    min_buffer_for_gate,
    read_metric_window,
    support_mask,
    window_checksum,
)
from analog_pipelines.terrain_research import (
    curvature_components,
    gaussian_smooth_strict,
    quadratic_curvature_components,
    smoothed_curvature_components,
    surface_curvedness,
)
from fixtures.terrain_buffer_synthetic import (
    SYNTHETIC_LABEL,
    SYNTHETIC_METHOD,
    gaussian_hill,
    quadratic_bowl,
)
from rasterio.transform import from_origin

REQUIREMENT_BY_ID = {requirement.method_id: requirement for requirement in BUFFER_REQUIREMENTS}


def test_buffer_requirements_match_the_documented_support_radii():
    expected = {
        "slope_mean_deg": (1, 24, 2160.0, 400),
        "slope_p90_deg": (1, 24, 2160.0, 400),
        "terrain_relief_p95_p05_m": (0, 22, 1980.0, 484),
        "roughness_std_m": (0, 22, 1980.0, 484),
        "flat_terrain_fraction": (1, 24, 2160.0, 400),
        "surface_curvedness_central": (1, 24, 2160.0, 400),
        "mean_curvature_central": (1, 24, 2160.0, 400),
        "gaussian_curvature_central": (1, 24, 2160.0, 400),
        "surface_curvedness_quadratic_r2": (2, 26, 2340.0, 324),
        "surface_curvedness_quadratic_r3": (3, 28, 2520.0, 256),
        "surface_curvedness_smoothed": (4, 30, 2700.0, 196),
        "profile_curvature_central": (1, 24, 2160.0, 400),
        "profile_curvature_smoothed": (4, 30, 2700.0, 196),
        "tangential_curvature_central": (1, 24, 2160.0, 400),
        "tangential_curvature_smoothed": (4, 30, 2700.0, 196),
        "geomorphon_10class": (12, 46, 4140.0, 0),
    }
    assert set(REQUIREMENT_BY_ID) == set(expected)
    for method_id, (radius, cells, footprint_m, normal_cells) in expected.items():
        requirement = REQUIREMENT_BY_ID[method_id]
        assert requirement.radius_cells == radius
        assert requirement.full_footprint_cells == cells
        assert requirement.full_footprint_m == footprint_m
        assert requirement.normal_support_cells == normal_cells
        assert requirement.buffer_m == radius * 90.0


def test_minimum_gate_buffer_is_one_cell_below_full_support_except_for_small_radii():
    expected = {
        "slope_mean_deg": 0,
        "terrain_relief_p95_p05_m": 0,
        "surface_curvedness_quadratic_r2": 1,
        "surface_curvedness_quadratic_r3": 2,
        "surface_curvedness_smoothed": 3,
        "geomorphon_10class": 11,
    }
    for method_id, gate_cells in expected.items():
        requirement = REQUIREMENT_BY_ID[method_id]
        assert requirement.min_gate_buffer_cells == gate_cells
        if requirement.radius_cells:
            assert core_support_fraction(gate_cells, requirement.radius_cells) >= 0.8
            if gate_cells > 0:
                assert core_support_fraction(gate_cells - 1, requirement.radius_cells) < 0.8


def test_core_support_counts_are_exact_and_monotonic():
    assert core_support_cells(0, 0) == 484
    assert core_support_cells(0, 1) == 400
    assert core_support_cells(0, 2) == 324
    assert core_support_cells(0, 3) == 256
    assert core_support_cells(0, 4) == 196
    assert core_support_cells(0, 12) == 0
    for required in range(13):
        values = [core_support_cells(provided, required) for provided in range(13)]
        assert values == sorted(values)
        assert values[-1] == CORE_CELLS * CORE_CELLS
    with pytest.raises(ValueError):
        core_support_cells(-1, 0)
    with pytest.raises(ValueError):
        core_support_cells(0, -1)
    with pytest.raises(ValueError):
        min_buffer_for_gate(1, gate=0.0)
    with pytest.raises(ValueError):
        min_buffer_for_gate(1, gate=1.1)


def test_edge_validity_reports_edge_loss_and_gate_status_without_filling():
    rows = {row["method_id"]: row for row in edge_validity(0)}
    assert rows["surface_curvedness_central"]["supported_core_cells"] == 400
    assert rows["surface_curvedness_central"]["missing_core_cells"] == 84
    assert rows["surface_curvedness_central"]["meets_min_valid_fraction"] is True
    assert rows["surface_curvedness_smoothed"]["supported_core_cells"] == 196
    assert rows["surface_curvedness_smoothed"]["meets_min_valid_fraction"] is False
    assert rows["geomorphon_10class"]["supported_core_cells"] == 0
    assert rows["geomorphon_10class"]["full_core_support"] is False
    assert all(row["supported_core_fraction"] >= 0 for row in edge_validity(12))


def test_support_mask_never_invents_partial_neighbourhoods():
    normal = support_mask((22, 22), 1)
    assert normal.sum() == 400
    assert not normal[0].any() and not normal[-1].any()
    assert support_mask((22, 22), 12).sum() == 0
    buffered = support_mask((46, 46), 12)
    assert buffered.sum() == 484
    assert buffered[12:34, 12:34].all()
    assert support_mask((5, 5), 0).all()


def test_buffer_summary_rows_are_flat_and_portable():
    rows = buffer_summary_rows()
    assert len(rows) == len(BUFFER_REQUIREMENTS)
    assert {row["method_id"] for row in rows} == set(REQUIREMENT_BY_ID)
    assert all(row["min_valid_fraction"] == 0.8 for row in rows)


def test_curvature_components_are_analytic_for_plane_bowl_and_saddle():
    y, x = np.meshgrid(np.arange(-11, 12) * 90, np.arange(-11, 12) * 90, indexing="ij")
    a = 1.0e-4
    plane = curvature_components(0.1 * x + 0.2 * y, 90)
    assert np.nanmax(np.abs(plane["surface_curvedness_m_inv"])) < 1e-14
    assert np.nanmax(np.abs(plane["mean_curvature_m_inv"])) < 1e-14
    assert np.nanmax(np.abs(plane["profile_curvature_m_inv"])) < 1e-14

    bowl = curvature_components(0.5 * a * (x * x + y * y), 90)
    saddle = curvature_components(0.5 * a * (x * x - y * y), 90)
    assert bowl["surface_curvedness_m_inv"][11, 11] == pytest.approx(a)
    assert bowl["mean_curvature_m_inv"][11, 11] == pytest.approx(a)
    assert bowl["gaussian_curvature_m_inv2"][11, 11] == pytest.approx(a * a)
    assert saddle["surface_curvedness_m_inv"][11, 11] == pytest.approx(a)
    assert abs(saddle["mean_curvature_m_inv"][11, 11]) < 1e-14
    assert saddle["gaussian_curvature_m_inv2"][11, 11] == pytest.approx(-(a * a))
    # Positive-convex convention: the bowl is concave along the gradient.
    assert bowl["profile_curvature_m_inv"][11, 14] < 0
    assert np.isnan(bowl["surface_curvedness_m_inv"][0]).all()
    np.testing.assert_allclose(
        bowl["surface_curvedness_m_inv"],
        surface_curvedness(0.5 * a * (x * x + y * y), 90),
        equal_nan=True,
    )


def test_gaussian_smoothing_sums_to_one_and_stays_strict_at_nodata():
    z = np.full((22, 22), 7.0)
    smoothed = gaussian_smooth_strict(z, sigma_cells=1.0, truncate=3.0)
    np.testing.assert_allclose(smoothed[5:17, 5:17], 7.0)
    assert np.isnan(smoothed[0, 0])  # 3-cell kernel cannot be completed at the edge.

    z[10, 10] = np.nan
    smoothed = gaussian_smooth_strict(z, sigma_cells=1.0, truncate=3.0)
    assert np.isnan(smoothed[7:14, 7:14]).all()
    assert np.isfinite(smoothed[14, 14])
    components = smoothed_curvature_components(z, 90)
    assert np.isnan(components["surface_curvedness_m_inv"][6:15, 6:15]).all()


def test_quadratic_fit_recovers_an_exact_quadratic_surface():
    size = 45
    cell = 90.0
    a = 1.0e-4
    z = quadratic_bowl(size=size, cell_m=cell, a=a)
    fields = quadratic_curvature_components(z, cell, radius_cells=2)
    inner = np.s_[2 : size - 2, 2 : size - 2]
    assert np.isfinite(fields["surface_curvedness_m_inv"][inner]).all()
    # The paraboloid's curvature varies with gradient away from the centre, but the
    # quadratic fit and the exact 3x3 central differences must agree at every cell.
    central = curvature_components(z, cell)
    for name in (
        "mean_curvature_m_inv",
        "gaussian_curvature_m_inv2",
        "surface_curvedness_m_inv",
        "profile_curvature_m_inv",
        "tangential_curvature_m_inv",
    ):
        np.testing.assert_allclose(
            fields[name][inner], central[name][inner], rtol=1e-10, atol=1e-14
        )
    centre = (size - 1) // 2
    assert fields["surface_curvedness_m_inv"][centre, centre] == pytest.approx(a, rel=1e-12)
    assert fields["mean_curvature_m_inv"][centre, centre] == pytest.approx(a, rel=1e-12)
    assert fields["gaussian_curvature_m_inv2"][centre, centre] == pytest.approx(a * a, rel=1e-12)
    with pytest.raises(ValueError):
        quadratic_curvature_components(np.zeros((4, 4)), 90, radius_cells=2)
    with pytest.raises(ValueError):
        quadratic_curvature_components(z, 90, radius_cells=0)


def test_comparisons_report_edge_loss_and_zero_value_change_on_common_cells():
    z = gaussian_hill()
    normal = surface_curvedness(z[12:34, 12:34], 90)
    buffered = surface_curvedness(z, 90)[12:34, 12:34]
    comparison = compare_feature(normal, buffered, method_id="surface_curvedness_central")
    assert comparison["normal_supported_cells"] == 400
    assert comparison["buffered_core_supported_cells"] == 484
    assert comparison["common_cells"] == 400
    assert comparison["edge_loss_cells"] == 84
    assert comparison["value_comparison_on_common_cells"]["rms_diff"] < 1e-15
    assert comparison["summary_normal_supported"]["count"] == 400
    assert comparison["summary_buffered_core"]["count"] == 484
    assert SYNTHETIC_METHOD and SYNTHETIC_LABEL  # fixture is explicitly labelled.

    smoothed_normal = smoothed_curvature_components(z[12:34, 12:34], 90)["surface_curvedness_m_inv"]
    smoothed_buffered = smoothed_curvature_components(z, 90)["surface_curvedness_m_inv"][
        12:34, 12:34
    ]
    smoothed = compare_feature(
        smoothed_normal, smoothed_buffered, method_id="surface_curvedness_smoothed"
    )
    assert smoothed["normal_supported_cells"] == 196
    assert smoothed["buffered_core_supported_cells"] == 484
    assert smoothed["edge_loss_cells"] == 288
    assert smoothed["value_comparison_on_common_cells"]["rms_diff"] < 1e-15
    assert smoothed["mean_shift_buffered_minus_normal"] is not None


def test_support_comparison_does_not_fabricate_geomorphon_values():
    normal = support_mask((22, 22), 12)
    buffered = support_mask((46, 46), 12)[12:34, 12:34]
    comparison = compare_support(normal, buffered, method_id="geomorphon_10class")
    assert comparison["normal_supported_cells"] == 0
    assert comparison["buffered_core_supported_cells"] == 484
    assert comparison["edge_loss_cells"] == 484
    assert comparison["value_comparison"] is None
    assert "No reference classifier" in comparison["blocker"]


def test_compare_fields_is_symmetric_on_common_support():
    z = gaussian_hill()
    central = surface_curvedness(z, 90)
    smoothed = smoothed_curvature_components(z, 90)["surface_curvedness_m_inv"]
    forward = compare_fields(central, smoothed, reference_id="central", candidate_id="smoothed")
    reverse = compare_fields(smoothed, central, reference_id="smoothed", candidate_id="central")
    assert forward["common_cells"] == reverse["common_cells"]
    assert forward["difference_summary"]["rms"] == pytest.approx(
        reverse["difference_summary"]["rms"]
    )


def _write_test_geotiff(path, size=600, resolution=10.0, a=1.0e-4):
    centre = (size - 1) / 2.0
    y, x = np.meshgrid(
        (np.arange(size) - centre) * resolution,
        (np.arange(size) - centre) * resolution,
        indexing="ij",
    )
    elevation = 0.5 * a * (x * x + y * y)
    transform = from_origin(
        500000.0 - size * resolution / 2, 4500000.0 + size * resolution / 2, resolution, resolution
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype="float64",
        crs="EPSG:32629",
        transform=transform,
        nodata=np.nan,
    ) as dst:
        dst.write(elevation, 1)
    return transform


def test_buffered_reader_keeps_the_core_aligned_and_identical(tmp_path):
    path = tmp_path / "synthetic_utm.tif"
    _write_test_geotiff(path)
    from pyproj import CRS

    normal = read_metric_window(
        path, dst_crs=CRS.from_epsg(32629), center_xy=(500000.0, 4500000.0), buffer_cells=0
    )
    buffered = read_metric_window(
        path, dst_crs=CRS.from_epsg(32629), center_xy=(500000.0, 4500000.0), buffer_cells=12
    )
    assert normal.grid_cells == 22
    assert buffered.grid_cells == 46
    assert normal.core().shape == (22, 22)
    np.testing.assert_allclose(normal.elevation, buffered.core(), equal_nan=True)
    assert normal.transform == normal.core_transform
    assert normal.core_transform == buffered.core_transform

    normal_hash = window_checksum(normal.elevation, "synthetic")
    buffered_hash = window_checksum(buffered.core(), "synthetic")
    assert normal_hash == buffered_hash
    assert normal_hash.startswith("sha256:")


def test_reader_rejects_non_metric_destination_and_bad_centers(tmp_path):
    path = tmp_path / "synthetic_utm.tif"
    _write_test_geotiff(path)
    from pyproj import CRS

    with pytest.raises(ValueError):
        read_metric_window(path, dst_crs=CRS.from_epsg(4326), center_xy=(500000.0, 4500000.0))
    with pytest.raises(ValueError):
        read_metric_window(path, dst_crs=CRS.from_epsg(32629), center_xy=(math.nan, 4500000.0))
    with pytest.raises(ValueError):
        read_metric_window(
            path, dst_crs=CRS.from_epsg(32629), center_xy=(500000.0, 4500000.0), buffer_cells=-1
        )
