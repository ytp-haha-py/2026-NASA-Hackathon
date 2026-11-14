"""Validate the committed Copernicus GLO-90 manifest against both contracts."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

import pytest
from analog_api.domain.features import FEATURE_BY_ID, METHOD_VERSION
from analog_api.domain.manifest import Manifest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "pipelines" / "earth" / "manifests" / "copernicus_glo90_earth.json"
SCHEMA_PATH = REPO_ROOT / "contracts" / "pipeline-manifest.schema.json"
FEATURE_IDS = {
    "slope_mean_deg",
    "slope_p90_deg",
    "terrain_relief_p95_p05_m",
    "roughness_std_m",
}


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def model(document: dict) -> Manifest:
    return Manifest.model_validate(document)


def test_manifest_exists():
    assert MANIFEST_PATH.is_file(), f"missing committed manifest: {MANIFEST_PATH}"


def test_manifest_validates_against_json_schema(document: dict):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(document), key=str)
    assert not errors, "\n".join(error.message for error in errors)


def test_manifest_validates_against_api_model(model: Manifest):
    assert model.manifest_version == "1.0.0"
    assert model.contract_version == "1.0.0"
    assert model.data_kind == "observed"


def test_manifest_has_at_least_three_real_windows(model: Manifest):
    assert len(model.candidates) >= 3


def test_candidate_ids_are_unique_and_earth_prefixed(model: Manifest):
    ids = [candidate.location_id for candidate in model.candidates]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"earth-[a-z0-9-]+", location_id) for location_id in ids)


def test_every_candidate_has_all_four_verified_features(model: Manifest):
    for candidate in model.candidates:
        assert set(candidate.features) == FEATURE_IDS, candidate.location_id
        for feature_id, observation in candidate.features.items():
            definition = FEATURE_BY_ID[feature_id]
            assert observation.unit == definition.unit
            assert observation.method_version == METHOD_VERSION
            assert observation.quality == "verified"
            assert observation.extraction_scale_m == 90.0
            assert 0.0 <= observation.valid_fraction <= 1.0
            assert math.isfinite(observation.value)
            assert definition.min_value <= observation.value <= definition.max_value


def test_feature_valid_fraction_matches_candidate(model: Manifest):
    for candidate in model.candidates:
        for observation in candidate.features.values():
            assert observation.valid_fraction == pytest.approx(candidate.valid_fraction)


def test_candidate_centre_lies_inside_measured_bbox(model: Manifest):
    for candidate in model.candidates:
        assert candidate.bbox.west <= candidate.lon <= candidate.bbox.east, candidate.location_id
        assert candidate.bbox.south <= candidate.lat <= candidate.bbox.north, candidate.location_id
        assert candidate.bbox.east > candidate.bbox.west
        assert candidate.bbox.north > candidate.bbox.south


def test_candidate_geometry_matches_method_constants(model: Manifest):
    for candidate in model.candidates:
        assert candidate.window_size_m == 1980.0
        assert candidate.analysis_resolution_m == 90.0
        assert candidate.crs.startswith("EPSG:")
        assert candidate.valid_fraction >= 0.8


def test_window_checksums_are_recorded(model: Manifest):
    pattern = re.compile(r"window_checksum=sha256:[0-9a-f]{64}")
    for candidate in model.candidates:
        assert candidate.notes is not None
        assert pattern.search(candidate.notes), candidate.location_id


def test_dataset_provenance_is_complete(model: Manifest):
    assert len(model.datasets) == 1
    dataset = model.datasets[0]
    assert dataset.dataset_id == "copernicus-dem-glo90"
    assert dataset.source_kind == "earth_dem"
    assert dataset.source_crs == "EPSG:4326"
    assert dataset.native_resolution_m == 90.0
    assert dataset.processing_version == METHOD_VERSION
    assert dataset.license.strip()
    assert dataset.license_url.startswith("https://")
    assert dataset.url.startswith("https://")
    datetime.fromisoformat(dataset.access_timestamp.replace("Z", "+00:00"))
    assert all(candidate.dataset_id == dataset.dataset_id for candidate in model.candidates)


def test_manifest_generated_at_is_iso8601(model: Manifest):
    assert model.generated_at is not None
    datetime.fromisoformat(model.generated_at.replace("Z", "+00:00"))


def test_manifest_contains_no_synthetic_or_suspect_observations(model: Manifest):
    assert model.data_kind == "observed"
    for record in [*model.candidates, *model.references]:
        for observation in record.features.values():
            assert observation.quality == "verified"


def test_manifest_covers_priority_candidates(model: Manifest):
    ids = {candidate.location_id for candidate in model.candidates}
    assert {"earth-atacama-desert", "earth-death-valley", "earth-lanzarote"} <= ids
