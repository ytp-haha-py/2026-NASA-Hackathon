#!/usr/bin/env python
"""Build the offline D02 CRISM spectral-detection evidence pilot (Issue #42).

Reads the committed bounded-window evidence in
``pipelines/pilots/d02_spectral_detection/windows.json`` and writes the pilot
manifest, typed-style observations and report. No network access happens here;
raw rasters are not committed.

Because the documented continuum-removal decoder failed the reproduction
tolerance against the official refined-summary product, every observation is
``unknown``. No detection, non-detection or abundance is fabricated.

Usage::

    uv run python scripts/build_spectral_detection_pilot.py
    uv run python scripts/build_spectral_detection_pilot.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from analog_pipelines.geology.spectral_detection import (
    AOI_WINDOWS,
    D02_PILOT_METHOD_VERSION,
    DETECTION_THRESHOLDS,
    FEATURE_D2300,
    FEATURE_OLINDEX3,
    PRODUCT_ID,
    THRESHOLD_STATUS,
    build_observation,
    classify,
)

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "pipelines/pilots/d02_spectral_detection"
WINDOWS = PILOT / "windows.json"
SOURCES = PILOT / "sources.json"
MANIFEST = PILOT / "manifest.json"
OBSERVATIONS = PILOT / "observations.jsonl"
REPORT = PILOT / "REPORT.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _observation_rows(decoder_validated: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for area_id in AOI_WINDOWS:
        for feature_id, value in ((FEATURE_D2300, None), (FEATURE_OLINDEX3, None)):
            result = classify(
                feature_id,
                decoder_validated,
                valid_pixels=None,
                total_pixels=AOI_WINDOWS[area_id]["pixel_count"],
                value=value,
                threshold=DETECTION_THRESHOLDS[feature_id],
            )
            observation = build_observation(
                feature_id,
                area_id,
                state=result["state"],
                value=result["value"],
                valid_fraction=result["valid_fraction"],
                decoder_validated=decoder_validated,
            )
            rows.append(observation.model_dump(mode="json"))
    return rows


def build(decoder_validated: bool = False) -> dict[str, Any]:
    windows = json.loads(WINDOWS.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    rows = _observation_rows(decoder_validated)

    attempt = windows["reproduction_attempt"]
    counts = {
        "sources": len(sources),
        "areas": len(AOI_WINDOWS),
        "features": 2,
        "observations": len(rows),
        "by_state": {
            state: sum(1 for row in rows if row["state"] == state)
            for state in (
                "detected",
                "not_detected_under_valid_observation",
                "no_coverage",
                "unknown",
                "not_applicable",
            )
        },
        "numeric_values": sum(1 for row in rows if row["value"] is not None),
        "abundance_values": 0,
        "granules_downloaded_in_full": 0,
        "products_inspected": len(sources),
        "bytes_read_estimate": windows["access"]["bytes_downloaded_estimate"],
        "reproduction": {
            "acceptance_tolerance_abs": attempt["acceptance_tolerance_abs"],
            "d2300_rmse_by_area": {
                area: values["d2300_rmse"] for area, values in attempt["subwindows"].items()
            },
            "olindex3_rmse_by_area": {
                area: values["olindex3_rmse"] for area, values in attempt["subwindows"].items()
            },
            "decoder_validated": attempt["decoder_validated"],
            "verdict": attempt["verdict"],
        },
    }

    manifest = {
        "schema_version": "spectral-detection-pilot-1.0.0",
        "pilot_id": "d02-spectral-detection",
        "issue": 42,
        "method_version": D02_PILOT_METHOD_VERSION,
        "scope": (
            "One bounded CRISM MTRDR targeted observation over the two existing Mars test AOIs; "
            "one M3 global-mode product inspected over headers only. Not a global or body-wide survey."
        ),
        "product_id": PRODUCT_ID,
        "decoder_validated": decoder_validated,
        "thresholds": {
            feature: {"value": value, "status": THRESHOLD_STATUS, "applied": False}
            for feature, value in DETECTION_THRESHOLDS.items()
        },
        "counts": counts,
        "blockers": windows["blockers"],
        "provenance": {
            "windows_sha256": _sha256(WINDOWS),
            "sources_sha256": _sha256(SOURCES),
            "observations_sha256": hashlib.sha256(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode("utf-8")
            ).hexdigest(),
            "raw_data_in_git": False,
        },
        "limitations": [
            "Band depth and spectral indices are dimensionless; no abundance is produced.",
            "Mafic/Fe-oxide indices can never be relabelled aqueous alteration.",
            "Two 256-pixel sub-windows were read; valid_fraction over the full AOIs is not asserted.",
            "The official refined-summary medians are context, not detections by this repository.",
        ],
    }
    return {"manifest": manifest, "sources": sources, "observations": rows}


def render_report(payload: dict[str, Any]) -> str:
    manifest = payload["manifest"]
    counts = manifest["counts"]
    lines = [
        "# D02 spectral-detection pilot — bounded CRISM/M3 evidence",
        "",
        "Status: **evidence-only; decoder unvalidated; no mineral detection and no abundance**.",
        "This is the Issue #42 follow-up to the source-status matrix. It reads part of a real",
        "public PDS granule and records the exact band, geolocation and quality facts, but it",
        "does **not** promote any spectral parameter to a mineral observation.",
        "",
        f"- Product: `{manifest['product_id']}` (Mars, CRISM MTRDR)",
        f"- Method version: `{manifest['method_version']}`",
        f"- Bounded read: {counts['bytes_read_estimate']} bytes; full cubes downloaded: "
        f"{counts['granules_downloaded_in_full']}",
        f"- Observations: {counts['observations']} (all `unknown`); numeric values: "
        f"{counts['numeric_values']}; abundance values: {counts['abundance_values']}",
        "",
        "## What was verified",
        "",
        "- Anonymous HTTPS byte-range access works on every requested CRISM asset; no credentials.",
        "- The `IF` cube is 455 x 751 x 489 BSQ float32, 668,374,980 bytes; the label and detached",
        "  wavelength TAB agree on 489 bands from 436.13 to 3896.76 nm in ascending order.",
        "- Three detector gaps are present (631-710, 1010-1047, 2655-2800 nm); all bands are `bbl=1`.",
        "- The grid is 36 m equirectangular on a 3,394,839.81 m Mars sphere (standard parallel 15 N);",
        "  recomputing the label corners reproduces them to printed precision (<= 18 m offset).",
        "- Both existing Mars test AOIs map to interior native windows:",
        "  `mars-test-jezero-floor` rows 448-515 x columns 198-262;",
        "  `mars-test-jezero-western-delta` rows 383-433 x columns 118-167.",
        "- Moon M3 `M3G20090203T175131_V01_RFL` is public and range-readable, but its RFL/SUP",
        "  headers expose no per-pixel latitude/longitude; geolocation needs the archive",
        "  GEOMETRY/SPICE products. The 2.17 GB cube was intentionally not downloaded.",
        "",
        "## Why there is no detection",
        "",
        "The documented Viviano-Beck et al. (2014) parameters `D2300` (Table 2 #38) and",
        "`OLINDEX3` (#14) were implemented with their published 3/5/7-band median kernels and",
        "compared with the official refined-summary bands over 16 x 16 native-pixel sub-windows.",
        "",
        "| AOI | D2300 RMSE | OLINDEX3 RMSE | Tolerance |",
        "|---|---:|---:|---:|",
    ]
    for area, value in manifest["counts"]["reproduction"]["d2300_rmse_by_area"].items():
        lines.append(
            f"| `{area}` | {value:.5f} | "
            f"{manifest['counts']['reproduction']['olindex3_rmse_by_area'][area]:.5f} | "
            f"{manifest['counts']['reproduction']['acceptance_tolerance_abs']} |"
        )
    lines += [
        "",
        "All errors exceed the 0.001 acceptance tolerance and D2300 correlation is not",
        "consistently positive, so the decoder is **not validated**. States are therefore",
        "`unknown`, not `detected` and not `not_detected_under_valid_observation`.",
        "",
        "Explicit pilot thresholds are recorded but not applied:",
        "",
        "| Feature | Threshold | Status | Applied |",
        "|---|---:|---|---|",
    ]
    for feature, item in manifest["thresholds"].items():
        lines.append(f"| `{feature}` | {item['value']} | `{item['status']}` | no |")
    lines += [
        "",
        "## State semantics",
        "",
        "| State | Meaning here |",
        "|---|---|",
        "| `detected` | A validated decoder and threshold identify the spectral feature in valid pixels. Never abundance. |",
        "| `not_detected_under_valid_observation` | Valid pixels exist and a validated decoder does not identify the feature. Never a numeric zero. |",
        "| `no_coverage` | No native pixel covers the AOI. Not evidence of absence. |",
        "| `unknown` | Data exist but the decoder, mask or threshold is unvalidated. This pilot's only emitted state. |",
        "| `not_applicable` | The measurement is outside the product/body scope. |",
        "",
        "Band depth and spectral index are dimensionless. They are never wt%, vol%, mass fraction",
        "or mineral abundance. `OLINDEX3` is a mafic 1 um index: it is never aqueous alteration,",
        "water evidence or a Fe-oxide hydration claim. `D2300` alone is not proof of aqueous",
        "alteration and does not uniquely identify a mineral.",
        "",
        "## Blockers",
        "",
    ]
    for blocker in manifest["blockers"]:
        lines += [
            f"### `{blocker['blocker_id']}`",
            "",
            f"- {blocker['summary']}",
            f"- Detail: {blocker['detail']}",
            f"- Minimum missing validation: {blocker['resolution_required']}",
            "",
        ]
    lines += [
        "## Reproduce",
        "",
        "```bash",
        "uv run python scripts/build_spectral_detection_pilot.py --check",
        "uv run pytest pipelines/tests/test_spectral_detection.py -o addopts='' -q",
        "uv run ruff check pipelines/src/analog_pipelines/geology/spectral_detection.py \\",
        "  scripts/build_spectral_detection_pilot.py pipelines/tests/test_spectral_detection.py",
        "```",
        "",
        "The builder is offline and deterministic. `windows.json` holds the recorded bounded-read",
        "evidence (URLs, byte sizes, SHA-256 of metadata, observed values and the failed",
        "reproduction); raw window bytes are not committed and live outside Git.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed artifacts match")
    parser.add_argument(
        "--decoder-validated",
        action="store_true",
        help="only for a future validated decoder; refused while reproduction fails",
    )
    args = parser.parse_args()
    if args.decoder_validated:
        raise SystemExit(
            "refusing to enable detection: the reproduction attempt recorded in windows.json "
            "failed the 0.001 tolerance"
        )
    payload = build(decoder_validated=False)
    manifest_text = json.dumps(payload["manifest"], indent=2, sort_keys=True) + "\n"
    observations_text = "".join(
        json.dumps(row, sort_keys=True) + "\n" for row in payload["observations"]
    )
    report_text = render_report(payload)

    if args.check:
        problems = []
        if MANIFEST.read_text(encoding="utf-8") != manifest_text:
            problems.append("manifest.json")
        if OBSERVATIONS.read_text(encoding="utf-8") != observations_text:
            problems.append("observations.jsonl")
        if REPORT.read_text(encoding="utf-8") != report_text:
            problems.append("REPORT.md")
        if problems:
            raise SystemExit("committed artifacts out of date: " + ", ".join(problems))
        print("spectral detection pilot artifacts are up to date")
        return 0

    PILOT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(manifest_text, encoding="utf-8")
    OBSERVATIONS.write_text(observations_text, encoding="utf-8")
    REPORT.write_text(report_text, encoding="utf-8")
    print(
        f"wrote {MANIFEST.name}, {OBSERVATIONS.name}, {REPORT.name}: "
        f"{payload['manifest']['counts']['observations']} observations, "
        f"{payload['manifest']['counts']['numeric_values']} numeric values"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
