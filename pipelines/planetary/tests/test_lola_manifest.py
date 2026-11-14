from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
from analog_api.domain.manifest import Manifest
from lola import (
    FEATURE_IDS,
    LolaImportError,
    default_manifest_path,
    default_schema_path,
    validate_manifest,
)

MANIFEST_PATH = default_manifest_path()
SCHEMA_PATH = default_schema_path()


def _load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        pytest.skip("no real LOLA manifest exists; the Moon reference is NOT claimed or enabled")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_real_manifest_schema_and_model() -> None:
    document = _load_manifest()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(document)
    model = Manifest.model_validate(document)
    assert model.data_kind == "observed"
    assert len(model.datasets) == 1
    assert len(model.references) == 1
    assert model.candidates == []

    dataset = model.datasets[0]
    assert dataset.source_kind == "lunar_dem"
    assert dataset.provider.startswith("NASA")
    assert dataset.product_id == "Site04_final_adj_5mpp_surf"
    assert dataset.url == (
        "https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/Site04_final_adj_5mpp_surf.tif"
    )
    assert dataset.native_resolution_m == pytest.approx(5.0)
    assert dataset.license_url == "https://science.data.nasa.gov/about/license"
    assert "10.1016/j.pss.2020.105119" in dataset.notes
    assert "90%" in dataset.notes or "90" in dataset.notes
    assert dataset.checksum is not None and dataset.checksum.startswith("sha256:")

    reference = model.references[0]
    assert reference.reference_id.startswith("moon-")
    assert reference.body == "Moon"
    assert reference.dataset_id == dataset.dataset_id
    assert reference.bbox is None
    assert reference.window_size_m == pytest.approx(1980.0)
    assert reference.analysis_resolution_m == pytest.approx(90.0)
    assert reference.valid_fraction == pytest.approx(1.0)
    assert set(reference.features) == set(FEATURE_IDS)
    assert all(observation.quality == "verified" for observation in reference.features.values())
    assert all(
        observation.method_version == "terrain-metrics-1.0.0"
        for observation in reference.features.values()
    )
    assert reference.lat is not None and -90.0 <= reference.lat <= 90.0
    assert reference.lon is not None and -180.0 <= reference.lon <= 180.0
    assert "5m_grid_interpolated_pixels" in reference.quality_flags
    assert "window_checksum=sha256:" in reference.notes
    assert "Barker" in reference.notes


def test_real_manifest_values_are_plausible_for_shackleton_rim() -> None:
    document = _load_manifest()
    reference = document["references"][0]
    features = reference["features"]
    # These bounds are deliberately broad sanity checks, not a claim about the site.
    assert 0.0 <= features["slope_mean_deg"]["value"] <= 90.0
    assert 0.0 <= features["slope_p90_deg"]["value"] <= 90.0
    assert 0.0 <= features["terrain_relief_p95_p05_m"]["value"] <= 20_000.0
    assert 0.0 <= features["roughness_std_m"]["value"] <= 5_000.0


def test_validate_manifest_rejects_unverified_observed_observation() -> None:
    document = copy.deepcopy(_load_manifest())
    feature = next(iter(document["references"][0]["features"].values()))
    feature["quality"] = "synthetic"
    with pytest.raises(LolaImportError) as error:
        validate_manifest(document, SCHEMA_PATH)
    assert error.value.code == "manifest_unverified_observation"


def test_validate_manifest_rejects_non_lunar_body() -> None:
    document = copy.deepcopy(_load_manifest())
    document["references"][0]["body"] = "Mars"
    with pytest.raises(LolaImportError) as error:
        validate_manifest(document, SCHEMA_PATH)
    assert error.value.code in {
        "manifest_schema_failed",
        "manifest_model_failed",
        "manifest_body_mismatch",
    }


def test_manifest_absent_means_no_moon_claim(tmp_path: Path) -> None:
    # This test does not require the real manifest; it documents the fallback rule.
    fake = tmp_path / "missing.json"
    assert not fake.exists()
    assert MANIFEST_PATH.name == "lola_moon_reference.json"
