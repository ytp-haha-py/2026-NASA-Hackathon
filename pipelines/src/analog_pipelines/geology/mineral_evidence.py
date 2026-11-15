"""Bounded mineral spectral evidence: official source status and honest states.

This module separates three things that are often conflated:

* official product identity/version/license/access (a metadata claim);
* observation validity (whether a valid pixel supported a statement at all);
* mineral detection versus spectral band depth or abundance (never equivalent).

No provider is downloaded here, no mineral abundance is produced, and no new
scored Terrain feature is introduced. The committed report is built from the
pinned geology snapshot plus the pinned typed-v2 export.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from analog_pipelines.geology.io import verify_snapshot
from analog_pipelines.geology.models import Body, Manifest, State

DEFAULT_ROOT = Path(__file__).resolve().parents[4] / "pipelines/geology"

AccessStatus = Literal["open", "auth_required", "restricted", "unknown"]
SourceVerification = Literal["verified_metadata", "identified_metadata", "blocked"]
BlockerScope = Literal["all", "detection", "abundance"]
UnavailableReason = Literal["outside_footprint", "observation_unavailable", "outside_product_scope"]

STATES: tuple[State, ...] = (
    "detected",
    "not_detected_under_valid_observation",
    "no_coverage",
    "unknown",
    "not_applicable",
)

# Abundance units are listed only so the report can prove that none were produced.
ABUNDANCE_UNITS = {
    "wt%",
    "weight_percent",
    "mass_fraction",
    "mass_percent",
    "vol%",
    "volume_percent",
}

# A provider outage is unknown, a footprint miss is no coverage, and a mineral
# outside a product's scope is not applicable. None of them is a valid zero.
UNAVAILABLE_STATES: dict[UnavailableReason, State] = {
    "outside_footprint": "no_coverage",
    "observation_unavailable": "unknown",
    "outside_product_scope": "not_applicable",
}


def unavailable_state(reason: UnavailableReason) -> State:
    """Map an explicit unavailability reason to the matching observation state."""
    return UNAVAILABLE_STATES[reason]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Blocker(Model):
    blocker_id: str = Field(min_length=1)
    scope: BlockerScope
    summary: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    resolution_required: str = Field(min_length=1)


class OfficialProduct(Model):
    product_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    collection_id: str | None = None
    doi: str | None = None
    url: str
    metadata_query_url: str | None = None
    license: str
    license_url: str
    access_status: AccessStatus
    access_note: str
    verified_example: str | None = None
    wavelength_or_parameter_semantics: str


class SourceStatus(Model):
    source_id: str = Field(min_length=1)
    body: Body
    provider: str
    instrument: str
    geology_dataset_ids: list[str]
    products: list[OfficialProduct] = Field(min_length=1)
    verification_status: SourceVerification
    access_status: AccessStatus
    mineral_detection_enabled: bool
    abundance_enabled: bool
    semantics: str
    limitations: list[str] = Field(min_length=1)
    blockers: list[Blocker]

    @model_validator(mode="after")
    def claims_are_bounded(self) -> SourceStatus:
        if self.abundance_enabled and not self.mineral_detection_enabled:
            raise ValueError("abundance cannot be enabled without mineral detection")
        if self.mineral_detection_enabled and self.verification_status != "verified_metadata":
            raise ValueError("mineral detection requires verified source metadata")
        blocking = {blocker.scope for blocker in self.blockers}
        if self.mineral_detection_enabled and blocking & {"all", "detection"}:
            raise ValueError("mineral detection is blocked by an open blocker")
        if self.abundance_enabled and blocking & {"all", "abundance"}:
            raise ValueError("abundance is blocked by an open blocker")
        if {product.access_status for product in self.products} != {self.access_status}:
            raise ValueError("source and product access status disagree")
        return self


class StateSemantics(Model):
    state: State
    numeric_value: Literal["required", "optional", "forbidden"]
    valid_fraction: Literal["required_positive", "optional"]
    meaning: str
    never_means: str


class MineralEvidenceMatrix(Model):
    schema_version: Literal["mineral-evidence-1.0.0"] = "mineral-evidence-1.0.0"
    scope: str
    state_contract: list[StateSemantics]
    sources: list[SourceStatus]
    limitations: list[str]

    @model_validator(mode="after")
    def complete_and_unique(self) -> MineralEvidenceMatrix:
        if {item.state for item in self.state_contract} != set(STATES):
            raise ValueError("state contract must describe all five observation states")
        if len(self.state_contract) != len(STATES):
            raise ValueError("state contract has duplicate states")
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("duplicate mineral source ID")
        if {source.body for source in self.sources} != {"Earth", "Moon", "Mars"}:
            raise ValueError("source matrix must cover Earth, Moon and Mars")
        return self


STATE_CONTRACT = [
    StateSemantics(
        state="detected",
        numeric_value="optional",
        valid_fraction="required_positive",
        meaning=(
            "A validated detector identified the mineral in at least one valid AOI pixel; "
            "a numeric band depth may accompany identification."
        ),
        never_means=(
            "It is not an abundance, not a mass fraction, and not proof of aqueous alteration."
        ),
    ),
    StateSemantics(
        state="not_detected_under_valid_observation",
        numeric_value="forbidden",
        valid_fraction="required_positive",
        meaning=(
            "Valid observation covered the AOI but the validated detector did not identify "
            "the mineral; the state is categorical."
        ),
        never_means="It is not zero abundance, zero concentration or a quantitative null.",
    ),
    StateSemantics(
        state="no_coverage",
        numeric_value="forbidden",
        valid_fraction="optional",
        meaning="No provider pixel/footprint intersects the AOI, or the product is absent there.",
        never_means="It is not non-detection and it is not zero mineral content.",
    ),
    StateSemantics(
        state="unknown",
        numeric_value="forbidden",
        valid_fraction="optional",
        meaning=(
            "The footprint exists but no valid observation was obtained: provider outage, "
            "authentication block, missing quality/uncertainty, or unvalidated detector."
        ),
        never_means="It is not evidence of absence and it is not zero.",
    ),
    StateSemantics(
        state="not_applicable",
        numeric_value="forbidden",
        valid_fraction="optional",
        meaning=(
            "The requested mineral or measurement is outside the valid scope of the body, "
            "sensor or product."
        ),
        never_means="It is not an outage, not a footprint miss and not a non-detection.",
    ),
]


def _emit_products() -> list[OfficialProduct]:
    return [
        OfficialProduct(
            product_id="EMITL2BMIN",
            version="002",
            collection_id="C4079846859-LPCLOUD",
            doi="10.5067/EMIT/EMITL2BMIN.002",
            url="https://doi.org/10.5067/EMIT/EMITL2BMIN.002",
            metadata_query_url=(
                "https://cmr.earthdata.nasa.gov/search/collections.json?"
                "short_name=EMITL2BMIN&page_size=30"
            ),
            license="NASA Earth science data and information policy (open, no fee)",
            license_url=(
                "https://www.earthdata.nasa.gov/engage/open-data-services-and-software/"
                "data-and-information-policy"
            ),
            access_status="auth_required",
            access_note=(
                "Anonymous CMR collection/granule metadata works; LP DAAC granule assets are "
                "under lp-prod-protected and require Earthdata Login. No credentials were used."
            ),
            verified_example="EMIT_L2B_MIN_002_20260927T195248",
            wavelength_or_parameter_semantics=(
                "60 m mineral identification codes with per-mineral band depth and uncertainty "
                "on the native EMIT swath; band depth is not mass fraction."
            ),
        ),
        OfficialProduct(
            product_id="EMITL2BMIN",
            version="001",
            collection_id="C2408034484-LPCLOUD",
            doi="10.5067/EMIT/EMITL2BMIN.001",
            url="https://doi.org/10.5067/EMIT/EMITL2BMIN.001",
            metadata_query_url=(
                "https://cmr.earthdata.nasa.gov/search/collections.json?"
                "short_name=EMITL2BMIN&page_size=30"
            ),
            license="NASA Earth science data and information policy (open, no fee)",
            license_url=(
                "https://www.earthdata.nasa.gov/engage/open-data-services-and-software/"
                "data-and-information-policy"
            ),
            access_status="auth_required",
            access_note=(
                "Historical release; same protected LP DAAC asset access as V002. "
                "Never silently substituted for the current version."
            ),
            wavelength_or_parameter_semantics=(
                "60 m mineral identification codes with band depth and uncertainty; "
                "not an abundance product."
            ),
        ),
    ]


SOURCE_MATRIX = MineralEvidenceMatrix(
    scope=(
        "Bounded audit of Earth EMIT, Moon M3 and Mars CRISM mineral spectral evidence for "
        "the five existing Earth AOIs, one existing lunar ROI and two explicitly marked Mars "
        "test AOIs; not a global survey."
    ),
    state_contract=STATE_CONTRACT,
    sources=[
        SourceStatus(
            source_id="earth-emit-mineral",
            body="Earth",
            provider="NASA JPL / LP DAAC (EMIT mission)",
            instrument="EMIT imaging spectrometer",
            geology_dataset_ids=["emit-l2bmin-002", "emit-l2bmin-001"],
            products=_emit_products(),
            verification_status="verified_metadata",
            access_status="auth_required",
            mineral_detection_enabled=False,
            abundance_enabled=False,
            semantics=(
                "L2B MIN supplies mineral identification plus band depth/uncertainty. "
                "Detections require decoding product IDs, quality and uncertainty on a real "
                "granule; a band depth is never converted to wt%."
            ),
            limitations=[
                "A CMR bounding-box hit is potential footprint coverage, not valid pixel coverage.",
                "EMIT is a native swath product, not orthorectified to a fixed grid.",
                "60 m native pixels do not become a 1.98 km terrain metric.",
                "No granule was downloaded in this pilot; no value is presented as observed.",
            ],
            blockers=[
                Blocker(
                    blocker_id="emit_auth",
                    scope="all",
                    summary="Earthdata Login required for granule assets",
                    detail=(
                        "CMR metadata is anonymous, but every LP DAAC asset URL observed is "
                        "lp-prod-protected. No credentials were requested, read or stored."
                    ),
                    resolution_required=(
                        "An authorized operator must download one bounded granule and record "
                        "its checksum; access remains opt-in and outside CI."
                    ),
                ),
                Blocker(
                    blocker_id="emit_decoder",
                    scope="detection",
                    summary="NetCDF mineral-ID/quality/uncertainty decoding not validated",
                    detail=(
                        "The repository has a tested native-pixel aggregation core "
                        "(summarize_identification) but no granule-validated EMIT reader, "
                        "mineral-group lookup or quality-bit mapping."
                    ),
                    resolution_required=(
                        "Validate the real group/band names, fill values, QA mask and "
                        "per-pixel uncertainty against one pinned granule before enabling state."
                    ),
                ),
                Blocker(
                    blocker_id="emit_native_swath",
                    scope="detection",
                    summary="Native swath geolocation not validated for the AOIs",
                    detail=(
                        "The counts in this report are CMR metadata candidates only. "
                        "AOI intersection requires per-pixel geolocation, not a granule "
                        "bounding box."
                    ),
                    resolution_required=(
                        "Reproject/intersect native pixel centers and retain valid_fraction per AOI."
                    ),
                ),
            ],
        ),
        SourceStatus(
            source_id="moon-m3-reflectance",
            body="Moon",
            provider="NASA Planetary Data System / Chandrayaan-1 M3 team",
            instrument="Moon Mineralogy Mapper (M3) imaging spectrometer",
            geology_dataset_ids=[],
            products=[
                OfficialProduct(
                    product_id="CH1-ORB-L-M3-4-L2-REFLECTANCE-V1.0",
                    version="1.0",
                    collection_id="urn:nasa:pds:ch1_m3:data::1.0",
                    doi="10.17189/f8xf-6a29",
                    url=(
                        "https://pds.nasa.gov/ds-view/pds/viewProfile.jsp?"
                        "dsid=CH1-ORB-L-M3-4-L2-REFLECTANCE-V1.0"
                    ),
                    metadata_query_url=(
                        "https://pds.nasa.gov/ds-view/pds/viewCollection.jsp?"
                        "identifier=urn:nasa:pds:ch1_m3:data"
                    ),
                    license="NASA Planetary Data System archived data (public domain)",
                    license_url="https://pds.nasa.gov/",
                    access_status="open",
                    access_note=(
                        "Public PDS archive; no login. Data set was reorganised into the PDS4 "
                        "collection urn:nasa:pds:ch1_m3:data::1.0 (DOI 10.17189/f8xf-6a29)."
                    ),
                    verified_example="M3G20090203T175131_V01_RFL",
                    wavelength_or_parameter_semantics=(
                        "Pixel-located, reflectance-calibrated (RADF at i=30 deg, e=0 deg) "
                        "near-IR spectra in 85 global-mode or 260 target-mode bands, "
                        "Mean Earth/planetocentric coordinates, 0-360 deg east longitude."
                    ),
                )
            ],
            verification_status="identified_metadata",
            access_status="open",
            mineral_detection_enabled=False,
            abundance_enabled=False,
            semantics=(
                "M3 reflectance and derived band depth are spectral measurements. "
                "No validated continuum-removal estimator, detection threshold or abundance "
                "model is applied in this pilot."
            ),
            limitations=[
                "M3 operated 2008-11 to 2009-08 and covered about 95% of the Moon in global mode; "
                "it is not a current or exhaustive survey.",
                "Global-mode footprints are coarse relative to a small ROI; target-mode coverage "
                "is minimal because the mission ended early.",
                "The existing Zenodo v4 research-derived lunar mineral matrix is not the official "
                "M3 archive and lacks CRS, units, QA and uncertainty.",
            ],
            blockers=[
                Blocker(
                    blocker_id="m3_registration",
                    scope="detection",
                    summary="M3 registration/geolocation not validated in this repository",
                    detail=(
                        "The official L2 product is documented as pixel-located in the Mean "
                        "Earth frame, but no M3 granule was downloaded and no per-pixel "
                        "latitude/longitude, thermal correction or band alignment was checked "
                        "against the lunar ROI."
                    ),
                    resolution_required=(
                        "Download one bounded L2 granule, validate its detached label "
                        "geolocation and band binning, and record pixel-center coverage."
                    ),
                ),
                Blocker(
                    blocker_id="m3_thresholds",
                    scope="detection",
                    summary="No validated M3 mineral detection thresholds",
                    detail=(
                        "Continuum removal, band-depth placement and a detection threshold "
                        "must be specified and tested before any detected/non-detected state."
                    ),
                    resolution_required=(
                        "Pin an estimator and threshold with a citation and offline fixtures; "
                        "keep reflectance and band depth separate from abundance."
                    ),
                ),
                Blocker(
                    blocker_id="m3_research_product",
                    scope="all",
                    summary="Research-derived polar matrix cannot substitute for M3",
                    detail=(
                        "The pinned Zenodo v4 matrix has no CRS, transform, units, valid range, "
                        "QA or uncertainty, and its registration is unverified."
                    ),
                    resolution_required=(
                        "Do not extract AOI values from it; treat it as format-inspected metadata "
                        "until an authoritative registration exists."
                    ),
                ),
            ],
        ),
        SourceStatus(
            source_id="mars-crism-mtrdr",
            body="Mars",
            provider="NASA PDS Geosciences Node / MRO CRISM",
            instrument="Compact Reconnaissance Imaging Spectrometer for Mars (CRISM)",
            geology_dataset_ids=["crism-mtrdr-context"],
            products=[
                OfficialProduct(
                    product_id="hrl000040ff_07_if183j_mtr3",
                    version="MTR3",
                    collection_id="mro-m-crism-5-rdr-mptargeted-v1 (volume mrocr_4001)",
                    url=(
                        "https://pds-geosciences.wustl.edu/mro/"
                        "mro-m-crism-5-rdr-mptargeted-v1/mrocr_4001/mtrdr/2007/2007_029/"
                        "hrl000040ff/hrl000040ff_07_if183j_mtr3.lbl"
                    ),
                    metadata_query_url="https://oderest.rsl.wustl.edu/live2/",
                    license="PDS archived data are in the public domain",
                    license_url=(
                        "https://pds-geosciences.wustl.edu/documents/"
                        "ViewingPDSImagesAndExportingToOtherFormats.pdf"
                    ),
                    access_status="open",
                    access_note=(
                        "Public HTTPS. This pilot downloaded the refined spectral summary and "
                        "DP information maps only, never the full spectral cube."
                    ),
                    verified_example="hrl000040ff_07_sr183j_mtr3 (refined summary pair)",
                    wavelength_or_parameter_semantics=(
                        "36 m refined spectral summary parameters (OLINDEX3, LCPINDEX2, "
                        "HCPINDEX2, D2300, BD1900_2, SINDEX2, CINDEX2); dimensionless "
                        "spectral parameters, not band depth in abundance units."
                    ),
                )
            ],
            verification_status="verified_metadata",
            access_status="open",
            mineral_detection_enabled=False,
            abundance_enabled=False,
            semantics=(
                "The MTRDR refined summary parameters are context. Published parameter names "
                "do not establish a mineral identification threshold, and the DP information "
                "maps are not interpreted as a detection algorithm here."
            ),
            limitations=[
                "Two explicitly marked Mars test AOIs only; not a Mars reference area.",
                "Summary-parameter coverage counts finite non-fill pixels, not validated mineral "
                "detections.",
                "MTRDR uses a latitude-dependent custom Mars sphere; native CRS and pixel size "
                "are retained rather than forced to WGS84.",
                "One bounded ODE query over the first five products selected one summary cube; "
                "this is not an exhaustive CRISM survey.",
            ],
            blockers=[
                Blocker(
                    blocker_id="crism_thresholds",
                    scope="detection",
                    summary="CRISM summary-parameter detection thresholds undocumented here",
                    detail=(
                        "OLINDEX3/LCPINDEX2/HCPINDEX2/D2300/BD1900_2/SINDEX2/CINDEX2 values are "
                        "retained as medians, but no published threshold or DP-mask bit mapping "
                        "was validated for mineral identification."
                    ),
                    resolution_required=(
                        "Pin the CRISM ATBD/DP mask definition and validate thresholds on "
                        "labelled spectra before emitting detected/non-detected states."
                    ),
                ),
                Blocker(
                    blocker_id="crism_continuum",
                    scope="detection",
                    summary="No continuum removal or uncertainty propagation",
                    detail=(
                        "The summary parameters are published indices; no independent band-depth "
                        "estimator, atmospheric correction or uncertainty model was applied."
                    ),
                    resolution_required=(
                        "Define measurement semantics and uncertainty before any numeric "
                        "mineral observation is exported."
                    ),
                ),
                Blocker(
                    blocker_id="crism_bounded_query",
                    scope="all",
                    summary="Bounded product selection is not exhaustive coverage",
                    detail=(
                        "The ODE query is bounded to two test AOIs and the first five products, "
                        "then one smallest refined-summary cube is selected."
                    ),
                    resolution_required=(
                        "Any wider claim needs a new bounded audit with explicit product counts."
                    ),
                ),
            ],
        ),
    ],
    limitations=[
        "No source in this pilot has a validated mineral detector or abundance model.",
        "Band depth, spectral index and reflectance are never converted to mass fraction.",
        "Metadata presence, footprint overlap and finite-pixel fraction are not detections.",
        "Existing Terrain observations, IDs, method versions and snapshots are untouched.",
    ],
)


def _load_typed(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    manifest = json.loads((root / "typed/manifest.json").read_text(encoding="utf-8"))
    path = root / "typed/observations.jsonl"
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if manifest.get("observations_sha256") != checksum:
        raise ValueError("typed observations checksum does not match typed manifest")
    rows = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != manifest.get("observations"):
        raise ValueError("typed observations count does not match typed manifest")
    return manifest, rows, checksum


def _status_counts(manifest: Manifest) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for body in ("Earth", "Moon", "Mars"):
        records = [item for item in manifest.analyses if item.body == body]
        counts = Counter(item.status for item in records)
        result[body] = {"total": len(records)} | dict(sorted(counts.items()))
    return result


def _state_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(row["state"] for row in rows)
    states = ("detected", "not_detected", "no_coverage", "unknown", "not_applicable")
    return {state: counts.get(state, 0) for state in states}


def _emit_counts(emit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    versions: dict[str, Any] = {}
    for version in ("002", "001"):
        rows = [row for row in emit_rows if row.get("version") == version]
        statuses = Counter(row.get("status") for row in rows)
        versions[version] = {
            "aoi_rows": len(rows),
            "metadata_candidate_count": sum(row.get("bbox_candidate_count") or 0 for row in rows),
            "aois_with_candidates": sum(
                1 for row in rows if (row.get("bbox_candidate_count") or 0) > 0
            ),
            "aois_without_candidates": sum(
                1 for row in rows if not (row.get("bbox_candidate_count") or 0)
            ),
            "status_counts": dict(sorted(statuses.items())),
            "granules_downloaded": 0,
            "valid_pixel_fraction": None,
        }
    return {
        "aoi_rows": len(emit_rows),
        "versions": versions,
        "granules_downloaded": 0,
        "mineral_detections": 0,
        "abundance_values": 0,
    }


def _crism_counts(manifest: Manifest, context_rows: list[dict[str, Any]]) -> dict[str, Any]:
    records = [
        item
        for item in manifest.analyses
        if item.dataset_id == "crism-mtrdr-context" and item.status == "context_only"
    ]
    parameters = sorted({row["observation_id"].rsplit(":", 1)[-1] for row in context_rows})
    products = sorted({row["observation_id"].rsplit(":", 2)[-2] for row in context_rows})
    values_by_area: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(context_rows, key=lambda item: (item["area_id"], item["observation_id"])):
        values_by_area.setdefault(row["area_id"], []).append(
            {
                "parameter": row["observation_id"].rsplit(":", 1)[-1],
                "value": row["value"],
                "unit": row["unit"],
                "valid_fraction": row["spatial_support"]["valid_fraction"],
                "observation_id": row["observation_id"],
                "state": row["state"],
                "score_role": row["score_role"],
            }
        )
    return {
        "aoi_count": len(records),
        "selected_products": products,
        "parameter_count": len(parameters),
        "parameters": parameters,
        "value_count": sum(1 for row in context_rows if row.get("value") is not None),
        "values_by_area": values_by_area,
        "unit": "dimensionless_spectral_parameter",
        "native_resolution_m": sorted(
            {item.native_resolution_m for item in records if item.native_resolution_m}
        ),
        "valid_fraction_by_area": {
            item.area_id: item.valid_fraction for item in sorted(records, key=lambda r: r.area_id)
        },
        "coverage_semantics": (
            "AOI pixel-center, non-fill finite summary and DP-mask pixels; not validated "
            "mineral detection coverage"
        ),
        "mineral_detections": 0,
        "abundance_values": 0,
    }


def build_mineral_evidence(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    """Build a deterministic evidence report from pinned local artifacts only."""
    root = Path(root)
    snapshot = json.loads((root / "snapshots/geology.snapshot.json").read_text(encoding="utf-8"))
    manifest = verify_snapshot(root, snapshot)
    typed_manifest, rows, typed_checksum = _load_typed(root)
    audit_bytes = (root / "coverage-audit.json").read_bytes()
    audit = json.loads(audit_bytes)

    mineral_rows = [row for row in rows if row["feature_id"].startswith("mineral_")]
    context_rows = [row for row in rows if row["feature_id"].startswith("context_")]
    emit_rows = audit.get("emit_coverage", [])

    mineral_by_body = Counter(row["body"] for row in mineral_rows)
    context_by_body = Counter(row["body"] for row in context_rows)
    context_states = Counter(row["state"] for row in context_rows)

    counts = {
        "areas": dict(sorted(Counter(item.body for item in manifest.areas).items())),
        "analyses": {
            "total": len(manifest.analyses),
            "by_body": _status_counts(manifest),
        },
        "exported_observations": {
            "total": len(rows),
            "dimension_codes": dict(sorted(Counter(row["dimension_code"] for row in rows).items())),
            "score_roles": dict(sorted(Counter(row["score_role"] for row in rows).items())),
            "by_body": dict(sorted(Counter(row["body"] for row in rows).items())),
            "by_state": _state_counts(rows),
        },
        "mineral_observations": {
            "total": len(mineral_rows),
            "by_body": {body: mineral_by_body.get(body, 0) for body in ("Earth", "Moon", "Mars")},
            "by_state": _state_counts(mineral_rows),
            "numeric_values": sum(1 for row in mineral_rows if row.get("value") is not None),
            "abundance_values": sum(
                1 for row in mineral_rows if (row.get("unit") or "") in ABUNDANCE_UNITS
            ),
            "detected": sum(1 for row in mineral_rows if row["state"] == "detected"),
        },
        "spectral_context_observations": {
            "total": len(context_rows),
            "by_body": {body: context_by_body.get(body, 0) for body in ("Earth", "Moon", "Mars")},
            "by_state": dict(sorted(context_states.items())),
            "numeric_values": sum(1 for row in context_rows if row.get("value") is not None),
            "units": sorted({row.get("unit") for row in context_rows if row.get("unit")}),
            "mineral_detection_claims": 0,
            "abundance_values": 0,
        },
        "emit_metadata_coverage": _emit_counts(emit_rows),
        "m3_evidence": {
            "granules_downloaded": 0,
            "validated_observations": 0,
            "aoi_extractions": 0,
            "mineral_detections": 0,
            "abundance_values": 0,
            "status": "official_product_identified_not_fetched",
        },
        "crism_context": _crism_counts(manifest, context_rows),
    }

    blockers = [
        blocker.model_dump(mode="json")
        for source in SOURCE_MATRIX.sources
        for blocker in source.blockers
    ]
    return {
        "schema_version": "mineral-evidence-report-1.0.0",
        "matrix": SOURCE_MATRIX.model_dump(mode="json"),
        "provenance": {
            "pinned_snapshot_id": snapshot["snapshot_id"],
            "snapshot_created_at": snapshot["created_at"],
            "manifest_generated_at": manifest.generated_at.isoformat(),
            "source_record": typed_manifest.get("source_record"),
            "coverage_audit": "pipelines/geology/coverage-audit.json",
            "typed_observations_sha256": typed_checksum,
            "coverage_audit_sha256": hashlib.sha256(audit_bytes).hexdigest(),
        },
        "counts": counts,
        "blockers": blockers,
        "limitations": [
            "All counts describe the bounded AOIs above, not global coverage.",
            "No mineral abundance is produced, imputed or zero-filled by this report.",
            "EMIT counts are CMR metadata candidates; CRISM counts are spectral context.",
        ],
    }


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    counts, provenance = report["counts"], report["provenance"]
    lines = [
        "# D02 mineral spectral evidence — bounded pilot",
        "",
        "Official product status, observation-state semantics and measured counts for",
        "Earth EMIT, Moon M3 and Mars CRISM. Built from pinned local artifacts only;",
        "no granule was downloaded for this report.",
        "",
        f"- Pinned geology snapshot: `{provenance['pinned_snapshot_id']}`",
        f"- Typed observations SHA-256: `{provenance['typed_observations_sha256']}`",
        f"- Source record: `{provenance['source_record']}`",
        "",
        "## Source status matrix",
        "",
        "| Body | Source | Official product | Access | Verification | Detection | Abundance |",
        "|---|---|---|---|---|---|---|",
    ]
    for source in report["matrix"]["sources"]:
        products = ", ".join(
            f"{product['product_id']} ({product['version']})" for product in source["products"]
        )
        examples = "; ".join(
            product["verified_example"]
            for product in source["products"]
            if product.get("verified_example")
        )
        if examples:
            products = f"{products}; example {examples}"
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(source["body"]),
                    _cell(source["source_id"]),
                    _cell(products),
                    _cell(source["access_status"]),
                    _cell(source["verification_status"]),
                    "disabled",
                    "disabled",
                ]
            )
            + " |"
        )
    lines += [
        "",
        "Access `auth_required` means anonymous metadata was read but granule assets need an",
        "Earthdata Login. `verification_status` describes product metadata, not observation",
        "validity. No row enables detection or abundance in this pilot.",
        "",
        "## Actual counts",
        "",
        "| Item | Value |",
        "|---|---:|",
        f"| Areas (Earth / Moon / Mars) | {counts['areas'].get('Earth', 0)} / "
        f"{counts['areas'].get('Moon', 0)} / {counts['areas'].get('Mars', 0)} |",
        f"| Analyses | {counts['analyses']['total']} |",
        f"| Exported typed-v2 observations | {counts['exported_observations']['total']} |",
        f"| Mineral observations | {counts['mineral_observations']['total']} |",
        f"| Mineral detections | {counts['mineral_observations']['detected']} |",
        f"| Mineral numeric values | {counts['mineral_observations']['numeric_values']} |",
        f"| Mineral abundance values | {counts['mineral_observations']['abundance_values']} |",
        f"| CRISM spectral-context values | "
        f"{counts['spectral_context_observations']['numeric_values']} |",
        f"| EMIT granules downloaded | {counts['emit_metadata_coverage']['granules_downloaded']} |",
        f"| M3 granules downloaded | {counts['m3_evidence']['granules_downloaded']} |",
        "",
        "Provider status (analyses):",
        "",
        "| Body | Total | Status counts |",
        "|---|---:|---|",
    ]
    for body, values in counts["analyses"]["by_body"].items():
        statuses = ", ".join(f"{name}={value}" for name, value in values.items() if name != "total")
        lines.append(f"| {_cell(body)} | {values['total']} | {_cell(statuses)} |")
    emit = counts["emit_metadata_coverage"]
    lines += [
        "",
        "EMIT CMR metadata candidates (no granule downloaded):",
        "",
        "| Version | AOI rows | Candidate count | AOIs with hits | AOIs without hits | Status counts |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for version, values in emit["versions"].items():
        statuses = ", ".join(f"{k}={v}" for k, v in values["status_counts"].items())
        lines.append(
            f"| {version} | {values['aoi_rows']} | {values['metadata_candidate_count']} | "
            f"{values['aois_with_candidates']} | {values['aois_without_candidates']} | "
            f"{_cell(statuses)} |"
        )
    crism = counts["crism_context"]
    lines += [
        "",
        f"CRISM context covers {crism['aoi_count']} test AOIs with "
        f"{crism['parameter_count']} parameters and {crism['value_count']} numeric values. "
        f"Selected product(s): {', '.join(f'`{p}`' for p in crism['selected_products'])}; "
        f"native resolution {', '.join(str(v) for v in crism['native_resolution_m'])} m. "
        "All values are dimensionless spectral parameters, not detections or abundances.",
        "",
        "CRISM refined-summary medians by test AOI (context only):",
        "",
    ]
    areas = list(crism["values_by_area"])
    header = "| Parameter | " + " | ".join(_cell(area) for area in areas) + " | Unit |"
    lines += [header, "|---|" + "---:|" * len(areas) + "---|"]
    unit = crism["unit"]
    for parameter in crism["parameters"]:
        cells = []
        for area in areas:
            value = next(
                item["value"]
                for item in crism["values_by_area"][area]
                if item["parameter"] == parameter
            )
            cells.append("null" if value is None else f"{value:.6g}")
        lines.append(f"| {parameter} | " + " | ".join(cells) + f" | {unit} |")
    lines += [
        "",
        "## Evidence-state semantics",
        "",
        "| State | Numeric value | Valid fraction | Meaning | Never means |",
        "|---|---|---|---|---|",
    ]
    for item in report["matrix"]["state_contract"]:
        lines.append(
            f"| `{item['state']}` | {item['numeric_value']} | {item['valid_fraction']} | "
            f"{_cell(item['meaning'])} | {_cell(item['never_means'])} |"
        )
    lines += [
        "",
        "The typed-v2 export collapses `not_detected_under_valid_observation` to",
        "`not_detected`; neither is a numeric zero. `no_coverage`, `unknown` and",
        "`not_applicable` remain distinct and always carry a null value.",
        "",
        "## Blockers",
        "",
    ]
    for blocker in report["blockers"]:
        lines += [
            f"### `{blocker['blocker_id']}` ({blocker['scope']})",
            "",
            f"- **Summary**: {blocker['summary']}",
            f"- **Detail**: {blocker['detail']}",
            f"- **Resolution required**: {blocker['resolution_required']}",
            "",
        ]
    lines += [
        "## Reproduce",
        "",
        "```bash",
        "uv run python scripts/build_mineral_evidence.py",
        "uv run pytest pipelines/tests/test_mineral_evidence.py -o addopts='' -q",
        "```",
        "",
        "The builder verifies the pinned geology snapshot and the typed-v2 observations",
        "checksum before writing. It performs no network access and writes only",
        "`pipelines/geology/mineral-evidence.json` and `pipelines/geology/MINERAL_EVIDENCE.md`.",
        "",
    ]
    return "\n".join(lines)


def write_mineral_evidence(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    root = Path(root)
    report = build_mineral_evidence(root)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (root / "mineral-evidence.json").write_text(payload, encoding="utf-8")
    (root / "MINERAL_EVIDENCE.md").write_text(render_markdown(report), encoding="utf-8")
    return report
