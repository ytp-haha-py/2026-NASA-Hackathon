"""Unit tests for the WorldCover tile/window/class-fraction helpers (offline)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from analog_api.domain.features import CONTEXT_METHOD_VERSION, WORLDCOVER_CLASSES
from rasterio.transform import Affine
from worldcover import (
    BARE_SPARSE_FEATURE_ID,
    CLASS_CODES,
    DEGREES_PER_PIXEL,
    MIN_VALID_FRACTION,
    NATIVE_RESOLUTION_M,
    Aoi,
    IngestError,
    build_context_observations,
    compute_class_fractions,
    default_cache_dir,
    envelope_pixel_grid,
    envelope_transform,
    find_aoi,
    load_earth_aois,
    tile_bounds_from_id,
    tile_id_for_lonlat,
    tile_url,
    tiles_for_bbox,
    window_checksum,
)


@pytest.fixture(scope="module")
def earth_aois() -> list[Aoi]:
    return load_earth_aois(
        Path(__file__).resolve().parents[3]
        / "pipelines"
        / "earth"
        / "manifests"
        / "copernicus_glo90_earth.json"
    )


def synthetic_transform() -> Affine:
    return Affine(0.001, 0.0, 10.0, 0.0, -0.001, 20.0)


def synthetic_bbox() -> dict[str, float]:
    """Exactly covers a 20 x 20 array created with :func:`synthetic_transform`."""
    return {"west": 10.0, "south": 19.98, "east": 10.02, "north": 20.0}


def test_tile_ids_for_the_five_indexed_aois(earth_aois: list[Aoi]):
    expected = {
        "earth-atacama-desert": "S24W069",
        "earth-death-valley": "N36W120",
        "earth-lanzarote": "N27W015",
        "earth-devon-island": "N75W090",
        "earth-rio-tinto": "N36W009",
    }
    assert {aoi.location_id: aoi.tile_ids for aoi in earth_aois} == {
        location_id: (tile_id,) for location_id, tile_id in expected.items()
    }


def test_tile_url_matches_verified_public_key():
    assert tile_url("N36W120") == (
        "https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
        "v200/2021/map/ESA_WorldCover_10m_2021_v200_N36W120_Map.tif"
    )


def test_tile_bounds_are_three_degrees():
    assert tile_bounds_from_id("S24W069") == {
        "west": -69.0,
        "south": -24.0,
        "east": -66.0,
        "north": -21.0,
    }
    assert tile_bounds_from_id("N36W120")["east"] - tile_bounds_from_id("N36W120")["west"] == 3.0


def test_tile_id_for_lonlat_uses_floor_multiples_of_three():
    assert tile_id_for_lonlat(-68.4, -23.5) == "S24W069"
    assert tile_id_for_lonlat(-117.1, 36.25) == "N36W120"
    assert tile_id_for_lonlat(-13.63, 29.04) == "N27W015"
    assert tile_id_for_lonlat(-89.85, 75.4) == "N75W090"
    assert tile_id_for_lonlat(-6.55, 37.69) == "N36W009"


def test_tile_id_rejects_out_of_range_coordinates():
    with pytest.raises(IngestError):
        tile_id_for_lonlat(-181.0, 0.0)
    with pytest.raises(IngestError):
        tile_id_for_lonlat(0.0, 91.0)


def test_tiles_for_bbox_can_span_four_tiles():
    tiles = tiles_for_bbox({"west": 2.5, "south": -0.5, "east": 3.5, "north": 0.5})
    assert set(tiles) == {"S03E000", "S03E003", "N00E000", "N00E003"}


def test_tiles_for_bbox_does_not_add_tile_at_exact_northern_edge():
    tiles = tiles_for_bbox({"west": 0.0, "south": 0.0, "east": 1.0, "north": 3.0})
    assert tiles == ("N00E000",)


def test_envelope_grid_covers_bbox(earth_aois: list[Aoi]):
    for aoi in earth_aois:
        col0, row0, width, height = envelope_pixel_grid(aoi.bbox)
        transform = envelope_transform(col0, row0)
        west_edge = transform.c
        east_edge = transform.c + width * transform.a
        north_edge = transform.f
        south_edge = transform.f + height * transform.e
        assert west_edge <= aoi.bbox["west"] + DEGREES_PER_PIXEL
        assert east_edge >= aoi.bbox["east"] - DEGREES_PER_PIXEL
        assert north_edge >= aoi.bbox["north"] - DEGREES_PER_PIXEL
        assert south_edge <= aoi.bbox["south"] + DEGREES_PER_PIXEL


def _known_and_nodata_array() -> np.ndarray:
    values = np.full((20, 20), 60, dtype=np.uint8)  # 400 pixels
    values[0:5, :] = 30  # 100 grassland
    values[5:7, :] = 50  # 40 built-up
    values[7:9, :] = 0  # 40 nodata
    values[9, :] = 42  # 20 unknown codes
    return values


def test_known_proportions_and_nodata_denominator():
    fractions = compute_class_fractions(
        _known_and_nodata_array(), synthetic_transform(), synthetic_bbox()
    )
    assert fractions.total_pixels == 400
    assert fractions.valid_pixels == 360  # 40 nodata excluded from the denominator
    assert fractions.valid_fraction == pytest.approx(0.9)
    assert fractions.counts[60] == 200
    assert fractions.fractions[60] == pytest.approx(200 / 360)
    assert fractions.fractions[30] == pytest.approx(100 / 360)
    assert fractions.fractions[50] == pytest.approx(40 / 360)
    # Unknown code 42 stays in the denominator but is never mapped to a class.
    assert fractions.unknown_codes == (42,)
    assert fractions.fraction_sum == pytest.approx(340 / 360)
    assert fractions.quality == "suspect"


def test_fractions_sum_to_one_when_every_valid_pixel_is_known():
    values = np.full((20, 20), 60, dtype=np.uint8)
    values[0:10, :] = 30
    values[10:15, :] = 20
    values[15:18, :] = 0
    fractions = compute_class_fractions(values, synthetic_transform(), synthetic_bbox())
    assert fractions.valid_pixels == 340
    assert sum(fractions.fractions.values()) == pytest.approx(1.0)
    assert fractions.fraction_sum == pytest.approx(1.0)
    assert fractions.quality == "verified"


def test_low_valid_fraction_is_suspect():
    values = np.zeros((20, 20), dtype=np.uint8)
    values[: int(20 * 20 * (MIN_VALID_FRACTION - 0.05)) // 20, :] = 60
    fractions = compute_class_fractions(values, synthetic_transform(), synthetic_bbox())
    assert fractions.valid_fraction < MIN_VALID_FRACTION
    assert fractions.quality == "suspect"


def test_all_nodata_is_suspect_and_does_not_divide_by_zero():
    fractions = compute_class_fractions(
        np.zeros((20, 20), dtype=np.uint8), synthetic_transform(), synthetic_bbox()
    )
    assert fractions.valid_fraction == 0.0
    assert fractions.valid_pixels == 0
    assert all(value == 0.0 for value in fractions.fractions.values())
    assert fractions.quality == "suspect"


def test_pixels_outside_bbox_are_excluded():
    values = np.full((20, 20), 60, dtype=np.uint8)
    values[:, 10:] = 30
    bbox = {"west": 10.0, "south": 19.98, "east": 10.01, "north": 20.0}  # left half
    fractions = compute_class_fractions(values, synthetic_transform(), bbox)
    assert fractions.total_pixels == 200
    assert fractions.counts[60] == 200
    assert fractions.counts[30] == 0


def test_empty_bbox_is_an_error():
    bbox = {"west": 50.0, "south": 50.0, "east": 50.01, "north": 50.01}
    with pytest.raises(IngestError):
        compute_class_fractions(np.zeros((20, 20), dtype=np.uint8), synthetic_transform(), bbox)


def test_context_observations_cover_all_eleven_classes():
    fractions = compute_class_fractions(
        _known_and_nodata_array(), synthetic_transform(), synthetic_bbox()
    )
    context = build_context_observations(fractions)
    assert len(context) == 11
    assert set(context) == {f"landcover_{slug}_fraction" for _, slug, _ in WORLDCOVER_CLASSES}
    assert {item["class_code"] for item in context.values()} == set(CLASS_CODES)
    for feature_id, observation in context.items():
        assert observation["unit"] == "fraction"
        assert observation["method_version"] == CONTEXT_METHOD_VERSION
        assert observation["source_resolution_m"] == NATIVE_RESOLUTION_M
        assert observation["valid_fraction"] == pytest.approx(0.9)
        assert observation["quality"] == "suspect"
        assert observation["provider"].startswith("European Space Agency")
        assert 0.0 <= observation["value"] <= 1.0
        assert observation["class_code"] in CLASS_CODES
        assert feature_id.startswith("landcover_")


def test_bare_sparse_fraction_is_class_60():
    fractions = compute_class_fractions(
        _known_and_nodata_array(), synthetic_transform(), synthetic_bbox()
    )
    context = build_context_observations(fractions)
    assert context[BARE_SPARSE_FEATURE_ID]["class_code"] == 60
    assert context[BARE_SPARSE_FEATURE_ID]["value"] == pytest.approx(round(200 / 360, 6))


def test_checksum_is_deterministic_and_sensitive():
    array = _known_and_nodata_array()
    urls = (tile_url("N36W120"),)
    first = window_checksum(array, urls, synthetic_bbox())
    second = window_checksum(array.copy(), urls, synthetic_bbox())
    assert first == second
    assert first.startswith("sha256:") and len(first) == 71
    changed = array.copy()
    changed[0, 0] = 10
    assert window_checksum(changed, urls, synthetic_bbox()) != first
    assert window_checksum(array, (tile_url("N36W009"),), synthetic_bbox()) != first


def test_find_aoi_accepts_both_id_forms(earth_aois: list[Aoi]):
    assert find_aoi(earth_aois, "atacama-desert").location_id == "earth-atacama-desert"
    assert find_aoi(earth_aois, "earth-atacama-desert").location_id == "earth-atacama-desert"
    with pytest.raises(IngestError):
        find_aoi(earth_aois, "not-a-place")


def test_default_cache_dir_is_gitignored_data_cache():
    assert default_cache_dir().parts[-3:] == ("data", "cache", "worldcover")
