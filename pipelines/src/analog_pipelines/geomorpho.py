"""Bounded local raster reader for the future Geomorpho250 workflow (Issue #29).

Offline by construction: accepts only local filesystem paths. It never opens a
URL and never downloads. A future ingestion command may stage an authorized file
under ANALOG_RAW_DIR, then pass that path here.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS as ProjCRS
from rasterio.windows import from_bounds

REMOTE_PREFIXES = ("http://", "https://", "s3://", "gs://", "ftp://")


class RasterReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RasterMetadata:
    path: str
    crs: str | None
    width: int
    height: int
    count: int
    dtype: str
    nodata: float | None
    resolution: tuple[float, float]
    native_resolution_unit: str
    bounds: tuple[float, float, float, float]
    scale: float | None
    offset: float | None
    block_shape: tuple[int, int] | None
    size_bytes: int


@dataclass(frozen=True, slots=True)
class RasterWindow:
    data: np.ndarray
    valid_mask: np.ndarray
    transform: Any
    crs: str
    bbox_wgs84: dict[str, float]
    resolution_m: tuple[float, float]
    bbox_metric: dict[str, float] | None
    metadata: RasterMetadata


def _local_path(path: str | Path) -> Path:
    raw = str(path)
    if raw.lower().startswith(REMOTE_PREFIXES):
        raise RasterReadError(
            "Remote raster reads are disabled in this offline phase; stage an authorized "
            "local file and pass its filesystem path."
        )
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise RasterReadError(f"Raster file not found: {resolved}")
    return resolved


def inspect_raster(path: str | Path) -> RasterMetadata:
    resolved = _local_path(path)
    try:
        with rasterio.open(resolved) as src:
            scales = list(src.scales or [])
            offsets = list(src.offsets or [])
            return RasterMetadata(
                path=str(resolved),
                crs=str(src.crs) if src.crs else None,
                width=src.width,
                height=src.height,
                count=src.count,
                dtype=src.dtypes[0] if src.dtypes else "unknown",
                nodata=float(src.nodata) if src.nodata is not None else None,
                resolution=(abs(float(src.res[0])), abs(float(src.res[1]))),
                native_resolution_unit=(
                    str(ProjCRS.from_user_input(src.crs).axis_info[0].unit_name)
                    if src.crs
                    else "unknown"
                ),
                bounds=tuple(float(x) for x in src.bounds),
                scale=float(scales[0]) if scales and scales[0] not in (None, 0) else None,
                offset=float(offsets[0]) if offsets and offsets[0] is not None else None,
                block_shape=getattr(src, "block_shapes", [None])[0],
                size_bytes=resolved.stat().st_size,
            )
    except rasterio.errors.RasterioError as exc:  # pragma: no cover - provider-specific
        raise RasterReadError(f"Could not inspect raster {resolved}: {exc}") from exc


def read_window(
    path: str | Path,
    *,
    bbox: dict[str, float],
    band: int = 1,
    max_pixels: int = 1_000_000,
) -> RasterWindow:
    """Read a bounded WGS84 bbox from a local GeoTIFF.

    Applies the raster's scale/offset, keeps valid zero distinct from nodata, and
    rejects geographic-CRS windows whose pixel count exceeds the configured bound.
    """
    resolved = _local_path(path)
    try:
        with rasterio.open(resolved) as src:
            metadata = inspect_raster(resolved)
            if src.crs is None:
                raise RasterReadError("Raster has no CRS")
            if not src.crs.is_geographic:
                raise RasterReadError(
                    "Geomorpho250 reader expects a geographic source raster; got "
                    f"{src.crs}. Reproject explicitly in a future provider adapter."
                )
            if band < 1 or band > src.count:
                raise RasterReadError(f"Band {band} out of range 1..{src.count}")
            window = from_bounds(
                bbox["west"], bbox["south"], bbox["east"], bbox["north"], src.transform
            )
            window = window.round_offsets().round_lengths()
            if window.width <= 0 or window.height <= 0:
                raise RasterReadError("Requested bbox does not intersect the raster")
            if (
                window.col_off >= src.width
                or window.row_off >= src.height
                or window.col_off + window.width <= 0
                or window.row_off + window.height <= 0
            ):
                raise RasterReadError("Requested bbox lies outside the raster bounds")
            if window.width * window.height > max_pixels:
                raise RasterReadError(
                    f"Window has {int(window.width * window.height)} pixels; max_pixels={max_pixels}"
                )
            nodata = src.nodata
            data = src.read(
                band,
                window=window,
                masked=True,
                boundless=False,
                out_dtype="float64",
            )
            array = np.asarray(data.filled(np.nan), dtype="float64")
            valid = np.isfinite(array)
            if nodata is not None:
                valid &= array != float(nodata)
            scales = src.scales or []
            offsets = src.offsets or []
            scale = float(scales[band - 1]) if scales and scales[band - 1] else None
            offset = float(offsets[band - 1]) if offsets and offsets[band - 1] else None
            if scale is not None:
                if not math.isfinite(scale) or scale == 0:
                    raise RasterReadError("Invalid raster scale; refusing to fabricate values")
                array = array * scale
            if offset is not None:
                array = array + offset
            array = np.where(valid, array, np.nan)
            transform = src.window_transform(window)
            from pyproj import Geod

            lon_c = (bbox["west"] + bbox["east"]) / 2
            lat_c = (bbox["south"] + bbox["north"]) / 2
            geod = Geod(ellps="WGS84")
            _, _, x_size = geod.inv(lon_c, lat_c, lon_c + abs(float(src.res[0])), lat_c)
            _, _, y_size = geod.inv(lon_c, lat_c, lon_c, lat_c + abs(float(src.res[1])))
            return RasterWindow(
                data=array,
                valid_mask=valid,
                transform=transform,
                crs=str(src.crs),
                bbox_wgs84={key: float(bbox[key]) for key in ("west", "south", "east", "north")},
                resolution_m=(abs(x_size), abs(y_size)),
                bbox_metric=None,
                metadata=metadata,
            )
    except RasterReadError:
        raise
    except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
        raise RasterReadError(f"Could not read raster window: {exc}") from exc


def read_projected_window(
    path: str | Path,
    *,
    bbox_metric: dict[str, float],
    target_crs: str,
    resolution_m: float = 250.0,
    max_pixels: int = 1_000_000,
) -> RasterWindow:
    """Read the actual projected analysis footprint by reprojecting a local source.

    The geographic bbox is only an I/O envelope; it is never the analysis footprint.
    Destination cells are exactly in the requested projected metric CRS and resolution.
    """
    from pyproj import CRS as ProjCRS
    from pyproj import Transformer
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.warp import reproject

    resolved = _local_path(path)
    if resolution_m <= 0 or not math.isfinite(resolution_m):
        raise RasterReadError("resolution_m must be positive and finite")
    try:
        dst_crs = ProjCRS.from_user_input(target_crs)
    except Exception as exc:  # noqa: BLE001
        raise RasterReadError(f"target CRS cannot be parsed: {target_crs}") from exc
    if not dst_crs.is_projected:
        raise RasterReadError("projected analysis footprint requires a projected target CRS")
    unit = dst_crs.axis_info[0].unit_name.lower() if dst_crs.axis_info else ""
    if unit not in {"metre", "meter", "m"}:
        raise RasterReadError(f"target CRS must use metres, got {unit!r}")
    minx, miny = float(bbox_metric["west"]), float(bbox_metric["south"])
    maxx, maxy = float(bbox_metric["east"]), float(bbox_metric["north"])
    if maxx <= minx or maxy <= miny:
        raise RasterReadError("invalid projected bbox")
    nx = max(1, round((maxx - minx) / resolution_m))
    ny = max(1, round((maxy - miny) / resolution_m))
    if nx * ny > max_pixels:
        raise RasterReadError(f"window has {nx * ny} pixels; max_pixels={max_pixels}")
    dst_transform = from_origin(minx, maxy, (maxx - minx) / nx, (maxy - miny) / ny)

    try:
        with rasterio.open(resolved) as src:
            if src.crs is None:
                raise RasterReadError("Raster has no CRS")
            if not src.crs.is_geographic:
                raise RasterReadError(
                    "Projected-footprint reader expects a geographic source raster; got "
                    f"{src.crs}. Use a provider-specific reprojection adapter."
                )
            src_crs = src.crs
            to_geo = Transformer.from_crs(dst_crs, src_crs, always_xy=True)
            corners = [
                to_geo.transform(minx, miny),
                to_geo.transform(maxx, miny),
                to_geo.transform(minx, maxy),
                to_geo.transform(maxx, maxy),
            ]
            pad = 2 * max(abs(src.transform.a), abs(src.transform.e))
            lons = [point[0] for point in corners]
            lats = [point[1] for point in corners]
            src_window = (
                from_bounds(
                    min(lons) - pad,
                    min(lats) - pad,
                    max(lons) + pad,
                    max(lats) + pad,
                    src.transform,
                )
                .round_offsets()
                .round_lengths()
            )
            if src_window.width <= 0 or src_window.height <= 0:
                raise RasterReadError("projected footprint does not intersect the raster")
            if (
                src_window.col_off >= src.width
                or src_window.row_off >= src.height
                or src_window.col_off + src_window.width <= 0
                or src_window.row_off + src_window.height <= 0
            ):
                raise RasterReadError("projected footprint lies outside the raster bounds")
            fill = src.nodata if src.nodata is not None else np.nan
            data = src.read(
                1,
                window=src_window,
                boundless=True,
                masked=True,
                out_dtype="float64",
                fill_value=fill,
            )
            source = np.asarray(data.filled(np.nan), dtype="float64")
            valid = np.isfinite(source)
            if src.nodata is not None:
                valid &= source != float(src.nodata)
            scales = src.scales or []
            offsets = src.offsets or []
            scale = float(scales[0]) if scales and scales[0] else None
            offset = float(offsets[0]) if offsets and offsets[0] is not None else None
            if scale is not None:
                if not math.isfinite(scale) or scale == 0:
                    raise RasterReadError("invalid raster scale")
                source = source * scale
            if offset is not None:
                source = source + offset
            source = np.where(valid, source, np.nan)
            destination = np.full((ny, nx), np.nan, dtype="float64")
            reproject(
                source=source,
                destination=destination,
                src_transform=src.window_transform(src_window),
                src_crs=src_crs,
                src_nodata=np.nan,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
            valid_dst = np.isfinite(destination)
            inverse = Transformer.from_crs(dst_crs, "EPSG:4326", always_xy=True)
            geo_corners = [
                inverse.transform(minx, miny),
                inverse.transform(maxx, miny),
                inverse.transform(minx, maxy),
                inverse.transform(maxx, maxy),
            ]
            metadata = inspect_raster(resolved)
            return RasterWindow(
                data=destination,
                valid_mask=valid_dst,
                transform=dst_transform,
                crs=dst_crs.to_string(),
                bbox_wgs84={
                    "west": min(point[0] for point in geo_corners),
                    "south": min(point[1] for point in geo_corners),
                    "east": max(point[0] for point in geo_corners),
                    "north": max(point[1] for point in geo_corners),
                },
                resolution_m=((maxx - minx) / nx, (maxy - miny) / ny),
                bbox_metric={
                    "west": minx,
                    "south": miny,
                    "east": maxx,
                    "north": maxy,
                },
                metadata=metadata,
            )
    except RasterReadError:
        raise
    except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
        raise RasterReadError(f"Could not read projected raster window: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a local Geomorpho250 GeoTIFF")
    parser.add_argument("path")
    parser.add_argument("--metadata", action="store_true", help="Print metadata JSON")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"))
    args = parser.parse_args(argv)
    metadata = inspect_raster(args.path)
    if args.metadata or not args.bbox:
        print(metadata)
        return 0
    window = read_window(
        args.path,
        bbox={
            "west": args.bbox[0],
            "south": args.bbox[1],
            "east": args.bbox[2],
            "north": args.bbox[3],
        },
    )
    print(
        f"shape={window.data.shape} valid={window.valid_mask.sum()} "
        f"min={np.nanmin(window.data):.6g} max={np.nanmax(window.data):.6g}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
