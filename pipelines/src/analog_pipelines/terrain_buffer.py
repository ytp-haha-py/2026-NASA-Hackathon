"""Buffer and edge-validity bookkeeping for derived terrain features.

The production analysis core is a fixed 22 x 22 grid of 90 m cells (1980 m).
Every derived method needs a different amount of surrounding source support:

* window statistics (relief, roughness): 0 extra cells;
* Horn/central 3x3 operators (slope, flat fraction, central curvature): 1 cell;
* a 5x5 quadratic fit: 2 cells;
* a 7x7 quadratic fit: 3 cells;
* Gaussian sigma=90 m, truncated at 3 sigma, plus a 90 m derivative radius: 4 cells;
* GRASS ``r.geomorphon`` search=12 cells: 12 cells.

This module is deliberately separate from the production extractor. It can read a
buffered metric window from a local raster or a GDAL URL and report which core cells
have complete support. It never fills, pads, shortens a ray, or invents values.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from analog_api.domain.features import ANALYSIS_RESOLUTION_M, MIN_VALID_FRACTION
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.transform import Affine, from_origin
from rasterio.warp import reproject
from rasterio.windows import from_bounds

CORE_CELLS = 22
CORE_SIZE_M = CORE_CELLS * ANALYSIS_RESOLUTION_M
METRE_UNITS = {"m", "metre", "meter", "metres", "meters"}


@dataclass(frozen=True, slots=True)
class BufferRequirement:
    """One derived method's support requirement at the common 90 m resolution."""

    method_id: str
    label: str
    family: str
    support: str
    radius_cells: int
    rationale: str
    registered: bool = False

    @property
    def buffer_m(self) -> float:
        return self.radius_cells * ANALYSIS_RESOLUTION_M

    @property
    def full_footprint_cells(self) -> int:
        return CORE_CELLS + 2 * self.radius_cells

    @property
    def full_footprint_m(self) -> float:
        return self.full_footprint_cells * ANALYSIS_RESOLUTION_M

    @property
    def normal_support_cells(self) -> int:
        return core_support_cells(0, self.radius_cells)

    @property
    def normal_support_fraction(self) -> float:
        return self.normal_support_cells / float(CORE_CELLS * CORE_CELLS)

    @property
    def normal_edge_loss_cells(self) -> int:
        return CORE_CELLS * CORE_CELLS - self.normal_support_cells

    @property
    def min_full_support_buffer_cells(self) -> int:
        return self.radius_cells

    @property
    def min_gate_buffer_cells(self) -> int:
        return min_buffer_for_gate(self.radius_cells)

    def to_payload(self) -> dict[str, Any]:
        return {
            "method_id": self.method_id,
            "label": self.label,
            "family": self.family,
            "registered": self.registered,
            "support": self.support,
            "radius_cells": self.radius_cells,
            "buffer_m": self.buffer_m,
            "full_footprint_cells": self.full_footprint_cells,
            "full_footprint_m": self.full_footprint_m,
            "normal_crop": {
                "provided_buffer_cells": 0,
                "provided_buffer_m": 0.0,
                "supported_core_cells": self.normal_support_cells,
                "supported_core_fraction": self.normal_support_fraction,
                "edge_loss_cells": self.normal_edge_loss_cells,
            },
            "minimum_buffer_for_full_core_support": {
                "cells": self.min_full_support_buffer_cells,
                "metres": self.min_full_support_buffer_cells * ANALYSIS_RESOLUTION_M,
            },
            "minimum_buffer_for_min_valid_fraction": {
                "cells": self.min_gate_buffer_cells,
                "metres": self.min_gate_buffer_cells * ANALYSIS_RESOLUTION_M,
                "min_valid_fraction": MIN_VALID_FRACTION,
            },
        }


BUFFER_REQUIREMENTS: tuple[BufferRequirement, ...] = (
    BufferRequirement(
        method_id="slope_mean_deg",
        label="Mean slope",
        family="registry_terrain",
        support="Horn 3x3 gradient",
        radius_cells=1,
        rationale="A 3x3 stencil needs one cell on every side; the normal 22x22 crop "
        "computes slope only on its 20x20 interior.",
        registered=True,
    ),
    BufferRequirement(
        method_id="slope_p90_deg",
        label="90th percentile slope",
        family="registry_terrain",
        support="Horn 3x3 gradient",
        radius_cells=1,
        rationale="Same gradient grid as mean slope; one-cell support on every side.",
        registered=True,
    ),
    BufferRequirement(
        method_id="terrain_relief_p95_p05_m",
        label="Terrain relief (p95-p05)",
        family="registry_terrain",
        support="window percentile statistic",
        radius_cells=0,
        rationale="A statistic of the core elevations needs no surrounding cells.",
        registered=True,
    ),
    BufferRequirement(
        method_id="roughness_std_m",
        label="Detrended roughness",
        family="registry_terrain",
        support="window plane fit",
        radius_cells=0,
        rationale="The plane is fitted to the core cells only; no outside cell is read.",
        registered=True,
    ),
    BufferRequirement(
        method_id="flat_terrain_fraction",
        label="Flat terrain fraction",
        family="registry_terrain",
        support="Horn 3x3 gradient",
        radius_cells=1,
        rationale="Same Horn 3x3 grid as slope; one cell on every side.",
        registered=True,
    ),
    BufferRequirement(
        method_id="surface_curvedness_central",
        label="Central-difference surface curvedness",
        family="curvature",
        support="central 3x3 derivatives",
        radius_cells=1,
        rationale="p, q, r, s, t use the 3x3 neighbourhood; one-cell support on every side.",
    ),
    BufferRequirement(
        method_id="mean_curvature_central",
        label="Central-difference mean (total) curvature",
        family="curvature",
        support="central 3x3 derivatives",
        radius_cells=1,
        rationale="Graph mean curvature H uses the same 3x3 derivatives.",
    ),
    BufferRequirement(
        method_id="gaussian_curvature_central",
        label="Central-difference Gaussian curvature",
        family="curvature",
        support="central 3x3 derivatives",
        radius_cells=1,
        rationale="Gaussian curvature K uses the same 3x3 derivatives.",
    ),
    BufferRequirement(
        method_id="surface_curvedness_quadratic_r2",
        label="Quadratic-fit surface curvedness (5x5)",
        family="curvature",
        support="least-squares quadratic fit, radius 2",
        radius_cells=2,
        rationale="A 5x5 local quadratic fit needs two cells on every side.",
    ),
    BufferRequirement(
        method_id="surface_curvedness_quadratic_r3",
        label="Quadratic-fit surface curvedness (7x7)",
        family="curvature",
        support="least-squares quadratic fit, radius 3",
        radius_cells=3,
        rationale="A 7x7 local quadratic fit needs three cells on every side.",
    ),
    BufferRequirement(
        method_id="surface_curvedness_smoothed",
        label="Smoothed surface curvedness",
        family="curvature",
        support="Gaussian sigma=90 m, 3-sigma truncation, + 3x3 derivative",
        radius_cells=4,
        rationale="Smoothing radius ceil(3*1)=3 cells plus one derivative cell = 4 cells.",
    ),
    BufferRequirement(
        method_id="profile_curvature_central",
        label="Central-difference profile curvature",
        family="curvature",
        support="central 3x3 derivatives",
        radius_cells=1,
        rationale="Profile curvature uses the same 3x3 derivatives as central curvedness.",
    ),
    BufferRequirement(
        method_id="profile_curvature_smoothed",
        label="Smoothed profile curvature",
        family="curvature",
        support="Gaussian sigma=90 m, 3-sigma truncation, + 3x3 derivative",
        radius_cells=4,
        rationale="Same smoothing and derivative support as smoothed curvedness.",
    ),
    BufferRequirement(
        method_id="tangential_curvature_central",
        label="Central-difference tangential curvature",
        family="curvature",
        support="central 3x3 derivatives",
        radius_cells=1,
        rationale="Tangential curvature uses the same 3x3 derivatives.",
    ),
    BufferRequirement(
        method_id="tangential_curvature_smoothed",
        label="Smoothed tangential curvature",
        family="curvature",
        support="Gaussian sigma=90 m, 3-sigma truncation, + 3x3 derivative",
        radius_cells=4,
        rationale="Same smoothing and derivative support as smoothed curvedness.",
    ),
    BufferRequirement(
        method_id="geomorphon_10class",
        label="GRASS r.geomorphon 10-class composition",
        family="landform",
        support="8 rays, search=12 cells, skip=0, flat=1 degree, no edge extension",
        radius_cells=12,
        rationale="Each core cell must compare against the full 12-cell search distance "
        "in every direction; the normal 22x22 crop cannot support a single core cell.",
    ),
)

BUFFER_REQUIREMENT_BY_ID: dict[str, BufferRequirement] = {
    requirement.method_id: requirement for requirement in BUFFER_REQUIREMENTS
}


def core_support_cells(
    provided_buffer_cells: int, required_buffer_cells: int, *, core_cells: int = CORE_CELLS
) -> int:
    """Number of core cells with a complete neighbourhood.

    ``provided_buffer_cells`` is the halo already present around the 22x22 core.
    A required radius larger than the provided halo removes that many cells from
    every side. No partial/shortened neighbourhood is counted as supported.
    """
    if core_cells < 0:
        raise ValueError("core_cells must be non-negative")
    if provided_buffer_cells < 0 or required_buffer_cells < 0:
        raise ValueError("buffer cells must be non-negative")
    deficit = max(0, required_buffer_cells - provided_buffer_cells)
    side = max(0, core_cells - 2 * deficit)
    return side * side


def core_support_fraction(
    provided_buffer_cells: int, required_buffer_cells: int, *, core_cells: int = CORE_CELLS
) -> float:
    total = core_cells * core_cells
    if total == 0:
        raise ValueError("core_cells must be positive")
    return core_support_cells(
        provided_buffer_cells, required_buffer_cells, core_cells=core_cells
    ) / float(total)


def min_buffer_for_gate(
    required_buffer_cells: int,
    *,
    gate: float = MIN_VALID_FRACTION,
    core_cells: int = CORE_CELLS,
) -> int:
    """Smallest halo that meets ``gate`` complete-core support, or full support.

    The 80% gate is the documented minimum for accepting a core summary. It does
    not imply full support: for example a four-cell method passes at three cells
    but still loses a one-cell rim on every side.
    """
    if not (0 < gate <= 1):
        raise ValueError("gate must be in (0, 1]")
    for provided in range(required_buffer_cells + 1):
        if core_support_fraction(provided, required_buffer_cells, core_cells=core_cells) >= gate:
            return provided
    return required_buffer_cells


def support_mask(shape: tuple[int, int], radius_cells: int) -> np.ndarray:
    """Boolean mask of cells whose full ``radius_cells`` neighbourhood exists."""
    rows, cols = shape
    mask = np.zeros(shape, dtype=bool)
    if radius_cells == 0:
        mask[:] = True
        return mask
    if rows <= 2 * radius_cells or cols <= 2 * radius_cells:
        return mask
    mask[radius_cells : rows - radius_cells, radius_cells : cols - radius_cells] = True
    return mask


def edge_validity(provided_buffer_cells: int = 0) -> list[dict[str, Any]]:
    """Per-method support counts for a crop with the given halo."""
    if provided_buffer_cells < 0:
        raise ValueError("provided_buffer_cells must be non-negative")
    rows: list[dict[str, Any]] = []
    for requirement in BUFFER_REQUIREMENTS:
        supported = core_support_cells(provided_buffer_cells, requirement.radius_cells)
        fraction = supported / float(CORE_CELLS * CORE_CELLS)
        rows.append(
            {
                "method_id": requirement.method_id,
                "label": requirement.label,
                "family": requirement.family,
                "registered": requirement.registered,
                "radius_cells": requirement.radius_cells,
                "required_buffer_m": requirement.buffer_m,
                "provided_buffer_cells": provided_buffer_cells,
                "provided_buffer_m": provided_buffer_cells * ANALYSIS_RESOLUTION_M,
                "supported_core_cells": supported,
                "supported_core_fraction": fraction,
                "missing_core_cells": CORE_CELLS * CORE_CELLS - supported,
                "full_core_support": supported == CORE_CELLS * CORE_CELLS,
                "meets_min_valid_fraction": fraction >= MIN_VALID_FRACTION,
            }
        )
    return rows


def buffer_table_payload() -> dict[str, Any]:
    return {
        "core_cells": CORE_CELLS,
        "core_size_m": CORE_SIZE_M,
        "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
        "min_valid_fraction": MIN_VALID_FRACTION,
        "methods": [requirement.to_payload() for requirement in BUFFER_REQUIREMENTS],
    }


@dataclass(frozen=True, slots=True)
class MetricWindow:
    """A metric-CRS elevation grid with a known core offset."""

    elevation: np.ndarray
    transform: Affine
    crs: CRS
    source_crs: CRS
    core_cells: int
    buffer_cells: int
    resolution_m: float
    warnings: tuple[str, ...] = ()
    source_resolution: tuple[float, float] | None = None
    source_shape: tuple[int, int] | None = None
    source_nodata: float | None = None

    @property
    def grid_cells(self) -> int:
        return self.core_cells + 2 * self.buffer_cells

    def core(self) -> np.ndarray:
        offset = self.buffer_cells
        return self.elevation[offset : offset + self.core_cells, offset : offset + self.core_cells]

    @property
    def core_transform(self) -> Affine:
        """Affine transform of the central core, independent of the buffer size."""
        offset = self.buffer_cells * self.resolution_m
        return Affine.translation(offset, -offset) @ self.transform


def _validate_metric_source(src: rasterio.DatasetReader, source_label: str) -> CRS:
    src_crs = CRS.from_user_input(src.crs) if src.crs else None
    if src_crs is None:
        raise ValueError(f"{source_label}: source raster has no CRS")
    if src_crs.is_projected:
        units = src_crs.axis_info[0].unit_name.lower() if src_crs.axis_info else ""
        if units not in METRE_UNITS:
            raise ValueError(
                f"{source_label}: projected source units are {units!r}, expected metres"
            )
    return src_crs


def read_metric_window(
    source: str | Any,
    *,
    dst_crs: CRS | str,
    center_xy: tuple[float, float],
    core_cells: int = CORE_CELLS,
    buffer_cells: int = 0,
    resolution_m: float = ANALYSIS_RESOLUTION_M,
) -> MetricWindow:
    """Read a core window plus ``buffer_cells`` of 90 m support from ``source``.

    The destination grid is aligned to the core: its origin is the core's upper-left
    corner minus the buffer, so the central 22x22 cells are identical for every
    buffer size. Missing source data stays NaN. The source read is bounded to the
    requested metric footprint plus two native pixels.
    """
    if core_cells <= 0:
        raise ValueError("core_cells must be positive")
    if buffer_cells < 0:
        raise ValueError("buffer_cells must be non-negative")
    if not np.isfinite(resolution_m) or resolution_m <= 0:
        raise ValueError("resolution_m must be positive and finite")
    dst_crs = CRS.from_user_input(dst_crs)
    if dst_crs.is_projected:
        units = dst_crs.axis_info[0].unit_name.lower() if dst_crs.axis_info else ""
        if units not in METRE_UNITS:
            raise ValueError(f"destination CRS units are {units!r}, expected metres")
    x, y = center_xy
    if not (np.isfinite(x) and np.isfinite(y)):
        raise ValueError("center_xy must be finite")

    grid = core_cells + 2 * buffer_cells
    # Build the buffered transform from the exact core transform so the central
    # 22x22 cell centres are bit-identical for every buffer size. Constructing both
    # independently from ``x +/- grid/2`` can differ at the last floating-point bit
    # and change bilinear weights on steep terrain.
    core_half = core_cells * resolution_m / 2.0
    core_transform = from_origin(x - core_half, y + core_half, resolution_m, resolution_m)
    dst_transform = (
        Affine.translation(-buffer_cells * resolution_m, buffer_cells * resolution_m)
        @ core_transform
    )
    half = grid * resolution_m / 2.0
    label = str(source)

    with rasterio.open(source) as src:
        src_crs = _validate_metric_source(src, label)
        if src.count < 1:
            raise ValueError(f"{label}: source raster has no bands")
        to_src = Transformer.from_crs(dst_crs, src_crs, always_xy=True)
        corners_x = [x - half, x + half, x - half, x + half]
        corners_y = [y - half, y - half, y + half, y + half]
        src_xs, src_ys = to_src.transform(corners_x, corners_y)
        # Four native pixels of padding: bilinear resampling at the outer destination
        # cell can touch the 2x2 source neighbourhood and a one-pixel guard band.
        pad = 4 * max(abs(src.transform.a), abs(src.transform.e))
        src_window = (
            from_bounds(
                min(src_xs) - pad,
                min(src_ys) - pad,
                max(src_xs) + pad,
                max(src_ys) + pad,
                src.transform,
            )
            .round_offsets()
            .round_lengths()
        )
        fill = src.nodata if src.nodata is not None else np.nan
        data = src.read(
            1,
            window=src_window,
            boundless=True,
            masked=True,
            out_dtype="float64",
            fill_value=fill,
        )
        source_array = np.asarray(data.filled(np.nan), dtype="float64")
        source_array[~np.isfinite(source_array)] = np.nan
        src_window_transform = src.window_transform(src_window)
        warnings: list[str] = []
        if src.nodata is None:
            warnings.append("source_nodata_undefined")

        destination = np.full((grid, grid), np.nan, dtype="float64")
        reproject(
            source=source_array,
            destination=destination,
            src_transform=src_window_transform,
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        return MetricWindow(
            elevation=destination,
            transform=dst_transform,
            crs=dst_crs,
            source_crs=src_crs,
            core_cells=core_cells,
            buffer_cells=buffer_cells,
            resolution_m=resolution_m,
            warnings=tuple(warnings),
            source_resolution=(float(src.res[0]), float(src.res[1])),
            source_shape=(int(src.height), int(src.width)),
            source_nodata=(
                float(src.nodata) if src.nodata is not None and np.isfinite(src.nodata) else None
            ),
        )


def window_checksum(array: np.ndarray, label: str) -> str:
    canonical = np.nan_to_num(np.asarray(array, dtype="<f8"), nan=-9999.0).tobytes()
    digest = hashlib.sha256(label.encode("utf-8") + canonical).hexdigest()
    return f"sha256:{digest}"


def summarize(values: np.ndarray) -> dict[str, Any]:
    """Summary of finite values only; an empty field is explicit, never zero-filled."""
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return {
            "count": 0,
            "mean": None,
            "mean_abs": None,
            "rms": None,
            "p10": None,
            "p50": None,
            "p90": None,
            "min": None,
            "max": None,
        }
    return {
        "count": int(finite.size),
        "mean": float(np.mean(finite)),
        "mean_abs": float(np.mean(np.abs(finite))),
        "rms": float(np.sqrt(np.mean(finite * finite))),
        "p10": float(np.percentile(finite, 10)),
        "p50": float(np.percentile(finite, 50)),
        "p90": float(np.percentile(finite, 90)),
        "min": float(np.min(finite)),
        "max": float(np.max(finite)),
    }


def compare_feature(
    normal: np.ndarray, buffered_core: np.ndarray, *, method_id: str
) -> dict[str, Any]:
    """Compare a normal-crop field with the buffered-crop core field.

    Both arrays must have the same core shape. Edge loss counts core cells that the
    normal crop cannot support but the buffered crop can; it is not a value change.
    """
    normal = np.asarray(normal, dtype=float)
    buffered_core = np.asarray(buffered_core, dtype=float)
    if normal.shape != buffered_core.shape:
        raise ValueError("normal and buffered fields must have the same core shape")
    normal_valid = np.isfinite(normal)
    buffered_valid = np.isfinite(buffered_core)
    common = normal_valid & buffered_valid
    edge_loss = int(np.count_nonzero(buffered_valid & ~normal_valid))
    normal_only = int(np.count_nonzero(normal_valid & ~buffered_valid))
    differences = buffered_core[common] - normal[common]
    core_size = int(normal.size)
    normal_summary = summarize(normal)
    buffered_summary = summarize(buffered_core)
    mean_shift = None
    relative_mean_shift = None
    if normal_summary["mean"] is not None and buffered_summary["mean"] is not None:
        mean_shift = float(buffered_summary["mean"] - normal_summary["mean"])
        if normal_summary["mean"] != 0:
            relative_mean_shift = float(mean_shift / abs(normal_summary["mean"]))
    return {
        "method_id": method_id,
        "core_cells": core_size,
        "normal_supported_cells": int(normal_valid.sum()),
        "normal_supported_fraction": float(normal_valid.sum() / core_size),
        "buffered_core_supported_cells": int(buffered_valid.sum()),
        "buffered_core_supported_fraction": float(buffered_valid.sum() / core_size),
        "common_cells": int(common.sum()),
        "edge_loss_cells": edge_loss,
        "edge_loss_fraction_of_core": float(edge_loss / core_size),
        "normal_only_cells": normal_only,
        "value_comparison_on_common_cells": {
            "count": int(differences.size),
            "mean_abs_diff": float(np.mean(np.abs(differences))) if differences.size else None,
            "rms_diff": (
                float(np.sqrt(np.mean(differences * differences))) if differences.size else None
            ),
            "max_abs_diff": float(np.max(np.abs(differences))) if differences.size else None,
        },
        "summary_normal_supported": normal_summary,
        "summary_buffered_core": buffered_summary,
        "mean_shift_buffered_minus_normal": mean_shift,
        "relative_mean_shift": relative_mean_shift,
    }


def compare_support(
    normal_support: np.ndarray, buffered_core_support: np.ndarray, *, method_id: str
) -> dict[str, Any]:
    """Compare boolean support masks without inventing feature values."""
    normal_support = np.asarray(normal_support, dtype=bool)
    buffered_core_support = np.asarray(buffered_core_support, dtype=bool)
    if normal_support.shape != buffered_core_support.shape:
        raise ValueError("support masks must have the same core shape")
    core_size = int(normal_support.size)
    return {
        "method_id": method_id,
        "core_cells": core_size,
        "normal_supported_cells": int(normal_support.sum()),
        "normal_supported_fraction": float(normal_support.sum() / core_size),
        "buffered_core_supported_cells": int(buffered_core_support.sum()),
        "buffered_core_supported_fraction": float(buffered_core_support.sum() / core_size),
        "edge_loss_cells": int(np.count_nonzero(buffered_core_support & ~normal_support)),
        "normal_only_cells": int(np.count_nonzero(normal_support & ~buffered_core_support)),
        "value_comparison": None,
        "blocker": "No reference classifier output; support/edge loss only, not class agreement.",
    }


def compare_fields(
    reference: np.ndarray, candidate: np.ndarray, *, reference_id: str, candidate_id: str
) -> dict[str, Any]:
    """Summarize two research fields on their common finite support."""
    reference = np.asarray(reference, dtype=float)
    candidate = np.asarray(candidate, dtype=float)
    if reference.shape != candidate.shape:
        raise ValueError("fields must have the same shape")
    common = np.isfinite(reference) & np.isfinite(candidate)
    left = reference[common]
    right = candidate[common]
    differences = right - left
    correlation = None
    if left.size >= 2 and np.std(left) > 0 and np.std(right) > 0:
        correlation = float(np.corrcoef(left, right)[0, 1])
    return {
        "reference": reference_id,
        "candidate": candidate_id,
        "common_cells": int(common.sum()),
        "reference_summary": summarize(reference),
        "candidate_summary": summarize(candidate),
        "difference_summary": {
            "mean": float(np.mean(differences)) if differences.size else None,
            "mean_abs": float(np.mean(np.abs(differences))) if differences.size else None,
            "rms": float(np.sqrt(np.mean(differences * differences))) if differences.size else None,
            "max_abs": float(np.max(np.abs(differences))) if differences.size else None,
        },
        "correlation": correlation,
        "mean_ratio_candidate_over_reference": (
            float(np.mean(right) / np.mean(left)) if left.size and np.mean(left) != 0 else None
        ),
    }


def buffer_summary_rows() -> list[dict[str, Any]]:
    """Flat rows for the portable CSV artifact."""
    rows: list[dict[str, Any]] = []
    for requirement in BUFFER_REQUIREMENTS:
        rows.append(
            {
                "method_id": requirement.method_id,
                "label": requirement.label,
                "family": requirement.family,
                "registered": requirement.registered,
                "support": requirement.support,
                "required_buffer_cells": requirement.radius_cells,
                "required_buffer_m": requirement.buffer_m,
                "full_footprint_cells": requirement.full_footprint_cells,
                "full_footprint_m": requirement.full_footprint_m,
                "normal_crop_supported_cells": requirement.normal_support_cells,
                "normal_crop_supported_fraction": requirement.normal_support_fraction,
                "normal_crop_edge_loss_cells": requirement.normal_edge_loss_cells,
                "full_support_buffer_cells": requirement.min_full_support_buffer_cells,
                "full_support_buffer_m": (
                    requirement.min_full_support_buffer_cells * ANALYSIS_RESOLUTION_M
                ),
                "gate_buffer_cells": requirement.min_gate_buffer_cells,
                "gate_buffer_m": requirement.min_gate_buffer_cells * ANALYSIS_RESOLUTION_M,
                "min_valid_fraction": MIN_VALID_FRACTION,
            }
        )
    return rows
