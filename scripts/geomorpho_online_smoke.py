#!/usr/bin/env python
"""Opt-in online smoke for the real Geomorpho250 slope COG (range reads only)."""

from __future__ import annotations

import json

import rasterio
from rasterio.windows import from_bounds

URL = "https://hs.pangaea.de/Maps/DEM_Geomorpho90m/dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif"


def main() -> int:
    env = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": "1048576",
    }
    with rasterio.Env(**env), rasterio.open("/vsicurl/" + URL) as src:
        window = from_bounds(10.0, 24.0, 10.02, 24.02, src.transform)
        data = src.read(1, window=window)
        payload = {
            "url": URL,
            "crs": str(src.crs),
            "size": [src.width, src.height],
            "pixel_size_deg": list(src.res),
            "dtype": src.dtypes[0],
            "nodata": src.nodata,
            "scale_tag": "0.01",
            "block_shape": src.block_shapes[0],
            "read_shape": list(data.shape),
            "raw_min": int(data.min()),
            "raw_max": int(data.max()),
            "slope_deg_min": float(data.min()) * 0.01,
            "slope_deg_max": float(data.max()) * 0.01,
            "access": "bounded /vsicurl range read; full global file not downloaded",
        }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
