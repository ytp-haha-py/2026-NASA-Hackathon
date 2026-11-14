"""Native-scale polygon overlay. Optional GIS imports stay off the offline read path."""

from __future__ import annotations

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


def equal_area_crs(area: Area) -> str:
    crs = validate_body_crs(area.body, area.crs)
    geometry = area_shape(area)
    point = transform_geometry(geometry.centroid, area.crs, crs.geodetic_crs).coords[0]
    radius = crs.ellipsoid.semi_major_metre
    earth = "+datum=WGS84" if area.body == "Earth" else f"+R={radius}"
    return f"+proj=laea +lat_0={point[1]} +lon_0={point[0]} {earth} +units=m +type=crs"


class VectorIndex:
    """Full source-unit spatial index; invalid source geometries are counted, never repaired silently."""

    def __init__(self, path, layer=None):
        import fiona
        from shapely.geometry import shape
        from shapely.strtree import STRtree

        self.features, self.geometries = [], []
        self.invalid_count = 0
        with fiona.open(path, layer=layer) as source:
            self.crs = source.crs_wkt
            for feature in source:
                if feature.geometry is None:
                    self.invalid_count += 1
                    continue
                geometry = shape(feature.geometry)
                if not geometry.is_valid or geometry.is_empty:
                    self.invalid_count += 1
                    continue
                self.features.append((feature.id, dict(feature.properties)))
                self.geometries.append(geometry)
        self.tree = STRtree(self.geometries)

    def overlay(self, area: Area) -> tuple[list[tuple[str, dict, float]], float, str]:
        from shapely.ops import unary_union

        validate_body_crs(area.body, self.crs)
        aoi_source = transform_geometry(area_shape(area), area.crs, self.crs)
        metric_crs = equal_area_crs(area)
        aoi_metric = transform_geometry(area_shape(area), area.crs, metric_crs)
        intersections = []
        rows = []
        for index in self.tree.query(aoi_source, predicate="intersects"):
            clipped = self.geometries[index].intersection(aoi_source)
            if clipped.is_empty:
                continue
            metric = transform_geometry(clipped.segmentize(100), self.crs, metric_crs)
            fraction = metric.area / aoi_metric.area
            if fraction <= 1e-12:
                continue
            if fraction > 1.0001:
                raise ValueError("overlay area exceeds AOI; check CRS and seam handling")
            intersections.append(metric)
            fid, props = self.features[index]
            rows.append((fid, props, min(1.0, fraction)))
        coverage = unary_union(intersections).area / aoi_metric.area if intersections else 0.0
        return rows, min(1.0, coverage), metric_crs
