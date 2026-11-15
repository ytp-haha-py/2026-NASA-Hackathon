"""Offline tests for the #170 D03/D04 AOI evidence package."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from analog_pipelines.aoi_thermal_water import (
    PRP_INVALID_DEPTH_M,
    gcp_brightness_state,
    gcp_cell_center,
    gcp_cell_indices,
    gcp_row_byte_range,
    gcp_row_index,
    index_existing_observations,
    odyssey_ns_cell_center,
    odyssey_ns_cell_indices,
    odyssey_ns_row_index,
    odyssey_ns_state,
    parse_gcp_row,
    parse_odyssey_ns_row,
    parse_prp_row,
    prp_aggregate,
    prp_valid_ice_depth,
    select_prp_triangles,
    tes_mask_byte_range,
    tes_pixel_center,
    tes_pixel_index,
    tes_pixel_state,
    tes_value_byte_range,
)
from analog_pipelines.typed_contracts import AreaV2, DatasetV2, SourceAssetV2, TypedObservation

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "pipelines" / "d02_d04_aoi" / "thermal_water"


def test_gcp_cell_and_row_index_match_published_layout() -> None:
    assert gcp_cell_indices(-179.75, -89.75) == (0, 0)
    assert gcp_cell_indices(179.75, -80.25) == (719, 19)
    assert gcp_cell_center(0, 0) == (-179.75, -89.75)
    assert gcp_row_index(0, 0, 0) == 0
    assert gcp_row_index(0, 0, 48) == 48
    assert gcp_row_index(1, 0, 0) == 20 * 96
    start, end = gcp_row_byte_range(0)
    assert (start, end) == (0, 112)
    assert end - start + 1 == 113


def test_gcp_row_parser_and_missing_states() -> None:
    row = (
        "-179.75, -89.75,   0.12,  149.8113,  153.2071,  152.2686,  100.2653,"
        "   92.9009,   78.2395,   78.6598,   91.7670\r\n"
    )
    parsed = parse_gcp_row(row)
    assert parsed["clon"] == -179.75
    assert parsed["clat"] == -89.75
    assert parsed["tbol"] == pytest.approx(91.7670)
    assert gcp_brightness_state(parsed["tbol"]) == ("detected", 91.7670, None)
    assert gcp_brightness_state(-9999.0) == (
        "no_coverage",
        None,
        "gcp_no_observation_constant",
    )
    assert gcp_brightness_state(-5.0) == (
        "unknown",
        None,
        "negative_radiance_below_channel_sensitivity",
    )


def test_tes_pixel_mapping_is_one_based_in_provenance() -> None:
    line, sample = tes_pixel_index(18.220253246764436, 77.37095200216012)
    assert (line, sample) == (1435, 5147)
    lat, lon = tes_pixel_center(line, sample)
    assert lat == pytest.approx(18.225)
    assert lon == pytest.approx(77.375)
    vstart, vend = tes_value_byte_range(line, sample)
    mstart, mend = tes_mask_byte_range(line, sample)
    assert (vstart, vend) == ((1435 * 7200 + 5147) * 2, (1435 * 7200 + 5147) * 2 + 1)
    assert (mstart, mend) == (1435 * 7200 + 5147, 1435 * 7200 + 5147)


def test_tes_state_uses_mask_and_published_range() -> None:
    assert tes_pixel_state(275, 1) == ("detected", 275.0, None)
    assert tes_pixel_state(275, 0) == (
        "no_coverage",
        None,
        "interpolated_pixel_excluded_by_mask",
    )
    assert tes_pixel_state(0, 1) == (
        "unknown",
        None,
        "value_outside_published_5_5000_range",
    )
    assert tes_pixel_state(6000, 1) == (
        "unknown",
        None,
        "value_outside_published_5_5000_range",
    )


def test_odyssey_ns_mapping_and_zero_is_not_physical() -> None:
    assert odyssey_ns_cell_indices(77.37, 18.22) == (154, 216)
    lon, lat = odyssey_ns_cell_center(*odyssey_ns_cell_indices(77.37, 18.22))
    assert lon == pytest.approx(77.25)
    assert lat == pytest.approx(18.25)
    assert odyssey_ns_row_index(154, 216) == 155674
    row = parse_odyssey_ns_row(" 257.25,  18.25,   7.960\r\n")
    assert row["counts_per_second"] == pytest.approx(7.960)
    assert odyssey_ns_state(7.96) == ("detected", 7.96, None)
    assert odyssey_ns_state(0.0) == (
        "no_coverage",
        None,
        "non_positive_value_no_documented_fill_constant",
    )


def _prp_row(
    *,
    clat: float,
    clon: float,
    temp_avg: float = 200.0,
    temp_max: float = 300.0,
    ice_depth: float = 0.5,
    offset: float = 0.0,
) -> str:
    # Three vertices on a 1737.4 km sphere around a slightly offset centre.
    import math

    vertices = []
    for dlon, dlat in ((-0.001, -0.001), (0.001, -0.001), (0.0, 0.001)):
        lat = math.radians(clat + dlat + offset)
        lon = math.radians(clon + dlon + offset)
        vertices.append(
            (
                1737.4 * math.cos(lat) * math.cos(lon),
                1737.4 * math.cos(lat) * math.sin(lon),
                1737.4 * math.sin(lat),
            )
        )
    values = [value for vertex in vertices for value in vertex]
    values.extend([clon, clat, 0.0, temp_avg, temp_max, ice_depth])
    return ",".join(f"{value:12.5f}" for value in values) + "\r\n"


def test_prp_row_and_selection() -> None:
    row = parse_prp_row(_prp_row(clat=-89.77, clon=-156.5))
    assert row["clat"] == pytest.approx(-89.77)
    assert row["temp_avg"] == pytest.approx(200.0)
    assert row["ice_depth"] == pytest.approx(0.5)
    selected = select_prp_triangles([row], -89.77, -156.5, 990.0)
    assert len(selected) == 1
    assert selected[0]["area_km2"] > 0
    assert prp_valid_ice_depth(0.0) is True
    assert prp_valid_ice_depth(PRP_INVALID_DEPTH_M) is False


def test_prp_aggregate_retains_physical_zero_and_excludes_fill() -> None:
    rows = [
        parse_prp_row(_prp_row(clat=-89.77, clon=-156.5, ice_depth=0.0)),
        parse_prp_row(_prp_row(clat=-89.77, clon=-156.5, ice_depth=1.0, offset=0.0001)),
        parse_prp_row(_prp_row(clat=-89.77, clon=-156.5, ice_depth=2.0, offset=0.0002)),
    ]
    # Make the third row an excluded value without rewriting the helper.
    rows[2]["ice_depth"] = PRP_INVALID_DEPTH_M
    for row in rows:
        row["area_km2"] = 0.1
    aggregate = prp_aggregate(rows, window_size_m=1980.0)
    assert aggregate["state"] == "detected"
    assert aggregate["temperature"]["annual_mean_2cm_k"] == pytest.approx(200.0)
    assert aggregate["ice_depth"]["surface_stable_triangle_count"] == 1
    assert aggregate["ice_depth"]["excluded_gt_2_8738m_count"] == 1
    assert aggregate["ice_depth"]["valid_triangle_count"] == 2
    assert aggregate["ice_depth"]["mean_stability_depth_m"] == pytest.approx(0.5)


def test_prp_aggregate_without_triangles_is_no_coverage() -> None:
    aggregate = prp_aggregate([], window_size_m=1980.0)
    assert aggregate["state"] == "no_coverage"
    assert aggregate["temperature"] is None
    assert aggregate["ice_depth"] is None


def test_existing_observation_index_uses_member_references() -> None:
    observations = [
        {
            "observation_id": "a",
            "dimension_code": "D04",
            "legacy_provenance": {"member_reference_ids": ["moon-x", "moon-y"]},
        },
        {
            "observation_id": "b",
            "dimension_code": "D03",
            "legacy_provenance": {"reference_id": "mars-z"},
        },
    ]
    index = index_existing_observations(observations)
    assert [row["observation_id"] for row in index["moon-x"]] == ["a"]
    assert [row["observation_id"] for row in index["moon-y"]] == ["a"]
    assert [row["observation_id"] for row in index["mars-z"]] == ["b"]


# ---------------------------------------------------------------------------
# Committed artifact consistency
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_committed_artifacts_are_present_and_valid() -> None:
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    observations = _read_jsonl(OUTPUT / "observations.jsonl")
    modeled = _read_jsonl(OUTPUT / "modeled_evidence.jsonl")
    datasets = json.loads((OUTPUT / "datasets.json").read_text(encoding="utf-8"))
    sources = json.loads((OUTPUT / "sources.json").read_text(encoding="utf-8"))
    areas = json.loads((OUTPUT / "areas.json").read_text(encoding="utf-8"))
    assets = json.loads((OUTPUT / "assets.json").read_text(encoding="utf-8"))
    aoi = json.loads((OUTPUT / "aoi_evidence.json").read_text(encoding="utf-8"))

    assert manifest["reference_count"] == 23
    assert manifest["moon_reference_count"] == 18
    assert manifest["mars_reference_count"] == 5
    assert len(observations) == manifest["observation_count"]
    assert len(modeled) == manifest["modeled_evidence_count"]
    assert len(aoi["aois"]) == 23

    assert set(sources["new_sources"]) == {
        "diviner-gcp",
        "tes-timap",
        "diviner-prp",
        "odyssey-ns",
    }
    assert len(assets) == 4
    for payload in datasets:
        DatasetV2.model_validate(payload)
    for payload in areas:
        AreaV2.model_validate(payload)
    for payload in assets:
        SourceAssetV2.model_validate(payload)
    for payload in observations:
        TypedObservation.model_validate(payload)

    for row in observations:
        if row["state"] == "detected":
            assert row["value"] is not None
        else:
            assert row["value"] is None
    for row in modeled:
        if row["state"] == "detected":
            assert row["value"] is not None
        else:
            assert row["value"] is None

    source_status = manifest["source_status"]
    assert set(source_status) == {"diviner-gcp", "tes-timap", "diviner-prp", "odyssey-ns"}
    for status in source_status.values():
        assert status["state"] in {"ok", "partial"}
        assert status["errors"] == [] or status["state"] == "partial"

    assert (
        manifest["checksums"]["aoi_evidence.json"]
        == __import__("hashlib").sha256((OUTPUT / "aoi_evidence.json").read_bytes()).hexdigest()
    )


def test_every_aoi_has_explicit_d03_and_d04_evidence_or_status() -> None:
    aoi = json.loads((OUTPUT / "aoi_evidence.json").read_text(encoding="utf-8"))
    for entry in aoi["aois"]:
        assert entry["d03"]["existing_observations"] or entry["d03"]["new_evidence"]
        assert entry["d04"]["existing_observations"] or entry["d04"]["new_evidence"]
        for row in entry["d03"]["existing_observations"] + entry["d03"]["new_evidence"]:
            assert row["state"] in {
                "detected",
                "not_detected",
                "no_coverage",
                "unknown",
                "not_applicable",
            }
        for row in entry["d04"]["existing_observations"] + entry["d04"]["new_evidence"]:
            assert row["state"] in {
                "detected",
                "not_detected",
                "no_coverage",
                "unknown",
                "not_applicable",
            }
