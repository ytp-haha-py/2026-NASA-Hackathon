"""Reusable provenance/QA validation around v2 typed records (Issue #27).

Validation never repairs a value. It returns explicit issues so a batch runner,
manifest validator or API can reject or flag a record with an audit trail.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Literal

from analog_pipelines.typed_contracts import (
    AreaV2,
    ProcessingRunV2,
    SourceAssetV2,
    TypedObservation,
)

Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class QAIssue:
    code: str
    message: str
    severity: Severity = "error"
    record_id: str | None = None


@dataclass(slots=True)
class QAResult:
    issues: list[QAIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[QAIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[QAIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, issue: QAIssue) -> None:
        self.issues.append(issue)

    def extend(self, issues: Iterable[QAIssue]) -> None:
        self.issues.extend(issues)


DIMENSION_UNITS: dict[str, set[str]] = {
    "D01": {"degree", "meter", "fraction", "m^-1", "dimensionless"},
    "D02": {"fraction", "percent", "category", "dimensionless", "detection"},
    "D03": {"K", "kelvin", "W/m^2", "J/m^2/K", "dimensionless"},
    "D04": {"fraction", "percent", "meter", "kg/m^3", "dimensionless", "detection"},
    "D05": {"degree", "W/m^2", "Gy", "Sv", "fraction", "dimensionless"},
    "D06": {"millimeter", "meter", "kg/m^3", "degree", "kPa", "fraction", "dimensionless"},
}


def validate_source_asset(asset: SourceAssetV2) -> QAResult:
    result = QAResult()
    if not asset.asset_id.strip():
        result.add(QAIssue("asset_id_empty", "asset_id is required", record_id=asset.asset_id))
    if not asset.uri.strip():
        result.add(QAIssue("asset_uri_empty", "asset URI is required", record_id=asset.asset_id))
    if asset.checksum_scope == "source" and asset.checksum_algorithm == "unknown":
        result.add(
            QAIssue(
                "source_checksum_missing",
                "source-scope asset requires a checksum algorithm",
                record_id=asset.asset_id,
            )
        )
    if asset.scale is not None and (not math.isfinite(asset.scale) or asset.scale == 0):
        result.add(
            QAIssue(
                "invalid_scale",
                "scale must be finite and non-zero",
                record_id=asset.asset_id,
            )
        )
    if asset.offset is not None and not math.isfinite(asset.offset):
        result.add(QAIssue("invalid_offset", "offset must be finite", record_id=asset.asset_id))
    if asset.native_crs and asset.native_crs == "unknown" and asset.checksum_scope == "source":
        result.add(
            QAIssue(
                "source_crs_unknown",
                "source asset has unknown CRS",
                severity="warning",
                record_id=asset.asset_id,
            )
        )
    return result


def validate_observation(
    observation: TypedObservation,
    *,
    ontology_mapping: dict[str, object] | None = None,
    known_asset_ids: set[str] | None = None,
) -> QAResult:
    result = QAResult()
    record_id = observation.observation_id
    if not set(observation.source_asset_ids):
        result.add(
            QAIssue(
                "source_assets_empty", "observation requires source assets", record_id=record_id
            )
        )
    if known_asset_ids is not None:
        missing = set(observation.source_asset_ids) - known_asset_ids
        if missing:
            result.add(
                QAIssue(
                    "source_asset_unknown",
                    f"observation references unknown assets: {sorted(missing)}",
                    record_id=record_id,
                )
            )
    if observation.dimension_code is not None:
        allowed = DIMENSION_UNITS.get(observation.dimension_code, set())
        if observation.unit and observation.unit not in allowed:
            result.add(
                QAIssue(
                    "unit_not_allowed",
                    f"unit {observation.unit!r} is not allowed for {observation.dimension_code}",
                    record_id=record_id,
                )
            )
    if (
        observation.state == "detected"
        and isinstance(observation.value, (int, float))
        and not isinstance(observation.value, bool)
        and not math.isfinite(float(observation.value))
    ):
        result.add(QAIssue("non_finite_value", "numeric value must be finite", record_id=record_id))
    if (
        observation.state in {"not_detected", "no_coverage", "unknown", "not_applicable"}
        and observation.value is not None
    ):
        result.add(
            QAIssue(
                "missing_state_has_value",
                f"{observation.state} must not carry a fabricated value",
                record_id=record_id,
            )
        )
    if observation.dimension_code == "D03" and (
        observation.temporal_support.kind == "unknown" and observation.value_kind == "timeseries"
    ):
        result.add(
            QAIssue(
                "thermal_time_support_missing",
                "D03 timeseries requires temporal support",
                record_id=record_id,
            )
        )
    if (
        observation.dimension_code == "D05"
        and observation.feature_id.startswith("illumination")
        and observation.unit in {"Gy", "Sv"}
    ):
        result.add(
            QAIssue(
                "illumination_radiation_conflation",
                "solar illumination cannot use dose units",
                record_id=record_id,
            )
        )
    if ontology_mapping is not None:
        expected_dimension = ontology_mapping.get("dimension_code")
        if expected_dimension != observation.dimension_code:
            result.add(
                QAIssue(
                    "ontology_mismatch",
                    f"ontology maps feature to {expected_dimension}, observation uses "
                    f"{observation.dimension_code}",
                    record_id=record_id,
                )
            )
    return result


def validate_processing_run(run: ProcessingRunV2) -> QAResult:
    result = QAResult()
    if not run.bounded:
        result.add(
            QAIssue("run_unbounded", "processing runs must be bounded", record_id=run.run_id)
        )
    if not run.input_asset_ids:
        result.add(
            QAIssue("run_no_inputs", "processing run requires input assets", record_id=run.run_id)
        )
    if not run.window_selection_rule.strip():
        result.add(
            QAIssue(
                "run_no_selection_rule", "window selection rule is required", record_id=run.run_id
            )
        )
    for key, value in run.counts.items():
        if value < 0:
            result.add(
                QAIssue(
                    "negative_count",
                    f"count {key} is negative",
                    record_id=run.run_id,
                )
            )
    attempts = run.counts.get("attempted")
    succeeded = run.counts.get("succeeded", 0)
    if attempts is not None and succeeded > attempts:
        result.add(
            QAIssue(
                "counts_inconsistent",
                "succeeded exceeds attempted",
                record_id=run.run_id,
            )
        )
    if run.status == "completed" and run.counts.get("failed", 0) > 0:
        result.add(
            QAIssue(
                "failed_count_in_completed",
                "completed run cannot contain failed records without partial status",
                record_id=run.run_id,
            )
        )
    if run.status == "completed" and not run.output_checksum:
        result.add(
            QAIssue(
                "run_no_output_checksum",
                "completed run requires output checksum",
                record_id=run.run_id,
            )
        )
    if run.status == "failed" and run.output_checksum:
        result.add(
            QAIssue(
                "failed_run_has_output",
                "failed run must not advertise a successful output",
                record_id=run.run_id,
            )
        )
    return result


def validate_area_overlaps(areas: Iterable[AreaV2]) -> QAResult:
    result = QAResult()
    seen: dict[str, AreaV2] = {}
    boxes: list[AreaV2] = []
    for area in areas:
        if area.area_id in seen:
            result.add(
                QAIssue(
                    "duplicate_area_id",
                    f"duplicate area_id {area.area_id}",
                    record_id=area.area_id,
                )
            )
        seen[area.area_id] = area
        if area.bbox:
            for other in boxes:
                if _bbox_overlap(area.bbox, other.bbox):  # type: ignore[arg-type]
                    result.add(
                        QAIssue(
                            "overlapping_area",
                            f"{area.area_id} overlaps {other.area_id}",
                            severity="warning",
                            record_id=area.area_id,
                        )
                    )
            boxes.append(area)
    return result


def _bbox_overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    return not (
        a["east"] <= b["west"]
        or b["east"] <= a["west"]
        or a["north"] <= b["south"]
        or b["north"] <= a["south"]
    )


def qa_report(issues: Iterable[QAIssue]) -> dict[str, object]:
    issues = list(issues)
    return {
        "ok": not any(issue.severity == "error" for issue in issues),
        "errors": [asdict(issue) for issue in issues if issue.severity == "error"],
        "warnings": [asdict(issue) for issue in issues if issue.severity == "warning"],
    }
