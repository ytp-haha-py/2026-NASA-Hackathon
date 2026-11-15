#!/usr/bin/env python3
"""Refine deterministic coarse hits with the existing 90 m Earth pipeline (Issue #37).

Default run (network; bounded 1.98 km windows only):

    uv run python pipelines/refine_coarse_hits.py

Offline plan only (derives focus windows and refined area IDs; no provider calls):

    uv run python pipelines/refine_coarse_hits.py --dry-run

The coarse index is verified before use. Each selected 5 km coarse candidate gets one
1.98 km focus window centred in its recorded projected metric frame. The real provider
reads small Copernicus GLO-90 windows over HTTPS and ESA WorldCover context, and writes
a small JSON + Markdown report. No full tiles, no global downloads, no synthetic
fallback, and no promotion into the seedable index.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any

from analog_pipelines.coarse_features import CoarseExtractionError
from analog_pipelines.coarse_index import CoarseIndexRepository, IndexError_
from analog_pipelines.paths import REPO_ROOT
from analog_pipelines.refine import (
    DEFAULT_TARGET,
    FOCUS_PLACEMENT_RULE,
    FOCUS_WINDOW_M,
    SELECTION_RULES,
    SELECTION_TOP_PER_REGION,
    RefineError,
    build_report,
    derive_focus_window,
    refine_hits,
    refined_area_id,
    select_hits,
    utc_now_iso,
    write_report_json,
    write_report_markdown,
)

DEFAULT_INDEX = REPO_ROOT / "pipelines/coarse/real-pilot/index"
DEFAULT_SNAPSHOT_ID = "geomorpho250-real-pilot-20261115"
DEFAULT_OUT = REPO_ROOT / "pipelines/coarse/real-pilot/refine/refinement.json"
DEFAULT_REPORT = REPO_ROOT / "docs/COARSE_REFINE_PILOT.md"


def _parse_pairs(values: list[str] | None, *, what: str) -> dict[str, float]:
    if not values:
        return {}
    parsed: dict[str, float] = {}
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                raise RefineError("invalid_argument", f"{what} must use FEATURE=VALUE: {part!r}")
            feature_id, raw = part.split("=", 1)
            try:
                parsed[feature_id.strip()] = float(raw)
            except ValueError as exc:
                raise RefineError(
                    "invalid_argument", f"{what} value is not numeric: {part!r}"
                ) from exc
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refine_coarse_hits.py",
        description=(
            "Select deterministic coarse hits, derive 1.98 km focus windows, run the "
            "existing bounded 90 m Copernicus + WorldCover provider and write a report."
        ),
    )
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Number of coarse hits to refine (1..10; default 3).",
    )
    parser.add_argument(
        "--selection",
        choices=SELECTION_RULES,
        default=SELECTION_TOP_PER_REGION,
        help=(
            "top-per-region (default): top hit in each committed coarse region; "
            "top-overall: global top K by coarse distance."
        ),
    )
    parser.add_argument(
        "--target",
        action="append",
        default=None,
        metavar="FEATURE=VALUE",
        help=(
            "Coarse target feature/value; repeat or comma-separate. Default: "
            "coarse_slope_mean_deg=8.0"
        ),
    )
    parser.add_argument(
        "--weight",
        action="append",
        default=None,
        metavar="FEATURE=VALUE",
        help="Coarse weight per target feature (default 1.0).",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--timeout",
        type=float,
        default=25.0,
        help="Per-request GDAL/HTTPS timeout in seconds (1..120; default 25).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Derive focus windows and refined area IDs only; no provider calls or writes.",
    )
    return parser


def _print_dry_run(selection, snapshot_id: str) -> None:
    print("DRY RUN - coarse selection only; no provider calls and no files written")
    print(
        f"index snapshot={snapshot_id} selection={selection.rule} "
        f"indexed={selection.indexed_candidate_count} eligible={selection.eligible_candidate_count}"
    )
    print(f"target={json.dumps(selection.target, sort_keys=True)}")
    print(f"weights={json.dumps(selection.weights, sort_keys=True)}")
    for hit in selection.hits:
        focus = derive_focus_window(hit)
        area_id = refined_area_id(focus, coarse_snapshot_id=snapshot_id)
        print(
            f"\n{hit['candidate_id']} region={hit['region_id']} "
            f"rank={hit['coarse_rank']} ({hit['coarse_rank_scope']}) "
            f"distance={hit['distance']:.6f}"
        )
        print(
            f"  coarse bbox: {hit['west']:.6f},{hit['south']:.6f} -> "
            f"{hit['east']:.6f},{hit['north']:.6f} ({hit['crs']})"
        )
        print(
            f"  focus {FOCUS_WINDOW_M:.0f} m: {focus.focus_bbox_wgs84['west']:.6f},"
            f"{focus.focus_bbox_wgs84['south']:.6f} -> "
            f"{focus.focus_bbox_wgs84['east']:.6f},{focus.focus_bbox_wgs84['north']:.6f}"
        )
        print(f"  placement: {FOCUS_PLACEMENT_RULE}")
        print(f"  refined area: {area_id}")


def _max_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS reports bytes.
    return usage / (1024.0 * 1024.0) if sys.platform == "darwin" else usage / 1024.0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.limit <= 10:
        print("ERROR: --limit must be between 1 and 10.", file=sys.stderr)
        return 2
    if not 1 <= args.timeout <= 120:
        print("ERROR: --timeout must be between 1 and 120 seconds.", file=sys.stderr)
        return 2

    try:
        target = _parse_pairs(args.target, what="--target") or dict(DEFAULT_TARGET)
        weights = _parse_pairs(args.weight, what="--weight")
        if not weights:
            weights = {feature_id: 1.0 for feature_id in target}
        repository = CoarseIndexRepository(args.index, snapshot_id=args.snapshot_id)
        selection = select_hits(
            repository,
            target=target,
            weights=weights,
            limit=args.limit,
            selection=args.selection,
        )
    except (IndexError_, CoarseExtractionError, RefineError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        _print_dry_run(selection, repository.snapshot_id)
        return 0

    try:
        from analog_pipelines.refine_earth import RefineEarthProvider

        provider = RefineEarthProvider(timeout=args.timeout)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: cannot configure the real 90 m provider: {exc}", file=sys.stderr)
        return 2

    started = time.perf_counter()
    links, failures = refine_hits(selection, provider, coarse_snapshot_id=repository.snapshot_id)
    elapsed = time.perf_counter() - started
    report = build_report(
        selection=selection,
        links=links,
        failures=failures,
        repository=repository,
        index_path=args.index,
        run_metrics={"elapsed_s": round(elapsed, 3), "max_rss_mb": round(_max_rss_mb(), 1)},
        provider_label="analog_pipelines.refine_earth.RefineEarthProvider",
        generated_by="pipelines/refine_coarse_hits.py",
        generated_at=utc_now_iso(),
    )
    json_path = write_report_json(report, args.out)
    json_bytes = json_path.stat().st_size
    markdown_report: dict[str, Any] = dict(report)
    markdown_report["run_metrics"] = {
        **report["run_metrics"],
        "json_bytes": json_bytes,
    }
    markdown_path = write_report_markdown(markdown_report, args.report)
    markdown_bytes = markdown_path.stat().st_size

    counts = report["counts"]
    print(
        f"refined snapshot={repository.snapshot_id} selection={selection.rule} "
        f"selected={counts['coarse_selected']} succeeded={counts['refined_succeeded']} "
        f"failed={counts['refined_failed']} no_coverage={counts['refined_no_coverage']} "
        f"fine_observations={counts['fine_feature_observations']} "
        f"context_observations={counts['context_observations']} "
        f"elapsed={elapsed:.2f}s"
    )
    print(f"wrote {json_path} ({json_bytes} bytes)")
    print(f"wrote {markdown_path} ({markdown_bytes} bytes)")
    if failures:
        print(
            f"{len(failures)} window(s) were not refined; see the report for per-window status.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
