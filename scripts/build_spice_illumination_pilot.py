#!/usr/bin/env python
"""Build a bounded, model-derived NAIF SPICE solar-geometry pilot (Issue #45).

The pilot uses only the existing Moon/Mars reference coordinates. It computes Sun
incidence and elevation at one documented UTC instant using pinned public NAIF
kernels cached under ``var/cache``. Kernels are never committed and no DEM is
downloaded. Output is a typed-v2 observation export plus a checksummed manifest.

This is solar geometry, not observed irradiance, not a shadow-fraction product and
not ionizing radiation.
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
from analog_pipelines.solar_geometry import (
    BODY_FRAME,
    KERNEL_SPECS,
    SPICE_DATASET_ID,
    SPICE_METHOD_VERSION,
    KernelIntegrityError,
    SolarGeometryError,
    SpiceGeometryRecord,
    SpiceSolarGeometryProvider,
    body_kernel_asset_ids,
    ensure_kernels,
    spice_available,
)
from analog_pipelines.typed_contracts import (
    DepthSupport,
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
    Uncertainty,
)
from pyproj import CRS, Transformer

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = "var/cache/spice-45"
DEFAULT_OUTPUT = "pipelines/pilots/d05_spice_illumination"
DEFAULT_DOC = "docs/D05_SPICE_PILOT.md"
DEFAULT_EPOCH = "2026-11-15T12:00:00Z"
DEFAULT_MOON_MANIFEST = "pipelines/planetary/manifests/lola_moon_reference.json"
DEFAULT_MARS_MANIFEST = "pipelines/planetary/manifests/ctx_mars_references.json"
RELEASE_VERSION = "spice-geometry-pilot-1.0.0"
FEATURES = ("solar_incidence_deg", "solar_elevation_deg")


@dataclass(frozen=True, slots=True)
class ReferenceSite:
    reference_id: str
    display_name: str
    body: str
    latitude_deg: float
    longitude_deg: float
    dataset_id: str
    source_crs: str
    source_manifest: str


def repo_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_reference_sites(path: Path, body: str) -> list[ReferenceSite]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    datasets = {item["dataset_id"]: item for item in raw["datasets"]}
    sites: list[ReferenceSite] = []
    for reference in raw["references"]:
        if reference.get("body") != body:
            continue
        dataset = datasets.get(reference["dataset_id"])
        if dataset is None:
            raise SolarGeometryError(
                f"{reference['reference_id']} references unknown dataset {reference['dataset_id']}"
            )
        sites.append(
            ReferenceSite(
                reference_id=reference["reference_id"],
                display_name=reference.get("display_name") or reference["reference_id"],
                body=body,
                latitude_deg=float(reference["lat"]),
                longitude_deg=float(reference["lon"]),
                dataset_id=reference["dataset_id"],
                source_crs=dataset["source_crs"],
                source_manifest=repo_relative(path),
            )
        )
    if not sites:
        raise SolarGeometryError(f"no {body} reference sites found in {path}")
    return sites


def projected_bbox(
    crs_wkt: str, latitude_deg: float, longitude_deg: float, *, half_extent_m: float
) -> dict[str, float]:
    """Project the point into its source map CRS and build a small area envelope.

    The envelope is only a database area holder; solar geometry is computed at the
    point centre, never averaged over the envelope or over the source DEM window.
    """
    if half_extent_m <= 0:
        raise SolarGeometryError("half_extent_m must be positive")
    crs = CRS.from_user_input(crs_wkt)
    if not crs.is_projected:
        raise SolarGeometryError("reference source CRS must be projected")
    if crs.geodetic_crs is None:
        raise SolarGeometryError("reference source CRS has no geographic base")
    transformer = Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True)
    x, y = transformer.transform(longitude_deg, latitude_deg)
    if not all(math.isfinite(value) for value in (x, y)):
        raise SolarGeometryError("projected site coordinate is not finite")
    return {
        "west": float(x - half_extent_m),
        "south": float(y - half_extent_m),
        "east": float(x + half_extent_m),
        "north": float(y + half_extent_m),
    }


def build_plan(
    sites: list[ReferenceSite],
    *,
    epoch_utc: str,
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
            coordinate_frame=("lunar_projected" if site.body == "Moon" else "mars_projected"),
            notes=(
                f"{site.body} point site ({site.latitude_deg:.8f} deg, "
                f"{site.longitude_deg:.8f} deg, east-positive), body-fixed frame "
                f"{BODY_FRAME[site.body]}; source dataset {site.dataset_id}. "
                "The projected bbox is a 2 km database envelope, not an analysis "
                "footprint; solar geometry is computed at the centre point."
            ),
        )
        tasks.append(
            AcquisitionTask(
                task_id=f"spice-solar-geometry-{site.reference_id}",
                provider_id="naif-spice",
                source_id=SPICE_DATASET_ID,
                body=site.body,  # type: ignore[arg-type]
                dimension="D05",
                area=area,
                endpoint="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/",
                parameters={
                    "epoch_utc": epoch_utc,
                    "body_fixed_frame": BODY_FRAME[site.body],
                    "method_version": SPICE_METHOD_VERSION,
                },
                method_version=SPICE_METHOD_VERSION,
                dataset_id=SPICE_DATASET_ID,
            )
        )
    return AcquisitionPlan(
        scope="pilot-d05-spice-illumination",
        generated_at=epoch_utc,
        tasks=tasks,
        totals={
            "tasks": len(tasks),
            "sites": len(sites),
            "bodies": dict(sorted(Counter(site.body for site in sites).items())),
            "epoch_utc": epoch_utc,
            "method_version": SPICE_METHOD_VERSION,
        },
        notes=[
            "Bounded pilot at existing Moon/Mars reference coordinates only.",
            "Model-derived solar geometry; not observed irradiance or radiation.",
            "No kernel bytes and no DEM are committed.",
        ],
    )


def _observation_id(site: ReferenceSite, feature_id: str, epoch_utc: str) -> str:
    compact = epoch_utc.replace("-", "").replace(":", "")
    return f"spice:{site.reference_id}:{feature_id}:{compact}"


def observation_payloads(
    sites: list[ReferenceSite],
    records: dict[str, SpiceGeometryRecord],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for site in sites:
        record = records[site.reference_id]
        geometry = record.geometry
        values = {
            "solar_incidence_deg": geometry.incidence_deg,
            "solar_elevation_deg": geometry.elevation_deg,
        }
        source_asset_ids = body_kernel_asset_ids(site.body)
        quality_flags = [
            "model_derived_solar_geometry",
            "not_observed_irradiance",
            "not_shadow_fraction",
            "not_ionizing_radiation",
            "flat_ellipsoid_tangent_plane",
            "no_terrain_horizon",
            "no_atmosphere_or_cloud_model",
            "point_site_not_window_average",
            f"body_fixed_frame_{record.frame.lower()}",
            "planetocentric_latitude_longitude",
            "kernel_checksums_pinned",
            "de421_ephemeris",
        ]
        if abs(site.latitude_deg) > 85.0:
            quality_flags.append("polar_longitude_ill_conditioned")
        provenance = {
            "reference_manifest": site.source_manifest,
            "reference_id": site.reference_id,
            "dataset_id": site.dataset_id,
            "source_crs_reference": (
                f"plan.json area {site.reference_id} carries the source projected CRS"
            ),
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
            "kernel_asset_ids": source_asset_ids,
        }
        for feature_id in FEATURES:
            observation = TypedObservation(
                observation_id=_observation_id(site, feature_id, record.epoch_utc),
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
                        "SPICE ephemeris/frame model; no per-site measurement "
                        "uncertainty is supplied. Flat ellipsoid tangent plane; "
                        "no horizon raster or atmosphere model."
                    ),
                ),
                spatial_support=SpatialSupport(
                    kind="point",
                    crs=record.frame,
                    notes=(
                        f"Body-fixed point ({record.site_latitude_deg:.8f} deg, "
                        f"{record.site_longitude_deg:.8f} deg, east-positive) in "
                        f"{record.frame}; site radius {record.site_radius_m:.3f} m. "
                        "No window aggregation."
                    ),
                ),
                temporal_support=TemporalSupport(
                    kind="instant",
                    start=record.epoch_utc,
                    local_solar_time=f"{geometry.local_solar_time_hours:05.2f} h",
                    notes=(
                        "One documented UTC instant; local solar time is derived "
                        "from subsolar longitude and is ill-conditioned near a pole."
                    ),
                ),
                depth_support=DepthSupport(kind="surface"),
                method_version=SPICE_METHOD_VERSION,
                dataset_ids=[SPICE_DATASET_ID],
                source_asset_ids=source_asset_ids,
                quality_flags=quality_flags,
                score_role="context_only",
                legacy_provenance=provenance,
                notes=(
                    f"Model-derived solar geometry only (SPICE, "
                    f"{SPICE_METHOD_VERSION}) at {record.epoch_utc} in "
                    f"{record.frame}. Flat ellipsoid tangent plane; no terrain "
                    "horizon, shadow fraction, irradiance, radiance, or ionizing "
                    "radiation."
                ),
            )
            payloads.append(observation.model_dump(mode="json"))
    return payloads


def write_observations(path: Path, payloads: list[dict[str, Any]]) -> str:
    text = "\n".join(json.dumps(payload, sort_keys=True) for payload in payloads) + "\n"
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_assets(cache_dir: Path) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for spec in KERNEL_SPECS:
        path = cache_dir / spec.filename
        fetched_at = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        assets.append(
            {
                "asset_id": spec.asset_id,
                "dataset_id": SPICE_DATASET_ID,
                "uri": spec.url,
                "checksum_algorithm": "sha256",
                "checksum": spec.sha256,
                "checksum_scope": "source",
                "bytes": spec.bytes,
                "fetched_at": fetched_at,
                "redistribution_allowed": "unknown",
                "purpose": spec.purpose,
                "local_cache_path": repo_relative(path),
                "source": "NASA JPL NAIF generic kernels",
            }
        )
    return assets


def _site_rows(
    sites: list[ReferenceSite], records: dict[str, SpiceGeometryRecord]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for site in sites:
        record = records[site.reference_id]
        geometry = record.geometry
        rows.append(
            {
                "area_id": site.reference_id,
                "body": site.body,
                "display_name": site.display_name,
                "frame": record.frame,
                "latitude_deg": record.site_latitude_deg,
                "longitude_deg": record.site_longitude_deg,
                "site_radius_m": record.site_radius_m,
                "incidence_deg": geometry.incidence_deg,
                "elevation_deg": geometry.elevation_deg,
                "local_solar_time_hours": geometry.local_solar_time_hours,
                "sun_distance_m": geometry.sun_distance_m,
                "subsolar_latitude_deg": geometry.subsolar_latitude_deg,
                "subsolar_longitude_deg": geometry.subsolar_longitude_deg,
            }
        )
    return rows


def _counts(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "observations": len(payloads),
        "areas": len({payload["area_id"] for payload in payloads}),
        "by_body": dict(sorted(Counter(payload["body"] for payload in payloads).items())),
        "by_feature": dict(sorted(Counter(payload["feature_id"] for payload in payloads).items())),
        "by_state": dict(sorted(Counter(payload["state"] for payload in payloads).items())),
        "by_score_role": dict(
            sorted(Counter(payload["score_role"] for payload in payloads).items())
        ),
    }


def build_manifest(
    *,
    epoch_utc: str,
    sites: list[ReferenceSite],
    records: dict[str, SpiceGeometryRecord],
    payloads: list[dict[str, Any]],
    observations_sha256: str,
    plan_sha256: str,
    assets_sha256: str,
    cache_dir: Path,
    frames: dict[str, dict[str, Any]],
    input_manifests: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    counts = _counts(payloads)
    counts.update(
        {
            "sites_attempted": len(sites),
            "sites_succeeded": len(records),
            "sites_failed": len(sites) - len(records),
            "no_coverage": 0,
        }
    )
    limitations = [
        "Model-derived geometry only: not observed irradiance, radiance, shadow "
        "fraction, or ionizing radiation.",
        "Flat biaxial-ellipsoid tangent-plane normal; no DEM terrain horizon, "
        "horizon mask, observer height, or atmospheric/cloud model.",
        "The site is placed on the reference biaxial ellipsoid; source DEM "
        "elevations and observer heights are not added.",
        "Sun position is the light-time-corrected body-centre vector from SPICE; "
        "site parallax is applied by vector subtraction.",
        "Point sites at existing reference coordinates; not a spatial average over "
        "the existing 1.98 km DEM windows.",
        "Moon uses the MOON_ME body-fixed frame realized from DE421; Mars uses "
        "IAU_MARS from pck00011. Source projected-CRS coordinates are interpreted "
        "in those frames; prime-meridian/frame registration is a documented "
        "assumption, not an independent geodetic validation.",
        "One documented instant only; no time series, eclipse/occultation, or "
        "local-solar-time series is produced.",
        "DE421 covers approximately 1900-2050; kernel SHA-256 digests are pinned "
        "and any drift is rejected by the builder.",
        "No cross-body D05 score; illumination and ionizing radiation remain "
        "separate subgroups. No Gy/Sv value is produced.",
        "Kernels are cached under a gitignored var/cache directory and are not committed to Git.",
    ]
    return {
        "release_version": RELEASE_VERSION,
        "generated_at": generated_at,
        "scope": "pilot-d05-spice-illumination",
        "data_kind": "model_derived",
        "method_version": SPICE_METHOD_VERSION,
        "epoch_utc": epoch_utc,
        "et_seconds": next(iter(records.values())).et_seconds,
        "spice_version": next(iter(records.values())).spice_version,
        "frames": frames,
        "kernel_count": len(KERNEL_SPECS),
        "kernel_total_bytes": sum(spec.bytes for spec in KERNEL_SPECS),
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
        "input_manifests": input_manifests,
        "observations_sha256": observations_sha256,
        "plan_sha256": plan_sha256,
        "assets_sha256": assets_sha256,
        "counts": counts,
        "sites": _site_rows(sites, records),
        "limitations": limitations,
        "non_claims": [
            "No observed solar irradiance or radiance.",
            "No shadow fraction, shadow mask, or horizon visibility interval.",
            "No ionizing radiation, absorbed dose (Gy), or equivalent dose (Sv).",
            "No global or exhaustive Moon/Mars illumination coverage.",
            "No cross-body or Earth/Moon/Mars D05 similarity score.",
        ],
        "reproduce_commands": [
            "uv sync --extra spice",
            ("uv run --extra spice python scripts/build_spice_illumination_pilot.py --download"),
            ("uv run --extra spice python scripts/build_spice_illumination_pilot.py"),
            "uv run --extra spice pytest pipelines/tests/test_solar_geometry.py -q",
            ("uv run --extra spice pytest apps/api/tests/test_typed_api.py -q -k d05_spice"),
        ],
        "notes": [
            "Bounded pilot at existing Moon/Mars terrain reference coordinates only.",
            "The values are a solar-geometry model, not a measurement of surface illumination.",
            "Kernel URLs, SHA-256 digests, frame names, epoch, site coordinates, and "
            "limitations are preserved in this manifest.",
            "Kernels are public NAIF products; redistribution of the kernel bytes is "
            "not re-asserted here.",
        ],
    }


def _markdown_site_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Body | Area | Frame | Latitude (deg) | Longitude (deg) | Incidence (deg) | Elevation (deg) | Local solar time |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['body']} | `{row['area_id']}` | `{row['frame']}` | "
            f"{row['latitude_deg']:.6f} | {row['longitude_deg']:.6f} | "
            f"{row['incidence_deg']:.6f} | {row['elevation_deg']:.6f} | "
            f"{row['local_solar_time_hours']:.2f} h |"
        )
    return "\n".join(lines)


def _markdown_kernel_table(manifest: dict[str, Any]) -> str:
    lines = [
        "| Kernel | SHA-256 | Bytes | Purpose |",
        "|---|---|---:|---|",
    ]
    for kernel in manifest["kernels"]:
        lines.append(
            f"| [`{kernel['filename']}`]({kernel['url']}) | "
            f"`{kernel['sha256']}` | {kernel['bytes']} | {kernel['purpose']} |"
        )
    return "\n".join(lines)


def write_readme(output: Path, manifest: dict[str, Any]) -> None:
    counts = manifest["counts"]
    text = f"""# D05 Moon/Mars — NAIF SPICE solar-geometry pilot

This directory is a portable, checksummed, **model-derived** pilot. It is not an
observation of surface irradiance, a shadow-fraction product, or a radiation dose.

## Actual content

| Item | Value |
|---|---:|
| Sites attempted | {counts["sites_attempted"]} |
| Sites succeeded | {counts["sites_succeeded"]} |
| Sites failed | {counts["sites_failed"]} |
| Areas | {counts["areas"]} |
| Observations | {counts["observations"]} |
| Bodies | {", ".join(f"{body} ({count})" for body, count in counts["by_body"].items())} |
| Features | {", ".join(f"`{feature}` ({count})" for feature, count in counts["by_feature"].items())} |
| States | {", ".join(f"`{state}` ({count})" for state, count in counts["by_state"].items())} |
| Score role | {", ".join(f"`{role}` ({count})" for role, count in counts["by_score_role"].items())} |

## Scientific limits

- The values are SPICE solar incidence/elevation angles at one documented instant
  (`{manifest["epoch_utc"]}`). They are **not** observed irradiance.
- A flat biaxial-ellipsoid tangent plane is used. There is no DEM terrain horizon,
  horizon mask, observer-height model, shadow fraction, or atmosphere/cloud model.
- Moon uses `MOON_ME` from DE421; Mars uses `IAU_MARS` from `pck00011`. Source
  projected-CRS coordinates are interpreted in those frames; registration is a
  documented assumption, not an independent geodetic validation.
- The pilot is bounded to the existing Moon/Mars reference coordinates. It is not
  global coverage and does not change existing terrain observations or scores.
- Illumination and ionizing radiation remain separate. No `Gy`/`Sv` value is
  produced.

## Files

- `plan.json` — area/task envelope for the existing reference sites.
- `observations.jsonl` — typed-v2 solar-geometry observations.
- `assets.json` — public kernel URLs and SHA-256 provenance; kernels are **not**
  committed and live under the gitignored `var/cache/spice-45` cache.
- `manifest.json` — epoch, frames, kernel checksums, counts and limitations.
- `REPORT.md` — compact site results.

## Reproduce

```bash
uv sync --extra spice
uv run --extra spice python scripts/build_spice_illumination_pilot.py --download
uv run --extra spice python scripts/build_spice_illumination_pilot.py
```

The first command downloads only the five pinned public SPICE kernels (about
18 MB total). The second run is offline and verifies the pinned digests. Full
details and computed results are in `docs/D05_SPICE_PILOT.md`.
"""
    (output / "README.md").write_text(text, encoding="utf-8")


def write_report(output: Path, manifest: dict[str, Any]) -> None:
    counts = manifest["counts"]
    text = f"""# D05 SPICE solar-geometry pilot — report

- Release: `{manifest["release_version"]}`
- Method: `{manifest["method_version"]}`
- Epoch: `{manifest["epoch_utc"]}`
- SPICE: `{manifest["spice_version"]}`
- Sites attempted/succeeded/failed: {counts["sites_attempted"]}/{counts["sites_succeeded"]}/{counts["sites_failed"]}
- Observations: {counts["observations"]}
- Areas: {counts["areas"]}
- By body: {counts["by_body"]}
- By feature: {counts["by_feature"]}
- By state: {counts["by_state"]}

{_markdown_site_table(manifest["sites"])}

All values are model-derived solar geometry. No observed irradiance, shadow
fraction or ionizing radiation is present.
"""
    (output / "REPORT.md").write_text(text, encoding="utf-8")


def write_doc(path: Path, manifest: dict[str, Any]) -> None:
    counts = manifest["counts"]
    frame_lines = [
        f"| {body} | `{info['frame']}` | {info['frame_id']} | {info['validation']} |"
        for body, info in manifest["frames"].items()
    ]
    input_lines = [
        f"| `{item['path']}` | `{item['sha256']}` | {item['references']} |"
        for item in manifest["input_manifests"]
    ]
    text = f"""# D05 NAIF SPICE solar-geometry pilot

Status: **model-derived context/evidence pilot, not an observation of surface
irradiance**. Issue #45 separates solar illumination from ionizing radiation; this
pilot addresses only the solar-geometry part for existing Moon and Mars reference
coordinates.

## What was computed

For each existing Moon/Mars reference point, the pilot computes:

- Sun **incidence angle** in degrees: angle between the local outward ellipsoid
  normal and the site-to-Sun vector.
- Sun **elevation angle** in degrees: `90 - incidence`; a negative value means the
  Sun is below the local flat tangent plane.
- Local solar time from the subsolar longitude, for provenance only.

The computation uses NASA/JPL NAIF SPICE with the pinned DE421 ephemeris and
body-fixed frames:

| Body | Frame | Frame ID | Validation |
|---|---|---:|---|
{chr(10).join(frame_lines)}

The documented epoch is `{manifest["epoch_utc"]}` (SPICE ET
`{manifest["et_seconds"]:.6f} s`). The pilot is a single instant, not a time series.

## Results

{_markdown_site_table(manifest["sites"])}

Counts: **{counts["sites_succeeded"]}/{counts["sites_attempted"]} sites succeeded**
(**{counts["sites_failed"]} failed**, 0 no-coverage), **{counts["observations"]}
observations**, **{counts["areas"]} areas**, by body `{counts["by_body"]}`, by
feature `{counts["by_feature"]}`, by state `{counts["by_state"]}`. All observations
are `score_role = context_only` and `method_version = {manifest["method_version"]}`.

## Kernel provenance

Kernels are downloaded into the gitignored `var/cache/spice-45` cache and are
**never committed**. The builder verifies size and SHA-256 on every run.

{_markdown_kernel_table(manifest)}

## Input manifests

| Manifest | SHA-256 | Reference count |
|---|---|---:|
{chr(10).join(input_lines)}

## Exact commands

```bash
uv sync --extra spice
uv run --extra spice python scripts/build_spice_illumination_pilot.py --download
uv run --extra spice python scripts/build_spice_illumination_pilot.py
uv run --extra spice pytest pipelines/tests/test_solar_geometry.py -q
uv run --extra spice pytest apps/api/tests/test_typed_api.py -q -k d05_spice
uv run ruff check .
uv run ruff format --check .
```

The first builder invocation fetches only the five pinned public kernels. The
second is offline and re-verifies the cache. When `spiceypy` is unavailable, the
builder exits with install instructions; the committed pilot remains readable and
the pure-geometry tests do not require SPICE.

## What this is not

- No observed solar irradiance or radiance.
- No shadow fraction, shadow mask, terrain horizon, or horizon visibility interval.
- No ionizing radiation, absorbed dose (Gy), or equivalent dose (Sv).
- No atmospheric/cloud correction, no observer height, no DEM horizon raster.
- No global or exhaustive Moon/Mars coverage; only the existing reference points.
- No cross-body or Earth/Moon/Mars D05 similarity score.

## Remaining scientific limits / blockers

{chr(10).join(f"- {item}" for item in manifest["limitations"])}

## Follow-up work

1. Replace the flat tangent plane with a documented DEM horizon/shadow model before
   claiming any shadow fraction or visibility interval.
2. Validate the projected source-CRS-to-body-fixed-frame registration independently
   (especially the lunar prime-meridian and Mars 2000/IAU_MARS assumptions).
3. Add a bounded time series only when the underlying time support and local solar
   time are meaningful; do not infer illumination from dark pixels.
4. Keep ionizing-radiation detectors and dose definitions in a separate D05
   subgroup; no flux/dose-to-Sv conversion without physics.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--doc", default=DEFAULT_DOC)
    parser.add_argument("--epoch", default=DEFAULT_EPOCH)
    parser.add_argument("--moon-manifest", default=DEFAULT_MOON_MANIFEST)
    parser.add_argument("--mars-manifest", default=DEFAULT_MARS_MANIFEST)
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
    moon_manifest = Path(args.moon_manifest)
    if not moon_manifest.is_absolute():
        moon_manifest = ROOT / moon_manifest
    mars_manifest = Path(args.mars_manifest)
    if not mars_manifest.is_absolute():
        mars_manifest = ROOT / mars_manifest

    try:
        kernels = ensure_kernels(cache_dir, download=args.download)
        sites = load_reference_sites(moon_manifest, "Moon") + load_reference_sites(
            mars_manifest, "Mars"
        )
        with SpiceSolarGeometryProvider(kernels) as provider:
            records = {
                site.reference_id: provider.compute_site(
                    body=site.body,
                    latitude_deg=site.latitude_deg,
                    longitude_deg=site.longitude_deg,
                    epoch_utc=args.epoch,
                )
                for site in sites
            }
            frames = {}
            for body in ("Moon", "Mars"):
                body_records = [record for record in records.values() if record.body == body]
                if not body_records:
                    continue
                frame = BODY_FRAME[body]
                frames[body] = {
                    "frame": frame,
                    "frame_id": provider.frame_id(frame),
                    "validation": body_records[0].frame_validation,
                    "epoch_utc": args.epoch,
                }
    except (SolarGeometryError, KernelIntegrityError, OSError, KeyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    payloads = observation_payloads(sites, records)
    output.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    observations_sha256 = write_observations(output / "observations.jsonl", payloads)
    plan = build_plan(sites, epoch_utc=args.epoch, half_extent_m=args.half_bbox_m)
    write_json(output / "plan.json", plan.model_dump(mode="json"))
    assets = build_assets(cache_dir)
    write_json(output / "assets.json", assets)
    input_manifests = [
        {
            "path": repo_relative(path),
            "sha256": file_sha256(path),
            "references": len(load_reference_sites(path, body)),
        }
        for path, body in ((moon_manifest, "Moon"), (mars_manifest, "Mars"))
    ]
    manifest = build_manifest(
        epoch_utc=args.epoch,
        sites=sites,
        records=records,
        payloads=payloads,
        observations_sha256=observations_sha256,
        plan_sha256=file_sha256(output / "plan.json"),
        assets_sha256=file_sha256(output / "assets.json"),
        cache_dir=cache_dir,
        frames=frames,
        input_manifests=input_manifests,
        generated_at=generated_at,
    )
    write_json(output / "manifest.json", manifest)
    write_readme(output, manifest)
    write_report(output, manifest)
    write_doc(doc, manifest)
    print(
        json.dumps(
            {
                "output": repo_relative(output),
                "doc": repo_relative(doc),
                "observations": manifest["counts"]["observations"],
                "areas": manifest["counts"]["areas"],
                "by_body": manifest["counts"]["by_body"],
                "observations_sha256": observations_sha256,
                "kernel_count": manifest["kernel_count"],
                "kernel_total_bytes": manifest["kernel_total_bytes"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
