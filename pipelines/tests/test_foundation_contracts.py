from __future__ import annotations

import json
from pathlib import Path

import pytest
from analog_api.domain.features import CONTEXT_FEATURES, FEATURES
from analog_api.domain.manifest import Manifest
from analog_pipelines.inventory import D02_CLASSES, D05_SUBGROUPS, inventory_matrix, load_inventory
from analog_pipelines.ontology import load_ontology, scored_features
from analog_pipelines.qa import (
    QAIssue,
    qa_report,
    validate_area_overlaps,
    validate_observation,
    validate_processing_run,
    validate_source_asset,
)
from analog_pipelines.typed_contracts import (
    AreaV2,
    ProcessingRunV2,
    SourceAssetV2,
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
    migrate_v1_manifest,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def test_inventory_has_all_18_entries_and_formal_dimensions():
    inventory = load_inventory()
    matrix = inventory_matrix(inventory)
    assert len(matrix) == 18
    assert [d.code for d in inventory.dimensions] == ["D01", "D02", "D03", "D04", "D05", "D06"]
    assert set(inventory.dimensions[1].research_classes) == D02_CLASSES
    assert set(inventory.dimensions[4].subgroups) == D05_SUBGROUPS
    assert all({row["body"] for row in matrix} == {"Earth", "Moon", "Mars"} for _ in [0])


def test_inventory_keeps_unknown_fields_explicit():
    inventory = load_inventory()
    d02 = inventory.dimensions[1]
    earth = d02.bodies["Earth"].sources
    glim = next(source for source in earth if source.source_id == "glim-global-lithology")
    assert glim.availability == "identified"
    assert glim.data_status == "not_fetched"
    assert {"url", "license", "license_url"}.issubset(glim.unknown_fields)
    assert all(source.availability != "verified" for source in d02.bodies["Earth"].sources)


def test_ontology_maps_existing_feature_ids_without_changing_them():
    ontology = load_ontology()
    expected = {feature.id for feature in FEATURES} | {
        feature.feature_id for feature in CONTEXT_FEATURES
    }
    assert set(ontology.feature_mappings) == expected
    assert scored_features(ontology) == [
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
        "flat_terrain_fraction",
    ]
    assert ontology.feature_mappings["landcover_water_fraction"].dimension_code is None
    assert ontology.feature_mappings["landcover_water_fraction"].score_role == "context_only"
    assert ontology.legacy_dimension_mapping["terrain_shape"] == "D01"


def _numeric_observation(**overrides) -> TypedObservation:
    payload = {
        "observation_id": "obs-1",
        "area_id": "area-1",
        "body": "Earth",
        "dimension_code": "D01",
        "feature_id": "slope_mean_deg",
        "value_kind": "numeric",
        "state": "detected",
        "value": 0.0,
        "unit": "degree",
        "spatial_support": SpatialSupport(
            kind="window", crs="EPSG:32633", resolution_m=90.0, window_size_m=1980
        ),
        "temporal_support": TemporalSupport(kind="static"),
        "method_version": "terrain-metrics-1.0.0",
        "dataset_ids": ["dataset-1"],
        "source_asset_ids": ["asset-1"],
        "score_role": "similarity",
    }
    payload.update(overrides)
    return TypedObservation.model_validate(payload)


def test_physical_zero_is_valid_but_missing_states_carry_no_value():
    assert _numeric_observation(value=0.0).value == 0.0
    for state in ("not_detected", "no_coverage", "unknown", "not_applicable"):
        observation = _numeric_observation(state=state, value=None)
        assert observation.state == state
    with pytest.raises(ValidationError):
        _numeric_observation(state="no_coverage", value=0.0)
    with pytest.raises(ValidationError):
        _numeric_observation(state="detected", value=None)


def test_distribution_and_detection_semantics():
    distribution = {
        "bins": [10, 20, 30],
        "fractions": [0.2, 0.3, 0.5],
        "labels": ["a", "b", "c"],
    }
    observation = TypedObservation.model_validate(
        {
            **_numeric_observation().model_dump(),
            "feature_id": "landcover_tree_cover_fraction",
            "dimension_code": None,
            "value_kind": "distribution",
            "unit": "fraction",
            "value": distribution,
            "score_role": "context_only",
        }
    )
    assert observation.value["fractions"] == [0.2, 0.3, 0.5]
    with pytest.raises(ValidationError):
        TypedObservation.model_validate(
            {
                **_numeric_observation().model_dump(),
                "feature_id": "landcover_tree_cover_fraction",
                "dimension_code": None,
                "value_kind": "distribution",
                "unit": "fraction",
                "value": {"bins": [1, 2], "fractions": [0.8, 0.4]},
                "score_role": "context_only",
            }
        )
    detection = TypedObservation.model_validate(
        {
            **_numeric_observation().model_dump(),
            "feature_id": "mineral_spectral_detection",
            "dimension_code": "D02",
            "value_kind": "detection",
            "unit": "detection",
            "value": {"indicator": 0, "confidence": 0.2},
            "score_role": "evidence_only",
        }
    )
    assert detection.value["indicator"] == 0


def test_timeseries_spectrum_and_process_shapes_are_checked():
    base = _numeric_observation().model_dump()
    ts = TypedObservation.model_validate(
        {
            **base,
            "feature_id": "surface_temperature_mean_k",
            "dimension_code": "D03",
            "value_kind": "timeseries",
            "unit": "K",
            "value": {"timestamps": ["2020-01-01", "2020-01-02"], "values": [250.0, 260.0]},
            "temporal_support": TemporalSupport(
                kind="interval", start="2020-01-01", end="2020-01-02"
            ),
            "score_role": "evidence_only",
        }
    )
    assert len(ts.value["values"]) == 2
    with pytest.raises(ValidationError):
        TypedObservation.model_validate(
            {
                **base,
                "value_kind": "timeseries",
                "unit": "K",
                "value": {"timestamps": ["a"], "values": []},
            }
        )
    spectrum = TypedObservation.model_validate(
        {
            **base,
            "feature_id": "mineral_spectrum",
            "dimension_code": "D02",
            "value_kind": "spectrum",
            "unit": "reflectance",
            "value": {"wavelengths": [500, 600], "values": [0.1, 0.2], "wavelength_unit": "nm"},
            "score_role": "evidence_only",
        }
    )
    assert spectrum.value["wavelength_unit"] == "nm"
    with pytest.raises(ValidationError):
        TypedObservation.model_validate(
            {
                **base,
                "feature_id": "mineral_spectrum",
                "dimension_code": "D02",
                "value_kind": "spectrum",
                "unit": "reflectance",
                "value": {"wavelengths": [500], "values": []},
                "score_role": "evidence_only",
            }
        )


def test_area_frames_are_body_specific():
    with pytest.raises(ValidationError):
        AreaV2(
            area_id="bad-moon",
            body="Moon",
            crs="EPSG:4326",
            coordinate_frame="earth_wgs84",
        )
    with pytest.raises(ValidationError):
        AreaV2(
            area_id="bad-moon-epsg",
            body="Moon",
            crs="EPSG:32633",
            coordinate_frame="lunar_projected",
        )
    with pytest.raises(ValidationError):
        AreaV2(
            area_id="bad-earth",
            body="Earth",
            crs="EPSG:4326",
            coordinate_frame="lunar_projected",
        )


def test_v1_migration_preserves_support_and_separates_dataset_assets():
    raw = json.loads(
        (ROOT / "pipelines/earth/manifests/copernicus_glo90_earth.json").read_text(encoding="utf-8")
    )
    migrated = migrate_v1_manifest(raw)
    observation = migrated.observations[0]
    candidate = raw["candidates"][0]
    assert observation.spatial_support.window_size_m == candidate["window_size_m"]
    assert observation.spatial_support.resolution_m == candidate["analysis_resolution_m"]
    assert observation.spatial_support.bbox == candidate["bbox"]
    assert observation.spatial_support.crs == candidate["crs"]
    assert observation.temporal_support.kind == "unknown"
    assert observation.depth_support.kind == "unknown"
    assert observation.uncertainty is not None
    assert observation.uncertainty.kind == "unknown"
    assert observation.dataset_ids == [candidate["dataset_id"]]
    assert observation.source_asset_ids[0] != candidate["dataset_id"]
    assert any(asset.asset_id == observation.source_asset_ids[0] for asset in migrated.assets)
    assert migrated.assets[0].dataset_id == candidate["dataset_id"]


def test_v1_manifest_still_valid_and_migration_preserves_ids():
    raw = json.loads(
        (ROOT / "pipelines/earth/manifests/copernicus_glo90_earth.json").read_text(encoding="utf-8")
    )
    v1 = Manifest.model_validate(raw)
    migrated = migrate_v1_manifest(raw)
    assert len(migrated.areas) == len(v1.candidates)
    assert len(migrated.observations) == len(v1.candidates) * 5
    first = migrated.observations[0]
    assert first.feature_id in {feature.id for feature in FEATURES}
    assert first.method_version == "terrain-metrics-1.0.0"
    again = migrate_v1_manifest(raw)
    assert [obs.model_dump() for obs in migrated.observations] == [
        obs.model_dump() for obs in again.observations
    ]


def test_moon_migration_uses_planetary_frame():
    raw = json.loads(
        (ROOT / "pipelines/planetary/manifests/lola_moon_reference.json").read_text(
            encoding="utf-8"
        )
    )
    migrated = migrate_v1_manifest(raw)
    assert migrated.areas
    assert {area.coordinate_frame for area in migrated.areas} == {"lunar_projected"}
    assert {obs.body for obs in migrated.observations} == {"Moon"}


def test_qa_rejects_invalid_provenance_and_status_conflation():
    asset = SourceAssetV2(
        asset_id="asset-1",
        dataset_id="dataset-1",
        uri="local://fixture",
        checksum_algorithm="unknown",
        checksum_scope="source",
        native_crs="EPSG:4326",
    )
    assert not validate_source_asset(asset).ok

    invalid_observation = _numeric_observation(
        unit="mystery",
        spatial_support=SpatialSupport(kind="unknown"),
    )
    result = validate_observation(invalid_observation, known_asset_ids={"other"})
    assert not result.ok
    assert {issue.code for issue in result.errors} >= {"unit_not_allowed", "source_asset_unknown"}

    run = ProcessingRunV2(
        run_id="run-1",
        started_at="2020-01-01",
        input_asset_ids=["asset-1"],
        window_selection_rule="pilot",
        method_versions={"coarse": "coarse-terrain-250m-1.0.0"},
        counts={"attempted": 2, "succeeded": 2, "failed": 1},
        status="completed",
        output_checksum="sha256:abc",
    )
    assert not validate_processing_run(run).ok


def test_qa_area_overlap_and_report_shape():
    a = AreaV2(
        area_id="a",
        body="Earth",
        bbox={"west": 0, "south": 0, "east": 1, "north": 1},
        crs="EPSG:4326",
        coordinate_frame="earth_wgs84",
    )
    b = AreaV2(
        area_id="a",
        body="Earth",
        bbox={"west": 0.5, "south": 0.5, "east": 1.5, "north": 1.5},
        crs="EPSG:4326",
        coordinate_frame="earth_wgs84",
    )
    result = validate_area_overlaps([a, b])
    assert {issue.code for issue in result.issues} == {"duplicate_area_id", "overlapping_area"}
    report = qa_report([QAIssue("x", "bad"), QAIssue("y", "warn", severity="warning")])
    assert report["ok"] is False
    assert len(report["errors"]) == 1


def test_committed_v2_observation_schema_is_current():
    from analog_pipelines.typed_contracts import TypedObservation

    committed = json.loads(
        (ROOT / "contracts/v2/typed-observation.schema.json").read_text(encoding="utf-8")
    )
    generated = TypedObservation.model_json_schema()
    generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    assert committed == generated


def test_temporal_interval_requires_endpoints_and_chronological_order():
    with pytest.raises(ValidationError):
        TemporalSupport(kind="interval", start="2020-01-01")
    with pytest.raises(ValidationError):
        TemporalSupport(kind="interval", start="2020-01-03", end="2020-01-02")
    with pytest.raises(ValidationError):
        TemporalSupport(kind="interval", start="not-a-date", end="2020-01-02")
    assert TemporalSupport(kind="interval", start="2020-01-01", end="2020-01-02")
    assert TemporalSupport(kind="instant", start="2020-01-01")
