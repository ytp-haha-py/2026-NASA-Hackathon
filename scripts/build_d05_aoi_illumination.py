#!/usr/bin/env python
"""Build the bounded D05 AOI/time illumination + ionizing-radiation evidence pilot (#170).

The builder computes model-derived Sun incidence/elevation for one Moon and one Mars
AOI over an explicit UTC window, records the geometric flat-ellipsoid horizon
(above/below) and its bracketed crossings, and writes an official-source audit for
ionizing radiation with an explicit ``no_coverage``/``unsupported`` decision.

It never observes irradiance, never computes a shadow fraction from terrain, never
ingests or converts a radiation dose/flux value, and never downloads a science
raster or data product. Only five pinned NAIF kernels (if missing) and bounded PDS
catalog/label metadata are fetched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_pipelines.acquisition.models import (
    AcquisitionPlan,
    AcquisitionTask,
    PlanArea,
    file_sha256,
    write_json,
)
from analog_pipelines.illumination_aoi import (
    DEFAULT_CADENCE_MINUTES,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    ILLUMINATION_FEATURES,
    METHOD_VERSION,
    PILOT_AOIS,
    RADIATION_FEATURE,
    RADIATION_METHOD_VERSION,
    RELEASE_VERSION,
    SPICE_DATASET_ID,
    AoiSite,
    Sample,
    build_radiation_evidence,
    elevation_runs,
    horizon_crossing_brackets,
    illuminated_sample_fraction,
    illumination_observation,
    illumination_request,
    load_aoi_site,
    probe_radiation_sources,
    radiation_assets,
    radiation_observation,
    radiation_request,
    refine_crossing,
    sample_epochs,
    sample_record,
    utc_text,
)
from analog_pipelines.solar_geometry import (
    KERNEL_SPECS,
    KernelIntegrityError,
    SolarGeometryError,
    SpiceSolarGeometryProvider,
    ensure_kernels,
    spice_available,
)
from pyproj import CRS, Transformer

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = "var/cache/spice-45"
DEFAULT_OUTPUT = "pipelines/d05_d06_aoi/illumination"
DEFAULT_DOC = "docs/D05_AOI_170.md"
TERRAIN_HORIZON_REASON = (
    "Raw DEM rasters are not committed and no bounded DEM ingest is part of this "
    "scope, so no terrain horizon, ridge shadow mask or horizon visibility interval "
    "is computed. Only the geometric flat-ellipsoid horizon (Sun above/below the "
    "local tangent plane) is reported."
)


@dataclass(frozen=True, slots=True)
class AoiSeries:
    """Computed illumination samples and horizon support for one AOI."""

    site: AoiSite
    samples: list[Sample]
    crossings: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    elevation_min_deg: float
    elevation_max_deg: float

    @property
    def illuminated_fraction(self) -> float:
        return illuminated_sample_fraction(self.samples)


def repo_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def projected_bbox(
    crs_wkt: str, latitude_deg: float, longitude_deg: float, *, half_extent_m: float
) -> dict[str, float]:
    """Projected database envelope for the AOI; never the analysis footprint."""
    if half_extent_m <= 0:
        raise ValueError("half_extent_m must be positive")
    crs = CRS.from_user_input(crs_wkt)
    if not crs.is_projected or crs.geodetic_crs is None:
        raise ValueError("reference source CRS must be projected with a geographic base")
    transformer = Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True)
    x, y = transformer.transform(longitude_deg, latitude_deg)
    if not all(math.isfinite(value) for value in (x, y)):
        raise ValueError("projected site coordinate is not finite")
    return {
        "west": float(x - half_extent_m),
        "south": float(y - half_extent_m),
        "east": float(x + half_extent_m),
        "north": float(y + half_extent_m),
    }


def build_plan(
    sites: list[AoiSite],
    *,
    window_start: str,
    window_end: str,
    cadence_minutes: int,
    sample_count: int,
    half_extent_m: float,
) -> AcquisitionPlan:
    tasks: list[AcquisitionTask] = []
    for site in sites:
        area = PlanArea(
            area_id=site.reference_id,
            display_name=site.display_name,
            body=site.body,  # type: ignore[arg-type]
            bbox=projected_bbox(
                site.source_crs,
                site.latitude_deg,
                site.longitude_deg,
                half_extent_m=half_extent_m,
            ),
            crs=site.source_crs,
            coordinate_frame="lunar_projected" if site.body == "Moon" else "mars_projected",
            notes=(
                f"{site.body} AOI point ({site.latitude_deg:.8f} deg, "
                f"{site.longitude_deg:.8f} deg, east-positive) in {site.frame}; "
                f"source dataset {site.dataset_id}. The projected bbox is a 2 km "
                "database envelope, not an analysis footprint. Solar geometry is "
                "computed at the centre point."
            ),
        )
        tasks.append(
            AcquisitionTask(
                task_id=f"d05-aoi-illumination-{site.reference_id}",
                provider_id="naif-spice",
                source_id=SPICE_DATASET_ID,
                body=site.body,  # type: ignore[arg-type]
                dimension="D05",
                area=area,
                endpoint="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/",
                parameters={
                    "subgroup": "solar_illumination",
                    "window_start_utc": window_start,
                    "window_end_utc": window_end,
                    "cadence_minutes": str(cadence_minutes),
                    "sample_count": str(sample_count),
                    "features": ",".join(ILLUMINATION_FEATURES),
                    "body_fixed_frame": site.frame,
                    "method_version": METHOD_VERSION,
                    "horizon_support": "geometric_flat_ellipsoid_only",
                    "ionizing_radiation": "separate_subgroup_no_value",
                },
                method_version=METHOD_VERSION,
                dataset_id=SPICE_DATASET_ID,
            )
        )
    return AcquisitionPlan(
        scope="pilot-d05-aoi-illumination-170",
        generated_at=window_start,
        tasks=tasks,
        totals={
            "tasks": len(tasks),
            "aois": len(sites),
            "bodies": dict(sorted(Counter(site.body for site in sites).items())),
            "window": {"start_utc": window_start, "end_utc": window_end},
            "cadence_minutes": cadence_minutes,
            "sample_instants_per_aoi": sample_count,
            "method_version": METHOD_VERSION,
            "radiation_method_version": RADIATION_METHOD_VERSION,
        },
        notes=[
            "Bounded AOI/time request: one Moon and one Mars AOI, explicit UTC window.",
            "Model-derived solar geometry only; not observed irradiance or shadow fraction.",
            "Ionizing radiation is a separate subgroup with an explicit missing-value record.",
            "No kernel bytes, DEM raster or PDS science product are committed.",
        ],
    )


def build_series(
    sites: list[AoiSite],
    epochs: list[datetime],
    provider: SpiceSolarGeometryProvider,
) -> tuple[dict[str, AoiSeries], dict[str, dict[str, Any]], dict[str, Any]]:
    """Compute all samples, brackets and refined crossings for every AOI."""
    records: dict[str, dict[str, Any]] = {}
    series: dict[str, AoiSeries] = {}
    frames: dict[str, Any] = {}
    for site in sites:
        samples: list[Sample] = []
        site_records: dict[str, Any] = {}
        for epoch in epochs:
            record = provider.compute_site(
                body=site.body,
                latitude_deg=site.latitude_deg,
                longitude_deg=site.longitude_deg,
                epoch_utc=utc_text(epoch),
            )
            site_records[record.epoch_utc] = record
            samples.append(sample_record(record))
        records[site.reference_id] = site_records
        by_epoch = {sample.epoch_utc: sample for sample in samples}

        def elevation_at(epoch_utc: str, _site: AoiSite = site) -> float:
            return provider.compute_site(
                body=_site.body,
                latitude_deg=_site.latitude_deg,
                longitude_deg=_site.longitude_deg,
                epoch_utc=epoch_utc,
            ).geometry.elevation_deg

        crossings = [
            refine_crossing(
                elevation_at,
                by_epoch[bracket["from_utc"]],
                by_epoch[bracket["to_utc"]],
            )
            for bracket in horizon_crossing_brackets(samples)
        ]
        series[site.reference_id] = AoiSeries(
            site=site,
            samples=samples,
            crossings=crossings,
            runs=elevation_runs(samples),
            elevation_min_deg=min(sample.elevation_deg for sample in samples),
            elevation_max_deg=max(sample.elevation_deg for sample in samples),
        )
        first = site_records[samples[0].epoch_utc]
        frames[site.body] = {
            "frame": first.frame,
            "frame_id": provider.frame_id(first.frame),
            "validation": first.frame_validation,
            "spice_version": first.spice_version,
            "aoi_id": site.reference_id,
        }
    return series, records, frames


def build_payloads(
    series: dict[str, AoiSeries],
    records: dict[str, dict[str, Any]],
    *,
    window_start: str,
    window_end: str,
    cadence_minutes: int,
    radiation: dict[str, Any],
    radiation_sources_sha256: str | None,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for site_series in series.values():
        site = site_series.site
        for index, sample in enumerate(site_series.samples):
            record = records[site.reference_id][sample.epoch_utc]
            for feature_id in ILLUMINATION_FEATURES:
                payloads.append(
                    illumination_observation(
                        site,
                        record,
                        feature_id,
                        index,
                        window_start=window_start,
                        window_end=window_end,
                        cadence_minutes=cadence_minutes,
                    )
                )
        payloads.append(
            radiation_observation(
                site,
                radiation,
                radiation_sources_sha256=radiation_sources_sha256,
            )
        )
    return payloads


def build_requests(
    series: dict[str, AoiSeries],
    *,
    window_start: str,
    window_end: str,
    cadence_minutes: int,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for site_series in series.values():
        site = site_series.site
        requests.append(
            illumination_request(
                site,
                window_start=window_start,
                window_end=window_end,
                cadence_minutes=cadence_minutes,
                sample_count=len(site_series.samples),
            )
        )
        requests.append(radiation_request(site, window_start, window_end))
    return requests


def write_jsonl(path: Path, payloads: list[dict[str, Any]]) -> str:
    text = "\n".join(json.dumps(payload, sort_keys=True) for payload in payloads) + "\n"
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_horizon(
    series: dict[str, AoiSeries],
    *,
    window_start: str,
    window_end: str,
    cadence_minutes: int,
) -> dict[str, Any]:
    aois: list[dict[str, Any]] = []
    for site_series in series.values():
        site = site_series.site
        aois.append(
            {
                "area_id": site.reference_id,
                "body": site.body,
                "frame": site.frame,
                "window": {"start_utc": window_start, "end_utc": window_end},
                "cadence_minutes": cadence_minutes,
                "sample_count": len(site_series.samples),
                "illuminated_sample_count": sum(
                    1 for sample in site_series.samples if sample.above_geometric_horizon
                ),
                "illuminated_sample_fraction": site_series.illuminated_fraction,
                "elevation_min_deg": site_series.elevation_min_deg,
                "elevation_max_deg": site_series.elevation_max_deg,
                "runs": site_series.runs,
                "horizon_crossings": site_series.crossings,
                "terrain_horizon": {
                    "available": False,
                    "shadow_fraction_computed": False,
                    "horizon_visibility_interval_available": False,
                    "reason": TERRAIN_HORIZON_REASON,
                },
            }
        )
    return {
        "release_version": RELEASE_VERSION,
        "method_version": METHOD_VERSION,
        "subgroup": "solar_illumination",
        "definition": (
            "Geometric flat-ellipsoid horizon: the Sun is above the horizon when the "
            "model elevation is strictly greater than 0 degrees. Runs are bounded by "
            "sampled instants; refined crossings use elevation-zero bisection with the "
            "same model and do not add terrain relief."
        ),
        "window": {"start_utc": window_start, "end_utc": window_end},
        "cadence_minutes": cadence_minutes,
        "aois": aois,
        "terrain_horizon_available": False,
        "terrain_horizon_reason": TERRAIN_HORIZON_REASON,
        "limitations": [
            "The geometric horizon ignores DEM relief, ridges, boulders and local "
            "topography; it is not a shadow mask or a visibility interval.",
            "A small above/below margin (under roughly 0.5 degrees) can be reversed by "
            "unmodeled terrain relief and site elevation; a negative flat-model "
            "elevation is not evidence of permanent darkness.",
            "No observer height, atmospheric extinction, cloud or dust model is applied.",
            "Sampled instants are discrete; refined crossings are model crossings of "
            "the flat tangent plane, not observed sunrise/sunset times.",
        ],
        "non_claims": [
            "No shadow fraction or shadow mask is produced.",
            "No terrain horizon visibility interval is produced.",
            "No observed irradiance, radiance or power value is produced.",
        ],
    }


def build_manifest(
    *,
    cache_dir: Path,
    hashes: dict[str, str],
    series: dict[str, AoiSeries],
    frames: dict[str, Any],
    payloads: list[dict[str, Any]],
    radiation: dict[str, Any],
    window_start: str,
    window_end: str,
    cadence_minutes: int,
    input_manifests: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    counts = {
        "observations": len(payloads),
        "illumination_observations": sum(
            1 for payload in payloads if payload["feature_id"] in ILLUMINATION_FEATURES
        ),
        "radiation_evidence_observations": sum(
            1 for payload in payloads if payload["feature_id"] == RADIATION_FEATURE
        ),
        "aois": len(series),
        "bodies": dict(sorted(Counter(payload["body"] for payload in payloads).items())),
        "by_feature": dict(sorted(Counter(payload["feature_id"] for payload in payloads).items())),
        "by_state": dict(sorted(Counter(payload["state"] for payload in payloads).items())),
        "by_score_role": dict(
            sorted(Counter(payload["score_role"] for payload in payloads).items())
        ),
        "sample_instants_per_aoi": len(next(iter(series.values())).samples),
        "no_coverage": sum(1 for payload in payloads if payload["state"] == "no_coverage"),
        "radiation_values_produced": 0,
    }
    aois = []
    for site_series in series.values():
        site = site_series.site
        aois.append(
            {
                "area_id": site.reference_id,
                "display_name": site.display_name,
                "body": site.body,
                "frame": site.frame,
                "latitude_deg": site.latitude_deg,
                "longitude_deg": site.longitude_deg,
                "source_dataset_id": site.dataset_id,
                "reference_manifest": site.source_manifest,
                "window": {"start_utc": window_start, "end_utc": window_end},
                "cadence_minutes": cadence_minutes,
                "sample_count": len(site_series.samples),
                "elevation_min_deg": site_series.elevation_min_deg,
                "elevation_max_deg": site_series.elevation_max_deg,
                "illuminated_sample_fraction": site_series.illuminated_fraction,
                "horizon_crossings": len(site_series.crossings),
                "radiation_coverage_status": radiation["aoi_evidence"][site.reference_id][
                    "coverage_status"
                ],
                "radiation_values_produced": 0,
            }
        )
    return {
        "release_version": RELEASE_VERSION,
        "generated_at": generated_at,
        "scope": "pilot-d05-aoi-illumination-170",
        "data_kind": "model_derived",
        "method_version": METHOD_VERSION,
        "radiation_method_version": RADIATION_METHOD_VERSION,
        "subgroups": ["solar_illumination", "ionizing_radiation"],
        "window": {
            "start_utc": window_start,
            "end_utc": window_end,
            "cadence_minutes": cadence_minutes,
            "inclusive_end": True,
            "time_basis": "UTC instants converted to SPICE ET by naif0012.tls",
        },
        "features": [
            *ILLUMINATION_FEATURES,
            RADIATION_FEATURE,
        ],
        "frames": frames,
        "kernels": [
            {
                "filename": spec.filename,
                "url": spec.url,
                "sha256": spec.sha256,
                "bytes": spec.bytes,
                "purpose": spec.purpose,
                "asset_id": spec.asset_id,
                "local_cache_path": repo_relative(cache_dir / spec.filename),
            }
            for spec in KERNEL_SPECS
        ],
        "kernel_count": len(KERNEL_SPECS),
        "kernel_total_bytes": sum(spec.bytes for spec in KERNEL_SPECS),
        "input_manifests": input_manifests,
        "hashes": hashes,
        "counts": counts,
        "observation_count": counts["observations"],
        "area_count": counts["aois"],
        "aois": aois,
        "coverage_contract": {
            "contract_version": "location-coverage-status-1.0.0",
            "global_coverage_claimed": False,
            "aoi_status": {
                aoi["area_id"]: {
                    "solar_incidence_deg": "modeled",
                    "solar_elevation_deg": "modeled",
                    RADIATION_FEATURE: aoi["radiation_coverage_status"],
                }
                for aoi in aois
            },
            "score_roles": {
                "solar_incidence_deg": "context_only",
                "solar_elevation_deg": "context_only",
                RADIATION_FEATURE: "evidence_only",
            },
            "note": (
                "Statuses follow location-coverage-status-1.0.0 vocabulary; indexing "
                "them into the API snapshot and the D05 coverage matrix is a separate "
                "integration step."
            ),
        },
        "radiation": {
            "no_value_ingested": radiation["no_value_ingested"],
            "values_produced": radiation["values_produced"],
            "source_count": len(radiation["sources"]),
            "sources": [
                {
                    "source_id": source["source_id"],
                    "dataset_id": source["dataset_id"],
                    "body": source["body"],
                    "product_kind": source["product_kind"],
                    "verification_status": source["verification_status"],
                    "aoi_coverage_status": source["aoi_coverage_status"],
                    "coverage_start": source["coverage_start"],
                    "coverage_stop": source["coverage_stop"],
                }
                for source in radiation["sources"]
            ],
            "aoi_evidence": radiation["aoi_evidence"],
            "forbidden_conversions": radiation["forbidden_conversions"],
        },
        "limitations": [
            "Model-derived solar geometry only: not observed irradiance, radiance, "
            "shadow fraction, or ionizing radiation.",
            "Flat biaxial-ellipsoid tangent plane; no DEM terrain horizon, shadow mask, "
            "observer height, atmosphere or cloud model.",
            "The AOI is placed on the reference biaxial ellipsoid; source DEM elevations "
            "and observer heights are not added.",
            "Sun position is the light-time-corrected body-centre vector from SPICE; "
            "site parallax is applied by vector subtraction.",
            "Point AOI centres; not a spatial average over the existing 1.98 km DEM "
            "windows and not global coverage.",
            "Moon uses MOON_ME realised from DE421; Mars uses IAU_MARS from pck00011. "
            "Source projected-CRS registration is a documented assumption, not an "
            "independent geodetic validation.",
            "Ionizing radiation is an official-source audit only; no dose/flux value is "
            "ingested, converted, interpolated or substituted.",
            "DE421 covers approximately 1900-2050; kernel SHA-256 digests are pinned and "
            "any drift is rejected by the builder.",
            "Kernels are cached under a gitignored var/cache directory and are not "
            "committed to Git.",
        ],
        "non_claims": [
            "No observed solar irradiance, radiance or power.",
            "No terrain shadow fraction, shadow mask or horizon visibility interval.",
            "No absorbed dose (Gy) or equivalent dose (Sv) value for any AOI.",
            "No flux-to-dose or Gy-to-Sv conversion.",
            "No global or exhaustive illumination or radiation coverage.",
            "No cross-body or cross-dimension similarity score from these values.",
        ],
        "reproduce_commands": [
            "uv sync --extra spice",
            "uv run --extra spice python scripts/build_d05_aoi_illumination.py --download",
            "uv run --extra spice python scripts/build_d05_aoi_illumination.py",
            "uv run --extra spice python scripts/build_d05_aoi_illumination.py "
            "--probe-radiation --download",
            "uv run --extra spice pytest pipelines/tests/test_d05_aoi_illumination.py -q",
        ],
        "notes": [
            "Bounded AOI/time request: one Moon and one Mars AOI, 24 h window, hourly cadence.",
            "Values are a solar-geometry model, not a measurement of surface illumination.",
            "The radiation audit records verification evidence and blockers; it never "
            "creates a value.",
        ],
    }


def markdown_series_table(series: dict[str, AoiSeries]) -> str:
    lines = [
        "| Body | Area | Sample UTC | Incidence (deg) | Elevation (deg) | Local solar time | Geometric horizon |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for site_series in series.values():
        for sample in site_series.samples:
            horizon = "above" if sample.above_geometric_horizon else "below"
            lines.append(
                f"| {site_series.site.body} | `{site_series.site.reference_id}` | "
                f"{sample.epoch_utc} | {sample.incidence_deg:.6f} | "
                f"{sample.elevation_deg:+.6f} | {sample.local_solar_time_hours:.2f} h | "
                f"{horizon} |"
            )
    return "\n".join(lines)


def markdown_aoi_table(manifest: dict[str, Any]) -> str:
    lines = [
        "| Body | AOI | Frame | Lat (deg) | Lon (deg) | Samples | Elevation min/max (deg) | Illuminated fraction | Crossings | Radiation |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for aoi in manifest["aois"]:
        lines.append(
            f"| {aoi['body']} | `{aoi['area_id']}` | `{aoi['frame']}` | "
            f"{aoi['latitude_deg']:.6f} | {aoi['longitude_deg']:.6f} | "
            f"{aoi['sample_count']} | {aoi['elevation_min_deg']:.6f} / "
            f"{aoi['elevation_max_deg']:.6f} | "
            f"{aoi['illuminated_sample_fraction']:.4f} | {aoi['horizon_crossings']} | "
            f"`{aoi['radiation_coverage_status']}` |"
        )
    return "\n".join(lines)


def markdown_radiation_table(radiation: dict[str, Any]) -> str:
    lines = [
        "| Body | Dataset | Product kind | Archive window | Verification | AOI coverage |",
        "|---|---|---|---|---|---|",
    ]
    for source in radiation["sources"]:
        lines.append(
            f"| {source['body']} | `{source['dataset_id']}` | "
            f"{source['product_kind']} | {source['coverage_start']} .. "
            f"{source['coverage_stop']} | {source['verification_status']} | "
            f"`{source['aoi_coverage_status']}` |"
        )
    return "\n".join(lines)


def markdown_probe_table(radiation: dict[str, Any]) -> str:
    lines = [
        "| Dataset | Probe URL | HTTP | Bytes read | SHA-256 (bounded response) | Markers |",
        "|---|---|---:|---:|---|---|",
    ]
    for source in radiation["sources"]:
        probe = source["catalog_probe"]
        if probe is None:
            lines.append(f"| `{source['dataset_id']}` | metadata probe not run | - | - | - | - |")
            continue
        markers = ", ".join(
            f"{name}={'yes' if present else 'no'}"
            for name, present in probe["markers_present"].items()
        )
        lines.append(
            f"| `{source['dataset_id']}` | `{probe['url']}` | {probe['status']} | "
            f"{probe['bytes_read']} | `{probe['sha256']}` | {markers} |"
        )
    return "\n".join(lines)


def write_readme(output: Path, manifest: dict[str, Any]) -> None:
    counts = manifest["counts"]
    text = f"""# D05 AOI/time illumination + ionizing-radiation evidence (#170)

Bounded, model-derived, offline-reproducible pilot for **one Moon** and **one Mars**
AOI. Illumination geometry and ionizing radiation are separate D05 subgroups and are
never combined into one value.

## Actual content

| Item | Value |
|---|---|
| Method | `{manifest["method_version"]}` |
| Radiation audit | `{manifest["radiation_method_version"]}` |
| Window | `{manifest["window"]["start_utc"]}` .. `{manifest["window"]["end_utc"]}` ({manifest["window"]["cadence_minutes"]} min cadence) |
| AOIs | {counts["aois"]} |
| Observations | {counts["observations"]} (illumination {counts["illumination_observations"]}, radiation evidence {counts["radiation_evidence_observations"]}) |
| Bodies | {counts["bodies"]} |
| States | {counts["by_state"]} |
| Score roles | {counts["by_score_role"]} |

| Body | AOI | Illuminated sample fraction | Horizon crossings | Radiation |
|---|---|---|---:|---:|---|
{chr(10).join(f"| {aoi['body']} | `{aoi['area_id']}` | {aoi['illuminated_sample_fraction']:.4f} | {aoi['horizon_crossings']} | `{aoi['radiation_coverage_status']}` |" for aoi in manifest["aois"])}

## What the values are

- Sun incidence/elevation angles at explicit UTC instants, from pinned NAIF SPICE
  kernels and documented body-fixed frames.
- A **geometric flat-ellipsoid horizon**: Sun above/below the local tangent plane,
  with bracketed and bisection-refined zero crossings.
- **Not** observed irradiance, **not** a shadow fraction, **not** a terrain horizon,
  and **not** ionizing radiation.

## Ionizing radiation

The audit found real, accessible official products (MSL RAD RDR/EDR, LRO CRaTER
CDR/EDR/DDR). None is an AOI-attributable surface absorbed-dose product for these
requests, so each AOI gets an explicit missing-value record:

- Moon: `unsupported` — CRaTER products are orbital detector energy/LET, not surface
  absorbed dose.
- Mars: `no_coverage` — MSL RAD is in situ at Gale Crater (~3,752 km away), not at the
  Jezero AOI.

No dose/flux value is ingested, converted, interpolated or zero-filled.

## Files

- `requests.jsonl` — explicit AOI/time requests (illumination and radiation).
- `observations.jsonl` — typed-v2 observations (numeric angles + `no_coverage` evidence).
- `horizon.json` — geometric horizon runs and refined crossings per AOI.
- `radiation_sources.json` — official-source audit, probes, blockers and decisions.
- `plan.json` — acquisition-plan envelope; `assets.json` — kernels + audited metadata.
- `manifest.json` — hashes, counts, frames, kernels, limitations and non-claims.
- `REPORT.md` — compact per-sample results.

## Reproduce

```bash
uv sync --extra spice
uv run --extra spice python scripts/build_d05_aoi_illumination.py --download
uv run --extra spice python scripts/build_d05_aoi_illumination.py
uv run --extra spice python scripts/build_d05_aoi_illumination.py --probe-radiation --download
```

Full detail: [`docs/D05_AOI_170.md`](../../docs/D05_AOI_170.md).
"""
    (output / "README.md").write_text(text, encoding="utf-8")


def write_report(output: Path, manifest: dict[str, Any], series: dict[str, AoiSeries]) -> None:
    counts = manifest["counts"]
    horizon_lines = []
    for site_series in series.values():
        for crossing in site_series.crossings:
            horizon_lines.append(
                f"- `{site_series.site.reference_id}` {crossing['direction']} at "
                f"`{crossing['crossing_utc']}` (bisection, tol "
                f"{crossing['tolerance_seconds']} s, bracket "
                f"{crossing['from_utc']} .. {crossing['to_utc']})"
            )
    if not horizon_lines:
        horizon_lines.append("- No geometric horizon crossing inside the sampled window.")
    text = f"""# D05 AOI illumination pilot — report

- Release: `{manifest["release_version"]}`
- Illumination method: `{manifest["method_version"]}`
- Radiation audit: `{manifest["radiation_method_version"]}`
- Window: `{manifest["window"]["start_utc"]}` .. `{manifest["window"]["end_utc"]}`,
  {manifest["window"]["cadence_minutes"]} min cadence
- Observations: {counts["observations"]} (illumination {counts["illumination_observations"]},
  radiation evidence {counts["radiation_evidence_observations"]})
- AOIs: {counts["aois"]}
- By body: {counts["bodies"]}
- By feature: {counts["by_feature"]}
- By state: {counts["by_state"]}
- Radiation values produced: {counts["radiation_values_produced"]}

## AOI summary

{markdown_aoi_table(manifest)}

## Geometric horizon support

{chr(10).join(horizon_lines)}

All horizon support is the flat-ellipsoid geometric horizon. No terrain horizon,
shadow mask or horizon visibility interval is computed.

## Sample values

{markdown_series_table(series)}

All values are model-derived solar geometry at explicit UTC instants. No observed
irradiance, shadow fraction, ionizing dose or flux is present.
"""
    (output / "REPORT.md").write_text(text, encoding="utf-8")


def write_doc(
    path: Path,
    manifest: dict[str, Any],
    series: dict[str, AoiSeries],
    radiation: dict[str, Any],
) -> None:
    counts = manifest["counts"]
    frame_lines = [
        f"| {body} | `{info['frame']}` | {info['frame_id']} | {info['validation']} |"
        for body, info in manifest["frames"].items()
    ]
    input_lines = [
        f"| `{item['path']}` | `{item['sha256']}` | `{item['reference_id']}` |"
        for item in manifest["input_manifests"]
    ]
    moon_series = next(
        site_series for site_series in series.values() if site_series.site.body == "Moon"
    )
    moon_margin = abs(moon_series.elevation_max_deg)
    moon_area_id = moon_series.site.reference_id
    moon_max_elevation = moon_series.elevation_max_deg
    aoi_evidence_lines = []
    for area_id, evidence in radiation["aoi_evidence"].items():
        aoi_evidence_lines.append(
            f"\n### `{area_id}`\n\n"
            f"- Typed state: `{evidence['typed_state']}`; coverage decision: "
            f"`{evidence['coverage_status']}`; AOI attribution: "
            f"`{evidence['aoi_attribution']}`.\n"
            f"- Requested window: `{evidence['requested_window']['start_utc']}` .. "
            f"`{evidence['requested_window']['end_utc']}`; audited archive time "
            f"overlap: `{str(evidence['time_coverage_overlap']).lower()}`.\n"
            f"- Values produced: `{evidence['values_produced']}`.\n"
            + "\n".join(f"- Blocker: {item}" for item in evidence["blockers"])
        )
    text = f"""# D05 AOI/time illumination and ionizing-radiation evidence (Issue #170)

Status: **bounded, model-derived illumination geometry plus an explicit
missing-value record for ionizing radiation.** This is not observed irradiance, not a
shadow-fraction product, and not a radiation dose. Two AOIs are covered — one Moon and
one Mars — over an explicit 24 h UTC window.

## 1) AOI/time requests

| Body | AOI | Frame | Latitude (deg) | Longitude (deg) | Window (UTC) | Cadence | Samples |
|---|---|---|---:|---:|---|---:|---:|
{chr(10).join(f"| {aoi['body']} | `{aoi['area_id']}` | `{aoi['frame']}` | {aoi['latitude_deg']:.8f} | {aoi['longitude_deg']:.8f} | {aoi['window']['start_utc']} .. {aoi['window']['end_utc']} | {aoi['cadence_minutes']} min | {aoi['sample_count']} |" for aoi in manifest["aois"])}

Machine-readable requests: `pipelines/d05_d06_aoi/illumination/requests.jsonl`. Each
AOI has one `solar_illumination` request and one `ionizing_radiation` request with the
same window.

Time assumptions, stated explicitly:

- Time basis is UTC; instants are converted to SPICE ephemeris time with the pinned
  `naif0012.tls` leap-second kernel.
- The window endpoints are both sampled ({manifest["window"]["cadence_minutes"]} min
  cadence, {counts["sample_instants_per_aoi"]} instants), so the sampling grid is closed;
  nothing is interpolated between sampled instants.
- The existing single-instant SPICE pilot (epoch `2026-11-15T12:00:00Z`) remains a
  separate committed artifact; this pilot does not modify it.

## 2) Computed illumination values

{markdown_aoi_table(manifest)}

The complete {counts["illumination_observations"]} illumination observations are in
`pipelines/d05_d06_aoi/illumination/observations.jsonl`; the full table is repeated in
`REPORT.md`. Each observation carries:

- `feature_id` `solar_incidence_deg` or `solar_elevation_deg`, unit `degree`,
  `state=detected`, `score_role=context_only`;
- `temporal_support.kind=instant` with the sampled UTC epoch and the derived local
  solar time;
- `spatial_support.kind=point` in the body-fixed frame
  ({", ".join(f"`{body}` `{info['frame']}`" for body, info in manifest["frames"].items())});
- provenance with the Sun vector, site vector, surface normal, subsolar point,
  kernel asset IDs and the exact AOI/time request.

## 3) Shadow / horizon support — what is and is not available

Available: a **geometric flat-ellipsoid horizon** per AOI — contiguous above/below
runs at sampled instants and zero crossings refined by bisection with the same model.
See `horizon.json`.

Not available: a terrain horizon, ridge shadow mask or horizon visibility interval.
Reason: {TERRAIN_HORIZON_REASON}

The consequence is explicit: a near-polar site that the flat model places just below
the tangent plane is **not** thereby proven to be in permanent darkness; local relief
can illuminate it. For `{moon_area_id}` the highest sampled elevation is
{moon_max_elevation:+.6f} deg, so the unmodeled margin is only about
{moon_margin:.3f} deg. The pilot reports the geometric result only and makes no shadow
claim.

## 4) Ionizing radiation — official-source audit with no value

Audited official products (metadata only; no science product downloaded or ingested):

{markdown_radiation_table(radiation)}

Bounded metadata probes observed while building this artifact:

{markdown_probe_table(radiation)}

Every AOI receives the same explicit decision, for different reasons:

{chr(10).join(aoi_evidence_lines)}

Typed representation: `feature_id=ionizing_dose_gy`, `value=null`,
`state=no_coverage`, `unit=Gy`, `score_role=evidence_only`. The typed-v2 state
vocabulary has no `unsupported` value, so semantically unsupported cases are recorded
as `no_coverage` and the exact decision is preserved in `legacy_provenance` and
`radiation_sources.json`.

Forbidden and not applied: flux-to-dose, Gy-to-Sv, orbital energy deposition to
surface dose, and in-situ dose at one site to a different AOI. No value is zero-filled.

## 5) Frames, kernels and provenance

| Body | Frame | Frame ID | Validation |
|---|---|---:|---|
{chr(10).join(frame_lines)}

| Input manifest | SHA-256 | AOI |
|---|---|---|
{chr(10).join(input_lines)}

Kernels are the pinned public NAIF products already used by the D05 SPICE pilot
(`naif0012.tls`, `pck00011.tpc`, `de421.bsp`, `moon_pa_de421_1900-2050.bpc`,
`moon_080317.tf`). They are verified by size and SHA-256 on every run and cached in the
gitignored `var/cache/spice-45` directory; no kernel bytes are committed.

Artifact hashes are recorded in `manifest.json` (`hashes`): observations, requests,
horizon, radiation sources, plan and assets.

## 6) Coverage-contract mapping (#160)

The artifacts speak the `location-coverage-status-1.0.0` vocabulary without editing
the shared API contract or the D05 coverage matrix:

| D05 subgroup | Contract status | Score role | Evidence here |
|---|---|---|---|
| `solar_illumination` | `modeled` | `context_only` | time-bounded SPICE angles + geometric horizon runs |
| `ionizing_radiation` (Moon) | `unsupported` | `evidence_only` | orbital detector products; no surface dose product |
| `ionizing_radiation` (Mars) | `no_coverage` | `evidence_only` | in-situ dose only at Gale, not this AOI |

`global_coverage_claimed=false` for every request. Indexing these artifacts into the
API snapshot and the D05 coverage matrix is a separate integration step; this slice
does not edit shared API, scoring or inventory files.

## 7) Limits and blockers

- Model-derived geometry only; not observed irradiance, radiance or power.
- Flat biaxial-ellipsoid tangent plane; no terrain horizon, shadow mask, observer
  height, atmosphere, cloud or dust model.
- The AOI is placed on the reference ellipsoid; source DEM elevations are not added.
- Point AOI centres, not a spatial average over the existing 1.98 km DEM windows.
- Source projected-CRS to body-fixed-frame registration remains a documented
  assumption (lunar prime meridian; Mars 2000/IAU_MARS), not an independent geodetic
  validation.
- No AOI-attributable ionizing-radiation product exists in this bounded scope; the
  audited alternatives are orbital detector data (Moon) or in-situ data at another
  landing site (Mars).
- No global or exhaustive coverage; no cross-body D05 score.

## 8) Exact commands

```bash
uv sync --extra spice
uv run --extra spice python scripts/build_d05_aoi_illumination.py --download
uv run --extra spice python scripts/build_d05_aoi_illumination.py
uv run --extra spice python scripts/build_d05_aoi_illumination.py --probe-radiation --download
uv run --extra spice pytest pipelines/tests/test_d05_aoi_illumination.py -q
uv run ruff check .
uv run ruff format --check .
```

The first builder invocation fetches only the five pinned kernels when they are
missing. `--probe-radiation` performs bounded PDS metadata requests (catalog and one
label range probe, capped at 64 KiB each). Without it the committed audit evidence is
reused and no network call is made.

## 9) Tests

`pipelines/tests/test_d05_aoi_illumination.py` covers:

- pure time-grid, fraction, run and bisection helpers (no SPICE needed);
- committed artifact integrity: typed-v2 validation, unit/role/state separation,
  explicit missing radiation value, manifest hashes, request/window consistency;
- an opt-in SPICE recomputation test when `spiceypy` and the pinned kernel cache are
  available.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--doc", default=DEFAULT_DOC)
    parser.add_argument("--start", default=DEFAULT_WINDOW_START)
    parser.add_argument("--end", default=DEFAULT_WINDOW_END)
    parser.add_argument("--cadence-minutes", type=int, default=DEFAULT_CADENCE_MINUTES)
    parser.add_argument(
        "--half-bbox-m",
        type=float,
        default=1000.0,
        help="Half extent of the projected database area envelope (not an analysis footprint).",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download missing pinned public kernels; default is offline verification.",
    )
    parser.add_argument(
        "--probe-radiation",
        action="store_true",
        help="Run bounded PDS catalog/label metadata probes for the radiation audit.",
    )
    args = parser.parse_args()

    if not spice_available():
        print(
            "ERROR: spiceypy is not installed. Run `uv sync --extra spice` or "
            "`uv run --with 'spiceypy>=6,<7' ...`.",
            file=sys.stderr,
        )
        return 2

    cache_dir = Path(args.cache)
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    doc = Path(args.doc)
    if not doc.is_absolute():
        doc = ROOT / doc
    output.mkdir(parents=True, exist_ok=True)

    try:
        epochs = sample_epochs(args.start, args.end, args.cadence_minutes)
        kernels = ensure_kernels(cache_dir, download=args.download)
        sites = [
            load_aoi_site(
                ROOT / selection.manifest_path,
                selection.reference_id,
                source_manifest=selection.manifest_path,
            )
            for selection in PILOT_AOIS
        ]
        with SpiceSolarGeometryProvider(kernels) as provider:
            series, records, frames = build_series(sites, epochs, provider)
    except (SolarGeometryError, KernelIntegrityError, OSError, KeyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    radiation_sources_path = output / "radiation_sources.json"
    probes = None
    if args.probe_radiation:
        probes = probe_radiation_sources()
    elif radiation_sources_path.is_file():
        previous = json.loads(radiation_sources_path.read_text(encoding="utf-8"))
        probes = {
            source["source_id"]: {
                "checked_at": source["probe_checked_at"],
                "catalog": source["catalog_probe"],
                "label": source["label_probe"],
            }
            for source in previous.get("sources", [])
            if source.get("catalog_probe") is not None
        }

    radiation = build_radiation_evidence(
        sites,
        window_start=args.start,
        window_end=args.end,
        probes=probes,
    )
    write_json(radiation_sources_path, radiation)
    radiation_sources_sha256 = file_sha256(radiation_sources_path)

    payloads = build_payloads(
        series,
        records,
        window_start=args.start,
        window_end=args.end,
        cadence_minutes=args.cadence_minutes,
        radiation=radiation,
        radiation_sources_sha256=radiation_sources_sha256,
    )
    observations_sha256 = write_jsonl(output / "observations.jsonl", payloads)

    requests = build_requests(
        series,
        window_start=args.start,
        window_end=args.end,
        cadence_minutes=args.cadence_minutes,
    )
    requests_sha256 = write_jsonl(output / "requests.jsonl", requests)

    horizon = build_horizon(
        series,
        window_start=args.start,
        window_end=args.end,
        cadence_minutes=args.cadence_minutes,
    )
    write_json(output / "horizon.json", horizon)

    plan = build_plan(
        sites,
        window_start=args.start,
        window_end=args.end,
        cadence_minutes=args.cadence_minutes,
        sample_count=len(epochs),
        half_extent_m=args.half_bbox_m,
    )
    write_json(output / "plan.json", plan.model_dump(mode="json"))

    kernel_assets = []
    for spec in KERNEL_SPECS:
        path = cache_dir / spec.filename
        kernel_assets.append(
            {
                "asset_id": spec.asset_id,
                "dataset_id": SPICE_DATASET_ID,
                "uri": spec.url,
                "checksum_algorithm": "sha256",
                "checksum": spec.sha256,
                "checksum_scope": "source",
                "bytes": spec.bytes,
                "fetched_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                "redistribution_allowed": "unknown",
                "purpose": spec.purpose,
                "local_cache_path": repo_relative(path),
                "source": "NASA JPL NAIF generic kernels",
            }
        )
    assets = kernel_assets + radiation_assets(radiation)
    write_json(output / "assets.json", assets)

    input_manifests = [
        {
            "path": selection.manifest_path,
            "sha256": file_sha256(ROOT / selection.manifest_path),
            "reference_id": selection.reference_id,
            "body": selection.body,
        }
        for selection in PILOT_AOIS
    ]
    generated_at = datetime.now(UTC).isoformat()
    hashes = {
        "observations_sha256": observations_sha256,
        "requests_sha256": requests_sha256,
        "horizon_sha256": file_sha256(output / "horizon.json"),
        "radiation_sources_sha256": radiation_sources_sha256,
        "plan_sha256": file_sha256(output / "plan.json"),
        "assets_sha256": file_sha256(output / "assets.json"),
    }
    manifest = build_manifest(
        cache_dir=cache_dir,
        hashes=hashes,
        series=series,
        frames=frames,
        payloads=payloads,
        radiation=radiation,
        window_start=args.start,
        window_end=args.end,
        cadence_minutes=args.cadence_minutes,
        input_manifests=input_manifests,
        generated_at=generated_at,
    )
    write_json(output / "manifest.json", manifest)
    write_readme(output, manifest)
    write_report(output, manifest, series)
    write_doc(doc, manifest, series, radiation)

    print(
        json.dumps(
            {
                "output": repo_relative(output),
                "doc": repo_relative(doc),
                "observations": manifest["counts"]["observations"],
                "illumination_observations": manifest["counts"]["illumination_observations"],
                "radiation_evidence_observations": manifest["counts"][
                    "radiation_evidence_observations"
                ],
                "aois": manifest["counts"]["aois"],
                "by_state": manifest["counts"]["by_state"],
                "radiation_coverage": {
                    area["area_id"]: area["radiation_coverage_status"] for area in manifest["aois"]
                },
                "illuminated_fraction": {
                    area["area_id"]: area["illuminated_sample_fraction"]
                    for area in manifest["aois"]
                },
                "hashes": hashes,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
