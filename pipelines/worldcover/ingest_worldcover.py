#!/usr/bin/env python3
"""Ingest ESA WorldCover 2021 v200 land-cover context for the Earth AOIs.

Usage (from the repository root):

    uv run python pipelines/worldcover/ingest_worldcover.py --dry-run
    uv run python pipelines/worldcover/ingest_worldcover.py
    uv run python pipelines/worldcover/ingest_worldcover.py --only atacama-desert
    uv run python pipelines/worldcover/ingest_worldcover.py --force

Only the small class window over each indexed Earth AOI bbox is read from the public
WorldCover COGs over unsigned HTTPS and cached under ``data/cache/worldcover/``
(gitignored). Failed AOIs are reported and omitted; no value is ever synthesised.

These fractions are Earth land-cover context only. They are not lunar mineralogy, not
geology, and never a similarity feature.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from worldcover import (  # noqa: E402
    DEFAULT_TIMEOUT_S,
    MAX_RETRIES,
    NATIVE_RESOLUTION_M,
    AoiObservation,
    IngestError,
    build_manifest,
    default_cache_dir,
    default_earth_manifest_path,
    default_manifest_path,
    default_schema_path,
    fetch_aoi_window,
    find_aoi,
    load_earth_aois,
    validate_manifest,
    write_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest_worldcover.py",
        description=(
            "Read bounded ESA WorldCover 2021 v200 windows for the indexed Earth AOIs, "
            "compute 11 class fractions at native 10 m, and write a validated manifest."
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="SLUG",
        help=(
            "Ingest only this AOI (accepts 'atacama-desert' or 'earth-atacama-desert'); "
            "may be repeated or comma-separated."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cached AOI windows and re-read every selected window from S3.",
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
        help="AOI window cache directory (default: data/cache/worldcover).",
    )
    parser.add_argument(
        "--earth-manifest",
        type=Path,
        default=None,
        help=(
            "Earth terrain manifest that supplies the AOI bboxes "
            "(default: pipelines/earth/manifests/copernicus_glo90_earth.json)."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "Output manifest path "
            "(default: pipelines/worldcover/manifests/worldcover_earth_context.json)."
        ),
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


def _selected_aois(aois, values: list[str] | None):
    if not values:
        return list(aois)
    selected = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                aoi = find_aoi(aois, part)
                if aoi not in selected:
                    selected.append(aoi)
    return selected


def _print_dry_run(aois, cache_dir: Path, manifest_path: Path, earth_manifest: Path) -> None:
    print("DRY RUN - no network access and no files will be written")
    print(f"Earth AOIs:     {earth_manifest}")
    print(f"cache directory: {cache_dir}")
    print(f"manifest:        {manifest_path}")
    for aoi in aois:
        cached = (cache_dir / f"{aoi.slug}.tif").is_file()
        tiles = aoi.tile_ids
        print(
            f"\n{aoi.location_id}  {aoi.display_name}\n"
            f"  bbox:   west={aoi.bbox['west']:.8f} south={aoi.bbox['south']:.8f} "
            f"east={aoi.bbox['east']:.8f} north={aoi.bbox['north']:.8f}\n"
            f"  tile(s): {', '.join(tiles)}\n"
            + "".join(f"  url:    {_tile_url(tile)}\n" for tile in tiles)
            + f"  cache:  {cache_dir / (aoi.slug + '.tif')} "
            f"({'present; would reuse' if cached else 'absent; would range-read'})\n"
            f"  action: read the {int(NATIVE_RESOLUTION_M)} m class window and compute "
            f"11 WorldCover fractions"
        )


def _tile_url(tile_id: str) -> str:
    from worldcover import tile_url

    return tile_url(tile_id)


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
    earth_manifest = (args.earth_manifest or default_earth_manifest_path()).expanduser()

    try:
        earth_aois = load_earth_aois(earth_manifest)
        selected = _selected_aois(earth_aois, args.only)
    except IngestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        _print_dry_run(selected, cache_dir, manifest_path, earth_manifest)
        return 0

    observations: list[AoiObservation] = []
    failures: list[tuple[str, str]] = []
    remote_bytes = 0

    for aoi in selected:
        print(f"[{aoi.location_id}] {aoi.display_name}", flush=True)
        try:
            observation = fetch_aoi_window(
                aoi,
                cache_dir,
                force=args.force,
                timeout=args.timeout,
                max_retries=args.max_retries,
            )
            observations.append(observation)
            if observation.remote_bytes and not observation.from_cache:
                remote_bytes += observation.remote_bytes
            fractions = observation.fractions
            values = fractions.fractions
            source = "cache" if observation.from_cache else "range-read"
            print(
                f"  OK ({source}) tiles={', '.join(observation.tile_ids)} "
                f"valid={fractions.valid_fraction:.4f} "
                f"bare_sparse={values[60]:.4f} built_up={values[50]:.4f} "
                f"sum={sum(values.values()):.6f} "
                f"checksum={observation.checksum[:19]}...",
                flush=True,
            )
            if fractions.unknown_codes:
                print(
                    f"  WARNING: unknown WorldCover class code(s) "
                    f"{fractions.unknown_codes}; quality={fractions.quality}",
                    file=sys.stderr,
                    flush=True,
                )
        except (IngestError, OSError) as exc:
            message = str(exc)
            failures.append((aoi.location_id, message))
            print(f"  FAILED: {message}", file=sys.stderr, flush=True)

    print()
    if not observations:
        print(
            "ERROR: no AOI windows succeeded. No manifest was written and no synthetic "
            "values were substituted.",
            file=sys.stderr,
        )
        for location_id, message in failures:
            print(f"  - {location_id}: {message}", file=sys.stderr)
        return 1

    earth_document = {
        "candidates": [aoi.earth_record for aoi in earth_aois],
    }
    document = build_manifest(observations, earth_document)
    try:
        validate_manifest(document, default_schema_path())
    except IngestError as exc:
        print(f"ERROR: internal manifest validation failed: {exc}", file=sys.stderr)
        return 1
    write_manifest(document, manifest_path)

    print(
        f"Wrote {manifest_path} with {len(observations)} AOI context record(s) "
        f"from {len(selected)} attempted window(s)."
    )
    print("Context summary (fractions of valid WorldCover pixels; bare/sparse is class 60):")
    print(f"  {'candidate':<26} {'valid':>6} {'bare/sparse':>11} {'built-up':>9} {'sum':>8}")
    for observation in observations:
        fractions = observation.fractions
        values = fractions.fractions
        print(
            f"  {observation.aoi.location_id:<26} "
            f"{fractions.valid_fraction:>6.3f} "
            f"{values[60]:>11.4f} "
            f"{values[50]:>9.4f} "
            f"{sum(values.values()):>8.5f}"
        )
    if remote_bytes:
        print(
            f"WorldCover object bytes covered by HTTPS reads (HEAD Content-Length, "
            f"upper bound): {remote_bytes:,}"
        )
    if failures:
        print(f"{len(failures)} AOI window(s) failed and were omitted:", file=sys.stderr)
        for location_id, message in failures:
            print(f"  - {location_id}: {message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
