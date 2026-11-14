"""Build reproducible acquisition plans from versioned Areas and providers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml

from analog_pipelines.acquisition.models import (
    AcquisitionPlan,
    AcquisitionTask,
    PlanArea,
    SourceQuery,
    write_json,
)
from analog_pipelines.acquisition.providers import SourceProvider, default_providers

DEFAULT_AREAS = Path(__file__).resolve().parents[4] / "configs" / "areas.yaml"


def load_areas(
    path: str | Path = DEFAULT_AREAS,
    *,
    body: str | None = None,
    area_ids: Iterable[str] | None = None,
) -> list[PlanArea]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    wanted = set(area_ids or [])
    areas: list[PlanArea] = []
    for item in raw.get("areas", []):
        if body and item.get("body") != body:
            continue
        if wanted and item.get("area_id") not in wanted:
            continue
        if not item.get("bbox"):
            continue
        area_body = item.get("body", "Earth")
        areas.append(
            PlanArea(
                area_id=item["area_id"],
                display_name=item.get("display_name"),
                body=area_body,
                bbox=item["bbox"],
                crs="EPSG:4326" if area_body == "Earth" else "unknown",
                coordinate_frame=(
                    "earth_wgs84" if area_body == "Earth" else f"{area_body.lower()}_unknown"
                ),
                notes=item.get("notes"),
            )
        )
    if not areas:
        raise ValueError(f"no areas selected from {path}")
    return areas


def build_plan(
    *,
    scope: str,
    areas: list[PlanArea],
    dimensions: Iterable[str],
    bodies: Iterable[str],
    provider_ids: Iterable[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    parameters: dict[str, str] | None = None,
    providers: dict[str, SourceProvider] | None = None,
) -> AcquisitionPlan:
    registry = providers or default_providers()
    selected = list(provider_ids or registry)
    unknown = sorted(set(selected) - set(registry))
    if unknown:
        raise ValueError(f"unknown provider(s): {unknown}")
    tasks: list[AcquisitionTask] = []
    seen: set[str] = set()
    notes: list[str] = []
    for dimension in dimensions:
        for body in bodies:
            query = SourceQuery(
                body=body,
                dimension=dimension,  # type: ignore[arg-type]
                start=start,
                end=end,
                parameters=parameters or {},
            )
            body_areas = [area for area in areas if area.body == body]
            if not body_areas:
                continue
            for provider_id in selected:
                provider = registry[provider_id]
                sources = provider.discover(query)
                for source in sources:
                    provider.inspect(source)
                planned = provider.plan(query, body_areas)
                if not planned:
                    notes.append(
                        f"{provider_id} produced no tasks for {body} {dimension}; "
                        "source may be unavailable or outside its scope"
                    )
                for task in planned:
                    if task.task_id in seen:
                        continue
                    seen.add(task.task_id)
                    tasks.append(task)
    totals = {
        "tasks": len(tasks),
        "expected_bytes": sum(task.expected_bytes or 0 for task in tasks),
        "by_dimension": {
            dimension: sum(1 for task in tasks if task.dimension == dimension)
            for dimension in dimensions
        },
        "by_body": {body: sum(1 for task in tasks if task.body == body) for body in bodies},
    }
    return AcquisitionPlan(scope=scope, tasks=tasks, totals=totals, notes=notes)


def write_plan(plan: AcquisitionPlan, path: str | Path) -> Path:
    target = Path(path)
    write_json(target, plan.model_dump(mode="json"))
    return target
