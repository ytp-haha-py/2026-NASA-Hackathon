from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import get_session
from analog_api.domain.errors import AppError

router = APIRouter(prefix="/api/v1/typed")


@router.get("/observations")
def list_typed_observations(
    dimension: str | None = None,
    body: str | None = None,
    feature_id: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    store = TypedObservationStore(session)
    return store.list_observations(dimension=dimension, body=body, feature_id=feature_id)


@router.get("/areas/{area_id}")
def typed_area(area_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    store = TypedObservationStore(session)
    area = store.get_area(area_id)
    if area is None:
        raise AppError("not_found", f"Typed area {area_id} was not found.", status_code=404)
    return area
