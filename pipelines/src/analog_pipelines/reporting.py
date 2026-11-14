"""Coverage report for a verified coarse index (Issue #39).

Reports only what is present in the pinned manifest/index. It never turns a local
fixture into a global result and never runs a scan.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analog_pipelines.coarse_features import COARSE_FEATURES, COARSE_METHOD_VERSION
from analog_pipelines.coarse_index import CoarseIndexRepository, verify_index

REPORT_VERSION = "1.0.0"


def build_coverage_report(index_root: str | Path) -> dict[str, Any]:
    root = Path(index_root)
    manifest = verify_index(root)
    repo = CoarseIndexRepository(root)
    coverage = repo.coverage()
    return {
        "report_version": REPORT_VERSION,
        "index_version": manifest["index_version"],
        "snapshot_id": manifest["snapshot_id"],
        "data_kind": "fixture_or_verified_coarse_index",
        "coverage": coverage,
        "counts": {
            "candidates": manifest["row_count"],
            "regions": len(manifest["coverage"]["regions"]),
            "partitions": len(manifest["partitions"]),
        },
        "features": [feature.to_payload() for feature in COARSE_FEATURES],
        "limitations": [
            "Coarse 250 m-derived features are not the fine 90 m terrain metrics.",
            "The index coverage is not global-exhaustive; Antarctica and unindexed regions are absent.",
            "No real coarse Moon/Mars reference is validated in the offline phase.",
            "Distances are engineering shortlist scores, not physical similarity percentages.",
        ],
        "method_version": COARSE_METHOD_VERSION,
        "global_claim": False,
    }


def write_report(index_root: str | Path, output: str | Path) -> Path:
    report = build_coverage_report(index_root)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = build_coverage_report(args.index)
    if args.output:
        write_report(args.index, args.output)
        print(f"wrote {args.output}")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
