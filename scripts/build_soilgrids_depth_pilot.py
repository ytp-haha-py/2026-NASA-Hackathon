#!/usr/bin/env python
"""Build the bounded multi-depth D06 SoilGrids pilot (Issue #46).

The pilot requests the same five Earth AOIs and five SoilGrids properties at the
four registered depth intervals (0-5, 5-15, 15-30 and 30-60 cm) using the public
ISRIC WCS endpoint. Values are model predictions, never measurements, and each
depth is a separate observation. Raw rasters are not committed.

Dry run first:

    uv run python scripts/build_soilgrids_depth_pilot.py --dry-run

Live bounded run (network, small subsets):

    uv run python scripts/build_soilgrids_depth_pilot.py
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from analog_pipelines.acquisition.executor import run_plan
from analog_pipelines.acquisition.models import (
    AcquisitionPlan,
    TaskCheckpoint,
    file_sha256,
    write_json,
)
from analog_pipelines.acquisition.planner import build_plan, load_areas, write_plan
from analog_pipelines.acquisition.providers_soilgrids import (
    SOILGRIDS_LICENSE,
    SOILGRIDS_LICENSE_URL,
    SOILGRIDS_MAX_ATTEMPTS,
    SOILGRIDS_MIN_REQUEST_INTERVAL_S,
    SOILGRIDS_NATIVE_RESOLUTION_M,
    SOILGRIDS_PRODUCT_VERSION,
    SOILGRIDS_PROPERTIES,
    SOILGRIDS_WCS_VERSION,
    depth_interval_m,
)
from analog_pipelines.acquisition.release import build_release, verify_release_integrity

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AREA_IDS = [
    "earth-atacama-desert",
    "earth-death-valley",
    "earth-lanzarote",
    "earth-devon-island",
    "earth-sahara-r1c1",
]
DEFAULT_PROPERTIES = "clay,sand,silt,soc,bdod"
DEFAULT_DEPTHS = "0-5cm,5-15cm,15-30cm,30-60cm"
DEFAULT_STATISTICS = "mean,Q0.05,Q0.95"
QA_VERSION = "d06-soilgrids-depth-qa-1.0.0"
REQUEST_LOG_VERSION = "d06-soilgrids-requests-1.0.0"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--areas", default=str(ROOT / "configs" / "areas.yaml"))
    parser.add_argument("--area-id", action="append", default=None)
    parser.add_argument("--properties", default=DEFAULT_PROPERTIES)
    parser.add_argument("--depths", default=DEFAULT_DEPTHS)
    parser.add_argument("--statistics", default=DEFAULT_STATISTICS)
    parser.add_argument("--scope", default="pilot-d06-earth-depths")
    parser.add_argument("--plan", default="var/plans/d06-earth-depths.json")
    parser.add_argument("--run-dir", default="var/runs/d06-earth-depths")
    parser.add_argument("--cache", default="var/cache/acquisition")
    parser.add_argument("--release", default="pipelines/pilots/d06_earth_soilgrids_depths")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--skip-release", action="store_true")
    return parser.parse_args(argv)


def _selected_area_ids(args: argparse.Namespace) -> list[str]:
    return list(args.area_id or DEFAULT_AREA_IDS)


def _make_plan(args: argparse.Namespace) -> AcquisitionPlan:
    areas = load_areas(args.areas, area_ids=_selected_area_ids(args))
    return build_plan(
        scope=args.scope,
        areas=areas,
        dimensions=["D06"],
        bodies=["Earth"],
        provider_ids=["soilgrids-wcs"],
        parameters={
            "properties": args.properties,
            "depths": args.depths,
            "statistics": args.statistics,
        },
    )


def _request_rows(run_dir: Path, plan: AcquisitionPlan) -> list[dict[str, Any]]:
    task_by_id = {task.task_id: task for task in plan.tasks}
    rows: list[dict[str, Any]] = []
    for path in sorted((run_dir / "checkpoints").glob("*.json")):
        checkpoint = TaskCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
        task = task_by_id.get(checkpoint.task_id)
        if task is None:
            continue
        parameters = task.parameters
        asset = checkpoint.asset
        rows.append(
            {
                "task_id": task.task_id,
                "area_id": task.area.area_id,
                "property": parameters.get("property"),
                "depth": parameters.get("depth"),
                "statistic": parameters.get("statistic"),
                "depth_interval_m": list(depth_interval_m(parameters["depth"])),
                "status": checkpoint.status,
                "http_status": asset.http_status if asset else None,
                "response_source": (
                    "cache" if asset and asset.http_status is None else "http" if asset else None
                ),
                "sha256": asset.sha256 if asset else None,
                "bytes": asset.bytes if asset else None,
                "content_type": asset.content_type if asset else None,
                "fetched_at": asset.fetched_at if asset else None,
                "error": checkpoint.error,
                "endpoint": task.endpoint,
                "method_version": task.method_version,
                "parameters": parameters,
            }
        )
    return rows


def _observation_rows(release_dir: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (release_dir / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _build_qa(observations: list[dict[str, Any]], requests: list[dict[str, Any]]) -> dict[str, Any]:
    by_group: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
    for observation in observations:
        area_id, property_id, depth, statistic = observation["observation_id"].split(":")[1:]
        group = by_group[(area_id, property_id, depth)]
        group.setdefault("unit", observation["unit"])
        group.setdefault("depth_min_m", observation["depth_support"]["min_m"])
        group.setdefault("depth_max_m", observation["depth_support"]["max_m"])
        group.setdefault("valid_fraction", observation["spatial_support"]["valid_fraction"])
        group[statistic] = observation["value"]
        group["state"] = observation["state"]
    groups: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for (area_id, property_id, depth), values in sorted(by_group.items()):
        mean = values.get("mean")
        q05 = values.get("q05")
        q95 = values.get("q95")
        entry = {
            "area_id": area_id,
            "property": property_id,
            "depth": depth,
            "unit": values["unit"],
            "depth_min_m": values["depth_min_m"],
            "depth_max_m": values["depth_max_m"],
            "state": values.get("state"),
            "mean": mean,
            "q05": q05,
            "q95": q95,
            "q95_minus_q05": (
                q95 - q05
                if isinstance(q05, (int, float)) and isinstance(q95, (int, float))
                else None
            ),
            "valid_fraction": values.get("valid_fraction"),
        }
        groups.append(entry)
        if entry["state"] != "detected" or None in (mean, q05, q95):
            missing.append({"area_id": area_id, "property": property_id, "depth": depth})
    expected = len({(row["area_id"], row["property"], row["depth"]) for row in requests})
    return {
        "qa_version": QA_VERSION,
        "source": "ISRIC SoilGrids 2.0 via WCS",
        "units": {key: value[0] for key, value in SOILGRIDS_PROPERTIES.items()},
        "groups": groups,
        "completeness": {
            "expected_groups": expected,
            "complete_groups": len(groups) - len(missing),
            "missing_groups": len(missing),
            "missing": missing,
        },
        "notes": [
            "Each group is one area/property/depth combination; depths are never pooled.",
            "q95_minus_q05 is the spread of SoilGrids prediction quantiles, not a confidence "
            "interval and not a validation error estimate.",
            "All values are model predictions, not local measurements.",
        ],
    }


def _rewrite_manifest(
    release_dir: Path,
    plan: AcquisitionPlan,
    requests: list[dict[str, Any]],
    qa: dict[str, Any],
) -> dict[str, Any]:
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    observations = _observation_rows(release_dir)
    status_counts = Counter(row["status"] for row in requests)
    depth_counts = Counter(row["depth"] for row in requests if row["status"] == "succeeded")
    property_counts = Counter(row["property"] for row in requests if row["status"] == "succeeded")
    failed = [
        {"task_id": row["task_id"], "status": row["status"], "error": row["error"]}
        for row in requests
        if row["status"] != "succeeded"
    ]
    manifest["source_provenance"] = {
        "provider_id": "soilgrids-wcs",
        "dataset_id": "soilgrids-250m-v2",
        "product": "ISRIC SoilGrids 2.0",
        "product_version": SOILGRIDS_PRODUCT_VERSION,
        "wcs_version": SOILGRIDS_WCS_VERSION,
        "endpoint": "https://maps.isric.org/mapserv",
        "access": "public, no credentials",
        "license": SOILGRIDS_LICENSE,
        "license_url": SOILGRIDS_LICENSE_URL,
        "redistribution_allowed": "yes",
        "attribution_required": True,
        "native_resolution_m": SOILGRIDS_NATIVE_RESOLUTION_M,
        "evidence_class": "model_prediction",
        "not_a_measurement": True,
        "request_parameters": {
            "properties": sorted({task.parameters["property"] for task in plan.tasks}),
            "depths": sorted({task.parameters["depth"] for task in plan.tasks}),
            "statistics": sorted({task.parameters["statistic"] for task in plan.tasks}),
            "subsetting_crs": "EPSG:4326",
            "format": "image/tiff",
        },
        "rate_limit": {
            "min_interval_s": SOILGRIDS_MIN_REQUEST_INTERVAL_S,
            "max_attempts": SOILGRIDS_MAX_ATTEMPTS,
            "backoff": "exponential, honours Retry-After, capped at 30 s",
        },
        "model_uncertainty": (
            "Q0.05 and Q0.95 are separate SoilGrids prediction-quantile records per "
            "area/property/depth. They are not a confidence interval for the AOI mean."
        ),
    }
    manifest["request_summary"] = dict(sorted(status_counts.items()))
    manifest["request_count"] = len(requests)
    manifest["depth_counts"] = dict(sorted(depth_counts.items()))
    manifest["property_counts"] = dict(sorted(property_counts.items()))
    manifest["failed_requests"] = failed
    manifest["qa"] = {
        "qa_version": QA_VERSION,
        "groups": len(qa["groups"]),
        "missing_groups": qa["completeness"]["missing_groups"],
    }
    manifest["files"] = {
        "requests.jsonl": {
            "sha256": file_sha256(release_dir / "requests.jsonl"),
            "version": REQUEST_LOG_VERSION,
        },
        "qa.json": {
            "sha256": file_sha256(release_dir / "qa.json"),
            "version": QA_VERSION,
        },
    }
    manifest["notes"] = [
        "Portable release of a bounded five-AOI pilot, not a global survey.",
        "SoilGrids values are 250 m global model predictions, not local measurements.",
        "Each observation carries its own depth interval; depths are never pooled.",
        "Observations are evidence-only and never enter weighted similarity scoring.",
        "Raw rasters are not committed; response checksums are recorded per request.",
        "The 0-5 cm records reuse the same observation IDs as the earlier SoilGrids pilot "
        "and are upserted rather than duplicated on import.",
    ]
    manifest["observation_depths"] = sorted(
        {
            (observation["depth_support"]["min_m"], observation["depth_support"]["max_m"])
            for observation in observations
        }
    )
    write_json(release_dir / "manifest.json", manifest)
    return manifest


def _write_report(
    release_dir: Path,
    manifest: dict[str, Any],
    requests: list[dict[str, Any]],
    qa: dict[str, Any],
) -> None:
    observations = _observation_rows(release_dir)
    state_counts = Counter(row["state"] for row in observations)
    by_depth = Counter(
        f"{row['depth_support']['min_m']:g}-{row['depth_support']['max_m']:g} m"
        for row in observations
    )
    per_request = "| Status | Requests |\n| --- | ---: |\n" + "\n".join(
        f"| {status} | {count} |" for status, count in sorted(manifest["request_summary"].items())
    )
    depth_table = "| Depth | Observations |\n| --- | ---: |\n" + "\n".join(
        f"| {depth} | {count} |" for depth, count in sorted(by_depth.items())
    )
    failed_lines = [
        f"- `{item['task_id']}`: {item['status']}: {item['error']}"
        for item in manifest["failed_requests"]
    ] or ["- None observed in this run."]
    lines = [
        "# D06 SoilGrids multi-depth pilot",
        "",
        f"- Release version: `{manifest['release_version']}`",
        f"- Scope: `{manifest['scope']}`",
        f"- Generated at: `{manifest['generated_at']}`",
        f"- Observations: {len(observations)}",
        f"- Assets: {manifest['asset_count']} (response checksums only; raw rasters not committed)",
        f"- Plan tasks: {len(requests)}",
        f"- By state: {dict(sorted(state_counts.items()))}",
        f"- Areas: {', '.join(manifest['report']['areas']) or 'none'}",
        "",
        "## Source and licence",
        "",
        "- Product: ISRIC SoilGrids 2.0, 250 m global model predictions.",
        "- Service: public OGC WCS 2.0.1 at `https://maps.isric.org/mapserv`; no credentials.",
        "- Licence: CC BY 4.0; cite ISRIC SoilGrids 2.0.",
        "- Evidence class: **model prediction, not a measurement**.",
        "- Model uncertainty: separate Q0.05/Q0.95 prediction-quantile records per "
        "area/property/depth; not a confidence interval for the AOI mean.",
        "",
        "## Requests",
        "",
        per_request,
        "",
        f"- Failed/unavailable: {len(manifest['failed_requests'])}",
        "",
        "## Depth coverage",
        "",
        depth_table,
        "",
        "Depths are separate observations and are never averaged or scored together.",
        "",
        "## Failed requests",
        "",
        *failed_lines,
        "",
        "## QA",
        "",
        f"- QA groups (area/property/depth): {len(qa['groups'])}",
        f"- Complete groups: {qa['completeness']['complete_groups']}",
        f"- Missing groups: {qa['completeness']['missing_groups']}",
        "",
        "## Reproduction",
        "",
        "```bash",
        "uv run python scripts/build_soilgrids_depth_pilot.py --dry-run",
        "uv run python scripts/build_soilgrids_depth_pilot.py --resume --workers 2",
        f"uv run python scripts/import_typed_release.py --release {release_dir} "
        "--database var/d06-depths.db",
        "```",
        "",
        "Offline checks:",
        "",
        "```bash",
        "uv run pytest pipelines/tests/test_soilgrids_depths.py -q",
        "uv run pytest pipelines/tests/test_regolith_semantics.py -q",
        "```",
        "",
        "## Limitations",
        "",
        "- Five AOIs only; this is not a global or exhaustive SoilGrids survey.",
        "- AOI values are means over the AOI envelope on the native 250 m grid, not SoilGrids "
        "product cell values at a point.",
        "- SoilGrids bdod is fine-earth bulk density and excludes coarse fragments.",
        "- No Moon or Mars regolith value is provided; see `contracts/d06-regolith-semantics-v1.json`.",
        "",
    ]
    (release_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan = _make_plan(args)
    plan_path = write_plan(plan, args.plan)
    summary = {
        "plan": str(plan_path),
        "tasks": len(plan.tasks),
        "depths": sorted({task.parameters["depth"] for task in plan.tasks}),
        "properties": sorted({task.parameters["property"] for task in plan.tasks}),
        "statistics": sorted({task.parameters["statistic"] for task in plan.tasks}),
        "areas": sorted({task.area.area_id for task in plan.tasks}),
        "endpoint": "https://maps.isric.org/mapserv",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.dry_run:
        print("dry run: no network request and no release written")
        return 0

    run = run_plan(
        plan_path,
        run_dir=args.run_dir,
        cache_dir=args.cache,
        workers=args.workers,
        resume=args.resume,
        retry_failed=args.retry_failed,
    )
    print(json.dumps(run, indent=2, sort_keys=True))
    if args.skip_release:
        return 0 if run["validated"] else 1

    release_dir = Path(args.release)
    manifest = build_release(
        plan_path=plan_path,
        run_dir=args.run_dir,
        output_dir=release_dir,
        include_raw=False,
    )
    assets_path = release_dir / "assets.json"
    assets = json.loads(assets_path.read_text(encoding="utf-8"))
    for asset in assets:
        if asset.get("dataset_id") == "soilgrids-250m-v2":
            # CC BY 4.0 permits redistribution with attribution; raw TIFFs are still
            # not committed, only derived values, checksums and provenance.
            asset["redistribution_allowed"] = "yes"
    write_json(assets_path, assets)
    requests = _request_rows(Path(args.run_dir), plan)
    (release_dir / "requests.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in requests),
        encoding="utf-8",
    )
    observations = _observation_rows(release_dir)
    qa = _build_qa(observations, requests)
    write_json(release_dir / "qa.json", qa)
    manifest = _rewrite_manifest(release_dir, plan, requests, qa)
    _write_report(release_dir, manifest, requests, qa)
    integrity = verify_release_integrity(release_dir)
    print(json.dumps({"release": str(release_dir), "integrity": integrity}, indent=2))
    return 0 if all(row["status"] == "succeeded" for row in requests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
