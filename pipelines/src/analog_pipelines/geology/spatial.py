"""Native-scale polygon overlay. Optional GIS imports stay off the offline read path."""

from __future__ import annotations

from dataclasses import dataclass

from pyproj import CRS, Transformer

from analog_pipelines.geology.models import Area, validate_body_crs


def transform_geometry(geometry, source_crs: str, target_crs: str):
    from shapely import transform

    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    return transform(geometry, transformer.transform, interleaved=False)


def area_shape(area: Area):
    from shapely.geometry import shape

    geometry = shape(area.geometry)
    if not geometry.is_valid or geometry.is_empty or geometry.area <= 0:
        raise ValueError("AOI is empty, degenerate or self-intersecting")
    # Curved projection edges must not be represented only by the four bbox corners.
    step = 0.001 if CRS.from_user_input(area.crs).is_geographic else 100.0
    return geometry.segmentize(step)


def repaired_area_shape(area: Area, *, repair: bool = False):
    """Return the AOI shape, optionally repairing it with an explicit count.

    Repair is opt-in. Invalid AOIs otherwise fail closed instead of being
    silently altered.
    """

    if not repair:
        return area_shape(area)
    from shapely import make_valid
    from shapely.geometry import shape

    geometry = shape(area.geometry)
    if not geometry.is_valid:
        geometry = make_valid(geometry)
        geometry = _polygonal(geometry)
    if geometry is None or geometry.is_empty or geometry.area <= 0:
        raise ValueError("AOI cannot be repaired into a positive polygon")
    step = 0.001 if CRS.from_user_input(area.crs).is_geographic else 100.0
    return geometry.segmentize(step)


def _polygonal(geometry):
    """Keep only polygonal parts of a possibly mixed repair result."""

    from shapely.ops import unary_union

    if geometry is None:
        return None
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    parts = [
        part
        for part in getattr(geometry, "geoms", [])
        if part.geom_type in {"Polygon", "MultiPolygon"} and not part.is_empty
    ]
    return unary_union(parts) if parts else None


def equal_area_crs(area: Area) -> str:
    crs = validate_body_crs(area.body, area.crs)
    geometry = area_shape(area)
    point = transform_geometry(geometry.centroid, area.crs, crs.geodetic_crs).coords[0]
    radius = crs.ellipsoid.semi_major_metre
    earth = "+datum=WGS84" if area.body == "Earth" else f"+R={radius}"
    return f"+proj=laea +lat_0={point[1]} +lon_0={point[0]} {earth} +units=m +type=crs"


@dataclass(frozen=True)
class UnitOverlap:
    feature_id: str
    properties: dict
    fraction: float
    area_m2: float


@dataclass(frozen=True)
class OverlayResult:
    rows: list[UnitOverlap]
    coverage: float
    metric_crs: str
    aoi_area_m2: float
    invalid_count: int
    repaired_count: int
    repair_mode: str


class VectorIndex:
    """Full source-unit spatial index; invalid source geometries are counted, never silently repaired."""

    def __init__(self, path, layer=None, repair_invalid: bool = False):
        import fiona
        from shapely import make_valid
        from shapely.geometry import shape
        from shapely.strtree import STRtree

        self.features, self.geometries = [], []
        self.invalid_count = 0
        self.repaired_count = 0
        self.repair_mode = "invalid_excluded" if not repair_invalid else "make_valid_explicit"
        with fiona.open(path, layer=layer) as source:
            self.crs = source.crs_wkt
            for feature in source:
                if feature.geometry is None:
                    self.invalid_count += 1
                    continue
                geometry = shape(feature.geometry)
                if not geometry.is_valid or geometry.is_empty:
                    if repair_invalid:
                        repaired = _polygonal(make_valid(geometry))
                        if repaired is not None and repaired.is_valid and repaired.area > 0:
                            self.repaired_count += 1
                            geometry = repaired
                        else:
                            self.invalid_count += 1
                            continue
                    else:
                        self.invalid_count += 1
                        continue
                self.features.append((feature.id, dict(feature.properties)))
                self.geometries.append(geometry)
        self.tree = STRtree(self.geometries)

    def measure(self, area: Area, *, repair_aoi: bool = False) -> OverlayResult:
        """Intersect the AOI with source polygons and measure equal-area fractions."""

        from shapely.ops import unary_union

        validate_body_crs(area.body, self.crs)
        source_shape = repaired_area_shape(area, repair=repair_aoi)
        aoi_source = transform_geometry(source_shape, area.crs, self.crs)
        metric_crs = equal_area_crs(area)
        aoi_metric = transform_geometry(source_shape, area.crs, metric_crs)
        rows: list[UnitOverlap] = []
        intersections = []
        for index in self.tree.query(aoi_source, predicate="intersects"):
            clipped = self.geometries[index].intersection(aoi_source)
            if clipped.is_empty:
                continue
            metric = transform_geometry(clipped.segmentize(100), self.crs, metric_crs)
            if metric.is_empty or metric.area <= 0:
                continue
            fraction = metric.area / aoi_metric.area
            if fraction <= 1e-12:
                continue
            if fraction > 1.0001:
                raise ValueError("overlay area exceeds AOI; check CRS and seam handling")
            intersections.append(metric)
            fid, props = self.features[index]
            rows.append(UnitOverlap(fid, props, min(1.0, fraction), metric.area))
        coverage = unary_union(intersections).area / aoi_metric.area if intersections else 0.0
        return OverlayResult(
            rows=rows,
            coverage=min(1.0, coverage),
            metric_crs=metric_crs,
            aoi_area_m2=aoi_metric.area,
            invalid_count=self.invalid_count,
            repaired_count=self.repaired_count,
            repair_mode=self.repair_mode,
        )

    def overlay(self, area: Area) -> tuple[list[tuple[str, dict, float]], float, str]:
        result = self.measure(area)
        return (
            [(row.feature_id, row.properties, row.fraction) for row in result.rows],
            result.coverage,
            result.metric_crs,
        )
