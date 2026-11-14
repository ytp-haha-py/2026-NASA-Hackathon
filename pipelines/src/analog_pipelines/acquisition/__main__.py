"""CLI for source inventory, planning and bounded acquisition."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from analog_pipelines.acquisition.executor import run_plan, validate_run
from analog_pipelines.acquisition.models import write_json
from analog_pipelines.acquisition.planner import DEFAULT_AREAS, build_plan, load_areas, write_plan
from analog_pipelines.acquisition.release import build_release
from analog_pipelines.acquisition.reporting import write_report
from analog_pipelines.inventory import inventory_matrix, load_inventory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Programmatic six-dimension acquisition")
    sub = parser.add_subparsers(dest="command", required=True)

    inventory = sub.add_parser("inventory", help="Print the 18-combination source matrix")
    inventory.add_argument("--all-dimensions", action="store_true")
    inventory.add_argument("--output")

    plan = sub.add_parser("plan", help="Build a reproducible acquisition plan")
    plan.add_argument("--scope", default="pilot")
    plan.add_argument("--output", default="var/plans/pilot.json")
    plan.add_argument("--areas", default=str(DEFAULT_AREAS))
    plan.add_argument("--area-id", action="append", default=None)
    plan.add_argument("--dimension", action="append", default=None)
    plan.add_argument("--body", action="append", default=None)
    plan.add_argument("--provider", action="append", default=None)
    plan.add_argument("--start")
    plan.add_argument("--end")

    run = sub.add_parser("run", help="Execute a plan with bounded concurrency")
    run.add_argument("--plan", required=True)
    run.add_argument("--output", default=None)
    run.add_argument("--cache", default="var/cache/acquisition")
    run.add_argument("--workers", type=int, default=2)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--retry-failed", action="store_true")

    validate = sub.add_parser("validate", help="Verify a run's checkpoints/assets")
    validate.add_argument("--run-dir", required=True)

    report = sub.add_parser("report", help="Aggregate a run's observed coverage")
    report.add_argument("--run-dir", required=True)

    release = sub.add_parser("release", help="Create a portable checksummed pilot release")
    release.add_argument("--plan", required=True)
    release.add_argument("--run-dir", required=True)
    release.add_argument("--output", required=True)
    release.add_argument("--no-raw", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inventory":
        rows = inventory_matrix(load_inventory())
        payload = {"count": len(rows), "combinations": rows}
        if args.output:
            write_json(Path(args.output), payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "plan":
        areas = load_areas(args.areas, area_ids=args.area_id)
        plan = build_plan(
            scope=args.scope,
            areas=areas,
            dimensions=args.dimension or ["D01", "D02", "D03", "D04", "D05", "D06"],
            bodies=args.body or ["Earth", "Moon", "Mars"],
            provider_ids=args.provider or None,
            start=args.start,
            end=args.end,
        )
        target = write_plan(plan, args.output)
        print(f"Wrote {len(plan.tasks)} task(s) to {target}")
        print(json.dumps(plan.totals, indent=2, sort_keys=True))
        return 0
    if args.command == "run":
        run_dir = args.output or (
            "var/runs/acquisition-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        )
        summary = run_plan(
            args.plan,
            run_dir=run_dir,
            cache_dir=args.cache,
            workers=args.workers,
            resume=args.resume,
            retry_failed=args.retry_failed,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["validated"] else 1
    if args.command == "validate":
        report = validate_run(args.run_dir)
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0 if report.ok else 1
    if args.command == "report":
        report = write_report(args.run_dir)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "release":
        manifest = build_release(
            plan_path=args.plan,
            run_dir=args.run_dir,
            output_dir=args.output,
            include_raw=not args.no_raw,
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
