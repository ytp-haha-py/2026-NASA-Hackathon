#!/usr/bin/env python3
"""Official MOLA MEGDR PDS4 source adapter with bounded window reads (Issue #170).

Verified source (2026-11-15): NASA PDS Geosciences Node PDS4 bundle
``urn:nasa:pds:mgs_mola_topography_derived`` (bundle version 1.0), DOI
``10.17189/1z1b-kv84``. The MEGDR products are Mars body-fixed, planetocentric,
east-positive equirectangular grids on a 3396.0 km sphere. Topography is the
planetary radius minus the GMM3 (mgm1025) areoid.

What this module does and does not do
-------------------------------------
* It indexes the verified global 16 pixel/degree product and the 16 tiled
  128 pixel/degree products, plus the coarser 4/32/64 ppd collections.
* It reads only a bounded window from one product. A 128 ppd tile is
  129,761,280 bytes; the full 128 ppd mosaic is 16 such tiles (~2.08 GB) and is
  never downloaded here. No function in this module downloads a whole tile.
* It opens the original PDS3 label with GDAL's PDS driver for pixel I/O, but it
  **overrides GDAL's transform with the precise PDS4/ENVI georeference**. The
  PDS3 ``MAP_SCALE`` is rounded to three decimals (463 m, 3.705 km, ...), which
  shifts the MEGDR grid by hundreds of metres to kilometres. The PDS Geosciences
  ENVI guide documents this exact trap; the PDS4 labels carry the corrected
  2*pi*R/samples resolution and recomputed upper-left corner.
* It never treats ``0`` as nodata. The ENVI header's ``data ignore value = 0``
  conflicts with valid areoid-level topography (0 m occurs in the real data).
  GDAL's PDS driver reports ``-32768``; that sentinel is masked if and only if it
  actually occurs.

Coordinates are always Mars body-fixed (0..360 east-positive, planetocentric).
Earth ``EPSG:4326``/UTM is never used. The Mars 2020 Jezero CTX mosaic is a
local 20 m product and is not global Mars coverage; it is not read here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.env import Env
from rasterio.transform import Affine, from_origin
from rasterio.windows import Window

# ---------------------------------------------------------------------------
# Verified bundle/product constants (all checked 2026-11-15).
# ---------------------------------------------------------------------------

MOLA_BUNDLE_LID = "urn:nasa:pds:mgs_mola_topography_derived"
MOLA_BUNDLE_VERSION = "1.0"
MOLA_DOI = "10.17189/1z1b-kv84"
MOLA_PROVIDER = "NASA PDS Geosciences Node / MGS MOLA"
MOLA_BUNDLE_BASE = "https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived"
MOLA_BUNDLE_URL = f"{MOLA_BUNDLE_BASE}/mgs_mola_topography_derived_bundle.xml"
MOLA_README_URL = f"{MOLA_BUNDLE_BASE}/readme.txt"
MOLA_CHECKSUM_INDEX_URL = f"{MOLA_BUNDLE_BASE}/urn-nasa-pds-mgs_mola_topography_derived.md5"
MOLA_ENVI_GUIDE_URL = f"{MOLA_BUNDLE_BASE}/document/mola_envi.htm"
MOLA_PDS3_DATASET_ID = "MGS-M-MOLA-5-MEGDR-L3-V1.0"

# Verified HEAD Content-Length values (bytes), not downloads.
MOLA_128PPD_IMAGE_BYTES = 129_761_280
MOLA_16PPD_IMAGE_BYTES = 33_177_600

MARS_SPHERE_RADIUS_M = 3_396_000.0
MARS_REFERENCE_FRAME = "IAU2000_MARS body-fixed"
MARS_LATITUDE_TYPE = "planetocentric"
MARS_LONGITUDE_DIRECTION = "positive_east"
MARS_VERTICAL_REFERENCE = (
    "planetary radius minus the GMM3 (mgm1025) areoid, evaluated to degree and order 50"
)
MOLA_CITATION = (
    "Smith, D. E., et al. (2001), Mars Orbiter Laser Altimeter: Experiment summary "
    "after the first year of global mapping of Mars, J. Geophys. Res. 106(E10), "
    "23689-23722, doi:10.1029/2000JE001364. PDS4 bundle "
    "urn:nasa:pds:mgs_mola_topography_derived (1.0), DOI 10.17189/1z1b-kv84."
)
MOLA_LICENSE_STATUS = (
    "not_verified: the PDS4 bundle, collection, product labels, readme, and ENVI guide "
    "state no licence, copyright, or redistribution terms"
)
MOLA_LICENSE_URL = None

# The PDS3 labels are rounded to 0.463 km / 3.705 km. These are the exact values
# from the PDS4 cart:pixel_resolution and the ENVI headers (2*pi*R / samples).
PDS3_MAP_SCALE_ROUNDING_NOTE = (
    "PDS3 MAP_SCALE is rounded to three decimals; do not use GDAL's PDS transform "
    "for georeferencing. The PDS4 cart:pixel_resolution and upper-left corner are exact."
)

# GDAL's PDS driver reports this sentinel even though no PDS3/PDS4 label declares a
# missing constant. It is masked only when present; 0 is never masked.
MOLA_GDAL_NODATA_SENTINEL = -32768.0

ROLE_PREFIX: dict[str, str] = {
    "topography": "t",
    "count": "c",
    "radius": "r",
    "areoid": "a",
}
ROLE_LABEL: dict[str, str] = {
    "topography": "median observed topography",
    "count": "MOLA observation count per bin",
    "radius": "mean planetary radius (add 3,396,000 m)",
    "areoid": "areoid radius (add 3,396,000 m)",
}
ROLE_UNIT: dict[str, str | None] = {
    "topography": "meter",
    "count": None,
    "radius": "meter",
    "areoid": "meter",
}
ROLE_VALUE_OFFSET: dict[str, float] = {
    "topography": 0.0,
    "count": 0.0,
    "radius": MARS_SPHERE_RADIUS_M,
    "areoid": MARS_SPHERE_RADIUS_M,
}

DEFAULT_MAX_WINDOW_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_WINDOW_PIXELS = 512 * 512
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_CONNECT_TIMEOUT_S = 20.0
MAX_RETRIES = 2
CACHE_VERSION = "mola-window-1.0.0"
USER_AGENT = "planetary-analog-mars/0.1 (MOLA MEGDR bounded reader)"

_METRE_UNITS = {"m", "metre", "meter", "metres", "meters"}
_TILE_EDGE_TOLERANCE_DEG = 1e-9


class MolaSourceError(RuntimeError):
    """A real MOLA access/verification problem. Never replaced with synthetic data."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Product model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MolaCollection:
    """A verified MEGDR data collection and its tiling layout."""

    collection_lid: str
    directory: str
    ppd: int
    lines: int
    samples: int
    pixel_resolution_m: float
    coverage_lat_deg: tuple[float, float]
    layout: str
    product_suffix: str
    roles: tuple[str, ...]

    @property
    def collection_id(self) -> str:
        return self.collection_lid.rsplit(":", 1)[-1]

    @property
    def product_page(self) -> str:
        return f"{MOLA_BUNDLE_BASE}/{self.directory}/"

    def to_payload(self) -> dict[str, Any]:
        return {
            "collection_lid": self.collection_lid,
            "collection_id": self.collection_id,
            "directory": self.directory,
            "ppd": self.ppd,
            "lines": self.lines,
            "samples": self.samples,
            "pixel_resolution_m": self.pixel_resolution_m,
            "coverage_lat_deg": list(self.coverage_lat_deg),
            "layout": self.layout,
            "product_suffix": self.product_suffix,
            "roles": list(self.roles),
            "product_page": self.product_page,
            "global": self.layout == "global",
        }


@dataclass(frozen=True, slots=True)
class MolaTile:
    """One verified MEGDR product (tile or global grid) and one variable role."""

    grid_code: str
    collection_lid: str
    role: str
    ppd: int
    lines: int
    samples: int
    pixel_resolution_m: float
    west_deg: float
    south_deg: float
    east_deg: float
    north_deg: float
    product_suffix: str

    @property
    def product_id(self) -> str:
        return f"meg{ROLE_PREFIX[self.role]}{self.grid_code}{self.product_suffix}"

    @property
    def image_name(self) -> str:
        return f"{self.product_id}.img"

    @property
    def label_name(self) -> str:
        return f"{self.product_id}.lbl"

    @property
    def header_name(self) -> str:
        return f"{self.product_id}.hdr"

    @property
    def directory(self) -> str:
        return self.collection_lid.rsplit(":", 1)[-1].removeprefix("data_")

    @property
    def label_url(self) -> str:
        return f"{MOLA_BUNDLE_BASE}/{self.directory}/{self.label_name}"

    @property
    def image_url(self) -> str:
        return f"{MOLA_BUNDLE_BASE}/{self.directory}/{self.image_name}"

    @property
    def header_url(self) -> str:
        return f"{MOLA_BUNDLE_BASE}/{self.directory}/{self.header_name}"

    @property
    def pds4_label_url(self) -> str:
        return f"{MOLA_BUNDLE_BASE}/{self.directory}/{self.product_id}.xml"

    @property
    def item_size_bytes(self) -> int:
        # 128 ppd observation counts are UnsignedByte; every other verified array is
        # SignedMSB2.
        if self.role == "count" and self.ppd == 128:
            return 1
        return 2

    @property
    def image_bytes(self) -> int:
        return self.lines * self.samples * self.item_size_bytes

    @property
    def dtype_label(self) -> str:
        return "uint8" if self.item_size_bytes == 1 else "int16 (MSB)"

    @property
    def expected_data_type(self) -> str:
        return "UnsignedByte" if self.role == "count" and self.ppd == 128 else "SignedMSB2"

    @property
    def unit(self) -> str | None:
        return ROLE_UNIT[self.role]

    @property
    def value_offset_m(self) -> float:
        return ROLE_VALUE_OFFSET[self.role]

    @property
    def bounds_lonlat(self) -> tuple[float, float, float, float]:
        return (self.west_deg, self.south_deg, self.east_deg, self.north_deg)

    @property
    def center_lonlat(self) -> tuple[float, float]:
        return (
            (self.west_deg + self.east_deg) / 2.0,
            (self.south_deg + self.north_deg) / 2.0,
        )

    @property
    def estimate_note(self) -> str:
        return (
            "PDS3 row-striped layout: a window read touches whole rows of "
            f"{self.samples * self.item_size_bytes:,} bytes"
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "collection_lid": self.collection_lid,
            "role": self.role,
            "role_label": ROLE_LABEL[self.role],
            "ppd": self.ppd,
            "pixel_resolution_m": self.pixel_resolution_m,
            "shape": [self.lines, self.samples],
            "bounds_lonlat": list(self.bounds_lonlat),
            "image_name": self.image_name,
            "label_url": self.label_url,
            "image_url": self.image_url,
            "pds4_label_url": self.pds4_label_url,
            "header_url": self.header_url,
            "image_bytes": self.image_bytes,
            "dtype": self.dtype_label,
            "unit": self.unit,
            "value_offset_m": self.value_offset_m,
        }


def _make_collection(
    *,
    ppd: int,
    lines: int,
    samples: int,
    resolution_m: float,
    coverage: tuple[float, float],
    layout: str,
    suffix: str,
) -> MolaCollection:
    directory = f"meg{ppd:03d}"
    roles: tuple[str, ...] = ("topography", "count", "radius")
    if ppd in (4, 16):
        roles = ("topography", "count", "radius", "areoid")
    return MolaCollection(
        collection_lid=f"{MOLA_BUNDLE_LID}:data_meg{ppd:03d}",
        directory=directory,
        ppd=ppd,
        lines=lines,
        samples=samples,
        pixel_resolution_m=resolution_m,
        coverage_lat_deg=coverage,
        layout=layout,
        product_suffix=suffix,
        roles=roles,
    )


MOLA_COLLECTIONS: dict[int, MolaCollection] = {
    4: _make_collection(
        ppd=4,
        lines=720,
        samples=1440,
        resolution_m=14_817.845349,
        coverage=(-90.0, 90.0),
        layout="global",
        suffix="cb",
    ),
    16: _make_collection(
        ppd=16,
        lines=2880,
        samples=5760,
        resolution_m=3_704.4613374,
        coverage=(-90.0, 90.0),
        layout="global",
        suffix="eb",
    ),
    32: _make_collection(
        ppd=32,
        lines=5760,
        samples=11520,
        resolution_m=1_852.2306687,
        coverage=(-90.0, 90.0),
        layout="global",
        suffix="fb",
    ),
    64: _make_collection(
        ppd=64,
        lines=5760,
        samples=11520,
        resolution_m=926.11533434,
        coverage=(-90.0, 90.0),
        layout="hemisphere_quadrant",
        suffix="gb",
    ),
    128: _make_collection(
        ppd=128,
        lines=5632,
        samples=11520,
        resolution_m=463.0576672,
        coverage=(-88.0, 88.0),
        layout="band_quadrant",
        suffix="hb",
    ),
}

# (grid_code, west_deg, south_deg, east_deg, north_deg)
_LAYOUTS: dict[int, tuple[tuple[str, float, float, float, float], ...]] = {
    4: (("90n000", 0.0, -90.0, 360.0, 90.0),),
    16: (("90n000", 0.0, -90.0, 360.0, 90.0),),
    32: (("90n000", 0.0, -90.0, 360.0, 90.0),),
    64: (
        ("90n000", 0.0, 0.0, 180.0, 90.0),
        ("90n180", 180.0, 0.0, 360.0, 90.0),
        ("00n000", 0.0, -90.0, 180.0, 0.0),
        ("00n180", 180.0, -90.0, 360.0, 0.0),
    ),
    128: (
        ("88n000", 0.0, 44.0, 90.0, 88.0),
        ("88n090", 90.0, 44.0, 180.0, 88.0),
        ("88n180", 180.0, 44.0, 270.0, 88.0),
        ("88n270", 270.0, 44.0, 360.0, 88.0),
        ("44n000", 0.0, 0.0, 90.0, 44.0),
        ("44n090", 90.0, 0.0, 180.0, 44.0),
        ("44n180", 180.0, 0.0, 270.0, 44.0),
        ("44n270", 270.0, 0.0, 360.0, 44.0),
        ("00n000", 0.0, -44.0, 90.0, 0.0),
        ("00n090", 90.0, -44.0, 180.0, 0.0),
        ("00n180", 180.0, -44.0, 270.0, 0.0),
        ("00n270", 270.0, -44.0, 360.0, 0.0),
        ("44s000", 0.0, -88.0, 90.0, -44.0),
        ("44s090", 90.0, -88.0, 180.0, -44.0),
        ("44s180", 180.0, -88.0, 270.0, -44.0),
        ("44s270", 270.0, -88.0, 360.0, -44.0),
    ),
}


def resolve_collection(ppd: int | str) -> MolaCollection:
    """Resolve ``128``, ``"128"``, ``"meg128"`` or a full collection LID."""
    if isinstance(ppd, str):
        text = ppd.strip().lower()
        if text.startswith("meg"):
            text = text.removeprefix("meg")
        if text.startswith("data_meg"):
            text = text.removeprefix("data_meg")
        try:
            key = int(text)
        except ValueError as exc:
            raise MolaSourceError(
                "unknown_collection", f"{ppd!r} is not a MEGDR pixels/degree collection"
            ) from exc
    else:
        key = int(ppd)
    if key not in MOLA_COLLECTIONS:
        supported = ", ".join(str(value) for value in sorted(MOLA_COLLECTIONS))
        raise MolaSourceError(
            "unknown_collection", f"{key} ppd is not verified; choose one of {supported}"
        )
    return MOLA_COLLECTIONS[key]


def _normalise_role(role: str) -> str:
    text = role.strip().lower()
    aliases = {
        "topo": "topography",
        "elevation": "topography",
        "t": "topography",
        "c": "count",
        "r": "radius",
        "a": "areoid",
    }
    text = aliases.get(text, text)
    if text not in ROLE_PREFIX:
        supported = ", ".join(ROLE_PREFIX)
        raise MolaSourceError("unknown_role", f"{role!r} is not verified; choose {supported}")
    return text


def tiles(ppd: int | str = 128, role: str = "topography") -> tuple[MolaTile, ...]:
    """Return the verified products for one collection and variable role."""
    collection = resolve_collection(ppd)
    normalised = _normalise_role(role)
    if normalised not in collection.roles:
        raise MolaSourceError(
            "role_not_available",
            f"{normalised!r} is not part of {collection.collection_id}; "
            f"available: {', '.join(collection.roles)}",
        )
    return tuple(
        MolaTile(
            grid_code=code,
            collection_lid=collection.collection_lid,
            role=normalised,
            ppd=collection.ppd,
            lines=collection.lines,
            samples=collection.samples,
            pixel_resolution_m=collection.pixel_resolution_m,
            west_deg=west,
            south_deg=south,
            east_deg=east,
            north_deg=north,
            product_suffix=collection.product_suffix,
        )
        for code, west, south, east, north in _LAYOUTS[collection.ppd]
    )


def _tile_index() -> dict[str, MolaTile]:
    index: dict[str, MolaTile] = {}
    for collection in MOLA_COLLECTIONS.values():
        for role in collection.roles:
            for tile in tiles(collection.ppd, role):
                index[tile.product_id] = tile
    return index


MOLA_TILE_INDEX: dict[str, MolaTile] = _tile_index()
MOLA_TOPOGRAPHY_TILES: tuple[MolaTile, ...] = tiles(128, "topography")
MOLA_GLOBAL_16PPD: MolaTile = tiles(16, "topography")[0]


def tile_for_product_id(product_id: str) -> MolaTile:
    text = product_id.strip().lower()
    if text not in MOLA_TILE_INDEX:
        raise MolaSourceError(
            "unknown_product",
            f"{product_id!r} is not a verified MEGDR product id "
            "(for example megt44n000hb or megt90n000eb)",
        )
    return MOLA_TILE_INDEX[text]


def tiles_for_collection(ppd: int | str = 128) -> tuple[MolaTile, ...]:
    collection = resolve_collection(ppd)
    return tuple(
        tile
        for tile in MOLA_TILE_INDEX.values()
        if tile.collection_lid == collection.collection_lid
    )


def _contains_lonlat(tile: MolaTile, lon: float, lat: float) -> bool:
    lon = lon % 360.0
    return (
        tile.west_deg - _TILE_EDGE_TOLERANCE_DEG <= lon <= tile.east_deg + _TILE_EDGE_TOLERANCE_DEG
        and tile.south_deg - _TILE_EDGE_TOLERANCE_DEG
        <= lat
        <= tile.north_deg + _TILE_EDGE_TOLERANCE_DEG
    )


def tile_for_lonlat(
    lon: float,
    lat: float,
    *,
    ppd: int | str = 128,
    role: str = "topography",
) -> MolaTile:
    """Select the single verified tile covering a body-fixed lon/lat point."""
    collection = resolve_collection(ppd)
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise MolaSourceError("invalid_lonlat", "lon and lat must be finite")
    normalized_lon = lon % 360.0
    south, north = collection.coverage_lat_deg
    if not (south - _TILE_EDGE_TOLERANCE_DEG <= lat <= north + _TILE_EDGE_TOLERANCE_DEG):
        raise MolaSourceError(
            "outside_collection_coverage",
            f"lat {lat} is outside the {collection.ppd} ppd coverage "
            f"[{south}, {north}] deg; use the global 16 ppd collection for the poles "
            "or do not claim Mars coverage there",
        )
    for tile in tiles(collection.ppd, role):
        if _contains_lonlat(tile, normalized_lon, lat):
            return tile
    raise MolaSourceError(
        "no_tile_for_lonlat",
        f"no {collection.ppd} ppd tile covers lon {normalized_lon}, lat {lat}",
    )


def tiles_for_lonlat_bbox(
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    ppd: int | str = 128,
    role: str = "topography",
) -> tuple[MolaTile, ...]:
    """Return every tile touched by a lon/lat bbox (small AOIs normally touch one)."""
    collection = resolve_collection(ppd)
    _validate_lonlat_bbox(west, south, east, north)
    if south < collection.coverage_lat_deg[0] or north > collection.coverage_lat_deg[1]:
        raise MolaSourceError(
            "outside_collection_coverage",
            f"bbox lat [{south}, {north}] is outside the {collection.ppd} ppd coverage "
            f"{collection.coverage_lat_deg}; use the global 16 ppd collection for the poles",
        )
    selected: list[MolaTile] = []
    for tile in tiles(collection.ppd, role):
        if (
            tile.west_deg < east
            and tile.east_deg > west
            and tile.south_deg < north
            and tile.north_deg > south
        ):
            selected.append(tile)
    if not selected:
        raise MolaSourceError("no_tile_for_bbox", "no verified tile intersects the bbox")
    return tuple(selected)


# ---------------------------------------------------------------------------
# Precise georeference (PDS4 / ENVI), never the rounded PDS3 MAP_SCALE
# ---------------------------------------------------------------------------


def metres_per_degree(radius_m: float = MARS_SPHERE_RADIUS_M) -> float:
    return math.pi * float(radius_m) / 180.0


def tile_transform(tile: MolaTile) -> Affine:
    """Exact MEGDR affine from PDS4 cart:Geo_Transformation.

    Simple cylindrical, central meridian 180 deg, sphere R = 3396.0 km. The
    upper-left corner is at the tile's west/north edge. This matches the PDS4
    ``upperleft_corner_x/y`` and the ENVI ``map info`` reference pixel to
    0.001 m, and deliberately differs from GDAL's PDS-driver transform built
    from the rounded PDS3 ``MAP_SCALE``.
    """
    scale = metres_per_degree()
    west_m = (tile.west_deg - 180.0) * scale
    north_m = tile.north_deg * scale
    return from_origin(west_m, north_m, tile.pixel_resolution_m, tile.pixel_resolution_m)


def tile_bounds_metric(tile: MolaTile) -> tuple[float, float, float, float]:
    transform = tile_transform(tile)
    left, top = transform.c, transform.f
    right = left + tile.samples * tile.pixel_resolution_m
    bottom = top - tile.lines * tile.pixel_resolution_m
    return (left, bottom, right, top)


def georeference_delta(
    tile: MolaTile,
    gdal_transform: Affine | Sequence[float] | None,
) -> dict[str, float]:
    """Difference between GDAL's PDS transform and the precise PDS4 transform."""
    exact = tile_transform(tile)
    if gdal_transform is None:
        return {
            "origin_east_m": 0.0,
            "origin_north_m": 0.0,
            "resolution_east_m": 0.0,
            "resolution_north_m": 0.0,
        }
    values = tuple(float(value) for value in tuple(gdal_transform)[:6])
    return {
        "origin_east_m": float(values[2] - exact.c),
        "origin_north_m": float(values[5] - exact.f),
        "resolution_east_m": float(abs(values[0]) - abs(exact.a)),
        "resolution_north_m": float(abs(values[4]) - abs(exact.e)),
    }


def _validate_lonlat_bbox(west: float, south: float, east: float, north: float) -> None:
    values = (west, south, east, north)
    if not all(math.isfinite(value) for value in values):
        raise MolaSourceError("invalid_bbox", "bbox values must be finite")
    if not (0.0 <= west < east <= 360.0):
        raise MolaSourceError(
            "invalid_bbox",
            f"bbox lon [{west}, {east}] must satisfy 0 <= west < east <= 360 "
            "(Mars body-fixed, east-positive, non-wrapping). Split a 0/360 crossing.",
        )
    if not (-90.0 <= south < north <= 90.0):
        raise MolaSourceError(
            "invalid_bbox", f"bbox lat [{south}, {north}] must satisfy -90 <= south < north <= 90"
        )


def window_for_lonlat_bbox(
    tile: MolaTile,
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    pad_pixels: int = 0,
) -> Window:
    """Convert a body-fixed lon/lat bbox inside one tile to a native pixel window."""
    _validate_lonlat_bbox(west, south, east, north)
    if (
        west < tile.west_deg - _TILE_EDGE_TOLERANCE_DEG
        or east > tile.east_deg + _TILE_EDGE_TOLERANCE_DEG
        or south < tile.south_deg - _TILE_EDGE_TOLERANCE_DEG
        or north > tile.north_deg + _TILE_EDGE_TOLERANCE_DEG
    ):
        raise MolaSourceError(
            "window_crosses_tile",
            f"bbox [{west}, {south}, {east}, {north}] is not inside tile "
            f"{tile.product_id} bounds {tile.bounds_lonlat}; read each touched tile "
            "separately (the adapter never silently mosaics or clips across tiles)",
        )
    # Include every pixel whose cell intersects the bbox. Do not rely on
    # from_bounds().round_lengths(), which can round a small AOI down to zero.
    col0 = math.floor((west - tile.west_deg) * tile.ppd)
    col1 = math.ceil((east - tile.west_deg) * tile.ppd)
    row0 = math.floor((tile.north_deg - north) * tile.ppd)
    row1 = math.ceil((tile.north_deg - south) * tile.ppd)
    col_off = max(0, min(int(col0) - pad_pixels, tile.samples - 1))
    row_off = max(0, min(int(row0) - pad_pixels, tile.lines - 1))
    width = min(max(1, int(col1) - int(col0) + 2 * pad_pixels), tile.samples - col_off)
    height = min(max(1, int(row1) - int(row0) + 2 * pad_pixels), tile.lines - row_off)
    return Window(col_off, row_off, width, height)


def window_metric_bounds(tile: MolaTile, window: Window) -> tuple[float, float, float, float]:
    transform = tile_transform(tile)
    left = transform.c + window.col_off * transform.a
    top = transform.f + window.row_off * transform.e
    right = left + window.width * transform.a
    bottom = top + window.height * transform.e
    return (left, bottom, right, top)


def window_lonlat_bounds(tile: MolaTile, window: Window) -> tuple[float, float, float, float]:
    left, bottom, right, top = window_metric_bounds(tile, window)
    scale = metres_per_degree()
    return (
        left / scale + 180.0,
        bottom / scale,
        right / scale + 180.0,
        top / scale,
    )


def estimate_window_bytes(tile: MolaTile, window: Window) -> int:
    """Conservative upper bound on raster bytes touched by a PDS3 window read.

    The PDS3 product is a striped BSQ image with one row per block, so a window
    read transfers every touched row in full. This is a bound, not a measured
    HTTP byte count.
    """
    if window.width <= 0 or window.height <= 0:
        raise MolaSourceError("invalid_window", "window width and height must be positive")
    row_bytes = tile.samples * tile.item_size_bytes
    return int(window.height * row_bytes + 128 * 1024)


# ---------------------------------------------------------------------------
# PDS4 label inspection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MolaPds4Metadata:
    logical_identifier: str
    version_id: str
    title: str
    file_name: str
    lines: int
    samples: int
    data_type: str
    unit: str | None
    value_offset_m: float
    pixel_resolution_x_m: float
    pixel_resolution_y_m: float
    upperleft_x_m: float
    upperleft_y_m: float
    west_deg: float
    south_deg: float
    east_deg: float
    north_deg: float
    a_axis_radius_km: float
    latitude_type: str
    longitude_direction: str
    reference_frame: str
    raw: str = ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def _text(elem: ET.Element, name: str) -> str | None:
    found = _first(elem, name)
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


def _reference_frame(root: ET.Element) -> str:
    frame = _first(root, "Reference_Frame_Identification")
    if frame is None:
        return ""
    return _text(frame, "name") or ""


def _float(elem: ET.Element, name: str, *, default: float | None = None) -> float:
    text = _text(elem, name)
    if text is None:
        if default is None:
            raise MolaSourceError("pds4_missing_field", f"PDS4 label has no <{name}>")
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise MolaSourceError(
            "pds4_invalid_field", f"PDS4 <{name}> is not numeric: {text!r}"
        ) from exc


def _int(elem: ET.Element, name: str) -> int:
    return int(round(_float(elem, name)))


def parse_pds4_label(text: str) -> MolaPds4Metadata:
    """Parse the verified MEGDR PDS4 product label fields."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise MolaSourceError("pds4_parse_failed", f"PDS4 XML is not parseable: {exc}") from exc

    identification = _first(root, "Identification_Area")
    if identification is None:
        raise MolaSourceError("pds4_missing_field", "PDS4 label has no Identification_Area")
    array = _first(root, "Array_2D_Image")
    if array is None:
        raise MolaSourceError("pds4_missing_field", "PDS4 label has no Array_2D_Image")

    axes: dict[str, int] = {}
    for axis in array.iter():
        if _local_name(axis.tag) != "Axis_Array":
            continue
        axis_name = (_text(axis, "axis_name") or "").lower()
        elements = _text(axis, "elements")
        if axis_name and elements:
            axes[axis_name] = int(elements)
    if "line" not in axes or "sample" not in axes:
        raise MolaSourceError(
            "pds4_missing_field", "PDS4 Array_2D_Image has no Line/Sample axis lengths"
        )

    element_array = _first(array, "Element_Array")
    if element_array is None:
        raise MolaSourceError("pds4_missing_field", "PDS4 label has no Element_Array")
    data_type = _text(element_array, "data_type")
    if data_type is None:
        raise MolaSourceError("pds4_missing_field", "PDS4 Element_Array has no data_type")
    unit = _text(element_array, "unit")
    value_offset = _float(element_array, "value_offset", default=0.0)

    file_area = _first(root, "File_Area_Observational")
    file_name = _text(file_area, "file_name") if file_area is not None else None
    if file_name is None:
        raise MolaSourceError("pds4_missing_field", "PDS4 label has no observational file_name")

    logical_identifier = _text(identification, "logical_identifier")
    version_id = _text(identification, "version_id")
    title = _text(identification, "title")
    if not logical_identifier or not version_id or not title:
        raise MolaSourceError(
            "pds4_missing_field", "PDS4 Identification_Area lacks lid/version/title"
        )

    return MolaPds4Metadata(
        logical_identifier=logical_identifier,
        version_id=version_id,
        title=title,
        file_name=file_name,
        lines=axes["line"],
        samples=axes["sample"],
        data_type=data_type,
        unit=unit,
        value_offset_m=value_offset,
        pixel_resolution_x_m=_float(root, "pixel_resolution_x") * 1000.0,
        pixel_resolution_y_m=_float(root, "pixel_resolution_y") * 1000.0,
        upperleft_x_m=_float(root, "upperleft_corner_x"),
        upperleft_y_m=_float(root, "upperleft_corner_y"),
        west_deg=_float(root, "west_bounding_coordinate"),
        south_deg=_float(root, "south_bounding_coordinate"),
        east_deg=_float(root, "east_bounding_coordinate"),
        north_deg=_float(root, "north_bounding_coordinate"),
        a_axis_radius_km=_float(root, "a_axis_radius"),
        latitude_type=_text(root, "latitude_type") or "",
        longitude_direction=_text(root, "longitude_direction") or "",
        reference_frame=_reference_frame(root),
        raw=text,
    )


def verify_pds4_metadata(tile: MolaTile, meta: MolaPds4Metadata) -> None:
    """Fail closed when a fetched PDS4 label stops matching the pinned adapter."""
    exact = tile_transform(tile)
    problems: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    check(
        meta.logical_identifier.lower() == f"{tile.collection_lid}:{tile.product_id}".lower(),
        f"logical_identifier {meta.logical_identifier!r} does not match "
        f"{tile.collection_lid}:{tile.product_id}",
    )
    check(meta.file_name.lower() == tile.image_name.lower(), f"file_name {meta.file_name!r}")
    check((meta.lines, meta.samples) == (tile.lines, tile.samples), "array shape mismatch")
    check(meta.data_type == tile.expected_data_type, f"data_type {meta.data_type!r}")
    check(meta.unit == tile.unit, f"unit {meta.unit!r} != {tile.unit!r}")
    check(
        math.isclose(meta.value_offset_m, tile.value_offset_m, abs_tol=1e-9),
        f"value_offset {meta.value_offset_m} != {tile.value_offset_m}",
    )
    check(
        math.isclose(meta.pixel_resolution_x_m, tile.pixel_resolution_m, rel_tol=1e-12),
        f"pixel_resolution_x {meta.pixel_resolution_x_m}",
    )
    check(
        math.isclose(meta.pixel_resolution_y_m, tile.pixel_resolution_m, rel_tol=1e-12),
        f"pixel_resolution_y {meta.pixel_resolution_y_m}",
    )
    check(
        math.isclose(meta.upperleft_x_m, exact.c, abs_tol=0.01), f"upperleft_x {meta.upperleft_x_m}"
    )
    check(
        math.isclose(meta.upperleft_y_m, exact.f, abs_tol=0.01), f"upperleft_y {meta.upperleft_y_m}"
    )
    check(
        (meta.west_deg, meta.south_deg, meta.east_deg, meta.north_deg)
        == (tile.west_deg, tile.south_deg, tile.east_deg, tile.north_deg),
        "bounding coordinates mismatch",
    )
    check(math.isclose(meta.a_axis_radius_km, 3396.0, abs_tol=1e-9), "a_axis_radius")
    check(meta.latitude_type.lower() == "planetocentric", "latitude_type")
    check(meta.longitude_direction.lower() == "positive east", "longitude_direction")
    check(meta.reference_frame == "IAU2000_MARS", f"reference_frame {meta.reference_frame!r}")
    if problems:
        raise MolaSourceError(
            "pds4_label_mismatch",
            f"{tile.product_id}: " + "; ".join(problems) + ". Refusing to read a changed product.",
        )


# ---------------------------------------------------------------------------
# HTTP access
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MolaRemoteProbe:
    product_id: str
    label_url: str
    image_url: str
    label_status: int
    image_status: int
    image_bytes: int | None
    accept_ranges: bool
    range_verified: bool
    etag: str | None
    last_modified: str | None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Any:
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    request = urllib.request.Request(url, method=method, headers=merged)
    return opener(request, timeout=timeout)


def _range_probe(url: str, *, timeout: float, opener: Callable[..., Any]) -> tuple[int, bool]:
    with _request(url, headers={"Range": "bytes=0-0"}, timeout=timeout, opener=opener) as response:
        status = int(getattr(response, "status", 200))
        # Do not read a 200 body: the server ignored Range and may send the whole tile.
        return status, status == 206


def probe_remote(
    tile: MolaTile,
    *,
    timeout: float = 30.0,
    max_retries: int = MAX_RETRIES,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> MolaRemoteProbe:
    """HEAD the label and image and verify byte-range support with a 1-byte GET."""
    if max_retries < 0 or max_retries > MAX_RETRIES:
        raise MolaSourceError("invalid_retries", f"retries must be 0..{MAX_RETRIES}")
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            label_status = 0
            image_status = 0
            image_bytes: int | None = None
            accept_ranges = False
            etag: str | None = None
            last_modified: str | None = None
            with _request(
                tile.label_url, method="HEAD", timeout=timeout, opener=opener
            ) as response:
                label_status = int(getattr(response, "status", 200))
            with _request(
                tile.image_url, method="HEAD", timeout=timeout, opener=opener
            ) as response:
                image_status = int(getattr(response, "status", 200))
                length = response.headers.get("Content-Length")
                image_bytes = int(length) if length else None
                accept_ranges = response.headers.get("Accept-Ranges", "").lower() == "bytes"
                etag = response.headers.get("ETag")
                last_modified = response.headers.get("Last-Modified")
            range_status, range_ok = _range_probe(tile.image_url, timeout=timeout, opener=opener)
            if range_status == 206:
                image_status = 206
            return MolaRemoteProbe(
                product_id=tile.product_id,
                label_url=tile.label_url,
                image_url=tile.image_url,
                label_status=label_status,
                image_status=image_status,
                image_bytes=image_bytes,
                accept_ranges=accept_ranges,
                range_verified=range_ok,
                etag=etag,
                last_modified=last_modified,
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise MolaSourceError(
                    "source_not_found",
                    f"HTTP {exc.code} for {tile.image_url}; the verified PDS4 product may "
                    "have moved. Re-check the bundle before substituting a URL.",
                ) from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < max_retries:
            sleeper(1.5 * (attempt + 1))
    raise MolaSourceError(
        "source_unreachable",
        f"cannot reach {tile.image_url} after {max_retries + 1} attempt(s): {last_error}",
    )


def fetch_text(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> str:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with _request(url, timeout=timeout, opener=opener) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise MolaSourceError("source_not_found", f"HTTP {exc.code} for {url}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < max_retries:
            sleeper(1.5 * (attempt + 1))
    raise MolaSourceError(
        "source_unreachable",
        f"cannot fetch {url} after {max_retries + 1} attempt(s): {last_error}",
    )


def fetch_pds4_label(
    tile: MolaTile,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> MolaPds4Metadata:
    return parse_pds4_label(fetch_text(tile.pds4_label_url, timeout=timeout, opener=opener))


def inspect_remote_product(
    tile: MolaTile,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> MolaPds4Metadata:
    """Fetch and verify one PDS4 product label against the pinned adapter."""
    meta = fetch_pds4_label(tile, timeout=timeout, opener=opener)
    verify_pds4_metadata(tile, meta)
    return meta


def fetch_checksum_index(
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, str]:
    """Return ``{"meg128/megt44n000hb.img": "MD5", ...}`` from the bundle index."""
    text = fetch_text(MOLA_CHECKSUM_INDEX_URL, timeout=timeout, opener=opener)
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        digest, path = parts
        checksums[path.replace("\\", "/").lstrip("/").lower()] = digest.upper()
    if not checksums:
        raise MolaSourceError(
            "checksum_index_empty", f"{MOLA_CHECKSUM_INDEX_URL} contained no checksum lines"
        )
    return checksums


def published_md5(tile: MolaTile, *, checksums: Mapping[str, str] | None = None) -> str | None:
    index = checksums if checksums is not None else fetch_checksum_index()
    return index.get(f"{tile.directory}/{tile.image_name}".lower())


# ---------------------------------------------------------------------------
# Local staging (optional; no downloader in this module)
# ---------------------------------------------------------------------------


def staged_tile_paths(local_tile_dir: Path | str, tile: MolaTile) -> tuple[Path, Path, Path]:
    base = Path(local_tile_dir).expanduser()
    return (
        base / tile.label_name,
        base / tile.image_name,
        base / tile.header_name,
    )


def verify_staged_tile(
    tile: MolaTile,
    local_tile_dir: Path | str,
    *,
    checksums: Mapping[str, str] | None = None,
) -> str:
    """Verify a manually staged tile's size and published MD5. Reads 129 MB locally."""
    label, image, _header = staged_tile_paths(local_tile_dir, tile)
    if not label.is_file() or not image.is_file():
        raise MolaSourceError("staged_tile_missing", f"expected {label} and {image} to exist")
    size = image.stat().st_size
    if size != tile.image_bytes:
        raise MolaSourceError(
            "staged_tile_size_mismatch",
            f"{image} is {size:,} bytes, expected {tile.image_bytes:,}",
        )
    digest = hashlib.md5()  # noqa: S324 - official PDS checksum is MD5, not a security control
    with image.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    observed = digest.hexdigest().upper()
    expected = published_md5(tile, checksums=checksums)
    if expected is not None and observed != expected:
        raise MolaSourceError(
            "staged_tile_checksum_mismatch",
            f"{image} MD5 {observed} does not match published {expected}",
        )
    return observed


def _source_reference(
    tile: MolaTile,
    *,
    local_tile_dir: Path | str | None,
    allow_remote: bool,
) -> tuple[str, Literal["local", "remote"]]:
    if local_tile_dir is not None:
        label, image, _header = staged_tile_paths(local_tile_dir, tile)
        if label.is_file():
            if not image.is_file():
                raise MolaSourceError("staged_tile_missing", f"{label} exists but {image} does not")
            size = image.stat().st_size
            if size != tile.image_bytes:
                raise MolaSourceError(
                    "staged_tile_size_mismatch",
                    f"{image} is {size:,} bytes, expected {tile.image_bytes:,}; "
                    "refusing to read a truncated tile",
                )
            return str(label), "local"
    if not allow_remote:
        raise MolaSourceError(
            "remote_read_disabled",
            "no staged tile was found and remote reads are disabled; pass "
            "allow_remote=True for a bounded /vsicurl window",
        )
    return f"/vsicurl/{tile.label_url}", "remote"


# ---------------------------------------------------------------------------
# Bounded raster read
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MolaRasterMetadata:
    product_id: str
    source: str
    source_kind: str
    driver: str
    width: int
    height: int
    dtype: str
    driver_nodata: float | None
    nodata: float | None
    nodata_policy: str
    scale: float
    offset: float
    crs_wkt: str
    crs_name: str
    is_projected: bool
    axis_unit: str
    ellipsoid_radius_m: float
    gdal_transform: tuple[float, float, float, float, float, float]
    precise_transform: tuple[float, float, float, float, float, float]
    georeference_delta: dict[str, float]
    native_resolution_m: float
    block_shapes: tuple[tuple[int, int], ...]
    is_tiled: bool
    full_image_bytes: int

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["georeference_delta"] = dict(self.georeference_delta)
        return payload


@dataclass(frozen=True, slots=True)
class MolaWindow:
    tile: MolaTile
    data: np.ndarray
    valid_mask: np.ndarray
    window: Window
    transform: Affine
    crs: CRS
    lon_lat_bounds: tuple[float, float, float, float]
    metric_bounds: tuple[float, float, float, float]
    estimated_read_bytes: int
    source: str
    source_kind: str
    scale: float
    offset: float
    nodata: float | None
    nodata_policy: str
    gdal_transform: tuple[float, float, float, float, float, float]
    georeference_override: bool
    georeference_delta: dict[str, float]
    access_timestamp: str

    @property
    def valid_fraction(self) -> float:
        return float(self.valid_mask.mean())

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.data.shape[0]), int(self.data.shape[1]))

    def as_dict(self) -> dict[str, Any]:
        finite = self.data[self.valid_mask]
        return {
            "product_id": self.tile.product_id,
            "role": self.tile.role,
            "ppd": self.tile.ppd,
            "native_resolution_m": self.tile.pixel_resolution_m,
            "window": [
                int(self.window.col_off),
                int(self.window.row_off),
                int(self.window.width),
                int(self.window.height),
            ],
            "shape": list(self.shape),
            "lon_lat_bounds": list(self.lon_lat_bounds),
            "metric_bounds": list(self.metric_bounds),
            "transform": list(tuple(self.transform)[:6]),
            "crs_wkt": self.crs.to_wkt(),
            "valid_fraction": self.valid_fraction,
            "valid_pixels": int(self.valid_mask.sum()),
            "value_min": float(finite.min()) if finite.size else None,
            "value_max": float(finite.max()) if finite.size else None,
            "value_mean": float(finite.mean()) if finite.size else None,
            "unit": self.tile.unit,
            "scale": self.scale,
            "offset": self.offset,
            "nodata": self.nodata,
            "nodata_policy": self.nodata_policy,
            "estimated_read_bytes": self.estimated_read_bytes,
            "source": self.source,
            "source_kind": self.source_kind,
            "georeference_override": self.georeference_override,
            "georeference_delta": dict(self.georeference_delta),
            "access_timestamp": self.access_timestamp,
            "data_kind": "observed_derived",
        }


def _gdal_env(timeout: float = DEFAULT_TIMEOUT_S) -> Env:
    return Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".lbl,.img,.hdr",
        CPL_VSIL_CURL_USE_HEAD="YES",
        CPL_VSIL_CURL_MAX_RETRY="0",
        GDAL_HTTP_MAX_RETRY="0",
        GDAL_HTTP_MULTIRANGE="SERIAL",
        GDAL_HTTP_MULTIPLEX="NO",
        GDAL_HTTP_TIMEOUT=str(max(1, int(timeout))),
        GDAL_HTTP_CONNECTTIMEOUT=str(int(min(DEFAULT_CONNECT_TIMEOUT_S, timeout))),
        GDAL_HTTP_USERAGENT=USER_AGENT,
        VSI_CACHE="TRUE",
        VSI_CACHE_SIZE="16777216",
        PROJ_IGNORE_CELESTIAL_BODY="YES",
    )


def _scale_offset(src: rasterio.io.DatasetReader) -> tuple[float, float]:
    scales = list(src.scales or [])
    offsets = list(src.offsets or [])
    scale = float(scales[0]) if scales and scales[0] is not None else 1.0
    offset = float(offsets[0]) if offsets and offsets[0] is not None else 0.0
    if not math.isfinite(scale) or scale == 0:
        raise MolaSourceError("invalid_scale", "raster scale is missing/zero")
    if not math.isfinite(offset):
        raise MolaSourceError("invalid_offset", "raster offset is not finite")
    return scale, offset


def _metadata_from_dataset(
    src: rasterio.io.DatasetReader, tile: MolaTile, source: str, source_kind: str
) -> MolaRasterMetadata:
    if src.crs is None:
        raise MolaSourceError("missing_crs", f"{tile.product_id}: source raster has no CRS")
    crs = CRS.from_user_input(src.crs)
    if not crs.is_projected:
        raise MolaSourceError(
            "geographic_crs", f"{tile.product_id}: source CRS is geographic, expected metres"
        )
    unit = crs.axis_info[0].unit_name.lower() if crs.axis_info else ""
    if unit not in _METRE_UNITS:
        raise MolaSourceError(
            "non_metric_crs", f"{tile.product_id}: projected unit {unit!r}, expected metres"
        )
    radius = float(crs.ellipsoid.semi_major_metre)
    if not math.isclose(radius, MARS_SPHERE_RADIUS_M, abs_tol=5_000.0):
        raise MolaSourceError(
            "not_a_mars_crs",
            f"{tile.product_id}: CRS radius {radius:,.1f} m is not the MOLA 3,396,000 m "
            "Mars sphere; refusing to label a non-Mars raster as MOLA",
        )
    if (int(src.width), int(src.height)) != (tile.samples, tile.lines):
        raise MolaSourceError(
            "shape_mismatch",
            f"{tile.product_id}: source is {src.width}x{src.height}, expected "
            f"{tile.samples}x{tile.lines}",
        )
    driver_nodata = float(src.nodata) if src.nodata is not None else None
    nodata, nodata_policy = effective_nodata(tile.role, driver_nodata)
    scale, offset = _scale_offset(src)
    exact = tile_transform(tile)
    gdal_transform = tuple(float(value) for value in tuple(src.transform)[:6])
    return MolaRasterMetadata(
        product_id=tile.product_id,
        source=source,
        source_kind=source_kind,
        driver=src.driver,
        width=int(src.width),
        height=int(src.height),
        dtype=src.dtypes[0],
        driver_nodata=driver_nodata,
        nodata=nodata,
        nodata_policy=nodata_policy,
        scale=scale,
        offset=offset,
        crs_wkt=crs.to_wkt(),
        crs_name=crs.name,
        is_projected=bool(crs.is_projected),
        axis_unit=unit,
        ellipsoid_radius_m=radius,
        gdal_transform=gdal_transform,  # type: ignore[arg-type]
        precise_transform=tuple(float(value) for value in tuple(exact)[:6]),  # type: ignore[arg-type]
        georeference_delta=georeference_delta(tile, gdal_transform),
        native_resolution_m=tile.pixel_resolution_m,
        block_shapes=tuple((int(shape[0]), int(shape[1])) for shape in src.block_shapes),
        is_tiled=bool(src.is_tiled),
        full_image_bytes=tile.image_bytes,
    )


def inspect_source(
    tile: MolaTile,
    *,
    local_tile_dir: Path | str | None = None,
    allow_remote: bool = True,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> MolaRasterMetadata:
    """Inspect one product without reading an analysis window."""
    source, kind = _source_reference(tile, local_tile_dir=local_tile_dir, allow_remote=allow_remote)
    if kind == "remote":
        probe = probe_remote(
            tile,
            timeout=min(timeout, 60.0),
            max_retries=max_retries,
            opener=opener,
            sleeper=sleeper,
        )
        if not probe.range_verified:
            raise MolaSourceError(
                "range_read_unsupported",
                f"{tile.image_url}: the server did not return HTTP 206 for a 1-byte range "
                "request; refusing to open an object that could be streamed in full",
            )
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with _gdal_env(timeout), rasterio.open(source) as src:
                return _metadata_from_dataset(src, tile, source, kind)
        except MolaSourceError:
            raise
        except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                sleeper(1.5 * (attempt + 1))
    raise MolaSourceError(
        "source_open_failed",
        f"cannot open {source} after {max_retries + 1} attempt(s): {last_error}",
    )


def effective_nodata(role: str, driver_nodata: float | None) -> tuple[float | None, str]:
    """Return the masking rule for one MEGDR role.

    GDAL reports ``-32768`` for signed MEGDR arrays and ``0`` for the 128 ppd
    UnsignedByte observation-count array. Neither is declared in the PDS3/PDS4
    labels, and both are unsafe to apply blindly:

    * ``0`` is a valid MOLA elevation (areoid level) and a valid observation count
      (no MOLA shot in the bin). The ENVI header's ``data ignore value = 0`` is not
      a PDS4 missing constant.
    * ``-32768`` is outside the published topography range (-8208..21249 m) and is
      masked only for the elevation products if it occurs.
    """
    del driver_nodata  # retained only for provenance in the metadata record
    if role == "count":
        return None, "counts: 0 is a valid count (no MOLA shot); no value masked"
    return (
        MOLA_GDAL_NODATA_SENTINEL,
        "elevation: mask -32768 only; 0 m is valid areoid-level topography",
    )


def apply_nodata_and_scaling(
    raw: np.ndarray,
    *,
    nodata: float | None,
    scale: float,
    offset: float,
    role: str = "topography",
) -> tuple[np.ndarray, np.ndarray]:
    """Mask only a real sentinel and apply scale/offset. ``0`` is never masked."""
    mask_value, _policy = effective_nodata(role, nodata)
    data = np.asarray(raw, dtype="float64")
    valid = np.isfinite(data)
    if mask_value is not None:
        valid &= data != mask_value
    values = data * float(scale) + float(offset)
    values = np.where(valid, values, np.nan)
    return values, valid


def read_window(
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    ppd: int | str = 128,
    role: str = "topography",
    local_tile_dir: Path | str | None = None,
    allow_remote: bool = True,
    max_window_bytes: int = DEFAULT_MAX_WINDOW_BYTES,
    max_window_pixels: int = DEFAULT_MAX_WINDOW_PIXELS,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> MolaWindow:
    """Read one bounded window from one verified MOLA MEGDR product.

    Fails closed if the bbox crosses a tile edge, exceeds the byte/pixel budget, or
    the server cannot do byte ranges. It never downloads a full tile or the global
    mosaic.
    """
    if max_window_bytes <= 0 or max_window_pixels <= 0:
        raise MolaSourceError("invalid_budget", "window byte/pixel budgets must be positive")
    touched = tiles_for_lonlat_bbox(west, south, east, north, ppd=ppd, role=role)
    if len(touched) != 1:
        raise MolaSourceError(
            "window_crosses_tile",
            "bbox touches "
            + ", ".join(tile.product_id for tile in touched)
            + "; read each tile separately or use the global 16 ppd collection. "
            "This adapter never silently mosaics across tiles.",
        )
    tile = touched[0]
    window = window_for_lonlat_bbox(tile, west, south, east, north)
    pixels = int(window.width * window.height)
    if pixels > max_window_pixels:
        raise MolaSourceError(
            "window_too_large",
            f"{window.width}x{window.height} = {pixels:,} pixels exceeds the "
            f"{max_window_pixels:,}-pixel budget",
        )
    estimate = estimate_window_bytes(tile, window)
    if estimate > max_window_bytes:
        raise MolaSourceError(
            "window_too_large",
            f"estimated read for {window.width}x{window.height} is {estimate:,} bytes, "
            f"above the {max_window_bytes:,}-byte budget. Reduce the AOI or raise the "
            "budget explicitly; a full tile is never read.",
        )

    source, kind = _source_reference(tile, local_tile_dir=local_tile_dir, allow_remote=allow_remote)
    if kind == "remote":
        probe = probe_remote(
            tile,
            timeout=min(timeout, 60.0),
            max_retries=max_retries,
            opener=opener,
            sleeper=sleeper,
        )
        if not probe.range_verified:
            raise MolaSourceError(
                "range_read_unsupported",
                f"{tile.image_url}: no verified HTTP byte-range support; refusing a full "
                f"{tile.image_bytes:,}-byte tile download",
            )
        if probe.image_bytes is not None and probe.image_bytes != tile.image_bytes:
            raise MolaSourceError(
                "source_size_mismatch",
                f"{tile.image_url} is {probe.image_bytes:,} bytes, expected {tile.image_bytes:,}",
            )

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with _gdal_env(timeout), rasterio.open(source) as src:
                metadata = _metadata_from_dataset(src, tile, source, kind)
                # Read unmasked: GDAL's PDS driver reports 0 as nodata for the
                # UnsignedByte observation-count array, but 0 is a real count.
                # apply_nodata_and_scaling applies the verified per-role policy.
                raw = src.read(1, window=window, out_dtype="float64")
                data = np.asarray(raw)
                values, valid = apply_nodata_and_scaling(
                    data,
                    nodata=metadata.driver_nodata,
                    scale=metadata.scale,
                    offset=metadata.offset,
                    role=tile.role,
                )
                crs = CRS.from_user_input(src.crs)
                transform = tile_transform(tile)
                if np.isfinite(values).sum() == 0:
                    raise MolaSourceError(
                        "no_valid_elevation",
                        f"{tile.product_id}: the requested window has no finite values",
                    )
                return MolaWindow(
                    tile=tile,
                    data=values.astype("float64"),
                    valid_mask=valid,
                    window=window,
                    transform=transform,
                    crs=crs,
                    lon_lat_bounds=window_lonlat_bounds(tile, window),
                    metric_bounds=window_metric_bounds(tile, window),
                    estimated_read_bytes=estimate,
                    source=source,
                    source_kind=kind,
                    scale=metadata.scale,
                    offset=metadata.offset,
                    nodata=metadata.nodata,
                    nodata_policy=metadata.nodata_policy,
                    gdal_transform=metadata.gdal_transform,
                    georeference_override=True,
                    georeference_delta=metadata.georeference_delta,
                    access_timestamp=datetime.now(UTC)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                )
        except MolaSourceError:
            raise
        except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                sleeper(1.5 * (attempt + 1))
    raise MolaSourceError(
        "window_read_failed",
        f"{tile.product_id}: bounded read failed after {max_retries + 1} attempt(s): {last_error}",
    )


# ---------------------------------------------------------------------------
# Optional small-window output (never a full tile)
# ---------------------------------------------------------------------------


def write_window_geotiff(window: MolaWindow, path: Path | str) -> Path:
    """Write the bounded window as a small deflated GeoTIFF plus JSON sidecar."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": window.shape[0],
        "width": window.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": window.crs,
        "transform": window.transform,
        "nodata": float("nan"),
        "compress": "deflate",
        "predictor": 2,
        "tiled": False,
    }
    with rasterio.open(target, "w", **profile) as dst:
        dst.write(window.data.astype("float32"), 1)
    sidecar = target.with_suffix(target.suffix + ".json")
    sidecar.write_text(json.dumps(window.as_dict(), indent=2) + "\n", encoding="utf-8")
    return target


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _collection_payload(collection: MolaCollection) -> dict[str, Any]:
    payload = collection.to_payload()
    payload["products"] = [tile.product_id for tile in tiles(collection.ppd, "topography")]
    payload["product_count"] = {role: len(tiles(collection.ppd, role)) for role in collection.roles}
    return payload


def _print_json(document: Any) -> None:
    print(json.dumps(document, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mola_global.py",
        description=(
            "Official NASA PDS4 MOLA MEGDR source adapter. Reads only bounded windows; "
            "never downloads a full tile or the global mosaic."
        ),
    )
    parser.add_argument(
        "--list-collections", action="store_true", help="Offline collection inventory"
    )
    parser.add_argument("--list-tiles", action="store_true", help="Offline product inventory")
    parser.add_argument(
        "--ppd", default="128", help="MEGDR pixels/degree collection (4/16/32/64/128)"
    )
    parser.add_argument(
        "--role",
        default="topography",
        help="topography, count, radius, or areoid (where available)",
    )
    parser.add_argument("--probe", action="store_true", help="Network HEAD/range probe")
    parser.add_argument("--verify", action="store_true", help="Fetch and verify the PDS4 label")
    parser.add_argument(
        "--metadata",
        help="Inspect one product id (for example megt44n000hb) or the tile at --lonlat",
    )
    parser.add_argument(
        "--lonlat",
        nargs=2,
        type=float,
        metavar=("LON", "LAT"),
        help="Body-fixed lon/lat used to select the tile for --metadata",
    )
    parser.add_argument("--checksums", action="store_true", help="Fetch the bundle MD5 index")
    parser.add_argument(
        "--window",
        nargs=4,
        type=float,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="Bounded window in body-fixed lon/lat degrees",
    )
    parser.add_argument("--output", help="Write the bounded window to this GeoTIFF")
    parser.add_argument(
        "--local-tile-dir",
        help="Directory containing manually staged <product>.lbl/.img/.hdr",
    )
    parser.add_argument("--offline", action="store_true", help="Refuse all remote reads")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--max-window-mb", type=float, default=DEFAULT_MAX_WINDOW_BYTES / 1e6)
    return parser


# ---------------------------------------------------------------------------
# Goal B: arbitrary bbox Mars D01 terrain analysis (full-bbox, global MEG128)
# ---------------------------------------------------------------------------
# The AOI/bbox analysis and D01 computation are implemented in mola_aoi.py which
# provides bounded full-bbox reads from the verified MOLA MEGDR 128 ppd tiles and
# computes the five D01 terrain features (Horn/relief/roughness/flat fraction).
# This module re-exports a stable API/CLI surface owned by Goal B.

try:
    from .mola_aoi import (  # type: ignore[no-redef]  # noqa: I001
        ANALYSIS_RESOLUTION_M as D01_ANALYSIS_RESOLUTION_M,
        AOI_MAX_AREA_KM2,
        AOI_MAX_SIDE_M,
        AOI_MIN_SIDE_M,
        D01_FEATURE_IDS,
        METHOD_VERSION as D01_METHOD_VERSION,
        MOLA_NATIVE_RESOLUTION_M,
        MolaAoiError,
        MolaAoiRequest,
        MolaTile as MolaAoiTile,
        MolaWindow as MolaAoiWindow,
        RESULT_SCHEMA_VERSION as D01_RESULT_SCHEMA_VERSION,
        SOURCE_VERSION as D01_SOURCE_VERSION,
        analyze_mola_aoi,
        tiles_for_bbox as tiles_for_aoi_bbox,
        validate_mola_aoi,
    )
except Exception:  # pragma: no cover - fallback for script execution
    from mola_aoi import (  # type: ignore[no-redef]  # noqa: I001
        ANALYSIS_RESOLUTION_M as D01_ANALYSIS_RESOLUTION_M,
        AOI_MAX_AREA_KM2,
        AOI_MAX_SIDE_M,
        AOI_MIN_SIDE_M,
        D01_FEATURE_IDS,
        METHOD_VERSION as D01_METHOD_VERSION,
        MOLA_NATIVE_RESOLUTION_M,
        MolaAoiError,
        MolaAoiRequest,
        MolaTile as MolaAoiTile,
        MolaWindow as MolaAoiWindow,
        RESULT_SCHEMA_VERSION as D01_RESULT_SCHEMA_VERSION,
        SOURCE_VERSION as D01_SOURCE_VERSION,
        analyze_mola_aoi,
        tiles_for_bbox as tiles_for_aoi_bbox,
        validate_mola_aoi,
    )


MolaGlobalError = MolaAoiError
METHOD_VERSION = D01_METHOD_VERSION
TERRAIN_METHOD_VERSION = "terrain-metrics-1.0.0"
MARS_FRAME = "mars_planetocentric"
MEG128_RESOLUTION_M = MOLA_NATIVE_RESOLUTION_M


def analyze_mars_bbox_d01(
    bbox: Mapping[str, float],
    *,
    label: str = "custom-mars-aoi",
    allow_remote: bool = True,
    cache_dir: str | Path | None = None,
    analysis_resolution_m: float | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_read_bytes: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Compute D01 terrain features for an arbitrary Mars bbox from global MOLA MEGDR.

    This is the Goal B entry point owned by ``mola_global.py``. It performs bounded
    reads from the MOLA MEGDR 128 ppd tiles covering the requested bbox and computes
    the five D01 terrain features (slope_mean_deg, slope_p90_deg,
    terrain_relief_p95_p05_m, roughness_std_m, flat_terrain_fraction). Results include
    actual feature/elevation values, valid fraction, CRS/resolution, QA/warnings, and
    source checksum/version/provenance. Missing data is never zero-filled.

    Extra keyword arguments (cache_dir, analysis_resolution_m, timeout,
    max_read_bytes, refresh) are accepted for API compatibility; the primary bounded
    reader uses GDAL/vsicurl range reads via the planetary coarse infrastructure.
    """
    _ = (cache_dir, analysis_resolution_m, timeout, max_read_bytes, refresh)
    return analyze_mola_aoi(bbox, label=label, allow_remote=allow_remote)


def _build_parser_with_aoi() -> argparse.ArgumentParser:
    parser = build_parser()
    parser.add_argument(
        "--analyze-bbox",
        nargs=4,
        type=float,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="compute D01 features for a Mars lon/lat bbox (west south east north)",
    )
    parser.add_argument("--label", default="custom-mars-aoi", help="label for AOI analysis")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow bounded remote MOLA reads for AOI analysis",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser_with_aoi()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "analyze_bbox", None):
            if not args.allow_remote and not getattr(args, "offline", False):
                # AOI analysis needs remote reads by default for global tiles unless local cache provided
                args.allow_remote = True
            result = analyze_mars_bbox_d01(
                {
                    "west": args.analyze_bbox[0],
                    "south": args.analyze_bbox[1],
                    "east": args.analyze_bbox[2],
                    "north": args.analyze_bbox[3],
                },
                label=args.label,
                allow_remote=bool(args.allow_remote),
            )
            _print_json(result)
            return 0
        # fall through to original behavior
        # reparse with original parser logic by calling inner paths
        if args.list_collections:
            document = {
                "bundle_lid": MOLA_BUNDLE_LID,
                "bundle_version": MOLA_BUNDLE_VERSION,
                "doi": MOLA_DOI,
                "bundle_url": MOLA_BUNDLE_URL,
                "readme_url": MOLA_README_URL,
                "license_status": MOLA_LICENSE_STATUS,
                "collections": [_collection_payload(c) for c in MOLA_COLLECTIONS.values()],
            }
            _print_json(document)
            return 0
        if args.list_tiles:
            collection = resolve_collection(args.ppd)
            tile_list = tiles(collection.ppd, role=args.role)
            document = {
                "collection": _collection_payload(collection),
                "role": _normalise_role(args.role),
                "tile_count": len(tile_list),
                "tiles": [tile.to_payload() for tile in tile_list],
            }
            _print_json(document)
            return 0
        if args.checksums:
            index = fetch_checksum_index(timeout=args.timeout)
            _print_json({"url": MOLA_CHECKSUM_INDEX_URL, "count": len(index), "checksums": index})
            return 0
        if args.probe:
            probe_tile = tiles(args.ppd, args.role)[0]
            _print_json(probe_remote(probe_tile, timeout=min(args.timeout, 60.0)).to_payload())
            return 0
        if args.verify:
            verify_tile = tiles(args.ppd, args.role)[0]
            meta = inspect_remote_product(verify_tile, timeout=args.timeout)
            _print_json(
                {
                    "verified": True,
                    "product_id": verify_tile.product_id,
                    "label_url": verify_tile.pds4_label_url,
                    "logical_identifier": meta.logical_identifier,
                    "version_id": meta.version_id,
                    "shape": [meta.lines, meta.samples],
                    "data_type": meta.data_type,
                    "unit": meta.unit,
                    "value_offset_m": meta.value_offset_m,
                    "pixel_resolution_m": meta.pixel_resolution_x_m,
                    "upperleft_m": [meta.upperleft_x_m, meta.upperleft_y_m],
                    "bounds_lonlat": [
                        meta.west_deg,
                        meta.south_deg,
                        meta.east_deg,
                        meta.north_deg,
                    ],
                    "a_axis_radius_km": meta.a_axis_radius_km,
                    "latitude_type": meta.latitude_type,
                    "longitude_direction": meta.longitude_direction,
                    "reference_frame": meta.reference_frame,
                }
            )
            return 0
        if args.metadata:
            try:
                tile = tile_for_product_id(args.metadata)
            except MolaSourceError:
                if args.lonlat is None:
                    raise
                tile = tile_for_lonlat(args.lonlat[0], args.lonlat[1], ppd=args.ppd, role=args.role)
            if args.offline:
                if not args.local_tile_dir:
                    raise MolaSourceError(
                        "offline_requires_local", "--offline metadata requires --local-tile-dir"
                    )
                meta = inspect_source(tile, local_tile_dir=args.local_tile_dir, allow_remote=False)
                _print_json(meta.to_payload())
                return 0
            pds4 = fetch_pds4_label(tile, timeout=args.timeout)
            verify_pds4_metadata(tile, pds4)
            meta = inspect_source(
                tile,
                local_tile_dir=args.local_tile_dir,
                allow_remote=not args.offline,
                timeout=args.timeout,
            )
            payload = meta.to_payload()
            payload["pds4"] = {
                "product_lid": pds4.logical_identifier,
                "version_id": pds4.version_id,
                "pixel_resolution_m": pds4.pixel_resolution_x_m,
                "upperleft_x_m": pds4.upperleft_x_m,
                "upperleft_y_m": pds4.upperleft_y_m,
                "value_offset_m": pds4.value_offset_m,
                "unit": pds4.unit,
                "data_type": pds4.data_type,
                "latitude_type": pds4.latitude_type,
                "longitude_direction": pds4.longitude_direction,
                "reference_frame": pds4.reference_frame,
            }
            _print_json(payload)
            return 0
        if args.window:
            window = read_window(
                *args.window,
                ppd=args.ppd,
                role=args.role,
                local_tile_dir=args.local_tile_dir,
                allow_remote=not args.offline,
                max_window_bytes=int(args.max_window_mb * 1e6),
                timeout=args.timeout,
            )
            document = window.as_dict()
            if args.output:
                target = write_window_geotiff(window, args.output)
                document["output"] = str(target)
                document["output_sidecar"] = str(target) + ".json"
            _print_json(document)
            return 0
        parser.print_help()
        return 0
    except MolaSourceError as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 2
    except MolaAoiError as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 2


__all__ = (
    # MOLA MEGDR source adapter (this slice)
    "MARS_REFERENCE_FRAME",
    "MARS_SPHERE_RADIUS_M",
    "MOLA_128PPD_IMAGE_BYTES",
    "MOLA_16PPD_IMAGE_BYTES",
    "MOLA_BUNDLE_LID",
    "MOLA_BUNDLE_URL",
    "MOLA_CHECKSUM_INDEX_URL",
    "MOLA_COLLECTIONS",
    "MOLA_DOI",
    "MOLA_LICENSE_STATUS",
    "MOLA_PROVIDER",
    "MOLA_TILE_INDEX",
    "MolaCollection",
    "MolaPds4Metadata",
    "MolaRasterMetadata",
    "MolaRemoteProbe",
    "MolaSourceError",
    "MolaTile",
    "MolaWindow",
    "apply_nodata_and_scaling",
    "effective_nodata",
    "estimate_window_bytes",
    "fetch_checksum_index",
    "fetch_pds4_label",
    "georeference_delta",
    "inspect_remote_product",
    "inspect_source",
    "main",
    "metres_per_degree",
    "parse_pds4_label",
    "probe_remote",
    "published_md5",
    "read_window",
    "resolve_collection",
    "staged_tile_paths",
    "tile_bounds_metric",
    "tile_for_lonlat",
    "tile_for_product_id",
    "tile_transform",
    "tiles",
    "tiles_for_collection",
    "tiles_for_lonlat_bbox",
    "verify_pds4_metadata",
    "verify_staged_tile",
    "window_for_lonlat_bbox",
    "window_lonlat_bounds",
    "write_window_geotiff",
    # Goal B AOI API (owned by mola_aoi.py, re-exported here for a stable surface)
    "AOI_MAX_AREA_KM2",
    "AOI_MAX_SIDE_M",
    "AOI_MIN_SIDE_M",
    "D01_ANALYSIS_RESOLUTION_M",
    "D01_FEATURE_IDS",
    "D01_METHOD_VERSION",
    "D01_RESULT_SCHEMA_VERSION",
    "D01_SOURCE_VERSION",
    "MARS_FRAME",
    "MEG128_RESOLUTION_M",
    "METHOD_VERSION",
    "MOLA_NATIVE_RESOLUTION_M",
    "MolaAoiError",
    "MolaAoiRequest",
    "MolaAoiTile",
    "MolaAoiWindow",
    "MolaGlobalError",
    "TERRAIN_METHOD_VERSION",
    "analyze_mars_bbox_d01",
    "analyze_mola_aoi",
    "tiles_for_aoi_bbox",
    "validate_mola_aoi",
)

if __name__ == "__main__":
    raise SystemExit(main())
