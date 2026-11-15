"""Reproducible on-demand D01 AOI analysis for Moon and Mars (Issue #132).

This module is storage independent: it imports no FastAPI/SQLAlchemy code. It owns
the scientific selection, fixture verification, recomputation and explicit
coverage/unsupported statements that the API job layer persists.

Two analysis kinds exist:

* ``committed_reference_fixture`` - the AOI is covered by the committed 5 km
  planetary coarse pilot cells. Each cell's small elevation fixture is checksum
  verified, the shared Horn 3x3 slope and ``extract_coarse_features`` aggregation
  are recomputed, and the recomputed values must match the committed manifest.
* ``live_bounded_read`` - Moon only, explicitly opt-in. A single bounded
  ``/vsicurl`` 5 km window is read from the verified NASA PGDA LOLA128 source in a
  local lunar azimuthal-equidistant CRS. It is labelled as a new read, never as a
  committed reference fixture.

Only committed cell footprints are ever treated as coverage. Basemap extents are
not coverage, no value is zero-filled, and D02-D06 are reported as unsupported
for on-demand analysis instead of being fabricated.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS, Geod

from analog_pipelines.coarse_features import (
    COARSE_FEATURE_BY_ID,
    CoarseExtractionError,
    extract_coarse_features,
)
from analog_pipelines.features import horn_slope_degrees
from analog_pipelines.planetary_coarse import (
    ANALYSIS_RESOLUTION_M,
    BODY_SPHERE_RADIUS_M,
    MOON_LOLA128,
    PLANETARY_COARSE_METHOD_VERSION,
    PLANETARY_COARSE_SCHEMA_VERSION,
    PLANETARY_SOURCES,
    CoarseCellResult,
    PlanetaryCoarseError,
    PlanetaryDemSource,
    assert_planetary_crs,
    coarse_cell_from_dem,
)

PLANETARY_AOI_METHOD_VERSION = "planetary-terrain-horn-250m-aoi-1.0.0"
PLANETARY_AOI_SCHEMA_VERSION = "planetary-aoi-result-1.0.0"
PLANETARY_COARSE_INDEX_SCHEMA_VERSION = "planetary-coarse-index-1.0.0"

SUPPORTED_BODIES: tuple[str, ...] = ("Moon", "Mars")
DIMENSION_CODES: tuple[str, ...] = ("D01", "D02", "D03", "D04", "D05", "D06")
COORDINATE_FRAMES: dict[str, str] = {
    "Moon": "lunar_planetocentric",
    "Mars": "mars_planetocentric",
}

AOI_MIN_SIDE_M = 1_000.0
# The committed Mars Jezero case (0.19 deg of longitude at 18.5N) is 10.68 km wide on
# the Mars sphere, so the planetary default admits that exact reproducible request.
AOI_MAX_SIDE_M = 12_000.0
ANALYSIS_WINDOW_PIXELS = 20
ANALYSIS_WINDOW_M = ANALYSIS_WINDOW_PIXELS * ANALYSIS_RESOLUTION_M
COARSE_FEATURE_IDS_TUPLE = tuple(COARSE_FEATURE_BY_ID)

ANALYSIS_KIND_COMMITTED = "committed_reference_fixture"
ANALYSIS_KIND_LIVE = "live_bounded_read"
SUPPORT_ANALYSIS_ELIGIBLE = "analysis_eligible_250m"
SUPPORT_EVIDENCE_ONLY = "evidence_only_native_support"

DEFAULT_COARSE_ROOT = Path(__file__).resolve().parents[3] / "pipelines" / "planetary" / "coarse"

_SUPPORTED_MANIFEST_METHODS = (PLANETARY_COARSE_METHOD_VERSION,)
_SUPPORTED_INDEX_SCHEMAS = (PLANETARY_COARSE_INDEX_SCHEMA_VERSION,)
_SUPPORTED_MANIFEST_SCHEMAS = (PLANETARY_COARSE_SCHEMA_VERSION,)
_METRE_UNITS = {"m", "metre", "meter", "metres", "meters"}

_STORED_MOON_EVIDENCE = {
    "D03": (
        "Stored lunar D03 evidence exists for pre-indexed typed areas (for example "
        "Diviner brightness temperature), but this engine does not measure D03 for a "
        "new AOI."
    ),
    "D04": (
        "Stored lunar D04 evidence exists for pre-indexed typed areas (for example "
        "LP neutron and Mini-RF CPR evidence), but this engine does not measure D04 "
        "and does not treat it as confirmed ice."
    ),
    "D05": (
        "Stored lunar D05 evidence is model solar geometry for pre-indexed typed areas, "
        "not an observed illumination/radiation measurement for this new AOI."
    ),
}

_STORED_MARS_EVIDENCE = {
    "D03": (
        "Stored Mars D03 THEMIS apparent thermal-inertia evidence exists for "
        "pre-indexed typed areas only; no on-demand D03 source is wired here."
    ),
    "D04": (
        "Stored Mars D04 HEND epithermal neutron evidence exists for pre-indexed typed "
        "areas only; it is evidence, not a confirmed ice measurement."
    ),
    "D05": (
        "Stored Mars D05 SPICE model solar geometry exists for pre-indexed typed areas "
        "only; it is not an observed illumination/radiation measurement."
    ),
}

_UNSUPPORTED_REASONS = {
    "D02": (
        "No verified on-demand mineralogy/composition product is registered in this "
        "engine. Existing committed D02 records apply only to stored areas and are not "
        "interpolated onto this AOI."
    ),
    "D03": (
        "No verified on-demand thermal measurement is registered in this engine for "
        "this body/AOI; no thermal value is fabricated."
    ),
    "D04": (
        "No verified on-demand water/ice product is registered in this engine for this "
        "body/AOI; no ice detection is fabricated."
    ),
    "D05": (
        "No verified on-demand illumination/radiation product is registered in this "
        "engine for this body/AOI; no illumination or dose value is fabricated."
    ),
    "D06": (
        "No verified on-demand regolith/surface-property product is registered in this "
        "engine for this body/AOI; no regolith value is fabricated."
    ),
}


class PlanetaryAoiError(RuntimeError):
    """An on-demand planetary AOI request could not be answered honestly."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class PlanetaryCellCoverage:
    """One committed coarse cell that is available for this body."""

    candidate_id: str
    body: str
    region_id: str
    source_id: str
    source: dict[str, Any]
    bbox_body_lonlat: dict[str, float]
    bbox_metric: dict[str, float]
    fixture_path: str
    fixture_recorded_path: str
    fixture_sha256: str
    fixture_bytes: int
    analysis_resolution_m: float
    source_resolution_m: float
    window_size_m: float
    features: dict[str, float]
    crs_wkt: str
    crs_name: str
    valid_fraction: float
    manifest_path: str
    support_status: str
    analysis_eligible: bool
    recorded: dict[str, Any] = field(repr=False)

    @property
    def cell_id(self) -> str:
        return self.candidate_id

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "cell_id": self.cell_id,
            "body": self.body,
            "region_id": self.region_id,
            "source_id": self.source_id,
            "source": dict(self.source),
            "bbox_body_lonlat": dict(self.bbox_body_lonlat),
            "bbox_metric": dict(self.bbox_metric),
            "fixture_path": self.fixture_recorded_path,
            "fixture_recorded_path": self.fixture_recorded_path,
            "fixture_sha256": self.fixture_sha256,
            "fixture_bytes": self.fixture_bytes,
            "analysis_resolution_m": self.analysis_resolution_m,
            "source_resolution_m": self.source_resolution_m,
            "window_size_m": self.window_size_m,
            "features": dict(self.features),
            "crs_wkt": self.crs_wkt,
            "crs_name": self.crs_name,
            "valid_fraction": self.valid_fraction,
            "manifest_path": self.manifest_path,
            "support_status": self.support_status,
            "analysis_eligible": self.analysis_eligible,
        }


@dataclass(frozen=True, slots=True)
class PlanetaryBodyCoverage:
    """Exact committed-cell coverage for one body, with evidence separated."""

    body: str
    regions: list[dict[str, Any]]
    bbox: dict[str, float] | None
    cell_count: int
    comparable_cell_count: int
    evidence_cell_count: int
    sources: list[dict[str, Any]]
    dimension_support: dict[str, Any]
    notes: str
    evidence_bbox: dict[str, float] | None = None
    evidence_only_cell_ids: tuple[str, ...] = ()
    cells: tuple[PlanetaryCellCoverage, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "planetary-body-coverage-1.0.0",
            "body": self.body,
            "regions": [dict(region) for region in self.regions],
            "bbox": dict(self.bbox) if self.bbox is not None else None,
            "evidence_bbox": (dict(self.evidence_bbox) if self.evidence_bbox is not None else None),
            "cell_count": self.cell_count,
            "comparable_cell_count": self.comparable_cell_count,
            "evidence_cell_count": self.evidence_cell_count,
            "evidence_only_cell_ids": list(self.evidence_only_cell_ids),
            "sources": [dict(source) for source in self.sources],
            "dimension_support": self.dimension_support,
            "notes": self.notes,
            "cells": [cell.to_payload() for cell in self.cells],
        }


@dataclass(frozen=True, slots=True)
class PlanetaryAoiRequest:
    """A validated planetary AOI, independent of any storage layer."""

    body: str
    requested_bbox: dict[str, float]
    normalized_bbox: dict[str, float]
    area_id: str
    cache_key: str
    width_m: float
    height_m: float
    area_km2: float
    crs: str
    coordinate_frame: str
    geometry: dict[str, Any]
    center_lon: float
    center_lat: float
    crossing_antimeridian: bool
    bbox_pieces: tuple[dict[str, float], ...]
    warnings: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "requested_bbox": dict(self.requested_bbox),
            "normalized_bbox": dict(self.normalized_bbox),
            "area_id": self.area_id,
            "cache_key": self.cache_key,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "area_km2": self.area_km2,
            "crs": self.crs,
            "coordinate_frame": self.coordinate_frame,
            "geometry": self.geometry,
            "center_lon": self.center_lon,
            "center_lat": self.center_lat,
            "crossing_antimeridian": self.crossing_antimeridian,
            "bbox_pieces": [dict(piece) for piece in self.bbox_pieces],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class PlanetaryAoiResult:
    """A D01 on-demand analysis over committed fixtures or one bounded live read."""

    area_id: str
    body: str
    requested_bbox: dict[str, float]
    analysis_bbox: dict[str, float]
    cells: tuple[str, ...]
    features: dict[str, float]
    method_version: str
    resolution_m: float
    source_resolution_m: float
    window_size_m: float
    valid_fraction: float
    crs: str
    coordinate_frame: str
    geometry: dict[str, Any]
    area_km2: float
    dataset: dict[str, Any]
    source_assets: tuple[dict[str, Any], ...]
    provider_status: dict[str, dict[str, Any]]
    warnings: tuple[str, ...]
    checksum: str
    dimension_support: dict[str, Any]
    coverage_note: str
    analysis_kind: str
    cell_results: tuple[dict[str, Any], ...] = ()
    analysis_area_km2: float | None = None
    schema_version: str = PLANETARY_AOI_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "area_id": self.area_id,
            "body": self.body,
            "requested_bbox": dict(self.requested_bbox),
            "analysis_bbox": dict(self.analysis_bbox),
            "cells": list(self.cells),
            "cell_results": [dict(cell) for cell in self.cell_results],
            "features": dict(self.features),
            "method_version": self.method_version,
            "resolution_m": self.resolution_m,
            "source_resolution_m": self.source_resolution_m,
            "window_size_m": self.window_size_m,
            "valid_fraction": self.valid_fraction,
            "crs": self.crs,
            "coordinate_frame": self.coordinate_frame,
            "geometry": self.geometry,
            "area_km2": self.area_km2,
            "analysis_area_km2": self.analysis_area_km2,
            "dataset": self.dataset,
            "source_assets": [dict(asset) for asset in self.source_assets],
            "provider_status": {key: dict(value) for key, value in self.provider_status.items()},
            "warnings": list(self.warnings),
            "checksum": self.checksum,
            "dimension_support": self.dimension_support,
            "coverage_note": self.coverage_note,
            "analysis_kind": self.analysis_kind,
        }


@dataclass(frozen=True, slots=True)
class _CellRecord:
    coverage: PlanetaryCellCoverage
    cell: dict[str, Any]
    manifest: dict[str, Any]
    manifest_path: Path
    manifest_sha256: str
    fixture_path: Path
    source: PlanetaryDemSource


@dataclass(frozen=True, slots=True)
class _Catalog:
    coarse_dir: Path
    repo_root: Path | None
    index_path: Path
    index: dict[str, Any]
    index_sha256: str
    manifests: tuple[dict[str, Any], ...]
    manifest_paths: tuple[Path, ...]
    manifest_sha256: tuple[str, ...]
    records: tuple[_CellRecord, ...]

    def records_for_body(self, body: str) -> tuple[_CellRecord, ...]:
        return tuple(record for record in self.records if record.coverage.body == body)


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_lon(value: float) -> float:
    """Normalize a planetocentric longitude to [-180, 180)."""
    normalized = (float(value) + 180.0) % 360.0 - 180.0
    return 180.0 if normalized == -180.0 and value > 0 else normalized


def _assert_body(body: str) -> None:
    if body not in SUPPORTED_BODIES:
        raise PlanetaryAoiError(
            "unsupported_body",
            f"planetary AOI analysis supports {', '.join(SUPPORTED_BODIES)}; got {body!r}",
        )


@contextmanager
def _planetary_gdal_env():
    os.environ.setdefault("PROJ_IGNORE_CELESTIAL_BODY", "YES")
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        PROJ_IGNORE_CELESTIAL_BODY="YES",
        GDAL_HTTP_MAX_RETRY="2",
        GDAL_HTTP_RETRY_DELAY="1",
    ):
        yield


def _display_path(path: Path, catalog: _Catalog) -> str:
    for root in (catalog.repo_root, catalog.coarse_dir):
        if root is None:
            continue
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.name


def _body_geographic_crs_string(body: str) -> str:
    # The same sphere as body_geographic_crs(), expressed without a pyproj round-trip
    # warning. It is never an Earth datum/EPSG:4326 definition.
    return f"+proj=longlat +R={_body_radius_m(body):.0f} +no_defs"


def _body_radius_m(body: str) -> float:
    try:
        return float(BODY_SPHERE_RADIUS_M[body])
    except KeyError as exc:  # pragma: no cover - guarded by _assert_body
        raise PlanetaryAoiError("unsupported_body", f"unsupported body {body!r}") from exc


def _bbox_payload(west: float, south: float, east: float, north: float) -> dict[str, float]:
    return {
        "west": float(west),
        "south": float(south),
        "east": float(east),
        "north": float(north),
    }


def _bbox_values(bbox: Mapping[str, float] | Sequence[float]) -> dict[str, float]:
    if isinstance(bbox, Mapping):
        try:
            values = {
                "west": float(bbox["west"]),
                "south": float(bbox["south"]),
                "east": float(bbox["east"]),
                "north": float(bbox["north"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanetaryAoiError(
                "invalid_bbox", "bbox must contain west, south, east and north"
            ) from exc
    elif isinstance(bbox, Sequence) and not isinstance(bbox, (str, bytes)):
        if len(bbox) != 4:
            raise PlanetaryAoiError(
                "invalid_bbox", "bbox sequence must be [west, south, east, north]"
            )
        try:
            west, south, east, north = (float(item) for item in bbox)
        except (TypeError, ValueError) as exc:
            raise PlanetaryAoiError("invalid_bbox", "bbox values must be numeric") from exc
        values = _bbox_payload(west, south, east, north)
    else:
        raise PlanetaryAoiError(
            "invalid_bbox", "bbox must be a mapping or [west, south, east, north]"
        )
    if not all(math.isfinite(value) for value in values.values()):
        raise PlanetaryAoiError("invalid_bbox", "bbox values must all be finite")
    return values


def _polygon_area_km2(
    west: float, south: float, east: float, north: float, *, radius_m: float
) -> float:
    geod = Geod(a=radius_m, b=radius_m)
    lons = [west, east, east, west]
    lats = [south, south, north, north]
    area, _ = geod.polygon_area_perimeter(lons, lats)
    return abs(float(area)) / 1e6


def _request_area_km2(
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    radius_m: float,
    crossing: bool,
) -> float:
    if not crossing:
        return _polygon_area_km2(west, south, east, north, radius_m=radius_m)
    east_piece = _polygon_area_km2(west, south, 180.0, north, radius_m=radius_m)
    west_piece = _polygon_area_km2(-180.0, south, east, north, radius_m=radius_m)
    return east_piece + west_piece


def _geometry_for_bbox(bbox: Mapping[str, float], *, crossing: bool) -> dict[str, Any]:
    west, south = float(bbox["west"]), float(bbox["south"])
    east, north = float(bbox["east"]), float(bbox["north"])
    if not crossing:
        return {
            "type": "Polygon",
            "coordinates": [
                [
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ]
            ],
        }
    return {
        "type": "MultiPolygon",
        "coordinates": [
            [
                [
                    [west, south],
                    [180.0, south],
                    [180.0, north],
                    [west, north],
                    [west, south],
                ]
            ],
            [
                [
                    [-180.0, south],
                    [east, south],
                    [east, north],
                    [-180.0, north],
                    [-180.0, south],
                ]
            ],
        ],
    }


# ---------------------------------------------------------------------------
# Catalog discovery: index -> manifests -> fixtures
# ---------------------------------------------------------------------------


def _infer_repo_root(coarse_dir: Path) -> Path | None:
    if coarse_dir.parent.name == "planetary" and coarse_dir.parent.parent.name == "pipelines":
        return coarse_dir.parents[2]
    return None


def _resolve_layout(root: str | Path | None) -> tuple[Path, Path | None, Path]:
    candidate = Path(root).expanduser() if root is not None else DEFAULT_COARSE_ROOT
    if (candidate / "index.json").is_file():
        coarse_dir = candidate
    elif (candidate / "pipelines" / "planetary" / "coarse" / "index.json").is_file():
        coarse_dir = candidate / "pipelines" / "planetary" / "coarse"
    else:
        raise PlanetaryAoiError(
            "index_missing",
            f"planetary coarse index.json was not found under {candidate}; coverage "
            "cannot be inferred from basemap extents",
        )
    repo_root = _infer_repo_root(coarse_dir)
    return coarse_dir, repo_root, coarse_dir / "index.json"


def _resolve_manifest_path(raw: str, *, coarse_dir: Path, repo_root: Path | None) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        if candidate.is_file():
            return candidate
        raise PlanetaryAoiError(
            "manifest_missing", f"planetary coarse manifest not found: {candidate}"
        )
    options: list[Path] = []
    if repo_root is not None:
        options.append(repo_root / candidate)
    options.append(coarse_dir / candidate.name)
    options.append(coarse_dir / candidate)
    for option in options:
        if option.is_file():
            return option
    raise PlanetaryAoiError(
        "manifest_missing",
        f"index entry {raw!r} does not resolve to a committed manifest under {coarse_dir}",
    )


def _resolve_fixture_path(raw: str, *, coarse_dir: Path, repo_root: Path | None) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        if candidate.is_file():
            return candidate
        raise PlanetaryAoiError("fixture_missing", f"elevation fixture not found: {candidate}")
    options: list[Path] = []
    if repo_root is not None:
        options.append(repo_root / candidate)
    options.append(coarse_dir / candidate.name)
    options.append(coarse_dir / candidate)
    options.append(coarse_dir / "samples" / candidate)
    for option in options:
        if option.is_file():
            return option
    raise PlanetaryAoiError(
        "fixture_missing",
        f"manifest fixture path {raw!r} does not resolve to a committed file",
    )


def _read_json(path: Path, *, kind: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlanetaryAoiError(f"{kind}_missing", f"cannot read {kind} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PlanetaryAoiError(
            f"{kind}_corrupt", f"{kind} {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise PlanetaryAoiError(f"{kind}_corrupt", f"{kind} {path} must be a JSON object")
    return payload


def _validate_manifest_header(
    manifest: dict[str, Any], *, path: Path, body: str, entry: Mapping[str, Any]
) -> None:
    if manifest.get("schema_version") not in _SUPPORTED_MANIFEST_SCHEMAS:
        raise PlanetaryAoiError(
            "manifest_schema_mismatch",
            f"{path} has schema_version {manifest.get('schema_version')!r}; expected "
            f"{PLANETARY_COARSE_SCHEMA_VERSION!r}",
        )
    method_version = manifest.get("method", {}).get("method_version")
    if method_version not in _SUPPORTED_MANIFEST_METHODS:
        raise PlanetaryAoiError(
            "manifest_method_mismatch",
            f"{path} has method_version {method_version!r}; expected "
            f"{PLANETARY_COARSE_METHOD_VERSION!r}",
        )
    source = manifest.get("source") or {}
    if source.get("body") != body:
        raise PlanetaryAoiError(
            "manifest_body_mismatch",
            f"{path} source body {source.get('body')!r} does not match index body {body!r}",
        )
    cells = manifest.get("cells")
    if not isinstance(cells, list) or not cells:
        raise PlanetaryAoiError("manifest_corrupt", f"{path} has no committed cells")
    expected = entry.get("cells")
    if expected is not None and int(expected) != len(cells):
        raise PlanetaryAoiError(
            "manifest_count_mismatch",
            f"{path} has {len(cells)} cells but index.json records {expected}",
        )
    counts = manifest.get("counts") or {}
    computed = counts.get("computed_cells")
    if computed is None or int(computed) != len(cells):
        raise PlanetaryAoiError(
            "manifest_count_mismatch",
            f"{path} counts.computed_cells={computed!r} does not match {len(cells)} cells",
        )


def _load_catalog(root: str | Path | None = None) -> _Catalog:
    coarse_dir, repo_root, index_path = _resolve_layout(root)
    index = _read_json(index_path, kind="index")
    if index.get("schema_version") not in _SUPPORTED_INDEX_SCHEMAS:
        raise PlanetaryAoiError(
            "index_schema_mismatch",
            f"{index_path} schema_version {index.get('schema_version')!r} is unsupported",
        )
    if index.get("method_version") != PLANETARY_COARSE_METHOD_VERSION:
        raise PlanetaryAoiError(
            "index_method_mismatch",
            f"{index_path} method_version {index.get('method_version')!r} is not "
            f"{PLANETARY_COARSE_METHOD_VERSION!r}",
        )
    pilots = index.get("pilots")
    if not isinstance(pilots, list) or not pilots:
        raise PlanetaryAoiError("index_corrupt", f"{index_path} has no pilot manifests")

    manifests: list[dict[str, Any]] = []
    manifest_paths: list[Path] = []
    manifest_sha256: list[str] = []
    records: list[_CellRecord] = []
    seen_manifests: set[Path] = set()

    for entry in pilots:
        if not isinstance(entry, Mapping):
            raise PlanetaryAoiError("index_corrupt", "index pilots must be objects")
        body = str(entry.get("body") or "")
        if body not in SUPPORTED_BODIES:
            raise PlanetaryAoiError("index_corrupt", f"index pilot has unsupported body {body!r}")
        raw_manifest = entry.get("manifest")
        if not isinstance(raw_manifest, str) or not raw_manifest:
            raise PlanetaryAoiError("index_corrupt", "index pilot is missing its manifest path")
        manifest_path = _resolve_manifest_path(
            raw_manifest, coarse_dir=coarse_dir, repo_root=repo_root
        )
        if manifest_path in seen_manifests:
            raise PlanetaryAoiError(
                "index_corrupt", f"index lists manifest {manifest_path} more than once"
            )
        seen_manifests.add(manifest_path)
        manifest = _read_json(manifest_path, kind="manifest")
        _validate_manifest_header(manifest, path=manifest_path, body=body, entry=entry)

        source_payload = manifest.get("source") or {}
        source_id = source_payload.get("source_id")
        source = PLANETARY_SOURCES.get(str(source_id))
        if source is None:
            raise PlanetaryAoiError(
                "unknown_source",
                f"{manifest_path} references source_id {source_id!r} that is not a "
                "verified planetary DEM source",
            )
        if source.body != body:
            raise PlanetaryAoiError(
                "manifest_body_mismatch",
                f"source {source.source_id} belongs to {source.body}, not index body {body}",
            )
        if entry.get("source_id") not in (None, source.source_id):
            raise PlanetaryAoiError(
                "index_corrupt",
                f"index source_id {entry.get('source_id')!r} does not match manifest "
                f"source {source.source_id!r}",
            )

        region = manifest.get("region") or {}
        region_id = region.get("region_id")
        if not isinstance(region_id, str) or not region_id:
            raise PlanetaryAoiError(
                "manifest_corrupt", f"{manifest_path} region.region_id is missing"
            )
        region_evidence = "evidence" in region_id.lower()
        manifest_sha = _sha256_file(manifest_path)
        manifest_paths.append(manifest_path)
        manifest_sha256.append(manifest_sha)
        manifests.append(manifest)

        for cell in manifest["cells"]:
            if not isinstance(cell, Mapping):
                raise PlanetaryAoiError(
                    "manifest_corrupt", f"{manifest_path} has a non-object cell"
                )
            fixture = cell.get("fixture")
            if not isinstance(fixture, Mapping) or not isinstance(fixture.get("path"), str):
                raise PlanetaryAoiError(
                    "manifest_corrupt",
                    f"{manifest_path} cell {cell.get('candidate_id')!r} has no fixture path",
                )
            fixture_path = _resolve_fixture_path(
                str(fixture["path"]), coarse_dir=coarse_dir, repo_root=repo_root
            )
            observed_sha = _sha256_file(fixture_path)
            if observed_sha != fixture.get("sha256"):
                raise PlanetaryAoiError(
                    "fixture_checksum_mismatch",
                    f"fixture checksum for {fixture_path} is {observed_sha}, committed "
                    f"manifest records {fixture.get('sha256')}",
                )
            fixture_bytes = fixture_path.stat().st_size
            if fixture.get("bytes") is not None and int(fixture["bytes"]) != fixture_bytes:
                raise PlanetaryAoiError(
                    "fixture_size_mismatch",
                    f"fixture {fixture_path} is {fixture_bytes} bytes, committed "
                    f"{fixture['bytes']}",
                )
            if cell.get("body") != body:
                raise PlanetaryAoiError(
                    "manifest_corrupt",
                    f"{manifest_path} cell {cell.get('candidate_id')!r} body "
                    f"{cell.get('body')!r} does not match {body!r}",
                )
            features = cell.get("features")
            if not isinstance(features, Mapping) or not set(COARSE_FEATURE_IDS_TUPLE).issubset(
                features
            ):
                raise PlanetaryAoiError(
                    "manifest_corrupt",
                    f"{manifest_path} cell {cell.get('candidate_id')!r} is missing coarse "
                    "D01 feature values",
                )
            analysis_resolution = float(cell["analysis_resolution_m"])
            analysis_eligible = bool(
                source.same_support_as_earth
                and not region_evidence
                and math.isclose(
                    analysis_resolution, ANALYSIS_RESOLUTION_M, rel_tol=1e-9, abs_tol=1e-9
                )
            )
            support_status = (
                SUPPORT_ANALYSIS_ELIGIBLE if analysis_eligible else SUPPORT_EVIDENCE_ONLY
            )
            coverage = PlanetaryCellCoverage(
                candidate_id=str(cell["candidate_id"]),
                body=body,
                region_id=region_id,
                source_id=source.source_id,
                source=dict(source_payload),
                bbox_body_lonlat={
                    str(key): float(value)
                    for key, value in (cell.get("bbox_body_lonlat") or {}).items()
                },
                bbox_metric={
                    str(key): float(value) for key, value in (cell.get("bbox_metric") or {}).items()
                },
                fixture_path=str(fixture_path),
                fixture_recorded_path=str(fixture["path"]),
                fixture_sha256=str(fixture["sha256"]),
                fixture_bytes=fixture_bytes,
                analysis_resolution_m=analysis_resolution,
                source_resolution_m=float(cell["source_resolution_m"]),
                window_size_m=float(cell["window_size_m"]),
                features={str(key): float(value) for key, value in features.items()},
                crs_wkt=str(cell["crs_wkt"]),
                crs_name=str(cell["crs_name"]),
                valid_fraction=float(cell["valid_fraction"]),
                manifest_path=raw_manifest,
                support_status=support_status,
                analysis_eligible=analysis_eligible,
                recorded=dict(cell),
            )
            records.append(
                _CellRecord(
                    coverage=coverage,
                    cell=dict(cell),
                    manifest=manifest,
                    manifest_path=manifest_path,
                    manifest_sha256=manifest_sha,
                    fixture_path=fixture_path,
                    source=source,
                )
            )

    if not records:
        raise PlanetaryAoiError("index_corrupt", "no committed planetary coarse cells were found")
    return _Catalog(
        coarse_dir=coarse_dir,
        repo_root=repo_root,
        index_path=index_path,
        index=index,
        index_sha256=_sha256_file(index_path),
        manifests=tuple(manifests),
        manifest_paths=tuple(manifest_paths),
        manifest_sha256=tuple(manifest_sha256),
        records=tuple(records),
    )


# ---------------------------------------------------------------------------
# BBox validation, geometry and cache identity
# ---------------------------------------------------------------------------


def _validate_request_bbox(
    body: str,
    bbox: Mapping[str, float] | Sequence[float],
    *,
    min_side_m: float,
    max_side_m: float,
) -> dict[str, Any]:
    _assert_body(body)
    values = _bbox_values(bbox)
    raw_west, raw_south = values["west"], values["south"]
    raw_east, raw_north = values["east"], values["north"]
    if not (-90.0 <= raw_south < raw_north <= 90.0):
        raise PlanetaryAoiError(
            "invalid_bbox",
            "bbox must satisfy -90 <= south < north <= 90 in planetocentric latitude",
        )
    if not (-360.0 <= raw_west <= 360.0 and -360.0 <= raw_east <= 360.0):
        raise PlanetaryAoiError(
            "invalid_bbox", "planetocentric longitudes must lie within [-360, 360]"
        )
    if raw_west == raw_east:
        raise PlanetaryAoiError("invalid_bbox", "bbox must have non-zero longitude extent")
    if raw_west < raw_east and (raw_east - raw_west) > 180.0:
        raise PlanetaryAoiError(
            "bbox_too_wide",
            "bbox spans more than 180 degrees; split the request instead of treating a "
            "basemap extent as coverage",
        )

    west = _normalize_lon(raw_west)
    east = _normalize_lon(raw_east)
    crossing = west > east
    span = (180.0 - west) + (east + 180.0) if crossing else east - west
    if not (0.0 < span <= 180.0):
        raise PlanetaryAoiError(
            "bbox_too_wide",
            f"bbox longitude span is {span:.6f} degrees; a request may span at most 180",
        )

    radius = _body_radius_m(body)
    geod = Geod(a=radius, b=radius)
    lat_center = (raw_south + raw_north) / 2.0
    east_unwrapped = east + 360.0 if crossing else east
    width_m = abs(float(geod.inv(west, lat_center, east_unwrapped, lat_center)[2]))
    center_lon = _normalize_lon(west + span / 2.0)
    height_m = abs(float(geod.inv(center_lon, raw_south, center_lon, raw_north)[2]))
    if min(width_m, height_m) < min_side_m:
        raise PlanetaryAoiError(
            "aoi_too_small",
            f"AOI sides must be at least {min_side_m:.0f} m on the {body} sphere; "
            f"got {min(width_m, height_m):.0f} m",
        )
    if max(width_m, height_m) > max_side_m:
        raise PlanetaryAoiError(
            "aoi_too_large",
            f"AOI sides must be at most {max_side_m:.0f} m on the {body} sphere; "
            f"got {max(width_m, height_m):.0f} m",
        )
    area_km2 = _request_area_km2(
        west, raw_south, east, raw_north, radius_m=radius, crossing=crossing
    )
    if area_km2 <= 0 or not math.isfinite(area_km2):
        raise PlanetaryAoiError("invalid_bbox", "requested AOI area is not positive")

    if crossing:
        pieces = (
            _bbox_payload(west, raw_south, 180.0, raw_north),
            _bbox_payload(-180.0, raw_south, east, raw_north),
        )
    else:
        pieces = (_bbox_payload(west, raw_south, east, raw_north),)

    normalized_bbox = _bbox_payload(west, raw_south, east, raw_north)
    geometry = _geometry_for_bbox(normalized_bbox, crossing=crossing)
    return {
        "requested_bbox": {
            "west": round(raw_west, 6),
            "south": round(raw_south, 6),
            "east": round(raw_east, 6),
            "north": round(raw_north, 6),
        },
        "normalized_bbox": {
            "west": round(west, 6),
            "south": round(raw_south, 6),
            "east": round(east, 6),
            "north": round(raw_north, 6),
        },
        "width_m": width_m,
        "height_m": height_m,
        "area_km2": area_km2,
        "geometry": geometry,
        "center_lon": center_lon,
        "center_lat": lat_center,
        "crossing_antimeridian": crossing,
        "bbox_pieces": pieces,
        "warnings": (
            (
                "The bbox crosses the antimeridian; it was normalized and split into two "
                "planetocentric longitude pieces. It is never interpreted as an Earth datum.",
            )
            if crossing
            else ()
        ),
    }


def _build_request(
    body: str,
    bbox: Mapping[str, float] | Sequence[float],
    catalog: _Catalog,
    *,
    min_side_m: float = AOI_MIN_SIDE_M,
    max_side_m: float = AOI_MAX_SIDE_M,
) -> PlanetaryAoiRequest:
    validated = _validate_request_bbox(body, bbox, min_side_m=min_side_m, max_side_m=max_side_m)
    descriptor = {
        "body": body,
        "bbox": validated["normalized_bbox"],
        "method_version": PLANETARY_AOI_METHOD_VERSION,
        "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
        "antimeridian": validated["crossing_antimeridian"],
    }
    digest = hashlib.sha256(_canonical(descriptor).encode()).hexdigest()[:12]
    area_id = f"planet-aoi-{body.lower()}-{digest}"

    body_records = catalog.records_for_body(body)
    if not body_records:
        raise PlanetaryAoiError(
            "out_of_coverage",
            f"no committed planetary coarse cells exist for {body}; coverage is not "
            "inferred from basemap extents",
        )
    cache_descriptor = {
        "area_id": area_id,
        "body": body,
        "method_version": PLANETARY_AOI_METHOD_VERSION,
        "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
        "index_sha256": catalog.index_sha256,
        "source_manifest_sha256": sorted({record.manifest_sha256 for record in body_records}),
        "fixture_sha256": sorted({record.coverage.fixture_sha256 for record in body_records}),
    }
    cache_key = hashlib.sha256(_canonical(cache_descriptor).encode()).hexdigest()
    return PlanetaryAoiRequest(
        body=body,
        requested_bbox=validated["requested_bbox"],
        normalized_bbox=validated["normalized_bbox"],
        area_id=area_id,
        cache_key=cache_key,
        width_m=validated["width_m"],
        height_m=validated["height_m"],
        area_km2=validated["area_km2"],
        crs=_body_geographic_crs_string(body),
        coordinate_frame=COORDINATE_FRAMES[body],
        geometry=validated["geometry"],
        center_lon=validated["center_lon"],
        center_lat=validated["center_lat"],
        crossing_antimeridian=validated["crossing_antimeridian"],
        bbox_pieces=validated["bbox_pieces"],
        warnings=validated["warnings"],
    )


def validate_planetary_aoi(
    body: str,
    bbox: Mapping[str, float] | Sequence[float],
    *,
    root: str | Path | None = None,
    max_side_m: float = AOI_MAX_SIDE_M,
    min_side_m: float = AOI_MIN_SIDE_M,
) -> PlanetaryAoiRequest:
    """Validate a planetocentric bbox and derive deterministic identity values.

    ``max_side_m``/``min_side_m`` are exposed so callers can tighten, but never widen,
    the committed engine limits. The bbox is always interpreted on the body sphere,
    never as Earth WGS84/EPSG:4326.
    """
    _assert_body(body)
    if not (math.isfinite(float(max_side_m)) and math.isfinite(float(min_side_m))):
        raise PlanetaryAoiError("invalid_bounds", "AOI side bounds must be finite")
    if min_side_m < 0 or max_side_m <= min_side_m:
        raise PlanetaryAoiError("invalid_bounds", "required: 0 <= min_side_m < max_side_m")
    effective_max = min(AOI_MAX_SIDE_M, float(max_side_m))
    effective_min = max(AOI_MIN_SIDE_M, float(min_side_m))
    if effective_max <= effective_min:
        raise PlanetaryAoiError(
            "invalid_bounds",
            f"requested AOI side bounds [{effective_min}, {effective_max}] m are empty",
        )
    catalog = _load_catalog(root)
    return _build_request(
        body,
        bbox,
        catalog,
        min_side_m=effective_min,
        max_side_m=effective_max,
    )


# ---------------------------------------------------------------------------
# Committed cell selection and fixture recomputation
# ---------------------------------------------------------------------------


def _point_in_request(lon: float, lat: float, request: PlanetaryAoiRequest) -> bool:
    bbox = request.normalized_bbox
    if not (bbox["south"] <= lat <= bbox["north"]):
        return False
    normalized = _normalize_lon(lon)
    if request.crossing_antimeridian:
        return normalized >= bbox["west"] or normalized <= bbox["east"]
    return bbox["west"] <= normalized <= bbox["east"]


def _cell_bbox_pieces(cell_bbox: Mapping[str, float]) -> tuple[dict[str, float], ...]:
    west = _normalize_lon(float(cell_bbox["west"]))
    east = _normalize_lon(float(cell_bbox["east"]))
    south = float(cell_bbox["south"])
    north = float(cell_bbox["north"])
    if west > east:
        return (
            _bbox_payload(west, south, 180.0, north),
            _bbox_payload(-180.0, south, east, north),
        )
    return (_bbox_payload(west, south, east, north),)


def _bbox_contains(outer: Mapping[str, float], inner: Mapping[str, float]) -> bool:
    return (
        outer["west"] <= inner["west"]
        and outer["south"] <= inner["south"]
        and outer["east"] >= inner["east"]
        and outer["north"] >= inner["north"]
    )


def _select_records(
    records: tuple[_CellRecord, ...], request: PlanetaryAoiRequest
) -> tuple[_CellRecord, ...]:
    selected: list[_CellRecord] = []
    for record in records:
        if not record.coverage.analysis_eligible:
            continue
        bbox = record.coverage.bbox_body_lonlat
        center_lon = (float(bbox["west"]) + float(bbox["east"])) / 2.0
        center_lat = (float(bbox["south"]) + float(bbox["north"])) / 2.0
        covers_center = _point_in_request(center_lon, center_lat, request)
        contains_request = False
        if not covers_center:
            for cell_piece in _cell_bbox_pieces(bbox):
                if any(
                    _bbox_contains(cell_piece, request_piece)
                    for request_piece in request.bbox_pieces
                ):
                    contains_request = True
                    break
        if covers_center or contains_request:
            selected.append(record)
    return tuple(selected)


def _union_lonlat_bbox(records: Sequence[_CellRecord]) -> dict[str, float]:
    if not records:
        raise PlanetaryAoiError("out_of_coverage", "no cell footprints to union")
    return _bbox_payload(
        min(float(record.coverage.bbox_body_lonlat["west"]) for record in records),
        min(float(record.coverage.bbox_body_lonlat["south"]) for record in records),
        max(float(record.coverage.bbox_body_lonlat["east"]) for record in records),
        max(float(record.coverage.bbox_body_lonlat["north"]) for record in records),
    )


def _bbox_differ(
    a: Mapping[str, float], b: Mapping[str, float], *, tolerance: float = 1e-6
) -> bool:
    return any(
        not math.isclose(float(a[key]), float(b[key]), abs_tol=tolerance, rel_tol=0.0)
        for key in ("west", "south", "east", "north")
    )


def _recompute_cell(
    record: _CellRecord,
) -> tuple[dict[str, float], np.ndarray, np.ndarray, int, int]:
    coverage = record.coverage
    fixture = record.cell.get("fixture") or {}
    if not record.fixture_path.is_file():
        raise PlanetaryAoiError(
            "fixture_missing", f"elevation fixture is missing: {record.fixture_path}"
        )
    observed_sha = _sha256_file(record.fixture_path)
    if observed_sha != coverage.fixture_sha256:
        raise PlanetaryAoiError(
            "fixture_checksum_mismatch",
            f"fixture changed after discovery: {record.fixture_path}",
        )
    pad = int(fixture.get("pad_pixels", 0))
    if pad < 1:
        raise PlanetaryAoiError(
            "fixture_metadata_mismatch",
            f"{coverage.candidate_id} has no padded Horn stencil metadata",
        )
    expected_shape = tuple(int(value) for value in fixture.get("shape", ()))
    expected_transform = tuple(float(value) for value in fixture.get("transform", ()))
    try:
        with _planetary_gdal_env(), rasterio.open(record.fixture_path) as src:
            crs = CRS.from_user_input(src.crs)
            assert_planetary_crs(crs, body=coverage.body)
            data = np.asarray(src.read(1, masked=True).filled(np.nan), dtype="float64")
            observed_transform = tuple(float(value) for value in src.transform)[:6]
    except PlanetaryCoarseError as exc:
        raise PlanetaryAoiError(exc.code, exc.message) from exc
    except Exception as exc:  # noqa: BLE001 - corrupt/unreadable fixture must be explicit
        raise PlanetaryAoiError(
            "fixture_metadata_mismatch",
            f"{coverage.candidate_id} fixture could not be opened/read: {exc}",
        ) from exc
    if expected_shape and data.shape != expected_shape:
        raise PlanetaryAoiError(
            "fixture_metadata_mismatch",
            f"{coverage.candidate_id} fixture shape {data.shape} != committed {expected_shape}",
        )
    if expected_transform and len(expected_transform) == 6:
        for observed, expected in zip(observed_transform, expected_transform, strict=True):
            if not math.isclose(observed, expected, rel_tol=1e-9, abs_tol=1e-6):
                raise PlanetaryAoiError(
                    "fixture_metadata_mismatch",
                    f"{coverage.candidate_id} fixture transform {observed_transform} does "
                    f"not match committed {expected_transform}",
                )
    try:
        slope = horn_slope_degrees(data, float(coverage.analysis_resolution_m))
    except Exception as exc:  # noqa: BLE001 - shared extractor raises its own domain error
        raise PlanetaryAoiError(
            "fixture_recompute_mismatch",
            f"{coverage.candidate_id} Horn slope could not be recomputed: {exc}",
        ) from exc
    core = slope[slice(pad, -pad), slice(pad, -pad)]
    if core.size == 0:
        raise PlanetaryAoiError(
            "fixture_metadata_mismatch",
            f"{coverage.candidate_id} padding leaves no core slope pixels",
        )
    expected_grid = tuple(int(value) for value in record.cell.get("grid_shape", ()))
    if expected_grid and core.shape != expected_grid:
        raise PlanetaryAoiError(
            "fixture_metadata_mismatch",
            f"{coverage.candidate_id} core shape {core.shape} != committed {expected_grid}",
        )
    valid = np.isfinite(core)
    valid_pixels = int(valid.sum())
    missing_pixels = int(core.size - valid_pixels)
    try:
        features = extract_coarse_features(core, valid)
    except CoarseExtractionError as exc:
        raise PlanetaryAoiError(
            "fixture_recompute_mismatch",
            f"{coverage.candidate_id} coarse aggregation failed: {exc.message}",
        ) from exc
    for feature_id, committed_value in coverage.features.items():
        if feature_id not in features:
            raise PlanetaryAoiError(
                "fixture_recompute_mismatch",
                f"{coverage.candidate_id} recomputed features omit {feature_id}",
            )
        if not math.isclose(
            float(committed_value), float(features[feature_id]), rel_tol=1e-9, abs_tol=1e-12
        ):
            raise PlanetaryAoiError(
                "fixture_recompute_mismatch",
                f"{coverage.candidate_id} {feature_id}: committed {committed_value} != "
                f"recomputed {features[feature_id]}",
            )
    committed_valid = record.cell.get("valid_slope_pixels")
    if committed_valid is not None and int(committed_valid) != valid_pixels:
        raise PlanetaryAoiError(
            "fixture_recompute_mismatch",
            f"{coverage.candidate_id} valid slope pixels: committed {committed_valid} != "
            f"recomputed {valid_pixels}",
        )
    return features, core, valid, valid_pixels, missing_pixels


def _aggregate_slope_features(
    core_slopes: Sequence[np.ndarray], valid_masks: Sequence[np.ndarray]
) -> tuple[dict[str, float], float, int, int]:
    slopes = np.concatenate([core.ravel() for core in core_slopes]).reshape(1, -1)
    valid = np.concatenate([mask.ravel() for mask in valid_masks]).reshape(1, -1)
    total = int(slopes.size)
    valid_count = int(valid.sum())
    missing_count = total - valid_count
    if valid_count == 0:
        raise PlanetaryAoiError(
            "fixture_recompute_mismatch", "the selected cells have no valid slope pixels"
        )
    try:
        features = extract_coarse_features(slopes, valid)
    except CoarseExtractionError as exc:
        raise PlanetaryAoiError(
            "fixture_recompute_mismatch",
            f"selected-cell aggregation failed: {exc.message}",
        ) from exc
    return features, float(valid_count / total), valid_count, missing_count


def _cell_result_payload(
    record: _CellRecord,
    features: dict[str, float],
    *,
    valid_pixels: int,
    missing_pixels: int,
) -> dict[str, Any]:
    coverage = record.coverage
    fixture = record.cell.get("fixture") or {}
    return {
        "candidate_id": coverage.candidate_id,
        "cell_id": coverage.cell_id,
        "body": coverage.body,
        "region_id": coverage.region_id,
        "source_id": coverage.source_id,
        "bbox_body_lonlat": dict(coverage.bbox_body_lonlat),
        "bbox_metric": dict(coverage.bbox_metric),
        "crs_name": coverage.crs_name,
        "crs_wkt": coverage.crs_wkt,
        "features": dict(features),
        "valid_slope_pixels": valid_pixels,
        "missing_slope_pixels": missing_pixels,
        "valid_fraction": float(valid_pixels / (valid_pixels + missing_pixels)),
        "fixture_path": coverage.fixture_recorded_path,
        "fixture_recorded_path": coverage.fixture_recorded_path,
        "fixture_sha256": coverage.fixture_sha256,
        "fixture_bytes": coverage.fixture_bytes,
        "fixture_transform": list(fixture.get("transform", [])),
        "fixture_shape": list(fixture.get("shape", [])),
        "pad_pixels": int(fixture.get("pad_pixels", 0)),
        "analysis_resolution_m": coverage.analysis_resolution_m,
        "source_resolution_m": coverage.source_resolution_m,
        "window_size_m": coverage.window_size_m,
        "support_status": coverage.support_status,
        "analysis_eligible": coverage.analysis_eligible,
        "manifest_path": coverage.manifest_path,
    }


def _source_asset_payload(record: _CellRecord, *, analysis_kind: str) -> dict[str, Any]:
    coverage = record.coverage
    fixture = record.cell.get("fixture") or {}
    return {
        "uri": coverage.fixture_recorded_path,
        "path": coverage.fixture_recorded_path,
        "recorded_path": coverage.fixture_recorded_path,
        "sha256": coverage.fixture_sha256,
        "checksum": f"sha256:{coverage.fixture_sha256}",
        "bytes": coverage.fixture_bytes,
        "cell_id": coverage.candidate_id,
        "region_id": coverage.region_id,
        "source_id": coverage.source_id,
        "crs_wkt": coverage.crs_wkt,
        "crs_name": coverage.crs_name,
        "analysis_resolution_m": coverage.analysis_resolution_m,
        "source_resolution_m": coverage.source_resolution_m,
        "window_size_m": coverage.window_size_m,
        "transform": list(fixture.get("transform", [])),
        "pad_pixels": int(fixture.get("pad_pixels", 0)),
        "analysis_kind": analysis_kind,
    }


def _source_payloads(sources: Sequence[PlanetaryDemSource]) -> list[dict[str, Any]]:
    return [source.to_payload() for source in sources]


def _unique_sources(records: Sequence[_CellRecord]) -> tuple[PlanetaryDemSource, ...]:
    seen: dict[str, PlanetaryDemSource] = {}
    for record in records:
        seen[record.source.source_id] = record.source
    return tuple(seen.values())


def _dataset_payload(
    body: str,
    records: Sequence[_CellRecord],
    *,
    catalog: _Catalog,
    analysis_kind: str,
) -> dict[str, Any]:
    sources = _unique_sources(records)
    primary = sources[0]
    manifest_payloads = sorted(
        {
            (
                _display_path(record.manifest_path, catalog),
                record.manifest_sha256,
            )
            for record in records
        }
    )
    return {
        "dataset_id": primary.dataset_id,
        "source_id": primary.source_id,
        "body": body,
        "provider": primary.provider,
        "product_id": primary.product_id,
        "product_lid": primary.product_lid,
        "title": primary.title,
        "url": primary.raster_url or primary.url,
        "label_url": primary.url,
        "raster_url": primary.raster_url,
        "product_page": primary.product_page,
        "license": primary.license,
        "license_url": primary.license_url,
        "citation": primary.citation,
        "doi": primary.doi,
        "frame": primary.frame,
        "coordinate_system": primary.coordinate_system,
        "vertical_reference": primary.vertical_reference,
        "native_ppd": primary.native_ppd,
        "native_resolution_m": primary.native_resolution_m,
        "same_support_as_earth": primary.same_support_as_earth,
        "source_crs": primary.coordinate_system,
        "data_kind": "observed_derived",
        "method_version": PLANETARY_AOI_METHOD_VERSION,
        "source_method_version": PLANETARY_COARSE_METHOD_VERSION,
        "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
        "analysis_kind": analysis_kind,
        "quality_flags": list(primary.quality_flags),
        "index_path": _display_path(catalog.index_path, catalog),
        "index_sha256": catalog.index_sha256,
        "source_manifests": [
            {"path": path, "sha256": sha256} for path, sha256 in manifest_payloads
        ],
        "sources": _source_payloads(sources),
    }


def _provider_status(
    records: Sequence[_CellRecord], *, analysis_kind: str, network_used: bool
) -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    for record in records:
        source = record.source
        entry = status.setdefault(
            source.source_id,
            {
                "source_id": source.source_id,
                "provider": source.provider,
                "status": (
                    "verified_committed_fixture"
                    if analysis_kind == ANALYSIS_KIND_COMMITTED
                    else "live_bounded_read_ok"
                ),
                "network_used": network_used,
                "cells": 0,
                "fixture_checksums_verified": analysis_kind == ANALYSIS_KIND_COMMITTED,
                "url": source.raster_url or source.url,
                "license": source.license,
                "license_url": source.license_url,
                "native_resolution_m": source.native_resolution_m,
                "same_support_as_earth": source.same_support_as_earth,
            },
        )
        entry["cells"] = int(entry["cells"]) + 1
    return status


def _unsupported_dimensions(body: str) -> dict[str, Any]:
    stored = _STORED_MOON_EVIDENCE if body == "Moon" else _STORED_MARS_EVIDENCE
    support: dict[str, Any] = {}
    for code in DIMENSION_CODES:
        if code == "D01":
            continue
        support[code] = {
            "dimension_code": code,
            "status": "unsupported_for_on_demand_analysis",
            "reason": _UNSUPPORTED_REASONS[code],
            "stored_evidence_only": code in stored,
            "stored_evidence_note": stored.get(code),
            "value": None,
        }
    return support


def _result_dimension_support(
    body: str,
    *,
    cell_count: int,
    feature_count: int,
    analysis_kind: str,
) -> dict[str, Any]:
    support: dict[str, Any] = {
        "D01": {
            "dimension_code": "D01",
            "status": "present",
            "quality": (
                "verified_committed_fixture_recompute"
                if analysis_kind == ANALYSIS_KIND_COMMITTED
                else "new_bounded_live_read_not_committed"
            ),
            "method_version": PLANETARY_AOI_METHOD_VERSION,
            "source_method_version": PLANETARY_COARSE_METHOD_VERSION,
            "support": (
                "5 km planetary coarse cells aggregated on the fixed 250 m analysis grid "
                "with the shared Horn 3x3 slope and coarse feature definitions"
            ),
            "cell_count": cell_count,
            "feature_count": feature_count,
            "observation_count": feature_count,
            "feature_ids": list(COARSE_FEATURE_IDS_TUPLE),
            "analysis_kind": analysis_kind,
        }
    }
    support.update(_unsupported_dimensions(body))
    return support


def _coverage_dimension_support(
    body: str,
    *,
    comparable_cell_count: int,
    evidence_cell_count: int,
) -> dict[str, Any]:
    support: dict[str, Any] = {
        "D01": {
            "dimension_code": "D01",
            "status": "present" if comparable_cell_count else "evidence_only",
            "quality": "committed_reference_pilot",
            "method_version": PLANETARY_COARSE_METHOD_VERSION,
            "support": (
                "committed 5 km planetary coarse pilot cells; only same-support 250 m "
                "cells are eligible for on-demand D01 analysis"
            ),
            "comparable_cell_count": comparable_cell_count,
            "evidence_cell_count": evidence_cell_count,
            "observation_count": comparable_cell_count * len(COARSE_FEATURE_IDS_TUPLE),
            "feature_ids": list(COARSE_FEATURE_IDS_TUPLE),
            "claim_scope": "bounded_committed_pilot_cells_not_a_global_survey",
        }
    }
    support.update(_unsupported_dimensions(body))
    return support


# ---------------------------------------------------------------------------
# Coverage reporting
# ---------------------------------------------------------------------------


def _coverage_for_body(body: str, catalog: _Catalog) -> PlanetaryBodyCoverage:
    _assert_body(body)
    records = catalog.records_for_body(body)
    if not records:
        raise PlanetaryAoiError(
            "out_of_coverage",
            f"no committed planetary coarse cells exist for {body}; coverage is never "
            "inferred from basemap extents",
        )
    comparable = tuple(record for record in records if record.coverage.analysis_eligible)
    evidence = tuple(record for record in records if not record.coverage.analysis_eligible)

    regions: list[dict[str, Any]] = []
    by_region: dict[str, list[_CellRecord]] = {}
    for record in records:
        by_region.setdefault(record.coverage.region_id, []).append(record)
    for region_id, region_records in by_region.items():
        region_eligible = [record for record in region_records if record.coverage.analysis_eligible]
        manifest = region_records[0].manifest
        region_payload = manifest.get("region") or {}
        regions.append(
            {
                "region_id": region_id,
                "label": region_payload.get("label"),
                "body": body,
                "source_id": region_records[0].source.source_id,
                "dataset_id": region_records[0].source.dataset_id,
                "provider": region_records[0].source.provider,
                "support_status": (
                    SUPPORT_ANALYSIS_ELIGIBLE if region_eligible else SUPPORT_EVIDENCE_ONLY
                ),
                "cell_count": len(region_records),
                "analysis_eligible_cell_count": len(region_eligible),
                "evidence_only_cell_count": len(region_records) - len(region_eligible),
                "cell_ids": [record.coverage.candidate_id for record in region_records],
                "bbox_body_lonlat": _union_lonlat_bbox(region_records),
                "bbox_metric": dict(region_payload.get("bbox_metric") or {}),
                "analysis_resolution_m": region_payload.get("analysis_resolution_m"),
                "source_resolution_m": region_records[0].source.native_resolution_m,
                "window_size_m": region_payload.get("window_size_m"),
                "crs_name": region_records[0].coverage.crs_name,
                "sample_label": region_payload.get(
                    "sample_label", "bounded_pilot_not_global_exhaustive"
                ),
                "notes": region_payload.get("notes"),
            }
        )

    sources: list[dict[str, Any]] = []
    for source in _unique_sources(records):
        source_records = [
            record for record in records if record.source.source_id == source.source_id
        ]
        source_eligible = [record for record in source_records if record.coverage.analysis_eligible]
        payload = source.to_payload()
        payload.update(
            {
                "cell_count": len(source_records),
                "analysis_eligible_cell_count": len(source_eligible),
                "evidence_only_cell_count": len(source_records) - len(source_eligible),
                "support_status": (
                    SUPPORT_ANALYSIS_ELIGIBLE if source_eligible else SUPPORT_EVIDENCE_ONLY
                ),
            }
        )
        sources.append(payload)

    comparable_bbox = _union_lonlat_bbox(comparable) if comparable else None
    evidence_bbox = _union_lonlat_bbox(evidence) if evidence else None
    notes = (
        f"Exact committed-cell coverage for {body}: {len(records)} cell(s) in "
        f"{len(regions)} region(s); {len(comparable)} same-support 250 m analysis-eligible "
        f"cell(s) and {len(evidence)} evidence-only cell(s). This is a bounded pilot, not "
        "global coverage, and basemap extents are not treated as observations."
    )
    if evidence:
        notes += (
            " Evidence-only cells are reported separately and are never merged into the "
            "comparable D01 coverage or an on-demand analysis."
        )
    return PlanetaryBodyCoverage(
        body=body,
        regions=regions,
        bbox=comparable_bbox,
        cell_count=len(records),
        comparable_cell_count=len(comparable),
        evidence_cell_count=len(evidence),
        sources=sources,
        dimension_support=_coverage_dimension_support(
            body,
            comparable_cell_count=len(comparable),
            evidence_cell_count=len(evidence),
        ),
        notes=notes,
        evidence_bbox=evidence_bbox,
        evidence_only_cell_ids=tuple(record.coverage.candidate_id for record in evidence),
        cells=tuple(record.coverage for record in records),
    )


def planetary_body_coverage(body: str, *, root: str | Path | None = None) -> PlanetaryBodyCoverage:
    """Return exact committed-cell coverage for ``Moon`` or ``Mars``.

    Brand-new regions are not invented from a basemap. For Mars the two MOLA MEGDR
    evidence cells stay in their own region and are counted separately from the four
    same-support CTX cells.
    """
    catalog = _load_catalog(root)
    return _coverage_for_body(body, catalog)


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------


def _result_checksum(result: PlanetaryAoiResult) -> str:
    payload = result.to_payload()
    payload.pop("checksum", None)
    return "sha256:" + hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _finalize(result: PlanetaryAoiResult) -> PlanetaryAoiResult:
    return replace(result, checksum=_result_checksum(result))


def _base_warnings(
    request: PlanetaryAoiRequest,
    *,
    analysis_bbox: Mapping[str, float],
    analysis_kind: str,
    cell_count: int,
) -> list[str]:
    warnings = list(request.warnings)
    if _bbox_differ(request.normalized_bbox, analysis_bbox):
        warnings.append(
            "requested_bbox and analysis_bbox differ: analysis_bbox is the union of the "
            "selected 5 km committed cell footprints, so D01 results cover that union and "
            "not necessarily the exact requested polygon."
        )
    warnings.append(
        "Values are recomputed from verified planetary elevation fixtures/source windows; "
        "basemap extents are never used as coverage or as a scientific measurement."
    )
    warnings.append(
        f"Only {cell_count} committed bounded cell(s) were selected. This is not a global "
        "or exhaustive planetary survey."
    )
    warnings.append(
        f"D01 method {PLANETARY_AOI_METHOD_VERSION} uses "
        f"{PLANETARY_COARSE_METHOD_VERSION} coarse cells (Horn 3x3 on a 250 m grid). It is "
        "not interchangeable with the Earth coarse-terrain-250m-1.0.0 preprocessed slope "
        "product, and no cross-body score is produced."
    )
    warnings.append(
        "Planetary longitudes/latitudes are body-fixed planetocentric coordinates, never "
        "Earth WGS84 or an Earth geographic CRS."
    )
    warnings.append(
        "D02-D06 are explicitly unsupported for on-demand analysis in this engine; stored "
        "typed evidence for pre-indexed areas is not a measurement of this AOI."
    )
    warnings.append(
        "Source licences/redistribution terms remain those of the original providers; check "
        "the recorded licence fields before promoting derived values."
    )
    if analysis_kind == ANALYSIS_KIND_LIVE:
        warnings.append(
            "This result is a new bounded live /vsicurl read, not a committed reference "
            "fixture; reproduce it only with network access to the recorded source URL."
        )
    return warnings


def _analysis_coverage_note(
    request: PlanetaryAoiRequest,
    records: Sequence[_CellRecord],
    *,
    analysis_kind: str,
) -> str:
    region_ids = sorted({record.coverage.region_id for record in records})
    if analysis_kind == ANALYSIS_KIND_COMMITTED:
        return (
            f"Committed reference fixture analysis over {len(records)} {request.body} cell(s) "
            f"from region(s) {', '.join(region_ids)}. analysis_bbox is the union of the "
            "selected 5 km cell footprints; requested_bbox is the user AOI. Only these "
            "committed cells are coverage; this is not a global survey."
        )
    return (
        f"Live bounded read over one 5 km {request.body} window centered on the AOI from the "
        "verified NASA PGDA LOLA128 source. The result is not a committed reference fixture "
        "and is not merged with any other region."
    )


def _analyze_committed(
    request: PlanetaryAoiRequest,
    catalog: _Catalog,
    selected: tuple[_CellRecord, ...],
) -> PlanetaryAoiResult:
    core_slopes: list[np.ndarray] = []
    valid_masks: list[np.ndarray] = []
    cell_results: list[dict[str, Any]] = []
    source_assets: list[dict[str, Any]] = []
    for record in selected:
        features, core, valid, valid_pixels, missing_pixels = _recompute_cell(record)
        core_slopes.append(core)
        valid_masks.append(valid)
        cell_results.append(
            _cell_result_payload(
                record,
                features,
                valid_pixels=valid_pixels,
                missing_pixels=missing_pixels,
            )
        )
        source_assets.append(_source_asset_payload(record, analysis_kind=ANALYSIS_KIND_COMMITTED))
    features, valid_fraction, _, _ = _aggregate_slope_features(core_slopes, valid_masks)
    analysis_bbox = _union_lonlat_bbox(selected)
    analysis_area_km2 = _request_area_km2(
        float(analysis_bbox["west"]),
        float(analysis_bbox["south"]),
        float(analysis_bbox["east"]),
        float(analysis_bbox["north"]),
        radius_m=_body_radius_m(request.body),
        crossing=False,
    )
    source_resolutions = {record.coverage.source_resolution_m for record in selected}
    source_resolution = (
        next(iter(source_resolutions)) if len(source_resolutions) == 1 else max(source_resolutions)
    )
    window_sizes = {record.coverage.window_size_m for record in selected}
    window_size = next(iter(window_sizes)) if len(window_sizes) == 1 else max(window_sizes)
    warnings = _base_warnings(
        request,
        analysis_bbox=analysis_bbox,
        analysis_kind=ANALYSIS_KIND_COMMITTED,
        cell_count=len(selected),
    )
    if len(source_resolutions) > 1:
        warnings.append(
            "Selected cells come from more than one native source resolution; "
            f"source_resolution_m reports the coarsest ({source_resolution:.3f} m)."
        )
    if request.body == "Mars":
        mars_coverage = _coverage_for_body("Mars", catalog)
        if mars_coverage.evidence_only_cell_ids:
            warnings.append(
                "Mars MOLA MEGDR cells in "
                f"{', '.join(sorted({record.coverage.region_id for record in catalog.records_for_body('Mars') if not record.coverage.analysis_eligible}))} "
                "are evidence-only: native 463.0576672 m support with interpolated median "
                "bins. They are excluded from this comparable 250 m analysis."
            )
    result = PlanetaryAoiResult(
        area_id=request.area_id,
        body=request.body,
        requested_bbox=dict(request.requested_bbox),
        analysis_bbox=analysis_bbox,
        cells=tuple(record.coverage.candidate_id for record in selected),
        features=features,
        method_version=PLANETARY_AOI_METHOD_VERSION,
        resolution_m=ANALYSIS_RESOLUTION_M,
        source_resolution_m=source_resolution,
        window_size_m=window_size,
        valid_fraction=valid_fraction,
        crs=request.crs,
        coordinate_frame=request.coordinate_frame,
        geometry=request.geometry,
        area_km2=request.area_km2,
        analysis_area_km2=analysis_area_km2,
        dataset=_dataset_payload(
            request.body,
            selected,
            catalog=catalog,
            analysis_kind=ANALYSIS_KIND_COMMITTED,
        ),
        source_assets=tuple(source_assets),
        provider_status=_provider_status(
            selected, analysis_kind=ANALYSIS_KIND_COMMITTED, network_used=False
        ),
        warnings=tuple(warnings),
        checksum="",
        dimension_support=_result_dimension_support(
            request.body,
            cell_count=len(selected),
            feature_count=len(features),
            analysis_kind=ANALYSIS_KIND_COMMITTED,
        ),
        coverage_note=_analysis_coverage_note(
            request, selected, analysis_kind=ANALYSIS_KIND_COMMITTED
        ),
        analysis_kind=ANALYSIS_KIND_COMMITTED,
        cell_results=tuple(cell_results),
    )
    return _finalize(result)


def _out_of_coverage(
    body: str,
    catalog: _Catalog,
    request: PlanetaryAoiRequest,
    *,
    allow_remote: bool,
) -> PlanetaryAoiError:
    coverage = _coverage_for_body(body, catalog)
    region_text = "; ".join(
        f"{region['region_id']} bbox={region['bbox_body_lonlat']} "
        f"support={region['support_status']} cells={region['cell_count']}"
        for region in coverage.regions
    )
    details = (
        f"No committed analysis-eligible {body} cell covers requested bbox "
        f"{request.requested_bbox}. Available committed regions: {region_text}."
    )
    if body == "Mars":
        details += (
            " The Mars MOLA MEGDR cells at Jezero are evidence-only (native 463.0576672 m "
            "support, interpolated median bins) and are not comparable 250 m on-demand "
            "coverage; they are never silently merged."
        )
    if body == "Moon" and not allow_remote:
        details += (
            " A bounded live Moon read is available only with allow_remote=True and is "
            "reported as analysis_kind='live_bounded_read'."
        )
    if body == "Mars" and allow_remote:
        details += " Live remote reads are implemented for Moon only; Mars stays out of coverage."
    details += (
        " Coverage is not inferred from the planetary basemap, and an empty successful "
        "result is never returned."
    )
    return PlanetaryAoiError("out_of_coverage", details)


# ---------------------------------------------------------------------------
# Moon-only bounded live read
# ---------------------------------------------------------------------------


def _live_moon_cell(request: PlanetaryAoiRequest) -> CoarseCellResult:
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={request.center_lat:.10f} +lon_0={request.center_lon:.10f} "
        "+R=1737400 +units=m +no_defs"
    )
    try:
        return coarse_cell_from_dem(
            MOON_LOLA128.raster_url,
            source=MOON_LOLA128,
            region_id="moon-lola128-on-demand-live-01",
            row=0,
            col=0,
            target_crs=crs,
            center_xy=(0.0, 0.0),
            n_pixels=ANALYSIS_WINDOW_PIXELS,
            resolution_m=ANALYSIS_RESOLUTION_M,
            allow_remote=True,
        )
    except PlanetaryCoarseError as exc:
        raise PlanetaryAoiError(
            "live_read_failed", f"bounded Moon LOLA128 live read failed: {exc.message}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - network/GDAL failure must be explicit
        raise PlanetaryAoiError(
            "live_read_failed", f"bounded Moon LOLA128 live read failed: {exc}"
        ) from exc


def _analyze_live_moon(
    request: PlanetaryAoiRequest,
    catalog: _Catalog,
) -> PlanetaryAoiResult:
    source = MOON_LOLA128
    cell = _live_moon_cell(request)
    pad = int(cell.pad_pixels)
    slope = horn_slope_degrees(cell.fixture, float(cell.analysis_resolution_m))
    core = slope[slice(pad, -pad), slice(pad, -pad)]
    valid = np.isfinite(core)
    valid_pixels = int(valid.sum())
    missing_pixels = int(core.size - valid_pixels)
    try:
        cell_features = extract_coarse_features(core, valid)
    except CoarseExtractionError as exc:
        raise PlanetaryAoiError(
            "live_read_failed", f"bounded Moon live aggregation failed: {exc.message}"
        ) from exc
    features, valid_fraction, _, _ = _aggregate_slope_features([core], [valid])
    analysis_bbox = dict(cell.bbox_body_lonlat)
    analysis_area_km2 = _request_area_km2(
        float(analysis_bbox["west"]),
        float(analysis_bbox["south"]),
        float(analysis_bbox["east"]),
        float(analysis_bbox["north"]),
        radius_m=_body_radius_m("Moon"),
        crossing=False,
    )
    asset = {
        "uri": source.raster_url,
        "path": None,
        "recorded_path": source.raster_url,
        "sha256": None,
        "checksum": None,
        "bytes": None,
        "cell_id": cell.candidate_id,
        "region_id": cell.region_id,
        "source_id": source.source_id,
        "crs_wkt": cell.crs_wkt,
        "crs_name": cell.crs_name,
        "analysis_resolution_m": cell.analysis_resolution_m,
        "source_resolution_m": cell.source_resolution_m,
        "window_size_m": cell.window_size_m,
        "transform": list(tuple(cell.fixture_transform)[:6]),
        "pad_pixels": cell.pad_pixels,
        "analysis_kind": ANALYSIS_KIND_LIVE,
        "source_read_upper_bound_bytes": cell.source_read_bytes_upper_bound,
        "source_window_shape": list(cell.source_window_shape),
    }
    cell_result = {
        "candidate_id": cell.candidate_id,
        "cell_id": cell.candidate_id,
        "body": "Moon",
        "region_id": cell.region_id,
        "source_id": source.source_id,
        "bbox_body_lonlat": dict(cell.bbox_body_lonlat),
        "bbox_metric": dict(cell.bbox_metric),
        "crs_name": cell.crs_name,
        "crs_wkt": cell.crs_wkt,
        "features": dict(cell_features),
        "valid_slope_pixels": valid_pixels,
        "missing_slope_pixels": missing_pixels,
        "valid_fraction": float(valid_pixels / (valid_pixels + missing_pixels)),
        "fixture_path": None,
        "fixture_recorded_path": None,
        "fixture_sha256": None,
        "fixture_bytes": None,
        "analysis_resolution_m": cell.analysis_resolution_m,
        "source_resolution_m": cell.source_resolution_m,
        "window_size_m": cell.window_size_m,
        "support_status": SUPPORT_ANALYSIS_ELIGIBLE,
        "analysis_eligible": True,
        "analysis_kind": ANALYSIS_KIND_LIVE,
        "source_read_upper_bound_bytes": cell.source_read_bytes_upper_bound,
    }
    dataset = {
        **source.to_payload(),
        "dataset_id": source.dataset_id,
        "source_id": source.source_id,
        "body": "Moon",
        "url": source.raster_url or source.url,
        "raster_url": source.raster_url,
        "source_crs": source.coordinate_system,
        "data_kind": "observed_derived",
        "method_version": PLANETARY_AOI_METHOD_VERSION,
        "source_method_version": PLANETARY_COARSE_METHOD_VERSION,
        "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
        "analysis_kind": ANALYSIS_KIND_LIVE,
        "index_path": _display_path(catalog.index_path, catalog),
        "index_sha256": catalog.index_sha256,
        "source_manifests": [],
        "sources": [source.to_payload()],
    }
    provider_status = {
        source.source_id: {
            "source_id": source.source_id,
            "provider": source.provider,
            "status": "live_bounded_read_ok",
            "network_used": True,
            "cells": 1,
            "fixture_checksums_verified": False,
            "url": source.raster_url or source.url,
            "license": source.license,
            "license_url": source.license_url,
            "native_resolution_m": source.native_resolution_m,
            "source_read_upper_bound_bytes": cell.source_read_bytes_upper_bound,
            "source_window_shape": list(cell.source_window_shape),
        }
    }
    warnings = _base_warnings(
        request,
        analysis_bbox=analysis_bbox,
        analysis_kind=ANALYSIS_KIND_LIVE,
        cell_count=1,
    )
    warnings.append(
        "The live read used a 5 km window centered on the AOI; if the requested bbox was "
        "larger, only the central 5 km cell is covered."
    )
    warnings.append(
        "A live read is not a committed reproducible fixture. Reproducibility requires the "
        "recorded PGDA URL plus the exact source method version and network access."
    )
    result = PlanetaryAoiResult(
        area_id=request.area_id,
        body="Moon",
        requested_bbox=dict(request.requested_bbox),
        analysis_bbox=analysis_bbox,
        cells=(cell.candidate_id,),
        features=features,
        method_version=PLANETARY_AOI_METHOD_VERSION,
        resolution_m=ANALYSIS_RESOLUTION_M,
        source_resolution_m=source.native_resolution_m,
        window_size_m=cell.window_size_m,
        valid_fraction=valid_fraction,
        crs=request.crs,
        coordinate_frame=COORDINATE_FRAMES["Moon"],
        geometry=request.geometry,
        area_km2=request.area_km2,
        analysis_area_km2=analysis_area_km2,
        dataset=dataset,
        source_assets=(asset,),
        provider_status=provider_status,
        warnings=tuple(warnings),
        checksum="",
        dimension_support=_result_dimension_support(
            "Moon",
            cell_count=1,
            feature_count=len(features),
            analysis_kind=ANALYSIS_KIND_LIVE,
        ),
        coverage_note=_analysis_coverage_note(request, (), analysis_kind=ANALYSIS_KIND_LIVE),
        analysis_kind=ANALYSIS_KIND_LIVE,
        cell_results=(cell_result,),
    )
    return _finalize(result)


# ---------------------------------------------------------------------------
# Public analysis entry point
# ---------------------------------------------------------------------------


def analyze_planetary_aoi(
    body: str,
    bbox: Mapping[str, float] | Sequence[float],
    *,
    root: str | Path | None = None,
    allow_remote: bool = False,
) -> PlanetaryAoiResult:
    """Analyze a Moon/Mars AOI from committed cells or one explicit live Moon read.

    Offline by default. ``allow_remote=True`` only enables the bounded Moon live path
    when no committed cell covers the AOI. Out-of-coverage is always an explicit
    ``PlanetaryAoiError('out_of_coverage', ...)``, never an empty success.
    """
    _assert_body(body)
    catalog = _load_catalog(root)
    request = _build_request(body, bbox, catalog)
    selected = _select_records(catalog.records_for_body(body), request)
    if selected:
        return _analyze_committed(request, catalog, selected)
    if allow_remote and body == "Moon":
        return _analyze_live_moon(request, catalog)
    raise _out_of_coverage(body, catalog, request, allow_remote=allow_remote)


__all__ = [
    "ANALYSIS_KIND_COMMITTED",
    "ANALYSIS_KIND_LIVE",
    "ANALYSIS_RESOLUTION_M",
    "AOI_MAX_SIDE_M",
    "AOI_MIN_SIDE_M",
    "COORDINATE_FRAMES",
    "DIMENSION_CODES",
    "PLANETARY_AOI_METHOD_VERSION",
    "PLANETARY_AOI_SCHEMA_VERSION",
    "PlanetaryAoiError",
    "PlanetaryAoiRequest",
    "PlanetaryAoiResult",
    "PlanetaryBodyCoverage",
    "PlanetaryCellCoverage",
    "SUPPORTED_BODIES",
    "analyze_planetary_aoi",
    "planetary_body_coverage",
    "validate_planetary_aoi",
]
