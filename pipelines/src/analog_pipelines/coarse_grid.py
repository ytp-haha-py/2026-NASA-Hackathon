"""Deterministic 5 km metric candidate grid (Issue #30).

The grid is generated in a projected metre frame (UTM by default), never by
assuming longitude/latitude degrees are constant metres. Only grid metadata is
produced; no raster is read here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from pyproj import CRS, Transformer

GRID_VERSION = "coarse-grid-1.0.0"
WINDOW_SIZE_M = 5000
GEOMORPHO_MIN_LAT = -60.0
GEOMORPHO_MAX_LAT = 85.0


class GridError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GridRegion:
    region_id: str
    name: str
    west: float
    south: float
    east: float
    north: float
    crs: str | None = None

    def bbox(self) -> dict[str, float]:
        return {"west": self.west, "south": self.south, "east": self.east, "north": self.north}


@dataclass(frozen=True, slots=True)
class CandidateCell:
    candidate_id: str
    grid_version: str
    region_id: str
    row: int
    col: int
    crs: str
    window_size_m: int
    bbox_wgs84: dict[str, float]
    bbox_metric: dict[str, float]


@dataclass(slots=True)
class GridCoverage:
    region_id: str
    requested_bbox: dict[str, float]
    metric_bbox: dict[str, float]
    candidate_count: int
    covered_area_km2: float
    uncovered_margin_m: dict[str, float]
    sample_label: str = "exhaustive_within_bbox"


@dataclass(slots=True)
class GridPlan:
    grid_version: str
    window_size_m: int
    regions: list[GridRegion]
    cells: list[CandidateCell] = field(default_factory=list)
    coverage: list[GridCoverage] = field(default_factory=list)


def _utm_crs(lon: float, lat: float) -> CRS:
    if not (-80 <= lat <= 84):
        raise GridError("UTM selection is limited to latitude -80..84")
    zone = int((lon + 180.0) // 6.0) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def _validate_region(region: GridRegion) -> None:
    if not region.region_id or not region.region_id.replace("-", "").replace("_", "").isalnum():
        raise GridError(f"invalid region_id {region.region_id!r}")
    if not (-180 <= region.west < region.east <= 180):
        raise GridError("region must satisfy -180 <= west < east <= 180")
    if not (-90 <= region.south < region.north <= 90):
        raise GridError("region must satisfy -90 <= south < north <= 90")
    if region.south < GEOMORPHO_MIN_LAT or region.north > GEOMORPHO_MAX_LAT:
        raise GridError(
            f"region outside Geomorpho90m coverage {GEOMORPHO_MIN_LAT}..{GEOMORPHO_MAX_LAT} "
            "latitude; Antarctica is not covered"
        )


def generate_region_grid(
    region: GridRegion, *, window_size_m: int = WINDOW_SIZE_M
) -> tuple[list[CandidateCell], GridCoverage]:
    _validate_region(region)
    if window_size_m <= 0:
        raise GridError("window_size_m must be positive")
    center_lon = (region.west + region.east) / 2
    center_lat = (region.south + region.north) / 2
    target_crs = CRS.from_user_input(region.crs) if region.crs else _utm_crs(center_lon, center_lat)
    if not target_crs.is_projected:
        raise GridError("grid CRS must be projected")
    unit = target_crs.axis_info[0].unit_name.lower() if target_crs.axis_info else ""
    if unit not in {"metre", "meter", "m"}:
        raise GridError(f"grid CRS must use metres, got {unit!r}")

    forward = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    inverse = Transformer.from_crs(target_crs, "EPSG:4326", always_xy=True)
    xs, ys = forward.transform(
        [region.west, region.east, region.west, region.east],
        [region.south, region.south, region.north, region.north],
    )
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    width = maxx - minx
    height = maxy - miny
    cols = int(width // window_size_m)
    rows = int(height // window_size_m)
    if cols < 1 or rows < 1:
        raise GridError("region is smaller than one grid window")

    cells: list[CandidateCell] = []
    for row in range(rows):
        for col in range(cols):
            west_m = minx + col * window_size_m
            south_m = miny + row * window_size_m
            east_m = west_m + window_size_m
            north_m = south_m + window_size_m
            lon_w, lat_s = inverse.transform(west_m, south_m)
            lon_e, lat_n = inverse.transform(east_m, north_m)
            cells.append(
                CandidateCell(
                    candidate_id=f"coarse-v1:{region.region_id}:{row:04d}:{col:04d}",
                    grid_version=GRID_VERSION,
                    region_id=region.region_id,
                    row=row,
                    col=col,
                    crs=target_crs.to_string(),
                    window_size_m=window_size_m,
                    bbox_wgs84={
                        "west": lon_w,
                        "south": lat_s,
                        "east": lon_e,
                        "north": lat_n,
                    },
                    bbox_metric={
                        "west": west_m,
                        "south": south_m,
                        "east": east_m,
                        "north": north_m,
                    },
                )
            )
    covered = len(cells) * (window_size_m**2) / 1e6
    coverage = GridCoverage(
        region_id=region.region_id,
        requested_bbox=region.bbox(),
        metric_bbox={"west": minx, "south": miny, "east": maxx, "north": maxy},
        candidate_count=len(cells),
        covered_area_km2=covered,
        uncovered_margin_m={
            "east": width - cols * window_size_m,
            "north": height - rows * window_size_m,
        },
    )
    return cells, coverage


def generate_grid(regions: Iterable[GridRegion], *, window_size_m: int = WINDOW_SIZE_M) -> GridPlan:
    regions = list(regions)
    if not regions:
        raise GridError("at least one region is required")
    ids = [region.region_id for region in regions]
    if len(ids) != len(set(ids)):
        raise GridError("duplicate region_id")
    all_cells: list[CandidateCell] = []
    coverage: list[GridCoverage] = []
    for region in regions:
        cells, report = generate_region_grid(region, window_size_m=window_size_m)
        all_cells.extend(cells)
        coverage.append(report)
    return GridPlan(
        grid_version=GRID_VERSION,
        window_size_m=window_size_m,
        regions=regions,
        cells=all_cells,
        coverage=coverage,
    )


def bbox_overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    return not (
        a["east"] <= b["west"]
        or b["east"] <= a["west"]
        or a["north"] <= b["south"]
        or b["north"] <= a["south"]
    )


def assert_no_overlap(cells: Iterable[CandidateCell]) -> None:
    seen: dict[tuple[int, int], CandidateCell] = {}
    for cell in cells:
        key = (round(cell.bbox_metric["west"], 6), round(cell.bbox_metric["south"], 6))
        if key in seen:
            raise GridError(
                f"duplicate grid origins: {seen[key].candidate_id} / {cell.candidate_id}"
            )
        seen[key] = cell
