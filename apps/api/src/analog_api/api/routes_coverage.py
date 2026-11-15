"""Arbitrary-location D01-D06 coverage/status contract (Issue #141)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import get_repo, get_session
from analog_api.domain.coverage_status import build_location_coverage
from analog_api.ports.repository import CandidateRepositoryFull

router = APIRouter(prefix="/api/v1/coverage", tags=["coverage"])


@router.get("/location")
def location_coverage(
    body: str = Query(..., description="Earth, Moon or Mars"),
    lat: float | None = Query(default=None),
    lon: float | None = Query(default=None),
    west: float | None = Query(default=None),
    south: float | None = Query(default=None),
    east: float | None = Query(default=None),
    north: float | None = Query(default=None),
    bbox_lon_convention: str = Query(default="signed"),
    session: Session = Depends(get_session),
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, Any]:
    bbox = None
    if any(value is not None for value in (west, south, east, north)):
        if not all(value is not None for value in (west, south, east, north)):
            from analog_api.domain.errors import AppError

            raise AppError(
                "validation_error",
                "bbox requires west, south, east and north.",
                status_code=422,
            )
        bbox = {"west": west, "south": south, "east": east, "north": north}
    return build_location_coverage(
        repo=repo,
        typed_store=TypedObservationStore(session),
        body=body,
        lat=lat,
        lon=lon,
        bbox=bbox,
        bbox_lon_convention=bbox_lon_convention,
    )
