#!/usr/bin/env python
"""Build a bounded, observed Moon/Mars D03 thermal pilot (Issue #43).

Moon: LRO Diviner Polar Cumulative Products (PCP), bolometric brightness
temperature at 240 m and one 0.25 h local-time bin per selected file.
Mars: USGS THEMIS-derived quantitative thermal-inertia mosaic, read only as
small 128 x 128 ISIS tiles around the existing Jezero reference windows.

Raw source files are cached under the gitignored ``var/cache/thermal-43`` and
are never committed. Only the portable typed-v2 export is written.

This is evidence-only. Brightness temperature is not kinetic surface
temperature; thermal inertia is a separate feature. No cross-body score is
produced and no Earth D03 observation is changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_pipelines.dimensions import validate_dimension_observation
from analog_pipelines.thermal_planetary import (
    MARS_TI_ABSOLUTE_ACCURACY_FRACTION,
    MARS_TI_CITATION,
    MARS_TI_CRS,
    MARS_TI_DATASET_ID,
    MARS_TI_DOI,
    MARS_TI_NATIVE_RESOLUTION_M,
    MARS_TI_PRODUCT_ID,
    MARS_TI_PRODUCT_URL,
    MARS_TI_TILE,
    MARS_TI_UNIT,
    MARS_TI_VERSION,
    MOON_PCP_CRS,
    PCP_BINS,
    PCP_CALIBRATION_SOFTWARE_VERSION,
    PCP_CITATION,
    PCP_COLLECTION_LID,
    PCP_COLLECTION_URL,
    PCP_DATASET_ID,
    PCP_DOI,
    PCP_NATIVE_RESOLUTION_M,
    PCP_SEASON,
    PCP_TEMPORAL_END,
    PCP_TEMPORAL_START,
    IsisCubeTile,
    assert_no_diurnal_range,
    brightness_observation_payload,
    collect_ti_window,
    compute_window_statistics,
    decode_isis_tile,
    pcp_center_xy_m,
    select_pcp_windows,
    thermal_inertia_observation_payload,
)
from analog_pipelines.typed_contracts import (
    AreaV2,
    DatasetV2,
    SourceAssetV2,
    TypedObservation,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = "pipelines/pilots/d03_planetary_thermal"
DEFAULT_CACHE = "var/cache/thermal-43"
MOON_MANIFEST_DIR = ROOT / "pipelines" / "planetary" / "manifests"
MARS_MANIFEST = MOON_MANIFEST_DIR / "ctx_mars_references.json"
EXPORT_VERSION = "planetary-thermal-pilot-1.0.0"
WINDOW_SIZE_M = 1980.0
WINDOW_RADIUS_M = WINDOW_SIZE_M / 2.0
USER_AGENT = "planetary-analog/0.1 (+D03-thermal-pilot)"


@dataclass(frozen=True, slots=True)
class ThermalReference:
    reference_id: str
    display_name: str
    body: str
    latitude_deg: float
    longitude_deg: float
    dataset_id: str
    source_manifest: str


def repo_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_moon_references() -> list[ThermalReference]:
    references: list[ThermalReference] = []
    for path in sorted(MOON_MANIFEST_DIR.glob("lola_*_reference*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for reference in raw.get("references", []):
            if reference.get("body") != "Moon":
                continue
            references.append(
                ThermalReference(
                    reference_id=reference["reference_id"],
                    display_name=reference.get("display_name") or reference["reference_id"],
                    body="Moon",
                    latitude_deg=float(reference["lat"]),
                    longitude_deg=float(reference["lon"]),
                    dataset_id=reference["dataset_id"],
                    source_manifest=repo_relative(path),
                )
            )
    if not references:
        raise SystemExit("no Moon reference windows found")
    return references


def load_mars_references() -> list[ThermalReference]:
    raw = json.loads(MARS_MANIFEST.read_text(encoding="utf-8"))
    references = [
        ThermalReference(
            reference_id=reference["reference_id"],
            display_name=reference.get("display_name") or reference["reference_id"],
            body="Mars",
            latitude_deg=float(reference["lat"]),
            longitude_deg=float(reference["lon"]),
            dataset_id=reference["dataset_id"],
            source_manifest=repo_relative(MARS_MANIFEST),
        )
        for reference in raw.get("references", [])
        if reference.get("body") == "Mars"
    ]
    if not references:
        raise SystemExit("no Mars reference windows found")
    return references


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(url: str, path: Path, *, expected_bytes: int, allow_download: bool) -> None:
    if path.exists() and path.stat().st_size == expected_bytes:
        return
    if not allow_download:
        raise SystemExit(
            f"missing cache file {repo_relative(path)}; rerun with --download "
            f"(expected {expected_bytes} bytes from {url})"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.stat().st_size if path.exists() else 0
    headers = {"User-Agent": USER_AGENT}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(url, headers=headers)
    mode = "ab" if existing else "wb"
    try:
        with urllib.request.urlopen(request, timeout=120) as response, path.open(mode) as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and path.exists() and path.stat().st_size == expected_bytes:
            return
        raise
    size = path.stat().st_size
    if size != expected_bytes:
        raise SystemExit(
            f"downloaded {repo_relative(path)} is {size} bytes, expected {expected_bytes}"
        )


def fetch_tile_block(
    tile: IsisCubeTile,
    tile_row: int,
    tile_col: int,
    cache_dir: Path,
    *,
    allow_download: bool,
) -> bytes:
    index = tile.tile_index(tile_row, tile_col)
    path = cache_dir / f"{tile.product_id}_{index:05d}.bin"
    if not path.exists():
        if not allow_download:
            raise SystemExit(
                f"missing cached ISIS tile {repo_relative(path)}; rerun with --download"
            )
        start, end = tile.tile_byte_range(tile_row, tile_col)
        request = urllib.request.Request(
            tile.url,
            headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
        if len(payload) != tile.tile_bytes:
            raise SystemExit(
                f"range {start}-{end} returned {len(payload)} bytes, expected {tile.tile_bytes}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    payload = path.read_bytes()
    if len(payload) != tile.tile_bytes:
        raise SystemExit(f"cached tile {repo_relative(path)} has the wrong size")
    return payload


def area_payload(
    reference: ThermalReference,
    *,
    crs: str,
    center_x: float,
    center_y: float,
    coordinate_frame: str,
    native_resolution_m: float,
    notes: str,
) -> AreaV2:
    half = WINDOW_RADIUS_M
    return AreaV2(
        area_id=f"thermal-{reference.reference_id}",
        body=reference.body,
        geometry=None,
        bbox={
            "west": center_x - half,
            "south": center_y - half,
            "east": center_x + half,
            "north": center_y + half,
        },
        crs=crs,
        geometry_crs=None,
        analysis_crs=crs,
        coordinate_frame=coordinate_frame,
        area_km2=math.pi * (WINDOW_RADIUS_M / 1000.0) ** 2,
        source_area_refs=[reference.reference_id],
        legacy_provenance={
            "reference_manifest": reference.source_manifest,
            "reference_id": reference.reference_id,
            "source_dataset_id": reference.dataset_id,
            "reference_latitude_deg": reference.latitude_deg,
            "reference_longitude_deg": reference.longitude_deg,
            "window_size_m": WINDOW_SIZE_M,
            "native_resolution_m": native_resolution_m,
        },
        notes=notes,
    )


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> dict[str, Any]:
    cache = Path(args.cache)
    pcp_cache = cache / "pcp"
    ti_cache = cache / "themis-ti"
    output = Path(args.output)
    generated_at = datetime.now(UTC).isoformat()

    moon_refs = load_moon_references()
    mars_refs = load_mars_references()

    # --- Moon: PCP files, one scan per local-time bin -----------------------
    for item in PCP_BINS:
        download_file(
            item.url,
            pcp_cache / item.product_id.lower(),
            expected_bytes=item.source_bytes,
            allow_download=args.download,
        )

    pcp_assets: list[SourceAssetV2] = []
    pcp_asset_by_bin: dict[str, str] = {}
    for item in PCP_BINS:
        path = pcp_cache / item.product_id.lower()
        asset_id = f"pcp:{item.bin_id}"
        pcp_asset_by_bin[item.bin_id] = asset_id
        pcp_assets.append(
            SourceAssetV2(
                asset_id=asset_id,
                dataset_id=PCP_DATASET_ID,
                uri=item.url,
                checksum_algorithm="sha256",
                checksum=sha256_file(path),
                checksum_scope="source",
                native_crs=MOON_PCP_CRS,
                native_units="K (bolometric brightness temperature)",
                scale=1.0,
                offset=0.0,
                nodata=None,
                bytes=item.source_bytes,
                fetched_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
                redistribution_allowed="unknown",
            )
        )

    moon_centers = {
        reference.reference_id: (reference.latitude_deg, reference.longitude_deg)
        for reference in moon_refs
    }
    moon_windows: dict[str, dict[str, Any]] = {item.bin_id: {} for item in PCP_BINS}
    for item in PCP_BINS:
        selections = select_pcp_windows(
            pcp_cache / item.product_id.lower(),
            centers=moon_centers,
            radius_m=WINDOW_RADIUS_M,
        )
        for reference in moon_refs:
            samples = selections[reference.reference_id]
            moon_windows[item.bin_id][reference.reference_id] = {
                "samples": samples,
                "stats": compute_window_statistics(samples),
            }

    # --- Mars: only the 128 x 128 ISIS tiles touched by the windows ---------
    mars_tile_cache: dict[tuple[int, int], tuple[float, ...]] = {}
    mars_tile_bytes: dict[tuple[int, int], bytes] = {}
    for reference in mars_refs:
        sample, line = MARS_TI_TILE.sample_line_for(reference.latitude_deg, reference.longitude_deg)
        rows, cols = MARS_TI_TILE.tile_range_for_window(sample, line, WINDOW_RADIUS_M)
        for tile_row in rows:
            for tile_col in cols:
                key = (tile_row, tile_col)
                if key in mars_tile_cache:
                    continue
                payload = fetch_tile_block(
                    MARS_TI_TILE,
                    tile_row,
                    tile_col,
                    ti_cache,
                    allow_download=args.download,
                )
                mars_tile_bytes[key] = payload
                mars_tile_cache[key] = decode_isis_tile(payload, MARS_TI_TILE)

    mars_windows: dict[str, dict[str, Any]] = {}
    for reference in mars_refs:
        sample, line = MARS_TI_TILE.sample_line_for(reference.latitude_deg, reference.longitude_deg)
        samples = collect_ti_window(
            tile=MARS_TI_TILE,
            sample=sample,
            line=line,
            radius_m=WINDOW_RADIUS_M,
            decoded_tiles=mars_tile_cache,
        )
        mars_windows[reference.reference_id] = {
            "samples": samples,
            "stats": compute_window_statistics(samples),
        }

    mars_asset_digest = hashlib.sha256()
    mars_asset_bytes = 0
    for key in sorted(mars_tile_bytes):
        payload = mars_tile_bytes[key]
        mars_asset_digest.update(key[0].to_bytes(4, "big") + key[1].to_bytes(4, "big"))
        mars_asset_digest.update(payload)
        mars_asset_bytes += len(payload)
    mars_asset_id = f"themis-ti:{MARS_TI_PRODUCT_ID}:blocks"
    mars_asset = SourceAssetV2(
        asset_id=mars_asset_id,
        dataset_id=MARS_TI_DATASET_ID,
        uri=MARS_TI_TILE.url,
        checksum_algorithm="sha256",
        checksum=mars_asset_digest.hexdigest(),
        checksum_scope="window",
        native_crs=MARS_TI_CRS,
        native_units=MARS_TI_UNIT,
        scale=1.0,
        offset=0.0,
        nodata=None,
        bytes=mars_asset_bytes,
        fetched_at=generated_at,
        redistribution_allowed="yes",
    )

    # --- Areas, datasets and observations -----------------------------------
    areas: list[AreaV2] = []
    observations: list[dict[str, Any]] = []
    for reference in moon_refs:
        center_x, center_y = pcp_center_xy_m(reference.latitude_deg, reference.longitude_deg)
        areas.append(
            area_payload(
                reference,
                crs=MOON_PCP_CRS,
                center_x=center_x,
                center_y=center_y,
                coordinate_frame="lunar_projected",
                native_resolution_m=PCP_NATIVE_RESOLUTION_M,
                notes=(
                    "Thermal-only area around an existing LOLA reference window. "
                    "The product's own polar-stereographic x/y convention is used; "
                    "no 90 m resampling."
                ),
            )
        )
        for item in PCP_BINS:
            record = moon_windows[item.bin_id][reference.reference_id]
            observations.append(
                brightness_observation_payload(
                    area_id=f"thermal-{reference.reference_id}",
                    reference_id=reference.reference_id,
                    display_name=reference.display_name,
                    latitude_deg=reference.latitude_deg,
                    longitude_deg=reference.longitude_deg,
                    bin_metadata=item,
                    stats=record["stats"],
                    window_size_m=WINDOW_SIZE_M,
                    source_manifest=reference.source_manifest,
                    dataset_id=PCP_DATASET_ID,
                    asset_id=pcp_asset_by_bin[item.bin_id],
                )
            )

    for reference in mars_refs:
        sample, line = MARS_TI_TILE.sample_line_for(reference.latitude_deg, reference.longitude_deg)
        areas.append(
            area_payload(
                reference,
                crs=MARS_TI_CRS,
                center_x=MARS_TI_TILE.body_radius_m * math.radians(reference.longitude_deg),
                center_y=MARS_TI_TILE.body_radius_m * math.radians(reference.latitude_deg),
                coordinate_frame="mars_projected",
                native_resolution_m=MARS_TI_NATIVE_RESOLUTION_M,
                notes=(
                    "Thermal-only area around an existing Jezero CTX reference window, "
                    "in the THEMIS simple-cylindrical planetocentric tile frame. "
                    "Native 100 m; no 90 m resampling."
                ),
            )
        )
        record = mars_windows[reference.reference_id]
        observations.append(
            thermal_inertia_observation_payload(
                area_id=f"thermal-{reference.reference_id}",
                reference_id=reference.reference_id,
                display_name=reference.display_name,
                latitude_deg=reference.latitude_deg,
                longitude_deg=reference.longitude_deg,
                stats=record["stats"],
                window_size_m=WINDOW_SIZE_M,
                source_manifest=reference.source_manifest,
                dataset_id=MARS_TI_DATASET_ID,
                asset_id=mars_asset_id,
                tile=MARS_TI_TILE,
            )
        )

    assert_no_diurnal_range(observations)

    datasets = [
        DatasetV2(
            dataset_id=PCP_DATASET_ID,
            body="Moon",
            provider=(
                "NASA Planetary Data System Geosciences Node / UCLA Diviner "
                "Lunar Radiometer Experiment"
            ),
            product_id="; ".join(item.product_id for item in PCP_BINS),
            version=(
                f"PDS4 {PCP_BUNDLE_VERSION_LABEL()}; calibration software "
                f"{PCP_CALIBRATION_SOFTWARE_VERSION}"
            ),
            url=PCP_COLLECTION_URL,
            license=(
                "NASA PDS public archive; the PDS4 label carries no explicit licence "
                "field; cite Williams et al. (2019)"
            ),
            license_url=PCP_COLLECTION_URL,
            verification_status="verified",
            native_crs=MOON_PCP_CRS,
            native_resolution_m=PCP_NATIVE_RESOLUTION_M,
            temporal_support=(
                f"{PCP_TEMPORAL_START} to {PCP_TEMPORAL_END}; {PCP_SEASON}; 0.25 h local-time bins"
            ),
            depth_support="surface thermal-infrared brightness temperature",
            redistribution_allowed="unknown",
        ),
        DatasetV2(
            dataset_id=MARS_TI_DATASET_ID,
            body="Mars",
            provider=(
                "USGS Astrogeology Science Center / Arizona State University / "
                "NASA Mars Data Analysis Program"
            ),
            product_id=MARS_TI_PRODUCT_ID,
            version=MARS_TI_VERSION,
            url=MARS_TI_PRODUCT_URL,
            license="Public domain (USGS Astrogeology access constraints); cite authors",
            license_url=MARS_TI_PRODUCT_URL,
            verification_status="verified",
            native_crs=MARS_TI_CRS,
            native_resolution_m=MARS_TI_NATIVE_RESOLUTION_M,
            temporal_support=(
                "Apparent thermal inertia is time-independent by retrieval; per-pixel "
                "acquisition time/season is not carried by the mosaic"
            ),
            depth_support="upper few centimetres (apparent thermal inertia)",
            redistribution_allowed="yes",
        ),
    ]

    validated: list[TypedObservation] = []
    for payload in observations:
        observation = TypedObservation.model_validate(payload)
        errors = validate_dimension_observation(observation)
        if errors:
            raise SystemExit(f"{observation.observation_id}: {errors}")
        validated.append(observation)

    # --- Write export -------------------------------------------------------
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "areas.json", [item.model_dump(mode="json") for item in areas])
    write_json(output / "datasets.json", [item.model_dump(mode="json") for item in datasets])
    write_json(
        output / "assets.json",
        [item.model_dump(mode="json") for item in [*pcp_assets, mars_asset]],
    )
    observations_path = output / "observations.jsonl"
    observations_path.write_text(
        "".join(
            json.dumps(item.model_dump(mode="json"), sort_keys=True) + "\n" for item in validated
        ),
        encoding="utf-8",
    )

    counts = Counter()
    by_body: Counter[str] = Counter()
    by_feature: Counter[str] = Counter()
    by_state: Counter[str] = Counter()
    by_local_time: Counter[str] = Counter()
    for item in validated:
        counts["observations"] += 1
        by_body[item.body] += 1
        by_feature[item.feature_id] += 1
        by_state[item.state] += 1
        if item.temporal_support.local_solar_time:
            by_local_time[item.temporal_support.local_solar_time] += 1

    def _succeeded(records: dict[str, dict[str, Any]]) -> int:
        return sum(1 for record in records.values() if record["stats"]["n"] > 0)

    manifest = {
        "export_version": EXPORT_VERSION,
        "generated_at": generated_at,
        "data_kind": "observed",
        "scope": (
            "18 existing Moon LOLA reference windows at two Diviner PCP local-time "
            "bins, plus 5 existing Mars Jezero CTX reference windows from one THEMIS "
            "quantitative thermal-inertia tile. Not global coverage."
        ),
        "counts": {
            "areas": len(areas),
            "datasets": len(datasets),
            "assets": len(pcp_assets) + 1,
            "observations": len(validated),
            "moon_reference_windows": len(moon_refs),
            "moon_windows_with_samples": _succeeded(
                moon_windows["pcp_avg_tbol_pols_sum_ltim01_240"]
            ),
            "mars_reference_windows": len(mars_refs),
            "mars_windows_with_samples": _succeeded(mars_windows),
            "by_body": dict(sorted(by_body.items())),
            "by_feature": dict(sorted(by_feature.items())),
            "by_state": dict(sorted(by_state.items())),
            "by_local_solar_time": dict(sorted(by_local_time.items())),
        },
        "moon": {
            "product": "LRO Diviner Polar Cumulative Products (bolometric brightness temperature)",
            "product_ids": [item.product_id for item in PCP_BINS],
            "bundle_lid": "urn:nasa:pds:lro_diviner_derived1",
            "collection_lid": PCP_COLLECTION_LID,
            "version": f"PDS4 {PCP_BUNDLE_VERSION_LABEL()}; calibration software {PCP_CALIBRATION_SOFTWARE_VERSION}",
            "doi": PCP_DOI,
            "citation": PCP_CITATION,
            "license": ("NASA PDS public archive; no explicit licence field in the PDS4 label"),
            "native_resolution_m": PCP_NATIVE_RESOLUTION_M,
            "footprint": "south polar cap 80-90 S, full longitude range",
            "local_time_bins": [
                {
                    "product_id": item.product_id,
                    "local_solar_time": item.local_time_label,
                    "source_bytes": item.source_bytes,
                    "url": item.url,
                }
                for item in PCP_BINS
            ],
            "temporal_start": PCP_TEMPORAL_START,
            "temporal_end": PCP_TEMPORAL_END,
            "season": PCP_SEASON,
            "units": "K (bolometric brightness temperature)",
            "uncertainty": "no per-bin uncertainty supplied by the product",
            "crs": MOON_PCP_CRS,
            "nodata": "sparse bins omitted; invalid/unknown constants -9998/-9999 rejected",
            "coverage_test": (
                "reference latitude within [-90, -80] and at least one native 240 m "
                "bin within a 990 m radius of the reference centre"
            ),
        },
        "mars": {
            "product": "USGS THEMIS-derived global thermal inertia mosaic, quantitative 32-bit",
            "product_id": MARS_TI_PRODUCT_ID,
            "version": MARS_TI_VERSION,
            "doi": MARS_TI_DOI,
            "citation": MARS_TI_CITATION,
            "license": "Public domain (USGS Astrogeology); cite authors",
            "native_resolution_m": MARS_TI_NATIVE_RESOLUTION_M,
            "footprint": "tile 00N060E: 0-30 N, 60-120 E",
            "tiling": "ISIS3 tiled cube, 128 x 128 tiles, float32 LSB",
            "source_bytes": MARS_TI_TILE_BYTES(),
            "bytes_read": mars_asset_bytes,
            "units": MARS_TI_UNIT,
            "uncertainty": (
                "published absolute accuracy ~20%, relative within one image ~10%; "
                "no per-pixel uncertainty layer"
            ),
            "crs": MARS_TI_CRS,
            "nodata": (
                "no explicit no-data constant in the ISIS label; non-finite, "
                "non-positive and >10000 values rejected and counted"
            ),
            "coverage_test": (
                "reference coordinate inside the tile footprint and at least one "
                "native 100 m pixel within a 990 m radius"
            ),
            "absolute_accuracy_fraction": MARS_TI_ABSOLUTE_ACCURACY_FRACTION,
        },
        "blockers": [
            "Moon PCP is a multi-year composite at one local-time bin, not a single acquisition or same-sol time series.",
            "Moon PCP supplies no per-bin uncertainty; the reported value is a spatial window mean.",
            "Bolometric brightness temperature assumes unit emissivity and is not kinetic surface temperature.",
            "No diurnal range or change rate is computed because local-time bins are not same-sol samples.",
            "Mars THEMIS TI has no per-pixel uncertainty or QA layer; absolute accuracy is ~20%.",
            "Mars THEMIS TI retrieval does not correct atmospheric clouds, surface slopes or subsurface ice/bedrock.",
            "No cross-body D03 score; Earth NASA POWER context is unchanged.",
        ],
        "checksums": {
            "observations_sha256": hashlib.sha256(observations_path.read_bytes()).hexdigest(),
            "areas_sha256": sha256_file(output / "areas.json"),
            "datasets_sha256": sha256_file(output / "datasets.json"),
            "assets_sha256": sha256_file(output / "assets.json"),
        },
        "reproduce": [
            "uv run python scripts/build_planetary_thermal_pilot.py --download",
            "uv run python scripts/build_planetary_thermal_pilot.py",
            "uv run pytest pipelines/tests/test_thermal_planetary.py -q",
        ],
    }
    write_json(output / "manifest.json", manifest)
    write_json(
        output / "plan.json",
        {
            "plan_version": "acquisition-plan-1.0.0",
            "scope": "pilot-d03-planetary-thermal",
            "dimension": "D03",
            "bodies": ["Moon", "Mars"],
            "moon_reference_windows": [reference.reference_id for reference in moon_refs],
            "mars_reference_windows": [reference.reference_id for reference in mars_refs],
            "local_time_bins": [item.local_time_label for item in PCP_BINS],
            "window_size_m": WINDOW_SIZE_M,
        },
    )
    (output / "REPORT.md").write_text(
        render_report(manifest, moon_refs, mars_refs, moon_windows, mars_windows),
        encoding="utf-8",
    )
    (output / "README.md").write_text(render_readme(manifest), encoding="utf-8")
    return manifest


def PCP_BUNDLE_VERSION_LABEL() -> str:
    return "collection 1.0 / product version 1"


def MARS_TI_TILE_BYTES() -> int:
    from analog_pipelines.thermal_planetary import MARS_TI_S3_BYTES

    return MARS_TI_S3_BYTES


def render_report(
    manifest: dict[str, Any],
    moon_refs: list[ThermalReference],
    mars_refs: list[ThermalReference],
    moon_windows: dict[str, dict[str, Any]],
    mars_windows: dict[str, dict[str, Any]],
) -> str:
    counts = manifest["counts"]
    lines = [
        "# D03 planetary thermal pilot (Moon Diviner PCP + Mars THEMIS TI)",
        "",
        f"Generated: `{manifest['generated_at']}`  ",
        f"Export: `{manifest['export_version']}`  ",
        f"Data kind: `{manifest['data_kind']}` (bounded observed product values)",
        "",
        "## Actual content",
        "",
        "| Item | Value |",
        "|---|---:|",
        f"| Moon reference windows | {counts['moon_reference_windows']} |",
        f"| Moon windows with samples (ltim01) | {counts['moon_windows_with_samples']} |",
        f"| Mars reference windows | {counts['mars_reference_windows']} |",
        f"| Mars windows with samples | {counts['mars_windows_with_samples']} |",
        f"| Areas | {counts['areas']} |",
        f"| Observations | {counts['observations']} |",
        f"| By body | `{counts['by_body']}` |",
        f"| By feature | `{counts['by_feature']}` |",
        f"| By state | `{counts['by_state']}` |",
        f"| By local solar time | `{counts['by_local_solar_time']}` |",
        "",
        "## Moon — Diviner PCP bolometric brightness temperature",
        "",
        "Native 240 m bins, 1.98 km circular window, two 0.25 h local-time bins of the "
        "southern-summer composite. `n` is the number of native bins in the window; "
        "empty bins are absent from the product and are not zero-filled.",
        "",
        "| Reference window | 00:00-00:15 mean K (n) | 12:00-12:15 mean K (n) | min–max K (midnight) |",
        "|---|---:|---:|---:|",
    ]
    for reference in moon_refs:
        midnight = moon_windows["pcp_avg_tbol_pols_sum_ltim01_240"][reference.reference_id]["stats"]
        noon = moon_windows["pcp_avg_tbol_pols_sum_ltim49_240"][reference.reference_id]["stats"]
        lines.append(
            f"| `{reference.reference_id}` | {fmt(midnight['mean'])} ({midnight['n']}) | "
            f"{fmt(noon['mean'])} ({noon['n']}) | "
            f"{fmt(midnight['min'])}–{fmt(midnight['max'])} |"
        )
    lines += [
        "",
        "## Mars — THEMIS apparent thermal inertia",
        "",
        "Native 100 m, 1.98 km circular window, one quantitative tile (`00N060E`). "
        "`n` is the number of native pixels in the window.",
        "",
        "| Reference window | Mean TI (n) | Min–max TI | Published ±20% range |",
        "|---|---:|---:|---:|",
    ]
    for reference in mars_refs:
        stats = mars_windows[reference.reference_id]["stats"]
        lower = None if stats["mean"] is None else stats["mean"] * 0.8
        upper = None if stats["mean"] is None else stats["mean"] * 1.2
        lines.append(
            f"| `{reference.reference_id}` | {fmt(stats['mean'])} ({stats['n']}) | "
            f"{fmt(stats['min'])}–{fmt(stats['max'])} | {fmt(lower)}–{fmt(upper)} |"
        )
    lines += [
        "",
        "TI unit: `J m^-2 K^-1 s^-1/2`.",
        "",
        "## Scientific limits and blockers",
        "",
    ]
    lines += [f"- {item}" for item in manifest["blockers"]]
    lines += [
        "",
        "Additional limits:",
        "",
        "- Moon values are spatial means of 240 m bins; they are not 90 m measurements.",
        "- Mars values are spatial means of 100 m pixels; they are not 90 m measurements.",
        "- PCP local-time bins are different multi-year samples; a difference between the "
        "two bins is not a same-sol diurnal range and is therefore not emitted.",
        "- Mars thermal inertia is not surface temperature and has no per-pixel QA.",
        "- Only the existing reference windows are covered; this is not a global survey.",
        "- No raw raster or PCP table is committed; only this derived export.",
        "",
        "## Reproduction",
        "",
        "```bash",
        *manifest["reproduce"],
        "```",
        "",
        "The first command downloads the two PCP `.tab` files and the small 128 x 128 "
        "ISIS tile blocks into the gitignored `var/cache/thermal-43` cache. The second "
        "command rebuilds offline from that cache.",
        "",
    ]
    return "\n".join(lines)


def render_readme(manifest: dict[str, Any]) -> str:
    return f"""# D03 planetary thermal pilot

Real, bounded D03 thermal evidence from two official public products:

- **Moon:** LRO Diviner Polar Cumulative Products (PCP), bolometric brightness
  temperature, 240 m native bins, 0.25 h local-time bins, southern summer
  2009-07-05 to 2019-02-17.
- **Mars:** USGS THEMIS-derived quantitative thermal-inertia mosaic, 100 m/pixel,
  ISIS3 tiled cube; only small 128 x 128 tiles around the existing Jezero
  reference windows were read by HTTP range request.

Observations: **{manifest["counts"]["observations"]}**. Areas:
**{manifest["counts"]["areas"]}**. States: `{manifest["counts"]["by_state"]}`.

## Scientific limits

- Diviner PCP is a multi-year composite at one local-time bin, not a single
  acquisition and not a same-sol time series. No diurnal range or change rate is
  computed.
- Bolometric brightness temperature assumes unit emissivity; it is **not**
  kinetic surface temperature.
- THEMIS thermal inertia is a separate feature, with the Fergason et al. (2006)
  retrieval, ~20% published absolute accuracy and no per-pixel uncertainty/QA.
- No 240 m or 100 m value is presented as 90 m. Nothing is resampled.
- No cross-body D03 score. Earth NASA POWER context is unchanged.
- Only 18 existing Moon and 5 existing Mars reference windows are covered; this
  is not global or exhaustive coverage.

## Files

`areas.json`, `datasets.json`, `assets.json`, `observations.jsonl`,
`manifest.json`, `plan.json`, `REPORT.md`.

## Reproduce

```bash
{chr(10).join(manifest["reproduce"])}
```

Raw PCP tables and ISIS tile blocks stay in the gitignored
`var/cache/thermal-43` directory and are never committed.
"""


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument(
        "--download",
        action="store_true",
        help="fetch missing PCP tables and THEMIS tile ranges from the official sources",
    )
    args = parser.parse_args(argv)
    manifest = build(args)
    print(
        f"wrote {manifest['counts']['observations']} observations, "
        f"{manifest['counts']['areas']} areas to {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
