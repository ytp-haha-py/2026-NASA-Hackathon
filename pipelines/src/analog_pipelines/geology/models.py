"""Portable geology manifest. Unknown observations always retain null values."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pyproj import CRS

Body = Literal["Earth", "Moon", "Mars"]
State = Literal[
    "detected", "not_detected_under_valid_observation", "no_coverage", "unknown", "not_applicable"
]
Category = Literal["igneous", "impact", "sedimentary", "aqueous_alteration"]
Fraction = float


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def validate_body_crs(body: str, crs: str) -> CRS:
    parsed = CRS.from_user_input(crs)
    radius = parsed.ellipsoid.semi_major_metre
    expected = {"Earth": 6378137, "Moon": 1737400, "Mars": 3396190}[body]
    if not math.isclose(radius, expected, rel_tol=0.001):
        raise ValueError(f"CRS ellipsoid does not belong to {body}: {radius}")
    return parsed


def _ring_key(ring: list) -> tuple:
    points = [tuple(float(x) for x in point) for point in ring]
    if len(points) < 4 or points[0] != points[-1]:
        raise ValueError("polygon rings must be closed and contain at least four positions")
    if any(len(p) != 2 or not all(math.isfinite(x) for x in p) for p in points):
        raise ValueError("geometry requires finite two-dimensional positions")
    points = points[:-1]
    if len(set(points)) < 3:
        raise ValueError("degenerate polygon ring")
    # Ring start and winding have no effect on identity. No coordinate rounding.
    return min(
        tuple(seq[i:] + seq[:i]) for seq in (points, points[::-1]) for i in range(len(points))
    )


def geometry_fingerprint(body: str, crs: str, geometry: dict) -> str:
    parsed = validate_body_crs(body, crs)
    kind = geometry.get("type")
    if kind not in {"Polygon", "MultiPolygon"}:
        raise ValueError("AOI must be Polygon or MultiPolygon")
    polygons = [geometry["coordinates"]] if kind == "Polygon" else geometry["coordinates"]
    normalized = []
    if not polygons:
        raise ValueError("empty geometry")
    for polygon in polygons:
        if not polygon:
            raise ValueError("empty polygon")
        shell = _ring_key(polygon[0])
        holes = sorted(_ring_key(r) for r in polygon[1:])
        if parsed.is_geographic:
            for ring in polygon:
                if any(not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90) for p in ring):
                    raise ValueError("use east-positive -180..180 longitude and -90..90 latitude")
                if any(abs(a[0] - b[0]) > 180 for a, b in zip(ring, ring[1:], strict=False)):
                    raise ValueError("split antimeridian-crossing polygons before ingestion")
        normalized.append((shell, holes))
    return digest({"body": body, "crs": parsed.to_json_dict(), "polygons": sorted(normalized)})


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Area(Model):
    area_id: str = Field(min_length=1)
    display_name: str
    body: Body
    crs: str
    geometry: dict
    geometry_fingerprint: str = ""
    origin: Literal["existing", "external", "test_aoi"]
    geometry_source: str
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity(self) -> Area:
        actual = geometry_fingerprint(self.body, self.crs, self.geometry)
        if self.geometry_fingerprint and self.geometry_fingerprint != actual:
            raise ValueError("geometry fingerprint mismatch")
        self.geometry_fingerprint = actual
        return self


def validate_areas(areas: list[Area]) -> list[Area]:
    identities: dict[str, str] = {}
    for area in areas:
        if area.area_id in identities:
            raise ValueError(f"duplicate Area ID: {area.area_id}; do not replace geometry silently")
        identities[area.area_id] = area.geometry_fingerprint
    return areas


class Dataset(Model):
    dataset_id: str
    body: Body
    provider: str
    product: str
    version: str
    source_url: str
    license: str
    license_url: str
    redistribution: Literal["permitted", "not_verified"]
    citation: str
    source_crs: str | None
    native_resolution_m: float | None = Field(default=None, gt=0)
    map_scale: int | None = Field(default=None, gt=0)
    limitations: list[str]


class Artifact(Model):
    role: str
    source_url: str
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    provider_checksum: str | None = None


class Lithology(Model):
    evidence_id: str
    source_feature_id: str
    unit_id: str
    unit_name: str
    official_description: str
    original_attributes: dict
    unified_rocks: list[str]
    overlap_fraction: float = Field(gt=0, le=1)
    interpretation: str


class Mineral(Model):
    evidence_id: str
    mineral: str
    state: State
    value: float | None = None
    unit: str | None = None
    measurement: Literal[
        "identification", "band_depth", "spectral_parameter", "model_abundance", "unverified"
    ]
    semantics_id: str | None = None
    method: str
    valid_fraction: float | None = Field(default=None, ge=0, le=1)
    uncertainty: float | None = Field(default=None, ge=0)
    uncertainty_unit: str | None = None
    quality: Literal["verified", "context_only", "unverified"]
    limitations: list[str]

    @model_validator(mode="after")
    def valid_observation(self) -> Mineral:
        valid = self.state in {"detected", "not_detected_under_valid_observation"}
        if not valid and any(v is not None for v in [self.value, self.uncertainty]):
            raise ValueError("unobserved states must have null values/uncertainty")
        if valid and (self.valid_fraction is None or self.valid_fraction <= 0):
            raise ValueError("detection/non-detection requires positive valid coverage")
        if self.value is not None and (not self.unit or not self.semantics_id):
            raise ValueError("numeric observations require units and explicit semantics")
        if self.uncertainty is not None and not self.uncertainty_unit:
            raise ValueError("uncertainty requires its own unit")
        if self.measurement in {"band_depth", "spectral_parameter"} and self.unit in {
            "wt%",
            "weight_percent",
            "mass_fraction",
        }:
            raise ValueError("spectral measurements are not mineral mass fractions")
        if self.state == "not_detected_under_valid_observation" and self.value is not None:
            raise ValueError("non-detection is categorical, never a numeric zero")
        return self


class Process(Model):
    category: Category
    process: str
    evidence_ids: list[str] = Field(min_length=1)
    supporting_statement: str
    basis: Literal["mapped_geology", "documented_interpretation"]
    confidence: Literal["mapped", "interpreted"]


class Context(Model):
    evidence_id: str
    name: str
    value: float | None = None
    unit: str | None = None
    valid_fraction: float | None = Field(default=None, ge=0, le=1)
    method: str
    meaning: str
    limitations: list[str]


class Analysis(Model):
    area_id: str
    geometry_fingerprint: str
    body: Body
    dataset_id: str
    status: Literal[
        "success", "partial", "no_coverage", "failed", "auth_required", "unverified", "context_only"
    ]
    reason: str | None = None
    accessed_at: datetime
    processing_method: str
    source_crs: str | None = None
    native_resolution_m: float | None = Field(default=None, gt=0)
    map_scale: int | None = Field(default=None, gt=0)
    analysis_crs: str | None
    valid_fraction: float | None = Field(default=None, ge=0, le=1)
    artifacts: list[Artifact]
    lithology: list[Lithology] = Field(default_factory=list)
    mineralogy: list[Mineral] = Field(default_factory=list)
    geological_processes: list[Process] = Field(default_factory=list)
    geological_context: list[Context] = Field(default_factory=list)
    quality_flags: list[str]
    limitations: list[str]

    @model_validator(mode="after")
    def evidence_links(self) -> Analysis:
        ids = [v.evidence_id for v in [*self.lithology, *self.mineralogy]]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence IDs")
        geological_ids = {v.evidence_id for v in self.lithology}
        if any(not set(p.evidence_ids) <= geological_ids for p in self.geological_processes):
            raise ValueError(
                "process classification requires mapped/documented geological evidence"
            )
        if self.status in {"failed", "auth_required", "no_coverage", "unverified"} and (
            self.lithology
            or self.geological_processes
            or any(
                m.state in {"detected", "not_detected_under_valid_observation"}
                for m in self.mineralogy
            )
        ):
            raise ValueError("unavailable provider cannot contain observed evidence")
        return self


class Manifest(Model):
    schema_version: Literal["geology-manifest-1.0.0"] = "geology-manifest-1.0.0"
    processing_version: Literal["geology-1.0.0"] = "geology-1.0.0"
    generated_at: datetime
    data_kind: Literal["source_derived", "synthetic_test_only"]
    scope: str
    areas: list[Area]
    datasets: list[Dataset]
    analyses: list[Analysis]
    limitations: list[str]

    @model_validator(mode="after")
    def references(self) -> Manifest:
        validate_areas(self.areas)
        areas = {a.area_id: a for a in self.areas}
        datasets = {d.dataset_id: d for d in self.datasets}
        if len(datasets) != len(self.datasets):
            raise ValueError("duplicate dataset ID")
        keys = set()
        for record in self.analyses:
            if record.area_id not in areas or record.dataset_id not in datasets:
                raise ValueError("analysis references an unknown area or dataset")
            area, dataset = areas[record.area_id], datasets[record.dataset_id]
            if record.body != area.body or record.body != dataset.body:
                raise ValueError("body mismatch")
            if record.geometry_fingerprint != area.geometry_fingerprint:
                raise ValueError("analysis geometry fingerprint mismatch")
            key = (record.area_id, record.dataset_id)
            if key in keys:
                raise ValueError("duplicate area/provider analysis")
            keys.add(key)
            if record.analysis_crs:
                validate_body_crs(record.body, record.analysis_crs)
            if (
                record.lithology
                or record.geological_context
                or any(m.value is not None for m in record.mineralogy)
            ) and (dataset.redistribution != "permitted"):
                raise ValueError(
                    "unverified redistribution rights: keep extracted values outside Git"
                )
        return self
