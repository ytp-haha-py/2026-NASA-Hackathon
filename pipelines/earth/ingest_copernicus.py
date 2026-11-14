#!/usr/bin/env python3
"""Ingest real Copernicus DEM GLO-90 windows into the pipeline manifest.

Usage (from the repository root):

    uv run python pipelines/earth/ingest_copernicus.py --dry-run
    uv run python pipelines/earth/ingest_copernicus.py
    uv run python pipelines/earth/ingest_copernicus.py --only atacama-desert
    uv run python pipelines/earth/ingest_copernicus.py --force

The importer reads only a padded ~3 km window from each public S3 COG over HTTPS,
caches it as a tiny local GeoTIFF under ``data/cache/copernicus/`` (gitignored), and
computes the four registry metrics with the shared ``analog_pipelines.features``
extractor. Failed windows are reported and omitted; no values are ever synthesised.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analog_pipelines.features import ExtractionError  # noqa: E402
from copernicus_glo90 import (  # noqa: E402
    CANDIDATES,
    DEFAULT_TIMEOUT_S,
    MAX_RETRIES,
    CandidateMeasurement,
    IngestError,
    build_manifest,
    default_cache_dir,
    default_manifest_path,
    default_schema_path,
    extract_candidate,
    fetch_crop,
    find_candidate,
    validate_manifest,
    write_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest_copernicus.py",
        description=(
            "Read small Copernicus DEM GLO-90 windows, extract terrain-metrics-1.0.0 "
            "features and write a validated pipeline manifest."
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="SLUG",
        help=(
            "Ingest only this candidate (accepts 'atacama-desert' or "
            "'earth-atacama-desert'); may be repeated or comma-separated."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cached crops and re-read every selected window from S3.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved tiles, URLs and cache paths without network or writes.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Crop cache directory (default: data/cache/copernicus).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Output manifest path (default: pipelines/earth/manifests/copernicus_glo90_earth.json).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Per-attempt HTTP/GDAL timeout in seconds (default: {DEFAULT_TIMEOUT_S:.0f}).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        help=f"Retries per network operation, max 2 by policy (default: {MAX_RETRIES}).",
    )
    return parser


def _selected_candidates(values: list[str] | None):
    if not values:
        return list(CANDIDATES)
    selected = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                candidate = find_candidate(part)
                if candidate not in selected:
                    selected.append(candidate)
    return selected


def _print_dry_run(candidates, cache_dir: Path, manifest_path: Path) -> None:
    print("DRY RUN - no network access and no files will be written")
    print(f"cache directory: {cache_dir}")
    print(f"manifest:        {manifest_path}")
    for candidate in candidates:
        cached = (cache_dir / f"{candidate.slug}.tif").is_file()
        print(
            f"\n{candidate.location_id}  {candidate.display_name}\n"
            f"  centre:   lon={candidate.lon} lat={candidate.lat}\n"
            f"  tile:     {candidate.tile_id}\n"
            f"  url:      {candidate.tile_url}\n"
            f"  cache:    {cache_dir / (candidate.slug + '.tif')} "
            f"({'present; would reuse' if cached else 'absent; would range-read'})\n"
            f"  action:   read padded ~3 km window, crop to local GeoTIFF, extract "
            f"4 features at 90 m"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_retries < 0 or args.max_retries > MAX_RETRIES:
        print(
            f"ERROR: --max-retries must be between 0 and {MAX_RETRIES} (network policy).",
            file=sys.stderr,
        )
        return 2
    if args.timeout <= 0:
        print("ERROR: --timeout must be positive.", file=sys.stderr)
        return 2

    cache_dir = (args.cache_dir or default_cache_dir()).expanduser()
    manifest_path = (args.manifest or default_manifest_path()).expanduser()

    try:
        candidates = _selected_candidates(args.only)
    except IngestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        _print_dry_run(candidates, cache_dir, manifest_path)
        return 0

    measurements: list[CandidateMeasurement] = []
    failures: list[tuple[str, str]] = []
    remote_bytes = 0

    for candidate in candidates:
        print(f"[{candidate.location_id}] {candidate.display_name}", flush=True)
        try:
            crop = fetch_crop(
                candidate,
                cache_dir,
                force=args.force,
                timeout=args.timeout,
                max_retries=args.max_retries,
            )
            result = extract_candidate(candidate, crop)
            measurements.append(CandidateMeasurement(candidate=candidate, crop=crop, result=result))
            if crop.remote_content_length and not crop.from_cache:
                remote_bytes += crop.remote_content_length
            source = "cache" if crop.from_cache else "range-read"
            print(
                f"  OK ({source}) tile={crop.tile_id} crs={result.analysis_crs} "
                f"valid={result.valid_fraction:.4f} "
                f"slope_mean={result.features['slope_mean_deg']:.4f} deg "
                f"relief={result.features['terrain_relief_p95_p05_m']:.3f} m "
                f"checksum={result.window_checksum[:19]}...",
                flush=True,
            )
        except (IngestError, ExtractionError, OSError) as exc:
            message = str(exc)
            failures.append((candidate.location_id, message))
            print(f"  FAILED: {message}", file=sys.stderr, flush=True)

    print()
    if not measurements:
        print(
            "ERROR: no windows succeeded. No manifest was written and no synthetic "
            "values were substituted.",
            file=sys.stderr,
        )
        for location_id, message in failures:
            print(f"  - {location_id}: {message}", file=sys.stderr)
        return 1

    document = build_manifest(measurements)
    try:
        validate_manifest(document, default_schema_path())
    except IngestError as exc:
        print(f"ERROR: internal manifest validation failed: {exc}", file=sys.stderr)
        return 1
    write_manifest(document, manifest_path)

    print(
        f"Wrote {manifest_path} with {len(measurements)} observed candidate(s) "
        f"from {len(candidates)} attempted window(s)."
    )
    if remote_bytes:
        print(
            f"S3 object bytes covered by HTTPS reads (HEAD Content-Length, upper bound): "
            f"{remote_bytes:,}"
        )
    if failures:
        print(f"{len(failures)} window(s) failed and were omitted:", file=sys.stderr)
        for location_id, message in failures:
            print(f"  - {location_id}: {message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
