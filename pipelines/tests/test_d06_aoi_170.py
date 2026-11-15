"""Offline tests for the D06 Moon/Mars regolith AOI release (#170 Goal F)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from analog_pipelines.dimensions import FEATURE_V2_BY_ID, validate_dimension_observation
from analog_pipelines.typed_contracts import TypedObservation

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "pipelines" / "d05_d06_aoi" / "regolith"
EXPECTED_FEATURES = {
    "particle_size_distribution",
    "bulk_density_kg_m3",
    "porosity_fraction",
    "cohesion_kpa",
    "friction_angle_deg",
    "bearing_strength_kpa",
}
PLANETARY_CRS = {"IAU_2015:30100", "IAU_2015:49900"}


def load_json(name: str) -> dict:
    return json.loads((RELEASE / name).read_text(encoding="utf-8"))


def load_jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (RELEASE / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_builder():
    path = RELEASE / "build_release.py"
    spec = importlib.util.spec_from_file_location("d06_aoi_170_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundle_files_and_manifest_integrity():
    for name in [
        "aois.json",
        "sources.json",
        "source_values.json",
        "audit.json",
        "observations.jsonl",
        "aoi_results.json",
        "plan.json",
        "assets.json",
        "requests.jsonl",
        "qa.json",
        "manifest.json",
        "REPORT.md",
    ]:
        assert (RELEASE / name).is_file(), f"missing {name}"
    manifest = load_json("manifest.json")
    assert manifest["release_version"] == "d06-regolith-aoi-release-1.0.0"
    assert manifest["global_claim"] is False
    assert manifest["observation_count"] == 12
    assert manifest["area_count"] == 2
    for name, metadata in manifest["files"].items():
        path = RELEASE / name
        assert path.is_file(), f"manifest declares missing file {name}"
        assert path.stat().st_size == metadata["bytes"]


def test_aois_are_bounded_planetary_sites():
    aois = load_json("aois.json")["aois"]
    assert {aoi["body"] for aoi in aois} == {"Moon", "Mars"}
    assert len(aois) == 2
    for aoi in aois:
        assert aoi["crs"] in PLANETARY_CRS
        assert not aoi["crs"].upper().startswith("EPSG:")
        assert aoi["coordinate_frame"] in {"lunar_planetocentric", "mars_planetocentric"}
        bbox = aoi["bbox"]
        assert bbox["east"] > bbox["west"]
        assert bbox["north"] > bbox["south"]
        assert aoi["site_radius_m"] > 0
        assert aoi["source_refs"]


def test_each_aoi_returns_real_value_or_explicit_no_coverage():
    observations = load_jsonl("observations.jsonl")
    for aoi in load_json("aois.json")["aois"]:
        subset = [o for o in observations if o["area_id"] == aoi["area_id"]]
        assert subset, f"no observations for {aoi['area_id']}"
        assert any(o["state"] == "detected" for o in subset) or any(
            o["state"] == "no_coverage" for o in subset
        )
    moon = [o for o in observations if o["body"] == "Moon"]
    mars = [o for o in observations if o["body"] == "Mars"]
    assert any(o["state"] == "detected" for o in moon)
    assert any(o["state"] in {"detected", "no_coverage"} for o in mars)
    # At least one real Moon and one real Mars value.
    assert any(o["state"] == "detected" for o in moon)
    assert any(o["state"] == "detected" for o in mars)


def test_all_registered_features_are_explicit_per_aoi():
    observations = load_jsonl("observations.jsonl")
    assert len(observations) == 12
    for aoi in load_json("aois.json")["aois"]:
        subset = [o for o in observations if o["area_id"] == aoi["area_id"]]
        assert {o["feature_id"] for o in subset} == EXPECTED_FEATURES
        for feature_id in EXPECTED_FEATURES:
            rows = [o for o in subset if o["feature_id"] == feature_id]
            assert len(rows) == 1, f"{aoi['area_id']} {feature_id} duplicated"
            assert rows[0]["state"] in {"detected", "no_coverage"}
    assert all(o["feature_id"] in FEATURE_V2_BY_ID for o in observations)
    assert all(o["dimension_code"] == "D06" for o in observations)
    assert all(o["score_role"] == "evidence_only" for o in observations)


def test_detected_values_match_curated_source_values():
    source_values = {
        value["source_value_id"]: value for value in load_json("source_values.json")["values"]
    }
    observations = load_jsonl("observations.jsonl")
    for observation in observations:
        source_value_id = observation["legacy_provenance"]["source_value_id"]
        source = source_values[source_value_id]
        assert observation["state"] == source["state"]
        assert observation["value"] == source["value"]
        assert observation["unit"] == source["unit"]
        assert observation["depth_support"] == source["depth_support"]
        assert observation["method_version"] == source["method_version"]
        assert observation["dataset_ids"] == [source["dataset_id"]]


def test_detected_values_have_depth_and_are_not_proxies():
    observations = load_jsonl("observations.jsonl")
    required_flags = {
        "no_dem_roughness_inference",
        "no_worldcover_inference",
        "no_earth_transfer",
        "not_global_coverage",
    }
    for observation in observations:
        assert observation["spatial_support"]["crs"] in PLANETARY_CRS
        assert not observation["spatial_support"]["crs"].upper().startswith("EPSG:")
        assert observation["spatial_support"]["bbox"]
        assert required_flags <= set(observation["quality_flags"])
        if observation["state"] == "detected":
            assert observation["depth_support"]["kind"] in {"surface", "depth_interval"}
            assert observation["value"] is not None
            if observation["value_kind"] == "numeric":
                assert isinstance(observation["value"], (int, float))
                assert not isinstance(observation["value"], bool)
            assert observation["legacy_provenance"]["evidence_class"] in {
                "measured_sample",
                "measured_in_situ",
                "derived_from_in_situ_penetration",
                "derived_from_in_situ_trench_failure",
                "derived_from_in_situ_scoop_push",
                "model_derived_from_thermal_conductivity",
            }
            assert not any(
                flag.startswith(("proxy", "inferred_from_dem", "inferred_from_worldcover"))
                for flag in observation["quality_flags"]
            )
        else:
            assert observation["value"] is None
            assert observation["depth_support"]["kind"] == "unknown"
            assert observation["legacy_provenance"]["blocker"]
            assert "no_coverage_explicit" in observation["quality_flags"]


def test_observations_validate_as_typed_v2():
    observations = [
        TypedObservation.model_validate(row) for row in load_jsonl("observations.jsonl")
    ]
    assert len(observations) == 12
    for observation in observations:
        assert validate_dimension_observation(observation) == []


def test_aoi_results_match_observations():
    results = load_json("aoi_results.json")
    assert results["global_claim"] is False
    observations = {
        (row["area_id"], row["feature_id"]): row for row in load_jsonl("observations.jsonl")
    }
    for aoi in results["aois"]:
        assert set(aoi["features"]) == EXPECTED_FEATURES
        assert set(aoi["detected_features"]) | set(aoi["no_coverage_features"]) == EXPECTED_FEATURES
        for feature_id, entry in aoi["features"].items():
            observation = observations[(aoi["area_id"], feature_id)]
            assert entry["state"] == observation["state"]
            assert entry["value"] == observation["value"]
            assert entry["depth_support"] == observation["depth_support"]


def test_sources_have_version_licence_footprint_and_checksums():
    sources = load_json("sources.json")
    datasets = {d["dataset_id"]: d for d in sources["datasets"]}
    assets = {a["asset_id"]: a for a in sources["assets"]}
    for dataset_id, dataset in datasets.items():
        assert dataset["version"]
        assert dataset["license"]
        assert dataset["license_url"]
        assert dataset["footprint"]
        assert dataset["depth_support"]
        assert dataset["qa"]
        assert dataset["body"] in {"Moon", "Mars"}
        assert dataset_id
    for observation in load_jsonl("observations.jsonl"):
        dataset_id = observation["dataset_ids"][0]
        assert dataset_id in datasets
        assert datasets[dataset_id]["body"] == observation["body"]
        assert observation["source_asset_ids"]
        for asset_id in observation["source_asset_ids"]:
            assert asset_id in assets
        checksummed = [
            assets[asset_id]
            for asset_id in observation["source_asset_ids"]
            if assets[asset_id]["checksum_algorithm"] == "sha256" and assets[asset_id]["checksum"]
        ]
        assert checksummed, f"{observation['observation_id']} has no checksummed source asset"
    source_ids = " ".join(datasets)
    assert "soilgrids" not in source_ids.lower()


def test_audit_covers_chemistry_thermal_inertia_and_thickness():
    audit = load_json("audit.json")
    assert audit["global_claim"] is False
    unsupported = {(row["body"], row["feature_id"]) for row in audit["unsupported_features"]}
    assert ("Moon", "regolith_thickness_m") in unsupported
    assert ("Mars", "regolith_thickness_m") in unsupported
    assert ("Moon", "soil_chemistry") in unsupported
    assert ("Mars", "soil_chemistry") in unsupported
    chemistry = audit["chemistry_evidence"]
    assert chemistry["registered_feature"] is False
    assert chemistry["dataset_id"] == "nasa-pds-msl-apxs-supplement"
    assert chemistry["state"] == "unsupported_by_registry"
    assert len(chemistry["values"]) >= 8
    assert any(row["oxide"] == "FeO" for row in chemistry["values"])
    thermal = audit["thermal_inertia_audit"]
    assert thermal["dimension_code"] == "D03"
    assert thermal["used_for_d06_density"] is False
    assert thermal["doi"] == "10.1029/2006JE002735"
    assert audit["forbidden_derivations_not_performed"]
    assert audit["blockers"]


def test_builder_is_deterministic_and_matches_committed_artifacts():
    builder = load_builder()
    payloads = builder.build_payloads()
    assert payloads
    for name, text in payloads.items():
        path = RELEASE / name
        assert path.is_file(), f"builder produced {name} but it is not committed"
        assert path.read_text(encoding="utf-8") == text, f"{name} is stale"


def test_qa_checks_pass_and_counts_are_consistent():
    qa = load_json("qa.json")
    assert all(qa["checks"].values()), qa["checks"]
    assert qa["counts"]["observations"] == 12
    assert qa["counts"]["by_body"] == {"Mars": 6, "Moon": 6}
    assert qa["counts"]["by_state"] == {"detected": 6, "no_coverage": 6}
    assert qa["counts"]["by_feature"] == {feature: 2 for feature in EXPECTED_FEATURES}
    assert qa["counts"]["real_values"] == 6
    assert qa["counts"]["explicit_no_coverage"] == 6


def test_release_import_returns_aoi_values(tmp_path):
    from analog_api.adapters.release_import import import_release
    from analog_api.adapters.seed import make_engine
    from analog_api.adapters.typed_db import TypedObservationStore, init_typed_db
    from sqlalchemy.orm import Session

    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'aoi.db'}")
    init_typed_db(engine)
    with Session(engine) as session:
        result = import_release(session, RELEASE)
        session.commit()
        store = TypedObservationStore(session)
        moon = store.list_observations(dimension="D06", body="Moon")
        mars = store.list_observations(dimension="D06", body="Mars")
    assert result["observations"] == 12
    assert result["areas"] == 2
    assert any(row["state"] == "detected" for row in moon)
    assert any(row["state"] == "detected" for row in mars)
    moon_density = next(row for row in moon if row["feature_id"] == "bulk_density_kg_m3")
    mars_density = next(row for row in mars if row["feature_id"] == "bulk_density_kg_m3")
    assert moon_density["state"] == "detected"
    assert moon_density["value"] == 1970
    assert mars_density["state"] == "detected"
    assert mars_density["value"] == 1491
    assert all(row["spatial_support"]["crs"] in PLANETARY_CRS for row in moon + mars)


def test_no_global_or_exhaustive_claim():
    text = (RELEASE / "REPORT.md").read_text(encoding="utf-8").lower()
    doc = (ROOT / "docs" / "D06_AOI_170.md").read_text(encoding="utf-8").lower()
    assert "not a global" in text or "global claim" in text
    assert "not a global" in doc or "not global" in doc
    assert "no_coverage" in doc
    assert "thermal inertia" in doc
    assert "soil chemistry" in doc or "chemistry" in doc
