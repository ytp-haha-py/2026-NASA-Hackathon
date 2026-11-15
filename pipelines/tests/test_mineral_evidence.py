"""Offline checks for D02 mineral spectral evidence and source-status labels.

Synthetic arrays below are TEST ONLY. Committed counts come from the pinned
geology snapshot and typed-v2 export; no fixture is promoted to an observation.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from analog_pipelines.geology.mineral_evidence import (
    ABUNDANCE_UNITS,
    DEFAULT_ROOT,
    SOURCE_MATRIX,
    STATES,
    MineralEvidenceMatrix,
    SourceStatus,
    build_mineral_evidence,
    unavailable_state,
)
from analog_pipelines.geology.mineralogy import summarize_identification, unavailable_minerals
from analog_pipelines.geology.models import Mineral
from pydantic import ValidationError


def mineral(**kwargs) -> Mineral:
    values = {
        "evidence_id": "synthetic-test-only:kaolinite",
        "mineral": "kaolinite",
        "state": "detected",
        "value": 0.1,
        "unit": "dimensionless_band_depth",
        "measurement": "band_depth",
        "semantics_id": "synthetic-test-only:band-depth-v1",
        "method": "synthetic-test-only:aoi_mean",
        "valid_fraction": 0.8,
        "quality": "verified",
        "limitations": ["SYNTHETIC TEST ONLY"],
    }
    return Mineral(**(values | kwargs))


def typed_rows() -> list[dict]:
    path = DEFAULT_ROOT / "typed/observations.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_source_matrix_has_exact_public_product_identity_and_access():
    sources = {source.source_id: source for source in SOURCE_MATRIX.sources}

    emit = sources["earth-emit-mineral"]
    assert {product.collection_id for product in emit.products} == {
        "C4079846859-LPCLOUD",
        "C2408034484-LPCLOUD",
    }
    assert {product.doi for product in emit.products} == {
        "10.5067/EMIT/EMITL2BMIN.002",
        "10.5067/EMIT/EMITL2BMIN.001",
    }
    assert emit.access_status == "auth_required"
    assert emit.verification_status == "verified_metadata"
    assert all(product.metadata_query_url for product in emit.products)

    m3 = sources["moon-m3-reflectance"]
    product = m3.products[0]
    assert product.collection_id == "urn:nasa:pds:ch1_m3:data::1.0"
    assert product.doi == "10.17189/f8xf-6a29"
    assert product.product_id == "CH1-ORB-L-M3-4-L2-REFLECTANCE-V1.0"
    assert product.verified_example == "M3G20090203T175131_V01_RFL"
    assert "urn:nasa:pds:ch1_m3" in (product.metadata_query_url or "")
    assert m3.access_status == "open"
    assert m3.verification_status == "identified_metadata"

    crism = sources["mars-crism-mtrdr"]
    crism_product = crism.products[0]
    assert crism_product.product_id == "hrl000040ff_07_if183j_mtr3"
    assert crism_product.access_status == "open"
    assert "public domain" in crism_product.license
    assert "pds-geosciences.wustl.edu" in crism_product.url
    assert crism_product.metadata_query_url == "https://oderest.rsl.wustl.edu/live2/"

    for source in SOURCE_MATRIX.sources:
        assert source.access_status in {"open", "auth_required", "restricted", "unknown"}
        assert source.verification_status in {
            "verified_metadata",
            "identified_metadata",
            "blocked",
        }
        assert source.blockers


def test_no_source_enables_mineral_detection_or_abundance():
    for source in SOURCE_MATRIX.sources:
        assert source.mineral_detection_enabled is False
        assert source.abundance_enabled is False


def test_source_status_rejects_detection_without_verified_metadata_and_abundance_alone():
    payload = SOURCE_MATRIX.sources[0].model_dump()
    payload["mineral_detection_enabled"] = True
    payload["verification_status"] = "identified_metadata"
    with pytest.raises(ValidationError, match="verified source metadata"):
        SourceStatus.model_validate(payload)

    payload = SOURCE_MATRIX.sources[0].model_dump()
    payload["abundance_enabled"] = True
    with pytest.raises(ValidationError, match="without mineral detection"):
        SourceStatus.model_validate(payload)


def test_source_and_product_access_status_must_agree():
    payload = SOURCE_MATRIX.sources[0].model_dump()
    payload["access_status"] = "open"
    with pytest.raises(ValidationError, match="access status disagree"):
        SourceStatus.model_validate(payload)


def test_matrix_requires_all_five_states_and_three_bodies():
    payload = SOURCE_MATRIX.model_dump()
    payload["sources"] = payload["sources"][:2]
    with pytest.raises(ValidationError, match="Earth, Moon and Mars"):
        MineralEvidenceMatrix.model_validate(payload)

    payload = SOURCE_MATRIX.model_dump()
    payload["state_contract"] = payload["state_contract"][:4]
    with pytest.raises(ValidationError, match="all five observation states"):
        MineralEvidenceMatrix.model_validate(payload)


@pytest.mark.parametrize("state", STATES)
def test_each_state_has_distinct_numeric_rules(state):
    if state == "detected":
        observed = mineral(state=state)
        assert observed.value == 0.1 and observed.valid_fraction == 0.8
    elif state == "not_detected_under_valid_observation":
        observed = mineral(state=state, value=None, valid_fraction=0.8)
        assert observed.value is None
        with pytest.raises(ValidationError, match="categorical, never a numeric zero"):
            mineral(state=state, value=0)
    else:
        observed = mineral(state=state, value=None, uncertainty=None, valid_fraction=None)
        assert observed.value is None
        with pytest.raises(ValidationError, match="null"):
            mineral(state=state, value=0.1)


def test_unavailable_reasons_keep_three_missing_states_distinct():
    assert unavailable_state("outside_footprint") == "no_coverage"
    assert unavailable_state("observation_unavailable") == "unknown"
    assert unavailable_state("outside_product_scope") == "not_applicable"
    assert (
        len(
            {
                unavailable_state("outside_footprint"),
                unavailable_state("observation_unavailable"),
                unavailable_state("outside_product_scope"),
            }
        )
        == 3
    )

    default = unavailable_minerals(["clay_minerals"], "provider unavailable")
    assert default[0].state == "unknown" and default[0].value is None
    scoped = unavailable_minerals(
        ["clay_minerals"], "outside sensor scope", unavailable_state("outside_product_scope")
    )
    assert scoped[0].state == "not_applicable" and scoped[0].value is None


def test_native_aggregation_returns_band_depth_and_never_abundance():
    ids = np.array([[1, 1], [0, 2]])
    depth = np.array([[0.2, 0.8], [np.nan, 0.3]])
    uncertainty = np.array([[0.02, 0.03], [np.nan, np.nan]])
    inside = np.ones((2, 2), dtype=bool)
    quality = np.array([[True, False], [True, True]])

    detected = summarize_identification(1, ids, depth, uncertainty, inside, quality)
    assert detected["state"] == "detected"
    assert detected["value"] == pytest.approx(0.2)
    assert not any("abundance" in key for key in detected)

    missing = summarize_identification(2, ids, depth, uncertainty, inside, quality)
    assert missing["state"] == "not_detected_under_valid_observation"
    assert missing["value"] is None and missing["uncertainty"] is None
    assert not any("abundance" in key for key in missing)

    invalid = summarize_identification(1, ids, depth, uncertainty, inside, inside & False)
    assert invalid["state"] == "unknown" and invalid["value"] is None
    uncovered = summarize_identification(1, ids, depth, uncertainty, inside & False, quality)
    assert uncovered["state"] == "no_coverage" and uncovered["value"] is None


def test_crism_summary_context_is_evidence_only_not_mineral_detection():
    rows = typed_rows()
    contexts = [row for row in rows if row["feature_id"].startswith("context_")]
    minerals = [row for row in rows if row["feature_id"].startswith("mineral_")]

    assert len(contexts) == 14
    assert {row["body"] for row in contexts} == {"Mars"}
    assert {row["unit"] for row in contexts} == {"dimensionless_spectral_parameter"}
    assert {row["score_role"] for row in contexts} == {"evidence_only"}
    assert all(row["value"] is not None for row in contexts)
    parameters = {row["observation_id"].rsplit(":", 1)[-1] for row in contexts}
    assert parameters == {
        "OLINDEX3",
        "LCPINDEX2",
        "HCPINDEX2",
        "D2300",
        "BD1900_2",
        "SINDEX2",
        "CINDEX2",
    }

    assert len(minerals) == 10
    assert all(row["state"] != "detected" for row in minerals)
    assert all(row["value"] is None for row in minerals)
    assert not [row for row in rows if (row.get("unit") or "") in ABUNDANCE_UNITS]


def test_report_counts_are_actual_and_bounded():
    report = build_mineral_evidence(DEFAULT_ROOT)
    counts = report["counts"]

    assert counts["areas"] == {"Earth": 5, "Moon": 1, "Mars": 2}
    assert counts["analyses"]["total"] == 21
    assert counts["exported_observations"]["total"] == 56
    assert counts["mineral_observations"]["total"] == 10
    assert counts["mineral_observations"]["by_state"]["unknown"] == 10
    assert counts["mineral_observations"]["detected"] == 0
    assert counts["mineral_observations"]["numeric_values"] == 0
    assert counts["mineral_observations"]["abundance_values"] == 0

    context = counts["spectral_context_observations"]
    assert context["numeric_values"] == 14
    assert context["mineral_detection_claims"] == 0
    assert context["abundance_values"] == 0

    emit = counts["emit_metadata_coverage"]
    assert emit["granules_downloaded"] == 0
    assert emit["versions"]["002"]["metadata_candidate_count"] == 50
    assert emit["versions"]["001"]["metadata_candidate_count"] == 88
    assert emit["versions"]["002"]["status_counts"] == {
        "auth_required": 4,
        "no_coverage": 1,
    }

    assert counts["m3_evidence"]["granules_downloaded"] == 0
    assert counts["m3_evidence"]["validated_observations"] == 0
    assert counts["m3_evidence"]["status"] == "official_product_identified_not_fetched"

    crism = counts["crism_context"]
    assert crism["aoi_count"] == 2
    assert crism["parameter_count"] == 7
    assert crism["value_count"] == 14
    assert crism["selected_products"] == ["hrl000040ff_07_if183j_mtr3"]
    assert crism["mineral_detections"] == 0
    assert crism["abundance_values"] == 0
    assert set(crism["values_by_area"]) == {
        "mars-test-jezero-floor",
        "mars-test-jezero-western-delta",
    }
    assert all(len(items) == 7 for items in crism["values_by_area"].values())
    entries = [item for items in crism["values_by_area"].values() for item in items]
    assert {item["score_role"] for item in entries} == {"evidence_only"}
    assert {item["unit"] for item in entries} == {"dimensionless_spectral_parameter"}
    assert all(isinstance(item["value"], float) for item in entries)


def test_committed_report_is_reproducible_and_matches_the_builder():
    report = build_mineral_evidence(DEFAULT_ROOT)
    committed = json.loads((DEFAULT_ROOT / "mineral-evidence.json").read_text(encoding="utf-8"))
    assert committed == report
    assert json.dumps(report, sort_keys=True) == json.dumps(
        build_mineral_evidence(DEFAULT_ROOT), sort_keys=True
    )


def test_report_contains_no_credentials_or_abundance_claims():
    text = json.dumps(build_mineral_evidence(DEFAULT_ROOT), sort_keys=True).lower()
    for forbidden in ("password", "authorization", "bearer", "netrc", "api_key", "access_token"):
        assert forbidden not in text
    for source in SOURCE_MATRIX.sources:
        assert source.abundance_enabled is False
    assert "mass_fraction" not in text
