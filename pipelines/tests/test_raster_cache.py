"""Tests for Goal C raster cache/versioning/concurrency (offline)."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from pathlib import Path

import pytest
from analog_pipelines.raster_cache import (
    EvictionPolicy,
    HeavyJobLimiter,
    RasterCache,
    default_cache_root,
)


def test_default_cache_root_configurable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANALOG_CACHE_DIR", str(tmp_path / "c"))
    root = default_cache_root()
    assert str(root).endswith("c")


def test_cache_put_get_versioned_metadata_atomic(tmp_path: Path):
    cache = RasterCache(tmp_path / "cache", eviction=EvictionPolicy(max_entries=10))
    payload = b"raster-bytes-v1"
    src = tmp_path / "in.bin"
    src.write_bytes(payload)
    entry = cache.put_file(
        "moon/lola/tileA",
        "v1",
        src,
        license="NASA-PD",
        crs="IAU2000:30100",
        nodata=-32768,
        resolution=(20.0, 20.0),
        metadata={"body": "moon"},
    )
    assert entry.version == "v1"
    assert entry.license == "NASA-PD"
    assert entry.crs == "IAU2000:30100"
    assert entry.nodata == -32768
    assert entry.resolution == (20.0, 20.0)
    assert cache.has("moon/lola/tileA", "v1")
    got = cache.get("moon/lola/tileA", "v1")
    assert got is not None and got.checksum == entry.checksum
    assert cache.read_bytes("moon/lola/tileA", "v1") == payload
    assert not list((tmp_path / "cache").rglob("*.tmp"))

    # New version coexists.
    cache.put_bytes("moon/lola/tileA", "v2", b"raster-bytes-v2", license="NASA-PD")
    assert cache.read_bytes("moon/lola/tileA", "v1") == payload
    assert cache.read_bytes("moon/lola/tileA", "v2") == b"raster-bytes-v2"


def test_cache_eviction_lru(tmp_path: Path):
    cache = RasterCache(tmp_path / "cache", eviction=EvictionPolicy(max_entries=2))
    cache.put_bytes("a", "1", b"aaa")
    cache.put_bytes("b", "1", b"bbb")
    cache.get("a", "1")  # touch a, b becomes LRU
    cache.put_bytes("c", "1", b"ccc")
    assert cache.has("a", "1")
    assert not cache.has("b", "1")
    assert cache.has("c", "1")


def test_cache_parallel_atomic_writes(tmp_path: Path):
    cache = RasterCache(tmp_path / "cache")
    payloads = [bytes([i]) * 4096 for i in range(8)]

    def write(i: int):
        cache.put_bytes("same", "v1", payloads[i])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))
    result = cache.read_bytes("same", "v1")
    assert result in payloads
    assert not list((tmp_path / "cache").rglob("*.tmp"))


def test_cache_checkpoint_save_load_clear(tmp_path: Path):
    cache = RasterCache(tmp_path / "cache")
    path = cache.save_checkpoint(
        "job-1",
        key="moon/tile",
        version="v1",
        chunks_total=10,
        chunks_completed=3,
        completed_chunks=[0, 1, 2],
        byte_offset=3072,
        partial_path=tmp_path / "p.part",
        state={"etag": "abc"},
    )
    assert path.exists()
    ck = cache.load_checkpoint("job-1")
    assert ck["checkpoint_id"] == "job-1"
    assert ck["chunks_completed"] == 3
    assert ck["completed_chunks"] == [0, 1, 2]
    assert ck["byte_offset"] == 3072
    assert ck["state"]["etag"] == "abc"
    assert cache.has_checkpoint("job-1")
    assert cache.clear_checkpoint("job-1") is True
    assert not cache.has_checkpoint("job-1")


def test_heavy_job_limiter_defaults_and_cap():
    lim = HeavyJobLimiter()
    assert 2 <= lim.heavy_jobs <= 4
    lim3 = HeavyJobLimiter(heavy_jobs=3)
    assert lim3.heavy_jobs == 3

    seen = 0
    max_seen = 0
    lock = threading.Lock()

    def work():
        nonlocal seen, max_seen
        with lim3.heavy():
            with lock:
                seen += 1
                max_seen = max(max_seen, seen)
            time.sleep(0.03)
            with lock:
                seen -= 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: work(), range(12)))
    assert max_seen <= 3

    # release on exception
    for _ in range(4):
        with pytest.raises(RuntimeError), lim3.heavy():
            raise RuntimeError("boom")
    with lim3.heavy():
        pass
