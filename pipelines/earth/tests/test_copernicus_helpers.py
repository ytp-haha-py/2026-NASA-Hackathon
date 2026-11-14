"""Unit tests for Copernicus tile/URL, slug, bbox and retry helpers (offline)."""

from __future__ import annotations

import urllib.error
from pathlib import Path

import numpy as np
import pytest
import rasterio
from copernicus_glo90 import (
    CANDIDATES,
    Candidate,
    IngestError,
    TileNotFoundError,
    bbox_contains,
    find_candidate,
    head_remote_object,
    location_id_for_slug,
    normalise_slug,
    read_source_crop,
    round_bbox,
    source_window_bounds,
    tile_bounds_from_id,
    tile_id_for_lonlat,
    tile_id_from_key,
    tile_key,
    tile_url,
    utm_epsg,
    validate_tile_id,
)
from rasterio.transform import from_origin

EXPECTED_TILES = {
    "atacama-desert": "Copernicus_DSM_COG_30_S24_00_W069_00_DEM",
    "death-valley": "Copernicus_DSM_COG_30_N36_00_W118_00_DEM",
    "lanzarote": "Copernicus_DSM_COG_30_N29_00_W014_00_DEM",
    "devon-island": "Copernicus_DSM_COG_30_N75_00_W090_00_DEM",
    "rio-tinto": "Copernicus_DSM_COG_30_N37_00_W007_00_DEM",
}


@pytest.mark.parametrize("candidate", CANDIDATES, ids=lambda c: c.slug)
def test_candidate_tile_ids_match_verified_bucket_keys(candidate: Candidate):
    assert candidate.tile_id == EXPECTED_TILES[candidate.slug]
    assert candidate.location_id == f"earth-{candidate.slug}"


@pytest.mark.parametrize(
    ("lon", "lat", "tile_id"),
    [
        (-68.4, -23.5, "Copernicus_DSM_COG_30_S24_00_W069_00_DEM"),
        (-117.1, 36.25, "Copernicus_DSM_COG_30_N36_00_W118_00_DEM"),
        (-0.5, -0.5, "Copernicus_DSM_COG_30_S01_00_W001_00_DEM"),
        (0.5, 0.5, "Copernicus_DSM_COG_30_N00_00_E000_00_DEM"),
        (179.5, 83.5, "Copernicus_DSM_COG_30_N83_00_E179_00_DEM"),
        (-179.5, -89.5, "Copernicus_DSM_COG_30_S90_00_W180_00_DEM"),
    ],
)
def test_tile_id_uses_floor_and_fixed_width(lon: float, lat: float, tile_id: str):
    assert tile_id_for_lonlat(lon, lat) == tile_id


@pytest.mark.parametrize(("lon", "lat"), [(180.0, 0.0), (0.0, 90.0), (0.0, -90.0)])
def test_tile_id_rejects_out_of_range_coordinates(lon: float, lat: float):
    with pytest.raises(IngestError):
        tile_id_for_lonlat(lon, lat)


def test_tile_key_and_url_pattern():
    tile = "Copernicus_DSM_COG_30_N29_00_W014_00_DEM"
    key = "Copernicus_DSM_COG_30_N29_00_W014_00_DEM/Copernicus_DSM_COG_30_N29_00_W014_00_DEM.tif"
    assert tile_key(tile) == key
    assert tile_url(tile) == f"https://copernicus-dem-90m.s3.amazonaws.com/{key}"
    assert tile_id_from_key(key) == tile
    assert tile_id_from_key(tile) == tile


@pytest.mark.parametrize(
    "key",
    [
        "Copernicus_DSM_COG_10_N29_00_W014_00_DEM/Copernicus_DSM_COG_10_N29_00_W014_00_DEM.tif",
        "Copernicus_DSM_COG_30_N29_00_W014_00_DEM/other.tif",
        "Copernicus_DSM_COG_30_N29_00_W14_00_DEM",
        "random.tif",
    ],
)
def test_tile_id_from_key_rejects_malformed_keys(key: str):
    with pytest.raises(IngestError):
        tile_id_from_key(key)


def test_tile_bounds_are_one_degree_cells():
    bounds = tile_bounds_from_id("Copernicus_DSM_COG_30_S24_00_W069_00_DEM")
    assert bounds == {"west": -69.0, "south": -24.0, "east": -68.0, "north": -23.0}


def test_validate_tile_id_rejects_wrong_resolution_code():
    with pytest.raises(IngestError):
        validate_tile_id("Copernicus_DSM_COG_10_N29_00_W014_00_DEM")


def test_slug_helpers():
    assert normalise_slug("Rio Tinto, Spain") == "rio-tinto-spain"
    assert location_id_for_slug("atacama-desert") == "earth-atacama-desert"
    with pytest.raises(IngestError):
        location_id_for_slug("Atacama Desert")
    with pytest.raises(IngestError):
        location_id_for_slug("")


def test_find_candidate_accepts_slug_or_location_id():
    assert find_candidate("atacama-desert").slug == "atacama-desert"
    assert find_candidate("earth-atacama-desert").slug == "atacama-desert"
    with pytest.raises(IngestError):
        find_candidate("mars-olympus-mons")


@pytest.mark.parametrize(
    ("slug", "epsg"),
    [
        ("atacama-desert", 32719),
        ("death-valley", 32611),
        ("lanzarote", 32628),
        ("devon-island", 32616),
        ("rio-tinto", 32629),
    ],
)
def test_utm_epsg_matches_shared_extractor_selection(slug: str, epsg: int):
    candidate = find_candidate(slug)
    assert utm_epsg(candidate.lon, candidate.lat) == epsg


def test_source_window_contains_centre_and_stays_inside_tile():
    for candidate in CANDIDATES:
        bounds = source_window_bounds(candidate.lon, candidate.lat)
        assert bbox_contains(bounds, candidate.lon, candidate.lat)
        tile = tile_bounds_from_id(candidate.tile_id)
        assert bounds["west"] >= tile["west"]
        assert bounds["east"] <= tile["east"]
        assert bounds["south"] >= tile["south"]
        assert bounds["north"] <= tile["north"]
        assert bounds["east"] - bounds["west"] < 0.2
        assert bounds["north"] - bounds["south"] < 0.2


def test_bbox_helpers_round_and_contain():
    bbox = round_bbox({"west": -1.123456789, "south": -2.2, "east": 3.3, "north": 4.4})
    assert bbox["west"] == -1.12345679
    assert bbox_contains(bbox, 0.0, 0.0)
    assert bbox_contains(bbox, 3.3000000001, 0.0, tolerance=1e-9)
    assert not bbox_contains(bbox, 3.4, 0.0)


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status = status
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_head_remote_object_retries_transient_errors():
    calls: list[str] = []
    sleeps: list[float] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) < 3:
            raise urllib.error.URLError("temporary")
        return FakeResponse(200, {"Content-Length": "4168525", "ETag": '"abc"'})

    remote = head_remote_object(
        "https://example.invalid/tile.tif",
        max_retries=2,
        opener=opener,
        sleeper=sleeps.append,
    )
    assert len(calls) == 3
    assert sleeps == [1.5, 3.0]
    assert remote.content_length == 4168525
    assert remote.etag == '"abc"'


def test_head_remote_object_404_fails_fast():
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    with pytest.raises(TileNotFoundError):
        head_remote_object("https://example.invalid/missing.tif", opener=opener)
    assert len(calls) == 1


def _write_geographic_dem(path: Path, crs: str = "EPSG:4326") -> Path:
    values = np.arange(40 * 40, dtype="float32").reshape(40, 40) * 0.5 + 100.0
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(19.95, 10.05, 1.0 / 1200.0, 1.0 / 1200.0),
    ) as dst:
        dst.write(values, 1)
    return path


def test_read_source_crop_rejects_non_wgs84_source(tmp_path: Path):
    source = tmp_path / "mercator.tif"
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(0.0, 1000.0, 90.0, 90.0),
    ) as dst:
        dst.write(np.zeros((10, 10), dtype="float32"), 1)
    candidate = Candidate("test-only-crs", "test only", 10.0, 20.0)
    with pytest.raises(IngestError, match="EPSG:4326"):
        read_source_crop(source, candidate, tmp_path / "out.tif")


def test_read_source_crop_rejects_wrong_resolution(tmp_path: Path):
    source = tmp_path / "coarse.tif"
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(19.95, 10.05, 0.001, 0.001),
    ) as dst:
        dst.write(np.ones((40, 40), dtype="float32"), 1)
    candidate = Candidate("test-only-res", "test only", 10.0, 20.0)
    with pytest.raises(IngestError, match="3 arc-second"):
        read_source_crop(source, candidate, tmp_path / "out.tif")
