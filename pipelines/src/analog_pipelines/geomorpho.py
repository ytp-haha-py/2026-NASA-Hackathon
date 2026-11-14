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
            return RasterWindow(
                data=array,
                valid_mask=valid,
                transform=transform,
                crs=str(src.crs),
                bbox_wgs84={key: float(bbox[key]) for key in ("west", "south", "east", "north")},
                resolution_m=(abs(float(src.res[0])), abs(float(src.res[1]))),
                metadata=metadata,
            )
    except RasterReadError:
        raise
    except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
        raise RasterReadError(f"Could not read raster window: {exc}") from exc


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
