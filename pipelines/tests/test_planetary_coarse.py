from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_pipelines.features import horn_slope_degrees
from analog_pipelines.planetary_coarse import (
    DECISION_EVIDENCE_ONLY,
    DECISION_NOT_COMPARABLE,
    MARS_CTX_JEZERO,
    MARS_MOLA_MEG128,
    MOON_LOLA128,
    PLANETARY_COARSE_METHOD_VERSION,
    PlanetaryCoarseError,
    PlanetaryCrsError,
    assert_planetary_crs,
    classify_body_from_crs,
    coarse_cell_from_dem,
    cross_body_ranking_gate,
    inspect_planetary_raster,
    read_metric_window,
    verify_manifest,
)
from pyproj import CRS
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[2]
COARSE = ROOT / "pipelines/planetary/coarse"
FIXTURES = ROOT / "samples/planetary/coarse/fixtures"
MANIFESTS = {
    "moon": COARSE / "moon_lola128_window01.json",
    "mars-ctx": COARSE / "mars_ctx_jezero_window01.json",
    "mars-mola": COARSE / "mars_mola_meg128_evidence.json",
}
MOON_AEQD = CRS.from_proj4("+proj=aeqd +lat_0=-10 +lon_0=20 +R=1737400 +units=m +no_defs")
MARS_EQC = CRS.from_proj4("+proj=eqc +lat_ts=18.4663 +lon_0=0 +R=3396190 +units=m +no_defs")


def _write_dem(
    path: Path,
    *,
    crs: CRS,
    z: np.ndarray,
    resolution: float,
    origin: tuple[float, float],
    dtype: str = "float32",
    nodata: float | None = float("nan"),
    scale: float = 1.0,
    offset: float = 0.0,
) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=z.shape[0],
        width=z.shape[1],
        count=1,
        dtype=dtype,
        crs=crs,
        transform=from_origin(origin[0], origin[1], resolution, resolution),
        nodata=nodata,
    ) as dst:
        dst.write(z.astype(dtype), 1)
        dst.scales = (scale,)
        dst.offsets = (offset,)
    return path


def _plane(shape: tuple[int, int], resolution: float, grade: float = 0.1) -> np.ndarray:
    cols = np.arange(shape[1], dtype="float64") * resolution
    return np.tile(cols * grade, (shape[0], 1))


def test_earth_and_wrong_body_crs_are_rejected(tmp_path: Path):
    with pytest.raises(PlanetaryCrsError, match="Earth"):
        assert_planetary_crs(CRS.from_epsg(4326), body="Moon")
    with pytest.raises(PlanetaryCrsError, match="Earth|WGS84"):
        assert_planetary_crs(CRS.from_epsg(32633), body="Mars")
    with pytest.raises(PlanetaryCrsError) as mismatch:
        assert_planetary_crs(MOON_AEQD, body="Mars")
    assert mismatch.value.code == "body_mismatch"
    assert classify_body_from_crs(MOON_AEQD) == "Moon"
    assert classify_body_from_crs(MARS_EQC) == "Mars"

    earth = _write_dem(
        tmp_path / "earth.tif",
        crs=CRS.from_epsg(4326),
        z=np.zeros((8, 8)),
        resolution=0.01,
        origin=(10.0, 45.0),
    )
    with pytest.raises(PlanetaryCrsError, match="Earth"):
        inspect_planetary_raster(earth)


def test_synthetic_lunar_plane_slope_and_counts(tmp_path: Path):
    resolution = 250.0
    z = _plane((40, 40), resolution, grade=0.1)
    path = _write_dem(
        tmp_path / "moon.tif",
        crs=MOON_AEQD,
        z=z,
        resolution=resolution,
        origin=(0.0, 10_000.0),
    )
    source = replace(
        MOON_LOLA128,
        source_id="test-moon-projected",
        url=str(path),
        raster_url=str(path),
        native_resolution_m=resolution,
        expected_nodata=None,
        quality_flags=("synthetic_test_only",),
    )
    metadata = inspect_planetary_raster(path, source=source)
    assert metadata.body == "Moon"
    assert metadata.native_resolution_m == pytest.approx(250.0)

    cell = coarse_cell_from_dem(
        path,
        source=source,
        region_id="test-moon",
        row=0,
        col=0,
        target_crs=MOON_AEQD,
        center_xy=(5000.0, 5000.0),
        n_pixels=20,
        resolution_m=250.0,
    )
    assert cell.valid_slope_pixels == 400
    assert cell.missing_slope_pixels == 0
    assert cell.features["coarse_slope_mean_deg"] == pytest.approx(
        np.degrees(np.arctan(0.1)), abs=0.01
    )
    assert cell.features["coarse_flat_fraction_lt5deg"] == 0.0


def test_scale_offset_nodata_and_missing_pixels(tmp_path: Path):
    resolution = 250.0
    physical = _plane((40, 40), resolution, grade=0.2) + 500.0
    raw = np.round((physical - 100.0) / 0.5).astype("int16")
    nodata = -9999
    raw[20, 20] = nodata
    path = _write_dem(
        tmp_path / "scaled.tif",
        crs=MOON_AEQD,
        z=raw,
        resolution=resolution,
        origin=(0.0, 10_000.0),
        dtype="int16",
        nodata=nodata,
        scale=0.5,
        offset=100.0,
    )
    source = replace(
        MOON_LOLA128,
        source_id="test-moon-scaled",
        url=str(path),
        raster_url=str(path),
        native_resolution_m=resolution,
        expected_nodata=float(nodata),
        expected_scale=0.5,
        expected_offset=100.0,
    )
    metadata = inspect_planetary_raster(path, source=source)
    assert metadata.scale == 0.5
    assert metadata.offset == 100.0
    assert metadata.nodata == nodata

    cell = coarse_cell_from_dem(
        path,
        source=source,
        region_id="test-moon",
        row=0,
        col=0,
        target_crs=MOON_AEQD,
        center_xy=(5000.0, 5000.0),
        n_pixels=20,
        resolution_m=250.0,
    )
    assert cell.missing_slope_pixels == 9  # a nodata cell invalidates its 3x3 stencil
    assert 0 < cell.valid_fraction < 1
    assert cell.features["coarse_slope_mean_deg"] == pytest.approx(
        np.degrees(np.arctan(0.2)), abs=0.02
    )
    assert cell.elevation_stats["elevation_mean_m"] > 500.0


def test_insufficient_valid_data_is_never_filled(tmp_path: Path):
    resolution = 250.0
    raw = np.full((40, 40), 1000.0, dtype="int16")
    raw[:, 15:] = -9999
    path = _write_dem(
        tmp_path / "gappy.tif",
        crs=MOON_AEQD,
        z=raw,
        resolution=resolution,
        origin=(0.0, 10_000.0),
        dtype="int16",
        nodata=-9999,
    )
    source = replace(
        MOON_LOLA128,
        source_id="test-moon-gappy",
        url=str(path),
        raster_url=str(path),
        native_resolution_m=resolution,
        expected_nodata=-9999.0,
    )
    with pytest.raises(PlanetaryCoarseError, match="valid fraction|insufficient"):
        coarse_cell_from_dem(
            path,
            source=source,
            region_id="test-moon",
            row=0,
            col=0,
            target_crs=MOON_AEQD,
            center_xy=(5000.0, 5000.0),
            n_pixels=20,
            resolution_m=250.0,
        )


def test_planetary_geographic_source_is_reprojected(tmp_path: Path):
    # A lunar geographic source at 0.0025 deg (~75.8 m) is reprojected to the
    # 250 m metric grid; the planetocentric frame is never interpreted as WGS84.
    moon_geo = CRS.from_proj4("+proj=longlat +R=1737400 +no_defs")
    resolution_deg = 0.0025
    z = _plane((300, 300), resolution_deg, grade=3030.0)
    path = _write_dem(
        tmp_path / "moon_geo.tif",
        crs=moon_geo,
        z=z,
        resolution=resolution_deg,
        origin=(19.8, -9.7),
    )
    source = replace(
        MOON_LOLA128,
        source_id="test-moon-geographic",
        url=str(path),
        raster_url=str(path),
        native_resolution_m=75.8,
        expected_nodata=None,
    )
    window = read_metric_window(
        path,
        source=source,
        bbox_metric={"west": 0.0, "south": 0.0, "east": 5000.0, "north": 5000.0},
        target_crs=MOON_AEQD,
        resolution_m=250.0,
        pad_pixels=1,
    )
    assert window.data.shape == (20, 20)
    assert window.valid_mask.all()
    assert window.metadata.body == "Moon"
    slope = horn_slope_degrees(window.data, 250.0)
    assert np.isfinite(slope[1:-1, 1:-1]).all()
    assert np.nanmean(slope[1:-1, 1:-1]) == pytest.approx(np.degrees(np.arctan(0.1)), abs=0.6)


def test_bounded_reader_rejects_remote_by_default_and_oversize(tmp_path: Path):
    with pytest.raises(PlanetaryCoarseError, match="opt-in"):
        read_metric_window(
            "https://example.invalid/moon.tif",
            source=MOON_LOLA128,
            bbox_metric={"west": 0, "south": 0, "east": 5000, "north": 5000},
            target_crs=MOON_AEQD,
        )
    resolution = 250.0
    path = _write_dem(
        tmp_path / "moon.tif",
        crs=MOON_AEQD,
        z=_plane((40, 40), resolution),
        resolution=resolution,
        origin=(0.0, 10_000.0),
    )
    source = replace(
        MOON_LOLA128,
        source_id="test-moon-projected",
        url=str(path),
        raster_url=str(path),
        native_resolution_m=resolution,
        expected_nodata=None,
    )
    with pytest.raises(PlanetaryCoarseError, match="max_pixels|too_large"):
        read_metric_window(
            path,
            source=source,
            bbox_metric={"west": 0, "south": 0, "east": 5000, "north": 5000},
            target_crs=MOON_AEQD,
            resolution_m=25.0,
            max_pixels=1_000,
        )


def _load(name: str) -> dict:
    return json.loads(MANIFESTS[name].read_text(encoding="utf-8"))


def test_committed_manifests_verify_offline_and_are_planetary():
    expected_cells = {"moon": 4, "mars-ctx": 4, "mars-mola": 2}
    for name, path in MANIFESTS.items():
        manifest = verify_manifest(path, fixture_root=ROOT)
        assert manifest["method"]["method_version"] == PLANETARY_COARSE_METHOD_VERSION
        assert manifest["counts"]["computed_cells"] == expected_cells[name]
        assert manifest["counts"]["failed_cells"] == 0
        assert manifest["counts"]["missing_slope_pixels"] == 0
        assert manifest["cross_body_ranking"]["enabled"] is False
        for cell in manifest["cells"]:
            crs = CRS.from_wkt(cell["crs_wkt"])
            assert classify_body_from_crs(crs) == manifest["source"]["body"]
            assert crs.to_epsg() != 4326
            fixture = ROOT / cell["fixture"]["path"]
            assert fixture.is_file()
            assert fixture.stat().st_size == cell["fixture"]["bytes"]


def test_comparability_decisions_never_claim_equivalence():
    moon = _load("moon")
    ctx = _load("mars-ctx")
    mola = _load("mars-mola")
    assert {item["decision"] for item in moon["comparability"]} == {DECISION_EVIDENCE_ONLY}
    assert {item["decision"] for item in ctx["comparability"]} == {DECISION_EVIDENCE_ONLY}
    assert {item["decision"] for item in mola["comparability"]} == {DECISION_NOT_COMPARABLE}
    assert moon["source"]["same_support_as_earth"] is True
    assert ctx["source"]["same_support_as_earth"] is True
    assert mola["source"]["same_support_as_earth"] is False
    assert mola["source"]["native_resolution_m"] == pytest.approx(463.0576672)
    assert mola["cells"][0]["window_size_m"] == pytest.approx(11 * 463.0576672)
    gate = cross_body_ranking_gate(mola["comparability"])
    assert gate["enabled"] is False
    assert set(gate["blocking_features"]) == {
        "coarse_flat_fraction_lt5deg",
        "coarse_slope_mean_deg",
        "coarse_slope_p90_deg",
    }
    # No coarse metric is silently promoted to "comparable".
    for manifest in (moon, ctx, mola):
        assert all(item["decision"] != "comparable" for item in manifest["comparability"])
        assert manifest["method"]["earth_coarse_method_version"] == ("coarse-terrain-250m-1.0.0")


def test_mola_support_comparison_is_measured_not_claimed():
    mola = _load("mars-mola")
    pairs = mola["support_comparison"]["pairs"]
    assert len(pairs) == 2
    for pair in pairs:
        assert pair["ctx_window_size_m"] == pytest.approx(5000.0)
        assert pair["mola_window_size_m"] == pytest.approx(5093.6343392)
        assert pair["mola_native_pixels_per_side"] == 11
        assert pair["centre_separation_m"] < 0.01
        for feature_id, delta in pair["observed_delta"].items():
            expected = pair["mola_features"][feature_id] - pair["ctx_features"][feature_id]
            assert delta == pytest.approx(expected)


def test_existing_18_moon_and_7_mars_fine_references_are_untouched():
    moon_ids: set[str] = set()
    mars_ids: set[str] = set()
    for path in sorted((ROOT / "pipelines/planetary/manifests").glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for reference in manifest["references"]:
            if reference["body"] == "Moon":
                moon_ids.add(reference["reference_id"])
            elif reference["body"] == "Mars":
                mars_ids.add(reference["reference_id"])
    assert len(moon_ids) == 18
    assert len(mars_ids) == 7
    coarse_ids = {cell["candidate_id"] for name in MANIFESTS for cell in _load(name)["cells"]}
    assert coarse_ids.isdisjoint(moon_ids)
    assert coarse_ids.isdisjoint(mars_ids)
    assert all(candidate.startswith("planet-coarse-v1:") for candidate in coarse_ids)


def test_coarse_cell_payload_does_not_emit_unsupported_metrics():
    for name in MANIFESTS:
        manifest = _load(name)
        for cell in manifest["cells"]:
            assert set(cell["features"]) == {
                "coarse_slope_mean_deg",
                "coarse_slope_p90_deg",
                "coarse_flat_fraction_lt5deg",
            }
            assert "roughness_std_m" not in cell["features"]
            assert "terrain_relief_p95_p05_m" not in cell["features"]
        unsupported = manifest["method_calibration"]["unsupported_metrics"]
        assert "roughness_std_m" in unsupported
        assert "terrain_relief_p95_p05_m" in unsupported


def test_mars_mola_body_is_mars_in_fixture_crs():
    manifest = _load("mars-mola")
    for cell in manifest["cells"]:
        crs = CRS.from_wkt(cell["fixture"]["crs_wkt"])
        assert classify_body_from_crs(crs) == "Mars"
        assert crs.ellipsoid.semi_major_metre == pytest.approx(3_396_000.0)


def test_verified_source_constants_state_real_resolution_and_license():
    assert MOON_LOLA128.body == "Moon"
    assert MOON_LOLA128.native_resolution_m == pytest.approx(236.9012, abs=0.001)
    assert MARS_CTX_JEZERO.body == "Mars"
    assert MARS_CTX_JEZERO.native_resolution_m == 20.0
    assert MARS_MOLA_MEG128.body == "Mars"
    assert MARS_MOLA_MEG128.native_resolution_m == 463.0576672
    assert "not stated" in MARS_MOLA_MEG128.license
    assert "not stated" in MOON_LOLA128.license
