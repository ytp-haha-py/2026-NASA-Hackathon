"""Lightweight offline performance/resource checks for Goal C raster infra."""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from analog_pipelines.raster_cache import HeavyJobLimiter, RasterCache
from analog_pipelines.raster_source import HttpRangeSource


class _Handler(BaseHTTPRequestHandler):
    payload = b""

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        rng = self.headers.get("Range")
        body = self.payload
        if rng and rng.startswith("bytes="):
            start_s, end_s = rng.split("=", 1)[1].split("-", 1)
            start = int(start_s)
            end = int(end_s) if end_s else len(body) - 1
            chunk = body[start : min(end, len(body) - 1) + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{start + len(chunk) - 1}/{len(body)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003
        return


def test_chunked_download_throughput_small_synthetic(tmp_path: Path):
    payload = os.urandom(256 * 1024)
    handler = type("H", (_Handler,), {"payload": payload})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/x.bin"
        src = HttpRangeSource(url, chunk_size=32 * 1024)
        start = time.perf_counter()
        res = src.fetch(tmp_path / "x.bin")
        elapsed = time.perf_counter() - start
        assert res.path.read_bytes() == payload
        assert res.state.complete is True
        assert elapsed < 10.0
    finally:
        server.shutdown()
        t.join(timeout=2)


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
