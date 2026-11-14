"""Aggregate actual acquisition outputs into coverage/quality reports."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from analog_pipelines.acquisition.models import TaskCheckpoint, write_json


def collect_run(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    observations: list[dict[str, Any]] = []
    for path in sorted((root / "observations").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                observations.append(json.loads(line))
    checkpoints = [
        TaskCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((root / "checkpoints").glob("*.json"))
    ]
    by_dimension = Counter(str(item.get("dimension_code")) for item in observations)
    by_body = Counter(str(item.get("body")) for item in observations)
    by_state = Counter(str(item.get("state")) for item in observations)
    by_feature = Counter(str(item.get("feature_id")) for item in observations)
    areas = sorted({str(item.get("area_id")) for item in observations})
    try:
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        summary = {}
    return {
        "run_dir": str(root),
        "summary": summary,
        "tasks": {
            "total": len(checkpoints),
            "succeeded": sum(1 for cp in checkpoints if cp.status == "succeeded"),
            "failed": sum(1 for cp in checkpoints if cp.status == "failed"),
            "unavailable": sum(1 for cp in checkpoints if cp.status == "unavailable"),
        },
        "observations": {
            "total": len(observations),
            "by_dimension": dict(sorted(by_dimension.items())),
            "by_body": dict(sorted(by_body.items())),
            "by_state": dict(sorted(by_state.items())),
            "by_feature": dict(sorted(by_feature.items())),
            "areas": areas,
        },
        "scope_note": (
            "Counts cover only successfully processed tasks in this run. They are not a "
            "global survey and do not convert context values into comparable scored features."
        ),
    }


def write_report(run_dir: str | Path) -> dict[str, Any]:
    report = collect_run(run_dir)
    root = Path(run_dir)
    write_json(root / "report.json", report)
    lines = [
        "# Acquisition run report",
        "",
        f"- Run directory: `{report['run_dir']}`",
        f"- Tasks: {report['tasks']['succeeded']} succeeded, "
        f"{report['tasks']['failed']} failed, {report['tasks']['unavailable']} unavailable "
        f"(total {report['tasks']['total']})",
        f"- Observations: {report['observations']['total']}",
        f"- By dimension: {report['observations']['by_dimension']}",
        f"- By body: {report['observations']['by_body']}",
        f"- By state: {report['observations']['by_state']}",
        f"- Areas: {', '.join(report['observations']['areas']) or 'none'}",
        "",
        report["scope_note"],
        "",
    ]
    (root / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return report
