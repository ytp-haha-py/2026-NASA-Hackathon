#!/usr/bin/env python3
"""Import a real NASA LOLA 5 m/pix DEM window as the Moon reference manifest.

Usage (from the repository root):

    uv run python pipelines/planetary/lola_import.py --list-products
    uv run python pipelines/planetary/lola_import.py --dry-run
    uv run python pipelines/planetary/lola_import.py
    uv run python pipelines/planetary/lola_import.py --refresh

The default source is the documented PGDA Site04 (Shackleton rim) 5 m/pix surface
DEM, and the default centre is Site04 ROI 2 from the official ROI shapefile. The
importer reads only a bounded ~2.07 km source window through HTTP range requests,
caches the small derived GeoTIFF under ``data/cache/lola`` (gitignored), and runs
the shared ``analog_pipelines.features`` extractor with ``body="Moon"``.

A manifest is written only after a real window has been read and both the JSON
Schema contract and the API ``Manifest`` model accept it. On failure the command
exits non-zero, prints the exact error, and writes no manifest; an existing
manifest is left untouched. No synthetic values are ever substituted.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rasterio  # noqa: E402
from analog_pipelines.features import ExtractionError, extract_features  # noqa: E402
from lola import (  # noqa: E402
    BODY,
    CITATION,
    DATASET_ID,
    DEFAULT_ROI,
    DEFAULT_TIMEOUT_S,
    MAX_RETRIES,
    PRODUCT_ID,
    PRODUCT_TITLE,
    PROVIDER,
    SITE04_LDEM_URL,
    LolaImportError,
    LolaReferenceProvider,
    PreparedWindow,
    build_manifest,
    default_cache_dir,
    default_dataset_info,
    default_manifest_path,
    default_schema_path,
    format_bytes,
    product_catalog_text,
    resolve_center,
    validate_manifest,
    write_json_atomic,
)
from pyproj.exceptions import ProjError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lola_import.py",
        description=(
            "Read a bounded real NASA PGDA LRO-LOLA 5 m/pix south-pole DEM window, "
            "extract terrain-metrics-1.0.0 features at 90 m, and write the Moon "
            "reference manifest only if the real window validates."
        ),
    )
    parser.add_argument(
        "--list-products",
        action="store_true",
        help="Print the verified Site04 product URLs, sizes and ROI centres; no network.",
    )
    parser.add_argument(
        "--source",
        default=SITE04_LDEM_URL,
        help=(
            "LOLA surface DEM URL, /vsicurl/ URL, file:// URL, or local GeoTIFF path "
            f"(default: {SITE04_LDEM_URL})."
        ),
    )
    parser.add_argument(
        "--center-x",
        type=float,
        default=None,
        help="ROI centre X in the source map CRS (metres). Requires --center-y.",
    )
    parser.add_argument(
        "--center-y",
        type=float,
        default=None,
        help="ROI centre Y in the source map CRS (metres). Requires --center-x.",
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        default=None,
        metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
        help="Explicit source-CRS bounding box; its centre is used for the 1980 m window.",
    )
    parser.add_argument(
        "--reference-id",
        default=None,
        help="Manifest reference_id (default: moon-shackleton-rim-site04-roi2).",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="Human-readable reference name.",
    )
    parser.add_argument(
        "--roi-name",
        default=None,
        help="Short ROI label stored in the reference notes.",
    )
    parser.add_argument(
        "--roi-provenance",
        default=None,
        help="Provenance sentence for the ROI centre stored in the reference notes.",
    )
    parser.add_argument(
        "--dataset-id",
        default=DATASET_ID,
        help=f"Manifest dataset_id (default: {DATASET_ID}).",
    )
    parser.add_argument(
        "--product-id",
        default=PRODUCT_ID,
        help=f"Product identifier (default: {PRODUCT_ID}).",
    )
    parser.add_argument(
        "--product-title",
        default=PRODUCT_TITLE,
        help="Dataset title stored in the manifest.",
    )
    parser.add_argument(
        "--provider",
        default=PROVIDER,
        help="Dataset provider stored in the manifest.",
    )
    parser.add_argument(
        "--citation",
        default=CITATION,
        help="Citation stored in the dataset and reference notes.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output manifest path (default: pipelines/planetary/manifests/lola_moon_reference.json).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Window cache directory (default: data/cache/lola; gitignored).",
    )
    parser.add_argument(
        "--max-window-mb",
        type=float,
        default=30.0,
        help="Maximum estimated range-read size for the source window in MiB (default: 30).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Per-request HTTP/GDAL timeout in seconds (default: {DEFAULT_TIMEOUT_S:.0f}).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        choices=(0, 1, 2),
        default=MAX_RETRIES,
        help=f"Retries per network operation, capped at {MAX_RETRIES} (default: {MAX_RETRIES}).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore a cached window and re-read the source.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect metadata and print the plan; do not download the window or write a manifest.",
    )
    return parser


def _resolve_labels(args: argparse.Namespace) -> tuple[str, str, str, str]:
    custom_center = args.center_x is not None or args.bbox is not None
    custom_source = args.source != SITE04_LDEM_URL
    custom = custom_center or custom_source

    reference_id = args.reference_id
    if reference_id is None:
        reference_id = "moon-lola-window" if custom else "moon-shackleton-rim-site04-roi2"

    display_name = args.display_name
    if display_name is None:
        display_name = (
            "LOLA lunar south pole window" if custom else "Shackleton Rim (LOLA Site04 ROI 2)"
        )

    roi_name = args.roi_name
    if roi_name is None:
        if custom_center:
            roi_name = "custom-window"
        elif custom_source:
            roi_name = "site04-roi2 (default centre, custom source)"
        else:
            roi_name = "site04-roi2"

    roi_provenance = args.roi_provenance
    if roi_provenance is None:
        if args.bbox is not None:
            roi_provenance = (
                "centre of an explicit source-CRS bounding box supplied on the command line"
            )
        elif args.center_x is not None:
            roi_provenance = "explicit source X/Y supplied on the command line"
        elif custom_source:
            roi_provenance = DEFAULT_ROI.provenance + " (default ROI used with a custom source)"
        else:
            roi_provenance = DEFAULT_ROI.provenance
    return reference_id, display_name, roi_name, roi_provenance


def _print_plan(prepared: PreparedWindow, output: Path, *, dry_run: bool) -> None:
    meta = prepared.meta
    lon, lat = prepared.center_lonlat
    mode = "DRY RUN" if dry_run else "IMPORT"
    print(f"{mode}: {BODY} LOLA reference")
    print(f"  source:          {meta.source}")
    print(
        f"  source raster:   {meta.driver} {meta.width}x{meta.height} px, "
        f"{meta.native_resolution_m:.1f} m/pix, {meta.dtype}, "
        f"nodata={'NaN' if meta.nodata is None else meta.nodata}"
    )
    print(
        f"  source layout:   {'tiled' if meta.is_tiled else 'striped'}, "
        f"blocks={meta.block_shapes[0][0]}x{meta.block_shapes[0][1]}, "
        f"compress={meta.compress or 'none'}, overviews={list(meta.overviews) or 'none'}"
    )
    print(f"  source CRS:      {meta.crs_proj4}")
    print(
        f"  access:          remote={meta.source_is_remote}, "
        f"range_reads={meta.accept_ranges}, size={format_bytes(meta.source_size_bytes)}"
    )
    if meta.etag or meta.last_modified:
        print(f"  remote validators: ETag={meta.etag} Last-Modified={meta.last_modified}")
    print(
        f"  ROI centre:      X={prepared.center_xy[0]:.1f} m, "
        f"Y={prepared.center_xy[1]:.1f} m -> lon={lon:.6f}, lat={lat:.6f}"
    )
    print(
        f"  analysis window: 1980 m, source crop {prepared.crop_shape[1]}x"
        f"{prepared.crop_shape[0]} px"
    )
    print(
        f"  crop bounds:     X {prepared.crop_bounds_xy[0]:.1f} .. "
        f"{prepared.crop_bounds_xy[2]:.1f}, Y {prepared.crop_bounds_xy[1]:.1f} .. "
        f"{prepared.crop_bounds_xy[3]:.1f}"
    )
    print(
        f"  estimated range bytes: {format_bytes(prepared.estimated_range_bytes)} "
        "(conservative block/row upper bound)"
    )
    if prepared.from_cache:
        print(
            f"  cache:           HIT {prepared.cache_path} "
            f"({format_bytes(prepared.cache_bytes)}, fetched {prepared.access_timestamp})"
        )
    elif prepared.cache_path is not None:
        print(
            f"  cache:           {prepared.cache_path} "
            f"({format_bytes(prepared.cache_bytes)}, fetched {prepared.access_timestamp})"
        )
    else:
        print("  cache:           would be written under the cache directory")
    if meta.warnings:
        print(f"  source warnings: {', '.join(meta.warnings)}")
    print(f"  manifest output: {output}")


def _print_result(
    prepared: PreparedWindow,
    result,
    output: Path,
) -> None:
    print("  measured features:")
    for feature_id, value in result.features.items():
        print(f"    {feature_id:26s} {value:.6f}")
    print(f"  valid fraction:  {result.valid_fraction:.6f}")
    print(f"  analysis CRS:    {result.analysis_crs}")
    print(f"  window checksum: {result.window_checksum}")
    print(f"  cache:           {prepared.cache_path} ({format_bytes(prepared.cache_bytes)})")
    print(f"  manifest:        {output}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_products:
        print(product_catalog_text())
        return 0
    if args.source != SITE04_LDEM_URL and (
        args.dataset_id == DATASET_ID or args.product_id == PRODUCT_ID
    ):
        print(
            "WARNING: --source is not the documented Site04 LDEM, but the default "
            "Site04 dataset/product id is still in use. Pass --dataset-id, --product-id, "
            "--product-title and --citation for the real product to avoid mislabelling.",
            file=sys.stderr,
        )
    if os.environ.get("PROJ_IGNORE_CELESTIAL_BODY") != "YES":
        print(
            "ERROR [proj_body_override_required]: the shared extractor transforms the "
            "lunar projected CRS to EPSG:4326 for its approximate centre fields; PROJ "
            "requires PROJ_IGNORE_CELESTIAL_BODY=YES for that Moon-to-Earth transform. "
            "The metrics themselves are computed in the projected CRS. Unset the "
            "override or set it to YES before running this importer.",
            file=sys.stderr,
        )
        return 2
    if args.max_window_mb <= 0:
        print("ERROR [invalid_byte_budget]: --max-window-mb must be positive.", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("ERROR [invalid_timeout]: --timeout must be positive.", file=sys.stderr)
        return 2

    try:
        center = resolve_center(
            center_x=args.center_x,
            center_y=args.center_y,
            bbox=args.bbox,
        )
    except LolaImportError as exc:
        print(f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        return 2

    reference_id, display_name, roi_name, roi_provenance = _resolve_labels(args)
    cache_dir = (args.cache_dir or default_cache_dir()).expanduser()
    output = (args.output or default_manifest_path()).expanduser()

    try:
        prepared = LolaReferenceProvider().prepare(
            source=args.source,
            center_xy=center,
            cache_dir=cache_dir,
            timeout=args.timeout,
            max_retries=args.max_retries,
            max_window_bytes=int(args.max_window_mb * 1024 * 1024),
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    except (LolaImportError, ExtractionError) as exc:
        print(f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        print("No manifest was written.", file=sys.stderr)
        return 2
    except ProjError as exc:
        print(f"ERROR [proj_body_override_required]: {exc}", file=sys.stderr)
        print(
            "Set PROJ_IGNORE_CELESTIAL_BODY=YES; the shared extractor needs it for the "
            "lunar centre transform. No manifest was written.",
            file=sys.stderr,
        )
        return 2
    except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
        print(f"ERROR [source_access_failed]: {exc}", file=sys.stderr)
        print("No manifest was written.", file=sys.stderr)
        return 2

    _print_plan(prepared, output, dry_run=args.dry_run)
    if args.dry_run:
        print(
            "\nDRY RUN complete: no analysis window was downloaded and no manifest was "
            "written. Metadata HEAD/range reads may have occurred for a remote source."
        )
        return 0

    if prepared.cache_path is None:
        print("ERROR [internal_cache_missing]: no cached window was produced.", file=sys.stderr)
        return 2

    try:
        result = extract_features(
            str(prepared.cache_path),
            center_xy=center,
            source_label=f"{args.dataset_id}|{prepared.source_label}",
            body=BODY,
        )
        dataset = default_dataset_info(
            prepared,
            dataset_id=args.dataset_id,
            product_id=args.product_id,
            title=args.product_title,
            provider=args.provider,
            citation=args.citation,
        )
        document = build_manifest(
            prepared,
            result,
            dataset,
            reference_id=reference_id,
            display_name=display_name,
            roi_name=roi_name,
            roi_provenance=roi_provenance,
            data_kind="observed",
            observation_quality="verified",
        )
        validate_manifest(document, default_schema_path())
        write_json_atomic(document, output)
    except (LolaImportError, ExtractionError) as exc:
        print(f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        print(
            "No manifest was written. Any previously committed manifest is unchanged.",
            file=sys.stderr,
        )
        return 2
    except ProjError as exc:
        print(f"ERROR [proj_body_override_required]: {exc}", file=sys.stderr)
        print(
            "Set PROJ_IGNORE_CELESTIAL_BODY=YES; the shared extractor needs it for the "
            "lunar centre transform. No manifest was written.",
            file=sys.stderr,
        )
        return 2
    except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
        print(f"ERROR [extraction_failed]: {exc}", file=sys.stderr)
        print(
            "No manifest was written. Any previously committed manifest is unchanged.",
            file=sys.stderr,
        )
        return 2

    print("\nMeasured from the real cached LOLA window:")
    _print_result(prepared, result, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
