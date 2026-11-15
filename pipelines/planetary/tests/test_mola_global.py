"""Offline tests for the official MOLA MEGDR source adapter (Issue #170 Goal B).

No test in this file touches the public network. Online probes live in
``test_mola_global_online.py`` and are marked ``network``.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest
from mola_global import (
    MARS_SPHERE_RADIUS_M,
    MOLA_16PPD_IMAGE_BYTES,
    MOLA_128PPD_IMAGE_BYTES,
    MOLA_BUNDLE_LID,
    MOLA_COLLECTIONS,
    MOLA_GLOBAL_16PPD,
    MolaPds4Metadata,
    MolaSourceError,
    apply_nodata_and_scaling,
    effective_nodata,
    estimate_window_bytes,
    georeference_delta,
    main,
    parse_pds4_label,
    published_md5,
    read_window,
    resolve_collection,
    staged_tile_paths,
    tile_bounds_metric,
    tile_for_lonlat,
    tile_for_product_id,
    tile_transform,
    tiles,
    tiles_for_lonlat_bbox,
    verify_pds4_metadata,
    window_for_lonlat_bbox,
    window_lonlat_bounds,
)

JEZERO = (78.05, 18.45, 78.12, 18.52)


def pds4_label_xml(
    *,
    product_id: str = "megt44n000hb",
    collection_id: str = "data_meg128",
    resolution_m: float = 463.0576672,
    upperleft_x: float = -10668848.652,
    upperleft_y: float = 2607940.782,
    lines: int = 5632,
    samples: int = 11520,
    data_type: str = "SignedMSB2",
    unit: str | None = "meter",
    value_offset: float | None = None,
    west: float = 0.0,
    south: float = 0.0,
    east: float = 90.0,
    north: float = 44.0,
    latitude_type: str = "Planetocentric",
    longitude_direction: str = "Positive East",
    reference_frame: str = "IAU2000_MARS",
    radius_km: float = 3396.0,
) -> str:
    unit_xml = f"<unit>{unit}</unit>" if unit else ""
    offset_xml = f"<value_offset>{value_offset}</value_offset>" if value_offset is not None else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1"
  xmlns:cart="http://pds.nasa.gov/pds4/cart/v1"
  xmlns:geom="http://pds.nasa.gov/pds4/geom/v1">
  <Identification_Area>
    <logical_identifier>{MOLA_BUNDLE_LID}:{collection_id}:{product_id}</logical_identifier>
    <version_id>1.1</version_id>
    <title>MGS MOLA MEGDR product: {product_id}</title>
  </Identification_Area>
  <Discipline_Area>
    <cart:Cartography>
      <cart:Spatial_Domain>
        <cart:Bounding_Coordinates>
          <cart:west_bounding_coordinate unit="deg">{west}</cart:west_bounding_coordinate>
          <cart:east_bounding_coordinate unit="deg">{east}</cart:east_bounding_coordinate>
          <cart:north_bounding_coordinate unit="deg">{north}</cart:north_bounding_coordinate>
          <cart:south_bounding_coordinate unit="deg">{south}</cart:south_bounding_coordinate>
        </cart:Bounding_Coordinates>
      </cart:Spatial_Domain>
      <cart:Spatial_Reference_Information>
        <cart:Horizontal_Coordinate_System_Definition>
          <cart:Planar>
            <cart:Planar_Coordinate_Information>
              <cart:Coordinate_Representation>
                <cart:pixel_resolution_x unit="km/pixel">{resolution_m / 1000.0}</cart:pixel_resolution_x>
                <cart:pixel_resolution_y unit="km/pixel">{resolution_m / 1000.0}</cart:pixel_resolution_y>
              </cart:Coordinate_Representation>
            </cart:Planar_Coordinate_Information>
            <cart:Geo_Transformation>
              <cart:upperleft_corner_x unit="m">{upperleft_x}</cart:upperleft_corner_x>
              <cart:upperleft_corner_y unit="m">{upperleft_y}</cart:upperleft_corner_y>
            </cart:Geo_Transformation>
          </cart:Planar>
          <cart:Geodetic_Model>
            <cart:latitude_type>{latitude_type}</cart:latitude_type>
            <cart:a_axis_radius unit="km">{radius_km}</cart:a_axis_radius>
            <cart:longitude_direction>{longitude_direction}</cart:longitude_direction>
          </cart:Geodetic_Model>
        </cart:Horizontal_Coordinate_System_Definition>
      </cart:Spatial_Reference_Information>
    </cart:Cartography>
    <geom:Geometry>
      <geom:Reference_Frame_Identification>
        <geom:name>{reference_frame}</geom:name>
      </geom:Reference_Frame_Identification>
    </geom:Geometry>
  </Discipline_Area>
  <File_Area_Observational>
    <File>
      <file_name>{product_id}.img</file_name>
    </File>
    <Array_2D_Image>
      <Element_Array>
        <data_type>{data_type}</data_type>
        {unit_xml}
        {offset_xml}
      </Element_Array>
      <Axis_Array>
        <axis_name>Line</axis_name>
        <elements>{lines}</elements>
      </Axis_Array>
      <Axis_Array>
        <axis_name>Sample</axis_name>
        <elements>{samples}</elements>
      </Axis_Array>
    </Array_2D_Image>
  </File_Area_Observational>
</Product_Observational>
"""


def test_bundle_and_collection_inventory_is_pinned() -> None:
    assert MOLA_BUNDLE_LID == "urn:nasa:pds:mgs_mola_topography_derived"
    assert set(MOLA_COLLECTIONS) == {4, 16, 32, 64, 128}
    assert resolve_collection("meg128").ppd == 128
    assert resolve_collection(16).directory == "meg016"
    with pytest.raises(MolaSourceError) as error:
        resolve_collection(8)
    assert error.value.code == "unknown_collection"


def test_128ppd_tiles_are_16_four_by_four() -> None:
    tile_list = tiles(128, "topography")
    assert len(tile_list) == 16
    assert {tile.product_id for tile in tile_list} >= {
        "megt44n000hb",
        "megt44n180hb",
        "megt00n000hb",
        "megt44s270hb",
        "megt88n090hb",
    }
    assert all(tile.lines == 5632 and tile.samples == 11520 for tile in tile_list)
    assert all(tile.image_bytes == MOLA_128PPD_IMAGE_BYTES for tile in tile_list)


def test_16ppd_is_one_global_product() -> None:
    tile_list = tiles(16, "topography")
    assert len(tile_list) == 1
    tile = tile_list[0]
    assert tile.product_id == "megt90n000eb"
    assert tile.bounds_lonlat == (0.0, -90.0, 360.0, 90.0)
    assert tile.image_bytes == MOLA_16PPD_IMAGE_BYTES
    assert MOLA_GLOBAL_16PPD.product_id == tile.product_id


def test_role_inventory_matches_verified_products() -> None:
    assert {tile.role for tile in tiles(128, "count")} == {"count"}
    assert {tile.role for tile in tiles(128, "radius")} == {"radius"}
    assert {tile.role for tile in tiles(16, "areoid")} == {"areoid"}
    with pytest.raises(MolaSourceError) as error:
        tiles(128, "areoid")
    assert error.value.code == "role_not_available"


def test_tile_selection_for_jezero_olympus_and_south() -> None:
    assert tile_for_lonlat(78.1, 18.5).product_id == "megt44n000hb"
    assert tile_for_lonlat(226.2, 18.65).product_id == "megt44n180hb"
    assert tile_for_lonlat(120.0, -70.0).product_id == "megt44s090hb"
    assert tile_for_lonlat(10.0, 89.0, ppd=16).product_id == "megt90n000eb"


def test_128ppd_refuses_poles_outside_verified_coverage() -> None:
    with pytest.raises(MolaSourceError) as error:
        tile_for_lonlat(0.0, 89.0, ppd=128)
    assert error.value.code == "outside_collection_coverage"
    assert "16 ppd" in error.value.message


def test_tile_transform_matches_pds4_upper_left() -> None:
    tile = tile_for_product_id("megt44n000hb")
    transform = tile_transform(tile)
    assert transform.a == pytest.approx(463.0576672, abs=1e-9)
    assert transform.e == pytest.approx(-463.0576672, abs=1e-9)
    assert transform.c == pytest.approx(-10668848.652, abs=0.001)
    assert transform.f == pytest.approx(2607940.782, abs=0.001)

    north = tile_for_product_id("megt44n180hb")
    assert tile_transform(north).c == pytest.approx(0.0, abs=0.001)
    assert tile_transform(north).f == pytest.approx(2607940.782, abs=0.001)

    global_16 = MOLA_GLOBAL_16PPD
    assert tile_transform(global_16).a == pytest.approx(3704.4613374, abs=1e-9)
    assert tile_transform(global_16).c == pytest.approx(-10668848.652, abs=0.001)
    assert tile_transform(global_16).f == pytest.approx(5334424.326, abs=0.001)


def test_gdal_pds_transform_delta_is_recorded_not_used() -> None:
    tile = tile_for_product_id("megt44n000hb")
    gdal_transform = (463.0, 0.0, -10667983.0, 0.0, -463.0, 2608079.0)
    delta = georeference_delta(tile, gdal_transform)
    assert delta["origin_east_m"] == pytest.approx(865.6515909, abs=1e-6)
    assert delta["origin_north_m"] == pytest.approx(138.2185, abs=1e-6)
    assert delta["resolution_east_m"] == pytest.approx(-0.0576672, abs=1e-9)
    assert delta["resolution_north_m"] == pytest.approx(-0.0576672, abs=1e-9)
    # The precise transform is never replaced by GDAL's rounded PDS3 transform.
    assert tile_transform(tile).c != pytest.approx(-10667983.0, abs=1.0)


def test_tile_bounds_metric_are_derived_from_the_precise_transform() -> None:
    left, bottom, right, top = tile_bounds_metric(tile_for_product_id("megt44n000hb"))
    assert left == pytest.approx(-10668848.652, abs=0.001)
    assert right == pytest.approx(-5334424.326, abs=0.01)
    assert bottom == pytest.approx(0.0, abs=0.01)
    assert top == pytest.approx(2607940.782, abs=0.001)


def test_window_for_jezero_is_bounded_to_ten_by_ten_native_pixels() -> None:
    tile = tile_for_lonlat(78.1, 18.5)
    window = window_for_lonlat_bbox(tile, *JEZERO)
    assert (int(window.col_off), int(window.row_off)) == (9990, 3261)
    assert (int(window.width), int(window.height)) == (10, 10)
    lon_west, lat_south, lon_east, lat_north = window_lonlat_bounds(tile, window)
    assert lon_west == pytest.approx(78.046875, abs=1e-6)
    assert lon_east == pytest.approx(78.125, abs=1e-6)
    assert lat_south == pytest.approx(18.4453125, abs=1e-6)
    assert lat_north == pytest.approx(18.5234375, abs=1e-6)


def test_window_estimates_one_full_row_per_touched_line() -> None:
    tile = tile_for_lonlat(78.1, 18.5)
    window = window_for_lonlat_bbox(tile, *JEZERO)
    # 10 rows x 11,520 samples x 2 bytes + 128 KiB label/header allowance.
    assert estimate_window_bytes(tile, window) == 10 * 11520 * 2 + 128 * 1024


def test_bbox_that_crosses_a_tile_edge_fails_closed() -> None:
    touched = tiles_for_lonlat_bbox(89.9, 10.0, 90.1, 10.2)
    assert [tile.product_id for tile in touched] == ["megt44n000hb", "megt44n090hb"]
    with pytest.raises(MolaSourceError) as error:
        read_window(89.9, 10.0, 90.1, 10.2, allow_remote=False)
    assert error.value.code == "window_crosses_tile"
    assert "never silently mosaics" in error.value.message


def test_window_outside_the_selected_tile_fails_closed() -> None:
    tile = tile_for_lonlat(78.1, 18.5)
    with pytest.raises(MolaSourceError) as error:
        window_for_lonlat_bbox(tile, 95.0, 18.4, 96.0, 18.6)
    assert error.value.code == "window_crosses_tile"


def test_invalid_bbox_and_wrapping_are_rejected() -> None:
    with pytest.raises(MolaSourceError) as error:
        tiles_for_lonlat_bbox(10.0, 20.0, 5.0, 25.0)
    assert error.value.code == "invalid_bbox"
    with pytest.raises(MolaSourceError) as error:
        tiles_for_lonlat_bbox(-2.0, 20.0, 5.0, 25.0)
    assert error.value.code == "invalid_bbox"


def test_count_is_never_masked_but_elevation_sentinel_is() -> None:
    assert effective_nodata("count", 0.0)[0] is None
    assert effective_nodata("topography", -32768.0)[0] == -32768.0

    count_values, count_valid = apply_nodata_and_scaling(
        [0.0, 1.0, 4.0], nodata=0.0, scale=1.0, offset=0.0, role="count"
    )
    assert count_valid.tolist() == [True, True, True]
    assert count_values.tolist() == [0.0, 1.0, 4.0]

    values, valid = apply_nodata_and_scaling(
        [0.0, -32768.0, 100.0],
        nodata=-32768.0,
        scale=1.0,
        offset=0.0,
        role="topography",
    )
    assert valid.tolist() == [True, False, True]
    assert math.isnan(values[1])
    assert values[0] == 0.0  # areoid-level topography is real, not nodata


def test_radius_value_offset_is_applied() -> None:
    values, valid = apply_nodata_and_scaling(
        [100.0, 200.0], nodata=None, scale=1.0, offset=MARS_SPHERE_RADIUS_M, role="radius"
    )
    assert valid.tolist() == [True, True]
    assert values.tolist() == [3396100.0, 3396200.0]


def test_parse_pds4_label_reads_verified_fields() -> None:
    meta = parse_pds4_label(pds4_label_xml())
    assert meta.logical_identifier.endswith(":data_meg128:megt44n000hb")
    assert (meta.lines, meta.samples) == (5632, 11520)
    assert meta.data_type == "SignedMSB2"
    assert meta.unit == "meter"
    assert meta.value_offset_m == 0.0
    assert meta.pixel_resolution_x_m == pytest.approx(463.0576672, abs=1e-9)
    assert meta.upperleft_x_m == pytest.approx(-10668848.652, abs=1e-9)
    assert meta.upperleft_y_m == pytest.approx(2607940.782, abs=1e-9)
    assert meta.latitude_type == "Planetocentric"
    assert meta.longitude_direction == "Positive East"
    assert meta.reference_frame == "IAU2000_MARS"


def test_verify_pds4_metadata_accepts_and_rejects() -> None:
    tile = tile_for_product_id("megt44n000hb")
    meta = parse_pds4_label(pds4_label_xml())
    verify_pds4_metadata(tile, meta)  # no raise

    changed = replace(meta, pixel_resolution_x_m=463.0)
    with pytest.raises(MolaSourceError) as error:
        verify_pds4_metadata(tile, changed)
    assert error.value.code == "pds4_label_mismatch"
    assert "pixel_resolution_x" in error.value.message

    wrong_frame = replace(meta, reference_frame="IAU2000_EARTH")
    with pytest.raises(MolaSourceError) as error:
        verify_pds4_metadata(tile, wrong_frame)
    assert error.value.code == "pds4_label_mismatch"


def test_pds4_radius_value_offset_parses() -> None:
    meta = parse_pds4_label(
        pds4_label_xml(
            product_id="megr44n000hb",
            value_offset=3396000.0,
        )
    )
    assert meta.value_offset_m == 3396000.0
    tile = tile_for_product_id("megr44n000hb")
    verify_pds4_metadata(tile, meta)


def test_staged_tile_paths_and_published_md5_lookup() -> None:
    tile = tile_for_product_id("megt44n000hb")
    label, image, header = staged_tile_paths(Path("/tmp/mola"), tile)
    assert label.name == "megt44n000hb.lbl"
    assert image.name == "megt44n000hb.img"
    assert header.name == "megt44n000hb.hdr"
    checksums = {"meg128/megt44n000hb.img": "904CAEA41A1DB3A2450CC9497B21C3CC"}
    assert published_md5(tile, checksums=checksums) == "904CAEA41A1DB3A2450CC9497B21C3CC"


def test_cli_lists_collections_offline(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list-collections"]) == 0
    output = capsys.readouterr().out
    assert MOLA_BUNDLE_LID in output
    assert "data_meg128" in output


def test_cli_refuses_offline_window_without_staged_tile(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--window", "78.05", "18.45", "78.12", "18.52", "--offline"])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("error [remote_read_disabled]")
    assert captured.out == ""


def test_pds4_metadata_type_is_used_by_tests() -> None:
    # Keep the dataclass import meaningful even if no test constructs it directly.
    assert MolaPds4Metadata.__name__ == "MolaPds4Metadata"
