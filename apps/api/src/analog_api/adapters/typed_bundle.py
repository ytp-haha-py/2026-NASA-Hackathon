"""Import a portable typed-v2 bundle into the additive SQLite store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analog_pipelines.typed_contracts import AreaV2, SourceAssetV2, TypedObservation
from sqlalchemy.orm import Session

from analog_api.adapters.typed_db import TypedObservationStore


def import_typed_bundle(session: Session, bundle_dir: str | Path) -> dict[str, Any]:
    root = Path(bundle_dir)
    areas = json.loads((root / "areas.json").read_text(encoding="utf-8"))
    assets = json.loads((root / "assets.json").read_text(encoding="utf-8"))
    observations = [
        TypedObservation.model_validate(json.loads(line))
        for line in (root / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    store = TypedObservationStore(session)
    for raw in assets:
        allowed = {key: value for key, value in raw.items() if key in SourceAssetV2.model_fields}
        store.upsert_asset(SourceAssetV2.model_validate(allowed))
    for raw in areas:
        store.upsert_area(AreaV2.model_validate(raw))
    for observation in observations:
        store.upsert_observation(observation)
    return {
        "areas": len(areas),
        "assets": len(assets),
        "observations": len(observations),
        "dimensions": sorted(
            {
                observation.dimension_code
                for observation in observations
                if observation.dimension_code
            }
        ),
    }
