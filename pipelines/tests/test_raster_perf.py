"""Lightweight offline performance/resource checks for Goal C raster infra."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np
import rasterio
from analog_pipelines.raster_cache import HeavyJobLimiter, RasterCache
from analog_pipelines.raster_source import RasterAccessKind, RasterSource, read_source_window
from rasterio.transform import from_origin
from rasterio.windows import Window


def test_bounded_window_read_throughput_small_synthetic(tmp_path: Path):
    path = tmp_path / "synthetic_raster_perf_fixture_not_an_observation.tif"
    profile = {
        "driver": "GTiff",
        "width": 256,
        "height": 256,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:32633",
        "transform": from_origin(500_000.0, 5_000_000.0, 30.0, 30.0),
        "nodata": -9999.0,
        "tiled": True,
        "blockxsize": 64,
        "blockysize": 64,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(np.arange(256 * 256, dtype="float32").reshape(256, 256), 1)

    source = RasterSource(
        path,
        kind=RasterAccessKind.STAGED_TILE,
        version="synthetic",
        license="test only",
    )
    start = time.perf_counter()
    result = read_source_window(source, window=Window(64, 64, 128, 128))
    elapsed = time.perf_counter() - start
    assert result.data.shape == (128, 128)
    assert result.valid_fraction == 1.0
    assert elapsed < 10.0


def test_cache_put_get_micro_perf(tmp_path: Path):
    cache = RasterCache(tmp_path / "cache")
    payload = os.urandom(64 * 1024)
    start = time.perf_counter()
    for i in range(30):
        cache.put_bytes(f"k/{i}", "v1", payload)
        assert cache.read_bytes(f"k/{i}", "v1") == payload
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0
    assert len(cache.list_entries()) == 30


def test_concurrency_budget_limits_heavy_jobs():
    lim = HeavyJobLimiter(heavy_jobs=2)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def job(_):
        nonlocal active, max_active
        with lim.heavy():
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
        return True

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        assert all(pool.map(job, range(10)))
    assert max_active <= 2
