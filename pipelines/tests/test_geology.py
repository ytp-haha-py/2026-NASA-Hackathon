"""Offline scientific and portability checks for the independent geology dimension.

All numbers constructed in this file are synthetic TEST ONLY. No fixture is promoted
to the committed source-derived manifests.
"""

import json
import shutil
from pathlib import Path

import jsonschema
import numpy as np
import pytest
from analog_pipelines.geology.__main__ import DEFAULT_ROOT, external_cache, selected_areas
from analog_pipelines.geology.areas import existing_areas, polygon
from analog_pipelines.geology.io import (
    Cache,
    build_snapshot,
    safe_path,
    verify_snapshot,
    write_json,
)
from analog_pipelines.geology.matching import compare_minerals, match_categories
from analog_pipelines.geology.mineralogy import summarize_identification
from analog_pipelines.geology.models import Area, Manifest, Mineral, validate_areas
from analog_pipelines.geology.taxonomy import glim_mapping
from pydantic import ValidationError

REPO = Path(__file__).resolve().parents[2]


def mineral(**kwargs):
    values = {
        "evidence_id": "synthetic-test-only:kaolinite",
        "mineral": "kaolinite",
        "state": "detected",
        "value": 0.1,
        "unit": "dimensionless_band_depth",
        "measurement": "band_depth",
        "semantics_id": "synthetic-test-only:emit-group-1-band-depth-v1",
        "method": "synthetic-test-only:native_pixel_aoi_mean",
        "valid_fraction": 0.8,
        "uncertainty": 0.02,
        "uncertainty_unit": "dimensionless_band_depth",
        "quality": "verified",
        "limitations": ["SYNTHETIC TEST ONLY"],
    }
    return Mineral(**(values | kwargs))


def portable():
    snapshot = json.loads((DEFAULT_ROOT / "snapshots/geology.snapshot.json").read_text())
    return verify_snapshot(DEFAULT_ROOT, snapshot)


def test_existing_area_ids_and_planetary_geometry_are_preserved():
    areas = existing_areas(REPO)
    assert len(areas) == 6
    assert {a.area_id for a in areas if a.body == "Earth"} == {
        "earth-atacama-desert",
        "earth-death-valley",
        "earth-lanzarote",
        "earth-devon-island",
        "earth-rio-tinto",
    }
    moon = next(a for a in areas if a.body == "Moon")
    assert moon.geometry["coordinates"][0][0] == [-3747.5, -7332.5]
    assert "1737400" in moon.crs


@pytest.mark.parametrize("body", ["Moon", "Mars"])
def test_planetary_area_cannot_use_earth_wgs84(body):
    with pytest.raises(ValueError, match="CRS ellipsoid"):
        Area(
            area_id="synthetic",
            display_name="TEST ONLY",
            body=body,
            crs="EPSG:4326",
            geometry=polygon(0, 0, 1, 1),
            origin="test_aoi",
            geometry_source="synthetic",
        )


def test_fingerprint_ignores_ring_start_winding_but_detects_changed_region():
    original = existing_areas(REPO)[0]
    data = original.model_dump()
    points = data["geometry"]["coordinates"][0][:-1]
    rotated = list(reversed(points[1:] + points[:1]))
    data["geometry"]["coordinates"] = [rotated + [rotated[0]]]
    assert Area.model_validate(data).geometry_fingerprint == original.geometry_fingerprint
    data["geometry"]["coordinates"][0][1][0] += 0.001
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        Area.model_validate(data)


def test_duplicate_ids_and_changed_existing_external_aoi_rejected(tmp_path):
    area = existing_areas(REPO)[0]
    with pytest.raises(ValueError, match="duplicate Area ID"):
        validate_areas([area, area])
    data = area.model_dump()
    data["geometry_fingerprint"] = ""
    data["geometry"] = polygon(10, 10, 11, 11)
    path = tmp_path / "areas.json"
    write_json(path, {"areas": [data]})
    with pytest.raises(ValueError, match="changed geometry"):
        selected_areas(path)


@pytest.mark.parametrize(
    "coordinates",
    [
        [[[0, 0], [1, 0], [1, 1]]],
        [[[179, 0], [-179, 0], [-179, 1], [179, 0]]],
        [[[0, 0], [float("nan"), 0], [1, 1], [0, 0]]],
        [[[0, 0], [0, 0], [0, 0], [0, 0]]],
    ],
)
def test_invalid_coordinate_inputs_rejected(coordinates):
    data = existing_areas(REPO)[0].model_dump()
    data["geometry_fingerprint"] = ""
    data["geometry"] = {"type": "Polygon", "coordinates": coordinates}
    with pytest.raises(ValueError):
        Area.model_validate(data)


@pytest.mark.parametrize("state", ["unknown", "no_coverage", "not_applicable"])
def test_missing_minerals_cannot_carry_numeric_values(state):
    with pytest.raises(ValueError, match="null"):
        mineral(state=state)
    absent = mineral(state=state, value=None, uncertainty=None, valid_fraction=None)
    assert absent.value is None
    assert compare_minerals(absent, mineral())["absolute_difference"] is None


@pytest.mark.parametrize("fraction", [None, 0, -1, 1.1])
def test_detection_requires_positive_valid_coverage(fraction):
    with pytest.raises(ValueError):
        mineral(valid_fraction=fraction)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {"unit": "wt%"},
        {"unit": None},
        {"semantics_id": None},
        {"uncertainty": -1},
        {"uncertainty_unit": None},
        {"state": "not_detected_under_valid_observation", "value": 0},
    ],
)
def test_inconsistent_mineral_units_and_states_rejected(kwargs):
    with pytest.raises(ValueError):
        mineral(**kwargs)


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"semantics_id": "different-spectral-estimator"}, "incompatible_observation_semantics"),
        ({"measurement": "model_abundance", "unit": "vol%"}, "incompatible_observation_semantics"),
        ({"method": "different-native-support"}, "incompatible_observation_semantics"),
        ({"quality": "context_only"}, "context_only_or_unverified"),
        ({"mineral": "olivine"}, "different_minerals"),
    ],
)
def test_only_compatible_mineral_semantics_compared(kwargs, reason):
    result = compare_minerals(mineral(), mineral(**kwargs))
    assert result["compared"] is False
    assert result["reason"] == reason
    assert result["absolute_difference"] is None


def test_compatible_minerals_expose_units_coverage_and_evidence():
    result = compare_minerals(mineral(), mineral(value=0.15, valid_fraction=0.5))
    assert result["compared"]
    assert result["absolute_difference"] == pytest.approx(0.05)
    assert result["right_valid_fraction"] == 0.5
    assert result["unit"] == "dimensionless_band_depth"
    assert len(result["evidence_ids"]) == 2


def test_native_identification_quality_uncertainty_and_no_coverage():
    ids = np.array([[1, 1], [0, 2]])
    depth = np.array([[0.2, 0.8], [np.nan, 0.3]])
    uncertainty = np.array([[0.02, 0.03], [np.nan, np.nan]])
    inside = np.ones((2, 2), dtype=bool)
    quality = np.array([[True, False], [True, True]])
    result = summarize_identification(1, ids, depth, uncertainty, inside, quality)
    assert result["value"] == 0.2  # cloudy and missing-uncertainty pixels excluded
    assert result["valid_fraction"] == 0.5
    assert result["uncertainty"] == 0.02
    missing = summarize_identification(2, ids, depth, uncertainty, inside, quality)
    assert missing["state"] == "not_detected_under_valid_observation"
    assert missing["value"] is None
    unknown = summarize_identification(1, ids, depth, uncertainty, inside, inside & False)
    assert unknown["state"] == "unknown" and unknown["value"] is None
    no_coverage = summarize_identification(1, ids, depth, uncertainty, inside & False, quality)
    assert no_coverage["state"] == "no_coverage"
    with pytest.raises(ValueError, match="pixel support"):
        summarize_identification(1, ids, depth, uncertainty, inside[0], quality)


def test_broad_volcanics_never_become_basalt_or_minerals():
    rocks, category = glim_mapping("vb")
    assert category == "igneous"
    assert rocks == ["basic_volcanic_rocks"]
    assert "basalt" not in rocks and "olivine" not in rocks
    assert glim_mapping("unknown") == ([], None)
    assert glim_mapping("sc")[1] == "sedimentary"  # carbonates do not prove aqueous alteration


def test_portable_manifest_and_json_schema_are_valid_without_raw_files():
    manifest = portable()
    schema = json.loads((DEFAULT_ROOT / "schemas/manifest.schema.json").read_text())
    jsonschema.validate(manifest.model_dump(mode="json"), schema)
    assert manifest.data_kind == "source_derived"
    assert all(a.origin == "test_aoi" for a in manifest.areas if a.body == "Mars")
    assert not any(m.value is not None for r in manifest.analyses for m in r.mineralogy)


def test_independent_manifests_do_not_enter_shared_terrain_contract_discovery():
    assert not list(DEFAULT_ROOT.glob("**/manifests/*.json"))
    snapshot = json.loads((DEFAULT_ROOT / "snapshots/geology.snapshot.json").read_text())
    assert sum(entry["path"].startswith("records/") for entry in snapshot["files"]) == 1


def test_process_cannot_be_inferred_only_from_minerals():
    data = portable().model_dump(mode="json")
    record = next(r for r in data["analyses"] if r["lithology"])
    record["geological_processes"][0]["evidence_ids"] = ["unavailable:olivine"]
    with pytest.raises(ValueError, match="geological evidence"):
        Manifest.model_validate(data)


@pytest.mark.parametrize("mutation", ["fingerprint", "body", "dataset", "rights"])
def test_manifest_rejects_cross_area_cross_body_and_unverified_rights(mutation):
    data = portable().model_dump(mode="json")
    record = next(r for r in data["analyses"] if r["lithology"])
    if mutation == "fingerprint":
        record["geometry_fingerprint"] = "sha256:" + "0" * 64
    elif mutation == "body":
        record["body"] = "Earth"
    elif mutation == "dataset":
        record["dataset_id"] = "nonexistent"
    else:
        next(d for d in data["datasets"] if d["dataset_id"] == record["dataset_id"])[
            "redistribution"
        ] = "not_verified"
    with pytest.raises(ValueError):
        Manifest.model_validate(data)


def test_category_results_disclose_unknown_earth_features_instead_of_false_matches():
    manifest = portable()
    result = match_categories(manifest.analyses, ["impact", "aqueous_alteration"], ["basalt"])
    assert result["indexed_earth_count"] == 5
    for row in result["results"]:
        assert row["matched_categories"] == []
        assert row["unknown_categories"] == ["aqueous_alteration", "impact"]
        assert row["unknown_rocks"] == ["basalt"]
        assert row["evidence"] == []
    with pytest.raises(ValueError):
        match_categories(manifest.analyses, ["impact", "impact"])
    with pytest.raises(ValueError):
        match_categories(manifest.analyses, ["biology"])


def test_snapshot_relocates_and_detects_artifact_tampering(tmp_path):
    snapshot = json.loads((DEFAULT_ROOT / "snapshots/geology.snapshot.json").read_text())
    for entry in snapshot["files"]:
        target = tmp_path / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_ROOT / entry["path"], target)
    assert verify_snapshot(tmp_path, snapshot).areas
    rebuilt = build_snapshot(tmp_path, [e["path"] for e in snapshot["files"]])
    assert rebuilt["snapshot_id"] == snapshot["snapshot_id"]
    target = tmp_path / snapshot["files"][0]["path"]
    target.write_text(target.read_text() + "\n")
    with pytest.raises(ValueError, match="mismatch"):
        verify_snapshot(tmp_path, snapshot)


@pytest.mark.parametrize("name", ["../outside", "/etc/passwd", "dir/../../outside", "dir\\outside"])
def test_snapshot_path_escape_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        safe_path(tmp_path, name)


def test_cache_offline_miss_and_no_repository_raw_storage(tmp_path):
    with pytest.raises(FileNotFoundError, match="offline cache miss"):
        Cache(tmp_path, offline=True).fetch("https://example.org/test", "test.json")
    with pytest.raises(ValueError, match="outside"):
        external_cache(REPO / "var/geology-test", True)
