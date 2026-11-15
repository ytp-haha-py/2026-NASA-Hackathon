"""Additive typed-v2 persistence for six-dimension observations (Issue #47).

New tables are created alongside the legacy SQLite schema; no legacy column is
renamed or dropped. Only verified typed observations may be inserted. Fixture rows
are explicitly synthetic and never presented as real coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from analog_pipelines.typed_contracts import (
    AreaV2,
    ProcessingRunV2,
    SourceAssetV2,
    TypedObservation,
)
from sqlalchemy import DateTime, Float, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import JSON

from analog_api.adapters.db import Base


class TypedSourceAsset(Base):
    __tablename__ = "typed_source_assets"
    asset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(128))
    uri: Mapped[str] = mapped_column(Text)
    checksum_algorithm: Mapped[str] = mapped_column(String(16))
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checksum_scope: Mapped[str] = mapped_column(String(16))
    native_crs: Mapped[str | None] = mapped_column(String(256), nullable=True)
    native_units: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scale: Mapped[float | None] = mapped_column(Float, nullable=True)
    offset: Mapped[float | None] = mapped_column(Float, nullable=True)
    nodata: Mapped[float | None] = mapped_column(Float, nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redistribution_allowed: Mapped[str] = mapped_column(String(16))


class TypedArea(Base):
    __tablename__ = "typed_areas"
    area_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    body: Mapped[str] = mapped_column(String(8))
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    crs: Mapped[str] = mapped_column(String(256))
    coordinate_frame: Mapped[str] = mapped_column(String(32))
    area_km2: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class TypedObservationRow(Base):
    __tablename__ = "typed_observations"
    observation_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    area_id: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(String(8))
    dimension_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    feature_id: Mapped[str] = mapped_column(String(128), index=True)
    value_kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32))
    value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uncertainty: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    spatial_support: Mapped[dict] = mapped_column(JSON)
    temporal_support: Mapped[dict] = mapped_column(JSON)
    depth_support: Mapped[dict] = mapped_column(JSON)
    method_version: Mapped[str] = mapped_column(String(128))
    dataset_ids: Mapped[list] = mapped_column(JSON)
    source_asset_ids: Mapped[list] = mapped_column(JSON)
    quality_flags: Mapped[list] = mapped_column(JSON)
    score_role: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_version: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TypedProcessingRun(Base):
    __tablename__ = "typed_processing_runs"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TypedObservationStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_asset(self, asset: SourceAssetV2) -> None:
        self.session.merge(
            TypedSourceAsset(
                asset_id=asset.asset_id,
                dataset_id=asset.dataset_id,
                uri=asset.uri,
                checksum_algorithm=asset.checksum_algorithm,
                checksum=asset.checksum,
                checksum_scope=asset.checksum_scope,
                native_crs=asset.native_crs,
                native_units=asset.native_units,
                scale=asset.scale,
                offset=asset.offset,
                nodata=asset.nodata,
                bytes=asset.bytes,
                fetched_at=asset.fetched_at,
                redistribution_allowed=asset.redistribution_allowed,
            )
        )

    def upsert_area(self, area: AreaV2) -> None:
        self.session.merge(
            TypedArea(
                area_id=area.area_id,
                body=area.body,
                geometry=area.geometry,
                bbox=area.bbox,
                crs=area.crs,
                coordinate_frame=area.coordinate_frame,
                area_km2=area.area_km2,
                notes=area.notes,
            )
        )

    def upsert_observation(self, observation: TypedObservation) -> None:
        payload = observation.model_dump(mode="json")
        self.session.merge(
            TypedObservationRow(
                observation_id=observation.observation_id,
                area_id=observation.area_id,
                body=observation.body,
                dimension_code=observation.dimension_code,
                feature_id=observation.feature_id,
                value_kind=observation.value_kind,
                state=observation.state,
                value=payload.get("value"),
                unit=observation.unit,
                uncertainty=payload.get("uncertainty"),
                spatial_support=payload["spatial_support"],
                temporal_support=payload["temporal_support"],
                depth_support=payload["depth_support"],
                method_version=observation.method_version,
                dataset_ids=list(observation.dataset_ids),
                source_asset_ids=list(observation.source_asset_ids),
                quality_flags=list(observation.quality_flags),
                score_role=observation.score_role,
                evidence=payload.get("evidence"),
                notes=observation.notes,
                schema_version=observation.schema_version,
            )
        )

    def upsert_run(self, run: ProcessingRunV2) -> None:
        self.session.merge(
            TypedProcessingRun(
                run_id=run.run_id,
                status=run.status,
                payload=run.model_dump(mode="json"),
            )
        )

    def list_observations(
        self,
        *,
        dimension: str | None = None,
        body: str | None = None,
        feature_id: str | None = None,
        area_id: str | None = None,
        state: str | None = None,
        dataset_id: str | None = None,
        source_asset_id: str | None = None,
        min_depth_m: float | None = None,
        max_depth_m: float | None = None,
        temporal_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        query = select(TypedObservationRow)
        if area_id:
            query = query.where(TypedObservationRow.area_id == area_id)
        if dimension:
            query = query.where(TypedObservationRow.dimension_code == dimension)
        if body:
            query = query.where(TypedObservationRow.body == body)
        if feature_id:
            query = query.where(TypedObservationRow.feature_id == feature_id)
        if state:
            query = query.where(TypedObservationRow.state == state)
        rows = self.session.scalars(query.order_by(TypedObservationRow.observation_id)).all()
        result: list[dict[str, Any]] = []
        for row in rows:
            # JSON-backed fields are filtered in Python for cross-dialect compatibility.
            if dataset_id and dataset_id not in (row.dataset_ids or []):
                continue
            if source_asset_id and source_asset_id not in (row.source_asset_ids or []):
                continue
            if temporal_kind and (row.temporal_support or {}).get("kind") != temporal_kind:
                continue
            depth = row.depth_support or {}
            row_min = depth.get("min_m")
            row_max = depth.get("max_m")
            if min_depth_m is not None and (row_max is None or float(row_max) < min_depth_m):
                continue
            if max_depth_m is not None and (row_min is None or float(row_min) > max_depth_m):
                continue
            result.append(
                {
                    "observation_id": row.observation_id,
                    "area_id": row.area_id,
                    "body": row.body,
                    "dimension_code": row.dimension_code,
                    "feature_id": row.feature_id,
                    "value_kind": row.value_kind,
                    "state": row.state,
                    "value": row.value,
                    "unit": row.unit,
                    "method_version": row.method_version,
                    "dataset_ids": row.dataset_ids or [],
                    "source_asset_ids": row.source_asset_ids or [],
                    "quality_flags": row.quality_flags or [],
                    "score_role": row.score_role,
                    "spatial_support": row.spatial_support or {},
                    "temporal_support": row.temporal_support or {},
                    "depth_support": row.depth_support or {},
                    "uncertainty": row.uncertainty,
                    "notes": row.notes,
                }
            )
        return result

    def coverage(self) -> dict[str, Any]:
        rows = self.session.execute(
            select(
                TypedObservationRow.dimension_code,
                TypedObservationRow.body,
                TypedObservationRow.state,
                func.count().label("count"),
            ).group_by(
                TypedObservationRow.dimension_code,
                TypedObservationRow.body,
                TypedObservationRow.state,
            )
        ).all()
        by_dimension: dict[str, dict[str, Any]] = {}
        for dimension, body, state, count in rows:
            key = dimension or "context"
            entry = by_dimension.setdefault(key, {"by_body": {}, "by_state": {}, "total": 0})
            entry["by_body"][body] = entry["by_body"].get(body, 0) + count
            entry["by_state"][state] = entry["by_state"].get(state, 0) + count
            entry["total"] += count
        return {
            "total": sum(item["total"] for item in by_dimension.values()),
            "by_dimension": dict(sorted(by_dimension.items())),
            "scope_note": (
                "Counts cover typed-v2 records in this database only. Evidence/context records "
                "are not scored similarity features and missing sources are not zero-filled."
            ),
        }

    def get_area(self, area_id: str) -> dict[str, Any] | None:
        row = self.session.get(TypedArea, area_id)
        if row is None:
            return None
        return self._area_payload(row, include_observations=True)

    def list_areas(self, *, body: str | None = None) -> list[dict[str, Any]]:
        query = select(TypedArea)
        if body:
            query = query.where(TypedArea.body == body)
        rows = self.session.scalars(query.order_by(TypedArea.area_id)).all()
        return [self._area_payload(row, include_observations=False) for row in rows]

    def _area_payload(self, row: TypedArea, *, include_observations: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "area_id": row.area_id,
            "body": row.body,
            "bbox": row.bbox,
            "geometry": row.geometry,
            "crs": row.crs,
            "coordinate_frame": row.coordinate_frame,
            "area_km2": row.area_km2,
            "notes": row.notes,
        }
        if include_observations:
            payload["observations"] = self.list_observations(area_id=row.area_id)
        return payload


def init_typed_db(engine) -> None:
    """Create additive tables; safe on an existing legacy database."""
    Base.metadata.create_all(engine)
