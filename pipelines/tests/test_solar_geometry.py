from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import pytest
from analog_pipelines.acquisition.models import AcquisitionPlan, file_sha256
from analog_pipelines.dimensions import validate_dimension_observation
from analog_pipelines.solar_geometry import (
    BODY_FRAME,
    BODY_KERNEL_FILENAMES,
    KERNEL_SPECS,
    SPICE_METHOD_VERSION,
    KernelIntegrityError,
    SolarGeometryError,
    SpiceSolarGeometryProvider,
    body_fixed_site_vector,
    body_kernel_asset_ids,
    compute_solar_geometry,
    ellipsoid_surface_normal,
    ensure_kernels,
    local_east_vector,
    local_north_vector,
    local_solar_time_hours,
    spice_available,
    spice_epoch_text,
    verify_kernel,
)
from analog_pipelines.typed_contracts import TypedObservation

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "pipelines" / "pilots" / "d05_spice_illumination"
CACHE = ROOT / "var" / "cache" / "spice-45"
EPOCH = "2026-11-15T12:00:00Z"


def _close(left: float, right: float, tol: float = 1e-9) -> bool:
    return math.isclose(left, right, abs_tol=tol)


def test_body_fixed_site_vector_and_ellipsoid_normal():
    site = body_fixed_site_vector(0.0, 0.0, 1737.4, 1737.4)
    assert site[0] == pytest.approx(1737.4, abs=1e-6)
    assert site[1] == pytest.approx(0.0, abs=1e-9)
    assert site[2] == pytest.approx(0.0, abs=1e-9)
    assert ellipsoid_surface_normal(site, 1737.4, 1737.4) == pytest.approx((1.0, 0.0, 0.0))

    north_pole = body_fixed_site_vector(90.0, 0.0, 1737.4, 1737.4)
    assert north_pole[2] == pytest.approx(1737.4, abs=1e-6)
    assert ellipsoid_surface_normal(north_pole, 1737.4, 1737.4) == pytest.approx(
        (0.0, 0.0, 1.0), abs=1e-9
    )


def test_ellipsoid_normal_differs_from_radial_at_midlatitude():
    equatorial, polar = 3396.19, 3376.2
    site = body_fixed_site_vector(45.0, 0.0, equatorial, polar)
    radial = tuple(component / math.sqrt(sum(x * x for x in site)) for component in site)
    normal = ellipsoid_surface_normal(site, equatorial, polar)
    assert sum(a * b for a, b in zip(radial, normal, strict=True)) < 1.0 - 1e-6


def test_local_east_and_north_are_orthonormal():
    for latitude, longitude in ((0.0, 0.0), (18.22, 77.37), (-89.76, -157.0)):
        radii = (3396.19, 3376.2) if latitude > -80 else (1737.4, 1737.4)
        site = body_fixed_site_vector(latitude, longitude, *radii)
        up = ellipsoid_surface_normal(site, *radii)
        east = local_east_vector(longitude, up)
        north = local_north_vector(up, east)
        for vector in (up, east, north):
            assert sum(component * component for component in vector) == pytest.approx(1.0)
        assert sum(a * b for a, b in zip(up, east, strict=True)) == pytest.approx(0.0)
        assert sum(a * b for a, b in zip(up, north, strict=True)) == pytest.approx(0.0)
        assert sum(a * b for a, b in zip(east, north, strict=True)) == pytest.approx(0.0)


def test_overhead_horizon_and_below_horizon_sun():
    # Unit sphere site at (1, 0, 0); local normal is +X.
    overhead = compute_solar_geometry(
        site_xyz_m=(1.0, 0.0, 0.0),
        sun_xyz_m=(10.0, 0.0, 0.0),
        surface_normal=(1.0, 0.0, 0.0),
        site_longitude_deg=0.0,
    )
    assert overhead.incidence_deg == pytest.approx(0.0, abs=1e-9)
    assert overhead.elevation_deg == pytest.approx(90.0, abs=1e-9)
    assert overhead.local_solar_time_hours == pytest.approx(12.0, abs=1e-9)

    horizon = compute_solar_geometry(
        site_xyz_m=(1.0, 0.0, 0.0),
        sun_xyz_m=(1.0, 10.0, 0.0),
        surface_normal=(1.0, 0.0, 0.0),
        site_longitude_deg=0.0,
    )
    assert horizon.incidence_deg == pytest.approx(90.0, abs=1e-9)
    assert horizon.elevation_deg == pytest.approx(0.0, abs=1e-9)

    below = compute_solar_geometry(
        site_xyz_m=(1.0, 0.0, 0.0),
        sun_xyz_m=(0.5, 0.0, -math.sqrt(3.0) / 2.0),
        surface_normal=(1.0, 0.0, 0.0),
        site_longitude_deg=0.0,
    )
    assert below.incidence_deg == pytest.approx(120.0, abs=1e-9)
    assert below.elevation_deg == pytest.approx(-30.0, abs=1e-9)


def test_incidence_and_elevation_are_complementary_and_clamped():
    geometry = compute_solar_geometry(
        site_xyz_m=(1.0, 0.0, 0.0),
        sun_xyz_m=(-10.0, 0.0, 0.0),
        surface_normal=(1.0, 0.0, 0.0),
        site_longitude_deg=0.0,
    )
    assert geometry.incidence_deg == pytest.approx(180.0, abs=1e-9)
    assert geometry.elevation_deg == pytest.approx(-90.0, abs=1e-9)
    assert _close(geometry.incidence_deg + geometry.elevation_deg, 90.0)


def test_local_solar_time_wraps_across_the_antimeridian():
    assert local_solar_time_hours(0.0, 0.0) == pytest.approx(12.0)
    assert local_solar_time_hours(0.0, -90.0) == pytest.approx(18.0)
    assert local_solar_time_hours(170.0, -170.0) == pytest.approx(10.0 + 2.0 / 3.0)
    assert local_solar_time_hours(-170.0, 170.0) == pytest.approx(13.0 + 1.0 / 3.0)


def test_epoch_text_conversion_is_explicit():
    assert spice_epoch_text("2026-11-15T12:00:00Z") == "2026-11-15 12:00:00 UTC"
    with pytest.raises(SolarGeometryError):
        spice_epoch_text("   ")


def test_kernel_specs_are_pinned_public_https():
    assert {spec.filename for spec in KERNEL_SPECS} == set(BODY_KERNEL_FILENAMES["Moon"])
    for spec in KERNEL_SPECS:
        assert spec.url.startswith("https://naif.jpl.nasa.gov/")
        assert len(spec.sha256) == 64
        int(spec.sha256, 16)
        assert spec.bytes > 0
        assert spec.filename in BODY_KERNEL_FILENAMES["Mars"] or spec.filename in {
            "moon_pa_de421_1900-2050.bpc",
            "moon_080317.tf",
        }


def test_verify_kernel_rejects_wrong_bytes(tmp_path: Path):
    path = tmp_path / "wrong.tls"
    path.write_bytes(b"not a kernel")
    spec = KERNEL_SPECS[0]
    with pytest.raises(KernelIntegrityError, match="size|sha256"):
        verify_kernel(path, spec)


def test_committed_pilot_is_typed_v2_and_separates_radiation():
    raw = [
        json.loads(line)
        for line in (PILOT / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    observations = [TypedObservation.model_validate(item) for item in raw]
    assert len(observations) == 18
    assert {obs.body for obs in observations} == {"Moon", "Mars"}
    assert {obs.feature_id for obs in observations} == {
        "solar_incidence_deg",
        "solar_elevation_deg",
    }
    assert {obs.state for obs in observations} == {"detected"}
    assert {obs.score_role for obs in observations} == {"context_only"}
    assert {obs.method_version for obs in observations} == {SPICE_METHOD_VERSION}
    assert {obs.unit for obs in observations} == {"degree"}
    assert all(not obs.feature_id.startswith("ionizing") for obs in observations)
    assert all(obs.unit not in {"Gy", "Sv"} for obs in observations)
    for observation in observations:
        assert validate_dimension_observation(observation) == []
        assert observation.temporal_support.kind == "instant"
        assert observation.temporal_support.start == EPOCH
        assert observation.spatial_support.kind == "point"
        assert observation.spatial_support.crs == BODY_FRAME[observation.body]
        assert "flat_ellipsoid_tangent_plane" in observation.quality_flags
        assert "not_ionizing_radiation" in observation.quality_flags

    by_area: dict[str, dict[str, float]] = {}
    for observation in observations:
        by_area.setdefault(observation.area_id, {})[observation.feature_id] = float(
            observation.value
        )
    for values in by_area.values():
        assert set(values) == {"solar_incidence_deg", "solar_elevation_deg"}
        assert values["solar_incidence_deg"] + values["solar_elevation_deg"] == pytest.approx(
            90.0, abs=1e-9
        )
        assert 0.0 <= values["solar_incidence_deg"] <= 180.0


def test_committed_manifest_pins_kernels_epoch_and_limits():
    manifest = json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_kind"] == "model_derived"
    assert manifest["epoch_utc"] == EPOCH
    assert manifest["method_version"] == SPICE_METHOD_VERSION
    assert manifest["frames"]["Moon"]["frame"] == "MOON_ME"
    assert manifest["frames"]["Mars"]["frame"] == "IAU_MARS"
    assert manifest["counts"]["observations"] == 18
    assert manifest["counts"]["by_body"] == {"Mars": 10, "Moon": 8}
    assert manifest["counts"]["sites_attempted"] == 9
    assert manifest["counts"]["sites_succeeded"] == 9
    assert manifest["counts"]["sites_failed"] == 0
    assert manifest["counts"]["no_coverage"] == 0
    assert manifest["kernel_count"] == len(KERNEL_SPECS)
    specs = {spec.filename: spec for spec in KERNEL_SPECS}
    assert {kernel["filename"] for kernel in manifest["kernels"]} == set(specs)
    for kernel in manifest["kernels"]:
        spec = specs[kernel["filename"]]
        assert kernel["url"] == spec.url
        assert kernel["sha256"] == spec.sha256
        assert kernel["bytes"] == spec.bytes
        assert "var/cache/" in kernel["local_cache_path"]
    assert manifest["limitations"]
    assert any("not observed irradiance" in item for item in manifest["limitations"])
    assert any("no DEM terrain horizon" in item for item in manifest["limitations"])
    assert manifest["non_claims"]
    assert manifest["observations_sha256"] == file_sha256(PILOT / "observations.jsonl")


def test_committed_plan_and_assets_are_consistent():
    plan = AcquisitionPlan.model_validate_json((PILOT / "plan.json").read_text(encoding="utf-8"))
    assets = json.loads((PILOT / "assets.json").read_text(encoding="utf-8"))
    observations = [
        json.loads(line)
        for line in (PILOT / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    area_ids = {task.area.area_id for task in plan.tasks}
    assert len(plan.tasks) == 9
    assert area_ids == {observation["area_id"] for observation in observations}
    assert all(task.dimension == "D05" for task in plan.tasks)
    assert {task.area.coordinate_frame for task in plan.tasks} == {
        "lunar_projected",
        "mars_projected",
    }
    asset_ids = {asset["asset_id"] for asset in assets}
    assert asset_ids == {f"naif-kernel:{spec.filename}" for spec in KERNEL_SPECS}
    for observation in observations:
        assert set(observation["source_asset_ids"]) <= asset_ids
        assert set(observation["dataset_ids"]) == {"naif-spice-generic-kernels"}
    for task in plan.tasks:
        assert set(body_kernel_asset_ids(task.body)) <= asset_ids


def test_committed_observations_hash_matches_manifest_assets():
    manifest = json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["plan_sha256"] == file_sha256(PILOT / "plan.json")
    assert manifest["assets_sha256"] == file_sha256(PILOT / "assets.json")
    digest = hashlib.sha256((PILOT / "observations.jsonl").read_bytes()).hexdigest()
    assert digest == manifest["observations_sha256"]


@pytest.mark.skipif(not spice_available(), reason="spiceypy is not installed")
def test_cached_spice_kernel_smoke_when_available():
    if not CACHE.is_dir() or not all((CACHE / spec.filename).is_file() for spec in KERNEL_SPECS):
        pytest.skip("pinned SPICE kernels are not present in the local gitignored cache")
    kernels = ensure_kernels(CACHE)
    with SpiceSolarGeometryProvider(kernels) as provider:
        moon = provider.compute_site(
            body="Moon",
            latitude_deg=-89.76196621,
            longitude_deg=-157.06719662,
            epoch_utc=EPOCH,
        )
        mars = provider.compute_site(
            body="Mars",
            latitude_deg=18.220253246764436,
            longitude_deg=77.37095200216012,
            epoch_utc=EPOCH,
        )
    assert moon.frame == "MOON_ME"
    assert mars.frame == "IAU_MARS"
    for record in (moon, mars):
        assert 0.0 <= record.geometry.incidence_deg <= 180.0
        assert record.geometry.incidence_deg + record.geometry.elevation_deg == pytest.approx(
            90.0, abs=1e-9
        )
        assert record.geometry.sun_distance_m > 1.0e11
        assert record.frame_validation.startswith("pxform(")


@pytest.mark.network
@pytest.mark.skipif(not spice_available(), reason="spiceypy is not installed")
def test_network_spice_kernel_smoke_is_opt_in(tmp_path: Path):
    if os.environ.get("RUN_SPICE_NETWORK_SMOKE") != "1":
        pytest.skip("set RUN_SPICE_NETWORK_SMOKE=1 to download the pinned kernels")
    kernels = ensure_kernels(tmp_path, download=True)
    with SpiceSolarGeometryProvider(kernels) as provider:
        record = provider.compute_site(
            body="Moon",
            latitude_deg=-89.76196621,
            longitude_deg=-157.06719662,
            epoch_utc=EPOCH,
        )
    assert record.frame == "MOON_ME"
    assert record.spice_version
    assert 0.0 <= record.geometry.incidence_deg <= 180.0
