"""Typed, serializable contracts for programmatic source acquisition.

These models describe plans, tasks, local assets and checkpoints. They are not
scientific observations; processed outputs use ``typed_contracts.TypedObservation``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Body = Literal["Earth", "Moon", "Mars"]
DimensionCode = Literal["D01", "D02", "D03", "D04", "D05", "D06"]
PLAN_VERSION = "acquisition-plan-1.0.0"
CHECKPOINT_VERSION = "acquisition-checkpoint-1.0.0"


class SourceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Body = "Earth"
    dimension: DimensionCode
    area_ids: list[str] = Field(default_factory=list)
    start: str | None = None
    end: str | None = None
    parameters: dict[str, str] = Field(default_factory=dict)


class PlanArea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    area_id: str
    display_name: str | None = None
    body: Body
    bbox: dict[str, float]
    crs: str = "EPSG:4326"
    coordinate_frame: str = "earth_wgs84"
    notes: str | None = None


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    provider_id: str
    body: Body
    dimension: DimensionCode
    product: str
    version: str
    endpoint: str
    license: str
    license_url: str
    native_resolution: str
    observation_kinds: list[str] = Field(min_length=1)
    comparability: Literal[
        "comparable", "context_only", "evidence_only", "not_comparable", "unknown"
    ]
    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    checked_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: Literal["metadata_verified", "accessible", "blocked", "unknown"]
    fields: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)


class AcquisitionTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    provider_id: str
    source_id: str
    body: Body
    dimension: DimensionCode
    area: PlanArea
    endpoint: str
    parameters: dict[str, str] = Field(default_factory=dict)
    method_version: str
    dataset_id: str | None = None
    expected_bytes: int | None = Field(default=None, ge=0)
    output_suffix: str = ".json"

    @model_validator(mode="after")
    def safe_task_id(self) -> AcquisitionTask:
        if not self.task_id or "/" in self.task_id or "\\" in self.task_id:
            raise ValueError("task_id must be a path-safe non-empty string")
        if not self.output_suffix.startswith("."):
            raise ValueError("output_suffix must start with '.'")
        return self

    def fingerprint(self) -> str:
        payload = {
            "task_id": self.task_id,
            "source_id": self.source_id,
            "area_id": self.area.area_id,
            "body": self.body,
            "dimension": self.dimension,
            "endpoint": self.endpoint,
            "parameters": self.parameters,
            "method_version": self.method_version,
            "dataset_id": self.dataset_id,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class LocalAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    path: str
    sha256: str
    bytes: int = Field(ge=0)
    content_type: str = "application/octet-stream"
    fetched_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    severity: Literal["error", "warning", "info"] = "error"
    observation_id: str | None = None


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    checked: int
    issues: list[ValidationIssue] = Field(default_factory=list)

    @classmethod
    def from_issues(cls, checked: int, issues: list[ValidationIssue]) -> ValidationReport:
        return cls(
            ok=not any(issue.severity == "error" for issue in issues),
            checked=checked,
            issues=issues,
        )


class AcquisitionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_version: str = PLAN_VERSION
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    scope: str
    tasks: list[AcquisitionTask]
    totals: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class TaskCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checkpoint_version: str = CHECKPOINT_VERSION
    task_id: str
    fingerprint: str
    status: Literal["pending", "succeeded", "failed", "unavailable"]
    asset: LocalAsset | None = None
    observations_path: str | None = None
    error: str | None = None
    attempts: int = 0
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_json(path: Path, payload: Any) -> None:
    atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
