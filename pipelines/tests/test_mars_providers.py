"""Offline tests for Mars MOLA/THEMIS metadata providers and comparability gates."""

from __future__ import annotations

from pathlib import Path

import pytest
from analog_pipelines.acquisition.errors import AcquisitionError
from analog_pipelines.acquisition.mars_comparability import (
    MOLA_MEGDR_16PPD_RESOLUTION_M,
    MOLA_MEGDR_128PPD_RESOLUTION_M,
    MarsComparabilityError,
    assess_mola_megdr_terrain,
    assess_themis_thermal_inertia,
    require_evidence_only,
)
from analog_pipelines.acquisition.models import (
    AcquisitionTask,
    LocalAsset,
    PlanArea,
    SourceQuery,
)
from analog_pipelines.acquisition.providers import default_providers
from analog_pipelines.acquisition.providers_mars import (
    MOLA_BUNDLE_LID,
    MOLA_PRODUCT_LID,
    MOLA_PRODUCT_VERSION,
    THERMAL_DOI,
    THERMAL_NATIVE_RESOLUTION_M,
    THERMAL_PRODUCT_PAGE,
    THERMAL_TILE_BYTES,
    THERMAL_TILE_COUNT,
    MarsMolaMegdrProvider,
    MarsThemisThermalProvider,
)
from analog_pipelines.typed_contracts import SpatialSupport, TemporalSupport, TypedObservation


def _mars_area() -> PlanArea:
    return PlanArea(
        area_id="mars-test-jezero",
        display_name="Mars test AOI",
        body="Mars",
        bbox={"west": 77.0, "south": 18.0, "east": 78.0, "north": 19.0},
        crs="+proj=eqc +R=3396190 +units=m +type=crs",
        coordinate_frame="mars_projected",
        notes="TEST-ONLY area; not an observation",
    )


def _task(provider_id: str, dimension: str = "D01") -> AcquisitionTask:
    return AcquisitionTask(
        task_id=f"{provider_id}-test-task",
        provider_id=provider_id,
        source_id="test-source",
        body="Mars",
        dimension=dimension,  # type: ignore[arg-type]
        area=_mars_area(),
        endpoint="https://example.invalid/blocked",
        method_version="test-1.0.0",
        dataset_id="test-dataset",
    )


def _observation(*, dimension: str, score_role: str, state: str = "detected") -> TypedObservation:
    return TypedObservation(
        observation_id="mars-test-observation",
        area_id="mars-test-jezero",
        body="Mars",
        dimension_code=dimension,  # type: ignore[arg-type]
        feature_id="test_feature",
        value_kind="numeric",
        state=state,  # type: ignore[arg-type]
        value=-123.0 if state == "detected" else None,
        unit="meter",
        spatial_support=SpatialSupport(kind="unknown"),
        temporal_support=TemporalSupport(kind="unknown"),
        method_version="test-1.0.0",
        dataset_ids=["test-dataset"],
        source_asset_ids=["test-asset"],
        score_role=score_role,  # type: ignore[arg-type]
    )


def test_mola_discovery_uses_official_pds4_metadata():
    provider = MarsMolaMegdrProvider()
    sources = provider.discover(SourceQuery(body="Mars", dimension="D01"))

    assert len(sources) == 1
    source = sources[0]
    assert source.source_id == "pds4-mgs-mola-megdr-topography"
    assert source.comparability == "not_comparable"
    assert source.version.startswith("PDS4 product 1.1")
    assert source.endpoint.endswith("megt90n000eb.img")
    assert source.native_resolution.startswith("3.7044613374 km/pixel")
    assert source.metadata["pds4_bundle_lid"] == MOLA_BUNDLE_LID
    assert source.metadata["pds4_product_lid"] == MOLA_PRODUCT_LID
    assert source.metadata["pds4_product_version"] == MOLA_PRODUCT_VERSION
    assert source.metadata["pds3_dataset_id"] == "MGS-M-MOLA-5-MEGDR-L3-V1.0"
    assert source.metadata["image_bytes"] == 33_177_600
    assert source.metadata["license_status"] == "not_verified_in_pds4_bundle"
    assert source.metadata["observation_status"] == "unavailable"
    assert source.metadata["comparability_gate"]["allows_similarity_score"] is False
    assert source.metadata["megdr_128ppd_resolution_m"] == pytest.approx(
        MOLA_MEGDR_128PPD_RESOLUTION_M
    )


def test_mola_provider_is_mars_d01_only():
    provider = MarsMolaMegdrProvider()
    assert provider.discover(SourceQuery(body="Earth", dimension="D01")) == []
    assert provider.discover(SourceQuery(body="Mars", dimension="D03")) == []
    assert provider.discover(SourceQuery(body="Moon", dimension="D01")) == []


def test_mola_inspect_reports_verified_metadata_and_blocks_acquisition():
    provider = MarsMolaMegdrProvider()
    source = provider.discover(SourceQuery(body="Mars", dimension="D01"))[0]
    metadata = provider.inspect(source)

    assert metadata.status == "blocked"
    assert metadata.fields["metadata_verified"] is True
    assert metadata.fields["observations"] == "unavailable"
    assert metadata.fields["analysis_resolution_m"] == 90.0
    assert metadata.fields["comparability_gate"]["status"] == "not_comparable"
    blockers = set(metadata.blockers)
    assert "native_resolution_coarser_than_90m_analysis_support" in blockers
    assert "acquisition_not_attempted_to_avoid_an_unvalidated_global_mars_grid" in blockers
    assert "no_validated_mars_reference_manifest_exists" in blockers
    assert not any("value" in blocker for blocker in blockers)


def test_mola_plan_has_no_tasks_and_acquisition_fails_closed():
    provider = MarsMolaMegdrProvider()
    assert provider.plan(SourceQuery(body="Mars", dimension="D01"), [_mars_area()]) == []
    with pytest.raises(AcquisitionError, match="mola_megdr_acquisition_blocked"):
        provider.acquire(_task(provider.provider_id), Path("/tmp"))
    with pytest.raises(AcquisitionError, match="mola_megdr_processing_blocked"):
        provider.process(
            LocalAsset(task_id="x", path="/tmp/x", sha256="0" * 64, bytes=0),
            _task(provider.provider_id),
        )


def test_mola_validate_rejects_fabricated_detected_mars_value():
    provider = MarsMolaMegdrProvider()
    report = provider.validate([_observation(dimension="D01", score_role="similarity")])
    codes = {issue.code for issue in report.issues}
    assert report.ok is False
    assert {"mola_detected_value_not_permitted", "wrong_score_role"} <= codes


def test_mola_terrain_gate_blocks_every_megdr_resolution():
    for resolution in (MOLA_MEGDR_16PPD_RESOLUTION_M, MOLA_MEGDR_128PPD_RESOLUTION_M):
        decision = assess_mola_megdr_terrain(
            native_resolution_m=resolution,
            analysis_resolution_m=90.0,
            projected_metric_crs=True,
            vertical_units_verified=True,
            per_window_uncertainty_verified=True,
            local_dem=True,
        )
        assert decision.status == "not_comparable"
        assert "native_resolution_coarser_than_90m_analysis_support" in decision.blockers
        assert decision.allows_similarity_score is False
    with pytest.raises(MarsComparabilityError):
        require_evidence_only(
            assess_mola_megdr_terrain(native_resolution_m=MOLA_MEGDR_128PPD_RESOLUTION_M),
            "similarity",
        )


def test_thermal_discovery_uses_official_public_domain_metadata():
    provider = MarsThemisThermalProvider()
    sources = provider.discover(SourceQuery(body="Mars", dimension="D03"))

    assert len(sources) == 1
    source = sources[0]
    assert source.source_id == "usgs-mars-themis-global-thermal-inertia"
    assert source.comparability == "evidence_only"
    assert source.license.startswith("Public domain")
    assert source.license_url == THERMAL_PRODUCT_PAGE
    assert source.native_resolution == "100 m/pixel; 24 tiles; 60S to 60N latitude"
    assert source.metadata["native_resolution_m"] == THERMAL_NATIVE_RESOLUTION_M
    assert source.metadata["tile_count"] == THERMAL_TILE_COUNT
    assert source.metadata["tile_bytes"] == THERMAL_TILE_BYTES
    assert source.metadata["doi"] == THERMAL_DOI
    assert source.metadata["score_role"] == "evidence_only"
    assert source.metadata["observation_status"] == "unavailable"
    assert source.metadata["comparability_gate"]["status"] == "evidence_only"
    assert source.metadata["comparability_gate"]["allows_similarity_score"] is False


def test_thermal_inspect_blocks_acquisition_without_fabricating_values():
    provider = MarsThemisThermalProvider()
    source = provider.discover(SourceQuery(body="Mars", dimension="D03"))[0]
    metadata = provider.inspect(source)

    assert metadata.status == "blocked"
    assert metadata.fields["metadata_verified"] is True
    assert metadata.fields["observations"] == "unavailable"
    blockers = set(metadata.blockers)
    assert "tile_size_2532510198_bytes_exceeds_bounded_acquisition_budget" in blockers
    assert "no_approved_d03_thermal_inertia_similarity_feature" in blockers
    assert "no_verified_per_pixel_qa_or_uncertainty_layer" in blockers
    assert provider.plan(SourceQuery(body="Mars", dimension="D03"), [_mars_area()]) == []
    with pytest.raises(AcquisitionError, match="mars_themis_thermal_acquisition_blocked"):
        provider.acquire(_task(provider.provider_id, "D03"), Path("/tmp"))


def test_thermal_validate_rejects_similarity_and_detected_values():
    provider = MarsThemisThermalProvider()
    report = provider.validate([_observation(dimension="D03", score_role="similarity")])
    codes = {issue.code for issue in report.issues}
    assert report.ok is False
    assert {"wrong_score_role", "thermal_inertia_feature_not_registered"} <= codes


def test_thermal_gate_is_evidence_only_and_rejects_similarity_role():
    decision = assess_themis_thermal_inertia(native_resolution_m=100.0)
    assert decision.status == "evidence_only"
    assert decision.allows_similarity_score is False
    assert "no_approved_d03_thermal_inertia_similarity_feature" in decision.blockers
    assert (
        assess_themis_thermal_inertia(
            native_resolution_m=100.0, proposed_score_role="similarity"
        ).status
        == "not_comparable"
    )
    require_evidence_only(decision, "evidence_only")


def test_default_provider_registry_includes_mars_interfaces():
    registry = default_providers()
    assert "pds4-mola-megdr" in registry
    assert "usgs-themis-thermal-inertia" in registry
