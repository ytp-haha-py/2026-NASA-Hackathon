"""Offline tests for bounded planetary D03 thermal evidence (Issue #43)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from analog_pipelines.dimensions import (
    FEATURE_V2_BY_ID,
    validate_dimension_observation,
)
from analog_pipelines.thermal_planetary import (
    BRIGHTNESS_TEMPERATURE_FEATURE,
    MARS_TI_CRS,
    MARS_TI_NATIVE_RESOLUTION_M,
    MARS_TI_TILE,
    MARS_TI_UNIT,
    PCP_BINS,
    PCP_NATIVE_RESOLUTION_M,
    THERMAL_INERTIA_FEATURE,
    IsisCubeTile,
    WindowSamples,
    assert_no_diurnal_range,
    brightness_observation_payload,
    collect_ti_window,
    compute_window_statistics,
    decode_isis_tile,
    observed_state,
    parse_pcp_header,
    parse_pcp_row,
    pcp_normalized_xy,
    select_pcp_window,
    thermal_inertia_observation_payload,
)
from analog_pipelines.typed_contracts import TypedObservation

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "pipelines" / "pilots" / "d03_planetary_thermal"


def test_pcp_metadata_is_verified_and_bounded():
    assert len(PCP_BINS) == 2
    assert PCP_NATIVE_RESOLUTION_M == 240.0
    assert [item.local_time_label for item in PCP_BINS] == [
        "00:00-00:15",
        "12:00-12:15",
    ]
    for item in PCP_BINS:
        assert item.product_id.endswith("_240.TAB")
        assert item.url.startswith("https://pds-geosciences.wustl.edu/")
        assert item.source_bytes > 100_000_000


def test_pcp_normalized_xy_reproduces_published_row():
    # First data row of pcp_avg_tbol_pols_sum_ltim01_240.tab.
    x, y = pcp_normalized_xy(-80.003830, -90.271559)
    assert x == pytest.approx(-0.174908, abs=1e-6)
    assert y == pytest.approx(-0.000829, abs=1e-6)


def test_pcp_row_parsing_rejects_invalid_constants():
    assert parse_pcp_header("        x,         y,        clon,       clat,       tbol") == [
        "x",
        "y",
        "clon",
        "clat",
        "tbol",
    ]
    values = parse_pcp_row("-0.174908, -0.000829,  -90.271559, -80.003830,  75.385880")
    assert values[-1] == pytest.approx(75.38588)
    with pytest.raises(ValueError):
        parse_pcp_row("-9999, -0.000829, -90.271559, -80.003830, 75.385880")
    with pytest.raises(ValueError):
        parse_pcp_row("1,2,3")


def test_select_pcp_window_counts_sparse_bins_and_never_zero_fills(tmp_path: Path):
    path = tmp_path / "pcp.tab"
    lines = ["        x,         y,        clon,       clat,       tbol"]
    # Centre at lon 0, lat -89.9 -> x=0, y=2*tan(0.05 deg).
    center_y = pcp_normalized_xy(-89.9, 0.0)[1]
    for offset in (-0.00005, 0.0, 0.00005):
        lines.append(f"0.000000, {center_y + offset:.6f}, 0.000000, -89.900000, 120.000000")
    lines.append("0.010000, 0.010000, 1.000000, -89.000000, 200.000000")
    path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")

    samples = select_pcp_window(path, latitude_deg=-89.9, longitude_deg=0.0, radius_m=200.0)
    assert samples.n == 3
    stats = compute_window_statistics(samples)
    assert stats["mean"] == pytest.approx(120.0)
    assert stats["rejected"] == 0
    assert stats["valid_fraction"] > 0


def _brightness_payload(stats: dict[str, object]) -> dict[str, object]:
    return brightness_observation_payload(
        area_id="thermal-moon-test",
        reference_id="moon-test",
        display_name="Moon test window",
        latitude_deg=-89.9,
        longitude_deg=0.0,
        bin_metadata=PCP_BINS[0],
        stats=stats,
        window_size_m=1980.0,
        source_manifest="pipelines/planetary/manifests/lola_moon_reference.json",
        dataset_id="lro-diviner-pcp-pols-summer-240m",
        asset_id="pcp:test",
    )


def test_brightness_temperature_is_not_kinetic_and_not_diurnal():
    payload = _brightness_payload(
        {
            "n": 20,
            "mean": 120.0,
            "min": 100.0,
            "max": 140.0,
            "std": 5.0,
            "valid_fraction": 0.4,
            "rows_scanned": 10,
            "rejected": 0,
        }
    )
    observation = TypedObservation.model_validate(payload)
    assert validate_dimension_observation(observation) == []
    assert observation.feature_id == BRIGHTNESS_TEMPERATURE_FEATURE
    assert observation.unit == "K"
    assert observation.score_role == "evidence_only"
    assert observation.spatial_support.native_resolution_m == 240.0
    assert observation.spatial_support.resolution_m == 240.0
    assert "not_kinetic_surface_temperature" in observation.quality_flags
    assert "no_diurnal_range_computed" in observation.quality_flags
    assert observation.temporal_support.local_solar_time == "00:00-00:15"
    assert payload["legacy_provenance"]["diurnal_range_computed"] is False


def test_missing_states_carry_no_numeric_value():
    empty = _brightness_payload(
        {
            "n": 0,
            "mean": None,
            "min": None,
            "max": None,
            "std": None,
            "valid_fraction": 0.0,
            "rows_scanned": 10,
            "rejected": 0,
        }
    )
    assert empty["state"] == "no_coverage"
    assert empty["value"] is None
    TypedObservation.model_validate(empty)

    weak = _brightness_payload(
        {
            "n": 3,
            "mean": 120.0,
            "min": 110.0,
            "max": 130.0,
            "std": 5.0,
            "valid_fraction": 0.06,
            "rows_scanned": 10,
            "rejected": 0,
        }
    )
    assert weak["state"] == "unknown"
    assert weak["value"] is None
    TypedObservation.model_validate(weak)
    assert observed_state(0) == "no_coverage"
    assert observed_state(3) == "unknown"
    assert observed_state(5) == "detected"


def test_diurnal_range_is_refused_by_construction():
    payload = _brightness_payload(
        {
            "n": 20,
            "mean": 120.0,
            "min": 100.0,
            "max": 140.0,
            "std": 5.0,
            "valid_fraction": 0.4,
            "rows_scanned": 10,
            "rejected": 0,
        }
    )
    assert_no_diurnal_range([payload])
    forbidden = {**payload, "feature_id": "diurnal_temperature_range_k"}
    with pytest.raises(ValueError, match="diurnal range"):
        assert_no_diurnal_range([forbidden])


def test_thermal_inertia_is_a_separate_feature_and_unit():
    assert THERMAL_INERTIA_FEATURE != BRIGHTNESS_TEMPERATURE_FEATURE
    assert FEATURE_V2_BY_ID[THERMAL_INERTIA_FEATURE].unit == MARS_TI_UNIT
    assert FEATURE_V2_BY_ID[THERMAL_INERTIA_FEATURE].dimension_code == "D03"
    assert FEATURE_V2_BY_ID[BRIGHTNESS_TEMPERATURE_FEATURE].unit == "K"
    payload = thermal_inertia_observation_payload(
        area_id="thermal-mars-test",
        reference_id="mars-test",
        display_name="Mars test window",
        latitude_deg=18.22,
        longitude_deg=77.37,
        stats={
            "n": 300,
            "mean": 300.0,
            "min": 200.0,
            "max": 400.0,
            "std": 20.0,
            "valid_fraction": 0.97,
            "rows_scanned": 65_536,
            "rejected": 0,
        },
        window_size_m=1980.0,
        source_manifest="pipelines/planetary/manifests/ctx_mars_references.json",
        dataset_id="usgs-mars-themis-global-thermal-inertia-quantitative",
        asset_id="themis-ti:test",
        tile=MARS_TI_TILE,
    )
    observation = TypedObservation.model_validate(payload)
    assert validate_dimension_observation(observation) == []
    assert observation.body == "Mars"
    assert observation.unit == MARS_TI_UNIT
    assert observation.spatial_support.crs == MARS_TI_CRS
    assert observation.spatial_support.native_resolution_m == MARS_TI_NATIVE_RESOLUTION_M
    assert observation.uncertainty is not None
    assert observation.uncertainty.lower == pytest.approx(240.0)
    assert observation.uncertainty.upper == pytest.approx(360.0)


def test_isis_tile_geometry_matches_verified_label():
    tile = MARS_TI_TILE
    assert tile.samples == 35_566
    assert tile.lines == 17_783
    assert tile.tile_samples == 128
    assert tile.start_byte == 65_537
    assert tile.ulx_m == 3_556_400.0
    assert tile.uly_m == 1_778_300.0
    assert tile.tile_bytes == 65_536
    assert tile.tiles_per_row == math.ceil(35_566 / 128)
    sample, line = tile.sample_line_for(18.220253246764436, 77.37095200216012)
    assert sample == pytest.approx(10_296.4, abs=1.0)
    assert line == pytest.approx(6_982.5, abs=1.0)
    start, end = tile.tile_byte_range(0, 0)
    assert (start, end) == (65_536, 65_536 + 65_536 - 1)


def test_collect_ti_window_rejects_fill_without_zero_filling():
    tile = IsisCubeTile(
        product_id="synthetic",
        url="https://example.invalid/synthetic.cub",
        samples=8,
        lines=8,
        tile_samples=4,
        tile_lines=4,
        start_byte=65_537,
        ulx_m=0.0,
        uly_m=800.0,
        pixel_resolution_m=100.0,
        min_latitude_deg=0.0,
        max_latitude_deg=8.0,
        min_longitude_deg=0.0,
        max_longitude_deg=8.0,
    )
    values = [float(i) for i in range(16)]
    values[5] = float("nan")
    values[6] = -1.0
    values[7] = 0.0
    decoded = {(0, 0): tuple(values)}
    samples = collect_ti_window(
        tile=tile,
        sample=1.5,
        line=1.5,
        radius_m=240.0,
        decoded_tiles=decoded,
    )
    assert samples.n == 12
    assert samples.rejected == 4
    stats = compute_window_statistics(samples)
    assert stats["n"] == 12


def test_decode_isis_tile_enforces_payload_size():
    tile = IsisCubeTile(
        product_id="synthetic",
        url="https://example.invalid/synthetic.cub",
        samples=4,
        lines=4,
        tile_samples=2,
        tile_lines=2,
        start_byte=1,
        ulx_m=0.0,
        uly_m=0.0,
        pixel_resolution_m=1.0,
        min_latitude_deg=0.0,
        max_latitude_deg=0.0,
        min_longitude_deg=0.0,
        max_longitude_deg=0.0,
    )
    decoded = decode_isis_tile(b"\x00" * tile.tile_bytes, tile)
    assert len(decoded) == 4
    with pytest.raises(ValueError):
        decode_isis_tile(b"\x00", tile)


def test_committed_pilot_is_schema_valid_and_counts_match():
    if not PILOT.exists():
        pytest.skip("pilot export is not present")
    manifest = json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))
    rows = [
        TypedObservation.model_validate(json.loads(line))
        for line in (PILOT / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == manifest["counts"]["observations"]
    for observation in rows:
        assert validate_dimension_observation(observation) == []
        if observation.state == "detected":
            assert isinstance(observation.value, (int, float))
        else:
            assert observation.value is None
    feature_ids = {row.feature_id for row in rows}
    assert feature_ids == {
        BRIGHTNESS_TEMPERATURE_FEATURE,
        THERMAL_INERTIA_FEATURE,
    }
    assert_no_diurnal_range([row.model_dump(mode="json") for row in rows])
    # No value is presented on a 90 m support.
    for observation in rows:
        assert observation.spatial_support.resolution_m != 90.0
        assert observation.spatial_support.native_resolution_m in {100.0, 240.0}


def test_window_statistics_do_not_invent_extrema():
    stats = compute_window_statistics(
        WindowSamples(values=(), rows_scanned=100, rejected=2, expected_bins=50.0)
    )
    assert stats["n"] == 0
    assert stats["mean"] is None
    assert stats["min"] is None
    assert stats["max"] is None
    assert stats["valid_fraction"] == 0.0
