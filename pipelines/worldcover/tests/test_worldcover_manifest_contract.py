"""Validate the committed WorldCover context manifest against both contracts."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from analog_api.domain.features import (
    CONTEXT_FEATURE_BY_ID,
    CONTEXT_METHOD_VERSION,
    WORLDCOVER_CLASSES,
)
from analog_api.domain.manifest import Manifest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = (
    REPO_ROOT / "pipelines" / "worldcover" / "manifests" / "worldcover_earth_context.json"
)
EARTH_MANIFEST_PATH = (
    REPO_ROOT / "pipelines" / "earth" / "manifests" / "copernicus_glo90_earth.json"
)
SCHEMA_PATH = REPO_ROOT / "contracts" / "pipeline-manifest.schema.json"
CONTEXT_FEATURE_IDS = {f"landcover_{slug}_fraction" for _, slug, _ in WORLDCOVER_CLASSES}
CLASS_CODE_BY_FEATURE_ID = {
    feature_id: definition.class_code for feature_id, definition in CONTEXT_FEATURE_BY_ID.items()
}


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def earth_document() -> dict:
    return json.loads(EARTH_MANIFEST_PATH.read_text(encoding="utf-8"))


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
    assert len(model.candidates) == 5


def test_dataset_provenance_is_complete(model: Manifest):
    assert len(model.datasets) == 1
    dataset = model.datasets[0]
    assert dataset.dataset_id == "esa-worldcover-2021-v200"
    assert dataset.provider == "European Space Agency (ESA) WorldCover"
    assert dataset.product_id == "ESA WorldCover 2021 v200"
    assert dataset.title == "ESA WorldCover 10 m 2021 v200 land cover"
    assert dataset.source_kind == "landcover"
    assert dataset.source_crs == "EPSG:4326"
    assert dataset.native_resolution_m == 10.0
    assert dataset.processing_version == CONTEXT_METHOD_VERSION
    assert dataset.license.startswith("CC BY 4.0")
    assert dataset.license_url == "https://creativecommons.org/licenses/by/4.0/"
    assert dataset.url == "https://esa-worldcover.org/en/data-access"
    assert dataset.checksum is not None
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", dataset.checksum)
    assert dataset.notes is not None
    assert "not lunar mineralogy" in dataset.notes
    assert "10 m" in dataset.notes and "2021" in dataset.notes
    assert all(candidate.dataset_id == dataset.dataset_id for candidate in model.candidates)
    datetime.fromisoformat(dataset.access_timestamp.replace("Z", "+00:00"))


def test_manifest_generated_at_is_iso8601(model: Manifest):
    assert model.generated_at is not None
    datetime.fromisoformat(model.generated_at.replace("Z", "+00:00"))


def test_candidates_match_the_earth_terrain_manifest(document: dict, earth_document: dict):
    earth_by_id = {record["location_id"]: record for record in earth_document["candidates"]}
    assert set(earth_by_id) == {record["location_id"] for record in document["candidates"]}
    for record in document["candidates"]:
        earth = earth_by_id[record["location_id"]]
        assert record["display_name"] == earth["display_name"]
        assert record["lat"] == earth["lat"]
        assert record["lon"] == earth["lon"]
        assert record["bbox"] == earth["bbox"]
        assert record["window_size_m"] == earth["window_size_m"]
        assert record["analysis_resolution_m"] == earth["analysis_resolution_m"]
        assert record["crs"] == earth["crs"]


def test_terrain_features_are_copied_unchanged(document: dict, earth_document: dict):
    earth_by_id = {record["location_id"]: record for record in earth_document["candidates"]}
    for record in document["candidates"]:
        assert record["features"] == earth_by_id[record["location_id"]]["features"]
        assert "copied verbatim" in record["notes"]


def test_every_candidate_has_all_eleven_context_classes(model: Manifest):
    for candidate in model.candidates:
        assert set(candidate.context_observations) == CONTEXT_FEATURE_IDS, candidate.location_id
        assert len(candidate.context_observations) == 11


def test_context_class_codes_and_metadata(model: Manifest):
    for candidate in model.candidates:
        for feature_id, observation in candidate.context_observations.items():
            assert observation.class_code == CLASS_CODE_BY_FEATURE_ID[feature_id]
            assert observation.unit == "fraction"
            assert observation.method_version == CONTEXT_METHOD_VERSION
            assert observation.source_resolution_m == 10.0
            assert observation.provider == "European Space Agency (ESA) WorldCover"
            assert 0.0 <= observation.value <= 1.0
            assert 0.0 <= observation.valid_fraction <= 1.0


def test_context_fractions_sum_to_one(model: Manifest):
    for candidate in model.candidates:
        total = sum(observation.value for observation in candidate.context_observations.values())
        assert total == pytest.approx(1.0, abs=1e-4), candidate.location_id


def test_context_quality_and_valid_fraction(model: Manifest):
    for candidate in model.candidates:
        for observation in candidate.context_observations.values():
            assert observation.quality == "verified"
            assert observation.valid_fraction >= 0.8
            assert observation.valid_fraction == pytest.approx(
                candidate.context_observations["landcover_bare_sparse_fraction"].valid_fraction
            )


def test_bare_sparse_fraction_is_present_and_class_60(model: Manifest):
    for candidate in model.candidates:
        observation = candidate.context_observations["landcover_bare_sparse_fraction"]
        assert observation.class_code == 60
        assert 0.0 <= observation.value <= 1.0


def test_manifest_contains_no_synthetic_or_suspect_observations(model: Manifest):
    for candidate in model.candidates:
        for observation in candidate.context_observations.values():
            assert observation.quality == "verified"
        for observation in candidate.features.values():
            assert observation.quality == "verified"


def test_candidate_notes_record_worldcover_checksum_and_scope(model: Manifest):
    pattern = re.compile(r"window_checksum=sha256:[0-9a-f]{64}")
    for candidate in model.candidates:
        assert candidate.notes is not None
        assert pattern.search(candidate.notes)
        assert "Earth land-cover context only" in candidate.notes
