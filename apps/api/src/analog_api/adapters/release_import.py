"""Import a portable acquisition release into the additive typed-v2 store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analog_pipelines.acquisition.models import AcquisitionPlan
from analog_pipelines.acquisition.release import verify_release_integrity
from analog_pipelines.typed_contracts import (
    AreaV2,
    ProcessingRunV2,
    SourceAssetV2,
    TypedObservation,
)
from sqlalchemy.orm import Session

from analog_api.adapters.typed_db import TypedObservationStore


def import_release(session: Session, release_dir: str | Path) -> dict[str, Any]:
    root = Path(release_dir)
    integrity = verify_release_integrity(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    plan = AcquisitionPlan.model_validate_json((root / "plan.json").read_text(encoding="utf-8"))
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
    areas = 0
    seen_areas: set[str] = set()
    for task in plan.tasks:
        area = task.area
        if area.area_id in seen_areas:
            continue
        seen_areas.add(area.area_id)
        if area.crs == "unknown":
            raise ValueError(
                f"release area {area.area_id} has no real CRS; refusing to fabricate one"
            )
        frame = (
            "earth_wgs84"
            if area.body == "Earth" and area.crs.upper() == "EPSG:4326"
            else area.coordinate_frame
        )
        store.upsert_area(
            AreaV2(
                area_id=area.area_id,
                body=area.body,
                bbox=area.bbox,
                crs=area.crs,
                coordinate_frame=frame,  # type: ignore[arg-type]
                notes=area.notes,
            )
        )
        areas += 1
    for observation in observations:
        store.upsert_observation(observation)
    method_versions = sorted({observation.method_version for observation in observations})
    run = ProcessingRunV2(
        run_id=f"release:{manifest.get('observations_sha256', 'unknown')[:16]}",
        started_at=manifest["generated_at"],
        finished_at=manifest["generated_at"],
        input_asset_ids=[asset["asset_id"] for asset in assets],
        input_checksums={
            asset["asset_id"]: asset["checksum"] for asset in assets if asset.get("checksum")
        },
        window_selection_rule=f"release scope {plan.scope}; existing Areas only",
        method_versions={version: version for version in method_versions},
        output_checksum=manifest.get("observations_sha256"),
        counts={
            "observations": len(observations),
            "assets": len(assets),
            "areas": areas,
        },
        status="completed",
        command="scripts/import_typed_release.py",
        warnings=list(manifest.get("notes", [])),
    )
    store.upsert_run(run)
    return {
        "areas": areas,
        "assets": len(assets),
        "observations": len(observations),
        "run_id": run.run_id,
        "release_version": manifest.get("release_version"),
        "integrity": integrity,
    }
