"""Offline D02 area-fraction/evidence checks.

All synthetic arrays and geometries are TEST ONLY. The committed evidence is
derived from the committed source-derived geology record, never fabricated.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from analog_pipelines.geology.area_fraction import (
    EVIDENCE_SCHEMA,
    build_evidence,
    class_fractions,
    coverage_state,
    geometry_area_m2,
    nearest_neighbour_resample,
    validate_evidence,
    write_evidence,
)
from analog_pipelines.geology.legend import (
    PROCESS_CATEGORIES,
    LegendRegistryError,
    load_registry,
    resolve_unit,
    validate_registry,
)

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "pipelines/geology/area-fraction"
REGISTRY_PATH = EVIDENCE_DIR / "registry.json"
EVIDENCE_PATH = EVIDENCE_DIR / "evidence.json"
RECORD_PATH = ROOT / "pipelines/geology/records/geology-e920e7e3ae79e176.json"


def committed_evidence() -> dict:
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


def records_for(evidence: dict, body: str) -> list[dict]:
    return [record for record in evidence["records"] if record["body"] == body]


def test_registry_is_versioned_nonexclusive_and_dataset_scoped():
    registry = load_registry(REGISTRY_PATH)
    assert registry["nonexclusive"] is True
    assert registry["registry_version"] == "geology-legend-registry-1.0.0"
    assert set(registry["datasets"]) == {"usgs-moon-geology-v2", "usgs-jezero-sim3464"}
    bodies = {entry["body"] for entry in registry["datasets"].values()}
    assert bodies == {"Moon", "Mars"}
    for dataset_id, entry in registry["datasets"].items():
        for code, unit in entry["units"].items():
            labels = unit["process_labels"]
            assert isinstance(labels, list)
            mapped = resolve_unit(registry, dataset_id, code)
            assert mapped.status in {
                "mapped_with_process_evidence",
                "mapped_without_process_evidence",
            }
            assert set(mapped.categories) <= set(PROCESS_CATEGORIES)


def test_cross_body_codes_never_resolve_across_datasets():
    registry = load_registry(REGISTRY_PATH)
    moon_cr = resolve_unit(registry, "usgs-moon-geology-v2", "cr")
    mars_ec = resolve_unit(registry, "usgs-jezero-sim3464", "Ec")
    assert moon_cr.status == "unmapped_code"
    assert mars_ec.status == "unmapped_code"
    assert moon_cr.process_labels == () and mars_ec.process_labels == ()
    moon_ec = resolve_unit(registry, "usgs-moon-geology-v2", "Ec")
    mars_cr = resolve_unit(registry, "usgs-jezero-sim3464", "cr")
    assert moon_ec.body == "Moon" and mars_cr.body == "Mars"
    assert "crater" in moon_ec.process_labels[0].supporting_statement.lower()
    assert moon_ec.process_labels[0].source_document != mars_cr.process_labels[0].source_document
    with pytest.raises(LegendRegistryError, match="no legend registered"):
        resolve_unit(registry, "not-a-dataset", "Ec")


def test_unknown_code_is_explicit_and_never_guessed():
    registry = load_registry(REGISTRY_PATH)
    mapping = resolve_unit(registry, "usgs-jezero-sim3464", "Zz")
    assert mapping.status == "unmapped_code"
    assert mapping.process_labels == ()
    assert mapping.categories == ()
    assert set(mapping.unknown_categories) == set(PROCESS_CATEGORIES)
    assert "not guessed" in mapping.notes


def _aqueous_registry(statement: str) -> dict:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    data["datasets"]["usgs-moon-geology-v2"]["units"]["Ec"]["process_labels"].append(
        {
            "category": "aqueous_alteration",
            "process": "synthetic-test-only",
            "evidence_basis": "documented_interpretation",
            "confidence": "interpreted",
            "supporting_statement": statement,
            "source_document": "SYNTHETIC TEST ONLY",
        }
    )
    return data


def test_aqueous_alteration_requires_documented_evidence():
    with pytest.raises(LegendRegistryError, match="aqueous_alteration requires"):
        validate_registry(
            _aqueous_registry("Iron oxide spectral index only; no alteration stated.")
        )
    with pytest.raises(LegendRegistryError, match="aqueous_alteration requires"):
        validate_registry(_aqueous_registry("Hematite abundance is high."))
    documented = validate_registry(
        _aqueous_registry(
            "Documented hydrated sulfate alteration assemblage in the unit description."
        )
    )
    assert (
        documented["datasets"]["usgs-moon-geology-v2"]["units"]["Ec"]["process_labels"][-1][
            "category"
        ]
        == "aqueous_alteration"
    )


def test_committed_registry_does_not_infer_aqueous_alteration_from_oxides():
    registry = load_registry(REGISTRY_PATH)
    labels = [
        label
        for entry in registry["datasets"].values()
        for unit in entry["units"].values()
        for label in unit["process_labels"]
    ]
    assert not [label for label in labels if label["category"] == "aqueous_alteration"]
    for label in labels:
        statement = label["supporting_statement"].lower()
        assert "iron oxide" not in statement or "spectral index alone" in statement
    assert registry["policies"]["iron_oxide_alone_insufficient"] is True
    assert registry["policies"]["no_numeric_similarity"]


def test_nearest_neighbour_preserves_exact_class_codes():
    source = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    source_transform = (10.0, 0.0, 0.0, 0.0, -10.0, 20.0)
    target_transform = (5.0, 0.0, 0.0, 0.0, -5.0, 20.0)
    result = nearest_neighbour_resample(source, source_transform, target_transform, (4, 4))
    assert result.resampling == "nearest_neighbour"
    assert set(np.unique(result.classes)) == {1.0, 2.0, 3.0, 4.0}
    # Bilinear interpolation would create intermediate values such as 1.5 or 2.5.
    assert not any(value in {1.5, 2.5, 3.5} for value in np.unique(result.classes))
    counts = {value: int(np.count_nonzero(result.classes == value)) for value in (1, 2, 3, 4)}
    assert counts == {1: 4, 2: 4, 3: 4, 4: 4}


def test_nearest_neighbour_keeps_nodata_and_zero_class_distinct():
    source = np.array([[0, 7], [255, 7]], dtype=np.uint8)
    source_transform = (10.0, 0.0, 0.0, 0.0, -10.0, 20.0)
    target_transform = (10.0, 0.0, 0.0, 0.0, -10.0, 20.0)
    result = nearest_neighbour_resample(
        source, source_transform, target_transform, (2, 2), nodata=255
    )
    assert result.classes[0, 0] == 0 and result.valid[0, 0]
    assert result.classes[1, 0] == 255 and not result.valid[1, 0]
    fractions = class_fractions(result.classes, result.valid, nodata=255)
    assert fractions.state == "detected"
    assert fractions.fractions is not None
    assert fractions.fractions[0] == pytest.approx(1 / 3)
    assert fractions.fractions[7] == pytest.approx(2 / 3)
    assert 255 not in fractions.fractions


def test_nearest_neighbour_refuses_to_fabricate_out_of_bounds_classes():
    source = np.array([[1, 2], [3, 4]], dtype=np.uint8)
    source_transform = (10.0, 0.0, 0.0, 0.0, -10.0, 20.0)
    target_transform = (20.0, 0.0, 0.0, 0.0, -20.0, 40.0)
    with pytest.raises(ValueError, match="nodata"):
        nearest_neighbour_resample(source, source_transform, target_transform, (4, 4))
    result = nearest_neighbour_resample(
        source, source_transform, target_transform, (4, 4), nodata=0
    )
    assert not result.valid[3, 3]
    assert result.classes[3, 3] == 0


def test_all_nodata_is_no_coverage_with_null_fractions_not_zero():
    classes = np.full((4, 4), 255, dtype=np.uint8)
    fractions = class_fractions(classes, nodata=255)
    assert fractions.state == "no_coverage"
    assert fractions.fractions is None and fractions.pixel_counts is None
    assert fractions.valid_fraction == 0.0
    assert fractions.nodata_pixel_count == 16


def test_zero_class_is_a_real_detection_and_unknown_codes_survive():
    classes = np.array([[0, 0], [9, 9]], dtype=np.uint8)
    fractions = class_fractions(classes, allowed_codes=(0,))
    assert fractions.state == "detected"
    assert fractions.fractions == {0: 0.5, 9: 0.5}
    assert fractions.unknown_codes == (9,)
    zero_only = class_fractions(np.zeros((2, 2), dtype=np.uint8), allowed_codes=(0,))
    assert zero_only.fractions == {0: 1.0}


def test_coverage_state_separates_unknown_no_coverage_and_zero():
    assert coverage_state(None) == "unknown"
    assert coverage_state(0.0) == "no_coverage"
    assert coverage_state(0.5) == "partial"
    assert coverage_state(1.0) == "complete"
    assert coverage_state(1 - 1e-9) == "complete"


def test_equal_area_measurement_matches_known_planetary_windows():
    areas = json.loads((ROOT / "pipelines/geology/areas.json").read_text(encoding="utf-8"))["areas"]
    moon = next(area for area in areas if area["area_id"] == "moon-shackleton-rim-site04-roi2")
    assert geometry_area_m2(
        moon["geometry"],
        moon["crs"],
        "+proj=laea +lat_0=-89.77192510538467 +lon_0=-156.50226013773622 +R=1737400 +units=m "
        "+type=crs",
    ) == pytest.approx(3920368.5, abs=1.0)
    mars = next(area for area in areas if area["area_id"] == "mars-test-jezero-western-delta")
    assert geometry_area_m2(
        mars["geometry"],
        mars["crs"],
        "+proj=laea +lat_0=18.485 +lon_0=77.365 +R=3396190 +units=m +type=crs",
    ) == pytest.approx(2998995.5, abs=2.0)
    earth = next(area for area in areas if area["area_id"] == "earth-rio-tinto")
    assert geometry_area_m2(
        earth["geometry"],
        earth["crs"],
        "+proj=laea +lat_0=37.69 +lon_0=-6.55 +datum=WGS84 +units=m +type=crs",
    ) == pytest.approx(4123929.0, abs=2.0)
    with pytest.raises(ValueError, match="equal-area"):
        geometry_area_m2(moon["geometry"], moon["crs"], "EPSG:4326")


def test_committed_evidence_is_valid_reproducible_and_auditable():
    committed = committed_evidence()
    assert committed["schema"] == EVIDENCE_SCHEMA
    assert committed["nonexclusive"] is True
    assert committed["score_role"] == "evidence_only"
    assert committed["registry_sha256"] == hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    rebuilt = build_evidence(RECORD_PATH, REGISTRY_PATH)
    assert rebuilt == committed
    assert validate_evidence(rebuilt)["records"]
    registry = load_registry(REGISTRY_PATH)
    for record in committed["records"]:
        assert record["score_role"] == "evidence_only"
        if record["availability_status"] != "available":
            continue
        mapping = resolve_unit(registry, record["dataset_id"], record["unit"]["raw_code"])
        assert mapping.status == record["unit"]["mapping_status"]
        assert mapping.body == record["body"]
        assert list(mapping.unknown_categories) == record["unknown_categories"]
        assert record["source"]["license"] and record["source"]["product"]
        assert record["source"]["map_scale"]
        assert record["area"]["crs"] and record["source"]["source_crs"]


def test_committed_evidence_fraction_and_area_accounting():
    evidence = committed_evidence()
    summaries = {(item["area_id"], item["dataset_id"]): item for item in evidence["area_summaries"]}
    moon = summaries[("moon-shackleton-rim-site04-roi2", "usgs-moon-geology-v2")]
    assert moon["coverage_state"] == "partial"
    assert moon["coverage_fraction"] == pytest.approx(0.999998889958193)
    assert moon["fraction_sum"] == pytest.approx(moon["coverage_fraction"])
    assert moon["uncovered_fraction"] == pytest.approx(1 - 0.999998889958193)
    assert moon["category_fractions"][0]["category"] == "impact"
    assert moon["category_fractions"][0]["area_m2"] == pytest.approx(3920364.2, abs=1.0)
    assert moon["unknown_categories"] == ["igneous", "sedimentary", "aqueous_alteration"]

    delta = summaries[("mars-test-jezero-western-delta", "usgs-jezero-sim3464")]
    categories = {entry["category"]: entry for entry in delta["category_fractions"]}
    assert categories["sedimentary"]["fraction_of_aoi"] == pytest.approx(0.8491868093081738)
    assert set(categories["sedimentary"]["unit_codes"]) == {"Aeb", "NHjf2"}
    assert categories["impact"]["fraction_of_aoi"] == pytest.approx(0.08819231297289115)
    assert delta["coverage_state"] == "complete"
    assert delta["unknown_categories"] == ["igneous", "aqueous_alteration"]
    for record in records_for(evidence, "Mars"):
        if record["availability_status"] != "available":
            continue
        assert 0 < record["fraction_of_aoi"] <= 1
        assert record["area_m2"] == pytest.approx(
            record["fraction_of_aoi"] * record["area"]["area_m2"], rel=1e-9
        )
        assert record["area_m2"] <= record["area"]["area_m2"]


def test_missing_coverage_and_unverified_license_are_not_zero_filled():
    evidence = committed_evidence()
    glim = [record for record in records_for(evidence, "Earth") if "glim" in record["dataset_id"]]
    assert len(glim) == 5
    for record in glim:
        assert record["availability_status"] == "license_unverified"
        assert record["fraction_of_aoi"] is None and record["area_m2"] is None
        assert record["area"]["coverage_state"] in {"no_coverage", "unknown"}
        assert record["process_labels"] == []
        assert record["area"]["uncovered_fraction"] is None
    emit = [
        record
        for record in records_for(evidence, "Earth")
        if record["dataset_id"] == "emit-l2bmin-001"
    ]
    devon = next(record for record in emit if record["area_id"] == "earth-devon-island")
    assert devon["availability_status"] == "no_coverage"
    assert devon["fraction_of_aoi"] is None and not devon["process_labels"]
    assert devon["area"]["coverage_state"] == "no_coverage"


def test_validation_rejects_zero_filled_and_similarity_output():
    evidence = committed_evidence()
    zero_filled = json.loads(json.dumps(evidence))
    record = next(item for item in zero_filled["records"] if item["body"] == "Moon")
    record["fraction_of_aoi"] = 0.0
    with pytest.raises(ValueError, match="fraction must be"):
        validate_evidence(zero_filled)
    scored = json.loads(json.dumps(evidence))
    scored["geology_similarity"] = 0.8
    with pytest.raises(ValueError, match="must not contain"):
        validate_evidence(scored)
    leaked = json.loads(json.dumps(evidence))
    leaked["records"][0]["combined_score"] = 1
    with pytest.raises(ValueError, match="must not contain"):
        validate_evidence(leaked)


def test_earth_glim_licence_verdict_and_no_geometry_is_committed():
    document = json.loads((EVIDENCE_DIR / "earth-glim-license.json").read_text(encoding="utf-8"))
    assert document["verdict"] == "license_unverified"
    assert document["redistribution_allowed"] == "no"
    assert len(document["findings"]) >= 4
    assert all(finding["supports_redistribution"] is False for finding in document["findings"])
    alternative = document["legally_usable_alternative"]
    assert alternative["status"] == "candidate_not_ingested"
    assert "Open Government Licence" in alternative["license"]
    assert alternative["license_url"].startswith("https://open.canada.ca/")
    assert "coordinates" not in json.dumps(document)


def test_write_evidence_is_deterministic_and_pins_digests(tmp_path):
    document = committed_evidence()
    manifest = write_evidence(document, tmp_path)
    first = (tmp_path / "evidence.jsonl").read_bytes()
    write_evidence(document, tmp_path)
    assert (tmp_path / "evidence.jsonl").read_bytes() == first
    assert manifest["records"] == len(document["records"])
    assert manifest["bodies"] == ["Earth", "Mars", "Moon"]
    assert "no_coverage is not zero" in manifest["scope_note"]
