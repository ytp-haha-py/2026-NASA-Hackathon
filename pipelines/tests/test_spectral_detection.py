"""Offline checks for the D02 bounded spectral-detection pilot (Issue #42).

Synthetic arrays are TEST ONLY. No synthetic spectrum becomes an observation.
The committed pilot observations come from ``windows.json`` and are all
``unknown`` because the decoder failed reproduction.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from analog_pipelines.geology.spectral_detection import (
    ABUNDANCE_UNITS,
    AOI_WINDOWS,
    D2300_BANDS,
    DETECTION_THRESHOLDS,
    FEATURE_D2300,
    FEATURE_OLINDEX3,
    FEATURE_SEMANTICS,
    IF_BANDS,
    IF_BYTES,
    IF_LINES,
    IF_SAMPLES,
    LABEL_EAST_LONGITUDE,
    LABEL_MAX_LATITUDE,
    LABEL_MIN_LATITUDE,
    LABEL_WEST_LONGITUDE,
    OLINDEX3_WEIGHTS,
    PRODUCT_ID,
    REPRODUCTION_TOLERANCE,
    SpectralDetectionError,
    SpectralObservation,
    aoi_pixel_window,
    band_depth,
    build_observation,
    classify,
    d2300,
    linear_continuum,
    lonlat_to_pixel,
    nearest_band_indices,
    olindex3,
    pixel_to_lonlat,
    read_bsq_window,
    summary_stats,
)

HERE = Path(__file__).resolve().parents[2]
PILOT = HERE / "pipelines/pilots/d02_spectral_detection"


def synthetic_wavelengths() -> np.ndarray:
    # 1 nm spacing from 400 to 3999 nm; only used to isolate the formula.
    return np.arange(400.0, 4000.0, 1.0)


def flat_spectrum(level: float = 1.0) -> np.ndarray:
    return np.full(synthetic_wavelengths().shape, level)


def test_band_layout_is_the_bounded_official_product():
    assert IF_SAMPLES == 455 and IF_LINES == 751 and IF_BANDS == 489
    assert IF_BYTES == IF_SAMPLES * IF_LINES * IF_BANDS * 4
    assert IF_BYTES == 668_374_980


def test_nearest_band_indices_respect_the_documented_kernel():
    wavelengths = np.array([1000.0, 1080.0, 1150.0, 1210.0, 1300.0])
    assert nearest_band_indices(wavelengths, 1080.0, 3) == [0, 1, 2]
    assert nearest_band_indices(wavelengths, 1210.0, 1) == [3]
    with pytest.raises(SpectralDetectionError, match="odd"):
        nearest_band_indices(wavelengths, 1080.0, 2)
    with pytest.raises(SpectralDetectionError, match="leaves the band range"):
        nearest_band_indices(wavelengths, 1000.0, 5)


def test_linear_continuum_matches_hand_computation():
    assert linear_continuum(1815.0, 0.5, 2530.0, 0.3, 1815.0) == pytest.approx(0.5)
    assert linear_continuum(1815.0, 0.5, 2530.0, 0.3, 2530.0) == pytest.approx(0.3)
    assert linear_continuum(0.0, 0.0, 100.0, 1.0, 25.0) == pytest.approx(0.25)
    with pytest.raises(SpectralDetectionError, match="increase"):
        linear_continuum(10.0, 1.0, 10.0, 1.0, 10.0)
    with pytest.raises(SpectralDetectionError, match="outside"):
        linear_continuum(10.0, 1.0, 20.0, 1.0, 25.0)


def test_band_depth_is_zero_on_a_flat_continuum_and_positive_on_an_absorption():
    wavelengths = synthetic_wavelengths()
    flat = flat_spectrum(0.5)
    assert band_depth(wavelengths, flat, 1000.0, 900.0, 1100.0) == pytest.approx(0.0)

    dipped = flat.copy()
    dipped[np.abs(wavelengths - 1000.0) <= 10.0] = 0.4
    depth = band_depth(wavelengths, dipped, 1000.0, 900.0, 1100.0)
    assert depth == pytest.approx(0.2, abs=1e-6)
    # A parameter value is a ratio, never an abundance unit.
    assert depth <= 1.0


def test_d2300_is_zero_on_a_flat_spectrum_and_positive_on_a_2p3um_dropoff():
    wavelengths = synthetic_wavelengths()
    assert d2300(wavelengths, flat_spectrum()) == pytest.approx(0.0, abs=1e-9)

    spectrum = flat_spectrum()
    spectrum[(wavelengths >= 2280.0) & (wavelengths <= 2340.0)] = 0.9
    value = d2300(wavelengths, spectrum)
    assert value > 0.0
    assert value < 1.0


def test_olindex3_is_zero_on_a_flat_spectrum_and_positive_for_a_1um_absorption():
    wavelengths = synthetic_wavelengths()
    assert olindex3(wavelengths, flat_spectrum()) == pytest.approx(0.0, abs=1e-9)

    spectrum = flat_spectrum()
    spectrum[(wavelengths >= 1080.0) & (wavelengths <= 1470.0)] = 0.9
    value = olindex3(wavelengths, spectrum)
    assert value > 0.0
    assert sum(OLINDEX3_WEIGHTS.values()) == pytest.approx(1.0)


def test_parameter_band_tables_are_the_documented_reference_wavelengths():
    assert set(D2300_BANDS) == {1815.0, 2120.0, 2170.0, 2210.0, 2290.0, 2320.0, 2330.0, 2530.0}
    assert set(OLINDEX3_WEIGHTS) == set(
        {1080.0, 1152.0, 1210.0, 1250.0, 1263.0, 1276.0, 1330.0, 1368.0, 1395.0, 1427.0, 1470.0}
    )
    assert DETECTION_THRESHOLDS[FEATURE_D2300] == 0.02
    assert DETECTION_THRESHOLDS[FEATURE_OLINDEX3] == 0.10
    assert REPRODUCTION_TOLERANCE == 0.001


def test_geolocation_reproduces_the_label_corners_and_round_trips():
    corners = [
        (LABEL_WEST_LONGITUDE, LABEL_MAX_LATITUDE),
        (LABEL_EAST_LONGITUDE, LABEL_MIN_LATITUDE),
    ]
    for lon, lat in corners:
        column, row = lonlat_to_pixel(lon, lat)
        lon2, lat2 = pixel_to_lonlat(column, row)
        assert lon2 == pytest.approx(lon, abs=1e-9)
        assert lat2 == pytest.approx(lat, abs=1e-9)
    col0, row0 = lonlat_to_pixel(LABEL_WEST_LONGITUDE, LABEL_MAX_LATITUDE)
    assert col0 == pytest.approx(0.5, abs=0.5)
    assert row0 == pytest.approx(0.5, abs=0.5)


def test_aoi_windows_match_the_committed_pixel_indices():
    for _area_id, window in AOI_WINDOWS.items():
        computed = aoi_pixel_window(window["bbox_lon_lat"])
        assert computed["columns"] == window["columns"]
        assert computed["rows"] == window["rows"]
        assert computed["pixel_count"] == window["pixel_count"]


def test_classify_keeps_no_coverage_unknown_and_unvalidated_distinct():
    assert classify(FEATURE_D2300, True, valid_pixels=0, total_pixels=0, value=None)["state"] == (
        "no_coverage"
    )
    unmeasured = classify(FEATURE_D2300, True, valid_pixels=None, total_pixels=100, value=0.5)
    assert unmeasured["state"] == "unknown" and unmeasured["valid_fraction"] is None

    unvalidated = classify(FEATURE_D2300, False, valid_pixels=80, total_pixels=100, value=0.5)
    assert unvalidated["state"] == "unknown"
    assert unvalidated["value"] is None
    assert unvalidated["valid_fraction"] == pytest.approx(0.8)

    detected = classify(FEATURE_D2300, True, valid_pixels=80, total_pixels=100, value=0.5)
    assert detected["state"] == "detected" and detected["value"] == 0.5

    below = classify(FEATURE_D2300, True, valid_pixels=80, total_pixels=100, value=0.001)
    assert below["state"] == "not_detected_under_valid_observation"
    assert below["value"] is None


def test_unknown_and_non_detection_never_carry_a_numeric_zero():
    unknown = build_observation(
        FEATURE_D2300,
        "synthetic-area",
        state="unknown",
        decoder_validated=False,
        value=None,
        valid_fraction=None,
    )
    assert unknown.value is None
    with pytest.raises(Exception, match="categorical"):
        build_observation(
            FEATURE_D2300,
            "synthetic-area",
            state="unknown",
            decoder_validated=False,
            value=0.0,
        )
    with pytest.raises(Exception, match="categorical"):
        build_observation(
            FEATURE_D2300,
            "synthetic-area",
            state="not_detected_under_valid_observation",
            decoder_validated=True,
            value=0.0,
            valid_fraction=0.5,
        )
    with pytest.raises(Exception, match="valid observed fraction"):
        build_observation(
            FEATURE_D2300,
            "synthetic-area",
            state="not_detected_under_valid_observation",
            decoder_validated=True,
            value=None,
            valid_fraction=0.0,
        )


def test_unvalidated_decoder_can_never_emit_detected():
    with pytest.raises(Exception, match="unvalidated decoder"):
        build_observation(
            FEATURE_D2300,
            "synthetic-area",
            state="detected",
            decoder_validated=False,
            value=0.2,
            valid_fraction=0.5,
        )
    detected = build_observation(
        FEATURE_D2300,
        "synthetic-area",
        state="detected",
        decoder_validated=True,
        value=0.2,
        valid_fraction=0.5,
    )
    assert detected.state == "detected" and detected.value == 0.2


def test_observation_rejects_abundance_units_and_unknown_features():
    payload = build_observation(
        FEATURE_D2300,
        "synthetic-area",
        state="unknown",
        decoder_validated=False,
    ).model_dump()
    with pytest.raises(Exception, match="dimensionless_spectral_parameter"):
        SpectralObservation.model_validate(payload | {"unit": "wt%"})
    with pytest.raises(Exception, match="unknown spectral feature"):
        build_observation(
            "not_a_feature",
            "synthetic-area",
            state="unknown",
            decoder_validated=False,
        )


def test_fe_oxide_and_mafic_semantics_never_become_aqueous_alteration():
    mafic = FEATURE_SEMANTICS[FEATURE_OLINDEX3]
    assert mafic["aqueous_alteration_claim"] == "forbidden"
    assert "aqueous" not in mafic["meaning"].lower()
    assert "abundance" in mafic["never_means"]
    assert "aqueous alteration" in mafic["never_means"]
    assert FEATURE_SEMANTICS[FEATURE_D2300]["aqueous_alteration_claim"] == (
        "candidate_indicator_only"
    )
    for semantics in FEATURE_SEMANTICS.values():
        assert semantics["quantity_class"] == "dimensionless_spectral_parameter"
    assert not ABUNDANCE_UNITS & {
        semantics["quantity_class"] for semantics in FEATURE_SEMANTICS.values()
    }


def test_bsq_range_reader_reads_only_the_requested_window():
    samples, lines, bands = 4, 3, 2
    cube = np.arange(bands * lines * samples, dtype="<f4").reshape(bands, lines, samples)
    calls: list[tuple[int, int]] = []

    def source(url: str, start: int, end: int) -> bytes:
        assert url == "synthetic://cube"
        calls.append((start, end))
        return cube.tobytes()[start : end + 1]

    window = read_bsq_window(
        source,
        "synthetic://cube",
        samples=samples,
        lines=lines,
        band=1,
        row0=1,
        row1=3,
        col0=1,
        col1=3,
    )
    assert window.tolist() == cube[1, 1:3, 1:3].tolist()
    assert len(calls) == 1
    block = samples * lines * 4
    assert calls[0][0] >= block  # never reads the preceding band
    assert calls[0][1] - calls[0][0] + 1 == 2 * samples * 4  # only two rows

    with pytest.raises(SpectralDetectionError, match="column window"):
        read_bsq_window(
            source,
            "synthetic://cube",
            samples=samples,
            lines=lines,
            band=0,
            row0=0,
            row1=1,
            col0=3,
            col1=5,
        )


def test_summary_stats_ignore_non_finite_values():
    stats = summary_stats(np.array([1.0, np.nan, 3.0, np.inf]))
    assert stats == {"count": 2, "median": 2.0, "min": 1.0, "max": 3.0}
    assert summary_stats(np.array([np.nan])) == {
        "count": 0,
        "median": None,
        "min": None,
        "max": None,
    }


def test_committed_evidence_names_exact_products_urls_sizes_and_errors():
    windows = json.loads((PILOT / "windows.json").read_text(encoding="utf-8"))
    sources = json.loads((PILOT / "sources.json").read_text(encoding="utf-8"))

    assert windows["product"]["product_id"] == PRODUCT_ID
    assert windows["product"]["assets"][0]["bytes"] == IF_BYTES
    for asset in windows["product"]["assets"]:
        if "sha256" in asset:
            assert len(asset["sha256"]) == 64
    assert {source["body"] for source in sources} == {"Moon", "Mars"}
    for source in sources:
        assert source["url"].startswith("https://")
        assert source["access_status"] == "open"
        assert source["credentials_required"] is False
        assert source["limitations"]

    attempt = windows["reproduction_attempt"]
    assert attempt["decoder_validated"] is False
    assert attempt["verdict"] == "failed_reproduction_tolerance"
    assert attempt["acceptance_tolerance_abs"] == REPRODUCTION_TOLERANCE
    for values in attempt["subwindows"].values():
        assert values["d2300_rmse"] > REPRODUCTION_TOLERANCE
        assert values["olindex3_rmse"] > REPRODUCTION_TOLERANCE
    assert windows["blockers"]
    assert any("threshold" in blocker["blocker_id"] for blocker in windows["blockers"])


def test_committed_observations_are_unknown_and_never_abundance():
    rows = [
        json.loads(line)
        for line in (PILOT / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 4
    assert {row["state"] for row in rows} == {"unknown"}
    assert all(row["value"] is None for row in rows)
    assert all(row["unit"] == "dimensionless_spectral_parameter" for row in rows)
    assert all(row["unit"] not in ABUNDANCE_UNITS for row in rows)
    assert all(row["decoder_validated"] is False for row in rows)
    assert all(row["threshold_status"] == "explicit_pilot_unvalidated" for row in rows)
    assert {row["area_id"] for row in rows} == set(AOI_WINDOWS)
    assert {row["feature_id"] for row in rows} == {FEATURE_D2300, FEATURE_OLINDEX3}


def test_committed_manifest_report_and_observations_are_reproducible():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_spectral_detection_pilot", HERE / "scripts/build_spectral_detection_pilot.py"
    )
    script = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(script)

    payload = script.build(decoder_validated=False)
    assert json.loads((PILOT / "manifest.json").read_text(encoding="utf-8")) == payload["manifest"]
    assert (PILOT / "REPORT.md").read_text(encoding="utf-8") == script.render_report(payload)
    committed = hashlib.sha256((PILOT / "observations.jsonl").read_bytes()).hexdigest()
    rebuilt = hashlib.sha256(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in payload["observations"]).encode()
    ).hexdigest()
    assert committed == rebuilt
    assert payload["manifest"]["counts"]["numeric_values"] == 0
    assert payload["manifest"]["counts"]["abundance_values"] == 0
    assert payload["manifest"]["counts"]["by_state"]["detected"] == 0
    assert payload["manifest"]["counts"]["by_state"]["not_detected_under_valid_observation"] == 0
