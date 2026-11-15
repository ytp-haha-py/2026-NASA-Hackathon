#!/usr/bin/env python
"""Generate or verify the source-grounded global terrain evaluation report (Issue #39).

The report is built only from committed artefacts:

* ``pipelines/coarse/real-pilot/index`` — verified 168-cell real coarse Parquet index;
* ``pipelines/coarse/real-pilot/batch/geomorpho-pilot-real-20261115`` — committed batch
  report, checkpoint, and per-task sidecars for the same run;
* ``docs/GEOMORPHO_REAL_PILOT.md`` — committed pilot source/licence record;
* ``docs/DB_CAPACITY_REPORT.md`` — committed DB capacity measurements.

It never downloads, stages, scans, or synthesises a source, and it refuses to describe the
three-region pilot as global or exhaustive. Regenerate::

    uv run python scripts/report_global_terrain.py

Verify that committed files still match the inputs (offline, deterministic)::

    uv run python scripts/report_global_terrain.py --check

Exit codes: ``0`` success, ``1`` stale/mismatched committed report, ``2`` invalid input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from analog_pipelines.global_terrain_report import (
    ARTIFACT_NAMES,
    DOCUMENT_NAME,
    GlobalClaimError,
    GlobalReportError,
    ReportInputs,
    check_global_terrain_report,
    write_global_terrain_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = REPO_ROOT / "pipelines" / "coarse" / "real-pilot" / "index"
DEFAULT_BATCH = (
    REPO_ROOT / "pipelines" / "coarse" / "real-pilot" / "batch" / "geomorpho-pilot-real-20261115"
)
DEFAULT_PILOT_DOC = REPO_ROOT / "docs" / "GEOMORPHO_REAL_PILOT.md"
DEFAULT_DB_REPORT = REPO_ROOT / "docs" / "DB_CAPACITY_REPORT.md"
DEFAULT_DOCUMENT = REPO_ROOT / "docs" / DOCUMENT_NAME
DEFAULT_ARTIFACTS = REPO_ROOT / "docs" / "global-terrain-results"


def _inputs(args: argparse.Namespace) -> ReportInputs:
    return ReportInputs(
        repo_root=REPO_ROOT,
        index_root=args.index,
        batch_dir=args.batch,
        pilot_doc=args.pilot_doc,
        db_report=args.db_report,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help="coarse index root")
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH, help="batch evidence dir")
    parser.add_argument("--pilot-doc", type=Path, default=DEFAULT_PILOT_DOC)
    parser.add_argument("--db-report", type=Path, default=DEFAULT_DB_REPORT)
    parser.add_argument("--document", type=Path, default=DEFAULT_DOCUMENT)
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare generated output with the committed files instead of writing",
    )
    args = parser.parse_args(argv)

    inputs = _inputs(args)
    try:
        if args.check:
            problems = check_global_terrain_report(
                inputs,
                document_path=args.document,
                artifacts_dir=args.artifacts_dir,
            )
            if problems:
                for problem in problems:
                    print(problem, file=sys.stderr)
                print(
                    "committed report is stale; rerun "
                    "`uv run python scripts/report_global_terrain.py`",
                    file=sys.stderr,
                )
                return 1
            print(
                f"report current: {args.document} + "
                f"{len(ARTIFACT_NAMES)} artifacts match the committed inputs"
            )
            return 0
        written = write_global_terrain_report(
            inputs,
            document_path=args.document,
            artifacts_dir=args.artifacts_dir,
        )
    except GlobalClaimError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except GlobalReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
