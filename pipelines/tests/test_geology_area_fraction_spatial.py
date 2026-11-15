"""Optional native-vector area-fraction checks. Every geometry is synthetic TEST ONLY."""

import json

import pytest

pytest.importorskip("shapely", reason="Optional geology ingestion GIS environment")
pytest.importorskip("fiona", reason="Optional geology ingestion GIS environment")

from analog_pipelines.geology.areas import polygon
from analog_pipelines.geology.models import Area
from analog_pipelines.geology.spatial import VectorIndex, repaired_area_shape


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


def vector(tmp_path, geometries, crs="EPSG:4326", **kwargs):
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
    return VectorIndex(path, **kwargs)


def test_measure_reports_fraction_area_and_equal_area_crs(tmp_path):
    index = vector(
        tmp_path,
        [polygon(0, 0, 0.0025, 0.01), polygon(0.0025, 0, 0.01, 0.01)],
    )
    result = index.measure(area())
    assert [row.fraction for row in result.rows] == pytest.approx([0.25, 0.75], abs=1e-4)
    assert result.coverage == pytest.approx(1, abs=1e-4)
    assert "+proj=laea" in result.metric_crs
    assert result.aoi_area_m2 > 0
    assert sum(row.area_m2 for row in result.rows) == pytest.approx(result.aoi_area_m2, rel=1e-3)
    for row in result.rows:
        assert row.area_m2 == pytest.approx(row.fraction * result.aoi_area_m2, rel=1e-9)
    assert result.repair_mode == "invalid_excluded"
    assert result.invalid_count == 0 and result.repaired_count == 0


def test_overlapping_units_do_not_double_count_coverage(tmp_path):
    index = vector(
        tmp_path,
        [polygon(0, 0, 0.0075, 0.01), polygon(0.0025, 0, 0.01, 0.01)],
    )
    result = index.measure(area())
    assert sum(row.fraction for row in result.rows) > 1
    assert result.coverage == pytest.approx(1, abs=1e-4)


def test_no_overlap_is_empty_not_fabricated_zero(tmp_path):
    index = vector(tmp_path, [polygon(1, 1, 1.01, 1.01)])
    result = index.measure(area())
    assert result.rows == []
    assert result.coverage == 0
    assert result.aoi_area_m2 > 0


def test_invalid_source_geometry_excluded_by_default_and_repair_is_opt_in(tmp_path):
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]]}
    square = polygon(0, 0, 0.01, 0.01)
    excluded = vector(tmp_path, [bowtie, square])
    assert excluded.invalid_count == 1 and excluded.repaired_count == 0
    assert excluded.measure(area()).coverage == pytest.approx(1, abs=1e-4)

    repaired = vector(tmp_path, [bowtie, square], repair_invalid=True)
    assert repaired.invalid_count == 0
    assert repaired.repaired_count == 1
    assert repaired.repair_mode == "make_valid_explicit"
    result = repaired.measure(area())
    assert result.coverage == pytest.approx(1, abs=1e-4)
    assert result.repaired_count == 1


def test_invalid_aoi_fails_closed_unless_repair_is_requested():
    bowtie = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [0.01, 0.01], [0, 0.01], [0.01, 0], [0, 0]]],
    }
    invalid = area(bowtie)
    with pytest.raises(ValueError, match="self-intersecting"):
        repaired_area_shape(invalid)
    repaired = repaired_area_shape(invalid, repair=True)
    assert repaired.is_valid and repaired.area > 0


def test_measure_uses_native_lunar_frame_and_rejects_earth_wgs84(tmp_path):
    lunar_crs = "+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +R=1737400 +units=m +type=crs"
    index = vector(tmp_path, [polygon(-3747.5, -7332.5, -1767.5, -5352.5)], crs=lunar_crs)
    lunar = area(polygon(-3747.5, -7332.5, -1767.5, -5352.5), body="Moon", crs=lunar_crs)
    result = index.measure(lunar)
    assert result.coverage == pytest.approx(1, abs=1e-4)
    assert "1737400" in result.metric_crs
    assert "+datum=WGS84" not in result.metric_crs
    with pytest.raises(ValueError, match="does not belong"):
        index.measure(area(body="Moon", crs="EPSG:4326"))


def test_measure_rejects_cross_body_source_index(tmp_path):
    index = vector(tmp_path, [polygon(0, 0, 0.01, 0.01)], crs="EPSG:4326")
    with pytest.raises(ValueError, match="does not belong"):
        index.measure(area(body="Mars", crs="+proj=longlat +R=3396190 +type=crs"))
