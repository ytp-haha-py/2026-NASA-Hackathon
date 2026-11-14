"""Optional native-vector overlay checks. Every fixture is synthetic TEST ONLY."""

import json

import pytest

pytest.importorskip("shapely", reason="Optional geology ingestion GIS environment")
pytest.importorskip("fiona", reason="Optional geology ingestion GIS environment")

from analog_pipelines.geology.areas import polygon
from analog_pipelines.geology.models import Area
from analog_pipelines.geology.spatial import VectorIndex, area_shape


def area(geometry=None, body="Earth", crs="EPSG:4326"):
    return Area(
        area_id="synthetic-test-only",
        display_name="SYNTHETIC TEST ONLY",
        body=body,
        crs=crs,
        geometry=geometry or polygon(0, 0, 0.01, 0.01),
        origin="test_aoi",
        geometry_source="Synthetic test fixture only",
    )


def vector(tmp_path, geometries, crs="EPSG:4326"):
    path = tmp_path / "synthetic-test-only.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": crs}},
                "features": [
                    {"type": "Feature", "properties": {"unit": str(i)}, "geometry": g}
                    for i, g in enumerate(geometries)
                ],
            }
        )
    )
    return VectorIndex(path)


def test_holes_reduce_true_overlay_fraction(tmp_path):
    geometry = polygon(0, 0, 0.01, 0.01)
    geometry["coordinates"].append(polygon(0.0025, 0.0025, 0.0075, 0.0075)["coordinates"][0])
    index = vector(tmp_path, [geometry])
    rows, coverage, metric_crs = index.overlay(area())
    assert len(rows) == 1
    assert coverage == pytest.approx(0.75, abs=1e-4)
    assert rows[0][2] == pytest.approx(0.75, abs=1e-4)
    assert "+proj=laea" in metric_crs


def test_overlapping_source_polygons_do_not_double_count_coverage(tmp_path):
    index = vector(tmp_path, [polygon(0, 0, 0.0075, 0.01), polygon(0.0025, 0, 0.01, 0.01)])
    rows, coverage, _ = index.overlay(area())
    assert len(rows) == 2
    assert sum(row[2] for row in rows) > 1
    assert coverage == pytest.approx(1, abs=1e-4)


def test_invalid_source_polygon_is_excluded_and_counted(tmp_path):
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]]}
    index = vector(tmp_path, [bowtie, polygon(0, 0, 0.01, 0.01)])
    assert index.invalid_count == 1
    assert index.overlay(area())[1] == pytest.approx(1, abs=1e-4)
    with pytest.raises(ValueError, match="self-intersecting"):
        area_shape(area(bowtie))


def test_planetary_aoi_cannot_overlay_an_earth_source(tmp_path):
    index = vector(tmp_path, [polygon(0, 0, 0.01, 0.01)])
    with pytest.raises(ValueError, match="does not belong"):
        index.overlay(area(body="Moon", crs="+proj=longlat +R=1737400 +type=crs"))


def test_no_overlap_stays_empty(tmp_path):
    index = vector(tmp_path, [polygon(1, 1, 1.01, 1.01)])
    rows, coverage, _ = index.overlay(area())
    assert rows == [] and coverage == 0
