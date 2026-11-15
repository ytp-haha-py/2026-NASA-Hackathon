# Real Geomorpho250 coarse pilot

```json
{
  "coverage": {
    "bbox": {
      "east": 10.393982962376148,
      "north": 45.36007210182725,
      "south": 23.996959690126204,
      "west": -112.20542079733367
    },
    "label": "local_coarse_index_not_global_exhaustive",
    "regions": [
      "pilot-alps",
      "pilot-grand-canyon",
      "pilot-sahara"
    ]
  },
  "grid": {
    "cells": 168,
    "grid_version": "coarse-grid-1.0.0",
    "method_version": "coarse-terrain-250m-1.0.0",
    "regions": [
      "pilot-sahara",
      "pilot-alps",
      "pilot-grand-canyon"
    ],
    "rows": 168,
    "source_resolution_m": 250,
    "window_size_m": 5000
  },
  "index_partitions": [
    {
      "bytes": 9255,
      "path": "partitions/pilot-sahara/c38a11e353011d3d22d00d07d5291f03aaf3a0ef6a431fdd64afda2a833f7eb6.parquet",
      "region_id": "pilot-sahara",
      "rows": 64,
      "sha256": "c38a11e353011d3d22d00d07d5291f03aaf3a0ef6a431fdd64afda2a833f7eb6"
    },
    {
      "bytes": 8151,
      "path": "partitions/pilot-alps/49a22a65f8e827a7f72a5552e224a3edac0bba8892e941896a69664f5bf87a3a.parquet",
      "region_id": "pilot-alps",
      "rows": 48,
      "sha256": "49a22a65f8e827a7f72a5552e224a3edac0bba8892e941896a69664f5bf87a3a"
    },
    {
      "bytes": 8767,
      "path": "partitions/pilot-grand-canyon/b6d5a2d923d67c65619ac1f07c982fab498ada0706c97fdb7e6f4afa8b65ae40.parquet",
      "region_id": "pilot-grand-canyon",
      "rows": 56,
      "sha256": "b6d5a2d923d67c65619ac1f07c982fab498ada0706c97fdb7e6f4afa8b65ae40"
    }
  ],
  "limitations": [
    "Three 0.4 degree pilot regions, not global coverage.",
    "7.5 arc-second source is ~250 m only near the equator; no fixed metric source grid.",
    "Bilinear reprojection and 5 km aggregation are method-specific and not 90 m terrain metrics.",
    "Global coverage and same-support Moon/Mars coarse references remain unavailable."
  ],
  "sample_query": {
    "eligible_candidate_count": 168,
    "results": [
      {
        "candidate_id": "coarse-v1:95046a41aeb31ca9",
        "distance": 0.013548551537343379,
        "region_id": "pilot-sahara",
        "slope_mean_deg": 8.135485515373434,
        "valid_fraction": 1.0
      },
      {
        "candidate_id": "coarse-v1:61c31ae082cf0dbe",
        "distance": 0.028783536299297374,
        "region_id": "pilot-sahara",
        "slope_mean_deg": 7.712164637007026,
        "valid_fraction": 1.0
      },
      {
        "candidate_id": "coarse-v1:d558cc84b2550c64",
        "distance": 0.08187613758464156,
        "region_id": "pilot-sahara",
        "slope_mean_deg": 7.181238624153584,
        "valid_fraction": 1.0
      }
    ],
    "target": {
      "coarse_slope_mean_deg": 8.0
    }
  },
  "snapshot": "geomorpho250-real-pilot-20261115",
  "source": {
    "access": "bounded /vsicurl range reads only; no full global download",
    "citation": "Amatulli et al. (2019/2020), Geomorpho90m, PANGAEA 899135",
    "license": "CC BY 4.0",
    "metadata": {
      "pilot-alps": {
        "license": "CC BY 4.0",
        "local_window": "var/cache/geomorpho-pilot/pilot-alps.tif",
        "native_resolution_deg": 0.0020833333333333333,
        "native_resolution_note": "7.5 arc-second (~250 m), not a fixed metric resolution",
        "nodata": -32768.0,
        "raw_dtype": "int16",
        "scale_tag": 0.01,
        "sha256": "e1349d175135549fee6336c0aaa4d036fdaeb5d209b5a44c5865d73c17ca78fd",
        "slope_max_deg": 8.520000457763672,
        "slope_min_deg": 0.009999999776482582,
        "source_crs": "EPSG:4326",
        "source_url": "https://hs.pangaea.de/Maps/DEM_Geomorpho90m/dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif",
        "valid_fraction": 1.0,
        "window_offsets": [
          20122,
          91176
        ],
        "window_shape": [
          240,
          240
        ]
      },
      "pilot-grand-canyon": {
        "license": "CC BY 4.0",
        "local_window": "var/cache/geomorpho-pilot/pilot-grand-canyon.tif",
        "native_resolution_deg": 0.0020833333333333333,
        "native_resolution_note": "7.5 arc-second (~250 m), not a fixed metric resolution",
        "nodata": -32768.0,
        "raw_dtype": "int16",
        "scale_tag": 0.01,
        "sha256": "e0c301f2dcd4891c5f55533d526857f94b844bb8c80ab6a991e45bab34556885",
        "slope_max_deg": 62.40999984741211,
        "slope_min_deg": 0.07000000029802322,
        "source_crs": "EPSG:4326",
        "source_url": "https://hs.pangaea.de/Maps/DEM_Geomorpho90m/dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif",
        "valid_fraction": 1.0,
        "window_offsets": [
          24538,
          32520
        ],
        "window_shape": [
          240,
          240
        ]
      },
      "pilot-sahara": {
        "license": "CC BY 4.0",
        "local_window": "var/cache/geomorpho-pilot/pilot-sahara.tif",
        "native_resolution_deg": 0.0020833333333333333,
        "native_resolution_note": "7.5 arc-second (~250 m), not a fixed metric resolution",
        "nodata": -32768.0,
        "raw_dtype": "int16",
        "scale_tag": 0.01,
        "sha256": "3c8a3f993c43ddf79929b86db7c589abc781baa030b782db894926c8acf234c9",
        "slope_max_deg": 37.75,
        "slope_min_deg": 0.009999999776482582,
        "source_crs": "EPSG:4326",
        "source_url": "https://hs.pangaea.de/Maps/DEM_Geomorpho90m/dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif",
        "valid_fraction": 1.0,
        "window_offsets": [
          30202,
          91176
        ],
        "window_shape": [
          240,
          240
        ]
      }
    },
    "url": "https://hs.pangaea.de/Maps/DEM_Geomorpho90m/dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif"
  }
}
```
