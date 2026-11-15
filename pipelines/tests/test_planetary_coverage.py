"""Offline tests for the committed Moon/Mars coverage inventory and Mars D01 case."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from analog_pipelines.coarse_features import extract_coarse_features
from analog_pipelines.features import horn_slope_degrees
from analog_pipelines.planetary_coverage import (
    COVERAGE_INVENTORY_VERSION,
    DIMENSION_CODES,
    PlanetaryCoverageError,
    coarse_case_summary,
    load_planetary_coarse_index,
    mars_d01_case,
    planetary_science_coverage,
    typed_dimension_counts,
    write_coverage_inventory,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COARSE_ROOT = REPO_ROOT / "pipelines/planetary/coarse"
FIXTURE_ROOT = REPO_ROOT / "samples/planetary/coarse/fixtures"
CASE_MANIFESTS = (
    "index.json",
    "moon_lola128_window01.json",
    "mars_ctx_jezero_window01.json",
    "mars_mola_meg128_evidence.json",
)


@pytest.fixture(scope="module")
def inventory() -> dict[str, Any]:
    return planetary_science_coverage()


@pytest.fixture(scope="module")
def case() -> dict[str, Any]:
    return mars_d01_case()


def _copy_committed_case_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    coarse = root / "pipelines/planetary/coarse"
    coarse.mkdir(parents=True)
    for name in CASE_MANIFESTS:
        shutil.copy2(COARSE_ROOT / name, coarse / name)
    shutil.copytree(FIXTURE_ROOT, root / "samples/planetary/coarse/fixtures")
    return root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inventory_has_both_bodies_and_all_dimensions(inventory):
    assert inventory["metadata"]["inventory_version"] == COVERAGE_INVENTORY_VERSION
    assert inventory["metadata"]["claim_scope"] == "bounded_committed_subset_not_global"
    assert inventory["claim_scope"] == "bounded_committed_subset_not_global"
    assert set(inventory["bodies"]) == {"Moon", "Mars"}
    for body in ("Moon", "Mars"):
        payload = inventory["bodies"][body]
        assert tuple(payload["typed_by_dimension"]) == DIMENSION_CODES
        assert tuple(payload["dimension_support"]) == DIMENSION_CODES
        assert payload["coarse_d01"]["status"] == "present"


def test_inventory_coarse_cells_are_derived_and_mola_is_separate(inventory):
    moon = inventory["bodies"]["Moon"]["coarse_d01"]
    mars = inventory["bodies"]["Mars"]["coarse_d01"]

    assert moon["cell_count"] == 4
    assert moon["comparable_cell_count"] == 4
    assert moon["evidence_only_cell_count"] == 0
    assert moon["region_ids"] == ["moon-lola128-eqc-window-01"]

    assert mars["cell_count"] == 6
    assert mars["comparable_cell_count"] == 4
    assert mars["evidence_only_cell_count"] == 2
    assert set(mars["region_ids"]) == {
        "mars-ctx-jezero-window-01",
        "mars-mola-meg128-evidence-01",
    }
    assert mars["evidence_only_separated"] is True
    assert len(mars["comparable_candidate_ids"]) == 4
    assert len(mars["evidence_only_candidate_ids"]) == 2
    assert not set(mars["comparable_candidate_ids"]) & set(mars["evidence_only_candidate_ids"])
    assert mars["cross_body_ranking"]["enabled"] is False
    assert mars["resolutions"]["native_resolution_m"] == [20.0, 463.0576672]
    assert moon["resolutions"]["native_resolution_m"] == pytest.approx([236.90117518866782])

    summary = coarse_case_summary("Mars")
    assert summary["evidence_only_region_ids"] == ["mars-mola-meg128-evidence-01"]
    assert summary["comparable_region_ids"] == ["mars-ctx-jezero-window-01"]
    # The evidence-only bbox must not widen the comparable aggregate.
    assert summary["comparable_bbox"]["north"] == pytest.approx(18.642018368217318)
    assert summary["evidence_only_bbox"]["north"] > summary["comparable_bbox"]["north"]


def test_typed_dimension_counts_are_derived_from_committed_files(inventory):
    typed = typed_dimension_counts()
    assert typed["missing_files"] == []
    assert typed["ignored_rows"] == []
    groups = typed["groups"]
    assert groups["Mars"]["D02"]["observation_count"] == 42
    assert "usgs-jezero-sim3464" in groups["Mars"]["D02"]["dataset_ids"]
    assert "crism-mtrdr-hrl000040ff" in groups["Mars"]["D02"]["dataset_ids"]
    assert groups["Moon"]["D02"]["observation_count"] == 3
    assert groups["Moon"]["D03"]["observation_count"] == 36
    assert groups["Mars"]["D03"]["observation_count"] == 5
    assert groups["Moon"]["D04"]["observation_count"] == 53
    assert groups["Mars"]["D04"]["observation_count"] == 2
    assert groups["Moon"]["D05"]["observation_count"] == 8
    assert groups["Mars"]["D05"]["observation_count"] == 10
    assert groups["Earth"]["D06"]["observation_count"] == 1995
    assert groups["Earth"]["D06"]["dataset_ids"] == ["soilgrids-250m-v2"]
    for body in ("Moon", "Mars"):
        # Issue #140/#150 added a sparse official-source D06 evidence audit:
        # six explicit no_coverage records per body, never numeric values.
        d06 = groups[body]["D06"]
        assert d06["observation_count"] == 6
        assert d06["states"] == {"no_coverage": 6}
        assert d06["dataset_ids"]
        assert "soilgrids-250m-v2" not in d06["dataset_ids"]

    for body in ("Moon", "Mars"):
        for dimension in DIMENSION_CODES:
            entry = inventory["bodies"][body]["typed_by_dimension"][dimension]
            if entry["observation_count"]:
                assert entry["area_count"] > 0
                assert entry["dataset_ids"]
                assert entry["pilots"]


def test_dimension_statuses_do_not_fabricate_measurements(inventory):
    for body in ("Moon", "Mars"):
        support = inventory["bodies"][body]["dimension_support"]
        for dimension in DIMENSION_CODES:
            entry = support[dimension]
            assert entry["status"] in {
                "present",
                "present_evidence",
                "evidence_only",
                "evidence_no_coverage",
                "absent",
                "not_supported",
            }
            if entry["status"] in {
                "present",
                "present_evidence",
                "evidence_only",
                "evidence_no_coverage",
            }:
                assert entry["record_count"] > 0
                assert entry["dataset_ids"]
            else:
                assert entry["record_count"] == 0
                assert entry["reason"]
            if dimension != "D01":
                assert entry["on_demand_analysis_supported"] is False

    moon_d01 = inventory["bodies"]["Moon"]["dimension_support"]["D01"]
    mars_d01 = inventory["bodies"]["Mars"]["dimension_support"]["D01"]
    assert moon_d01["on_demand_analysis_supported"] is True
    assert moon_d01["on_demand_analysis_scope"] == ("global_lunar_body_via_bounded_lola128_windows")
    assert mars_d01["on_demand_analysis_supported"] is False
    assert mars_d01["on_demand_analysis_supported_within_scope"] is True
    assert mars_d01["on_demand_analysis_scope"] == "jezero_ctx_mosaic_window_only"


def test_d06_moon_mars_evidence_is_no_coverage_and_earth_soilgrids_is_excluded(inventory):
    for body in ("Moon", "Mars"):
        d06 = inventory["bodies"][body]["dimension_support"]["D06"]
        assert d06["status"] == "evidence_no_coverage"
        assert d06["record_count"] == 6
        assert d06["states"] == {"no_coverage": 6}
        assert d06["on_demand_analysis_supported"] is False
        assert d06["dataset_ids"]
        assert "soilgrids-250m-v2" not in d06["dataset_ids"]
        assert "regolith" in d06["reason"].lower()
    earth = inventory["earth_only_exclusions"]["D06"]
    assert earth["dataset_ids"] == ["soilgrids-250m-v2"]
    assert earth["observation_count"] > 0


def test_global_claims_are_explicitly_false(inventory):
    claims = inventory["global_claims"]
    assert claims["global"] is False
    assert claims["global_coverage_claimed"] is False
    assert claims["global_survey_claimed"] is False
    assert claims["body_wide_coverage_claimed"] is False
    assert claims["cross_body_ranking_enabled"] is False
    assert inventory["global_coverage_claimed"] is False
    assert inventory["global_survey_claimed"] is False
    for body in ("Moon", "Mars"):
        assert inventory["bodies"][body]["global_coverage_claimed"] is False


def test_mars_case_has_four_verified_ctx_cells_and_real_provenance(case):
    assert case["body"] == "Mars"
    assert case["cell_count"] == 4
    assert len(set(case["candidate_ids"])) == 4
    assert case["region_id"] == "mars-ctx-jezero-window-01"
    assert case["analysis_resolution_m"] == 250.0
    assert case["source_resolution_m"] == 20.0
    assert case["method_version"] == "coarse-planetary-dem-horn-1.0.0"
    assert case["source"]["provider"] == "USGS Astrogeology Science Center / NASA Mars Missions"
    assert case["license"] == "public domain"
    assert case["license_url"]
    assert case["source_url"].startswith("https://")
    assert case["source_raster_url"].startswith("https://")
    assert case["source"]["frame"] == "Mars_2000_Sphere (IAU2000), body-fixed"
    assert case["verification"]["verified"] is True
    assert case["verification"]["fixture_sha256_match"] is True
    assert case["verification"]["features_recomputed"] is True


def test_mars_case_fixture_checksums_and_features_recompute(case):
    for checksum in case["fixture_checksums"]:
        fixture_path = REPO_ROOT / checksum["path"]
        assert fixture_path.is_file()
        assert _sha256(fixture_path) == checksum["sha256"]

    for cell in case["cells"]:
        fixture_path = REPO_ROOT / cell["fixture"]["path"]
        with rasterio.open(fixture_path) as source:
            array = np.asarray(source.read(1, masked=True).filled(np.nan), dtype="float64")
        pad = int(cell["fixture"]["pad_pixels"])
        slope = horn_slope_degrees(array, float(case["analysis_resolution_m"]))
        core = slope[slice(pad, -pad), slice(pad, -pad)]
        recomputed = extract_coarse_features(core, np.isfinite(core))
        committed = case["features_by_cell"][cell["candidate_id"]]
        assert set(recomputed) == set(committed)
        for feature_id, value in committed.items():
            assert math.isclose(float(value), float(recomputed[feature_id]), rel_tol=1e-9)


def test_mars_case_bbox_is_jezero_and_frames_are_planetary(case):
    bbox = case["bbox"]
    assert 78.0 < bbox["west"] < 78.1
    assert 78.2 < bbox["east"] < 78.3
    assert 18.4 < bbox["south"] < 18.5
    assert 18.62 < bbox["north"] < 18.66
    frame_fields = (
        case["crs_wkt"],
        case["crs_name"],
        case["source"]["frame"],
        case["source"]["coordinate_system"],
    )
    for value in frame_fields:
        assert "EPSG:4326" not in value
        assert "WGS 84" not in value
        assert "WGS84" not in value
    assert case["claims"]["earth_wgs84_used"] is False
    assert case["claims"]["earth_epsg_4326_used"] is False


def test_mars_case_cross_body_gate_and_mola_separation(case):
    assert case["cross_body_ranking"]["enabled"] is False
    assert "coarse_slope_mean_deg" in case["cross_body_ranking"]["blocking_features"]
    assert case["claims"]["cross_body_ranking_enabled"] is False
    assert case["claims"]["distance_or_score_emitted"] is False
    assert case["claims"]["is_mola"] is False
    assert case["claims"]["mola_merged_into_comparable"] is False

    for aggregate in case["aggregate_features"].values():
        assert aggregate["cell_count"] == 4
        assert aggregate["min"] <= aggregate["mean"] <= aggregate["max"]

    mola = case["mola_evidence"]
    assert mola["evidence_only"] is True
    assert mola["not_comparable"] is True
    assert mola["merged_into_comparable"] is False
    assert mola["used_for_ranking"] is False
    assert mola["distance"] is None
    assert mola["cell_count"] == 2
    assert not set(mola["candidate_ids"]) & set(case["comparable_candidate_ids"])
    assert mola["not_comparable_reason"]
    assert "463.0576672" in mola["not_comparable_reason"]
    for pair in mola["pairs"]:
        assert pair["evidence_only"] is True
        assert pair["not_comparable"] is True
        assert pair["used_for_ranking"] is False
        assert pair["distance"] is None
        assert pair["score"] is None


def test_tampered_fixture_copy_fails_verification(tmp_path):
    root = _copy_committed_case_tree(tmp_path)
    target = root / "samples/planetary/coarse/fixtures/mars_8e7ce607c8c9001d.tif"
    payload = bytearray(target.read_bytes())
    payload[-16] ^= 0xFF
    target.write_bytes(bytes(payload))
    with pytest.raises(PlanetaryCoverageError) as excinfo:
        mars_d01_case(root=root)
    assert excinfo.value.code == "fixture_checksum_mismatch"


def test_missing_coarse_index_is_an_explicit_error(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(PlanetaryCoverageError) as excinfo:
        load_planetary_coarse_index(missing)
    assert excinfo.value.code == "coarse_index_missing"
    with pytest.raises(PlanetaryCoverageError) as excinfo:
        mars_d01_case(root=missing)
    assert excinfo.value.code == "coarse_index_missing"


def test_inventory_is_deterministic_and_roundtrips(tmp_path):
    first = planetary_science_coverage()
    second = planetary_science_coverage()
    first_json = json.dumps(first, sort_keys=True, indent=2)
    second_json = json.dumps(second, sort_keys=True, indent=2)
    assert first_json == second_json

    destination = tmp_path / "PLANETARY_COVERAGE_INVENTORY.json"
    written = write_coverage_inventory(destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == written
    assert destination.read_text(encoding="utf-8") == first_json + "\n"
