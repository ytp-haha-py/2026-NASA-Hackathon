#!/usr/bin/env python3
"""Global lunar LOLA DEM source adapter: catalogue, probes and bounded windows.

This is the Issue #170 Goal A source adapter. It owns the *source* facts and
bounded range access for global Moon terrain. It does not extract features, does
not write manifests and does not cache rasters. (The separate arbitrary-AOI
engine lives in ``pipelines/planetary/lola_global.py``; it consumes the same
verified products.)

Exposed here:

* a small catalogue of verified global LOLA elevation products, with the primary
  NASA PGDA MOON_PA shape models and the NASA SVS CGI Moon Kit displacement maps
  as an explicitly coarser fallback;
* an HTTP metadata/range probe (HEAD + a 512-byte range GET, checked for HTTP 206
  and TIFF/BigTIFF magic) and a GDAL metadata reader;
* a bounded geographic-window reader that returns a real float32 metre array,
  its lon/lat bounds, a conservative transfer estimate and a content checksum.

Everything was verified live on 2026-11-15. The observed evidence and exact
reproducible commands are in ``docs/MOON_LOLA_SOURCE.md``. No global raster is
downloaded, no value is zero-filled, and no source is promoted to 90 m D01
comparability.

Two facts drive the design:

* The PGDA global shape models are geographic 0..360, 64 or 128 pixels/degree
  (473.8 m / 236.9 m at the equator). They are *coarser* than the 90 m D01 grid,
  so they are coarse context, never a substitute for a 5 m site DEM.
* The NASA SVS displacement TIFFs have **no CRS and no geotransform** in the
  file. Their -180..180 equirectangular mapping, 1737.4 km sphere and vertical
  units come from the SVS page and were cross-checked against PGDA. They are a
  fallback of last resort and remain 64 ppd at best.

The reader never assumes degrees are metres and never labels a Moon grid with an
Earth CRS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.env import Env
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window

# ---------------------------------------------------------------------------
# Verified catalogue (2026-11-15). URLs and sizes were re-probed live.
# ---------------------------------------------------------------------------

BODY = "Moon"
BODY_RADIUS_M = 1_737_400.0
PGDA_PRODUCT_PAGE = "https://pgda.gsfc.nasa.gov/products/95"
PGDA_LDEM64_URL = "https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM64_PA_pixel_202405.tif"
PGDA_LDEM128_URL = "https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif"
SVS_PAGE = "https://svs.gsfc.nasa.gov/4720"
SVS_BASE = "https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720"

NASA_LICENSE_URL = "https://science.data.nasa.gov/about/license"
NASA_MEDIA_GUIDELINES_URL = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
PGDA_LICENSE = (
    "not stated on the PGDA product page; public NASA archive with a citation "
    "requirement (Data Usage Policy)"
)
SVS_LICENSE = (
    "NASA SVS visualisation product; no licence field. Underlying LOLA data are "
    "public NASA archive data (citation requested)"
)

PGDA_CITATION = (
    "Neumann, G. (2024). LOLA MOON_PA gridded dataset [Data set]. NASA Goddard "
    "Space Flight Center Planetary Geodesy Data Archive. doi:10.60903/LOLA_PA"
)
SVS_CITATION = (
    "Wright, E. (2019; page updated 2026), CGI Moon Kit, NASA Scientific "
    "Visualization Studio, https://svs.gsfc.nasa.gov/4720. Displacement maps "
    "derived from LRO LOLA gridded products (PDS Geosciences Node)."
)

ANALYSIS_RESOLUTION_M = 90.0
DEFAULT_MAX_WINDOW_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_S = 120.0
MAX_RETRIES = 2
USER_AGENT = "planetary-analog-moon/0.2 (LOLA global source adapter)"
PROBE_BYTES = 512


class LolaGlobalError(RuntimeError):
    """A real source/access problem. Never replaced with synthetic values."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class LolaGlobalSource:
    """A verified global lunar elevation product.

    ``vertical_scale_to_m`` and ``vertical_offset_m`` convert the stored sample
    value to metres: ``metres = raw * scale + offset``. They are verified facts,
    not defaults.
    """

    source_id: str
    role: str  # "primary" | "fallback"
    title: str
    url: str
    product_page: str
    provider: str
    license: str
    license_url: str
    citation: str
    doi: str | None
    frame: str
    longitude_convention: str  # "0_to_360" | "-180_to_180"
    native_ppd: float
    native_resolution_m: float
    expected_dtype: str
    nodata: float | None
    vertical_unit: str
    vertical_scale_to_m: float
    vertical_offset_m: float
    georeferenced: bool
    expected_width: int | None
    expected_height: int | None
    expected_size_bytes: int | None
    checksum_published: bool
    notes: str
    quality_flags: tuple[str, ...] = ()

    @property
    def is_fallback(self) -> bool:
        return self.role == "fallback"

    def to_payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["is_fallback"] = self.is_fallback
        data["quality_flags"] = list(self.quality_flags)
        return data


def _resolution_m(ppd: float) -> float:
    return math.radians(1.0 / ppd) * BODY_RADIUS_M


PGDA_LDEM64 = LolaGlobalSource(
    source_id="pgda-lola-ldem64-pa-pixel-202405",
    role="primary",
    title="LOLA MOON_PA global shape model, 64 pixels/degree, pixel-registered",
    url=PGDA_LDEM64_URL,
    product_page=PGDA_PRODUCT_PAGE,
    provider="NASA GSFC Planetary Geodesy Data Archive (PGDA) / LRO-LOLA",
    license=PGDA_LICENSE,
    license_url=PGDA_PRODUCT_PAGE,
    citation=PGDA_CITATION,
    doi="10.60903/LOLA_PA",
    frame="MOON_PA (DE421), body-fixed",
    longitude_convention="0_to_360",
    native_ppd=64.0,
    native_resolution_m=_resolution_m(64.0),
    expected_dtype="float32",
    nodata=float("nan"),
    vertical_unit="metre",
    vertical_scale_to_m=1.0,
    vertical_offset_m=0.0,
    georeferenced=True,
    expected_width=23040,
    expected_height=11520,
    expected_size_bytes=715_159_249,
    checksum_published=False,
    notes=(
        "Global 23040 x 11520 pixel-registered BigTIFF, float32, deflate, 256x256 "
        "tiles, nodata NaN, CRS GCS_Moon_2000 (ESRI:104903, sphere 1737400 m), "
        "bounds 0..360 longitude / -90..90 latitude. Values are metres relative to "
        "the 1737.4 km radius; the band actual_range tag is inherited in kilometres "
        "(-9.12 to 10.77) from the sibling netCDF/GMT grid. Native support is "
        "473.8 m at the equator, 5.26x coarser than 90 m. Built by transforming "
        "SLDEM2015 256 ppd and the LOLA polar stereographic LDEMs to MOON_PA and "
        "averaging at 64 ppd; partly interpolated, not raw altimetry."
    ),
    quality_flags=(
        "global_shape_model",
        "coarse_context_only",
        "moon_pa_frame",
        "partly_interpolated",
        "pixel_registered_geographic",
        "native_support_473m",
        "vertical_unit_inferred_from_pixel_magnitude",
        "bounded_vsicurl_window",
        "no_published_checksum",
    ),
)

PGDA_LDEM128 = LolaGlobalSource(
    source_id="pgda-lola-ldem128-pa-pixel-202405",
    role="primary",
    title="LOLA MOON_PA global shape model, 128 pixels/degree, pixel-registered",
    url=PGDA_LDEM128_URL,
    product_page=PGDA_PRODUCT_PAGE,
    provider="NASA GSFC Planetary Geodesy Data Archive (PGDA) / LRO-LOLA",
    license=PGDA_LICENSE,
    license_url=PGDA_PRODUCT_PAGE,
    citation=PGDA_CITATION,
    doi="10.60903/LOLA_PA",
    frame="MOON_PA (DE421), body-fixed",
    longitude_convention="0_to_360",
    native_ppd=128.0,
    native_resolution_m=_resolution_m(128.0),
    expected_dtype="float32",
    nodata=float("nan"),
    vertical_unit="metre",
    vertical_scale_to_m=1.0,
    vertical_offset_m=0.0,
    georeferenced=True,
    expected_width=46080,
    expected_height=23040,
    expected_size_bytes=2_705_286_892,
    checksum_published=False,
    notes=(
        "Global 46080 x 23040 pixel-registered BigTIFF, float32, deflate, 256x256 "
        "tiles, nodata NaN, CRS GCS_Moon_2000 (ESRI:104903, sphere 1737400 m), "
        "bounds 0..360 longitude / -90..90 latitude. Values are metres relative to "
        "the 1737.4 km radius; the band actual_range tag is inherited in kilometres. "
        "Native support is 236.9 m at the equator, 2.63x coarser than 90 m, so it "
        "cannot produce independent 90 m metrics. Same MOON_PA construction as the "
        "64 ppd model; partly interpolated. Highest-resolution *global* LOLA DEM "
        "verified here."
    ),
    quality_flags=(
        "global_shape_model",
        "coarse_context_only",
        "moon_pa_frame",
        "partly_interpolated",
        "pixel_registered_geographic",
        "native_support_236m",
        "vertical_unit_inferred_from_pixel_magnitude",
        "bounded_vsicurl_window",
        "no_published_checksum",
    ),
)


def _svs_source(
    source_id: str,
    filename: str,
    ppd: float,
    width: int,
    height: int,
    size_bytes: int,
    dtype: str,
    scale: float,
    offset: float,
    unit: str,
) -> LolaGlobalSource:
    return LolaGlobalSource(
        source_id=source_id,
        role="fallback",
        title=f"NASA SVS CGI Moon Kit LOLA displacement map, {ppd:g} pixels/degree",
        url=f"{SVS_BASE}/{filename}",
        product_page=SVS_PAGE,
        provider="NASA Scientific Visualization Studio (SVS) / LRO-LOLA",
        license=SVS_LICENSE,
        license_url=NASA_MEDIA_GUIDELINES_URL,
        citation=SVS_CITATION,
        doi=None,
        frame="LOLA gridded product frame, exact frame not stated by SVS",
        longitude_convention="-180_to_180",
        native_ppd=ppd,
        native_resolution_m=_resolution_m(ppd),
        expected_dtype=dtype,
        nodata=None,
        vertical_unit=unit,
        vertical_scale_to_m=scale,
        vertical_offset_m=offset,
        georeferenced=False,
        expected_width=width,
        expected_height=height,
        expected_size_bytes=size_bytes,
        checksum_published=False,
        notes=(
            f"{width} x {height} uncompressed row-striped TIFF, {dtype}, no CRS, no "
            "geotransform, no nodata tag, no overviews. The file is a bare array; the "
            "-180..180 longitude convention, north-up row order, 1737.4 km sphere and "
            "vertical units come from the SVS page and were cross-checked against "
            f"PGDA. {unit}: metres = raw * {scale:g} + {offset:g}. Native support is "
            f"{_resolution_m(ppd):.1f} m at the equator. This is a visualisation "
            "product derived from the spring-2019 LOLA PDS gridded data, not the 2024 "
            "MOON_PA shape model, and it is the fallback only when PGDA is "
            "unavailable."
        ),
        quality_flags=(
            "fallback_visualization_product",
            "no_crs_or_geotransform_in_file",
            "documented_equirectangular_mapping",
            "derived_from_2019_lola_pds_grids",
            "coarse_context_only",
            f"native_support_{_resolution_m(ppd):.0f}m",
            "no_published_checksum",
            "bounded_http_read",
        ),
    )


SVS_LDEM_64 = _svs_source(
    "svs-cgi-moon-kit-ldem64",
    "ldem_64.tif",
    64.0,
    23040,
    11520,
    1_061_775_722,
    "float32",
    1000.0,
    0.0,
    "kilometre",
)
SVS_LDEM_16 = _svs_source(
    "svs-cgi-moon-kit-ldem16",
    "ldem_16.tif",
    16.0,
    5760,
    2880,
    66_378_634,
    "float32",
    1000.0,
    0.0,
    "kilometre",
)
SVS_LDEM_4 = _svs_source(
    "svs-cgi-moon-kit-ldem4",
    "ldem_4.tif",
    4.0,
    1440,
    720,
    4_153_352,
    "float32",
    1000.0,
    0.0,
    "kilometre",
)
SVS_LDEM_64_UINT = _svs_source(
    "svs-cgi-moon-kit-ldem64-uint",
    "ldem_64_uint.tif",
    64.0,
    23040,
    11520,
    530_934_146,
    "uint16",
    0.5,
    -10_000.0,
    "half-metre_plus_10km_offset",
)

GLOBAL_LOLA_SOURCES: tuple[LolaGlobalSource, ...] = (
    PGDA_LDEM64,
    PGDA_LDEM128,
    SVS_LDEM_64,
    SVS_LDEM_16,
    SVS_LDEM_4,
    SVS_LDEM_64_UINT,
)
SOURCES_BY_ID = {source.source_id: source for source in GLOBAL_LOLA_SOURCES}


def source_by_id(source_id: str) -> LolaGlobalSource:
    try:
        return SOURCES_BY_ID[source_id]
    except KeyError as exc:
        known = ", ".join(SOURCES_BY_ID)
        raise LolaGlobalError(
            "unknown_source_id", f"unknown source id {source_id!r}; known: {known}"
        ) from exc


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceRef:
    raw: str
    is_remote: bool
    url: str | None
    path: Path | None
    gdal_path: str


@dataclass(frozen=True, slots=True)
class RemoteProbe:
    url: str
    status: int
    content_length: int | None
    accept_ranges: bool
    range_verified: bool
    etag: str | None
    last_modified: str | None
    tiff_magic: str | None
    bigtiff: bool | None


@dataclass(frozen=True, slots=True)
class GlobalRasterMeta:
    source_id: str
    role: str
    url: str
    observed_url: str
    driver: str
    width: int
    height: int
    count: int
    dtype: str
    nodata: float | None
    crs_wkt: str | None
    crs_name: str | None
    georeferenced: bool
    frame: str
    longitude_convention: str
    origin_lon: float
    bounds_lonlat: tuple[float, float, float, float]
    resolution_deg: float
    native_resolution_m: float
    radius_m: float
    bigtiff: bool | None
    tiff_magic: str | None
    is_tiled: bool
    block_shapes: tuple[tuple[int, int], ...]
    compress: str | None
    overviews: tuple[int, ...]
    size_bytes: int | None
    accept_ranges: bool
    etag: str | None
    last_modified: str | None
    checksum_published: bool
    access_timestamp: str
    warnings: tuple[str, ...]

    @property
    def is_fallback(self) -> bool:
        return self.role == "fallback"

    def to_payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["bounds_lonlat"] = list(self.bounds_lonlat)
        data["warnings"] = list(self.warnings)
        data["block_shapes"] = [list(shape) for shape in self.block_shapes]
        return data


@dataclass(frozen=True, slots=True)
class WindowPlan:
    window: Window
    bounds_lonlat: tuple[float, float, float, float]
    transform: tuple[float, float, float, float, float, float]
    center_lon_source: float
    resolution_deg: float
    width_px: int
    height_px: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "col_off": int(self.window.col_off),
            "row_off": int(self.window.row_off),
            "width_px": self.width_px,
            "height_px": self.height_px,
            "bounds_lonlat": list(self.bounds_lonlat),
            "transform": list(self.transform),
            "center_lon_source": self.center_lon_source,
            "resolution_deg": self.resolution_deg,
        }


@dataclass(frozen=True, slots=True)
class BoundedWindow:
    source_id: str
    center_lon: float
    center_lat: float
    center_lon_source: float
    window_m: float
    pad_m: float
    plan: WindowPlan
    meta: GlobalRasterMeta
    estimated_source_bytes: int
    array: np.ndarray | None
    valid_fraction: float | None
    min_elevation_m: float | None
    max_elevation_m: float | None
    window_sha256: str | None
    access_timestamp: str | None
    from_dry_run: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "center_lon": self.center_lon,
            "center_lat": self.center_lat,
            "center_lon_source": self.center_lon_source,
            "window_m": self.window_m,
            "pad_m": self.pad_m,
            "window": self.plan.to_payload(),
            "estimated_source_bytes": self.estimated_source_bytes,
            "valid_fraction": self.valid_fraction,
            "min_elevation_m": self.min_elevation_m,
            "max_elevation_m": self.max_elevation_m,
            "window_sha256": self.window_sha256,
            "access_timestamp": self.access_timestamp,
            "from_dry_run": self.from_dry_run,
            "source": self.meta.to_payload(),
        }


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} MB ({value:,} bytes)"
    if value >= 1_000:
        return f"{value / 1_000:.2f} kB ({value:,} bytes)"
    return f"{value:,} bytes"


def parse_source(raw_source: str, *, allow_remote: bool = True) -> SourceRef:
    """Resolve an HTTPS URL, ``/vsicurl/`` URL, ``file://`` URL or local path."""
    raw = str(raw_source).strip()
    if not raw:
        raise LolaGlobalError("empty_source", "The source URL/path is empty.")

    if raw.startswith("/vsicurl/"):
        url = raw[len("/vsicurl/") :]
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise LolaGlobalError(
                "invalid_source_url", f"{raw!r} is not a valid /vsicurl/ HTTP(S) URL."
            )
        if not allow_remote:
            raise LolaGlobalError("remote_read_disabled", "remote reads are disabled")
        if parsed.scheme == "http":
            raise LolaGlobalError("insecure_remote_read", "only HTTPS range reads are allowed")
        return SourceRef(raw=raw, is_remote=True, url=url, path=None, gdal_path=raw)

    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme in ("http", "https"):
        if not parsed.netloc:
            raise LolaGlobalError("invalid_source_url", f"{raw!r} has no host component.")
        if not allow_remote:
            raise LolaGlobalError("remote_read_disabled", "remote reads are disabled")
        if parsed.scheme == "http":
            raise LolaGlobalError("insecure_remote_read", "only HTTPS range reads are allowed")
        return SourceRef(raw=raw, is_remote=True, url=raw, path=None, gdal_path=f"/vsicurl/{raw}")
    if parsed.scheme == "file":
        path = Path(urllib.request.url2pathname(parsed.path)).expanduser()
        return SourceRef(raw=raw, is_remote=False, url=None, path=path, gdal_path=str(path))
    if parsed.scheme and len(parsed.scheme) > 1:
        raise LolaGlobalError(
            "unsupported_source_scheme",
            f"unsupported source scheme {parsed.scheme!r}; use https, file://, "
            "/vsicurl/ or a local path",
        )
    path = Path(raw).expanduser()
    return SourceRef(raw=raw, is_remote=False, url=None, path=path, gdal_path=str(path))


def rasterio_remote_env(timeout: float = DEFAULT_TIMEOUT_S) -> Env:
    """Bounded GDAL settings for anonymous HTTPS range reads."""
    return Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
        CPL_VSIL_CURL_USE_HEAD="YES",
        GDAL_HTTP_MAX_RETRY="0",
        GDAL_HTTP_MULTIRANGE="SERIAL",
        GDAL_HTTP_MULTIPLEX="NO",
        GDAL_HTTP_TIMEOUT=str(max(1, int(timeout))),
        GDAL_HTTP_CONNECTTIMEOUT=str(min(20, max(1, int(timeout)))),
        GDAL_HTTP_USERAGENT=USER_AGENT,
        VSI_CACHE="TRUE",
        VSI_CACHE_SIZE="33554432",
    )


def _tiff_magic(prefix: bytes) -> tuple[str, bool | None]:
    if len(prefix) < 4:
        return "", None
    if prefix[:4] == b"II+\x00":
        return "II+ (little-endian BigTIFF)", True
    if prefix[:4] == b"MM\x00+":
        return "MM+ (big-endian BigTIFF)", True
    if prefix[:4] == b"II*\x00":
        return "II* (little-endian classic TIFF)", False
    if prefix[:4] == b"MM\x00*":
        return "MM* (big-endian classic TIFF)", False
    return prefix[:4].hex(), None


def _range_probe(url: str, *, timeout: float, opener: Any) -> tuple[int, str | None, bool | None]:
    request = urllib.request.Request(
        url,
        headers={
            "Range": f"bytes=0-{PROBE_BYTES - 1}",
            "User-Agent": USER_AGENT,
        },
    )
    with opener(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200))
        if status != 206:
            # Never read a 200 body: the server ignored Range and could send the object.
            return status, None, None
        prefix = response.read(PROBE_BYTES)
    magic, bigtiff = _tiff_magic(prefix)
    return status, magic, bigtiff


def probe_remote(
    url: str,
    *,
    timeout: float = 30.0,
    opener: Any = urllib.request.urlopen,
) -> RemoteProbe:
    """HEAD the object, then verify range support with a 512-byte range GET."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            headers = response.headers
            length_text = headers.get("Content-Length")
            content_length = int(length_text) if length_text else None
            head_status = int(getattr(response, "status", 200))
            etag = headers.get("ETag")
            last_modified = headers.get("Last-Modified")
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            raise LolaGlobalError("source_not_found", f"HTTP {exc.code} for {url}") from exc
        if exc.code not in (405, 501):
            raise LolaGlobalError("source_unreachable", f"HTTP {exc.code} for {url}") from exc
        content_length = None
        etag = None
        last_modified = None
        head_status = exc.code

    try:
        range_status, magic, bigtiff = _range_probe(url, timeout=timeout, opener=opener)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            raise LolaGlobalError("source_not_found", f"HTTP {exc.code} for {url}") from exc
        raise LolaGlobalError(
            "range_read_unsupported",
            f"{url}: HTTP {exc.code} for a {PROBE_BYTES}-byte range request; a "
            "bounded remote read cannot be guaranteed.",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LolaGlobalError("source_unreachable", f"cannot reach {url}: {exc}") from exc

    range_ok = range_status == 206
    return RemoteProbe(
        url=url,
        status=range_status if range_ok else head_status,
        content_length=content_length,
        accept_ranges=range_ok,
        range_verified=range_ok,
        etag=etag,
        last_modified=last_modified,
        tiff_magic=magic,
        bigtiff=bigtiff,
    )


def _normalise_lon(lon: float, origin_lon: float) -> float:
    """Map a longitude onto the source's documented convention."""
    if not math.isfinite(lon) or not -180.0 <= lon <= 360.0:
        raise LolaGlobalError("invalid_longitude", f"longitude {lon!r} is not in [-180, 360]")
    if math.isclose(origin_lon, -180.0):
        return ((lon + 180.0) % 360.0) - 180.0
    return lon % 360.0


def _metadata_from_dataset(
    src: rasterio.io.DatasetReader,
    *,
    source: LolaGlobalSource,
    ref: SourceRef,
    probe: RemoteProbe | None,
) -> GlobalRasterMeta:
    warnings_list: list[str] = []
    observed_crs = src.crs
    crs: CRS | None = None
    if observed_crs is not None:
        crs = CRS.from_user_input(observed_crs)
        if not crs.is_geographic:
            raise LolaGlobalError(
                "not_geographic",
                f"{ref.raw}: expected a geographic lunar grid, got {crs.to_string()}",
            )
        radius = crs.ellipsoid.semi_major_metre
        if radius is None or not (1_700_000.0 <= radius <= 1_800_000.0):
            raise LolaGlobalError(
                "not_a_lunar_crs",
                f"{ref.raw}: ellipsoid radius {radius!r} m is not lunar; refusing to "
                "label a non-lunar DEM as Moon terrain.",
            )
        if not math.isclose(float(radius), BODY_RADIUS_M, abs_tol=1.0):
            warnings_list.append(f"crs_radius_{float(radius):.0f}m_not_1737400m")
    else:
        if source.georeferenced:
            raise LolaGlobalError(
                "missing_crs",
                f"{ref.raw}: the catalogue says this source is georeferenced but the "
                "file has no CRS.",
            )
        warnings_list.append("no_crs_using_documented_equirectangular_mapping")
        radius = BODY_RADIUS_M

    if src.width < 2 or src.height < 2:
        raise LolaGlobalError(
            "source_too_small", f"{ref.raw}: source is {src.width} x {src.height} pixels"
        )
    if src.count != 1:
        warnings_list.append("multiple_bands_using_band_1")

    if crs is not None:
        res_x = abs(float(src.res[0]))
        res_y = abs(float(src.res[1]))
        if not math.isclose(res_x, res_y, rel_tol=1e-6):
            raise LolaGlobalError(
                "anisotropic_pixels",
                f"{ref.raw}: pixels are {res_x} x {res_y} degrees; refusing a "
                "single metric window estimate",
            )
        bounds = (
            float(src.bounds.left),
            float(src.bounds.bottom),
            float(src.bounds.right),
            float(src.bounds.top),
        )
        origin_lon = bounds[0]
        resolution_deg = res_x
    else:
        awidth = source.expected_width or src.width
        aheight = source.expected_height or src.height
        if not (src.width == awidth and src.height == aheight):
            raise LolaGlobalError(
                "fallback_shape_mismatch",
                f"{ref.raw}: {src.width} x {src.height} px does not match the "
                f"documented {awidth} x {aheight} px fallback grid",
            )
        resolution_deg = 1.0 / source.native_ppd
        origin_lon = -180.0 if source.longitude_convention == "-180_to_180" else 0.0
        bounds = (origin_lon, -90.0, origin_lon + 360.0, 90.0)

    if source.longitude_convention == "0_to_360" and not math.isclose(
        origin_lon % 360.0, 0.0, abs_tol=1e-9
    ):
        warnings_list.append("source_bounds_do_not_start_at_0_longitude")
    if source.longitude_convention == "-180_to_180" and not math.isclose(
        origin_lon, -180.0, abs_tol=1e-9
    ):
        warnings_list.append("source_bounds_do_not_start_at_minus_180_longitude")

    observed_dtype = src.dtypes[0]
    if observed_dtype != source.expected_dtype:
        warnings_list.append(f"dtype_{observed_dtype}_not_{source.expected_dtype}")
    observed_nodata = float(src.nodata) if src.nodata is not None else None
    expected_nodata = source.nodata
    if expected_nodata is None:
        if observed_nodata is not None:
            warnings_list.append("unexpected_nodata_tag")
    elif observed_nodata is None:
        warnings_list.append("expected_nan_nodata_tag_missing")
    elif not math.isnan(observed_nodata):
        warnings_list.append("nodata_not_nan")

    if source.expected_width is not None and src.width != source.expected_width:
        warnings_list.append(f"width_{src.width}_not_{source.expected_width}")
    if source.expected_height is not None and src.height != source.expected_height:
        warnings_list.append(f"height_{src.height}_not_{source.expected_height}")

    is_tiled = bool(src.profile.get("tiled", False))
    compress = src.profile.get("compress")
    overviews = tuple(int(value) for value in src.overviews(1))
    if not is_tiled:
        warnings_list.append("source_striped_not_cog")
    if not overviews:
        warnings_list.append("source_has_no_overviews")
    if not compress:
        warnings_list.append("source_uncompressed")

    bigtiff = probe.bigtiff if probe is not None else None
    if probe is not None and probe.tiff_magic is None:
        warnings_list.append("unverified_tiff_magic")
    if (
        probe is not None
        and probe.tiff_magic is not None
        and not probe.tiff_magic.startswith(("II", "MM"))
    ):
        warnings_list.append("source_is_not_tiff")

    native_resolution_m = math.radians(resolution_deg) * float(radius)
    if native_resolution_m > ANALYSIS_RESOLUTION_M:
        warnings_list.append("source_coarser_than_90m")
    if source.is_fallback:
        warnings_list.append("fallback_source_not_primary_archive")
    if not source.checksum_published:
        warnings_list.append("no_published_checksum_etag_is_not_a_hash")
    if source.source_id.startswith("pgda-"):
        warnings_list.append("moon_pa_not_moon_me")
        warnings_list.append("vertical_unit_inferred_from_pixel_magnitude")
    if source.source_id.startswith("svs-"):
        warnings_list.append("svs_2019_derived_grid")

    if ref.is_remote:
        if probe is None:
            raise LolaGlobalError("missing_range_probe", f"{ref.raw}: remote source was not probed")
        size_bytes = probe.content_length
        accept_ranges = probe.accept_ranges
        etag = probe.etag
        last_modified = probe.last_modified
    else:
        size_bytes = ref.path.stat().st_size if ref.path and ref.path.is_file() else None
        accept_ranges = True
        etag = None
        last_modified = None

    block_shapes = tuple((int(shape[0]), int(shape[1])) for shape in src.block_shapes)
    if not block_shapes:
        raise LolaGlobalError("unknown_block_layout", f"{ref.raw}: GDAL reported no block layout")

    return GlobalRasterMeta(
        source_id=source.source_id,
        role=source.role,
        url=source.url,
        observed_url=ref.url or str(ref.path),
        driver=src.driver,
        width=int(src.width),
        height=int(src.height),
        count=int(src.count),
        dtype=observed_dtype,
        nodata=observed_nodata,
        crs_wkt=crs.to_wkt() if crs is not None else None,
        crs_name=crs.name if crs is not None else None,
        georeferenced=crs is not None,
        frame=source.frame,
        longitude_convention=source.longitude_convention,
        origin_lon=float(origin_lon),
        bounds_lonlat=bounds,
        resolution_deg=float(resolution_deg),
        native_resolution_m=float(native_resolution_m),
        radius_m=float(radius),
        bigtiff=bigtiff,
        tiff_magic=probe.tiff_magic if probe is not None else None,
        is_tiled=is_tiled,
        block_shapes=block_shapes,
        compress=compress,
        overviews=overviews,
        size_bytes=size_bytes,
        accept_ranges=accept_ranges,
        etag=etag,
        last_modified=last_modified,
        checksum_published=source.checksum_published,
        access_timestamp=utc_now_iso(),
        warnings=tuple(dict.fromkeys(warnings_list)),
    )


def inspect_raster(
    source: LolaGlobalSource | str,
    *,
    url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    probe: RemoteProbe | None = None,
    allow_remote: bool = True,
    opener: Any = urllib.request.urlopen,
    sleeper: Any = time.sleep,
) -> GlobalRasterMeta:
    """Read source metadata and verify the lunar CRS without reading a window.

    ``url`` overrides the catalogue URL (used by offline tests with a local file).
    For a remote source the HTTP probe is required: without a verified 206 a
    bounded read is not promised.
    """
    resolved = source_by_id(source) if isinstance(source, str) else source
    if timeout <= 0:
        raise LolaGlobalError("invalid_timeout", "The timeout must be positive.")
    ref = parse_source(url or resolved.url, allow_remote=allow_remote)

    if ref.is_remote:
        assert ref.url is not None
        if probe is None:
            probe = probe_remote(ref.url, timeout=min(timeout, 60.0), opener=opener)
        if not probe.range_verified:
            raise LolaGlobalError(
                "range_read_unsupported",
                f"{ref.url}: the server did not return HTTP 206 for a {PROBE_BYTES}-byte "
                "range request, so a bounded remote read cannot be guaranteed.",
            )
    elif ref.path is not None and not ref.path.is_file():
        raise LolaGlobalError("source_not_found", f"local source does not exist: {ref.path}")

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with warnings.catch_warnings(), rasterio_remote_env(timeout):
                warnings.simplefilter("ignore", NotGeoreferencedWarning)
                with rasterio.open(ref.gdal_path) as src:
                    return _metadata_from_dataset(src, source=resolved, ref=ref, probe=probe)
        except LolaGlobalError:
            raise
        except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                sleeper(1.5 * (attempt + 1))
    raise LolaGlobalError(
        "source_open_failed",
        f"cannot open {ref.raw} after {max_retries + 1} attempt(s): {last_error}",
    )


def window_for_centre(
    meta: GlobalRasterMeta,
    center_lon: float,
    center_lat: float,
    *,
    window_m: float,
    pad_m: float = 0.0,
) -> WindowPlan:
    """Plan a metre-sized window on the source's documented equirectangular grid.

    A metre window is converted to degrees at the centre latitude. This is an
    equirectangular approximation appropriate to these global grids; it is not a
    map projection, and the returned lon/lat bounds are the actual source extent.
    A window that would cross the 0/360 or -180/180 seam or a pole is refused
    rather than silently wrapped.
    """
    if not math.isfinite(window_m) or window_m <= 0:
        raise LolaGlobalError("invalid_window", "window_m must be finite and positive.")
    if not math.isfinite(pad_m) or pad_m < 0:
        raise LolaGlobalError("invalid_pad", "pad_m must be finite and non-negative.")
    if not math.isfinite(center_lat) or not -90.0 <= center_lat <= 90.0:
        raise LolaGlobalError("invalid_latitude", f"latitude {center_lat!r} is not in [-90, 90]")

    lon_source = _normalise_lon(center_lon, meta.origin_lon)
    half_m = (window_m / 2.0) + pad_m
    dlat = math.degrees(half_m / meta.radius_m)
    cos_lat = math.cos(math.radians(center_lat))
    if cos_lat <= 1e-6:
        raise LolaGlobalError("pole_window", "cannot size a longitude span at a pole.")
    dlon = math.degrees(half_m / (meta.radius_m * cos_lat))
    if dlon >= 180.0:
        raise LolaGlobalError(
            "window_too_wide_at_latitude",
            f"a {window_m:,.0f} m window spans {dlon:.1f} degrees of longitude at "
            f"latitude {center_lat}; reduce the window.",
        )

    west = lon_source - dlon
    east = lon_source + dlon
    south = center_lat - dlat
    north = center_lat + dlat
    source_west = meta.origin_lon
    source_east = meta.origin_lon + 360.0
    if west < source_west or east > source_east:
        raise LolaGlobalError(
            "antimeridian_crossing",
            f"window {west:.3f}..{east:.3f} crosses the source seam "
            f"{source_west:.0f}..{source_east:.0f}; choose another centre.",
        )
    if south < -90.0 or north > 90.0:
        raise LolaGlobalError(
            "outside_latitude_range",
            f"window {south:.3f}..{north:.3f} exceeds -90..90 latitude.",
        )

    res = meta.resolution_deg
    col_off = int(math.floor((west - source_west) / res))
    col_end = int(math.ceil((east - source_west) / res))
    row_off = int(math.floor((90.0 - north) / res))
    row_end = int(math.ceil((90.0 - south) / res))
    if col_off < 0 or row_off < 0 or col_end > meta.width or row_end > meta.height:
        raise LolaGlobalError(
            "window_outside_source",
            f"pixel window cols {col_off}..{col_end}, rows {row_off}..{row_end} "
            f"exceeds {meta.width} x {meta.height}.",
        )
    width_px = col_end - col_off
    height_px = row_end - row_off
    if width_px < 1 or height_px < 1:
        raise LolaGlobalError("window_too_small", "the planned pixel window is empty.")

    actual_west = source_west + col_off * res
    actual_east = source_west + col_end * res
    actual_north = 90.0 - row_off * res
    actual_south = 90.0 - row_end * res
    return WindowPlan(
        window=Window(col_off, row_off, width_px, height_px),
        bounds_lonlat=(actual_west, actual_south, actual_east, actual_north),
        transform=(res, 0.0, actual_west, 0.0, -res, actual_north),
        center_lon_source=lon_source,
        resolution_deg=res,
        width_px=width_px,
        height_px=height_px,
    )


def estimate_window_bytes(meta: GlobalRasterMeta, plan: WindowPlan) -> int:
    """Conservative upper bound on source bytes touched by a window read.

    Compressed tiles usually transfer fewer bytes. For a striped TIFF the whole
    row span is counted, matching GDAL's row-strip reading behaviour.
    """
    itemsize = np.dtype(meta.dtype).itemsize
    window = plan.window
    if meta.is_tiled:
        block_h, block_w = meta.block_shapes[0]
        row0 = math.floor(window.row_off / block_h)
        row1 = math.ceil((window.row_off + window.height) / block_h)
        col0 = math.floor(window.col_off / block_w)
        col1 = math.ceil((window.col_off + window.width) / block_w)
        blocks = max(0, row1 - row0) * max(0, col1 - col0)
        data_bytes = blocks * block_w * block_h * itemsize
    else:
        data_bytes = meta.width * plan.height_px * itemsize
    estimate = int(data_bytes) + 64 * 1024
    if meta.size_bytes is not None:
        estimate = min(estimate, meta.size_bytes + 64 * 1024)
    return estimate


def read_bounded_window(
    source: LolaGlobalSource | str,
    *,
    center_lon: float,
    center_lat: float,
    window_m: float = 5_000.0,
    pad_m: float = 0.0,
    max_window_bytes: int = DEFAULT_MAX_WINDOW_BYTES,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    dry_run: bool = False,
    allow_remote: bool = True,
    url: str | None = None,
    probe: RemoteProbe | None = None,
    opener: Any = urllib.request.urlopen,
    sleeper: Any = time.sleep,
) -> BoundedWindow:
    """Read one bounded window and return real metres, or a plan when dry_run.

    The returned array is float32 metres with NaN for masked/void cells. It is
    never zero-filled and never upsampled. Nothing is written to disk.
    """
    if max_window_bytes <= 0:
        raise LolaGlobalError("invalid_byte_budget", "max_window_bytes must be positive.")
    resolved = source_by_id(source) if isinstance(source, str) else source
    meta = inspect_raster(
        resolved,
        url=url,
        timeout=timeout,
        max_retries=max_retries,
        probe=probe,
        allow_remote=allow_remote,
        opener=opener,
        sleeper=sleeper,
    )
    plan = window_for_centre(meta, center_lon, center_lat, window_m=window_m, pad_m=pad_m)
    estimate = estimate_window_bytes(meta, plan)
    if estimate > max_window_bytes:
        raise LolaGlobalError(
            "window_too_large",
            f"estimated range read is {format_bytes(estimate)}, above the "
            f"{format_bytes(max_window_bytes)} budget; reduce window_m.",
        )

    if dry_run:
        return BoundedWindow(
            source_id=resolved.source_id,
            center_lon=float(center_lon),
            center_lat=float(center_lat),
            center_lon_source=plan.center_lon_source,
            window_m=float(window_m),
            pad_m=float(pad_m),
            plan=plan,
            meta=meta,
            estimated_source_bytes=estimate,
            array=None,
            valid_fraction=None,
            min_elevation_m=None,
            max_elevation_m=None,
            window_sha256=None,
            access_timestamp=None,
            from_dry_run=True,
        )

    ref = parse_source(url or resolved.url, allow_remote=allow_remote)
    last_error: Exception | None = None
    array: np.ndarray | None = None
    access_timestamp: str | None = None
    for attempt in range(max_retries + 1):
        try:
            with warnings.catch_warnings(), rasterio_remote_env(timeout):
                warnings.simplefilter("ignore", NotGeoreferencedWarning)
                with rasterio.open(ref.gdal_path) as src:
                    data = src.read(1, window=plan.window, masked=True, out_dtype="float64")
                    raw = data.filled(np.nan) if np.ma.isMaskedArray(data) else np.asarray(data)
                    access_timestamp = utc_now_iso()
            metres = np.asarray(raw, dtype="float64") * resolved.vertical_scale_to_m
            metres = metres + resolved.vertical_offset_m
            array = np.where(np.isfinite(metres), metres, np.nan).astype("float32")
            break
        except (rasterio.errors.RasterioError, OSError) as exc:
            last_error = exc
            if attempt < max_retries:
                sleeper(1.5 * (attempt + 1))
    if array is None:
        raise LolaGlobalError(
            "window_read_failed",
            f"{ref.raw}: window read failed after {max_retries + 1} attempt(s): {last_error}",
        )

    finite = np.isfinite(array)
    valid_count = int(finite.sum())
    if valid_count == 0:
        raise LolaGlobalError(
            "no_valid_elevation",
            f"{ref.raw}: the requested window contains no finite elevation cells.",
        )
    flat = np.ascontiguousarray(array, dtype="<f4")
    digest = hashlib.sha256(flat.tobytes()).hexdigest()
    return BoundedWindow(
        source_id=resolved.source_id,
        center_lon=float(center_lon),
        center_lat=float(center_lat),
        center_lon_source=plan.center_lon_source,
        window_m=float(window_m),
        pad_m=float(pad_m),
        plan=plan,
        meta=meta,
        estimated_source_bytes=estimate,
        array=array,
        valid_fraction=float(valid_count / array.size),
        min_elevation_m=float(np.nanmin(array)),
        max_elevation_m=float(np.nanmax(array)),
        window_sha256=f"sha256:{digest}",
        access_timestamp=access_timestamp,
        from_dry_run=False,
    )


def source_catalog_text() -> str:
    lines = [
        "Verified global lunar LOLA elevation sources (2026-11-15):",
        "",
    ]
    for source in GLOBAL_LOLA_SOURCES:
        lines.append(
            f"{source.source_id}\n"
            f"  role:        {source.role}\n"
            f"  title:       {source.title}\n"
            f"  resolution:  {source.native_ppd:g} ppd, "
            f"{source.native_resolution_m:.1f} m at the equator\n"
            f"  georeferenced in file: {source.georeferenced}\n"
            f"  vertical:    {source.vertical_unit} "
            f"(metres = raw * {source.vertical_scale_to_m:g} + "
            f"{source.vertical_offset_m:g})\n"
            f"  licence:     {source.license}\n"
            f"  url:         {source.url}\n"
        )
    lines.extend(
        [
            "The PGDA 64/128 ppd MOON_PA GeoTIFFs are the primary global sources;",
            "both are coarser than the 90 m D01 grid, so they are coarse context only.",
            "The NASA SVS TIFFs are a fallback: the files carry no CRS or",
            "geotransform, and 64 ppd (473.8 m) is their finest global grid.",
            "",
            f"PGDA evidence: {PGDA_PRODUCT_PAGE}",
            f"SVS evidence:  {SVS_PAGE}",
            f"NASA licence:  {NASA_LICENSE_URL}",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lola_global_source.py",
        description=(
            "Catalogue, probe and bounded-window reader for verified global Moon "
            "LOLA DEMs. Never downloads a global raster and never writes a manifest."
        ),
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="Print the verified global source catalogue; no network.",
    )
    parser.add_argument(
        "--inspect",
        metavar="SOURCE_ID",
        help="Probe HTTP range support and print GDAL metadata for a source.",
    )
    parser.add_argument(
        "--read",
        metavar="SOURCE_ID",
        help="Read one bounded window and print its statistics and checksum.",
    )
    parser.add_argument("--lon", type=float, help="Window centre longitude in degrees.")
    parser.add_argument("--lat", type=float, help="Window centre latitude in degrees.")
    parser.add_argument(
        "--window-m", type=float, default=5_000.0, help="Square window side in metres."
    )
    parser.add_argument("--pad-m", type=float, default=0.0, help="Extra metres of padding.")
    parser.add_argument(
        "--max-window-mb",
        type=float,
        default=DEFAULT_MAX_WINDOW_BYTES / 1_000_000,
        help="Refuse a window whose conservative transfer estimate exceeds this.",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--dry-run", action="store_true", help="Plan only; read nothing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.list_sources:
            print(source_catalog_text())
            return 0
        if args.inspect:
            payload = inspect_raster(args.inspect, timeout=args.timeout).to_payload()
            print(json.dumps(payload, indent=2))
            return 0
        if args.read:
            if args.lon is None or args.lat is None:
                raise LolaGlobalError("missing_centre", "--read requires --lon and --lat.")
            window = read_bounded_window(
                args.read,
                center_lon=args.lon,
                center_lat=args.lat,
                window_m=args.window_m,
                pad_m=args.pad_m,
                max_window_bytes=int(args.max_window_mb * 1_000_000),
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
            print(json.dumps(window.to_payload(), indent=2))
            return 0
        raise LolaGlobalError("missing_action", "choose --list-sources, --inspect or --read.")
    except LolaGlobalError as exc:
        print(f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
