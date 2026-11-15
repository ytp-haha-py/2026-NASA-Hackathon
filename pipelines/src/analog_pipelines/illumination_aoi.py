"""D05 AOI/time-bounded solar illumination and ionizing-radiation evidence (Issue #170).

Two deliberately separated subgroups:

- ``solar_illumination``: model-derived Sun incidence/elevation at a bounded set of
  documented UTC instants for an explicit AOI point, plus the **geometric**
  flat-ellipsoid horizon (Sun above/below the local tangent plane). It is not
  observed irradiance, not a shadow fraction, and not a terrain horizon.
- ``ionizing_radiation``: an official-source audit only. No dose or flux value is
  ingested, converted or fabricated. Each AOI receives an explicit
  ``no_coverage``/``unsupported`` record with the reason.

The SPICE dependency stays optional and is imported lazily by
:class:`~analog_pipelines.solar_geometry.SpiceSolarGeometryProvider`; the pure
helpers in this module are importable and testable without ``spiceypy``. Kernels
are pinned public NAIF products, verified by SHA-256, and cached outside Git.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from analog_pipelines.solar_geometry import (
    BODY_FRAME,
    BODY_KERNEL_FILENAMES,
    SPICE_METHOD_VERSION,
    SpiceGeometryRecord,
    body_kernel_asset_ids,
)
from analog_pipelines.typed_contracts import (
    DepthSupport,
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
    Uncertainty,
)

METHOD_VERSION = "d05-aoi-illumination-1.0.0"
RADIATION_METHOD_VERSION = "d05-aoi-ionizing-radiation-audit-1.0.0"
RELEASE_VERSION = "d05-aoi-illumination-1.0.0"
SPICE_DATASET_ID = "naif-spice-generic-kernels"
DEFAULT_WINDOW_START = "2026-03-01T00:00:00Z"
DEFAULT_WINDOW_END = "2026-03-02T00:00:00Z"
DEFAULT_CADENCE_MINUTES = 60
MAX_SAMPLES = 49
ILLUMINATION_FEATURES = ("solar_incidence_deg", "solar_elevation_deg")
RADIATION_FEATURE = "ionizing_dose_gy"
USER_AGENT = (
    "PlanetaryAnalogExplorer-D05-AOI/1.0 "
    "(bounded PDS metadata audit; +https://github.com/2026-NASA-Hackathon)"
)
PROBE_MAX_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class AoiSelection:
    """One bounded AOI, resolved from an existing committed reference manifest."""

    body: str
    manifest_path: str
    reference_id: str


PILOT_AOIS: tuple[AoiSelection, ...] = (
    AoiSelection(
        body="Moon",
        manifest_path="pipelines/planetary/manifests/lola_moon_reference.json",
        reference_id="moon-shackleton-rim-site04-roi1",
    ),
    AoiSelection(
        body="Mars",
        manifest_path="pipelines/planetary/manifests/ctx_mars_references.json",
        reference_id="mars-jezero-ctx-window-01",
    ),
)


@dataclass(frozen=True, slots=True)
class AoiSite:
    """A point AOI with explicit body-fixed coordinates and source provenance."""

    reference_id: str
    display_name: str
    body: str
    latitude_deg: float
    longitude_deg: float
    dataset_id: str
    source_crs: str
    source_manifest: str

    @property
    def frame(self) -> str:
        return BODY_FRAME[self.body]


@dataclass(frozen=True, slots=True)
class Sample:
    """One computed illumination sample at one documented UTC instant."""

    epoch_utc: str
    incidence_deg: float
    elevation_deg: float
    local_solar_time_hours: float

    @property
    def above_geometric_horizon(self) -> bool:
        return self.elevation_deg > 0.0


def utc_datetime(text: str) -> datetime:
    """Parse an ISO-8601 UTC instant; require an explicit timezone."""
    value = text.strip()
    if not value:
        raise ValueError("epoch text must not be empty")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"epoch {text!r} must carry an explicit timezone")
    return moment.astimezone(UTC)


def utc_text(moment: datetime) -> str:
    """Format an aware datetime as a second-resolution UTC instant."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sample_epochs(
    start_utc: str,
    end_utc: str,
    cadence_minutes: int,
    *,
    max_samples: int = MAX_SAMPLES,
) -> list[datetime]:
    """Build a bounded, inclusive-end sample grid inside the requested window."""
    start = utc_datetime(start_utc)
    end = utc_datetime(end_utc)
    if end <= start:
        raise ValueError("window end must be after window start")
    if cadence_minutes <= 0:
        raise ValueError("cadence_minutes must be positive")
    step = timedelta(minutes=cadence_minutes)
    span = end - start
    if span % step != timedelta(0):
        raise ValueError("window length must be an exact multiple of the cadence")
    count = int(span / step) + 1
    if count > max_samples:
        raise ValueError(f"{count} samples exceed the bounded maximum of {max_samples}")
    return [start + index * step for index in range(count)]


def illuminated_sample_fraction(samples: Sequence[Sample]) -> float:
    """Fraction of sampled instants strictly above the geometric horizon."""
    if not samples:
        raise ValueError("at least one sample is required")
    above = sum(1 for sample in samples if sample.above_geometric_horizon)
    return above / len(samples)


def elevation_runs(samples: Sequence[Sample]) -> list[dict[str, Any]]:
    """Contiguous runs of above/below-horizon samples, bounded by sampled instants."""
    if not samples:
        return []
    runs: list[dict[str, Any]] = []
    current_state = samples[0].above_geometric_horizon
    first = samples[0]
    previous = samples[0]
    count = 0
    for sample in samples:
        state = sample.above_geometric_horizon
        if state != current_state:
            runs.append(_run_payload(current_state, first, previous, count))
            current_state = state
            first = sample
            count = 0
        count += 1
        previous = sample
    runs.append(_run_payload(current_state, first, previous, count))
    return runs


def _run_payload(state: bool, first: Sample, last: Sample, count: int) -> dict[str, Any]:
    return {
        "state": "above_geometric_horizon" if state else "below_geometric_horizon",
        "first_sample_utc": first.epoch_utc,
        "last_sample_utc": last.epoch_utc,
        "sample_count": count,
        "first_elevation_deg": first.elevation_deg,
        "last_elevation_deg": last.elevation_deg,
    }


def horizon_crossing_brackets(samples: Sequence[Sample]) -> list[dict[str, Any]]:
    """Bracket every geometric-horizon sign change between consecutive samples.

    A bracket is evidence that a crossing happened between two sampled instants; it is
    not an interpolated crossing time. ``refine_crossing`` can tighten it.
    """
    brackets: list[dict[str, Any]] = []
    for previous, current in zip(samples, samples[1:], strict=False):
        if previous.above_geometric_horizon == current.above_geometric_horizon:
            continue
        brackets.append(
            {
                "from_utc": previous.epoch_utc,
                "to_utc": current.epoch_utc,
                "from_elevation_deg": previous.elevation_deg,
                "to_elevation_deg": current.elevation_deg,
                "direction": "sunrise" if current.above_geometric_horizon else "sunset",
                "bracketed_only": True,
            }
        )
    return brackets


def refine_crossing(
    elevation_at: Any,
    before: Sample,
    after: Sample,
    *,
    tolerance_seconds: float = 1.0,
    max_iterations: int = 80,
) -> dict[str, Any]:
    """Bisect a bracketed horizon crossing with the same documented geometry model.

    ``elevation_at`` receives a UTC epoch string and returns the model elevation in
    degrees. The result is a model crossing epoch, not an observed one; the flat
    tangent plane still ignores terrain relief.
    """
    if before.above_geometric_horizon == after.above_geometric_horizon:
        raise ValueError("crossing refinement needs samples on opposite horizon sides")
    if tolerance_seconds <= 0:
        raise ValueError("tolerance_seconds must be positive")
    low = utc_datetime(before.epoch_utc)
    high = utc_datetime(after.epoch_utc)
    low_value = float(elevation_at(before.epoch_utc))
    high_value = float(elevation_at(after.epoch_utc))
    if (low_value > 0.0) == (high_value > 0.0):
        raise ValueError("elevation function does not reproduce the bracket signs")
    iterations = 0
    while (high - low).total_seconds() > tolerance_seconds and iterations < max_iterations:
        middle = low + (high - low) / 2
        middle_value = float(elevation_at(utc_text(middle)))
        if (middle_value > 0.0) == (low_value > 0.0):
            low, low_value = middle, middle_value
        else:
            high, high_value = middle, middle_value
        iterations += 1
    crossing = low + (high - low) / 2
    return {
        "crossing_utc": utc_text(crossing),
        "from_utc": before.epoch_utc,
        "to_utc": after.epoch_utc,
        "direction": "sunrise" if after.above_geometric_horizon else "sunset",
        "bracketed_only": False,
        "refinement": "elevation_zero_bisection",
        "tolerance_seconds": tolerance_seconds,
        "iterations": iterations,
        "elevation_deg_at_crossing": float(elevation_at(utc_text(crossing))),
    }


def load_aoi_site(
    manifest_path: str | Path,
    reference_id: str,
    *,
    source_manifest: str | None = None,
) -> AoiSite:
    """Resolve one AOI from an existing committed reference manifest; invent nothing."""
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    datasets = {item["dataset_id"]: item for item in payload.get("datasets", [])}
    for reference in payload.get("references", []):
        if reference.get("reference_id") != reference_id:
            continue
        dataset = datasets.get(reference["dataset_id"])
        if dataset is None:
            raise ValueError(f"{reference_id} references unknown dataset")
        body = str(reference["body"])
        if body not in BODY_FRAME:
            raise ValueError(f"no documented body-fixed frame for {body!r}")
        return AoiSite(
            reference_id=reference_id,
            display_name=reference.get("display_name") or reference_id,
            body=body,
            latitude_deg=float(reference["lat"]),
            longitude_deg=float(reference["lon"]),
            dataset_id=str(reference["dataset_id"]),
            source_crs=str(dataset["source_crs"]),
            source_manifest=source_manifest or path.as_posix(),
        )
    raise ValueError(f"AOI {reference_id!r} not found in {path}")


def sample_record(record: SpiceGeometryRecord) -> Sample:
    """Convert a SPICE geometry record to the sample used by the pilot artifacts."""
    return Sample(
        epoch_utc=record.epoch_utc,
        incidence_deg=record.geometry.incidence_deg,
        elevation_deg=record.geometry.elevation_deg,
        local_solar_time_hours=record.geometry.local_solar_time_hours,
    )


def illumination_observation(
    site: AoiSite,
    record: SpiceGeometryRecord,
    feature_id: str,
    sample_index: int,
    *,
    window_start: str,
    window_end: str,
    cadence_minutes: int,
) -> dict[str, Any]:
    """Build a typed-v2 numeric observation for one sampled illumination instant."""
    if feature_id not in ILLUMINATION_FEATURES:
        raise ValueError(f"unsupported illumination feature: {feature_id}")
    geometry = record.geometry
    values = {
        "solar_incidence_deg": geometry.incidence_deg,
        "solar_elevation_deg": geometry.elevation_deg,
    }
    compact = record.epoch_utc.replace("-", "").replace(":", "")
    quality_flags = [
        "model_derived_solar_geometry",
        "time_bounded_aoi_series",
        "explicit_aoi_time_request",
        "not_observed_irradiance",
        "not_shadow_fraction",
        "not_ionizing_radiation",
        "flat_ellipsoid_tangent_plane",
        "geometric_horizon_only",
        "no_terrain_horizon",
        "no_atmosphere_or_cloud_model",
        "point_site_not_window_average",
        f"body_fixed_frame_{record.frame.lower()}",
        "planetocentric_latitude_longitude",
        "kernel_checksums_pinned",
        "de421_ephemeris",
    ]
    if geometry.elevation_deg <= 0.0:
        quality_flags.append("sun_below_geometric_horizon")
    observation = TypedObservation(
        observation_id=(f"d05-aoi:{site.body.lower()}:{site.reference_id}:{feature_id}:{compact}"),
        area_id=site.reference_id,
        body=site.body,  # type: ignore[arg-type]
        dimension_code="D05",
        feature_id=feature_id,
        value_kind="numeric",
        state="detected",
        value=float(values[feature_id]),
        unit="degree",
        uncertainty=Uncertainty(
            kind="unknown",
            notes=(
                "SPICE DE421 ephemeris and documented body-fixed frame; no per-site "
                "ephemeris/frame uncertainty is supplied. Flat biaxial-ellipsoid "
                "tangent plane; no terrain horizon, atmosphere or cloud model."
            ),
        ),
        spatial_support=SpatialSupport(
            kind="point",
            crs=record.frame,
            notes=(
                f"Body-fixed point ({record.site_latitude_deg:.8f} deg, "
                f"{record.site_longitude_deg:.8f} deg, east-positive) in {record.frame}; "
                f"site radius {record.site_radius_m:.3f} m. AOI centre only, no window "
                "aggregation."
            ),
        ),
        temporal_support=TemporalSupport(
            kind="instant",
            start=record.epoch_utc,
            local_solar_time=f"{geometry.local_solar_time_hours:05.2f} h",
            notes=(
                "One sampled UTC instant inside the declared AOI/time request "
                f"({window_start} to {window_end}, {cadence_minutes} min cadence, "
                f"sample index {sample_index}). Local solar time is derived from "
                "subsolar longitude and is ill-conditioned near a pole."
            ),
        ),
        depth_support=DepthSupport(kind="surface"),
        method_version=METHOD_VERSION,
        dataset_ids=[SPICE_DATASET_ID],
        source_asset_ids=body_kernel_asset_ids(site.body),
        quality_flags=quality_flags,
        score_role="context_only",
        legacy_provenance={
            "reference_manifest": site.source_manifest,
            "reference_id": site.reference_id,
            "source_dataset_id": site.dataset_id,
            "body_fixed_frame": record.frame,
            "epoch_utc": record.epoch_utc,
            "et_seconds": record.et_seconds,
            "site_latitude_deg": record.site_latitude_deg,
            "site_longitude_deg": record.site_longitude_deg,
            "site_radius_m": record.site_radius_m,
            "site_xyz_m": list(record.site_xyz_m),
            "surface_normal_xyz": list(record.surface_normal),
            "sun_xyz_m": list(record.sun_xyz_m),
            "sun_distance_m": geometry.sun_distance_m,
            "subsolar_latitude_deg": geometry.subsolar_latitude_deg,
            "subsolar_longitude_deg": geometry.subsolar_longitude_deg,
            "local_solar_time_hours": geometry.local_solar_time_hours,
            "kernel_asset_ids": body_kernel_asset_ids(site.body),
            "aoi_time_request": {
                "start_utc": window_start,
                "end_utc": window_end,
                "cadence_minutes": cadence_minutes,
                "sample_index": sample_index,
                "time_basis": "UTC instants converted to SPICE ET by naif0012.tls",
            },
            "horizon_definition": "geometric flat-ellipsoid tangent plane; elevation > 0",
        },
        notes=(
            f"Model-derived solar geometry ({SPICE_METHOD_VERSION}) at "
            f"{record.epoch_utc} in {record.frame}. Not observed irradiance, not a "
            "shadow fraction, not a terrain horizon, and not ionizing radiation."
        ),
    )
    return observation.model_dump(mode="json")


def radiation_observation(
    site: AoiSite,
    evidence: dict[str, Any],
    *,
    radiation_sources_sha256: str | None,
) -> dict[str, Any]:
    """Build the explicit missing-value record for the ionizing-radiation subgroup."""
    aoi_evidence = evidence["aoi_evidence"][site.reference_id]
    dataset_ids = [item["dataset_id"] for item in aoi_evidence["sources"]]
    flags = [
        "no_coverage_explicit",
        "ionizing_radiation_no_value_ingested",
        "no_flux_to_dose_conversion",
        "no_gy_to_sv_conversion",
        "official_source_audited",
        "missing_is_not_zero",
    ]
    if aoi_evidence["coverage_status"] == "unsupported":
        flags.append("unsupported_in_typed_vocabulary")
    if aoi_evidence["aoi_attribution"] == "different_site":
        flags.append("product_at_different_landing_site")
    if aoi_evidence["aoi_attribution"] == "orbital_instrument":
        flags.append("orbital_instrument_no_surface_dose")
    request = evidence["requested_window"]
    observation = TypedObservation(
        observation_id=(
            f"d05-aoi:ionizing:{site.body.lower()}:{site.reference_id}:"
            f"{RADIATION_FEATURE}:{request['start_utc'].replace('-', '').replace(':', '')}"
        ),
        area_id=site.reference_id,
        body=site.body,  # type: ignore[arg-type]
        dimension_code="D05",
        feature_id=RADIATION_FEATURE,
        value_kind="numeric",
        state="no_coverage",
        value=None,
        unit="Gy",
        uncertainty=Uncertainty(
            kind="unknown",
            notes=(
                "No AOI-attributable absorbed-dose/flux value exists in this bounded "
                "scope; no value, proxy or conversion is produced."
            ),
        ),
        spatial_support=SpatialSupport(
            kind="point",
            crs=site.frame,
            notes=(
                f"Requested AOI point ({site.latitude_deg:.8f} deg, "
                f"{site.longitude_deg:.8f} deg) in {site.frame}. No audited dose "
                "product covers this AOI."
            ),
        ),
        temporal_support=TemporalSupport(
            kind="unknown",
            notes=(
                f"Requested window {request['start_utc']} to {request['end_utc']} has "
                "no AOI-attributable ionizing-radiation record; no time support is "
                "derived from a different site or instrument."
            ),
        ),
        depth_support=DepthSupport(kind="surface"),
        method_version=RADIATION_METHOD_VERSION,
        dataset_ids=dataset_ids,
        source_asset_ids=[f"pds-dataset:{dataset_id}" for dataset_id in dataset_ids],
        quality_flags=flags,
        score_role="evidence_only",
        legacy_provenance={
            "coverage_status": aoi_evidence["coverage_status"],
            "aoi_attribution": aoi_evidence["aoi_attribution"],
            "typed_observation_state": "no_coverage",
            "requested_window": request,
            "time_coverage_overlap": aoi_evidence["time_coverage_overlap"],
            "audited_dataset_ids": dataset_ids,
            "blockers": aoi_evidence["blockers"],
            "radiation_sources_sha256": radiation_sources_sha256,
            "subgroup": "ionizing_radiation",
        },
        notes=(
            "Explicit missing evidence for the ionizing-radiation subgroup. "
            + " ".join(aoi_evidence["blockers"])
            + " Illumination geometry and ionizing radiation stay separate; no Gy/Sv "
            "value is produced."
        ),
    )
    return observation.model_dump(mode="json")


def radiation_request(site: AoiSite, window_start: str, window_end: str) -> dict[str, Any]:
    """The explicit AOI/time request for an ionizing-radiation value."""
    return {
        "request_id": (
            f"d05-aoi-ionizing:{window_start.replace('-', '').replace(':', '')}:{site.reference_id}"
        ),
        "dimension_code": "D05",
        "subgroup": "ionizing_radiation",
        "body": site.body,
        "area_id": site.reference_id,
        "feature_ids": [RADIATION_FEATURE],
        "site": {
            "latitude_deg": site.latitude_deg,
            "longitude_deg": site.longitude_deg,
            "frame": site.frame,
            "spatial_support": "point",
        },
        "time": {
            "start_utc": window_start,
            "end_utc": window_end,
            "kind": "interval",
            "time_basis": "UTC; no partial-interval substitution",
        },
        "required_before_any_value": [
            "verified instrument/model identity",
            "particle species/channel/energy range or dose definition",
            "exposure interval compatible with the requested window",
            "sensor geometry and shielding/material context",
            "calibration and dose-model version",
            "AOI attribution (instrument location or documented spatial mapping)",
        ],
        "forbidden": [
            "flux-to-dose or dose-to-flux conversion without explicit physics",
            "Gy/Sv conversion without a documented radiation-weighting model",
            "attributing an in-situ instrument at another landing site to this AOI",
            "treating orbital detector energy deposition as surface absorbed dose",
            "zero-filling or fabricating a value",
        ],
        "bounded": {
            "max_sources": 5,
            "max_aois": 1,
            "no_global_coverage_claim": True,
            "values_ingested": 0,
        },
    }


def illumination_request(
    site: AoiSite,
    *,
    window_start: str,
    window_end: str,
    cadence_minutes: int,
    sample_count: int,
) -> dict[str, Any]:
    """The explicit AOI/time request for solar illumination geometry."""
    return {
        "request_id": (
            f"d05-aoi-illumination:{window_start.replace('-', '').replace(':', '')}:"
            f"{site.reference_id}"
        ),
        "dimension_code": "D05",
        "subgroup": "solar_illumination",
        "body": site.body,
        "area_id": site.reference_id,
        "feature_ids": list(ILLUMINATION_FEATURES),
        "site": {
            "latitude_deg": site.latitude_deg,
            "longitude_deg": site.longitude_deg,
            "frame": site.frame,
            "spatial_support": "point",
            "reference_manifest": site.source_manifest,
            "source_dataset_id": site.dataset_id,
        },
        "time": {
            "start_utc": window_start,
            "end_utc": window_end,
            "cadence_minutes": cadence_minutes,
            "sample_count": sample_count,
            "inclusive_end": True,
            "time_basis": "UTC instants converted to SPICE ET by naif0012.tls",
        },
        "method_version": METHOD_VERSION,
        "source_dataset_id": SPICE_DATASET_ID,
        "kernels": list(BODY_KERNEL_FILENAMES[site.body]),
        "support_constraints": [
            "flat biaxial-ellipsoid tangent plane",
            "no terrain horizon or shadow mask",
            "no observer height or atmospheric model",
            "point AOI centre, not a spatial average",
        ],
        "bounded": {
            "max_samples": MAX_SAMPLES,
            "max_aois": 1,
            "no_global_coverage_claim": True,
        },
    }


# --- Ionizing-radiation official-source audit ---------------------------------------


@dataclass(frozen=True, slots=True)
class RadiationSourceSpec:
    """One audited official product. Metadata only: no science values are ingested."""

    source_id: str
    body: str
    provider: str
    dataset_id: str
    dataset_name: str
    collection_url: str
    catalog_url: str
    catalog_markers: tuple[str, ...]
    coverage_start: str
    coverage_stop: str
    spatial_support: str
    measured_quantity: str
    native_units: tuple[str, ...]
    product_kind: str
    aoi_coverage_status: str
    aoi_attribution: str
    blockers: tuple[str, ...]
    citation: str
    license: str = "NASA Planetary Data System public archive data; cite the dataset"
    license_url: str = "https://pds.nasa.gov/"
    label_probe_url: str | None = None
    label_probe_markers: tuple[str, ...] = ()


RADIATION_SOURCES: tuple[RadiationSourceSpec, ...] = (
    RadiationSourceSpec(
        source_id="pds-msl-rad-rdr",
        body="Mars",
        provider="NASA PDS Geosciences Node / MSL RAD team (SwRI)",
        dataset_id="MSL-M-RAD-3-RDR-V1.0",
        dataset_name="MSL MARS RADIATION ASSESSMENT DETECTOR RDR",
        collection_url="https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/",
        catalog_url=(
            "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/CATALOG/RAD_RDR_DS.CAT"
        ),
        catalog_markers=("MSL-M-RAD-3-RDR-V1.0", "START_TIME", "STOP_TIME"),
        coverage_start="2011-11-20T18:24:41",
        coverage_stop="2026-03-22T18:01:11",
        spatial_support=(
            "In-situ on the MSL/Curiosity rover; official landing site Gale Crater "
            "(4.5895 S, 137.4417 E per MSL_MISSION.CAT), about 3,752 km from the "
            "Jezero AOI (great-circle, spherical R=3389.5 km)"
        ),
        measured_quantity=(
            "Calibrated surface/cruise dosimetry incl. TOT_DOSE_B and TOT_DOSE_E dose-rate columns"
        ),
        native_units=("microGray / Hour",),
        product_kind="in_situ_surface_dose_rate",
        aoi_coverage_status="no_coverage",
        aoi_attribution="different_site",
        blockers=(
            "The audited in-situ dose record is measured at Gale Crater, about "
            "3,752 km from the Jezero AOI (great-circle, spherical R=3389.5 km); it "
            "is not an AOI observation.",
            "RAD RDR labels in the audited Sol carry placeholder ROVER_LAT/LON=0.000, "
            "so the archived record does not provide a usable per-record position for "
            "AOI attribution.",
            "No spatial mapping from the Gale record to the Jezero AOI is documented or "
            "applied; no proxy is substituted.",
        ),
        citation=(
            "J. Peterson, S. Rafkin, C. Zeitlin, B. Ehresmann, E. Weigle, S. Jeffers, "
            "and D.M. Hassler, MSL MARS RADIATION ASSESSMENT DETECTOR RDR V1.0, "
            "MSL-M-RAD-3-RDR-V1.0, NASA Planetary Data System, 2013."
        ),
        label_probe_url=(
            "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/DATA/"
            "SOL_04258_04354/RAD_RDR_2024_210_15_23_4258_V00.LBL"
        ),
        label_probe_markers=("OBS_000_TOT_DOSE_B", "microGray / Hour"),
    ),
    RadiationSourceSpec(
        source_id="pds-msl-rad-edr",
        body="Mars",
        provider="NASA PDS Geosciences Node / MSL RAD team",
        dataset_id="MSL-M-RAD-2-EDR-V1.0",
        dataset_name="MSL MARS RADIATION ASSESSMENT DETECTOR EDR",
        collection_url="https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-2-EDR-V1.0/",
        catalog_url=(
            "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-2-EDR-V1.0/CATALOG/RAD_EDR_DS.CAT"
        ),
        catalog_markers=("MSL-M-RAD-2-EDR-V1.0", "START_TIME", "STOP_TIME"),
        coverage_start="2023-07-29T21:03:21",
        coverage_stop="2026-03-22T08:29:11",
        spatial_support="In-situ on the MSL/Curiosity rover at Gale Crater",
        measured_quantity="Raw/telemetry-reconstructed RAD event data (EDR)",
        native_units=("raw instrument counts",),
        product_kind="in_situ_raw_event_data",
        aoi_coverage_status="no_coverage",
        aoi_attribution="different_site",
        blockers=(
            "EDR is raw event/telemetry data at Gale Crater, not a calibrated "
            "absorbed-dose value and not an observation at the Jezero AOI.",
            "Converting EDR counts to absorbed dose requires the RAD calibration and "
            "dose model, which is not selected or applied in this bounded scope.",
        ),
        citation=(
            "S. Rafkin, J. Peterson, C. Zeitlin, B. Ehresmann, E. Weigle, and "
            "D.M. Hassler, MSL MARS RADIATION ASSESSMENT DETECTOR EDR V1.0, "
            "MSL-M-RAD-2-EDR-V1.0, NASA Planetary Data System, 2013."
        ),
    ),
    RadiationSourceSpec(
        source_id="pds-lro-crater-cdr",
        body="Moon",
        provider="NASA PDS Planetary Plasma Interactions (PPI) Node / LRO CRaTER",
        dataset_id="LRO-L-CRAT-3-CDR-CALIBRATED-V1.0",
        dataset_name="LRO MOON CRATER 3 CALIBRATED ENERGY DATA V1.0",
        collection_url=("https://pds-ppi.igpp.ucla.edu/data/LRO-L-CRAT-3-CDR-CALIBRATED-V1.0/"),
        catalog_url=(
            "https://pds-ppi.igpp.ucla.edu/data/LRO-L-CRAT-3-CDR-CALIBRATED-V1.0/"
            "CATALOG/LRO_CRAT_L1_DS.CAT"
        ),
        catalog_markers=("LRO-L-CRAT-3-CDR-CALIBRATED-V1.0", "START_TIME", "STOP_TIME"),
        coverage_start="2009-06-29T00:00:00",
        coverage_stop="2026-06-30T23:59:59",
        spatial_support=(
            "Orbital instrument on LRO; six-detector telescope through shielding. "
            "Not a surface measurement at a lunar AOI."
        ),
        measured_quantity=(
            "Calibrated charged-particle energy depositions and instrument "
            "housekeeping (including accumulated instrument dosage)"
        ),
        native_units=("detector energy deposition / counts",),
        product_kind="orbital_detector_energy",
        aoi_coverage_status="unsupported",
        aoi_attribution="orbital_instrument",
        blockers=(
            "CRaTER measures energy deposition in an orbital detector stack, not "
            "absorbed dose at a lunar surface AOI.",
            "No surface dose transport model, detector-to-surface mapping or "
            "shielding/geometry model is selected or applied in this scope.",
            "The archived 'accumulated radiation dosage' housekeeping is instrument "
            "dosage, not a surface absorbed or equivalent dose.",
        ),
        citation=(
            "Spence, H.E., LRO MOON CRATER 3 CALIBRATED ENERGY DATA V1.0, "
            "LRO-L-CRAT-3-CDR-CALIBRATED-V1.0, NASA Planetary Data System, 2010."
        ),
    ),
    RadiationSourceSpec(
        source_id="pds-lro-crater-edr",
        body="Moon",
        provider="NASA PDS Planetary Plasma Interactions (PPI) Node / LRO CRaTER",
        dataset_id="LRO-L-CRAT-2-EDR-RAWDATA-V1.0",
        dataset_name="LRO MOON CRATER EDR RAWDATA VERSION 1.0",
        collection_url=("https://pds-ppi.igpp.ucla.edu/data/LRO-L-CRAT-2-EDR-RAWDATA-V1.0/"),
        catalog_url=(
            "https://pds-ppi.igpp.ucla.edu/data/LRO-L-CRAT-2-EDR-RAWDATA-V1.0/"
            "CATALOG/LRO_CRAT_L0_DS.CAT"
        ),
        catalog_markers=("LRO-L-CRAT-2-EDR-RAWDATA-V1.0", "START_TIME", "STOP_TIME"),
        coverage_start="2009-06-29T00:00:00",
        coverage_stop="2026-06-30T23:59:59",
        spatial_support="Orbital LRO CRaTER instrument; not a surface AOI observation",
        measured_quantity="Raw detector event records (EDR)",
        native_units=("raw instrument counts",),
        product_kind="orbital_raw_event_data",
        aoi_coverage_status="unsupported",
        aoi_attribution="orbital_instrument",
        blockers=(
            "EDR is raw orbital event data; no calibrated surface dose at the AOI.",
            "Reduction requires the CRaTER calibration and a surface transport model "
            "that are outside this bounded audit.",
        ),
        citation=(
            "Spence, H.E., LRO MOON CRATER EDR RAWDATA VERSION 1.0, "
            "LRO-L-CRAT-2-EDR-RAWDATA-V1.0, NASA Planetary Data System, 2010."
        ),
    ),
    RadiationSourceSpec(
        source_id="pds-lro-crater-ddr",
        body="Moon",
        provider="NASA PDS Planetary Plasma Interactions (PPI) Node / LRO CRaTER",
        dataset_id="LRO-L-CRAT-3/4-DDR-PROCESSED-V1.0",
        dataset_name="LRO MOON CRATER 3/4 CALIBRATED LET DATA V1.0",
        collection_url=("https://pds-ppi.igpp.ucla.edu/data/LRO-L-CRAT-3_4-DDR-PROCESSED-V1.0/"),
        catalog_url=(
            "https://pds-ppi.igpp.ucla.edu/data/LRO-L-CRAT-3_4-DDR-PROCESSED-V1.0/"
            "CATALOG/LRO_CRAT_L2_DS.CAT"
        ),
        catalog_markers=("LRO-L-CRAT-3/4-DDR-PROCESSED-V1.0", "CALIBRATED LET DATA"),
        coverage_start="2009-06-29T00:00:00",
        coverage_stop="2026-06-30T23:59:59",
        spatial_support=(
            "Orbital LRO CRaTER Level-2 products with spacecraft location/orientation "
            "ancillary data; not a surface AOI observation"
        ),
        measured_quantity=(
            "Per-event deposited energy in six silicon detectors plus estimated LET"
        ),
        native_units=("MeV deposited energy", "keV/um LET"),
        product_kind="orbital_deposited_energy_let",
        aoi_coverage_status="unsupported",
        aoi_attribution="orbital_instrument",
        blockers=(
            "Level-2 products are detector energy deposition and estimated LET, not "
            "surface absorbed or equivalent dose.",
            "No LET-to-dose transport, geometry or shielding model is applied here.",
        ),
        citation=(
            "Spence, H.E., LRO MOON CRATER 3/4 CALIBRATED LET DATA V1.0, "
            "LRO-L-CRAT-3/4-DDR-PROCESSED-V1.0, NASA Planetary Data System, 2010."
        ),
    ),
)


def interval_overlaps(
    first_start: str, first_stop: str, second_start: str, second_stop: str
) -> bool:
    """Half-open interval intersection on parsed UTC instants."""
    start_a = utc_datetime(first_start if first_start.endswith("Z") else first_start + "Z")
    stop_a = utc_datetime(first_stop if first_stop.endswith("Z") else first_stop + "Z")
    start_b = utc_datetime(second_start)
    stop_b = utc_datetime(second_stop)
    return start_a < stop_b and start_b < stop_a


def _probe_once(url: str, *, timeout: float, max_bytes: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Range": f"bytes=0-{max_bytes - 1}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            status = response.status
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
    except urllib.error.HTTPError as error:  # pragma: no cover - network dependent
        return {
            "url": url,
            "status": error.code,
            "content_type": "",
            "bytes_read": 0,
            "sha256": None,
            "error": f"HTTPError: {error.reason}",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as error:  # pragma: no cover
        return {
            "url": url,
            "status": None,
            "content_type": "",
            "bytes_read": 0,
            "sha256": None,
            "error": f"transport error: {error}",
        }
    truncated = len(body) > max_bytes
    body = body[:max_bytes]
    return {
        "url": url,
        "status": status,
        "content_type": content_type,
        "bytes_read": len(body),
        "truncated_at_probe_cap": truncated,
        "sha256": hashlib.sha256(body).hexdigest(),
        "text": body.decode("utf-8", errors="replace"),
        "error": None,
    }


def _retryable(status: int | None) -> bool:
    return status is None or status == 429 or 500 <= status < 600


def _probe(url: str, *, timeout: float, max_bytes: int, attempts: int = 3) -> dict[str, Any]:
    """Bounded metadata probe with a small retry budget for flaky archive servers."""
    result: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        result = _probe_once(url, timeout=timeout, max_bytes=max_bytes)
        result["attempts"] = attempt
        if not _retryable(result["status"]) or attempt == attempts:
            return result
        time.sleep(1.5)
    return result


def probe_radiation_sources(
    specs: Sequence[RadiationSourceSpec] = RADIATION_SOURCES,
    *,
    timeout: float = 45.0,
    max_bytes: int = PROBE_MAX_BYTES,
) -> dict[str, dict[str, Any]]:
    """Bounded metadata probes for the official sources; never downloads a data product."""
    results: dict[str, dict[str, Any]] = {}
    checked_at = datetime.now(UTC).isoformat()
    for spec in specs:
        catalog = _probe(spec.catalog_url, timeout=timeout, max_bytes=max_bytes)
        text = catalog.pop("text", "")
        results[spec.source_id] = {
            "checked_at": checked_at,
            "catalog": {
                **catalog,
                "markers_present": {marker: marker in text for marker in spec.catalog_markers},
            },
            "label": None,
        }
        if spec.label_probe_url:
            label = _probe(spec.label_probe_url, timeout=timeout, max_bytes=max_bytes)
            label_text = label.pop("text", "")
            results[spec.source_id]["label"] = {
                **label,
                "markers_present": {
                    marker: marker in label_text for marker in spec.label_probe_markers
                },
            }
    return results


def build_radiation_evidence(
    sites: Sequence[AoiSite],
    *,
    window_start: str,
    window_end: str,
    probes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the official-source audit and per-AOI coverage decision.

    A value is never produced. ``coverage_status`` is ``no_coverage`` when an official
    product exists but does not cover the requested AOI/site, and ``unsupported`` when
    the audited product is not a surface absorbed-dose product at all.
    """
    checked_at = datetime.now(UTC).isoformat()
    if probes:
        checked_at = max(
            (probe.get("checked_at") or checked_at for probe in probes.values()),
            default=checked_at,
        )
    sources: list[dict[str, Any]] = []
    for spec in RADIATION_SOURCES:
        probe = (probes or {}).get(spec.source_id)
        if probe is None:
            verification_status = "identified"
        else:
            catalog = probe["catalog"]
            markers_ok = all(catalog["markers_present"].values())
            verification_status = (
                "verified"
                if catalog["status"] in (200, 206) and markers_ok
                else "accessible"
                if catalog["status"] is not None
                else "unknown"
            )
        label = (probe or {}).get("label")
        sources.append(
            {
                "source_id": spec.source_id,
                "subgroup": "ionizing_radiation",
                "body": spec.body,
                "provider": spec.provider,
                "dataset_id": spec.dataset_id,
                "dataset_name": spec.dataset_name,
                "collection_url": spec.collection_url,
                "catalog_url": spec.catalog_url,
                "coverage_start": spec.coverage_start,
                "coverage_stop": spec.coverage_stop,
                "spatial_support": spec.spatial_support,
                "measured_quantity": spec.measured_quantity,
                "native_units": list(spec.native_units),
                "product_kind": spec.product_kind,
                "aoi_coverage_status": spec.aoi_coverage_status,
                "aoi_attribution": spec.aoi_attribution,
                "blockers": list(spec.blockers),
                "citation": spec.citation,
                "license": spec.license,
                "license_url": spec.license_url,
                "verification_status": verification_status,
                "values_ingested": 0,
                "probe_checked_at": (probe or {}).get("checked_at", checked_at),
                "catalog_probe": None if probe is None else probe["catalog"],
                "label_probe": None
                if label is None
                else {
                    **label,
                    "purpose": (
                        "Product-semantics check only (declared dose-rate columns and "
                        "units). The values in this file are not ingested."
                    ),
                },
            }
        )

    aoi_evidence: dict[str, Any] = {}
    for site in sites:
        body_sources = [source for source in sources if source["body"] == site.body]
        if not body_sources:
            raise ValueError(f"no audited ionizing-radiation source for {site.body}")
        overlapping = [
            source
            for source in body_sources
            if interval_overlaps(
                source["coverage_start"],
                source["coverage_stop"],
                window_start,
                window_end,
            )
        ]
        statuses = {source["aoi_coverage_status"] for source in body_sources}
        coverage_status = "no_coverage" if "no_coverage" in statuses else "unsupported"
        attributions = {source["aoi_attribution"] for source in body_sources}
        aoi_evidence[site.reference_id] = {
            "body": site.body,
            "area_id": site.reference_id,
            "feature_id": RADIATION_FEATURE,
            "requested_window": {"start_utc": window_start, "end_utc": window_end},
            "coverage_status": coverage_status,
            "typed_state": "no_coverage",
            "aoi_attribution": (
                "different_site" if "different_site" in attributions else "orbital_instrument"
            ),
            "time_coverage_overlap": bool(overlapping),
            "sources": [
                {
                    "source_id": source["source_id"],
                    "dataset_id": source["dataset_id"],
                    "product_kind": source["product_kind"],
                    "coverage_status": source["aoi_coverage_status"],
                    "verification_status": source["verification_status"],
                }
                for source in body_sources
            ],
            "blockers": [
                f"{source['dataset_id']}: {blocker}"
                for source in body_sources
                for blocker in source["blockers"]
            ],
            "values_produced": 0,
        }

    return {
        "scope": "d05-aoi-ionizing-radiation-audit",
        "subgroup": "ionizing_radiation",
        "method_version": RADIATION_METHOD_VERSION,
        "checked_at": checked_at,
        "requested_window": {"start_utc": window_start, "end_utc": window_end},
        "no_value_ingested": True,
        "values_produced": 0,
        "conversions_applied": [],
        "forbidden_conversions": [
            "flux to dose without detector response and exposure interval",
            "absorbed dose (Gy) to equivalent dose (Sv) without a documented "
            "radiation-weighting model",
            "orbital detector energy deposition to surface absorbed dose",
            "in-situ dose at one landing site to a different AOI",
            "zero-fill or synthetic value substitution",
        ],
        "sources": sources,
        "aoi_evidence": aoi_evidence,
        "non_claims": [
            "No ionizing-radiation dose or flux value is claimed for any AOI.",
            "The audited products are not ingested, averaged, interpolated or converted.",
            "No global or exhaustive ionizing-radiation coverage is claimed.",
        ],
    }


def radiation_assets(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Source-asset entries for the audited official datasets (metadata only)."""
    assets: list[dict[str, Any]] = []
    for source in evidence["sources"]:
        catalog = source["catalog_probe"]
        assets.append(
            {
                "asset_id": f"pds-dataset:{source['dataset_id']}",
                "dataset_id": source["dataset_id"],
                "uri": source["collection_url"],
                "checksum_algorithm": "sha256" if catalog and catalog["sha256"] else "unknown",
                "checksum": catalog["sha256"] if catalog else None,
                "checksum_scope": "metadata_snapshot",
                "checksum_note": (
                    "SHA-256 of the bounded catalog metadata response observed at "
                    "probe_checked_at; PDS catalog labels are revised over time and are "
                    "not pinned by this pilot."
                ),
                "bytes": catalog["bytes_read"] if catalog else None,
                "fetched_at": source["probe_checked_at"],
                "redistribution_allowed": "unknown",
                "purpose": f"Official ionizing-radiation source audit: {source['dataset_name']}",
                "values_ingested": 0,
            }
        )
    return assets
