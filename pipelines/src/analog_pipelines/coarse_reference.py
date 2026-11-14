"""Coarse-reference interfaces and 90 m refinement planning (Issues #35/#37).

No real coarse Moon/Mars reference has been validated in the offline phase. These
types make the comparability gate explicit and plan fine windows without reading a
DEM or downloading anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pyproj import Transformer

from analog_pipelines.coarse_features import (
    COARSE_FEATURE_BY_ID,
    COARSE_METHOD_VERSION,
    ComparabilityError,
)
from analog_pipelines.coarse_grid import WINDOW_SIZE_M

FINE_WINDOW_M = 1980
FINE_METHOD_VERSION = "terrain-metrics-1.0.0"


@dataclass(frozen=True, slots=True)
class CoarseReference:
    reference_id: str
    body: str
    feature_values: dict[str, float]
    method_version: str
    spatial_support: str
    source_dataset_id: str
    quality_flags: tuple[str, ...] = ()


class CoarseReferenceProvider(Protocol):
    def list_references(self) -> Sequence[CoarseReference]: ...

    def get_reference(self, reference_id: str) -> CoarseReference | None: ...


def validate_coarse_reference(reference: CoarseReference) -> CoarseReference:
    if reference.body not in {"Moon", "Mars"}:
        raise ComparabilityError("coarse planetary references must be Moon or Mars")
    if reference.method_version != COARSE_METHOD_VERSION:
        raise ComparabilityError(
            f"reference method_version {reference.method_version!r} is not the coarse method "
            f"{COARSE_METHOD_VERSION!r}"
        )
    unknown = sorted(set(reference.feature_values) - set(COARSE_FEATURE_BY_ID))
    if unknown:
        raise ComparabilityError(f"unsupported coarse reference features: {unknown}")
    if not reference.feature_values:
        raise ComparabilityError("coarse reference has no feature values")
    for feature_id, value in reference.feature_values.items():
        definition = COARSE_FEATURE_BY_ID[feature_id]
        if not definition.min_value <= value <= definition.max_value:
            raise ComparabilityError(f"{feature_id} value outside coarse domain")
    return reference


def reject_coarse_fine_comparison(coarse_method: str, fine_method: str) -> None:
    if coarse_method == COARSE_METHOD_VERSION or fine_method == FINE_METHOD_VERSION:
        raise ComparabilityError(
            "Coarse 250 m and fine 90 m terrain methods are separate; compare only after "
            "explicit 90 m refinement with the fine method version."
        )


@dataclass(frozen=True, slots=True)
class RefineWindow:
    fine_window_id: str
    parent_candidate_id: str
    bbox_wgs84: dict[str, float]
    window_size_m: int
    method_version: str = FINE_METHOD_VERSION


def plan_refine_windows(
    *,
    parent_candidate_id: str,
    parent_bbox_wgs84: dict[str, float],
    parent_crs: str = "EPSG:4326",
    per_side: int = 2,
) -> list[RefineWindow]:
    """Plan deterministic 1.98 km fine windows inside a coarse cell.

    This is a plan, not a measurement. It does not fetch DEM data; the existing AOI
    pipeline must be invoked later with explicit authorization.
    """
    if per_side < 1 or per_side > 4:
        raise ComparabilityError("per_side must be within 1..4")
    if parent_crs not in {"EPSG:4326", "WGS84"}:
        raise ComparabilityError("offline refine planning currently accepts WGS84 parent bboxes")
    west = parent_bbox_wgs84["west"]
    south = parent_bbox_wgs84["south"]
    east = parent_bbox_wgs84["east"]
    north = parent_bbox_wgs84["north"]
    lon_center = (west + east) / 2
    lat_center = (south + north) / 2
    zone = int((lon_center + 180) // 6) + 1
    utm = f"EPSG:{(32600 if lat_center >= 0 else 32700) + zone}"
    forward = Transformer.from_crs("EPSG:4326", utm, always_xy=True)
    inverse = Transformer.from_crs(utm, "EPSG:4326", always_xy=True)
    xs, ys = forward.transform([west, east, west, east], [south, south, north, north])
    minx, _maxx = min(xs), max(xs)
    miny, _maxy = min(ys), max(ys)
    margin_x = (WINDOW_SIZE_M - per_side * FINE_WINDOW_M) / 2
    margin_y = (WINDOW_SIZE_M - per_side * FINE_WINDOW_M) / 2
    if margin_x < 0 or margin_y < 0:
        raise ComparabilityError("requested fine windows do not fit inside the coarse cell")
    windows: list[RefineWindow] = []
    for row in range(per_side):
        for col in range(per_side):
            x0 = minx + margin_x + col * FINE_WINDOW_M
            y0 = miny + margin_y + row * FINE_WINDOW_M
            lon0, lat0 = inverse.transform(x0, y0)
            lon1, lat1 = inverse.transform(x0 + FINE_WINDOW_M, y0 + FINE_WINDOW_M)
            windows.append(
                RefineWindow(
                    fine_window_id=f"refine:{parent_candidate_id}:{row}:{col}",
                    parent_candidate_id=parent_candidate_id,
                    bbox_wgs84={"west": lon0, "south": lat0, "east": lon1, "north": lat1},
                    window_size_m=FINE_WINDOW_M,
                )
            )
    return windows
