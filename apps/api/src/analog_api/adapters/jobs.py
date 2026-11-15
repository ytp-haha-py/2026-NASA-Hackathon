from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from analog_api.adapters.aoi_store import persist_aoi_result
from analog_api.adapters.db import AnalysisJob
from analog_api.adapters.planetary_store import persist_planetary_aoi_result
from analog_api.domain.aoi import AoiProcessor, ValidatedAoi
from analog_api.domain.entities import BBox
from analog_api.domain.errors import AppError

logger = logging.getLogger("analog_api.jobs")


class JobStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_or_get(
        self,
        *,
        cache_key: str,
        idempotency_key: str | None,
        request_payload: dict[str, Any],
    ) -> tuple[AnalysisJob, bool]:
        if idempotency_key:
            existing = self.session.scalar(
                select(AnalysisJob).where(AnalysisJob.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return existing, False
        existing = self.session.scalar(
            select(AnalysisJob).where(AnalysisJob.cache_key == cache_key)
        )
        if existing is not None:
            if existing.status == "failed":
                existing.status = "queued"
                existing.error_code = None
                existing.error_message = None
                existing.started_at = None
                existing.finished_at = None
                self.session.commit()
                return existing, True
            return existing, False

        job = AnalysisJob(
            job_id=uuid.uuid4().hex,
            status="queued",
            cache_key=cache_key,
            idempotency_key=idempotency_key,
            request_json=request_payload,
            provider_status={},
        )
        self.session.add(job)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.scalar(
                select(AnalysisJob).where(AnalysisJob.cache_key == cache_key)
            )
            if existing is None:
                raise
            return existing, False
        self.session.refresh(job)
        return job, True


class JobRunner:
    """Minimal durable local queue: SQLite job rows + a bounded thread pool."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        processor: AoiProcessor,
        max_workers: int = 2,
        inline: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.processor = processor
        self.inline = inline
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aoi-job")

    def submit(self, job_id: str) -> None:
        if self.inline:
            self.run(job_id)
        else:
            self.executor.submit(self.run, job_id)

    def run(self, job_id: str) -> None:
        with self.session_factory() as session:
            job = session.get(AnalysisJob, job_id)
            if job is None:
                return
            if job.status not in {"queued", "running"}:
                return
            job.status = "running"
            job.started_at = datetime.now(UTC)
            session.commit()
            request = job.request_json
            try:
                aoi = ValidatedAoi(
                    body=request["body"],
                    bbox=BBox(**request["bbox"]),
                    geometry=request.get("geometry"),
                    area_km2=request["area_km2"],
                    requested_features=tuple(request["requested_features"]),
                    reference_id=request.get("reference_id"),
                    idempotency_key=job.idempotency_key,
                    area_id=request["area_id"],
                    cache_key=request["cache_key"],
                    width_m=request["width_m"],
                    height_m=request["height_m"],
                )
                result = self.processor.analyze(aoi)
                if result.body == "Earth":
                    data_version = persist_aoi_result(session, result)
                else:
                    data_version = persist_planetary_aoi_result(session, result)
                job.area_id = result.area_id
                job.provider_status = result.provider_status
                failed_providers = [
                    name
                    for name, status in result.provider_status.items()
                    if str(status.get("status", "")).lower()
                    not in {"ok", "verified", "verified_committed_fixture", "observed"}
                    and not str(status.get("status", "")).lower().startswith("verified")
                ]
                job.status = "partial" if failed_providers else "completed"
                result_json = {
                    "area_id": result.area_id,
                    "location_id": result.area_id,
                    "body": result.body,
                    "coordinate_frame": result.coordinate_frame,
                    "method_version": result.method_version,
                    "analysis_kind": result.analysis_kind,
                    "coverage_note": result.coverage_note,
                    "data_version": data_version,
                    "feature_count": len(result.features),
                    "context_feature_count": len(result.context_observations),
                    "features": dict(result.features),
                    "dimension_support": result.dimension_support,
                    "provider_status": result.provider_status,
                    "sources": [
                        {
                            "dataset_id": dataset.dataset_id,
                            "provider": dataset.provider,
                            "product_id": dataset.product_id,
                            "url": dataset.url,
                            "license": dataset.license,
                            "license_url": dataset.license_url,
                            "source_kind": dataset.source_kind,
                            "source_crs": dataset.source_crs,
                            "native_resolution_m": dataset.native_resolution_m,
                        }
                        for dataset in result.datasets
                    ],
                    "warnings": list(result.warnings),
                    "area_km2": result.area_km2,
                    "bbox": result.bbox.as_dict()
                    if hasattr(result.bbox, "as_dict")
                    else result.bbox,
                    "valid_fraction": getattr(result, "valid_fraction", None),
                    "resolution_m": getattr(result, "resolution_m", None),
                    "window_size_m": getattr(result, "window_size_m", None),
                    "geometry": getattr(result, "geometry", None),
                }
                # Preserve explicit pixel/coverage accounting when analyzers provide it.
                for extra_key in (
                    "analysis_bbox",
                    "analysis_area_km2",
                    "requested_bbox",
                    "cell_results",
                    "cells",
                    "source_resolution_m",
                ):
                    extra = getattr(result, extra_key, None)
                    if extra is not None:
                        result_json[extra_key] = extra
                # Fall back to dimension_support-embedded accounting hints.
                d01_support = (result.dimension_support or {}).get("D01") or {}
                if d01_support.get("pixel_accounting") and "pixel_accounting" not in result_json:
                    result_json["pixel_accounting"] = d01_support["pixel_accounting"]
                job.result_json = result_json
                job.error_code = None
                job.error_message = None
            except AppError as exc:
                session.rollback()
                job = session.get(AnalysisJob, job_id)
                if job is not None:
                    job.status = "failed"
                    job.error_code = exc.code
                    job.error_message = exc.message
                    job.provider_status = {"analysis": {"status": "failed", "reason": exc.code}}
            except Exception as exc:  # noqa: BLE001 - boundary must not crash the worker
                logger.exception("AOI job failed", exc_info=exc)
                session.rollback()
                job = session.get(AnalysisJob, job_id)
                if job is not None:
                    job.status = "failed"
                    job.error_code = "analysis_failed"
                    job.error_message = (
                        "The AOI analysis failed. Source errors are recorded in provider "
                        "status; retry or inspect the server log."
                    )
                    job.provider_status = {
                        "analysis": {"status": "failed", "reason": type(exc).__name__}
                    }
            finally:
                if job is not None:
                    job.finished_at = datetime.now(UTC)
                    session.commit()

    def recover(self) -> int:
        with self.session_factory() as session:
            session.execute(
                update(AnalysisJob).where(AnalysisJob.status == "running").values(status="queued")
            )
            session.commit()
            queued = list(
                session.scalars(select(AnalysisJob).where(AnalysisJob.status == "queued")).all()
            )
            for job in queued:
                self.submit(job.job_id)
            return len(queued)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
