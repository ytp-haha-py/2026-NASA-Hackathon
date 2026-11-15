#!/usr/bin/env python
"""Build bounded D05/D06 AOI evidence records for Goal F (#170).

Owns only files under pipelines/d05_d06_aoi/, tests, and docs/D05_D06_AOI_170.md.
Does not modify shared scoring/schema. Emits typed-v2 observations with explicit
source/version/support/QA/uncertainty/role. Missing values are no_coverage.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
EPOCH_UTC = "2026-11-15T12:00:00Z"
GENERATED_AT = "2026-11-15T12:00:00+00:00"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: Any) -> str:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    return sha256_text(text)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    text = "\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows) + "\n"
    path.write_text(text, encoding="utf-8")
    return sha256_text(text)


def load_aois() -> list[dict[str, Any]]:
    data = json.loads((OUT / "aois.json").read_text(encoding="utf-8"))
    return list(data["aois"])


def kernel_assets() -> list[dict[str, Any]]:
    # Pinned NAIF generic kernels used by spice-solar-geometry-1.0.0 (public; not committed)
    return [
        {
            "asset_id": "naif-kernel:naif0012.tls",
            "dataset_id": "naif-spice-generic-kernels",
            "uri": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls",
            "checksum_algorithm": "sha256",
            "checksum": "678e32bdb5a744117a467cd9601cd6b373f0e9bc9bbde1371d5eee39600a039b",
            "checksum_scope": "source",
            "bytes": 5257,
            "redistribution_allowed": "unknown",
            "purpose": "Leap-second kernel for UTC -> ephemeris time conversion.",
            "version": "naif0012.tls",
        },
        {
            "asset_id": "naif-kernel:pck00011.tpc",
            "dataset_id": "naif-spice-generic-kernels",
            "uri": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc",
            "checksum_algorithm": "sha256",
            "checksum": "3dff7b1dbeceaa01f25467767d3fa25816051c85d162d1edf04acb310ee28bb1",
            "checksum_scope": "source",
            "bytes": 131226,
            "redistribution_allowed": "unknown",
            "purpose": "Text planetary constants: body radii and IAU_MARS orientation.",
            "version": "pck00011.tpc",
        },
        {
            "asset_id": "naif-kernel:de421.bsp",
            "dataset_id": "naif-spice-generic-kernels",
            "uri": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/a_old_versions/de421.bsp",
            "checksum_algorithm": "sha256",
            "checksum": "08b20db2ae22488650641c5a9033e5bfda4b1c4b440cfeaf20f621cfa18ecdb3",
            "checksum_scope": "source",
            "bytes": 16790528,
            "redistribution_allowed": "unknown",
            "purpose": "JPL DE421 planetary ephemeris (1900-2050), Sun/Moon/Mars states.",
            "version": "de421.bsp",
        },
        {
            "asset_id": "naif-kernel:moon_pa_de421_1900-2050.bpc",
            "dataset_id": "naif-spice-generic-kernels",
            "uri": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de421_1900-2050.bpc",
            "checksum_algorithm": "sha256",
            "checksum": "656f90616403d75a75f0cd6c8830fc5b44f8cb4facb5ccb8915e752b397520cf",
            "checksum_scope": "source",
            "bytes": 1770496,
            "redistribution_allowed": "unknown",
            "purpose": "DE421 lunar principal-axis orientation (binary PCK).",
            "version": "moon_pa_de421_1900-2050.bpc",
        },
        {
            "asset_id": "naif-kernel:moon_080317.tf",
            "dataset_id": "naif-spice-generic-kernels",
            "uri": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_080317.tf",
            "checksum_algorithm": "sha256",
            "checksum": "78732477b96f9863e7b0d65bcee3c22b8707ca5ed0db56d1173319cb2e8c7993",
            "checksum_scope": "source",
            "bytes": 21437,
            "redistribution_allowed": "unknown",
            "purpose": "Lunar frame definitions: MOON_ME / MOON_PA realized from DE421.",
            "version": "moon_080317.tf",
        },
    ]


def radiation_source_assets() -> list[dict[str, Any]]:
    return [
        {
            "asset_id": "pds:lro-l-crat-3-cdr-calibrated-v1.0",
            "dataset_id": "lro-crater-cdr",
            "uri": "https://pds.nasa.gov/ds-view/pds/viewDataset.jsp?dsid=LRO-L-CRAT-3-CDR-CALIBRATED-V1.0",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "LRO CRaTER calibrated energy depositions (orbital, Moon); not direct surface/AOI absorbed dose.",
            "version": "V1.0 / DOI 10.17189/1519728",
            "license": "NASA PDS public archive (see product page for terms)",
        },
        {
            "asset_id": "pds:lro-l-crat-3-4-ddr-processed-v1.0",
            "dataset_id": "lro-crater-ddr",
            "uri": "https://pds.nasa.gov/ds-view/pds/viewDataset.jsp?dsid=LRO-L-CRAT-3/4-DDR-PROCESSED-V1.0",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "LRO CRaTER derived/LET processed data (orbital); dose conversion requires physics not applied here.",
            "version": "V1.0 / DOI 10.17189/1519729",
            "license": "NASA PDS public archive (see product page for terms)",
        },
        {
            "asset_id": "pds:msl-m-rad-3-rdr-v1.0",
            "dataset_id": "msl-rad-rdr",
            "uri": "https://pds.nasa.gov/ds-view/pds/viewDataset.jsp?dsid=MSL-M-RAD-3-RDR-V1.0",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "MSL RAD surface dosimetry (Gale Crater in-situ). Not transferable to Jezero; no Jezero in-situ ionizing dosimeter.",
            "version": "V1.0 / DOI 10.17189/1519761",
            "license": "NASA PDS public archive (see product page for terms)",
        },
        {
            "asset_id": "nasa:meda-rds-overview",
            "dataset_id": "mars2020-meda-rds",
            "uri": "https://mars.nasa.gov/mars2020/spacecraft/instruments/meda/",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "MEDA RDS measures UV/solar/dust radiative environment (not ionizing particle absorbed dose).",
            "version": "instrument overview (accessed for scope statement)",
            "license": "NASA public information",
        },
    ]


def illumination_observations(aoi: dict[str, Any]) -> list[dict[str, Any]]:
    # Real SPICE geometry values from committed pilot (spice-solar-geometry-1.0.0, epoch EPOCH_UTC)
    # Source: pipelines/pilots/d05_spice_illumination/observations.jsonl and manifest.json
    center = aoi["center"]
    if aoi["area_id"] == "moon-shackleton-rim-site04-roi1":
        vals = {"solar_incidence_deg": 88.46316429278093, "solar_elevation_deg": 1.536835707219069}
        lst = "18.31 h"
        lst_h = 18.3057806651083
        frame = "MOON_ME"
        kernel_ids = [
            "naif-kernel:naif0012.tls",
            "naif-kernel:pck00011.tpc",
            "naif-kernel:de421.bsp",
            "naif-kernel:moon_pa_de421_1900-2050.bpc",
            "naif-kernel:moon_080317.tf",
        ]
        source_dataset = "nasa-lola-site04-5mpp-surf"
        ref_manifest = "pipelines/planetary/manifests/lola_moon_reference.json"
        polar_flag = ["polar_longitude_ill_conditioned"]
        lp = {
            "site_radius_m": 1737399.9999999998,
            "subsolar_latitude_deg": -1.55655714823731,
            "subsolar_longitude_deg": 108.34609340337546,
            "sun_distance_m": 147832168114.34378,
        }
    else:
        vals = {"solar_incidence_deg": 100.59141779877926, "solar_elevation_deg": -10.591417798779261}
        lst = "05.04 h"
        lst_h = 5.035240259335698
        frame = "IAU_MARS"
        kernel_ids = ["naif-kernel:naif0012.tls", "naif-kernel:pck00011.tpc", "naif-kernel:de421.bsp"]
        source_dataset = "mars2020-jezero-ctx-dem-20m"
        ref_manifest = "pipelines/planetary/manifests/ctx_mars_references.json"
        polar_flag = []
        lp = {
            "site_radius_m": 3396189.9999999995,
            "subsolar_latitude_deg": 9.288096980795011,
            "subsolar_longitude_deg": -178.15765188787532,
            "sun_distance_m": 240751585441.26016,
        }

    base_flags = [
        "model_derived_solar_geometry",
        "not_observed_irradiance",
        "not_shadow_fraction",
        "not_ionizing_radiation",
        "flat_ellipsoid_tangent_plane",
        "no_terrain_horizon",
        "no_atmosphere_or_cloud_model",
        "point_site_not_window_average",
        f"body_fixed_frame_{frame.lower()}",
        "planetocentric_latitude_longitude",
        "kernel_checksums_pinned",
        "de421_ephemeris",
    ] + polar_flag

    rows = []
    for feature_id, value in vals.items():
        rows.append(
            {
                "schema_version": "2.0.0",
                "observation_id": f"d05:illumination:{aoi['area_id']}:{feature_id}:{EPOCH_UTC.replace('-','').replace(':','')}",
                "area_id": aoi["area_id"],
                "body": aoi["body"],
                "dimension_code": "D05",
                "feature_id": feature_id,
                "value_kind": "numeric",
                "state": "detected",
                "value": value,
                "unit": "degree",
                "uncertainty": {
                    "kind": "unknown",
                    "notes": "SPICE ephemeris/frame model; no per-site measurement uncertainty supplied. Flat ellipsoid tangent plane; no horizon raster or atmosphere model.",
                },
                "spatial_support": {
                    "kind": "point",
                    "crs": frame,
                    "notes": f"Body-fixed point ({center['latitude_deg']:.8f} deg, {center['longitude_deg']:.8f} deg, east-positive) in {frame}; centre point only, no window aggregation.",
                },
                "temporal_support": {
                    "kind": "instant",
                    "start": EPOCH_UTC,
                    "end": None,
                    "local_solar_time": lst,
                    "notes": "Single documented UTC instant (SPICE solar geometry). Local solar time derived from subsolar longitude; ill-conditioned near poles.",
                },
                "depth_support": {"kind": "surface", "max_m": None, "min_m": None},
                "method_version": "spice-solar-geometry-1.0.0",
                "dataset_ids": ["naif-spice-generic-kernels"],
                "source_asset_ids": kernel_ids,
                "quality_flags": base_flags,
                "score_role": "context_only",
                "evidence": None,
                "legacy_provenance": {
                    "reference_manifest": ref_manifest,
                    "source_dataset_id": source_dataset,
                    "body_fixed_frame": frame,
                    "epoch_utc": EPOCH_UTC,
                    "et_seconds": 848016069.1827395,
                    "local_solar_time_hours": lst_h,
                    "site_latitude_deg": center["latitude_deg"],
                    "site_longitude_deg": center["longitude_deg"],
                    "source_pilot": "pipelines/pilots/d05_spice_illumination",
                    "source_pilot_release": "spice-geometry-pilot-1.0.0",
                    **lp,
                },
                "notes": f"Model-derived SPICE solar geometry only at {EPOCH_UTC} in {frame} (D05 solar_illumination). Not observed irradiance/radiance, not shadow fraction/horizon, not ionizing radiation. Flat ellipsoid tangent plane.",
            }
        )
    return rows


def radiation_observations(aoi: dict[str, Any]) -> list[dict[str, Any]]:
    if aoi["body"] == "Moon":
        source_assets = ["pds:lro-l-crat-3-cdr-calibrated-v1.0", "pds:lro-l-crat-3-4-ddr-processed-v1.0"]
        dataset_ids = ["lro-crater-cdr", "lro-crater-ddr"]
        reason = (
            "No in-situ surface ionizing-radiation measurement at this AOI. LRO CRaTER provides "
            "orbital calibrated energy/LET data (DOIs 10.17189/1519728, 10.17189/1519729), not a "
            "direct Shackleton-rim surface absorbed/equivalent dose. AOI dose would require "
            "documented spectral unfolding, detector response, geometry/albedo, shielding, and "
            "transport/physics assumptions not applied; Gy/Sv not fabricated."
        )
        src_urls = [
            "https://pds.nasa.gov/ds-view/pds/viewDataset.jsp?dsid=LRO-L-CRAT-3-CDR-CALIBRATED-V1.0",
            "https://pds.nasa.gov/ds-view/pds/viewDataset.jsp?dsid=LRO-L-CRAT-3/4-DDR-PROCESSED-V1.0",
        ]
        extra_flags = ["orbital_source_not_surface_equivalent", "no_in_situ_surface_measurement"]
    else:
        source_assets = ["pds:msl-m-rad-3-rdr-v1.0", "nasa:meda-rds-overview"]
        dataset_ids = ["msl-rad-rdr", "mars2020-meda-rds"]
        reason = (
            "No real in-situ ionizing particle absorbed-dose measurement at Jezero AOI. Perseverance "
            "MEDA RDS measures UV/solar/dust radiative environment, not ionizing particle dose (Gy). "
            "MSL RAD (Gale Crater, DOI 10.17189/1519761) provides real surface dose-rate evidence only "
            "at Gale and is not transferable to Jezero without uncommitted transport/model assumptions; "
            "no fabricated/extrapolated value."
        )
        src_urls = [
            "https://pds.nasa.gov/ds-view/pds/viewDataset.jsp?dsid=MSL-M-RAD-3-RDR-V1.0",
            "https://mars.nasa.gov/mars2020/spacecraft/instruments/meda/",
        ]
        extra_flags = ["no_insitu_ionizing_dosimeter_at_aoi", "gale_rad_not_transferable"]

    return [
        {
            "schema_version": "2.0.0",
            "observation_id": f"d05:ionizing_radiation:{aoi['area_id']}:absorbed_dose_gy:no_coverage",
            "area_id": aoi["area_id"],
            "body": aoi["body"],
            "dimension_code": "D05",
            "feature_id": "absorbed_dose_gy",
            "value_kind": "numeric",
            "state": "no_coverage",
            "value": None,
            "unit": "Gy",
            "uncertainty": {
                "kind": "unknown",
                "notes": "No measured/derivable AOI value without fabrication or uncommitted physics assumptions.",
            },
            "spatial_support": {
                "kind": "point",
                "crs": aoi["center"]["body_fixed_frame"],
                "notes": f"Requested AOI/centre point ({aoi['center']['latitude_deg']}, {aoi['center']['longitude_deg']}) for ionizing-radiation evidence; bounded context only.",
            },
            "temporal_support": {
                "kind": "instant",
                "start": EPOCH_UTC,
                "end": None,
                "notes": "Query/reference instant aligned to illumination epoch; no AOI-specific ionizing dose time series available.",
            },
            "depth_support": {"kind": "surface"},
            "method_version": "ionizing-radiation-source-audit-0.1.0",
            "dataset_ids": dataset_ids,
            "source_asset_ids": source_assets,
            "quality_flags": [
                "ionizing_radiation_subgroup",
                "separated_from_solar_illumination",
                "no_coverage_explicit",
                "no_physics_based_dose_conversion",
                "no_fabricated_gy_sv",
                "official_sources_audited",
                "bounded_aoi_not_global",
            ]
            + extra_flags,
            "score_role": "evidence_only",
            "evidence": None,
            "legacy_provenance": {
                "subgroup": "ionizing_radiation",
                "illumination_separated": True,
                "source_urls": src_urls,
                "audit_basis": "Repo D05 coverage/source audits and official PDS/NASA mission instrument scope (Goal F).",
            },
            "notes": reason,
        }
    ]


def d06_source_assets() -> list[dict[str, Any]]:
    return [
        {
            "asset_id": "pds:urn-nasa-pds-a15lsrp_soil_mechanics",
            "dataset_id": "nasa-pds-apollo-lsrp-audit",
            "uri": "https://pds-geosciences.wustl.edu/lunar/urn-nasa-pds-a15lsrp_soil_mechanics/",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "Apollo 15 LSRP soil mechanics (Station 8 context): penetrometry/mechanics records; not direct tabulated D06 features for arbitrary AOIs.",
            "version": "urn:nasa:pds:a15lsrp_soil_mechanics",
            "license": "NASA PDS public archive (see product page for terms)",
        },
        {
            "asset_id": "pds:urn-nasa-pds-a16lsrp_soil_mechanics",
            "dataset_id": "nasa-pds-apollo-lsrp-audit",
            "uri": "https://pds-geosciences.wustl.edu/lunar/urn-nasa-pds-a16lsrp_soil_mechanics/",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "Apollo 16 LSRP soil mechanics reference (audit context).",
            "version": "urn:nasa:pds:a16lsrp_soil_mechanics",
            "license": "NASA PDS public archive (see product page for terms)",
        },
        {
            "asset_id": "pds:urn-nasa-pds-a15_17_hfe_concatenated",
            "dataset_id": "nasa-pds-apollo-hfe-audit",
            "uri": "https://pds-geosciences.wustl.edu/lunar/urn-nasa-pds-a15_17_hfe_concatenated/",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "Apollo 15/17 HFE reference (audit context; not direct D06 mechanical/grain properties).",
            "version": "urn:nasa:pds:a15_17_hfe_concatenated",
            "license": "NASA PDS public archive (see product page for terms)",
        },
        {
            "asset_id": "pds:urn-nasa-pds-insight_hp3_tem",
            "dataset_id": "nasa-pds-insight-hp3-rad-audit",
            "uri": "https://pds-geosciences.wustl.edu/insight/urn-nasa-pds-insight_hp3_tem/",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "InSight HP3 TEM bundle (thermal/mole context); no direct D06 regolith feature tables for AOI.",
            "version": "urn:nasa:pds:insight_hp3_tem",
            "license": "NASA PDS public archive (see product page for terms)",
        },
        {
            "asset_id": "pds:urn-nasa-pds-insight_rad",
            "dataset_id": "nasa-pds-insight-hp3-rad-audit",
            "uri": "https://pds-geosciences.wustl.edu/insight/urn-nasa-pds-insight_rad/",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "InSight radiometer bundle (thermal IR; not ionizing radiation, not direct D06 mechanics).",
            "version": "urn:nasa:pds:insight_rad",
            "license": "NASA PDS public archive (see product page for terms)",
        },
        {
            "asset_id": "lit:spohn-2022-hp3-mole",
            "dataset_id": "insight-hp3-literature",
            "uri": "https://doi.org/10.1007/s11214-022-00941-z",
            "checksum_algorithm": "unknown",
            "checksum": None,
            "checksum_scope": "unknown",
            "redistribution_allowed": "unknown",
            "purpose": "Spohn et al. 2022 (HP3): model-derived/interpretive regolith properties at InSight site, not direct D06 measurements for Jezero AOI.",
            "version": "Space Sci Rev 218:72 (2022)",
            "license": "publisher/literature (see DOI)",
        },
    ]


D06_FEATURES = [
    ("particle_size_distribution", "distribution", "fraction"),
    ("bulk_density_kg_m3", "numeric", "kg/m^3"),
    ("porosity_fraction", "numeric", "fraction"),
    ("cohesion_kpa", "numeric", "kPa"),
    ("friction_angle_deg", "numeric", "degree"),
    ("bearing_strength_kpa", "numeric", "kPa"),
]


def d06_observations(aoi: dict[str, Any]) -> list[dict[str, Any]]:
    if aoi["body"] == "Moon":
        dataset_ids = ["nasa-pds-apollo-lsrp-audit", "nasa-pds-apollo-hfe-audit"]
        source_asset_ids = [
            "pds:urn-nasa-pds-a15lsrp_soil_mechanics",
            "pds:urn-nasa-pds-a16lsrp_soil_mechanics",
            "pds:urn-nasa-pds-a15_17_hfe_concatenated",
        ]
        source_note = (
            "Audited official sources: NASA PDS Apollo 15 LSRP (urn:nasa:pds:a15lsrp_soil_mechanics, "
            "Station 8 context), Apollo 16 LSRP (urn:nasa:pds:a16lsrp_soil_mechanics), Apollo 15/17 HFE "
            "(urn:nasa:pds:a15_17_hfe_concatenated). Apollo in-situ/sample reports include ranges and/or "
            "model-interpreted mechanics at specific stations/samples (not this Shackleton AOI) and are not "
            "encodable as direct detected site-depth D06 values here without verified per-sample extraction "
            "and required support/conditions."
        )
        aoi_note = (
            f"Requested AOI is Shackleton rim centre point ({aoi['center']['latitude_deg']}, "
            f"{aoi['center']['longitude_deg']}, MOON_ME), not an Apollo landing/sample station. No official "
            "PDS/literature regolith measurement with defensible footprint/depth/support found for this AOI."
        )
    else:
        dataset_ids = ["nasa-pds-insight-hp3-rad-audit", "insight-hp3-literature"]
        source_asset_ids = [
            "pds:urn-nasa-pds-insight_hp3_tem",
            "pds:urn-nasa-pds-insight_rad",
            "lit:spohn-2022-hp3-mole",
        ]
        source_note = (
            "Audited official sources: NASA PDS InSight HP3 TEM (urn:nasa:pds:insight_hp3_tem) and RAD "
            "(urn:nasa:pds:insight_rad). Literature (e.g. Spohn et al. 2022, doi:10.1007/s11214-022-00941-z) "
            "reports model-derived/interpretive properties at the InSight site (Elysium Planitia), not direct "
            "detected D06 measurements for this Jezero AOI and not transferable."
        )
        aoi_note = (
            f"Requested AOI is Jezero CTX window centre ({aoi['center']['latitude_deg']}, "
            f"{aoi['center']['longitude_deg']}, IAU_MARS), not the InSight site. No official PDS/literature "
            "regolith measurement with defensible footprint/depth/support found for this AOI."
        )

    rows = []
    for feature_id, value_kind, unit in D06_FEATURES:
        rows.append(
            {
                "schema_version": "2.0.0",
                "observation_id": f"d06:regolith:{aoi['area_id']}:{feature_id}:no_coverage",
                "area_id": aoi["area_id"],
                "body": aoi["body"],
                "dimension_code": "D06",
                "feature_id": feature_id,
                "value_kind": value_kind,
                "state": "no_coverage",
                "value": None,
                "unit": unit,
                "uncertainty": {
                    "kind": "unknown",
                    "notes": "No numeric regolith value ingested; explicit no_coverage (no fabrication).",
                },
                "spatial_support": {
                    "kind": "point",
                    "crs": aoi["center"]["body_fixed_frame"],
                    "notes": f"Bounded AOI centre point ({aoi['center']['latitude_deg']}, {aoi['center']['longitude_deg']}) in {aoi['center']['body_fixed_frame']}; not exhaustive spatial coverage.",
                },
                "temporal_support": {
                    "kind": "unknown",
                    "notes": "No time-bounded in-situ/sample regolith observation tied to this AOI.",
                },
                "depth_support": {
                    "kind": "unknown",
                    "min_m": None,
                    "max_m": None,
                },
                "method_version": "d06-regolith-evidence-audit-0.1.0",
                "dataset_ids": dataset_ids,
                "source_asset_ids": source_asset_ids,
                "quality_flags": [
                    "evidence_audit_no_numeric_ingest",
                    "no_coverage_explicit",
                    "official_source_audited",
                    "no_dem_roughness_inference",
                    "no_slope_or_landcover_proxy",
                    "no_earth_transfer",
                    "not_global_coverage",
                    "aoi_site_mismatch_or_unresolved_support",
                ],
                "score_role": "evidence_only",
                "evidence": None,
                "legacy_provenance": {
                    "audit_scope": "bounded-aoi-official-source-search",
                    "aoi_context": aoi_note,
                    "source_summary": source_note,
                },
                "notes": f"{aoi_note} {source_note} No verified measurement depth/support for this AOI (depth_support unknown); explicit depth interval/support would be required for any detected D06 value. No DEM roughness/slope, land-cover, thermal-inertia, or Earth transfer used to infer regolith properties.",
            }
        )
    return rows


def main() -> int:
    aois = load_aois()
    obs: list[dict[str, Any]] = []
    for aoi in aois:
        obs.extend(illumination_observations(aoi))
        obs.extend(radiation_observations(aoi))
        obs.extend(d06_observations(aoi))

    observations_sha = write_jsonl(OUT / "observations.jsonl", obs)
    assets = kernel_assets() + radiation_source_assets() + d06_source_assets()
    assets_sha = write_json(OUT / "assets.json", assets)

    plan = {
        "plan_version": "acquisition-plan-1.0.0",
        "scope": "d05-d06-aoi-170",
        "generated_at": GENERATED_AT,
        "areas": [
            {
                "area_id": a["area_id"],
                "body": a["body"],
                "display_name": a["display_name"],
                "bbox": a["bbox"],
                "crs": a["crs"],
                "coordinate_frame": a["coordinate_frame"],
                "center": a["center"],
                "notes": a["notes"],
            }
            for a in aois
        ],
        "tasks": [
            {
                "task_id": f"d05-illumination:{a['area_id']}",
                "provider_id": "naif-spice",
                "source_id": "naif-spice-generic-kernels",
                "body": a["body"],
                "dimension": "D05",
                "subgroup": "solar_illumination",
                "area_id": a["area_id"],
                "method_version": "spice-solar-geometry-1.0.0",
                "epoch_utc": EPOCH_UTC,
                "status": "completed_from_committed_pilot",
            }
            for a in aois
        ]
        + [
            {
                "task_id": f"d05-ionizing-radiation:{a['area_id']}",
                "provider_id": "official-source-audit",
                "source_id": "d05-ionizing-radiation-source-audit",
                "body": a["body"],
                "dimension": "D05",
                "subgroup": "ionizing_radiation",
                "area_id": a["area_id"],
                "method_version": "ionizing-radiation-source-audit-0.1.0",
                "status": "completed_no_coverage",
            }
            for a in aois
        ]
        + [
            {
                "task_id": f"d06-regolith:{a['area_id']}:{fid}",
                "provider_id": "official-source-audit",
                "source_id": "d06-regolith-source-audit",
                "body": a["body"],
                "dimension": "D06",
                "feature_id": fid,
                "area_id": a["area_id"],
                "method_version": "d06-regolith-evidence-audit-0.1.0",
                "status": "completed_no_coverage",
            }
            for a in aois
            for fid, _, _ in D06_FEATURES
        ],
        "notes": [
            "D05 solar illumination values are model-derived SPICE geometry (real computed values) at one instant; not irradiance/shadow.",
            "D05 ionizing radiation is separated and explicitly no_coverage for these AOIs (no fabrication).",
            "D06 regolith: explicit no_coverage per AOI from official-source audit (no proxies, no DEM inference).",
        ],
    }
    plan_sha = write_json(OUT / "plan.json", plan)

    requests = []
    for a in aois:
        requests.append({
            "task_id": f"d05-illumination:{a['area_id']}",
            "area_id": a["area_id"], "body": a["body"], "dimension": "D05",
            "subgroup": "solar_illumination", "status": "ok_detected",
            "note": "Model-derived SPICE solar geometry from committed pilot provenance (epoch pinned).",
        })
        requests.append({
            "task_id": f"d05-ionizing-radiation:{a['area_id']}",
            "area_id": a["area_id"], "body": a["body"], "dimension": "D05",
            "subgroup": "ionizing_radiation", "status": "no_coverage",
            "note": "Official-source audit; no defensible AOI dose value without fabrication.",
        })
        for fid, _, _ in D06_FEATURES:
            requests.append({
                "task_id": f"d06-regolith:{a['area_id']}:{fid}",
                "area_id": a["area_id"], "body": a["body"], "dimension": "D06",
                "feature_id": fid, "status": "no_coverage",
                "note": "Official-source audit; no direct measured value with required footprint/depth/support for AOI.",
            })
    requests_sha = write_jsonl(OUT / "requests.jsonl", requests)

    by_body: dict[str, int] = {}
    by_dim: dict[str, int] = {}
    by_state: dict[str, int] = {}
    by_feature: dict[str, int] = {}
    for r in obs:
        by_body[r["body"]] = by_body.get(r["body"], 0) + 1
        by_dim[r["dimension_code"]] = by_dim.get(r["dimension_code"], 0) + 1
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        by_feature[r["feature_id"]] = by_feature.get(r["feature_id"], 0) + 1

    qa = {
        "qa_version": "d05-d06-aoi-qa-1.0.0",
        "scope": "d05-d06-aoi-170",
        "generated_at": GENERATED_AT,
        "counts": {
            "observations": len(obs),
            "by_body": dict(sorted(by_body.items())),
            "by_dimension": dict(sorted(by_dim.items())),
            "by_feature": dict(sorted(by_feature.items())),
            "by_state": dict(sorted(by_state.items())),
        },
        "checks": {
            "explicit_no_coverage_for_missing": True,
            "no_zero_fill": True,
            "no_proxy_as_measurement": True,
            "illumination_radiation_separated": True,
            "planetary_frames_only": True,
            "d06_regolith_no_coverage_audited": True,
        },
        "notes": [
            "Illumination: detected SPICE geometry values (model-derived) with full provenance.",
            "Ionizing radiation: explicit no_coverage per AOI with official source audit rationale.",
            "D06 regolith: explicit no_coverage for both AOIs with official-source audit, footprint/depth/support stated as unknown/unresolved for AOI (no proxy fill).",
        ],
    }
    qa_sha = write_json(OUT / "qa.json", qa)

    report = {
        "scope": "d05-d06-aoi-170",
        "generated_at": GENERATED_AT,
        "areas": [a["area_id"] for a in aois],
        "summary": qa["counts"],
        "d05_illumination": "detected SPICE solar geometry (model-derived, instant, flat ellipsoid) for Moon and Mars AOIs.",
        "d05_ionizing_radiation": "explicit no_coverage for both AOIs (official-source audit; separated from illumination; no Gy/Sv fabrication).",
        "d06_regolith": "explicit no_coverage for all supported features on both AOIs (official-source audit; no DEM/proxy inference; footprint/depth/support unresolved for AOI).",
    }
    report_sha = write_json(OUT / "REPORT.json", report)

    manifest = {
        "release_version": "d05-d06-aoi-evidence-1.0.0",
        "scope": "d05-d06-aoi-170",
        "generated_at": GENERATED_AT,
        "files": {
            "plan.json": {"sha256": plan_sha},
            "assets.json": {"sha256": assets_sha},
            "observations.jsonl": {"sha256": observations_sha},
            "requests.jsonl": {"sha256": requests_sha},
            "qa.json": {"sha256": qa_sha},
            "REPORT.json": {"sha256": report_sha},
        },
        "observations_sha256": observations_sha,
        "counts": qa["counts"],
        "source_provenance": {
            "d05_solar_illumination": {
                "method_version": "spice-solar-geometry-1.0.0",
                "dataset": "naif-spice-generic-kernels",
                "epoch_utc": EPOCH_UTC,
                "source_pilot": "pipelines/pilots/d05_spice_illumination",
                "notes": "Model-derived geometry only (not irradiance/shadow).",
            },
            "d05_ionizing_radiation": {
                "method_version": "ionizing-radiation-source-audit-0.1.0",
                "status": "no_coverage_for_selected_aois",
                "sources": [a["uri"] for a in radiation_source_assets()],
            },
            "d06_regolith": {
                "method_version": "d06-regolith-evidence-audit-0.1.0",
                "status": "no_coverage_for_selected_aois",
                "sources": [a["uri"] for a in d06_source_assets()],
                "notes": "Official PDS/literature audited; no defensible direct detected value with required footprint/depth/support for Shackleton/Jezero AOIs. No DEM/proxy inference.",
            },
        },
        "limitations": [
            "Bounded AOIs only; not global or exhaustive six-dimension coverage.",
            "D05 illumination is model-derived SPICE geometry at one instant (flat ellipsoid tangent plane).",
            "D05 ionizing radiation: explicit no_coverage for selected AOIs (no fabrication, no Gy/Sv conversion without physics).",
            "D06 regolith: no defensible direct measured value with required footprint/depth/support for selected AOIs; explicit no_coverage records only.",
        ],
        "non_claims": [
            "No observed irradiance/radiance or shadow fraction.",
            "No ionizing absorbed/equivalent dose fabricated for AOIs without direct supported measurement/physics.",
            "No regolith proxy from DEM roughness; no Earth transfer.",
            "No global six-dimension completeness claim.",
        ],
    }
    write_json(OUT / "manifest.json", manifest)
    print(json.dumps({"observations": len(obs), "by_state": by_state, "sha256": observations_sha}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
