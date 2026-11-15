from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from analog_pipelines.acquisition.models import AcquisitionPlan, file_sha256
from analog_pipelines.dimensions import validate_dimension_observation
from analog_pipelines.illumination_aoi import (
    ILLUMINATION_FEATURES,
    METHOD_VERSION,
    PILOT_AOIS,
    RADIATION_FEATURE,
    RADIATION_METHOD_VERSION,
    RADIATION_SOURCES,
    Sample,
    build_radiation_evidence,
    elevation_runs,
    horizon_crossing_brackets,
    illuminated_sample_fraction,
    illumination_request,
    load_aoi_site,
    probe_radiation_sources,
    radiation_observation,
    radiation_request,
    refine_crossing,
    sample_epochs,
    utc_datetime,
    utc_text,
)
from analog_pipelines.solar_geometry import (
    KERNEL_SPECS,
    SpiceSolarGeometryProvider,
    ensure_kernels,
    spice_available,
)
from analog_pipelines.typed_contracts import TypedObservation

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "pipelines" / "d05_d06_aoi" / "illumination"
CACHE = ROOT / "var" / "cache" / "spice-45"
WINDOW_START = "2026-03-01T00:00:00Z"
WINDOW_END = "2026-03-02T00:00:00Z"


def _samples(elevations: list[float], start: str = WINDOW_START) -> list[Sample]:
    origin = utc_datetime(start)
    return [
        Sample(
            epoch_utc=utc_text(origin + timedelta(hours=index)),
            incidence_deg=90.0 - elevation,
            elevation_deg=elevation,
            local_solar_time_hours=12.0,
        )
        for index, elevation in enumerate(elevations)
    ]


def _committed_observations() -> list[TypedObservation]:
    raw = [
        json.loads(line)
        for line in (PILOT / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [TypedObservation.model_validate(item) for item in raw]


class TestPureHelpers:
    def test_sample_epochs_are_bounded_inclusive_and_aligned(self):
        epochs = sample_epochs(WINDOW_START, WINDOW_END, 60)
        assert len(epochs) == 25
        assert utc_text(epochs[0]) == WINDOW_START
        assert utc_text(epochs[-1]) == WINDOW_END
        assert all(
            later - earlier == timedelta(hours=1)
            for earlier, later in zip(epochs, epochs[1:], strict=False)
        )
        with pytest.raises(ValueError, match="exact multiple"):
            sample_epochs(WINDOW_START, WINDOW_END, 70)
        with pytest.raises(ValueError, match="bounded maximum"):
            sample_epochs(WINDOW_START, "2026-03-04T00:00:00Z", 60)
        with pytest.raises(ValueError, match="after window start"):
            sample_epochs(WINDOW_END, WINDOW_START, 60)
        with pytest.raises(ValueError, match="positive"):
            sample_epochs(WINDOW_START, WINDOW_END, 0)

    def test_utc_text_requires_an_explicit_timezone(self):
        with pytest.raises(ValueError, match="explicit timezone"):
            utc_datetime("2026-03-01T00:00:00")
        with pytest.raises(ValueError, match="must not be empty"):
            utc_datetime("   ")

    def test_illuminated_fraction_and_runs(self):
        # three above; a sample exactly at zero counts as below the horizon
        samples = _samples([5.0, 0.0, -2.0, -1.0, 3.0, 4.0])
        assert illuminated_sample_fraction(samples) == pytest.approx(3 / 6)
        runs = elevation_runs(samples)
        assert [run["state"] for run in runs] == [
            "above_geometric_horizon",
            "below_geometric_horizon",
            "above_geometric_horizon",
        ]
        assert [run["sample_count"] for run in runs] == [1, 3, 2]
        assert runs[0]["first_sample_utc"] == WINDOW_START
        assert runs[-1]["last_sample_utc"] == samples[-1].epoch_utc

    def test_horizon_crossing_bracket_and_bisection(self):
        samples = _samples([4.0, 1.0, -1.0, -4.0])
        brackets = horizon_crossing_brackets(samples)
        assert len(brackets) == 1
        assert brackets[0]["direction"] == "sunset"
        assert brackets[0]["bracketed_only"] is True

        origin = utc_datetime(WINDOW_START)

        def linear_elevation(epoch_utc: str) -> float:
            # Simple, exact model: elevation falls 3 deg/hour from +4 at the origin.
            hours = (utc_datetime(epoch_utc) - origin).total_seconds() / 3600.0
            return 4.0 - 3.0 * hours

        refined = refine_crossing(
            linear_elevation,
            samples[1],
            samples[2],
            tolerance_seconds=1.0,
        )
        assert refined["bracketed_only"] is False
        assert refined["direction"] == "sunset"
        assert refined["crossing_utc"] == "2026-03-01T01:20:00Z"
        assert abs(refined["elevation_deg_at_crossing"]) < 1e-9

    def test_refine_crossing_rejects_same_side_or_inconsistent_model(self):
        samples = _samples([4.0, 1.0, -1.0, -4.0])
        with pytest.raises(ValueError, match="opposite horizon sides"):
            refine_crossing(lambda _: 1.0, samples[0], samples[1])
        with pytest.raises(ValueError, match="does not reproduce"):
            refine_crossing(lambda _: 1.0, samples[1], samples[2])

    def test_load_aoi_sites_from_committed_reference_manifests(self):
        for selection in PILOT_AOIS:
            site = load_aoi_site(
                ROOT / selection.manifest_path,
                selection.reference_id,
                source_manifest=selection.manifest_path,
            )
            assert site.reference_id == selection.reference_id
            assert site.body == selection.body
            assert site.frame in {"MOON_ME", "IAU_MARS"}
            assert -90.0 <= site.latitude_deg <= 90.0
            assert -360.0 <= site.longitude_deg <= 360.0
            assert site.source_manifest == selection.manifest_path
        with pytest.raises(ValueError, match="not found"):
            load_aoi_site(ROOT / PILOT_AOIS[0].manifest_path, "no-such-aoi")


class TestRadiationEvidence:
    def _evidence(self) -> tuple[dict, list]:
        sites = [
            load_aoi_site(
                ROOT / selection.manifest_path,
                selection.reference_id,
                source_manifest=selection.manifest_path,
            )
            for selection in PILOT_AOIS
        ]
        evidence = build_radiation_evidence(
            sites, window_start=WINDOW_START, window_end=WINDOW_END, probes=None
        )
        return evidence, sites

    def test_no_value_is_produced_and_statuses_are_explicit(self):
        evidence, sites = self._evidence()
        assert evidence["no_value_ingested"] is True
        assert evidence["values_produced"] == 0
        assert evidence["conversions_applied"] == []
        assert evidence["sources"]
        assert len(evidence["sources"]) == len(RADIATION_SOURCES)
        for source in evidence["sources"]:
            assert source["values_ingested"] == 0
            assert source["verification_status"] in {"identified", "accessible", "verified"}
        statuses = {
            area_id: item["coverage_status"] for area_id, item in evidence["aoi_evidence"].items()
        }
        assert statuses == {
            "moon-shackleton-rim-site04-roi1": "unsupported",
            "mars-jezero-ctx-window-01": "no_coverage",
        }
        for item in evidence["aoi_evidence"].values():
            assert item["values_produced"] == 0
            assert item["time_coverage_overlap"] is True
            assert item["typed_state"] == "no_coverage"
            assert item["blockers"]
        assert {site.reference_id for site in sites} == set(statuses)

    def test_radiation_observation_is_typed_missing_value(self):
        evidence, sites = self._evidence()
        for site in sites:
            payload = radiation_observation(site, evidence, radiation_sources_sha256=None)
            observation = TypedObservation.model_validate(payload)
            assert observation.feature_id == RADIATION_FEATURE
            assert observation.state == "no_coverage"
            assert observation.value is None
            assert observation.unit == "Gy"
            assert observation.score_role == "evidence_only"
            assert observation.value_kind == "numeric"
            assert observation.method_version == RADIATION_METHOD_VERSION
            assert "ionizing_radiation_no_value_ingested" in observation.quality_flags
            assert "no_gy_to_sv_conversion" in observation.quality_flags
            assert validate_dimension_observation(observation) == []
            assert "coverage_status" in observation.legacy_provenance

    def test_requests_state_the_forbidden_conversions(self):
        site = load_aoi_site(
            ROOT / PILOT_AOIS[0].manifest_path,
            PILOT_AOIS[0].reference_id,
            source_manifest=PILOT_AOIS[0].manifest_path,
        )
        request = radiation_request(site, WINDOW_START, WINDOW_END)
        forbidden = " ".join(request["forbidden"])
        assert "flux-to-dose" in forbidden
        assert "Gy/Sv" in forbidden
        assert "zero-filling" in forbidden
        assert request["bounded"]["values_ingested"] == 0
        assert request["time"]["start_utc"] == WINDOW_START
        assert request["time"]["end_utc"] == WINDOW_END

    def test_illumination_request_is_explicit_and_bounded(self):
        site = load_aoi_site(
            ROOT / PILOT_AOIS[1].manifest_path,
            PILOT_AOIS[1].reference_id,
            source_manifest=PILOT_AOIS[1].manifest_path,
        )
        request = illumination_request(
            site,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            cadence_minutes=60,
            sample_count=25,
        )
        assert request["feature_ids"] == list(ILLUMINATION_FEATURES)
        assert request["time"]["sample_count"] == 25
        assert request["time"]["inclusive_end"] is True
        assert request["bounded"]["no_global_coverage_claim"] is True
        assert request["site"]["reference_manifest"] == PILOT_AOIS[1].manifest_path


class TestCommittedArtifacts:
    def test_observations_are_typed_and_subgroups_stay_separate(self):
        observations = _committed_observations()
        assert len(observations) == 102
        assert {obs.feature_id for obs in observations} == {
            "solar_incidence_deg",
            "solar_elevation_deg",
            RADIATION_FEATURE,
        }
        assert {obs.method_version for obs in observations} == {
            METHOD_VERSION,
            RADIATION_METHOD_VERSION,
        }
        illumination = [obs for obs in observations if obs.feature_id in ILLUMINATION_FEATURES]
        radiation = [obs for obs in observations if obs.feature_id == RADIATION_FEATURE]
        assert len(illumination) == 100
        assert len(radiation) == 2
        assert {obs.state for obs in illumination} == {"detected"}
        assert {obs.unit for obs in illumination} == {"degree"}
        assert {obs.score_role for obs in illumination} == {"context_only"}
        assert all(obs.value is not None for obs in illumination)
        assert all(not obs.feature_id.startswith("ionizing") for obs in illumination)
        for observation in radiation:
            assert observation.state == "no_coverage"
            assert observation.value is None
            assert observation.unit == "Gy"
            assert observation.score_role == "evidence_only"
            assert observation.dataset_ids
            assert observation.source_asset_ids
            assert "no_flux_to_dose_conversion" in observation.quality_flags
        for observation in observations:
            assert validate_dimension_observation(observation) == []

    def test_illumination_observations_are_time_bounded_and_complementary(self):
        observations = _committed_observations()
        by_area: dict[str, dict[str, dict[str, TypedObservation]]] = {}
        for observation in observations:
            if observation.feature_id not in ILLUMINATION_FEATURES:
                continue
            assert observation.temporal_support.kind == "instant"
            assert observation.temporal_support.start is not None
            moment = utc_datetime(observation.temporal_support.start)
            assert utc_datetime(WINDOW_START) <= moment <= utc_datetime(WINDOW_END)
            assert observation.spatial_support.kind == "point"
            expected_frame = "MOON_ME" if observation.body == "Moon" else "IAU_MARS"
            assert observation.spatial_support.crs == expected_frame
            assert "geometric_horizon_only" in observation.quality_flags
            assert "no_terrain_horizon" in observation.quality_flags
            assert "flat_ellipsoid_tangent_plane" in observation.quality_flags
            epoch = observation.temporal_support.start
            by_area.setdefault(observation.area_id, {}).setdefault(epoch, {})[
                observation.feature_id
            ] = observation

        for values in by_area.values():
            assert len(values) == 25
            for per_feature in values.values():
                assert set(per_feature) == set(ILLUMINATION_FEATURES)
                incidence = float(per_feature["solar_incidence_deg"].value)
                elevation = float(per_feature["solar_elevation_deg"].value)
                assert incidence + elevation == pytest.approx(90.0, abs=1e-9)
                assert 0.0 <= incidence <= 180.0
                assert -90.0 <= elevation <= 90.0
        assert set(by_area) == {
            "moon-shackleton-rim-site04-roi1",
            "mars-jezero-ctx-window-01",
        }

    def test_committed_manifest_hashes_counts_and_window(self):
        manifest = json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["data_kind"] == "model_derived"
        assert manifest["method_version"] == METHOD_VERSION
        assert manifest["radiation_method_version"] == RADIATION_METHOD_VERSION
        assert manifest["window"]["start_utc"] == WINDOW_START
        assert manifest["window"]["end_utc"] == WINDOW_END
        assert manifest["window"]["cadence_minutes"] == 60
        counts = manifest["counts"]
        assert counts["observations"] == 102
        assert counts["illumination_observations"] == 100
        assert counts["radiation_evidence_observations"] == 2
        assert counts["aois"] == 2
        assert counts["sample_instants_per_aoi"] == 25
        assert counts["by_state"] == {"detected": 100, "no_coverage": 2}
        assert counts["by_score_role"] == {"context_only": 100, "evidence_only": 2}
        assert counts["radiation_values_produced"] == 0
        hashes = manifest["hashes"]
        assert hashes["observations_sha256"] == file_sha256(PILOT / "observations.jsonl")
        assert hashes["requests_sha256"] == file_sha256(PILOT / "requests.jsonl")
        assert hashes["horizon_sha256"] == file_sha256(PILOT / "horizon.json")
        assert hashes["radiation_sources_sha256"] == file_sha256(PILOT / "radiation_sources.json")
        assert hashes["plan_sha256"] == file_sha256(PILOT / "plan.json")
        assert hashes["assets_sha256"] == file_sha256(PILOT / "assets.json")
        assert (
            hashlib.sha256((PILOT / "observations.jsonl").read_bytes()).hexdigest()
            == (hashes["observations_sha256"])
        )
        assert manifest["kernel_count"] == len(KERNEL_SPECS)
        assert {kernel["filename"] for kernel in manifest["kernels"]} == {
            spec.filename for spec in KERNEL_SPECS
        }
        assert manifest["limitations"] and manifest["non_claims"]
        assert any("not observed irradiance" in item for item in manifest["limitations"])
        assert any("No absorbed dose" in item for item in manifest["non_claims"])
        statuses = {aoi["area_id"]: aoi["radiation_coverage_status"] for aoi in manifest["aois"]}
        assert statuses == {
            "moon-shackleton-rim-site04-roi1": "unsupported",
            "mars-jezero-ctx-window-01": "no_coverage",
        }
        assert all(aoi["radiation_values_produced"] == 0 for aoi in manifest["aois"])
        coverage = manifest["coverage_contract"]
        assert coverage["contract_version"] == "location-coverage-status-1.0.0"
        assert coverage["global_coverage_claimed"] is False
        assert coverage["aoi_status"]["moon-shackleton-rim-site04-roi1"] == {
            "solar_incidence_deg": "modeled",
            "solar_elevation_deg": "modeled",
            "ionizing_dose_gy": "unsupported",
        }
        assert coverage["aoi_status"]["mars-jezero-ctx-window-01"]["ionizing_dose_gy"] == (
            "no_coverage"
        )
        assert coverage["score_roles"] == {
            "solar_incidence_deg": "context_only",
            "solar_elevation_deg": "context_only",
            "ionizing_dose_gy": "evidence_only",
        }

    def test_plan_assets_and_requests_are_consistent(self):
        plan = AcquisitionPlan.model_validate_json((PILOT / "plan.json").read_text("utf-8"))
        assets = json.loads((PILOT / "assets.json").read_text(encoding="utf-8"))
        requests = [
            json.loads(line)
            for line in (PILOT / "requests.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(plan.tasks) == 2
        assert {task.dimension for task in plan.tasks} == {"D05"}
        assert {task.area.area_id for task in plan.tasks} == {
            "moon-shackleton-rim-site04-roi1",
            "mars-jezero-ctx-window-01",
        }
        assert {task.parameters["subgroup"] for task in plan.tasks} == {"solar_illumination"}
        asset_ids = {asset["asset_id"] for asset in assets}
        assert {f"naif-kernel:{spec.filename}" for spec in KERNEL_SPECS} <= asset_ids
        assert {f"pds-dataset:{spec.dataset_id}" for spec in RADIATION_SOURCES} <= asset_ids
        assert len(requests) == 4
        assert {request["subgroup"] for request in requests} == {
            "solar_illumination",
            "ionizing_radiation",
        }
        for request in requests:
            assert request["dimension_code"] == "D05"
            assert request["time"]["start_utc"] == WINDOW_START
            assert request["time"]["end_utc"] == WINDOW_END
            assert request["bounded"]["no_global_coverage_claim"] is True

    def test_horizon_support_matches_observations_and_has_no_terrain_claim(self):
        horizon = json.loads((PILOT / "horizon.json").read_text(encoding="utf-8"))
        assert horizon["terrain_horizon_available"] is False
        assert "no terrain horizon" in horizon["terrain_horizon_reason"]
        observations = _committed_observations()
        elevations: dict[str, dict[str, float]] = {}
        for observation in observations:
            if observation.feature_id != "solar_elevation_deg":
                continue
            epoch = observation.temporal_support.start
            elevations.setdefault(observation.area_id, {})[epoch] = float(observation.value)
        assert set(elevations) == {aoi["area_id"] for aoi in horizon["aois"]}
        for aoi in horizon["aois"]:
            series = elevations[aoi["area_id"]]
            samples = [
                Sample(
                    epoch_utc=epoch,
                    incidence_deg=90.0 - elevation,
                    elevation_deg=elevation,
                    local_solar_time_hours=0.0,
                )
                for epoch, elevation in sorted(series.items())
            ]
            assert aoi["sample_count"] == len(samples) == 25
            assert aoi["illuminated_sample_fraction"] == pytest.approx(
                illuminated_sample_fraction(samples)
            )
            assert aoi["runs"] == elevation_runs(samples)
            crossings = aoi["horizon_crossings"]
            assert len(crossings) == len(horizon_crossing_brackets(samples))
            for crossing in crossings:
                assert crossing["bracketed_only"] is False
                assert (
                    utc_datetime(crossing["from_utc"])
                    <= utc_datetime(crossing["crossing_utc"])
                    <= utc_datetime(crossing["to_utc"])
                )
        by_area = {aoi["area_id"]: aoi for aoi in horizon["aois"]}
        assert by_area["moon-shackleton-rim-site04-roi1"]["illuminated_sample_fraction"] == 0.0
        assert by_area["mars-jezero-ctx-window-01"]["horizon_crossings"]
        assert all(aoi["terrain_horizon"]["available"] is False for aoi in horizon["aois"])

    def test_radiation_sources_are_verified_and_never_carry_values(self):
        evidence = json.loads((PILOT / "radiation_sources.json").read_text(encoding="utf-8"))
        assert evidence["no_value_ingested"] is True
        assert evidence["values_produced"] == 0
        assert evidence["conversions_applied"] == []
        assert len(evidence["sources"]) == len(RADIATION_SOURCES)
        for source in evidence["sources"]:
            assert source["verification_status"] in {"verified", "accessible"}
            assert source["values_ingested"] == 0
            assert source["blockers"]
            assert source["license_url"].startswith("https://")
            probe = source["catalog_probe"]
            assert probe is not None
            assert probe["status"] in {200, 206}
            assert probe["sha256"] and len(probe["sha256"]) == 64
            assert all(probe["markers_present"].values())
        rad_rdr = next(
            source
            for source in evidence["sources"]
            if source["dataset_id"] == "MSL-M-RAD-3-RDR-V1.0"
        )
        assert rad_rdr["label_probe"] is not None
        assert rad_rdr["label_probe"]["status"] in {200, 206}
        assert all(rad_rdr["label_probe"]["markers_present"].values())
        assert rad_rdr["native_units"] == ["microGray / Hour"]
        assert evidence["aoi_evidence"]["mars-jezero-ctx-window-01"]["coverage_status"] == (
            "no_coverage"
        )
        assert (
            evidence["aoi_evidence"]["moon-shackleton-rim-site04-roi1"]["coverage_status"]
            == "unsupported"
        )
        assert evidence["forbidden_conversions"]


class TestOptionalLiveRecomputation:
    @pytest.mark.skipif(not spice_available(), reason="spiceypy is not installed")
    def test_recomputed_spice_matches_the_committed_series(self):
        if not CACHE.is_dir() or not all(
            (CACHE / spec.filename).is_file() for spec in KERNEL_SPECS
        ):
            pytest.skip("pinned SPICE kernels are not present in the local gitignored cache")
        observations = _committed_observations()
        kernels = ensure_kernels(CACHE)
        with SpiceSolarGeometryProvider(kernels) as provider:
            for selection in PILOT_AOIS:
                site = load_aoi_site(
                    ROOT / selection.manifest_path,
                    selection.reference_id,
                    source_manifest=selection.manifest_path,
                )
                expected = {
                    observation.temporal_support.start: float(observation.value)
                    for observation in observations
                    if observation.area_id == site.reference_id
                    and observation.feature_id == "solar_elevation_deg"
                }
                assert len(expected) == 25
                for epoch, elevation in sorted(expected.items()):
                    record = provider.compute_site(
                        body=site.body,
                        latitude_deg=site.latitude_deg,
                        longitude_deg=site.longitude_deg,
                        epoch_utc=epoch,
                    )
                    assert record.geometry.elevation_deg == pytest.approx(elevation, abs=1e-9)
                    assert record.frame == site.frame

    @pytest.mark.network
    @pytest.mark.skipif(
        os.environ.get("RUN_D05_RADIATION_PROBE") != "1",
        reason="set RUN_D05_RADIATION_PROBE=1 to re-run the bounded PDS metadata audit",
    )
    def test_bounded_pds_metadata_probes_are_reachable(self):
        probes = probe_radiation_sources()
        assert set(probes) == {spec.source_id for spec in RADIATION_SOURCES}
        for probe in probes.values():
            assert probe["catalog"]["status"] in {200, 206}
            assert all(probe["catalog"]["markers_present"].values())
