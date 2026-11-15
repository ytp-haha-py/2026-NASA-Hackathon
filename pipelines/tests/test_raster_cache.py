"""Offline tests for the bounded raster window cache (Issue #170, Goal C).

Every test uses a tiny temporary root and bytes/arrays generated in-process. The
integration tests read a 32x32 synthetic GeoTIFF through C1's ``RasterSource``;
there are no provider reads, downloads, or committed raster fixtures.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_pipelines.raster_cache import (
    CACHE_FORMAT_VERSION,
    DEFAULT_HEAVY_JOBS,
    ENV_HEAVY_JOBS,
    ENV_MAX_BYTES,
    ENV_ROOT,
    STATUS_CORRUPT,
    STATUS_HIT,
    STATUS_MISS,
    STATUS_PARTIAL,
    BudgetExceeded,
    BudgetLimits,
    CacheKey,
    CacheLockTimeout,
    CacheStateError,
    ChunkedWrite,
    InsufficientDisk,
    InvalidCacheKey,
    RasterCache,
    RasterWindow,
    ResourceBudget,
    budget_limits_from_env,
    cache_from_env,
    default_cache_root,
    read_window_cached,
)
from analog_pipelines.raster_source import RasterAccessKind, RasterSource, RasterSourceError
from rasterio.transform import from_origin


def limits(**overrides: object) -> BudgetLimits:
    base = {
        "max_heavy_jobs": 4,
        "max_ram_bytes": 1_000_000,
        "min_free_disk_bytes": 0,
    }
    base.update(overrides)
    return BudgetLimits(**base)  # type: ignore[arg-type]


def make_cache(tmp_path: Path, **overrides: object) -> RasterCache:
    options: dict[str, object] = {
        "max_bytes": 10_000_000,
        "max_entries": 1000,
        "auto_evict": False,
        "env": {},
        "budget": ResourceBudget(limits()),
    }
    options.update(overrides)
    return RasterCache(tmp_path / "raster-cache", **options)  # type: ignore[arg-type]


def key(
    *,
    source_id: str = "moon-lola128-pa-pixel-202405",
    source_version: str = "sha256:abc123",
    window: RasterWindow | None = None,
    band: int = 1,
    options: dict[str, object] | None = None,
) -> CacheKey:
    return CacheKey.build(
        source_id=source_id,
        source_version=source_version,
        window=window or RasterWindow(10, 20, 32, 16),
        band=band,
        options=options,
    )


def write_synthetic_raster(path: Path) -> None:
    """A 32x32 tiled GeoTIFF with a nodata stripe; synthetic, not an observation."""
    profile = {
        "driver": "GTiff",
        "width": 32,
        "height": 32,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:32633",
        "transform": from_origin(500_000.0, 5_000_000.0, 30.0, 30.0),
        "nodata": -9999.0,
        "tiled": True,
        "blockxsize": 16,
        "blockysize": 16,
    }
    values = np.arange(32 * 32, dtype="float32").reshape(32, 32)
    values[0:4, :] = -9999.0
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(values, 1)


def raster_source(path: Path, *, version: str = "synthetic-v1") -> RasterSource:
    return RasterSource(
        path,
        kind=RasterAccessKind.STAGED_TILE,
        dataset_id="synthetic-window-test",
        version=version,
        license="test only; synthetic fixture",
    )


# ---------------------------------------------------------------------------
# Key identity
# ---------------------------------------------------------------------------


def test_key_identity_includes_source_version_window_band_and_options() -> None:
    base = key()
    assert key().fingerprint == base.fingerprint
    assert (
        key(options={"resampling": "bilinear", "pad": 2}).fingerprint
        == key(options={"pad": 2, "resampling": "bilinear"}).fingerprint
    )

    variants = [
        key(source_id="mars-mola-megdr-meg128-t44n000"),
        key(source_version="sha256:def456"),
        key(window=RasterWindow(10, 20, 32, 17)),
        key(window=RasterWindow(11, 20, 32, 16)),
        key(band=2),
        key(options={"resampling": "nearest"}),
    ]
    fingerprints = {base.fingerprint} | {variant.fingerprint for variant in variants}
    assert len(fingerprints) == len(variants) + 1
    assert base.to_payload()["cache_format"] == CACHE_FORMAT_VERSION


@pytest.mark.parametrize(
    "factory",
    [
        lambda: key(source_id=""),
        lambda: key(source_id="   "),
        lambda: key(source_version=""),
        lambda: key(window=RasterWindow(0, 0, 0, 1)),
        lambda: key(window=RasterWindow(-1, 0, 1, 1)),
        lambda: key(window=RasterWindow(0, 0, 1.5, 1)),  # type: ignore[arg-type]
        lambda: key(band=0),
        lambda: key(band=True),
        lambda: key(options={"nested": object()}),
        lambda: CacheKey(
            source_id="s", source_version="v", window=RasterWindow(0, 0, 1, 1), options_json="[]"
        ),
    ],
)
def test_invalid_keys_are_rejected(factory) -> None:
    with pytest.raises(InvalidCacheKey):
        factory()


def test_key_for_source_uses_source_identity_and_requires_version(tmp_path: Path) -> None:
    path = tmp_path / "window.tif"
    write_synthetic_raster(path)
    source = raster_source(path)
    built = CacheKey.for_source(source, window=(4, 4, 8, 8))
    assert built.source_id == "synthetic-window-test"
    assert built.source_version == "synthetic-v1"
    assert built.window == RasterWindow(4, 4, 8, 8)

    unversioned = RasterSource(path, dataset_id="synthetic-window-test")
    with pytest.raises(InvalidCacheKey, match="unversioned"):
        CacheKey.for_source(unversioned, window=(0, 0, 4, 4))


# ---------------------------------------------------------------------------
# Atomic put/get and checksums
# ---------------------------------------------------------------------------


def test_put_get_roundtrip_checksum_and_atomic_artifacts(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache_key = key()
    payload = b"\x00elevation-window\xff" * 32

    entry = cache.put(cache_key, payload, extra={"provider": "local-fixture"})

    assert entry.checksum == hashlib.sha256(payload).hexdigest()
    assert entry.size_bytes == len(payload)
    assert entry.extra == {"provider": "local-fixture"}
    assert cache.get(cache_key) == payload

    metadata = json.loads(entry.metadata_path.read_text(encoding="utf-8"))
    assert metadata["sha256"] == entry.checksum
    assert metadata["cache_key"] == cache_key.fingerprint
    assert metadata["source_version"] == "sha256:abc123"
    assert metadata["window"] == {"col_off": 10, "row_off": 20, "width": 32, "height": 16}
    assert cache.probe(cache_key).status == STATUS_HIT
    assert not list(cache.root.rglob("*.tmp"))
    assert (cache.root / ".gitignore").is_file()
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["bytes"] == len(payload)
    assert stats["counters"]["puts"] == 1
    assert stats["counters"]["hits"] == 1
    assert stats["budget"]["limits"]["max_heavy_jobs"] == 4


def test_array_roundtrip_and_non_array_payload_is_rejected(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    array_key = key()
    array = np.linspace(-12.5, 43.25, 24, dtype="float32").reshape(4, 6)
    cache.put_array(array_key, array)
    restored = cache.get_array(array_key)
    assert restored is not None
    np.testing.assert_array_equal(restored, array)
    assert restored.dtype == array.dtype

    bytes_key = key(band=2)
    cache.put(bytes_key, b"not-an-npy-payload")
    with pytest.raises(CacheStateError, match="npy"):
        cache.get_array(bytes_key)


def test_versions_and_windows_are_isolated(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    first, second, third = key(), key(source_version="sha256:def456"), key(band=2)
    cache.put(first, b"window-a")
    cache.put(second, b"window-a-new-version")
    cache.put(third, b"window-a-band-2")

    assert cache.get(first) == b"window-a"
    assert cache.get(second) == b"window-a-new-version"
    assert cache.get(third) == b"window-a-band-2"
    assert cache.stats()["entries"] == 3


def test_disk_budget_guard_blocks_put(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, budget=ResourceBudget(limits(min_free_disk_bytes=10**18)))
    with pytest.raises(InsufficientDisk):
        cache.put(key(), b"payload")
    assert cache.get(key()) is None
    assert not list(cache.iter_entries())


# ---------------------------------------------------------------------------
# Corrupt and partial entries
# ---------------------------------------------------------------------------


def test_corrupt_payload_is_quarantined_and_reported_as_miss(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache_key = key()
    cache.put(cache_key, b"original-payload")
    cache.probe(cache_key).entry.payload_path.write_bytes(b"tampered-payload")

    assert cache.probe(cache_key, verify=True).status == STATUS_CORRUPT
    assert cache.get(cache_key) is None
    assert cache.probe(cache_key).status == STATUS_MISS
    quarantined = list((cache.root / "quarantine").iterdir())
    assert len(quarantined) == 1
    assert (quarantined[0] / "payload.bin").read_bytes() == b"tampered-payload"
    assert cache.stats()["counters"]["corruptions"] >= 1
    assert cache.stats()["counters"]["quarantined"] == 1


def test_payload_without_metadata_is_corrupt(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache_key = key()
    entry = cache.put(cache_key, b"payload")
    entry.metadata_path.unlink()

    probe = cache.probe(cache_key)
    assert probe.status == STATUS_CORRUPT
    assert "without checksummed metadata" in probe.detail
    assert cache.get(cache_key) is None
    assert list((cache.root / "quarantine").iterdir())


def test_metadata_for_another_key_is_corrupt(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    original = key()
    entry = cache.put(original, b"payload")
    metadata = json.loads(entry.metadata_path.read_text(encoding="utf-8"))
    metadata["cache_key"] = key(band=2).fingerprint
    entry.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    probe = cache.probe(original)
    assert probe.status == STATUS_CORRUPT
    assert "does not match" in probe.detail


# ---------------------------------------------------------------------------
# Chunked writes, restart and resume
# ---------------------------------------------------------------------------


def test_chunked_write_resumes_after_restart(tmp_path: Path) -> None:
    cache_key = key()
    first = make_cache(tmp_path)
    session = first.chunked_write(cache_key, chunk_count=3)
    session.write_chunk(0, b"aaa")
    session.write_chunk(2, b"ccc")
    assert session.completed_chunks == (0, 2)
    assert session.missing_chunks == (1,)

    # Simulate a crash: no finalize, no cleanup, and a fresh cache instance.
    restarted = make_cache(tmp_path)
    resumed = restarted.chunked_write(cache_key, chunk_count=3)
    assert resumed.completed_chunks == (0, 2)
    assert restarted.probe(cache_key).status == STATUS_PARTIAL
    assert restarted.probe(cache_key).completed_chunks == (0, 2)

    resumed.write_chunk(1, b"bbb")
    entry = resumed.finalize(extra={"resumed": True})

    assert entry.checksum == hashlib.sha256(b"aaabbbccc").hexdigest()
    assert restarted.get(cache_key) == b"aaabbbccc"
    assert restarted.probe(cache_key).status == STATUS_HIT
    assert not (entry.directory / "chunks").exists()
    assert restarted.stats()["counters"]["puts"] == 1


def test_chunked_finalize_requires_every_chunk(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache_key = key()
    session = cache.chunked_write(cache_key, chunk_count=2)
    session.write_chunk(0, b"only-one")

    with pytest.raises(CacheStateError, match="chunk"):
        session.finalize()

    assert cache.get(cache_key) is None
    assert cache.probe(cache_key).status == STATUS_PARTIAL
    assert session.completed_chunks == (0,)


def test_chunk_plan_mismatch_and_bad_index_are_rejected(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache_key = key()
    session = cache.chunked_write(cache_key, chunk_count=2)
    session.write_chunk(0, b"a")

    with pytest.raises(CacheStateError, match="chunk_count"):
        cache.chunked_write(cache_key, chunk_count=4)
    with pytest.raises(ValueError):
        session.write_chunk(2, b"nope")
    with pytest.raises(ValueError):
        session.write_chunk(-1, b"nope")


def test_chunk_session_context_manager_keeps_chunks_on_normal_exit_and_discards_on_error(
    tmp_path: Path,
) -> None:
    cache = make_cache(tmp_path)
    cache_key = key()
    with cache.chunked_write(cache_key, chunk_count=2) as session:
        session.write_chunk(0, b"a")
    assert cache.probe(cache_key).status == STATUS_PARTIAL

    failing_key = key(band=2)
    with pytest.raises(RuntimeError):  # noqa: SIM117 - assert the session context
        with cache.chunked_write(failing_key, chunk_count=2) as session:
            session.write_chunk(0, b"a")
            raise RuntimeError("provider failed")
    assert cache.probe(failing_key).status == STATUS_MISS


# ---------------------------------------------------------------------------
# Locking and single-flight computation
# ---------------------------------------------------------------------------


def test_get_or_compute_runs_once_across_threads(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache_key = key()
    calls = 0
    calls_lock = threading.Lock()

    def compute() -> bytes:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return b"computed-window"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: cache.get_or_compute(cache_key, compute), range(8)))

    assert results == [b"computed-window"] * 8
    assert calls == 1
    assert cache.stats()["counters"]["puts"] == 1


def test_lock_timeout_is_reported(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache_key = key()

    def acquire() -> None:
        with cache.lock(cache_key, timeout=0.05):
            pass

    with cache.lock(cache_key):  # noqa: SIM117 - hold the key lock while the thread waits
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(acquire)
            with pytest.raises(CacheLockTimeout):
                future.result()
    assert cache.stats()["counters"]["lock_timeouts"] == 1


# ---------------------------------------------------------------------------
# Resource budget
# ---------------------------------------------------------------------------


def test_heavy_job_budget_bounds_concurrency_and_ram() -> None:
    budget = ResourceBudget(limits(max_heavy_jobs=2, max_ram_bytes=100))
    state = {"active": 0, "peak": 0, "ram_peak": 0}
    state_lock = threading.Lock()

    def work(_: int) -> None:
        with budget.heavy_job(ram_bytes=50, timeout=5.0) as held:
            with state_lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                snapshot = held.snapshot()
                state["ram_peak"] = max(state["ram_peak"], snapshot["ram_in_use_bytes"])
            time.sleep(0.03)
            with state_lock:
                state["active"] -= 1

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, range(6)))

    assert state["peak"] == 2
    assert state["ram_peak"] <= 100
    snapshot = budget.snapshot()
    assert snapshot["active_heavy_jobs"] == 0
    assert snapshot["peak_heavy_jobs"] == 2
    assert snapshot["ram_in_use_bytes"] == 0


def test_heavy_job_rejects_oversized_ram_and_times_out() -> None:
    budget = ResourceBudget(limits(max_heavy_jobs=2, max_ram_bytes=100))
    with pytest.raises(BudgetExceeded, match="exceeds the budget"):  # noqa: SIM117
        with budget.heavy_job(ram_bytes=101):
            pass

    with budget.heavy_job(ram_bytes=100):  # noqa: SIM117 - hold the RAM reservation
        with pytest.raises(BudgetExceeded, match="RAM budget"):
            with budget.heavy_job(ram_bytes=100, timeout=0.05):
                pass

    slot_budget = ResourceBudget(limits(max_heavy_jobs=1))
    with slot_budget.heavy_job():  # noqa: SIM117 - hold the only slot
        with pytest.raises(BudgetExceeded, match="no heavy job slot"):
            with slot_budget.heavy_job(timeout=0.05):
                pass

    assert budget.snapshot()["active_heavy_jobs"] == 0
    assert slot_budget.snapshot()["active_heavy_jobs"] == 0


def test_get_or_compute_holds_a_heavy_job_slot(tmp_path: Path) -> None:
    budget = ResourceBudget(limits(max_heavy_jobs=1, max_ram_bytes=64))
    cache = make_cache(tmp_path, budget=budget)
    cache.get_or_compute(key(), lambda: b"x", ram_bytes=64)
    snapshot = budget.snapshot()
    assert snapshot["peak_ram_bytes"] == 64
    assert snapshot["peak_heavy_jobs"] == 1
    assert snapshot["active_heavy_jobs"] == 0


def test_default_heavy_job_budget_is_small_and_configurable() -> None:
    assert 2 <= DEFAULT_HEAVY_JOBS <= 4
    assert budget_limits_from_env({}).max_heavy_jobs == DEFAULT_HEAVY_JOBS
    assert budget_limits_from_env({ENV_HEAVY_JOBS: "2"}).max_heavy_jobs == 2
    with pytest.raises(Exception, match=ENV_HEAVY_JOBS):
        budget_limits_from_env({ENV_HEAVY_JOBS: "lots"})


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


def _age(entry: object, seconds: float) -> None:
    payload = entry.payload_path  # type: ignore[attr-defined]
    stamp = time.time() - seconds
    os.utime(payload, (stamp, stamp))


def test_eviction_removes_least_recently_used_entries(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    oldest, middle, newest = key(band=1), key(band=2), key(band=3)
    entries = {
        k.fingerprint: cache.put(k, b"payload-" + bytes([k.band])) for k in (oldest, middle, newest)
    }
    _age(entries[oldest.fingerprint], 300)
    _age(entries[middle.fingerprint], 200)
    _age(entries[newest.fingerprint], 100)

    dry_run = cache.evict(max_entries=2, dry_run=True)
    assert dry_run.removed == (oldest.fingerprint,)
    assert dry_run.remaining_entries == 2
    assert cache.probe(oldest).status == STATUS_HIT

    report = cache.evict(max_entries=2)
    assert report.removed == (oldest.fingerprint,)
    assert report.remaining_entries == 2
    assert cache.get(oldest) is None
    assert cache.get(middle) is not None
    assert cache.get(newest) is not None
    assert cache.stats()["counters"]["evictions"] == 1
    assert cache.stats()["counters"]["evicted_bytes"] == report.removed_bytes


def test_eviction_honours_byte_limit(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    first, second = key(band=1), key(band=2)
    first_entry = cache.put(first, b"x" * 100)
    second_entry = cache.put(second, b"y" * 100)
    _age(first_entry, 100)
    _age(second_entry, 50)

    report = cache.evict(max_bytes=150, max_entries=10)

    assert report.removed == (first.fingerprint,)
    assert report.removed_bytes == 100
    assert report.remaining_bytes == 100
    assert cache.get(second) is not None


def test_auto_evict_on_put(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, max_entries=1, auto_evict=True)
    first, second = key(band=1), key(band=2)
    first_entry = cache.put(first, b"first")
    _age(first_entry, 100)
    cache.put(second, b"second")

    assert cache.get(first) is None
    assert cache.get(second) == b"second"
    assert cache.stats()["counters"]["evictions"] == 1


# ---------------------------------------------------------------------------
# RasterSource integration
# ---------------------------------------------------------------------------


def test_read_window_cached_cold_then_warm_without_source(tmp_path: Path) -> None:
    path = tmp_path / "synthetic_window_fixture_not_an_observation.tif"
    write_synthetic_raster(path)
    cache = make_cache(tmp_path)
    source = raster_source(path)

    cold = read_window_cached(cache, source, window=(4, 4, 8, 8))
    assert cold.data.shape == (8, 8)
    assert cold.window == (4, 4, 8, 8)
    assert cold.band == 1
    assert cold.crs_wkt is not None
    assert cold.valid_fraction == 1.0
    assert cache.stats()["entries"] == 1

    # Delete the source: a warm read must come from the cache, not a provider read.
    path.unlink()
    warm = read_window_cached(cache, source, window=(4, 4, 8, 8))
    np.testing.assert_array_equal(warm.data, cold.data)
    np.testing.assert_array_equal(warm.valid_mask, cold.valid_mask)
    assert warm.checksum == cold.checksum


def test_read_window_cached_preserves_nodata_mask(tmp_path: Path) -> None:
    path = tmp_path / "synthetic_window_fixture_not_an_observation.tif"
    write_synthetic_raster(path)
    cache = make_cache(tmp_path)
    source = raster_source(path)

    window = read_window_cached(cache, source, window=(0, 0, 32, 8))
    assert window.valid_mask[0:4, :].sum() == 0
    assert window.valid_fraction == pytest.approx(0.5)
    assert np.isnan(window.data[0:4, :]).all()
    cached = cache.get_window(CacheKey.for_source(source, window=(0, 0, 32, 8)))
    assert cached is not None
    np.testing.assert_array_equal(cached.transform, window.transform)


def test_version_bump_does_not_reuse_stale_window(tmp_path: Path) -> None:
    path = tmp_path / "synthetic_window_fixture_not_an_observation.tif"
    write_synthetic_raster(path)
    cache = make_cache(tmp_path)
    first = raster_source(path, version="synthetic-v1")
    read_window_cached(cache, first, window=(0, 0, 8, 8))

    second = raster_source(path, version="synthetic-v2")
    assert (
        CacheKey.for_source(first, window=(0, 0, 8, 8)).fingerprint
        != CacheKey.for_source(second, window=(0, 0, 8, 8)).fingerprint
    )
    path.unlink()
    with pytest.raises(RasterSourceError):
        read_window_cached(cache, second, window=(0, 0, 8, 8))


def test_put_window_and_get_window_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "synthetic_window_fixture_not_an_observation.tif"
    write_synthetic_raster(path)
    cache = make_cache(tmp_path)
    source = raster_source(path)
    cache_key = CacheKey.for_source(source, window=(2, 2, 6, 6))

    with source.open() as reader:
        result = reader.read_window(window=(2, 2, 6, 6))
    stored = cache.put_window(cache_key, result)
    loaded = cache.get_window(cache_key)

    assert loaded is not None
    np.testing.assert_array_equal(loaded.data, stored.data)
    np.testing.assert_array_equal(loaded.valid_mask, stored.valid_mask)
    assert loaded.transform == stored.transform
    assert loaded.to_affine() == result.transform
    assert loaded.to_payload()["cache_key"] == cache_key.fingerprint


# ---------------------------------------------------------------------------
# Roots, environment and maintenance
# ---------------------------------------------------------------------------


def test_default_root_uses_env_override_without_creating_it(tmp_path: Path) -> None:
    target = tmp_path / "operator-cache"
    assert default_cache_root({ENV_ROOT: str(target)}) == target
    assert not target.exists()
    assert default_cache_root({}).name == "raster"


def test_cache_from_env_reads_limits_and_root(tmp_path: Path) -> None:
    env = {
        ENV_ROOT: str(tmp_path / "env-cache"),
        ENV_MAX_BYTES: "1234",
        ENV_HEAVY_JOBS: "2",
    }
    cache = cache_from_env(env)
    assert cache.root == tmp_path / "env-cache"
    assert cache.max_bytes == 1234
    assert cache.budget.limits.max_heavy_jobs == 2


def test_delete_clear_and_stats(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    first, second = key(band=1), key(band=2)
    cache.put(first, b"one")
    cache.put(second, b"two")
    assert cache.stats()["entries"] == 2

    assert cache.delete(first) is True
    assert cache.delete(first) is False
    assert cache.stats()["entries"] == 1

    assert cache.clear() == 1
    stats = cache.stats()
    assert stats["entries"] == 0
    assert stats["bytes"] == 0
    assert cache.probe(second).status == STATUS_MISS


def test_chunked_write_instance_is_usable_standalone(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache_key = key()
    session = ChunkedWrite(cache, cache_key, 1)
    session.write_chunk(0, b"payload")
    assert session.finalize().size_bytes == len(b"payload")
    assert cache.get(cache_key) == b"payload"


def test_budget_limits_reject_impossible_values() -> None:
    with pytest.raises(ValueError):
        BudgetLimits(max_heavy_jobs=0)
    with pytest.raises(ValueError):
        BudgetLimits(max_ram_bytes=-1)
    with pytest.raises(ValueError):
        dataclasses.replace(limits(), max_heavy_jobs=True)
