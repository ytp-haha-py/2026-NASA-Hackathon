"""Lightweight offline performance/resource checks for Goal C raster infra.

These are guardrails, not benchmarks: a small synthetic 32x32 GeoTIFF and in-memory
bytes keep them fast and deterministic. No provider reads or committed rasters are
involved, and the synthetic fixture is never an observation.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from pathlib import Path

import numpy as np
import rasterio
from analog_pipelines.raster_cache import (
    CacheKey,
    RasterCache,
    ResourceBudget,
    read_window_cached,
)
from analog_pipelines.raster_source import RasterAccessKind, RasterSource
from rasterio.transform import from_origin


def _write_fixture(path: Path) -> RasterSource:
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
    return RasterSource(
        path,
        kind=RasterAccessKind.STAGED_TILE,
        dataset_id="synthetic-raster-perf-fixture",
        version="synthetic-v1",
        license="test only; synthetic fixture",
    )


def test_bounded_window_read_throughput_small_synthetic(tmp_path: Path):
    path = tmp_path / "synthetic_raster_perf_fixture_not_an_observation.tif"
    source = _write_fixture(path)

    start = time.perf_counter()
    result = source.read_window(window=(64, 64, 128, 128))
    elapsed = time.perf_counter() - start
    assert result.data.shape == (128, 128)
    assert result.valid_fraction == 1.0
    assert elapsed < 10.0


def test_cached_window_read_avoids_a_second_source_read(tmp_path: Path):
    path = tmp_path / "synthetic_raster_perf_fixture_not_an_observation.tif"
    source = _write_fixture(path)
    cache = RasterCache(tmp_path / "cache")

    cold = read_window_cached(cache, source, window=(32, 32, 64, 64))
    path.unlink()  # any second source read would now fail
    start = time.perf_counter()
    warm = read_window_cached(cache, source, window=(32, 32, 64, 64))
    elapsed = time.perf_counter() - start

    np.testing.assert_array_equal(warm.data, cold.data)
    assert elapsed < 10.0
    assert cache.stats()["counters"]["hits"] >= 1


def test_cache_put_get_micro_perf(tmp_path: Path):
    cache = RasterCache(tmp_path / "cache")
    payload = b"\x01" * (64 * 1024)
    start = time.perf_counter()
    for i in range(30):
        per_key = CacheKey.build(
            source_id=f"micro/{i}",
            source_version="v1",
            window=(0, 0, 256, 256),
        )
        cache.put(per_key, payload)
        assert cache.get(per_key) == payload
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0
    assert cache.stats()["entries"] == 30


def test_concurrency_budget_limits_heavy_jobs():
    budget = ResourceBudget()
    assert 2 <= budget.limits.max_heavy_jobs <= 4
    state = {"active": 0, "peak": 0}
    lock = threading.Lock()

    def job(_: int) -> bool:
        with budget.heavy_job():
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.02)
            with lock:
                state["active"] -= 1
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        assert all(pool.map(job, range(10)))
    assert state["peak"] <= budget.limits.max_heavy_jobs
    assert budget.snapshot()["active_heavy_jobs"] == 0


def test_cache_lock_serialises_single_flight_compute(tmp_path: Path):
    cache = RasterCache(tmp_path / "cache")
    cache_key = CacheKey.build(source_id="single-flight", source_version="v1", window=(0, 0, 8, 8))
    calls = 0
    calls_lock = threading.Lock()

    def compute() -> bytes:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return b"window"

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: cache.get_or_compute(cache_key, compute), range(4)))
    assert results == [b"window"] * 4
    assert calls == 1
    assert cache.get(cache_key) == b"window"
