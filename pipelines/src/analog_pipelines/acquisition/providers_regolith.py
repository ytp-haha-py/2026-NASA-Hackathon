"""Sparse Moon/Mars regolith evidence provider (D06).

No public Moon or Mars regolith sample or PDS measurement is registered in this
repository. This provider exists to make that absence explicit and machine
readable: it discovers the source, reports it as blocked, and plans zero tasks.
It never estimates grain size, density, porosity or strength from a DEM, a
terrain derivative, or an Earth analog.
"""

from __future__ import annotations

from pathlib import Path

from analog_pipelines.acquisition.errors import AcquisitionError
from analog_pipelines.acquisition.models import (
    AcquisitionTask,
    LocalAsset,
    PlanArea,
    SourceMetadata,
    SourceQuery,
    SourceRecord,
    ValidationReport,
)
from analog_pipelines.typed_contracts import TypedObservation

REGOLITH_PROVIDER_ID = "sparse-regolith-samples"
REGOLITH_SOURCE_ID = "sparse-regolith-measurements"
REGOLITH_METHOD_VERSION = "regolith-sparse-evidence-0.1.0"

NO_SAMPLE_BLOCKERS = {
    "Moon": (
        "No public lunar regolith sample record (for example Apollo/Luna sample "
        "measurements) is registered with an official version, licence and depth/basis "
        "metadata in this repository."
    ),
    "Mars": (
        "No public Mars regolith measurement (for example PDS rover/sample or in-situ "
        "instrument record) is registered with an official version, licence and "
        "measurement conditions in this repository."
    ),
}


class SparseRegolithProvider:
    """Explicit no-coverage source for D06 Moon/Mars; never fabricates values."""

    provider_id = REGOLITH_PROVIDER_ID

    def discover(self, query: SourceQuery) -> list[SourceRecord]:
        if query.body not in NO_SAMPLE_BLOCKERS or query.dimension != "D06":
            return []
        body = query.body
        return [
            SourceRecord(
                source_id=REGOLITH_SOURCE_ID,
                provider_id=self.provider_id,
                body=body,  # type: ignore[arg-type]
                dimension="D06",
                product="Sparse regolith sample/measurement evidence",
                version="unregistered",
                endpoint="not-applicable://no-public-sample-registered",
                license="unknown",
                license_url="not-applicable://no-public-sample-registered",
                native_resolution="sample/experiment support, not a raster grid",
                observation_kinds=["distribution", "numeric"],
                comparability="not_comparable",
                notes=NO_SAMPLE_BLOCKERS[body],
                metadata={
                    "verification_status": "blocked",
                    "allowed_sources": [
                        "official sample catalogues with sample IDs and measurement methods",
                        "PDS or mission instrument records with calibrated units and conditions",
                    ],
                    "forbidden_derivations": [
                        "DEM roughness or slope to grain size",
                        "land-cover bare fraction to regolith thickness",
                        "Earth SoilGrids values transferred to Moon or Mars",
                        "thermal inertia to bulk density without a verified physical model",
                    ],
                },
            )
        ]

    def inspect(self, source: SourceRecord) -> SourceMetadata:
        body = source.body
        return SourceMetadata(
            source_id=source.source_id,
            status="blocked",
            fields={
                "body": body,
                "verification_status": "blocked",
                "method_version": REGOLITH_METHOD_VERSION,
                "observation_states": ["no_coverage"],
            },
            blockers=[NO_SAMPLE_BLOCKERS[body]],
        )

    def plan(self, query: SourceQuery, areas: list[PlanArea]) -> list[AcquisitionTask]:
        # Deliberately no tasks: there is no verified source to acquire.
        return []

    def acquire(self, task: AcquisitionTask, cache_dir: Path) -> LocalAsset:
        raise AcquisitionError(
            "sparse regolith provider has no verified acquisition target; refusing to fetch"
        )

    def process(self, asset: LocalAsset, task: AcquisitionTask) -> list[TypedObservation]:
        raise AcquisitionError(
            "sparse regolith provider cannot process values without a verified measurement"
        )

    def validate(self, observations: list[TypedObservation]) -> ValidationReport:
        return ValidationReport.from_issues(len(observations), [])
