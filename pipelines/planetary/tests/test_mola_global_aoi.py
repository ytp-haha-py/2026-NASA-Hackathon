"""Offline tests for Goal B: global Mars MOLA MEGDR AOI D01 via mola_global.py.

No test here reaches the public network. Real bounded reads and 3 dispersed
low/mid/high-latitude AOI evidence are documented in docs/MARS_D01_GLOBAL_AOI.md
and pipelines/planetary/evidence/mars_mola_aoi_evidence.json.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

# Goal B owned module
import mola_global as mg
import numpy as np
import pytest

# Backend seam for offline synthetic windows
from mola_aoi import (
    ANALYSIS_RESOLUTION_M,
    D01_FEATURE_IDS,
    MolaAoiError,
    MolaWindow,
    analyze_mola_aoi,
    tiles_for_bbox,
    validate_mola_aoi,
)

JEZERO = {"west": 77.35, "south": 18.35, "east": 77.55, "north": 18.53}
HELLAS = {"west": 70.35, "south": -42.5, "east": 70.55, "north": -42.3}
OLYMPIA = {"west": 239.55, "south": 79.92, "east": 240.45, "north": 80.08}


def _fake_window(request, data: np.ndarray) -> MolaWindow:
    return MolaWindow(
        data=np.asarray(data, dtype="float64"),
        sources=(),
        source_crs_wkt='PROJCRS["SIMPLE_CYLINDRICAL MARS"]',
        source_crs_name="SIMPLE_CYLINDRICAL MARS",
        source_resolution_m=463.0576672,
    )


def _reader_for(data_factory):
    def reader(request, *, allow_remote=False):
        return _fake_window(request, data_factory(request))

    return reader


def test_mola_global_exports_goal_b_api():
    assert callable(mg.analyze_mars_bbox_d01)
    assert pytest.approx(463.0576672) == mg.MEG128_RESOLUTION_M
    assert mg.MARS_FRAME == "mars_planetocentric"
    assert set(D01_FEATURE_IDS) == {
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
        "flat_terrain_fraction",
    }
    assert mg.MolaGlobalError is MolaAoiError


def test_tiles_for_bbox_covers_low_mid_high_latitudes():
    low = tiles_for_bbox(JEZERO["west"], JEZERO["south"], JEZERO["east"], JEZERO["north"])
    mid = tiles_for_bbox(HELLAS["west"], HELLAS["south"], HELLAS["east"], HELLAS["north"])
    high = tiles_for_bbox(OLYMPIA["west"], OLYMPIA["south"], OLYMPIA["east"], OLYMPIA["north"])
    assert low and mid and high
    assert {t.tile_id for t in low} != {t.tile_id for t in mid}
    assert {t.tile_id for t in mid} != {t.tile_id for t in high}


def test_validate_rejects_polar_cap_outside_meg128():
    with pytest.raises(MolaAoiError) as exc:
        validate_mola_aoi({"west": 0.0, "south": 88.5, "east": 1.0, "north": 89.0})
    assert exc.value.code == "unsupported_latitude"


def test_validate_rejects_oversized_aoi():
    with pytest.raises(MolaAoiError) as exc:
        validate_mola_aoi({"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0})
    assert exc.value.code == "aoi_too_large"


def test_validate_rejects_invalid_latitude_order():
    with pytest.raises(MolaAoiError) as exc:
        validate_mola_aoi({"west": 10.0, "south": 20.0, "east": 11.0, "north": 20.0})
    assert exc.value.code == "invalid_bbox"


def test_analyze_all_nodata_returns_no_data_not_zero_fill():
    payload = analyze_mola_aoi(
        HELLAS,
        label="all-nodata",
        window_reader=_reader_for(lambda req: np.full(req.grid_shape, np.nan, dtype="float64")),
    )
    assert payload["status"] == "no_data"
    assert payload["features"] is None
    assert payload["valid_fraction"] == 0.0
    assert payload["valid_pixels"] == 0
    assert "no_data" in payload["qa_flags"]


def test_analyze_partial_nodata_reports_valid_fraction_and_features():
    def factory(req):
        rows, cols = req.grid_shape
        elevation = (
            np.tile(np.arange(cols, dtype="float64"), (rows, 1)) * ANALYSIS_RESOLUTION_M * 0.05
        )
        elevation[0, :] = np.nan
        return elevation

    payload = analyze_mola_aoi(HELLAS, label="partial", window_reader=_reader_for(factory))
    assert payload["status"] == "partial"
    assert 0.0 < payload["valid_fraction"] < 1.0
    assert payload["features"] is not None
    assert set(payload["features"]) == set(D01_FEATURE_IDS)


def test_analyze_complete_synthetic_plane_returns_five_features():
    def factory(req):
        rows, cols = req.grid_shape
        xs = np.arange(cols, dtype="float64")
        ys = np.arange(rows, dtype="float64")[:, None]
        return -2500.0 + 0.02 * xs * ANALYSIS_RESOLUTION_M + 0.01 * ys * ANALYSIS_RESOLUTION_M

    payload = analyze_mola_aoi(JEZERO, label="plane", window_reader=_reader_for(factory))
    assert payload["status"] == "complete"
    assert payload["valid_fraction"] == 1.0
    assert set(payload["features"]) == set(D01_FEATURE_IDS)
    for fid in D01_FEATURE_IDS:
        assert math.isfinite(payload["features"][fid]["value"])
    assert payload["data_kind"] == "observed"
    assert payload["scope"] == "bounded_full_bbox_aoi"
    assert payload["coordinate_frame"] == "mars_planetocentric"
    assert payload["resolution_m"] == pytest.approx(463.0576672)
    assert payload["checksum"].startswith("sha256:")
    assert payload["source"]["source_id"] == "mars-mola-megdr-meg128-t44n000"
    assert "PDS4" in payload["source_version"]


def test_source_disconnect_is_explicit():
    def boom(request, *, allow_remote=False):
        raise MolaAoiError("source_read_failed", "simulated disconnect")

    with pytest.raises(MolaAoiError) as exc:
        analyze_mola_aoi(JEZERO, allow_remote=True, window_reader=boom)
    assert exc.value.code == "source_read_failed"
    assert "disconnect" in exc.value.message


def test_mola_global_analyze_delegates(monkeypatch):
    called = {}

    def fake(b, *, label="custom-mars-aoi", allow_remote=False, window_reader=None):
        called["bbox"] = b
        called["label"] = label
        called["allow_remote"] = allow_remote
        return {"status": "complete", "features": {}}

    monkeypatch.setattr(mg, "analyze_mola_aoi", fake)
    result = mg.analyze_mars_bbox_d01(JEZERO, label="x", allow_remote=True)
    assert result["status"] == "complete"
    assert called["allow_remote"] is True
    assert called["label"] == "x"


def test_three_dispersed_presets_cover_low_mid_high_latitudes():
    presets = [JEZERO, HELLAS, OLYMPIA]
    requests = [validate_mola_aoi(b) for b in presets]
    bands = {r.latitude_band for r in requests}
    assert {"low", "mid", "high"}.issubset(bands)
    centers = [r.center_lonlat for r in requests]
    assert len({(round(c[0], 1), round(c[1], 1)) for c in centers}) == 3


def test_evidence_file_has_three_dispersed_real_aois():
    evidence_path = (
        Path(__file__).resolve().parents[3]
        / "pipelines/planetary/evidence/mars_mola_aoi_evidence.json"
    )
    if not evidence_path.exists():
        pytest.skip("evidence file not present")
    doc = json.loads(evidence_path.read_text(encoding="utf-8"))
    aois = doc.get("aois") or doc.get("results") or []
    assert len(aois) >= 3
    bands = {a.get("latitude_band") for a in aois}
    assert {"low", "mid", "high"}.issubset(bands)
    labels = {a.get("label") for a in aois}
    assert len(labels) >= 3
    for a in aois:
        assert a.get("status") in {"complete", "partial"}
        assert a.get("valid_fraction", 0) > 0
        assert str(a.get("checksum", "")).startswith("sha256:")
        feats = a.get("features") or {}
        assert set(feats) == set(D01_FEATURE_IDS)
        assert a.get("source", {}).get("source_version")


def test_aoi_result_cache_is_versioned_checksumed_and_rebuildable(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake(b, *, label="custom-mars-aoi", allow_remote=False, window_reader=None):
        calls["n"] += 1
        return {
            "schema_version": "mars-mola-aoi-result-1.0.0",
            "checksum": "sha256:abc",
            "method_version": mg.D01_METHOD_VERSION,
            "source_version": mg.D01_SOURCE_VERSION,
            "requested_bbox_lonlat": b,
            "status": "complete",
            "features": {"slope_mean_deg": {"value": 1.0}},
        }

    monkeypatch.setattr(mg, "analyze_mola_aoi", fake)
    bbox = {"west": 77.35, "south": 18.35, "east": 77.55, "north": 18.53}
    r1 = mg.analyze_mars_bbox_d01(bbox, cache_dir=tmp_path, label="t")
    r2 = mg.analyze_mars_bbox_d01(bbox, cache_dir=tmp_path, label="t")
    assert calls["n"] == 1
    assert r1["cache"]["cache_hit"] is False
    assert r2["cache"]["cache_hit"] is True
    assert r1["cache"]["cache_version"] == mg.AOI_CACHE_VERSION
    assert (
        r1["cache"]["cache_key"] == r2["cache_key"]
        if "cache_key" in r2
        else r1["cache"]["cache_key"]
    )
    assert r1["cache"]["cache_result_sha256"].startswith("sha256:")
    meta = tmp_path / r1["cache"]["cache_key"][:2] / f"{r1['cache']['cache_key']}.meta.json"
    assert meta.is_file()
    # rebuildable: delete cache and recompute
    for p in tmp_path.rglob("*"):
        if p.is_file():
            p.unlink()
    r3 = mg.analyze_mars_bbox_d01(bbox, cache_dir=tmp_path, label="t")
    assert calls["n"] == 2
    assert r3["cache"]["cache_hit"] is False
