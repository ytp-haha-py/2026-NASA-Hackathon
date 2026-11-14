from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Meta(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Dataset(Base):
    __tablename__ = "datasets"
    dataset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(256))
    product_id: Mapped[str] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    license: Mapped[str] = mapped_column(String(256))
    license_url: Mapped[str] = mapped_column(Text)
    access_timestamp: Mapped[str] = mapped_column(String(64))
    source_crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    native_resolution_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    processing_version: Mapped[str] = mapped_column(String(64))
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Candidate(Base):
    __tablename__ = "candidates"
    location_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    west: Mapped[float] = mapped_column(Float)
    south: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    north: Mapped[float] = mapped_column(Float)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.dataset_id"))
    window_size_m: Mapped[float] = mapped_column(Float)
    analysis_resolution_m: Mapped[float] = mapped_column(Float)
    crs: Mapped[str] = mapped_column(String(64))
    valid_fraction: Mapped[float] = mapped_column(Float)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_kind: Mapped[str] = mapped_column(String(16))
    area_km2: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), default="indexed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    features: Mapped[list[FeatureObservation]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin"
    )
    context_observations: Mapped[list[ContextObservation]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin"
    )


class PlanetaryReference(Base):
    __tablename__ = "planetary_references"
    reference_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    body: Mapped[str] = mapped_column(String(8))
    display_name: Mapped[str] = mapped_column(String(256))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.dataset_id"))
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    west: Mapped[float | None] = mapped_column(Float, nullable=True)
    south: Mapped[float | None] = mapped_column(Float, nullable=True)
    east: Mapped[float | None] = mapped_column(Float, nullable=True)
    north: Mapped[float | None] = mapped_column(Float, nullable=True)
    window_size_m: Mapped[float] = mapped_column(Float)
    analysis_resolution_m: Mapped[float] = mapped_column(Float)
    crs: Mapped[str] = mapped_column(String(64))
    valid_fraction: Mapped[float] = mapped_column(Float)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    features: Mapped[list[ReferenceFeatureObservation]] = relationship(
        back_populates="reference", cascade="all, delete-orphan", lazy="selectin"
    )


class ContextObservation(Base):
    __tablename__ = "context_observations"
    __table_args__ = (UniqueConstraint("candidate_id", "feature_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.location_id"))
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.dataset_id"), nullable=True)
    feature_id: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    method_version: Mapped[str] = mapped_column(String(64))
    source_resolution_m: Mapped[float] = mapped_column(Float)
    valid_fraction: Mapped[float] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(String(16))
    class_code: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(128))
    candidate: Mapped[Candidate] = relationship(back_populates="context_observations")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True, index=True
    )
    area_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_json: Mapped[dict] = mapped_column(JSON)
    provider_status: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeatureObservation(Base):
    __tablename__ = "feature_observations"
    __table_args__ = (UniqueConstraint("candidate_id", "feature_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.location_id"))
    feature_id: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    method_version: Mapped[str] = mapped_column(String(64))
    extraction_scale_m: Mapped[float] = mapped_column(Float)
    valid_fraction: Mapped[float] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(String(16))
    candidate: Mapped[Candidate] = relationship(back_populates="features")


class ReferenceFeatureObservation(Base):
    __tablename__ = "reference_feature_observations"
    __table_args__ = (UniqueConstraint("reference_id", "feature_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_id: Mapped[str] = mapped_column(ForeignKey("planetary_references.reference_id"))
    feature_id: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    method_version: Mapped[str] = mapped_column(String(64))
    extraction_scale_m: Mapped[float] = mapped_column(Float)
    valid_fraction: Mapped[float] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(String(16))
    reference: Mapped[PlanetaryReference] = relationship(back_populates="features")
