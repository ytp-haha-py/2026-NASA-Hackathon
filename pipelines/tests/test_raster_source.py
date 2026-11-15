"""Tests for Goal C raster source adapters (offline/synthetic)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_pipelines.raster_source import (
    HttpRangeSource,
    LocalStagedSource,
    RasterMetadata,
    VsiCurlSource,
    iter_byte_ranges,
    open_source,
)
from rasterio.transform import from_origin


def _write_tiny_geotiff(path: Path, *, nodata: float = -9999.0) -> np.ndarray:
    data = np.array([[1.0, 2.0, nodata], [4.0, 5.0, 6.0]], dtype="float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": from_origin(120.0, 23.0, 0.01, 0.01),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    return data


class _RangeHandler(BaseHTTPRequestHandler):
    payload = b""
    requests: list[dict] = []

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"v1"')
        self.end_headers()

    def do_GET(self):  # noqa: N802
        rng = self.headers.get("Range")
        self.requests.append({"range": rng})
        body = self.payload
        if rng and rng.startswith("bytes="):
            spec = rng.split("=", 1)[1]
            start_s, end_s = spec.split("-", 1)
            start = int(start_s)
            end = int(end_s) if end_s else len(body) - 1
            end = min(end, len(body) - 1)
            chunk = body[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", '"v1"')
            self.end_headers()
            self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003
        return


def _serve(payload: bytes):
    handler = type("H", (_RangeHandler,), {"payload": payload, "requests": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/raster.bin"
    return server, thread, handler, url


def test_iter_byte_ranges_explicit():
    ranges = list(iter_byte_ranges(10, 4, start=2))
    assert [(r.start, r.end, r.size) for r in ranges] == [(2, 6, 4), (6, 10, 4)]
    assert ranges[0].as_http_header(10) == "bytes=2-5"


def test_raster_metadata_not_observation_by_default():
    md = RasterMetadata(
        source_uri="file:///tmp/x.tif",
        source_kind="local_staged",
        checksum="abc",
        version="v1",
        license="CC-BY",
        crs="EPSG:4326",
        nodata=-9999,
        resolution=(30.0, 30.0),
    )
    assert md.is_observation is False
    assert md.data_kind == "staged_artifact"
    with pytest.raises(ValueError):
        RasterMetadata(
            source_uri="x",
            source_kind="local_staged",
            is_observation=True,
            data_kind="staged_artifact",
        )


def test_local_staged_source_describe_and_read(tmp_path: Path):
    tif = tmp_path / "tile.tif"
    _write_tiny_geotiff(tif)
    src = LocalStagedSource(
        tif,
        metadata=RasterMetadata(
            source_uri=str(tif),
            source_kind="local_staged",
            version="2026.1",
            license="open",
            checksum_algorithm="sha256",
        ),
        compute_checksum=True,
    )
    md = src.describe()
    assert md.version == "2026.1"
    assert md.license == "open"
    assert md.crs and "4326" in md.crs
    assert md.nodata == -9999.0
    assert md.resolution is not None
    assert md.checksum and len(md.checksum) == 64
    assert md.is_observation is False

    masked = src.read_window()
    assert isinstance(masked, np.ma.MaskedArray)
    assert masked.mask.any()  # nodata preserved, not zero-filled
    assert float(masked[0, 0]) == 1.0

    chunks = list(src.iter_window_chunks(block_height=1, block_width=2))
    assert len(chunks) == 4  # 2 rows x 2 windows (width 3 -> 2+1)
    assert chunks[0][1].shape[-2:] == (1, 2)


def test_open_source_resolves_adapters(tmp_path: Path):
    tif = tmp_path / "a.tif"
    _write_tiny_geotiff(tif)
    assert isinstance(open_source(tif), LocalStagedSource)
    assert isinstance(open_source("https://example.com/a.tif"), HttpRangeSource)
    assert isinstance(open_source("https://example.com/a.tif", prefer_vsicurl=True), VsiCurlSource)
    v = VsiCurlSource("https://example.com/a.tif")
    assert v.uri.startswith("/vsicurl/https://")


def test_http_range_source_fetch_resume_and_checksum(tmp_path: Path):
    payload = os.urandom(64 * 1024)
    expected = hashlib.sha256(payload).hexdigest()
    server, thread, handler, url = _serve(payload)
    try:
        src = HttpRangeSource(url, chunk_size=8 * 1024, max_retries=1)
        dest = tmp_path / "out.bin"
        state = tmp_path / "out.bin.state.json"

        # Simulate partial download.
        part = dest
        part.write_bytes(payload[: 20 * 1024])
        # First resume attempt may fail if server behavior differs; our handler supports ranges.
        result = src.fetch(dest, resume=True, state_path=state, expected_checksum=expected)
        assert result.path.read_bytes() == payload
        assert result.state.complete is True
        assert result.state.checksum == expected
        assert state.exists()
        saved = json.loads(state.read_text())
        assert saved["complete"] is True
        assert saved["next_offset"] == len(payload)
        assert any(r.get("range") for r in handler.requests)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_iter_chunks_explicit_ranges(tmp_path: Path):
    payload = b"0123456789abcdef" * 1024
    server, thread, handler, url = _serve(payload)
    try:
        src = HttpRangeSource(url, chunk_size=4096)
        seen = []
        for br, data, _headers in src.iter_chunks(total_size=len(payload), chunk_size=4096):
            seen.append((br.start, br.end, len(data)))
        assert seen[0][0] == 0
        assert sum(s[2] for s in seen) == len(payload)
        assert all(s[1] - s[0] == s[2] for s in seen)
    finally:
        server.shutdown()
        thread.join(timeout=2)
