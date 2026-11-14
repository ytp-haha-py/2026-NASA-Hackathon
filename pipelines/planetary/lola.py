"""Helpers for reading real LOLA 5 m/pix south-pole DEM windows.

Verified source (2026-11-14):

* PGDA product 78, "High-Resolution LOLA Topography for Lunar South Pole Sites"
  https://pgda.gsfc.nasa.gov/products/78
* Site04 (Shackleton rim) surface DEM:
  https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/Site04_final_adj_5mpp_surf.tif

The Site04 LDEM is a 3200 x 3200, 5 m/pix, float32 GeoTIFF in an unnamed lunar south
polar stereographic CRS (sphere R = 1,737,400 m, central meridian 0). The product
documentation says the coordinates use the MOON_ME reference frame of DE421. The file
is a striped, uncompressed GeoTIFF (one row per strip, no overviews), not a COG. GDAL
can still read a bounded window with HTTP range requests because the server advertises
``Accept-Ranges: bytes`` and each requested row is a contiguous strip. This module
bounds the requested window, refuses oversized estimates, retries at most twice, and
caches only the small derived window under ``data/cache/lola`` (gitignored).

The shared extractor is imported, never reimplemented. It transforms the lunar
projected CRS to EPSG:4326 only to report an approximate centre. PROJ refuses that
cross-celestial-body transform unless ``PROJ_IGNORE_CELESTIAL_BODY=YES`` is set, so
this module sets that variable for the process. All four metrics are computed in the
source projected CRS and are unaffected by the override. The exact extractor error
without the override is documented in ``NASA_LOLA_STATUS.md``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import rasterio
from analog_api.domain.manifest import Manifest
from analog_pipelines.features import (
    ExtractionResult,
    make_observations,
)
from pyproj import CRS, Transformer
from rasterio.env import Env
from rasterio.transform import Affine
from rasterio.windows import Window, from_bounds

# The shared extractor needs this to invert the lunar projected CRS to degrees for
# its approximate centre fields. Metrics are computed in the projected CRS.
os.environ.setdefault("PROJ_IGNORE_CELESTIAL_BODY", "YES")

# ---------------------------------------------------------------------------
# Product constants (all URLs and sizes were checked on 2026-11-14).
# ---------------------------------------------------------------------------

LOLA_PRODUCT_PAGE = "https://pgda.gsfc.nasa.gov/products/78"
LOLA_PRODUCT_81_PAGE = "https://pgda.gsfc.nasa.gov/products/81"
LOLA_README_URL = "https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/README"
LOLA_5MPP_BASE = "https://pgda.gsfc.nasa.gov/data/LOLA_5mpp"
SITE04_LDEM_URL = f"{LOLA_5MPP_BASE}/Site04/Site04_final_adj_5mpp_surf.tif"
SITE04_ROI_URL = f"{LOLA_5MPP_BASE}/Site04/Site04_ROIs.tgz"
SITE04_DIR = f"{LOLA_5MPP_BASE}/Site04"

NASA_DATA_LICENSE_URL = "https://science.data.nasa.gov/about/license"
CITATION = (
    "Barker, M.K., et al. (2021), Improved LOLA Elevation Maps for South Pole "
    "Landing Sites: Error Estimates and Their Impact on Illumination Conditions, "
    "Planetary & Space Science 203, 105119, doi:10.1016/j.pss.2020.105119."
)
SECONDARY_CITATION = (
    "Mazarico, E., et al. (2011), Illumination conditions of the lunar polar regions "
    "using LOLA topography, Icarus 211(2), 1066-1081, "
    "doi:10.1016/j.icarus.2010.10.030."
)

DATASET_ID = "nasa-lola-site04-5mpp-surf"
PRODUCT_ID = "Site04_final_adj_5mpp_surf"
PRODUCT_TITLE = "LRO-LOLA 5 m/pix surface-interpolated DEM, Shackleton rim (Site04)"
PROVIDER = "NASA GSFC Planetary Geodesy Data Archive (PGDA) / LRO-LOLA"
LICENSE_NAME = (
    "CC0-1.0 (NASA-led mission data per the NASA Science Data Portal; the PGDA "
    "page requests citation and carries no restrictive notice)"
)
PROCESSING_VERSION = "lola-import-1.0.0+terrain-metrics-1.0.0"
BODY = "Moon"
SOURCE_KIND = "lunar_dem"

ANALYSIS_WINDOW_M = 1980.0
ANALYSIS_RESOLUTION_M = 90.0
CACHE_PAD_M = 45.0
DEFAULT_MAX_WINDOW_BYTES = 30 * 1024 * 1024
DEFAULT_TIMEOUT_S = 180.0
MAX_RETRIES = 2
CACHE_VERSION = "lola-window-1.0.0"
USER_AGENT = "planetary-analog-moon/0.1 (LOLA reference importer)"

FEATURE_IDS = (
    "slope_mean_deg",
    "slope_p90_deg",
    "terrain_relief_p95_p05_m",
    "roughness_std_m",
)
METRE_UNITS = {"m", "metre", "meter", "metres", "meters"}
LUNAR_RADIUS_M = 1_737_400.0

DEFAULT_QUALITY_FLAGS = (
    "5m_grid_interpolated_pixels",
    "official_pgda_lola_site04",
    "source_native_resolution_5m",
    "analysis_resolution_90m",
    "roi_from_official_site04_roi_shapefile",
    "median_rms_z_error_0.30_0.50m",
    "median_rms_slope_error_1.5_2.5deg",
)

# This is the exact CRS WKT read from the real Site04 GeoTIFF. It is also used by
# the offline synthetic-lunar test so the test exercises the lunar CRS branch.
LUNAR_SOUTH_POLAR_STEREOGRAPHIC_WKT = (
    'PROJCS["unnamed",GEOGCS["unnamed ellipse",DATUM["unknown",'
    'SPHEROID["unnamed",1737400,0]],PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]]],'
    'PROJECTION["Polar_Stereographic"],PARAMETER["latitude_of_origin",-90],'
    'PARAMETER["central_meridian",0],PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],UNIT["metre",1],'
    'AXIS["Easting",NORTH],AXIS["Northing",NORTH]]'
)


@dataclass(frozen=True, slots=True)
class LolaProduct:
    product_id: str
    role: str
    url: str
    size_bytes: int | None
    last_modified: str | None
    notes: str = ""


SITE04_PRODUCTS: tuple[LolaProduct, ...] = (
    LolaProduct(
        "Site04_final_adj_5mpp_surf",
        "surface",
        SITE04_LDEM_URL,
        40_980_806,
        "Fri, 05 Jun 2020 18:40:02 GMT",
        "surface-interpolated elevation Z in metres (the source used here)",
    ),
    LolaProduct(
        "Site04_final_adj_5mpp_slp",
        "slope",
        f"{SITE04_DIR}/Site04_final_adj_5mpp_slp.tif",
        40_980_772,
        "Mon, 08 Jun 2020 22:05:53 GMT",
        "official slope map in degrees; not used for the shared four metrics",
    ),
    LolaProduct(
        "Site04_final_adj_5mpp_toterr",
        "toterr",
        f"{SITE04_DIR}/Site04_final_adj_5mpp_toterr.tif",
        40_980_808,
        "Fri, 05 Jun 2020 18:40:15 GMT",
        "total Z uncertainty in metres (RMS error)",
    ),
    LolaProduct(
        "Site04_final_adj_5mpp_slperr",
        "slperr",
        f"{SITE04_DIR}/Site04_final_adj_5mpp_slperr.tif",
        40_980_806,
        "Fri, 05 Jun 2020 18:40:22 GMT",
        "slope uncertainty in degrees (RMS error)",
    ),
    LolaProduct(
        "Site04_final_adj_5mpp_ldec",
        "ldec",
        f"{SITE04_DIR}/Site04_final_adj_5mpp_ldec.tif",
        40_980_740,
        "Fri, 05 Jun 2020 18:40:08 GMT",
        "LOLA return count per 5 m pixel; useful for masking interpolated pixels",
    ),
)


@dataclass(frozen=True, slots=True)
class RoiPreset:
    """A documented ROI centre from the official Site04_ROIs.tgz shapefile.

    The DBF records contain only an X/Y point per ROI (fields ``X`` and ``Y``). The
    row numbers below are 1-based shapefile record numbers; the polygon bounding
    boxes are included as provenance, not as the analysis extent.
    """

    name: str
    row: int
    center_xy: tuple[float, float]
    polygon_bbox_xy: tuple[float, float, float, float]
    provenance: str


SITE04_ROIS: tuple[RoiPreset, ...] = (
    RoiPreset(
        "site04-roi1",
        1,
        (-2812.5, -6647.5),
        (-2842.5, -6802.5, -2782.5, -6522.5),
        "Site04_ROIs.shp record 1; Site04_ROIs.dbf X/Y (-2812.5, -6647.5); "
        "official PGDA Site04_ROIs.tgz",
    ),
    RoiPreset(
        "site04-roi2",
        2,
        (-2757.5, -6342.5),
        (-2792.5, -6432.5, -2712.5, -6262.5),
        "Site04_ROIs.shp record 2; Site04_ROIs.dbf X/Y (-2757.5, -6342.5); "
        "official PGDA Site04_ROIs.tgz",
    ),
    RoiPreset(
        "site04-roi3",
        3,
        (-2677.5, -6107.5),
        (-2702.5, -6167.5, -2642.5, -6052.5),
        "Site04_ROIs.shp record 3; Site04_ROIs.dbf X/Y (-2677.5, -6107.5); "
        "official PGDA Site04_ROIs.tgz",
    ),
    RoiPreset(
        "site04-roi4",
        4,
        (-2657.5, -5957.5),
        (-2687.5, -6027.5, -2627.5, -5902.5),
        "Site04_ROIs.shp record 4; Site04_ROIs.dbf X/Y (-2657.5, -5957.5); "
        "official PGDA Site04_ROIs.tgz",
    ),
)

DEFAULT_ROI = SITE04_ROIS[1]


class LolaImportError(RuntimeError):
    """A real ingestion problem. Never replaced with synthetic values."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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


@dataclass(frozen=True, slots=True)
class SourceMeta:
    source: str
    source_url: str | None
    source_is_remote: bool
    source_size_bytes: int | None
    etag: str | None
    last_modified: str | None
    driver: str
    width: int
    height: int
    count: int
    dtype: str
    nodata: float | None
    crs_wkt: str
    crs_proj4: str
    is_projected: bool
    axis_unit: str
    ellipsoid_radius_m: float
    res_x: float
    res_y: float
    native_resolution_m: float
    bounds: tuple[float, float, float, float]
    transform: tuple[float, float, float, float, float, float]
    block_shapes: tuple[tuple[int, int], ...]
    is_tiled: bool
    compress: str | None
    overviews: tuple[int, ...]
    accept_ranges: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedWindow:
    meta: SourceMeta
    body: str
    source_kind: str
    center_xy: tuple[float, float]
    analysis_bounds_xy: tuple[float, float, float, float]
    crop_bounds_xy: tuple[float, float, float, float]
    crop_shape: tuple[int, int]
    estimated_range_bytes: int
    cache_path: Path | None
    cache_sha256: str | None
    cache_bytes: int | None
    access_timestamp: str | None
    source_valid_fraction: float | None
    from_cache: bool

    @property
    def source_label(self) -> str:
        return self.meta.source_url or self.meta.source

    @property
    def center_lonlat(self) -> tuple[float, float]:
        crs = CRS.from_wkt(self.meta.crs_wkt)
        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(*self.center_xy)
        return float(lon), float(lat)


@dataclass(frozen=True, slots=True)
class DatasetInfo:
    dataset_id: str
    provider: str
    product_id: str
    title: str
    url: str
    license: str
    license_url: str
    access_timestamp: str
    source_crs: str
    native_resolution_m: float
    processing_version: str
    checksum: str | None
    source_kind: str
    notes: str


class PlanetaryReferenceProvider(Protocol):
    """Extension point for other bodies.

    A real implementation must return a :class:`PreparedWindow` backed by a real
    raster it actually read, with the same bounded-window and cache semantics.
    """

    body: str
    source_kind: str

    def prepare(
        self,
        *,
        source: str,
        center_xy: tuple[float, float],
        cache_dir: Path,
        **kwargs: Any,
    ) -> PreparedWindow: ...


class LolaReferenceProvider:
    """Moon provider: read a bounded window from a real LOLA 5 m/pix GeoTIFF."""

    body = BODY
    source_kind = SOURCE_KIND

    def prepare(
        self,
        *,
        source: str,
        center_xy: tuple[float, float],
        cache_dir: Path,
        **kwargs: Any,
    ) -> PreparedWindow:
        return prepare_window(source, center_xy, cache_dir, **kwargs)


class MolaReferenceProvider:
    """Future Mars adapter. Interface only: no MOLA data is read or claimed.

    To implement it, return a ``PreparedWindow`` whose ``meta.crs_wkt`` is a real
    Mars projected metric CRS, whose ``body`` is ``"Mars"``, whose ``source_kind``
    is ``"martian_dem"``, and whose cache was produced from a real MOLA product.
    Do not reuse the lunar constants below for Mars.
    """

    body = "Mars"
    source_kind = "martian_dem"

    def prepare(self, **kwargs: Any) -> PreparedWindow:
        raise LolaImportError(
            "mola_not_implemented",
            "MOLA/Mars support is an extension point only; no Mars raster has been "
            "inspected, downloaded, or claimed. Implement MolaReferenceProvider "
            "against a real MOLA product before emitting a Mars reference.",
        )


PROVIDERS: dict[str, type[PlanetaryReferenceProvider]] = {
    "Moon": LolaReferenceProvider,
    "Mars": MolaReferenceProvider,
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_cache_dir() -> Path:
    return repo_root() / "data" / "cache" / "lola"


def default_manifest_path() -> Path:
    return repo_root() / "pipelines" / "planetary" / "manifests" / "lola_moon_reference.json"


def default_schema_path() -> Path:
    return repo_root() / "contracts" / "pipeline-manifest.schema.json"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} MB ({value:,} bytes)"
    if value >= 1_000:
        return f"{value / 1_000:.2f} kB ({value:,} bytes)"
    return f"{value:,} bytes"


def product_catalog_text() -> str:
    lines = [
        "LOLA 5 m/pix south-pole DEM products verified from PGDA product 78:",
        f"  product page: {LOLA_PRODUCT_PAGE}",
        f"  README:       {LOLA_README_URL}",
        "",
        "Site04 (Shackleton rim) GeoTIFFs:",
    ]
    for product in SITE04_PRODUCTS:
        lines.append(
            f"  {product.role:7s} {product.product_id:34s} "
            f"{format_bytes(product.size_bytes):>23s}  {product.url}"
        )
    lines.extend(
        [
            "",
            f"  ROI vector archive: {SITE04_ROI_URL} (1,712 bytes)",
            "  Official Site04 ROI centres (DBF X/Y, source metres):",
        ]
    )
    for roi in SITE04_ROIS:
        lines.append(f"    {roi.name}: X={roi.center_xy[0]:.1f} Y={roi.center_xy[1]:.1f}")
    lines.extend(
        [
            "",
            "All Site04 rasters are 5 m/pix, 3200 x 3200, float32, nodata=NaN,",
            "south polar stereographic metres, MOON_ME/DE421 per the product page.",
            "They are striped uncompressed GeoTIFFs (not COGs, no overviews), but the",
            "server supports HTTP range reads and GDAL can read a bounded row window.",
            "",
            f"Citation: {CITATION}",
            f"License:  {LICENSE_NAME}",
            f"          {NASA_DATA_LICENSE_URL}",
            "",
            "The 87-90 S 5 m mosaic (PGDA product 81) is about 3.3 GB and is NOT used",
            f"by this importer: {LOLA_PRODUCT_81_PAGE}",
        ]
    )
    return "\n".join(lines)


def parse_source(source: str) -> SourceRef:
    """Resolve an HTTP(S) URL, ``/vsicurl/`` URL, ``file://`` URL, or local path."""
    raw = source.strip()
    if not raw:
        raise LolaImportError("empty_source", "The source URL/path is empty.")

    if raw.startswith("/vsicurl/"):
        url = raw[len("/vsicurl/") :]
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise LolaImportError(
                "invalid_source_url",
                f"{raw!r} is not a valid /vsicurl/ HTTP(S) URL.",
            )
        return SourceRef(raw=raw, is_remote=True, url=url, path=None, gdal_path=raw)

    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme in ("http", "https"):
        if not parsed.netloc:
            raise LolaImportError("invalid_source_url", f"{raw!r} has no host component.")
        return SourceRef(
            raw=raw,
            is_remote=True,
            url=raw,
            path=None,
            gdal_path=f"/vsicurl/{raw}",
        )
    if parsed.scheme == "file":
        path = Path(urllib.request.url2pathname(parsed.path)).expanduser()
        return SourceRef(raw=raw, is_remote=False, url=None, path=path, gdal_path=str(path))
    if parsed.scheme and len(parsed.scheme) > 1:
        raise LolaImportError(
            "unsupported_source_scheme",
            f"Unsupported source scheme {parsed.scheme!r} in {raw!r}; "
            "use http(s), file://, /vsicurl/, or a local path.",
        )
    path = Path(raw).expanduser()
    return SourceRef(raw=raw, is_remote=False, url=None, path=path, gdal_path=str(path))


def _range_probe(
    url: str,
    *,
    timeout: float,
    opener: Callable[..., Any],
) -> tuple[int, bool]:
    request = urllib.request.Request(
        url,
        headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT},
    )
    with opener(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200))
        # Do not read a 200 response body: the server ignored Range and could send
        # the entire object. Exiting the context closes the connection.
        return status, status == 206


def probe_remote(
    url: str,
    *,
    timeout: float = 30.0,
    max_retries: int = MAX_RETRIES,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> RemoteProbe:
    """HEAD the object, then verify range support with a 1-byte GET."""
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with opener(request, timeout=timeout) as response:
                headers = response.headers
                length_text = headers.get("Content-Length")
                content_length = int(length_text) if length_text else None
                head_status = int(getattr(response, "status", 200))
                etag = headers.get("ETag")
                last_modified = headers.get("Last-Modified")
            try:
                range_status, range_ok = _range_probe(url, timeout=timeout, opener=opener)
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 404):
                    raise LolaImportError("source_not_found", f"HTTP {exc.code} for {url}") from exc
                range_status, range_ok = head_status, False
            return RemoteProbe(
                url=url,
                status=head_status if range_status != 206 else range_status,
                content_length=content_length,
                # Trust the verified 206, not the accepted HEAD advertisement: a
                # server can advertise bytes and still ignore Range.
                accept_ranges=range_ok,
                range_verified=range_ok,
                etag=etag,
                last_modified=last_modified,
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise LolaImportError(
                    "source_not_found",
                    f"HTTP {exc.code} for {url}; the documented LOLA product URL "
                    "may have moved. Check the product page before substituting a URL.",
                ) from exc
            if exc.code in (405, 501):
                try:
                    range_status, range_ok = _range_probe(url, timeout=timeout, opener=opener)
                    return RemoteProbe(
                        url=url,
                        status=range_status,
                        content_length=None,
                        accept_ranges=range_ok,
                        range_verified=range_ok,
                        etag=None,
                        last_modified=None,
                    )
                except urllib.error.HTTPError as range_exc:
                    if range_exc.code in (403, 404):
                        raise LolaImportError(
                            "source_not_found", f"HTTP {range_exc.code} for {url}"
                        ) from range_exc
                    last_error = range_exc
                except (urllib.error.URLError, TimeoutError, OSError) as range_exc:
                    last_error = range_exc
            else:
                last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < max_retries:
            sleeper(1.5 * (attempt + 1))
    raise LolaImportError(
        "source_unreachable",
        f"Cannot reach {url} after {max_retries + 1} attempt(s): {last_error}",
    )


def rasterio_remote_env(timeout: float = DEFAULT_TIMEOUT_S) -> Env:
    """Bounded GDAL settings for unsigned HTTPS range reads."""
    return Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
        CPL_VSIL_CURL_USE_HEAD="YES",
        CPL_VSIL_CURL_MAX_RETRY=str(MAX_RETRIES),
        GDAL_HTTP_MAX_RETRY=str(MAX_RETRIES),
        GDAL_HTTP_TIMEOUT=str(max(1, int(timeout))),
        GDAL_HTTP_CONNECTTIMEOUT=str(min(20, max(1, int(timeout)))),
        GDAL_HTTP_USERAGENT=USER_AGENT,
        VSI_CACHE="TRUE",
        VSI_CACHE_SIZE="33554432",
    )


def validate_source_crs(crs: CRS, label: str) -> list[str]:
    """Reject geographic, non-metre, and non-lunar DEM CRSs; return warnings."""
    if not crs.is_projected:
        raise LolaImportError(
            "geographic_crs",
            f"{label}: source CRS is geographic ({crs.to_string()}). The shared "
            "extractor requires a projected metric CRS; supply a projected LOLA DEM.",
        )
    if not crs.axis_info:
        raise LolaImportError("missing_crs_units", f"{label}: source CRS has no axis units.")
    unit = crs.axis_info[0].unit_name.lower()
    if unit not in METRE_UNITS:
        raise LolaImportError(
            "non_metric_crs",
            f"{label}: projected source units are {unit!r}, expected metres.",
        )
    radius = crs.ellipsoid.semi_major_metre
    if radius is None or not (1_700_000.0 <= radius <= 1_800_000.0):
        raise LolaImportError(
            "not_a_lunar_crs",
            f"{label}: ellipsoid semi-major radius is {radius!r} m, not a lunar "
            f"radius near {LUNAR_RADIUS_M:,.0f} m. Refusing to label a non-lunar "
            "DEM as a Moon reference.",
        )

    warnings: list[str] = []
    operation = crs.coordinate_operation
    method = operation.method_name.lower() if operation is not None else ""
    if "stereographic" not in method or "polar" not in method:
        warnings.append("source_projection_not_polar_stereographic")
    return warnings


def _crs_proj4(crs: CRS) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return crs.to_proj4()


def _metadata_from_dataset(
    src: rasterio.io.DatasetReader,
    ref: SourceRef,
    probe: RemoteProbe | None,
) -> SourceMeta:
    if src.crs is None:
        raise LolaImportError("missing_crs", f"{ref.raw}: source raster has no CRS.")
    crs = CRS.from_user_input(src.crs)
    warnings_list = validate_source_crs(crs, ref.raw)

    if src.width < 3 or src.height < 3:
        raise LolaImportError(
            "source_too_small",
            f"{ref.raw}: source is {src.width} x {src.height} pixels; at least 3 x 3 "
            "is required by the shared extractor.",
        )
    if src.count < 1:
        raise LolaImportError("no_bands", f"{ref.raw}: source raster has no bands.")
    if src.count != 1:
        warnings_list.append("multiple_bands_using_band_1")

    nodata = float(src.nodata) if src.nodata is not None else None
    if nodata is None:
        warnings_list.append("source_nodata_undefined")

    is_tiled = bool(src.profile.get("tiled", False))
    compress = src.profile.get("compress")
    overviews = tuple(int(value) for value in src.overviews(1))
    if not is_tiled:
        warnings_list.append("source_striped_not_cog")
    if not overviews:
        warnings_list.append("source_has_no_overviews")
    if compress is None:
        warnings_list.append("source_uncompressed")
    if src.driver != "GTiff":
        warnings_list.append(f"source_driver_{src.driver.lower()}")

    res_x, res_y = float(abs(src.res[0])), float(abs(src.res[1]))
    if not math.isclose(res_x, res_y, rel_tol=1e-9, abs_tol=1e-9):
        warnings_list.append("non_square_source_pixels")
    native_resolution = (res_x + res_y) / 2.0
    if native_resolution > ANALYSIS_RESOLUTION_M:
        warnings_list.append("source_coarser_than_90m")

    bounds = src.bounds
    transform = tuple(float(value) for value in tuple(src.transform)[:6])
    block_shapes = tuple((int(shape[0]), int(shape[1])) for shape in src.block_shapes)
    if not block_shapes:
        raise LolaImportError("unknown_block_layout", f"{ref.raw}: GDAL reported no block layout.")

    if ref.is_remote:
        size_bytes = probe.content_length if probe is not None else None
        accept_ranges = bool(probe.accept_ranges) if probe is not None else False
        etag = probe.etag if probe is not None else None
        last_modified = probe.last_modified if probe is not None else None
        source_url = ref.url
    else:
        size_bytes = ref.path.stat().st_size if ref.path and ref.path.is_file() else None
        accept_ranges = True
        etag = None
        last_modified = None
        source_url = None

    return SourceMeta(
        source=ref.raw,
        source_url=source_url,
        source_is_remote=ref.is_remote,
        source_size_bytes=size_bytes,
        etag=etag,
        last_modified=last_modified,
        driver=src.driver,
        width=int(src.width),
        height=int(src.height),
        count=int(src.count),
        dtype=src.dtypes[0],
        nodata=nodata,
        crs_wkt=crs.to_wkt(),
        crs_proj4=_crs_proj4(crs),
        is_projected=bool(crs.is_projected),
        axis_unit=crs.axis_info[0].unit_name if crs.axis_info else "",
        ellipsoid_radius_m=float(crs.ellipsoid.semi_major_metre),
        res_x=res_x,
        res_y=res_y,
        native_resolution_m=float(native_resolution),
        bounds=(float(bounds.left), float(bounds.bottom), float(bounds.right), float(bounds.top)),
        transform=transform,  # type: ignore[arg-type]
        block_shapes=block_shapes,
        is_tiled=is_tiled,
        compress=compress,
        overviews=overviews,
        accept_ranges=accept_ranges,
        warnings=tuple(dict.fromkeys(warnings_list)),
    )


def inspect_source(
    ref: SourceRef,
    probe: RemoteProbe | None,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    sleeper: Callable[[float], None] = time.sleep,
) -> SourceMeta:
    """Open the source metadata with bounded retries; read no analysis window."""
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with rasterio_remote_env(timeout), rasterio.open(ref.gdal_path) as src:
                return _metadata_from_dataset(src, ref, probe)
        except LolaImportError:
            raise
        except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                sleeper(1.5 * (attempt + 1))
    raise LolaImportError(
        "source_open_failed",
        f"Cannot open {ref.raw} after {max_retries + 1} attempt(s): {last_error}",
    )


def bounds_for_center(
    center_xy: tuple[float, float], half_m: float
) -> tuple[float, float, float, float]:
    cx, cy = center_xy
    return (cx - half_m, cy - half_m, cx + half_m, cy + half_m)


def bounds_contain(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    tolerance: float = 0.0,
) -> bool:
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )


def resolve_center(
    *,
    center_x: float | None = None,
    center_y: float | None = None,
    bbox: tuple[float, float, float, float] | list[float] | None = None,
    default: tuple[float, float] = DEFAULT_ROI.center_xy,
) -> tuple[float, float]:
    """Resolve an explicit source X/Y, the centre of a source bbox, or the default."""
    if bbox is not None:
        if center_x is not None or center_y is not None:
            raise LolaImportError(
                "ambiguous_center",
                "Provide either --center-x/--center-y or --bbox, not both.",
            )
        if len(bbox) != 4:
            raise LolaImportError(
                "invalid_bbox", "The bbox must have exactly four values: xmin ymin xmax ymax."
            )
        xmin, ymin, xmax, ymax = (float(value) for value in bbox)
        if not all(math.isfinite(value) for value in (xmin, ymin, xmax, ymax)):
            raise LolaImportError("invalid_bbox", "The bbox values must be finite.")
        if xmax <= xmin or ymax <= ymin:
            raise LolaImportError(
                "invalid_bbox",
                f"Invalid bbox ({xmin}, {ymin}, {xmax}, {ymax}); "
                "expected xmax > xmin and ymax > ymin.",
            )
        return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)

    if (center_x is None) != (center_y is None):
        raise LolaImportError(
            "incomplete_center",
            "Provide both --center-x and --center-y, or neither.",
        )
    if center_x is None:
        return default
    assert center_y is not None
    if not math.isfinite(float(center_x)) or not math.isfinite(float(center_y)):
        raise LolaImportError("invalid_center", "The centre coordinates must be finite.")
    return (float(center_x), float(center_y))


def source_window(meta: SourceMeta, bounds: tuple[float, float, float, float]) -> Window:
    transform = Affine(*meta.transform)
    window = from_bounds(*bounds, transform).round_offsets().round_lengths()
    col_off = max(0, min(int(window.col_off), meta.width - 1))
    row_off = max(0, min(int(window.row_off), meta.height - 1))
    width = min(int(window.width), meta.width - col_off)
    height = min(int(window.height), meta.height - row_off)
    if width < 3 or height < 3:
        raise LolaImportError(
            "window_too_small",
            f"{meta.source}: computed source window is {width} x {height} pixels.",
        )
    return Window(col_off, row_off, width, height)


def estimate_window_bytes(
    window: Window,
    *,
    width: int,
    height: int,
    block_shapes: tuple[tuple[int, int], ...],
    is_tiled: bool,
    dtype: str,
    source_size_bytes: int | None = None,
) -> int:
    """Conservative upper bound on the bytes needed to read a window.

    For the uncompressed striped LOLA GeoTIFF a window read touches whole rows:
    414 rows x 3200 px x 4 B is about 5.3 MB. For tiled/compressed sources this is
    the uncompressed byte count of the touched blocks, an upper bound on transfer.
    """
    if not block_shapes:
        raise LolaImportError("unknown_block_layout", "Cannot estimate window bytes.")
    if window.width <= 0 or window.height <= 0:
        raise LolaImportError("invalid_window", "Window width and height must be positive.")
    block_h, block_w = block_shapes[0]
    if block_h <= 0 or block_w <= 0:
        raise LolaImportError("invalid_block_layout", "Block dimensions must be positive.")
    try:
        itemsize = np.dtype(dtype).itemsize
    except TypeError as exc:
        raise LolaImportError("invalid_dtype", f"Unsupported source dtype {dtype!r}.") from exc

    if is_tiled:
        row0 = math.floor(window.row_off / block_h)
        row1 = math.ceil((window.row_off + window.height) / block_h)
        col0 = math.floor(window.col_off / block_w)
        col1 = math.ceil((window.col_off + window.width) / block_w)
        blocks = max(0, row1 - row0) * max(0, col1 - col0)
        block_bytes = block_w * block_h * itemsize
    else:
        # Strips span the full raster width; one block covers ``block_h`` rows.
        rows = max(
            0,
            math.ceil((window.row_off + window.height) / block_h)
            - math.floor(window.row_off / block_h),
        )
        block_bytes = max(block_w, width) * block_h * itemsize
        blocks = rows
    estimate = blocks * block_bytes + 128 * 1024
    if source_size_bytes is not None:
        estimate = min(estimate, source_size_bytes + 128 * 1024)
    return int(estimate)


def cache_paths(
    cache_dir: Path,
    source: str,
    center_xy: tuple[float, float],
    pad_m: float = CACHE_PAD_M,
) -> tuple[Path, Path]:
    key = hashlib.sha256(
        f"{source}|{center_xy[0]:.3f}|{center_xy[1]:.3f}|{pad_m:.3f}".encode()
    ).hexdigest()[:20]
    return (
        cache_dir / f"lola-window-{key}.tif",
        cache_dir / f"lola-window-{key}.json",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _meta_to_dict(meta: SourceMeta) -> dict[str, Any]:
    data = asdict(meta)
    nodata = meta.nodata
    if nodata is not None and math.isnan(nodata):
        data["nodata"] = "nan"
    return data


def _meta_from_dict(data: dict[str, Any]) -> SourceMeta:
    nodata_raw = data.get("nodata")
    if nodata_raw == "nan":
        nodata = float("nan")
    elif nodata_raw is None:
        nodata = None
    else:
        nodata = float(nodata_raw)
    return SourceMeta(
        source=str(data["source"]),
        source_url=data.get("source_url"),
        source_is_remote=bool(data["source_is_remote"]),
        source_size_bytes=(
            int(data["source_size_bytes"]) if data.get("source_size_bytes") is not None else None
        ),
        etag=data.get("etag"),
        last_modified=data.get("last_modified"),
        driver=str(data["driver"]),
        width=int(data["width"]),
        height=int(data["height"]),
        count=int(data["count"]),
        dtype=str(data["dtype"]),
        nodata=nodata,
        crs_wkt=str(data["crs_wkt"]),
        crs_proj4=str(data["crs_proj4"]),
        is_projected=bool(data["is_projected"]),
        axis_unit=str(data["axis_unit"]),
        ellipsoid_radius_m=float(data["ellipsoid_radius_m"]),
        res_x=float(data["res_x"]),
        res_y=float(data["res_y"]),
        native_resolution_m=float(data["native_resolution_m"]),
        bounds=tuple(float(value) for value in data["bounds"]),  # type: ignore[arg-type]
        transform=tuple(float(value) for value in data["transform"]),  # type: ignore[arg-type]
        block_shapes=tuple((int(shape[0]), int(shape[1])) for shape in data["block_shapes"]),
        is_tiled=bool(data["is_tiled"]),
        compress=data.get("compress"),
        overviews=tuple(int(value) for value in data.get("overviews", [])),
        accept_ranges=bool(data["accept_ranges"]),
        warnings=tuple(str(value) for value in data.get("warnings", [])),
    )


def _prepared_from_sidecar(data: dict[str, Any], cache_path: Path) -> PreparedWindow:
    meta = _meta_from_dict(data["source_meta"])
    return PreparedWindow(
        meta=meta,
        body=str(data["body"]),
        source_kind=str(data["source_kind"]),
        center_xy=(
            float(data["center_xy"][0]),
            float(data["center_xy"][1]),
        ),
        analysis_bounds_xy=tuple(float(value) for value in data["analysis_bounds_xy"]),  # type: ignore[arg-type]
        crop_bounds_xy=tuple(float(value) for value in data["crop_bounds_xy"]),  # type: ignore[arg-type]
        crop_shape=(int(data["crop_shape"][0]), int(data["crop_shape"][1])),
        estimated_range_bytes=int(data["estimated_range_bytes"]),
        cache_path=cache_path,
        cache_sha256=str(data["cache_sha256"]),
        cache_bytes=int(data["cache_bytes"]),
        access_timestamp=str(data["access_timestamp"]),
        source_valid_fraction=(
            float(data["source_valid_fraction"])
            if data.get("source_valid_fraction") is not None
            else None
        ),
        from_cache=True,
    )


def load_cached_window(
    cache_path: Path,
    metadata_path: Path,
    ref: SourceRef,
    center_xy: tuple[float, float],
) -> PreparedWindow | None:
    """Return a verified cache entry, or None when a fresh read is required."""
    if not cache_path.is_file() or not metadata_path.is_file():
        return None
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("cache_version") != CACHE_VERSION:
        return None
    if data.get("source") != ref.raw:
        return None
    if bool(data.get("source_is_remote")) != ref.is_remote:
        return None
    try:
        center = data["center_xy"]
        if not (
            math.isclose(float(center[0]), center_xy[0], rel_tol=0.0, abs_tol=1e-6)
            and math.isclose(float(center[1]), center_xy[1], rel_tol=0.0, abs_tol=1e-6)
        ):
            return None
        if not math.isclose(float(data["pad_m"]), CACHE_PAD_M, rel_tol=0.0, abs_tol=1e-6):
            return None
        expected_digest = str(data["cache_sha256"])
    except (KeyError, TypeError, ValueError):
        return None
    if sha256_file(cache_path) != expected_digest:
        return None
    try:
        prepared = _prepared_from_sidecar(data, cache_path)
        cache_crs = CRS.from_wkt(prepared.meta.crs_wkt)
        analysis_bounds = prepared.analysis_bounds_xy
        with rasterio.open(cache_path) as src:
            if src.crs is None or not CRS.from_user_input(src.crs).equals(cache_crs):
                return None
            cache_bounds = (
                float(src.bounds.left),
                float(src.bounds.bottom),
                float(src.bounds.right),
                float(src.bounds.top),
            )
            if not bounds_contain(cache_bounds, analysis_bounds, tolerance=1e-6):
                return None
            if (src.height, src.width) != prepared.crop_shape:
                return None
            if src.width < 3 or src.height < 3:
                return None
    except (
        KeyError,
        TypeError,
        ValueError,
        LolaImportError,
        rasterio.errors.RasterioError,
        OSError,
    ):
        return None
    return prepared


def fetch_window(
    ref: SourceRef,
    window: Window,
    cache_path: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[str, int, float, str]:
    """Range-read a bounded window and write the small local cache GeoTIFF."""
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        temporary = cache_path.with_name(f"{cache_path.name}.part-{os.getpid()}")
        try:
            with rasterio_remote_env(timeout), rasterio.open(ref.gdal_path) as src:
                data = src.read(
                    1,
                    window=window,
                    boundless=True,
                    masked=True,
                    out_dtype="float64",
                    fill_value=np.nan,
                )
                array = data.filled(np.nan) if np.ma.isMaskedArray(data) else np.asarray(data)
                array = np.asarray(array, dtype="float64")
                array = np.where(np.isfinite(array), array, np.nan).astype("float32")
                transform = src.window_transform(window)
                crs = src.crs
            if np.isfinite(array).sum() == 0:
                raise LolaImportError(
                    "no_valid_elevation",
                    f"{ref.raw}: the requested window contains no finite elevation cells.",
                )
            valid_fraction = float(np.isfinite(array).mean())
            if valid_fraction < 0.8:
                raise LolaImportError(
                    "insufficient_valid_data",
                    f"{ref.raw}: only {valid_fraction:.2%} of the source window has "
                    "finite elevation; the shared extractor requires at least 80%.",
                )
            profile = {
                "driver": "GTiff",
                "height": int(array.shape[0]),
                "width": int(array.shape[1]),
                "count": 1,
                "dtype": "float32",
                "crs": crs,
                "transform": transform,
                "nodata": float("nan"),
                "compress": "deflate",
                "predictor": 2,
                "tiled": False,
            }
            with rasterio.open(temporary, "w", **profile) as dst:
                dst.write(array, 1)
            digest = sha256_file(temporary)
            os.replace(temporary, cache_path)
            return digest, cache_path.stat().st_size, valid_fraction, utc_now_iso()
        except LolaImportError:
            temporary.unlink(missing_ok=True)
            raise
        except (rasterio.errors.RasterioError, OSError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt < max_retries:
                sleeper(1.5 * (attempt + 1))
    raise LolaImportError(
        "window_read_failed",
        f"{ref.raw}: window read failed after {max_retries + 1} attempt(s): {last_error}",
    )


def write_json_atomic(document: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.part-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def prepare_window(
    source: str,
    center_xy: tuple[float, float],
    cache_dir: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    max_window_bytes: int = DEFAULT_MAX_WINDOW_BYTES,
    refresh: bool = False,
    dry_run: bool = False,
    sleeper: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> PreparedWindow:
    """Prepare a bounded LOLA window, using a verified cache when available."""
    if timeout <= 0:
        raise LolaImportError("invalid_timeout", "The timeout must be positive.")
    if max_retries < 0 or max_retries > MAX_RETRIES:
        raise LolaImportError("invalid_retries", f"Retries must be between 0 and {MAX_RETRIES}.")
    if max_window_bytes <= 0:
        raise LolaImportError("invalid_byte_budget", "The byte budget must be positive.")

    ref = parse_source(source)
    cache_path, metadata_path = cache_paths(cache_dir, ref.raw, center_xy)

    if not refresh:
        cached = load_cached_window(cache_path, metadata_path, ref, center_xy)
        if cached is not None:
            return cached

    probe: RemoteProbe | None = None
    if ref.is_remote:
        assert ref.url is not None
        probe = probe_remote(
            ref.url,
            timeout=min(timeout, 60.0),
            max_retries=max_retries,
            opener=opener,
            sleeper=sleeper,
        )
        if not probe.accept_ranges:
            if probe.content_length is None:
                raise LolaImportError(
                    "range_read_unsupported",
                    f"{ref.url}: the server does not advertise byte ranges and did not "
                    "return HTTP 206 for a 1-byte range request, so a bounded remote "
                    "read cannot be guaranteed.",
                )
            if probe.content_length > max_window_bytes:
                raise LolaImportError(
                    "range_read_unsupported",
                    f"{ref.url}: no HTTP range support and the object is "
                    f"{format_bytes(probe.content_length)}, above the "
                    f"{format_bytes(max_window_bytes)} budget. Refusing a full download.",
                )
    else:
        assert ref.path is not None
        if not ref.path.is_file():
            raise LolaImportError("source_not_found", f"Local source does not exist: {ref.path}")

    meta = inspect_source(
        ref,
        probe,
        timeout=min(timeout, 120.0),
        max_retries=max_retries,
        sleeper=sleeper,
    )

    analysis_bounds = bounds_for_center(center_xy, ANALYSIS_WINDOW_M / 2.0)
    if not bounds_contain(meta.bounds, analysis_bounds, tolerance=1e-6):
        raise LolaImportError(
            "window_outside_source",
            f"{ref.raw}: the 1980 m analysis window centred at {center_xy} extends "
            f"outside the source bounds {meta.bounds}. Choose a centre inside the "
            "documented product footprint.",
        )
    requested_crop = bounds_for_center(center_xy, ANALYSIS_WINDOW_M / 2.0 + CACHE_PAD_M)
    crop_bounds = (
        max(requested_crop[0], meta.bounds[0]),
        max(requested_crop[1], meta.bounds[1]),
        min(requested_crop[2], meta.bounds[2]),
        min(requested_crop[3], meta.bounds[3]),
    )
    if not bounds_contain(crop_bounds, analysis_bounds, tolerance=1e-6):
        raise LolaImportError(
            "window_outside_source",
            f"{ref.raw}: a padded crop cannot cover the 1980 m analysis window "
            f"centred at {center_xy}. Choose a centre at least "
            f"{ANALYSIS_WINDOW_M / 2.0 + CACHE_PAD_M:.0f} m inside the source bounds.",
        )
    window = source_window(meta, crop_bounds)
    estimate = estimate_window_bytes(
        window,
        width=meta.width,
        height=meta.height,
        block_shapes=meta.block_shapes,
        is_tiled=meta.is_tiled,
        dtype=meta.dtype,
        source_size_bytes=meta.source_size_bytes,
    )
    if estimate > max_window_bytes:
        raise LolaImportError(
            "window_too_large",
            f"{ref.raw}: estimated range-read for the {window.width} x {window.height} "
            f"source window is {format_bytes(estimate)}, above the "
            f"{format_bytes(max_window_bytes)} budget. Move the ROI or raise the budget.",
        )
    if meta.source_is_remote and not meta.accept_ranges and meta.source_size_bytes is None:
        raise LolaImportError(
            "range_read_unsupported",
            f"{ref.raw}: no byte-range support and unknown object size; refusing an "
            "unbounded download.",
        )

    if dry_run:
        return PreparedWindow(
            meta=meta,
            body=BODY,
            source_kind=SOURCE_KIND,
            center_xy=center_xy,
            analysis_bounds_xy=analysis_bounds,
            crop_bounds_xy=crop_bounds,
            crop_shape=(window.height, window.width),
            estimated_range_bytes=estimate,
            cache_path=None,
            cache_sha256=None,
            cache_bytes=None,
            access_timestamp=None,
            source_valid_fraction=None,
            from_cache=False,
        )

    if not ref.is_remote and not meta.accept_ranges:
        raise LolaImportError(
            "range_read_unsupported",
            f"{ref.raw}: local source was not readable with the expected layout.",
        )

    cache_dir.mkdir(parents=True, exist_ok=True)
    digest, cache_bytes, source_valid_fraction, access_timestamp = fetch_window(
        ref,
        window,
        cache_path,
        timeout=timeout,
        max_retries=max_retries,
        sleeper=sleeper,
    )
    prepared = PreparedWindow(
        meta=meta,
        body=BODY,
        source_kind=SOURCE_KIND,
        center_xy=center_xy,
        analysis_bounds_xy=analysis_bounds,
        crop_bounds_xy=crop_bounds,
        crop_shape=(window.height, window.width),
        estimated_range_bytes=estimate,
        cache_path=cache_path,
        cache_sha256=digest,
        cache_bytes=cache_bytes,
        access_timestamp=access_timestamp,
        source_valid_fraction=source_valid_fraction,
        from_cache=False,
    )
    sidecar = {
        "cache_version": CACHE_VERSION,
        "body": prepared.body,
        "source_kind": prepared.source_kind,
        "source": ref.raw,
        "source_is_remote": ref.is_remote,
        "center_xy": [center_xy[0], center_xy[1]],
        "pad_m": CACHE_PAD_M,
        "analysis_bounds_xy": list(analysis_bounds),
        "crop_bounds_xy": list(crop_bounds),
        "crop_shape": [window.height, window.width],
        "estimated_range_bytes": estimate,
        "cache_sha256": digest,
        "cache_bytes": cache_bytes,
        "access_timestamp": access_timestamp,
        "source_valid_fraction": source_valid_fraction,
        "source_meta": _meta_to_dict(meta),
    }
    write_json_atomic(sidecar, metadata_path)
    return prepared


def default_dataset_info(
    prepared: PreparedWindow,
    *,
    dataset_id: str = DATASET_ID,
    product_id: str = PRODUCT_ID,
    title: str = PRODUCT_TITLE,
    provider: str = PROVIDER,
    citation: str = CITATION,
) -> DatasetInfo:
    meta = prepared.meta
    checksum = f"sha256:{prepared.cache_sha256}" if prepared.cache_sha256 else None
    layout = (
        f"{'tiled' if meta.is_tiled else 'striped'} "
        f"{meta.block_shapes[0][0]}x{meta.block_shapes[0][1]} blocks, "
        f"{meta.compress or 'uncompressed'}, "
        f"overviews={list(meta.overviews) or 'none'}"
    )
    access_text = "via HTTP range requests" if meta.source_is_remote else "from a local GeoTIFF"
    notes = (
        f"NASA PGDA LRO-LOLA product {product_id!r} ({provider}); "
        f"{meta.width}x{meta.height} px, {meta.native_resolution_m:.1f} m/pix, "
        f"{meta.dtype}, nodata={'NaN' if meta.nodata is None or math.isnan(meta.nodata) else meta.nodata}, "
        f"{layout}. Product documentation: MOON_ME reference frame of DE421, "
        "south polar stereographic X/Y in metres, pixel-registered GMT convention. "
        "The product page states that about 90% of the 5 m pixels are interpolated "
        "because LOLA cross-track/inter-spot spacing leaves only about 10% direct "
        "returns; median RMS Z error is about 0.30-0.50 m and median RMS slope error "
        "about 1.5-2.5 degrees. Track geolocation uncertainty is about 10-20 cm "
        f"horizontally and 2-4 cm vertically. This importer read a bounded window {access_text} "
        "and cached a small derived GeoTIFF; dataset checksum is "
        f"the SHA-256 of that cached window, not of the full {format_bytes(meta.source_size_bytes)} "
        f"product. Citation: {citation}"
    )
    return DatasetInfo(
        dataset_id=dataset_id,
        provider=provider,
        product_id=product_id,
        title=title,
        url=meta.source_url or f"file://{meta.source}",
        license=LICENSE_NAME,
        license_url=NASA_DATA_LICENSE_URL,
        access_timestamp=prepared.access_timestamp or utc_now_iso(),
        source_crs=meta.crs_wkt,
        native_resolution_m=float(meta.native_resolution_m),
        processing_version=PROCESSING_VERSION,
        checksum=checksum,
        source_kind=SOURCE_KIND,
        notes=notes,
    )


def build_manifest(
    prepared: PreparedWindow,
    result: ExtractionResult,
    dataset: DatasetInfo,
    *,
    reference_id: str,
    display_name: str,
    roi_name: str,
    roi_provenance: str,
    citation: str = CITATION,
    quality_flags: tuple[str, ...] | None = None,
    data_kind: str = "observed",
    observation_quality: str = "verified",
    generated_by: str = "pipelines/planetary/lola_import.py",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Shape one extraction as a pipeline-manifest 1.0.0 document."""
    if data_kind not in ("observed", "synthetic"):
        raise LolaImportError("invalid_data_kind", f"Unknown data_kind {data_kind!r}.")
    if data_kind == "synthetic":
        observation_quality = "synthetic"
    elif observation_quality not in ("verified", "suspect"):
        raise LolaImportError(
            "invalid_quality",
            f"Observed manifests must use 'verified' or 'suspect', not {observation_quality!r}.",
        )
    if data_kind == "synthetic" and dataset.source_kind != "synthetic":
        raise LolaImportError(
            "synthetic_dataset_mismatch",
            "A synthetic manifest must use a dataset with source_kind='synthetic'.",
        )
    if data_kind == "observed" and dataset.source_kind == "synthetic":
        raise LolaImportError(
            "observed_dataset_mismatch",
            "An observed manifest cannot use a synthetic dataset.",
        )

    missing = [feature_id for feature_id in FEATURE_IDS if feature_id not in result.features]
    if missing:
        raise LolaImportError(
            "incomplete_features",
            f"Extractor did not return all four registry features; missing {missing}.",
        )
    if not all(math.isfinite(result.features[feature_id]) for feature_id in FEATURE_IDS):
        raise LolaImportError(
            "non_finite_feature", "Extractor returned a non-finite feature value."
        )
    if result.valid_fraction < 0.8:
        raise LolaImportError(
            "insufficient_valid_data",
            f"Extracted valid fraction {result.valid_fraction:.2%} is below 80%.",
        )

    center_lon, center_lat = prepared.center_lonlat
    if not (math.isfinite(center_lon) and math.isfinite(center_lat)):
        raise LolaImportError(
            "non_finite_center", "Inverse projection of the ROI centre produced non-finite values."
        )
    if not (-180.0 <= center_lon <= 180.0 and -90.0 <= center_lat <= 90.0):
        raise LolaImportError(
            "center_out_of_range",
            f"Inverse-projected centre ({center_lon}, {center_lat}) is outside "
            "the longitude/latitude domain accepted by the manifest contract.",
        )

    features = make_observations(
        result.features,
        valid_fraction=result.valid_fraction,
        quality=observation_quality,
    )
    if quality_flags is None:
        base_quality_flags = (
            DEFAULT_QUALITY_FLAGS if data_kind == "observed" else ("synthetic_test_fixture",)
        )
    else:
        base_quality_flags = tuple(quality_flags)
    flags = list(base_quality_flags)
    for warning in prepared.meta.warnings:
        if warning in ("source_striped_not_cog", "source_has_no_overviews"):
            flags.append(warning)
    flags = list(dict.fromkeys(flags))

    reference_notes = (
        f"Shackleton rim / lunar south pole window. ROI {roi_name}: {roi_provenance}. "
        f"Window centre in source map coordinates: X={prepared.center_xy[0]:.1f} m, "
        f"Y={prepared.center_xy[1]:.1f} m. Inverse-projected centre: lon="
        f"{center_lon:.6f}, lat={center_lat:.6f} (east-positive degrees; longitude is "
        "ill-conditioned this close to the pole, so the X/Y centre is authoritative). "
        f"Analysis window {result.window_size_m:.0f} m resampled bilinearly to "
        f"{result.resolution_m:.0f} m; source crop {prepared.crop_shape[1]} x "
        f"{prepared.crop_shape[0]} px at {prepared.meta.native_resolution_m:.1f} m. "
        f"window_checksum={result.window_checksum}. "
        f"Extractor warnings: {', '.join(result.warnings) if result.warnings else 'none'}. "
        "The 5 m LDEM is about 90% interpolated; do not treat 5 m texture as an "
        "independent measurement. The four metrics are computed from elevation in the "
        f"source projected CRS. Citation: {citation}"
    )

    return {
        "manifest_version": "1.0.0",
        "contract_version": "1.0.0",
        "generated_by": generated_by,
        "generated_at": generated_at or utc_now_iso(),
        "data_kind": data_kind,
        "datasets": [
            {
                "dataset_id": dataset.dataset_id,
                "provider": dataset.provider,
                "product_id": dataset.product_id,
                "title": dataset.title,
                "url": dataset.url,
                "license": dataset.license,
                "license_url": dataset.license_url,
                "access_timestamp": dataset.access_timestamp,
                "source_crs": dataset.source_crs,
                "native_resolution_m": dataset.native_resolution_m,
                "processing_version": dataset.processing_version,
                "checksum": dataset.checksum,
                "source_kind": dataset.source_kind,
                "notes": dataset.notes,
            }
        ],
        "candidates": [],
        "references": [
            {
                "reference_id": reference_id,
                "body": prepared.body,
                "display_name": display_name,
                "dataset_id": dataset.dataset_id,
                "lat": round(float(center_lat), 8),
                "lon": round(float(center_lon), 8),
                "bbox": None,
                "window_size_m": float(result.window_size_m),
                "analysis_resolution_m": float(result.resolution_m),
                # Same CRS the extractor used (a projected source is never re-projected
                # to a different analysis CRS). Keep the WKT2 source rendering so the
                # dataset and reference CRS strings agree.
                "crs": prepared.meta.crs_wkt,
                "valid_fraction": round(float(result.valid_fraction), 6),
                "quality_flags": flags,
                "notes": reference_notes,
                "features": features,
            }
        ],
    }


def validate_manifest(
    document: dict[str, Any],
    schema_path: str | Path,
    *,
    expected_body: str = BODY,
) -> Manifest:
    """Validate with the JSON Schema contract and the API Pydantic model."""
    import jsonschema

    schema_file = Path(schema_path)
    try:
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LolaImportError(
            "schema_unavailable", f"Cannot read manifest schema {schema_file}: {exc}"
        ) from exc
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors[:10]
        )
        raise LolaImportError(
            "manifest_schema_failed", f"Manifest fails {schema_file.name}: {details}"
        )
    try:
        model = Manifest.model_validate(document)
    except ValueError as exc:
        raise LolaImportError(
            "manifest_model_failed", f"Manifest fails the API Manifest model: {exc}"
        ) from exc

    if model.data_kind == "observed":
        if not model.references:
            raise LolaImportError(
                "manifest_missing_reference", "Observed manifest has no reference entry."
            )
        for reference in model.references:
            if reference.body != expected_body:
                raise LolaImportError(
                    "manifest_body_mismatch",
                    f"Reference {reference.reference_id} has body {reference.body!r}, "
                    f"expected {expected_body!r}.",
                )
            if reference.valid_fraction < 0.8:
                raise LolaImportError(
                    "manifest_low_valid_fraction",
                    f"Reference {reference.reference_id} valid_fraction is below 80%.",
                )
            if set(reference.features) != set(FEATURE_IDS):
                raise LolaImportError(
                    "manifest_feature_set",
                    f"Reference {reference.reference_id} does not contain exactly the "
                    f"four registry features: {sorted(reference.features)}.",
                )
            if any(
                observation.quality != "verified" for observation in reference.features.values()
            ):
                raise LolaImportError(
                    "manifest_unverified_observation",
                    f"Reference {reference.reference_id} contains an observation whose "
                    "quality is not 'verified'.",
                )
    return model
