"""Mars metadata providers with explicit comparability gates.

Neither provider downloads a global Mars raster or emits a Mars observation in this
repository. The MOLA and THEMIS products are recorded from official public metadata,
and acquisition is blocked by resolution/format/size/feature-registry constraints.
No Mars numeric value is invented or zero-filled.
"""

from __future__ import annotations

from pathlib import Path

from analog_pipelines.acquisition.errors import AcquisitionError
from analog_pipelines.acquisition.mars_comparability import (
    MOLA_MEGDR_16PPD_RESOLUTION_M,
    MOLA_MEGDR_128PPD_RESOLUTION_M,
    THERMAL_METHOD_VERSION,
    assess_mola_megdr_terrain,
    assess_themis_thermal_inertia,
)
from analog_pipelines.acquisition.models import (
    AcquisitionTask,
    LocalAsset,
    PlanArea,
    SourceMetadata,
    SourceQuery,
    SourceRecord,
    ValidationIssue,
    ValidationReport,
)
from analog_pipelines.typed_contracts import TypedObservation

MOLA_PROVIDER_ID = "pds4-mola-megdr"
MOLA_SOURCE_ID = "pds4-mgs-mola-megdr-topography"
MOLA_BUNDLE_LID = "urn:nasa:pds:mgs_mola_topography_derived"
MOLA_BUNDLE_VERSION = "1.0"
MOLA_COLLECTION_VERSION = "2.0"
MOLA_PRODUCT_LID = "urn:nasa:pds:mgs_mola_topography_derived:data_meg016:megt90n000eb"
MOLA_PRODUCT_VERSION = "1.1"
MOLA_PDS3_DATASET_ID = "MGS-M-MOLA-5-MEGDR-L3-V1.0"
MOLA_DOI = "10.17189/1z1b-kv84"
MOLA_BUNDLE_URL = (
    "https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/"
    "mgs_mola_topography_derived_bundle.xml"
)
MOLA_PRODUCT_LABEL_URL = (
    "https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/"
    "meg016/megt90n000eb.xml"
)
MOLA_PRODUCT_IMAGE_URL = (
    "https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/"
    "meg016/megt90n000eb.img"
)
MOLA_IMAGE_BYTES = 33_177_600
MOLA_LINES = 2880
MOLA_SAMPLES = 5760
MOLA_DATASET_ID = "pds4-mgs-mola-megdr-meg016"

THERMAL_PROVIDER_ID = "usgs-themis-thermal-inertia"
THERMAL_SOURCE_ID = "usgs-mars-themis-global-thermal-inertia"
THERMAL_DATASET_ID = "usgs-mars-themis-global-thermal-inertia-quantitative"
THERMAL_PRODUCT_PAGE = (
    "https://astrogeology.usgs.gov/search/map/mars-themis-derived-global-thermal-inertia-mosaic"
)
THERMAL_TILE_PAGE = (
    "https://astrogeology.usgs.gov/search/map/"
    "themis_thermal_inertia_mosaic_quantitative_30n000e_100mpp"
)
THERMAL_TILE_URL = (
    "https://asc-astropedia.s3.us-west-2.amazonaws.com/Mars/Odyssey/"
    "THEMIS-Global-Thermal-Inertia-Mosaic/Quantitative-32-Bit/"
    "THEMIS_TI_Mosaic_Quant_30N000E_100mpp.cub"
)
THERMAL_TILE_BYTES = 2_532_510_198
THERMAL_NATIVE_RESOLUTION_M = 100.0
THERMAL_TILE_COUNT = 24
THERMAL_LATITUDE_COVERAGE = "-60 to 60 degrees"
THERMAL_ABSOLUTE_ACCURACY_FRACTION = 0.20
THERMAL_RELATIVE_ACCURACY_FRACTION = 0.10
THERMAL_CITATION = (
    "Fergason, R.L., Christensen, P.R., Kieffer, H.H. (2006), High-resolution thermal "
    "inertia derived from the Thermal Emission Imaging System (THEMIS): Thermal model "
    "and applications, J. Geophys. Res. 111, E12004, doi:10.1029/2006JE002735"
)
THERMAL_DOI = "10.1029/2006JE002735"


class MarsMolaMegdrProvider:
    """Metadata-only PDS4 MOLA MEGDR interface.

    The official MEGDR products are global median topography grids. The smallest
    single-file product (MEG016) is about 3.70 km/pixel; the 128 pixel/degree tiles
    are about 0.463 km/pixel. Neither supports a 90 m local terrain window without
    unvalidated upsampling, so no observation is produced.
    """

    provider_id = MOLA_PROVIDER_ID

    def discover(self, query: SourceQuery) -> list[SourceRecord]:
        if query.body != "Mars" or query.dimension != "D01":
            return []
        gate = assess_mola_megdr_terrain(
            native_resolution_m=MOLA_MEGDR_16PPD_RESOLUTION_M,
        )
        return [
            SourceRecord(
                source_id=MOLA_SOURCE_ID,
                provider_id=self.provider_id,
                body="Mars",
                dimension="D01",
                product="MGS MOLA Mission Experiment Gridded Data Record (MEGDR) topography",
                version=f"PDS4 product {MOLA_PRODUCT_VERSION}; PDS3 PRODUCT_VERSION_ID 2.0",
                endpoint=MOLA_PRODUCT_IMAGE_URL,
                license=(
                    "NASA PDS public data; an explicit redistribution licence is not "
                    "stated in the PDS4 bundle label"
                ),
                license_url=MOLA_BUNDLE_URL,
                native_resolution="3.7044613374 km/pixel (16 pixels/degree)",
                observation_kinds=["numeric"],
                comparability="not_comparable",
                notes=(
                    "Global median topography, not a local DEM. The 128 pixel/degree tiles "
                    "are ~0.463 km/pixel and are also too coarse for the 90 m fine method. "
                    "No Mars terrain observation is claimed."
                ),
                metadata={
                    "pds4_bundle_lid": MOLA_BUNDLE_LID,
                    "pds4_bundle_version": MOLA_BUNDLE_VERSION,
                    "pds4_collection_version": MOLA_COLLECTION_VERSION,
                    "pds4_product_lid": MOLA_PRODUCT_LID,
                    "pds4_product_version": MOLA_PRODUCT_VERSION,
                    "pds3_dataset_id": MOLA_PDS3_DATASET_ID,
                    "doi": MOLA_DOI,
                    "product_label_url": MOLA_PRODUCT_LABEL_URL,
                    "image_bytes": MOLA_IMAGE_BYTES,
                    "lines": MOLA_LINES,
                    "samples": MOLA_SAMPLES,
                    "element_type": "SignedMSB2",
                    "vertical_unit": "meter",
                    "map_projection": "Equirectangular",
                    "latitude_type": "Planetocentric",
                    "longitude_direction": "Positive East",
                    "body_radius_m": 3_396_000.0,
                    "pixel_scale_deg": 1 / 16,
                    "megdr_128ppd_resolution_m": MOLA_MEGDR_128PPD_RESOLUTION_M,
                    "license_status": "not_verified_in_pds4_bundle",
                    "comparability_gate": gate.as_dict(),
                    "observation_status": "unavailable",
                },
            )
        ]

    def inspect(self, source: SourceRecord) -> SourceMetadata:
        gate = assess_mola_megdr_terrain(
            native_resolution_m=MOLA_MEGDR_16PPD_RESOLUTION_M,
        )
        blockers = [
            *gate.blockers,
            "acquisition_not_attempted_to_avoid_an_unvalidated_global_mars_grid",
            "no_validated_mars_reference_manifest_exists",
        ]
        return SourceMetadata(
            source_id=source.source_id,
            status="blocked",
            fields={
                "metadata_verified": True,
                "pds4_product_lid": MOLA_PRODUCT_LID,
                "pds4_product_version": MOLA_PRODUCT_VERSION,
                "pds4_bundle_lid": MOLA_BUNDLE_LID,
                "doi": MOLA_DOI,
                "native_resolution_m": MOLA_MEGDR_16PPD_RESOLUTION_M,
                "megdr_128ppd_resolution_m": MOLA_MEGDR_128PPD_RESOLUTION_M,
                "analysis_resolution_m": 90.0,
                "license_status": "not_verified_in_pds4_bundle",
                "vertical_reference": "planetary radius minus GMM3 areoid",
                "interpolated_bins": True,
                "observations": "unavailable",
                "comparability_gate": gate.as_dict(),
            },
            blockers=blockers,
        )

    def plan(self, query: SourceQuery, areas: list[PlanArea]) -> list[AcquisitionTask]:
        return []

    def acquire(self, task: AcquisitionTask, cache_dir: Path) -> LocalAsset:
        raise AcquisitionError(
            "mola_megdr_acquisition_blocked: no bounded validated 90 m Mars MOLA window is "
            "available; no raster was downloaded and no Mars value was emitted"
        )

    def process(self, asset: LocalAsset, task: AcquisitionTask) -> list[TypedObservation]:
        raise AcquisitionError(
            "mola_megdr_processing_blocked: MEGDR native support is coarser than the 90 m "
            "terrain method and vertical/window uncertainty is not verified"
        )

    def validate(self, observations: list[TypedObservation]) -> ValidationReport:
        issues: list[ValidationIssue] = []
        for observation in observations:
            if observation.body != "Mars" or observation.dimension_code != "D01":
                issues.append(
                    ValidationIssue(
                        code="wrong_body_or_dimension",
                        message="MOLA outputs must be Mars D01",
                        observation_id=observation.observation_id,
                    )
                )
            if observation.state == "detected":
                issues.append(
                    ValidationIssue(
                        code="mola_detected_value_not_permitted",
                        message=(
                            "MEGDR is not comparable at 90 m; no detected Mars terrain "
                            "value is permitted until the comparability gate passes"
                        ),
                        observation_id=observation.observation_id,
                    )
                )
            if observation.score_role == "similarity":
                issues.append(
                    ValidationIssue(
                        code="wrong_score_role",
                        message="MOLA MEGDR cannot enter the terrain similarity score",
                        observation_id=observation.observation_id,
                    )
                )
        return ValidationReport.from_issues(len(observations), issues)


class MarsThemisThermalProvider:
    """Metadata-only Mars thermal-inertia evidence provider.

    The product is official, public-domain USGS Astrogeology quantitative THEMIS
    thermal inertia. It is D03 evidence only: no D03 thermal-inertia similarity
    feature is registered, there is no per-pixel uncertainty layer, and each
    30 x 60 degree tile is about 2.36 GB in ISIS3 .cub format.
    """

    provider_id = THERMAL_PROVIDER_ID

    def discover(self, query: SourceQuery) -> list[SourceRecord]:
        if query.body != "Mars" or query.dimension != "D03":
            return []
        gate = assess_themis_thermal_inertia(
            native_resolution_m=THERMAL_NATIVE_RESOLUTION_M,
        )
        return [
            SourceRecord(
                source_id=THERMAL_SOURCE_ID,
                provider_id=self.provider_id,
                body="Mars",
                dimension="D03",
                product="THEMIS-derived global thermal inertia mosaic (quantitative 32-bit)",
                version="Edition 1, published 2014-05-30; representative tile 30N000E",
                endpoint=THERMAL_TILE_URL,
                license="Public domain (USGS Astrogeology access constraints); please cite authors",
                license_url=THERMAL_PRODUCT_PAGE,
                native_resolution="100 m/pixel; 24 tiles; 60S to 60N latitude",
                observation_kinds=["numeric"],
                comparability="evidence_only",
                notes=(
                    "Apparent thermal inertia, not surface temperature. Absolute accuracy "
                    "is about 20% and relative accuracy within one image about 10%. "
                    "No observation is claimed and the value never enters terrain similarity."
                ),
                metadata={
                    "product_page": THERMAL_PRODUCT_PAGE,
                    "tile_page": THERMAL_TILE_PAGE,
                    "tile_bytes": THERMAL_TILE_BYTES,
                    "tile_count": THERMAL_TILE_COUNT,
                    "latitude_coverage": THERMAL_LATITUDE_COVERAGE,
                    "native_resolution_m": THERMAL_NATIVE_RESOLUTION_M,
                    "absolute_accuracy_fraction": THERMAL_ABSOLUTE_ACCURACY_FRACTION,
                    "relative_accuracy_fraction": THERMAL_RELATIVE_ACCURACY_FRACTION,
                    "citation": THERMAL_CITATION,
                    "doi": THERMAL_DOI,
                    "input_format": "ISIS3 .cub",
                    "method_version": THERMAL_METHOD_VERSION,
                    "score_role": "evidence_only",
                    "comparability_gate": gate.as_dict(),
                    "observation_status": "unavailable",
                },
            )
        ]

    def inspect(self, source: SourceRecord) -> SourceMetadata:
        gate = assess_themis_thermal_inertia(
            native_resolution_m=THERMAL_NATIVE_RESOLUTION_M,
        )
        blockers = [
            *gate.blockers,
            "tile_size_2532510198_bytes_exceeds_bounded_acquisition_budget",
            "isis3_cub_input_format_not_verified_with_the_configured_raster_reader",
            "coverage_is_limited_to_60s_to_60n_latitude",
            "no_verified_per_pixel_qa_or_uncertainty_layer",
        ]
        return SourceMetadata(
            source_id=source.source_id,
            status="blocked",
            fields={
                "metadata_verified": True,
                "product_page": THERMAL_PRODUCT_PAGE,
                "tile_page": THERMAL_TILE_PAGE,
                "license_status": "public_domain_per_usgs_product_page",
                "native_resolution_m": THERMAL_NATIVE_RESOLUTION_M,
                "tile_bytes": THERMAL_TILE_BYTES,
                "tile_count": THERMAL_TILE_COUNT,
                "coverage": THERMAL_LATITUDE_COVERAGE,
                "absolute_accuracy_fraction": THERMAL_ABSOLUTE_ACCURACY_FRACTION,
                "relative_accuracy_fraction": THERMAL_RELATIVE_ACCURACY_FRACTION,
                "score_role": "evidence_only",
                "observations": "unavailable",
                "comparability_gate": gate.as_dict(),
            },
            blockers=blockers,
        )

    def plan(self, query: SourceQuery, areas: list[PlanArea]) -> list[AcquisitionTask]:
        return []

    def acquire(self, task: AcquisitionTask, cache_dir: Path) -> LocalAsset:
        raise AcquisitionError(
            "mars_themis_thermal_acquisition_blocked: the 2.36 GB ISIS3 tile and missing "
            "per-pixel QA make a bounded observation unavailable; nothing was downloaded"
        )

    def process(self, asset: LocalAsset, task: AcquisitionTask) -> list[TypedObservation]:
        raise AcquisitionError(
            "mars_themis_thermal_processing_blocked: no approved D03 thermal-inertia "
            "feature ID or validated QA/uncertainty support exists"
        )

    def validate(self, observations: list[TypedObservation]) -> ValidationReport:
        issues: list[ValidationIssue] = []
        for observation in observations:
            if observation.body != "Mars" or observation.dimension_code != "D03":
                issues.append(
                    ValidationIssue(
                        code="wrong_body_or_dimension",
                        message="THEMIS thermal outputs must be Mars D03",
                        observation_id=observation.observation_id,
                    )
                )
            if observation.score_role not in {"evidence_only", "context_only"}:
                issues.append(
                    ValidationIssue(
                        code="wrong_score_role",
                        message="Mars thermal inertia is evidence/context only, never similarity",
                        observation_id=observation.observation_id,
                    )
                )
            if observation.state == "detected":
                issues.append(
                    ValidationIssue(
                        code="thermal_inertia_feature_not_registered",
                        message=(
                            "no approved D03 thermal-inertia feature ID exists; a detected "
                            "value cannot be persisted"
                        ),
                        observation_id=observation.observation_id,
                    )
                )
        return ValidationReport.from_issues(len(observations), issues)
