"""Offline tests for the reproducible planetary D01 on-demand AOI engine.

No test in this file reaches the network. The live Moon branch is exercised only
with a monkeypatched fake cell; the committed Moon/Mars cases use the real committed
manifests and elevation fixtures.
"""

from __future__ import annotations

import json
import shutil
import types
from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_pipelines.coarse_features import COARSE_FEATURE_BY_ID
from analog_pipelines.features import horn_slope_degrees
from analog_pipelines.planetary_aoi import (
    ANALYSIS_KIND_COMMITTED,
    ANALYSIS_KIND_LIVE,
    COARSE_FEATURE_IDS_TUPLE,
    PLANETARY_AOI_METHOD_VERSION,
    PlanetaryAoiError,
    analyze_planetary_aoi,
    planetary_body_coverage,
    validate_planetary_aoi,
)

ROOT = Path(__file__).resolve().parents[3]
COARSE = ROOT / "pipelines/planetary/coarse"
FIXTURES = ROOT / "samples/planetary/coarse/fixtures"
MOON_MANIFEST = COARSE / "moon_lola128_window01.json"
MARS_CTX_MANIFEST = COARSE / "mars_ctx_jezero_window01.json"
MARS_MOLA_MANIFEST = COARSE / "mars_mola_meg128_evidence.json"

MOON_BBOX = {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85}
MARS_BBOX = {"west": 78.08, "south": 18.47, "east": 78.27, "north": 18.65}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_ids(manifest: dict) -> set[str]:
    return {cell["candidate_id"] for cell in manifest["cells"]}


def _expected_aggregate(manifest: dict) -> tuple[dict[str, float], float]:
    slopes: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    for cell in manifest["cells"]:
        fixture = cell["fixture"]
        with rasterio.open(ROOT / fixture["path"]) as src:
            elevation = np.asarray(src.read(1, masked=True).filled(np.nan), dtype="float64")
        pad = int(fixture["pad_pixels"])
        slope = horn_slope_degrees(elevation, float(cell["analysis_resolution_m"]))
        core = slope[slice(pad, -pad), slice(pad, -pad)]
        slopes.append(core.ravel())
        masks.append(np.isfinite(core).ravel())
    values = np.concatenate(slopes).reshape(1, -1)
    valid = np.concatenate(masks).reshape(1, -1)
    sample = values[valid]
    return (
        {
            "coarse_slope_mean_deg": float(np.mean(sample)),
            "coarse_slope_p90_deg": float(np.percentile(sample, 90)),
            "coarse_flat_fraction_lt5deg": float(np.mean(sample < 5.0)),
        },
        float(valid.sum() / valid.size),
    )


def _copy_committed_catalog(destination: Path) -> Path:
    shutil.copytree(
        COARSE,
        destination / "pipelines/planetary/coarse",
    )
    shutil.copytree(
        FIXTURES,
        destination / "samples/planetary/coarse/fixtures",
    )
    return destination


def test_moon_reproducible_committed_case():
    manifest = _load(MOON_MANIFEST)
    expected, expected_valid_fraction = _expected_aggregate(manifest)

    first_request = validate_planetary_aoi("Moon", MOON_BBOX)
    second_request = validate_planetary_aoi("Moon", MOON_BBOX)
    assert first_request.area_id == second_request.area_id
    assert first_request.area_id.startswith("planet-aoi-moon-")
    assert first_request.cache_key == second_request.cache_key
    assert first_request.crs.startswith("+proj=longlat")
    assert "+R=1737400" in first_request.crs
    assert "4326" not in first_request.crs
    assert first_request.coordinate_frame == "lunar_planetocentric"

    result = analyze_planetary_aoi("Moon", MOON_BBOX)
    assert result.analysis_kind == ANALYSIS_KIND_COMMITTED
    assert result.method_version == PLANETARY_AOI_METHOD_VERSION
    assert result.body == "Moon"
    assert result.coordinate_frame == "lunar_planetocentric"
    assert result.crs == first_request.crs
    assert "4326" not in result.crs
    assert len(result.cells) == 4
    assert set(result.cells) == _candidate_ids(manifest)
    assert {cell["candidate_id"] for cell in result.cell_results} == _candidate_ids(manifest)
    assert result.valid_fraction > 0.99
    assert result.valid_fraction == pytest.approx(expected_valid_fraction)
    assert set(result.features) == set(COARSE_FEATURE_BY_ID)
    for feature_id, value in result.features.items():
        assert np.isfinite(value)
        assert value == pytest.approx(expected[feature_id], rel=1e-9, abs=1e-12)
    assert result.requested_bbox == MOON_BBOX
    assert result.analysis_bbox != MOON_BBOX
    assert any("requested_bbox and analysis_bbox differ" in item for item in result.warnings)
    assert result.area_km2 > 0
    assert result.analysis_area_km2 is not None and result.analysis_area_km2 > 0
    assert result.checksum.startswith("sha256:")
    json.dumps(result.to_payload())


def test_moon_provenance_units_and_no_fabricated_dimensions():
    manifest = _load(MOON_MANIFEST)
    fixture_sha = {cell["candidate_id"]: cell["fixture"]["sha256"] for cell in manifest["cells"]}
    result = analyze_planetary_aoi("Moon", MOON_BBOX)

    assert result.dataset["source_id"] == "moon-lola128-pa-pixel-202405"
    assert "NASA" in result.dataset["provider"]
    assert "LOLA" in result.dataset["provider"]
    assert "pgda.gsfc.nasa.gov" in result.dataset["raster_url"]
    assert "not stated" in result.dataset["license"]
    assert result.dataset["native_resolution_m"] == pytest.approx(236.90117518866782)
    assert result.dataset["source_method_version"] == "coarse-planetary-dem-horn-1.0.0"
    assert result.dataset["source_manifests"][0]["sha256"]

    assert len(result.source_assets) == 4
    for asset in result.source_assets:
        assert asset["cell_id"] in fixture_sha
        assert asset["sha256"] == fixture_sha[asset["cell_id"]]
        assert asset["checksum"] == f"sha256:{fixture_sha[asset['cell_id']]}"
        assert asset["uri"].startswith("samples/planetary/coarse/fixtures/")
        assert (ROOT / asset["path"]).is_file()

    d01 = result.dimension_support["D01"]
    assert d01["status"] == "present"
    assert d01["cell_count"] == 4
    assert set(d01["feature_ids"]) == set(COARSE_FEATURE_BY_ID)
    for code in ("D02", "D03", "D04", "D05", "D06"):
        entry = result.dimension_support[code]
        assert entry["status"] == "unsupported_for_on_demand_analysis"
        assert entry["value"] is None
        assert entry["reason"]
        assert "value" not in result.features
    assert result.dimension_support["D03"]["stored_evidence_only"] is True
    assert result.dimension_support["D05"]["stored_evidence_only"] is True
    assert result.dimension_support["D02"]["stored_evidence_only"] is False

    status = result.provider_status["moon-lola128-pa-pixel-202405"]
    assert status["status"] == "verified_committed_fixture"
    assert status["network_used"] is False
    assert status["fixture_checksums_verified"] is True
    assert status["cells"] == 4
    assert not any("failed" in item for item in result.provider_status)


def test_moon_out_of_coverage_is_explicit():
    bbox = {"west": -0.02, "south": -0.02, "east": 0.02, "north": 0.02}
    with pytest.raises(PlanetaryAoiError) as captured:
        analyze_planetary_aoi("Moon", bbox)
    assert captured.value.code == "out_of_coverage"
    assert "moon-lola128-eqc-window-01" in captured.value.message
    assert "allow_remote=True" in captured.value.message
    assert "basemap" in captured.value.message


def test_moon_live_path_is_labelled_and_uses_the_bounded_reader(monkeypatch, tmp_path):
    from analog_pipelines import planetary_aoi as module

    fixture_cell = _load(MOON_MANIFEST)["cells"][0]
    with rasterio.open(ROOT / fixture_cell["fixture"]["path"]) as src:
        elevation = np.asarray(src.read(1, masked=True).filled(np.nan), dtype="float64")

    def fake_live_cell(request):
        return types.SimpleNamespace(
            candidate_id="planet-coarse-v1:livefake00000000",
            region_id="moon-lola128-on-demand-live-01",
            fixture=elevation,
            fixture_transform=rasterio.Affine(*fixture_cell["fixture"]["transform"]),
            pad_pixels=int(fixture_cell["fixture"]["pad_pixels"]),
            analysis_resolution_m=250.0,
            source_resolution_m=236.90117518866782,
            window_size_m=5000.0,
            bbox_body_lonlat={
                "west": -0.02,
                "south": -0.02,
                "east": 0.02,
                "north": 0.02,
            },
            bbox_metric={"west": -2500.0, "south": -2500.0, "east": 2500.0, "north": 2500.0},
            crs_wkt=fixture_cell["crs_wkt"],
            crs_name="Lunar_Azimuthal_Equidistant_on_demand",
            source_read_bytes_upper_bound=1114112,
            source_window_shape=(27, 28),
        )

    monkeypatch.setattr(module, "_live_moon_cell", fake_live_cell)
    bbox = {"west": -0.02, "south": -0.02, "east": 0.02, "north": 0.02}
    result = analyze_planetary_aoi("Moon", bbox, allow_remote=True)
    assert result.analysis_kind == ANALYSIS_KIND_LIVE
    assert result.cells == ("planet-coarse-v1:livefake00000000",)
    assert result.provider_status["moon-lola128-pa-pixel-202405"]["network_used"] is True
    assert result.provider_status["moon-lola128-pa-pixel-202405"]["status"] == (
        "live_bounded_read_ok"
    )
    assert result.source_assets[0]["sha256"] is None
    assert result.source_assets[0]["uri"].startswith("https://pgda.gsfc.nasa.gov/")
    assert any("new bounded live" in item for item in result.warnings)
    assert result.dimension_support["D01"]["analysis_kind"] == ANALYSIS_KIND_LIVE


def test_mars_reproducible_committed_case_excludes_mola_evidence():
    ctx_manifest = _load(MARS_CTX_MANIFEST)
    mola_manifest = _load(MARS_MOLA_MANIFEST)
    expected, expected_valid_fraction = _expected_aggregate(ctx_manifest)

    result = analyze_planetary_aoi("Mars", MARS_BBOX)
    assert result.analysis_kind == ANALYSIS_KIND_COMMITTED
    assert result.body == "Mars"
    assert result.coordinate_frame == "mars_planetocentric"
    assert result.crs.startswith("+proj=longlat")
    assert "+R=3396000" in result.crs
    assert "4326" not in result.crs
    assert len(result.cells) == 4
    assert set(result.cells) == _candidate_ids(ctx_manifest)
    assert set(result.cells).isdisjoint(_candidate_ids(mola_manifest))
    assert result.valid_fraction == pytest.approx(expected_valid_fraction)
    for feature_id, value in result.features.items():
        assert value == pytest.approx(expected[feature_id], rel=1e-9, abs=1e-12)
    assert result.dataset["source_id"] == "mars2020-jezero-ctx-dem-20m"
    assert result.dataset["native_resolution_m"] == pytest.approx(20.0)
    assert result.dataset["license"] == "public domain"

    coverage = planetary_body_coverage("Mars")
    assert coverage.cell_count == 6
    assert coverage.comparable_cell_count == 4
    assert coverage.evidence_cell_count == 2
    assert len(coverage.regions) == 2
    assert set(coverage.evidence_only_cell_ids) == _candidate_ids(mola_manifest)
    assert set(coverage.evidence_only_cell_ids).isdisjoint(_candidate_ids(ctx_manifest))
    assert coverage.evidence_bbox is not None
    assert coverage.bbox is not None
    assert coverage.evidence_bbox != coverage.bbox
    assert coverage.dimension_support["D01"]["comparable_cell_count"] == 4
    assert coverage.dimension_support["D01"]["evidence_cell_count"] == 2
    assert "never merged" in coverage.notes
    assert any(
        region["support_status"] == "evidence_only_native_support" for region in coverage.regions
    )
    assert any("MOLA" in item and "evidence-only" in item for item in result.warnings)


def test_moon_and_mars_coverage_are_body_isolated():
    moon = planetary_body_coverage("Moon")
    mars = planetary_body_coverage("Mars")
    assert moon.cell_count == 4
    assert moon.comparable_cell_count == 4
    assert moon.evidence_cell_count == 0
    assert moon.bbox == {
        "west": 19.832481429354193,
        "south": -10.16488943108404,
        "east": 20.167518570645804,
        "north": -9.835068981384325,
    }
    moon_ids = {cell.candidate_id for cell in moon.cells}
    mars_ids = {cell.candidate_id for cell in mars.cells}
    assert moon_ids.isdisjoint(mars_ids)
    assert all(cell.body == "Moon" for cell in moon.cells)
    assert all(cell.body == "Mars" for cell in mars.cells)
    assert moon.sources[0]["source_id"] == "moon-lola128-pa-pixel-202405"
    assert {source["source_id"] for source in mars.sources} == {
        "mars2020-jezero-ctx-dem-20m",
        "mars-mola-megdr-meg128-t44n000",
    }


def test_antimeridian_polar_and_planetocentric_validation():
    crossing = validate_planetary_aoi(
        "Moon",
        {"west": 179.9, "south": -0.05, "east": -179.9, "north": 0.05},
    )
    assert crossing.crossing_antimeridian is True
    assert crossing.geometry["type"] == "MultiPolygon"
    assert crossing.width_m > 1000
    assert crossing.area_km2 > 0
    assert "4326" not in crossing.crs

    polar = validate_planetary_aoi(
        "Moon",
        {"west": -10.0, "south": 89.5, "east": 10.0, "north": 89.55},
    )
    assert polar.crossing_antimeridian is False
    assert polar.height_m > 1000
    assert polar.area_km2 > 0

    with pytest.raises(PlanetaryAoiError) as wide:
        validate_planetary_aoi(
            "Moon",
            {"west": -170.0, "south": -1.0, "east": 170.0, "north": 1.0},
        )
    assert wide.value.code == "bbox_too_wide"

    with pytest.raises(PlanetaryAoiError) as large:
        validate_planetary_aoi(
            "Moon",
            {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0},
        )
    assert large.value.code == "aoi_too_large"

    with pytest.raises(PlanetaryAoiError) as small:
        validate_planetary_aoi(
            "Moon",
            {"west": 0.0, "south": 0.0, "east": 0.001, "north": 0.001},
        )
    assert small.value.code == "aoi_too_small"

    with pytest.raises(PlanetaryAoiError) as body:
        validate_planetary_aoi("Venus", MOON_BBOX)
    assert body.value.code == "unsupported_body"

    with pytest.raises(PlanetaryAoiError) as latitude:
        validate_planetary_aoi(
            "Moon",
            {"west": 0.0, "south": 90.5, "east": 0.1, "north": 95.0},
        )
    assert latitude.value.code == "invalid_bbox"

    with pytest.raises(PlanetaryAoiError) as ordering:
        validate_planetary_aoi(
            "Moon",
            {"west": 0.0, "south": 1.0, "east": 0.1, "north": 0.0},
        )
    assert ordering.value.code == "invalid_bbox"

    with pytest.raises(PlanetaryAoiError) as nonfinite:
        validate_planetary_aoi(
            "Moon",
            {"west": 0.0, "south": 0.0, "east": 0.1, "north": float("nan")},
        )
    assert nonfinite.value.code == "invalid_bbox"


def test_missing_and_corrupt_catalog_are_explicit(tmp_path: Path):
    with pytest.raises(PlanetaryAoiError) as missing_index:
        analyze_planetary_aoi("Moon", MOON_BBOX, root=tmp_path)
    assert missing_index.value.code == "index_missing"

    _copy_committed_catalog(tmp_path)
    (tmp_path / "pipelines/planetary/coarse/index.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    with pytest.raises(PlanetaryAoiError) as corrupt_index:
        analyze_planetary_aoi("Moon", MOON_BBOX, root=tmp_path)
    assert corrupt_index.value.code == "index_corrupt"


def test_corrupt_manifest_and_tampered_fixture_are_explicit(tmp_path: Path):
    _copy_committed_catalog(tmp_path)
    moon_manifest = tmp_path / "pipelines/planetary/coarse/moon_lola128_window01.json"
    moon_manifest.write_text('{"schema_version": "broken"', encoding="utf-8")
    with pytest.raises(PlanetaryAoiError) as corrupt_manifest:
        analyze_planetary_aoi("Moon", MOON_BBOX, root=tmp_path)
    assert corrupt_manifest.value.code == "manifest_corrupt"

    other = tmp_path / "tampered"
    _copy_committed_catalog(other)
    fixture = other / "samples/planetary/coarse/fixtures/moon_c22eb2732afdfa9a.tif"
    fixture.write_bytes(fixture.read_bytes() + b"tampered")
    with pytest.raises(PlanetaryAoiError) as tampered:
        analyze_planetary_aoi("Moon", MOON_BBOX, root=other)
    assert tampered.value.code == "fixture_checksum_mismatch"


def test_result_identity_is_independent_of_catalog_location(tmp_path: Path):
    _copy_committed_catalog(tmp_path)
    original = analyze_planetary_aoi("Moon", MOON_BBOX)
    copied = analyze_planetary_aoi("Moon", MOON_BBOX, root=tmp_path)
    assert copied.area_id == original.area_id
    assert copied.checksum == original.checksum
    assert copied.features == original.features
    assert copied.cells == original.cells
    assert copied.dataset["index_path"] == "pipelines/planetary/coarse/index.json"
    assert copied.source_assets[0]["uri"].startswith("samples/planetary/coarse/fixtures/")
    assert (
        validate_planetary_aoi("Moon", MOON_BBOX).cache_key
        == validate_planetary_aoi("Moon", MOON_BBOX, root=tmp_path).cache_key
    )


def test_missing_fixture_is_explicit(tmp_path: Path):
    _copy_committed_catalog(tmp_path)
    fixture = tmp_path / "samples/planetary/coarse/fixtures/moon_c22eb2732afdfa9a.tif"
    fixture.unlink()
    with pytest.raises(PlanetaryAoiError) as missing:
        analyze_planetary_aoi("Moon", MOON_BBOX, root=tmp_path)
    assert missing.value.code == "fixture_missing"


def test_result_features_are_only_the_shared_coarse_d01_ids():
    moon = analyze_planetary_aoi("Moon", MOON_BBOX)
    mars = analyze_planetary_aoi("Mars", MARS_BBOX)
    assert set(moon.features) == set(COARSE_FEATURE_IDS_TUPLE)
    assert set(mars.features) == set(COARSE_FEATURE_IDS_TUPLE)
    for result in (moon, mars):
        for cell in result.cell_results:
            assert set(cell["features"]) == set(COARSE_FEATURE_IDS_TUPLE)
            assert "roughness_std_m" not in cell["features"]
            assert "terrain_relief_p95_p05_m" not in cell["features"]
            assert cell["support_status"] == "analysis_eligible_250m"
