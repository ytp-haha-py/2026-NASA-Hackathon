"""Offline tests for the D04 water/ice evidence semantics and pilot artifact."""

from __future__ import annotations

import json
import math
import os
import struct
import urllib.request
from pathlib import Path

import pytest
from analog_pipelines.dimensions import FEATURE_V2_BY_ID
from analog_pipelines.typed_contracts import (
    DepthSupport,
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
)
from analog_pipelines.water_ice import (
    CANDIDATE_SOURCES,
    FEATURE_EPITHERMAL_NEUTRON_FLUX,
    FEATURE_RADAR_CPR,
    FEATURE_WATER_EQUIVALENT_HYDROGEN,
    FEATURE_WATER_ICE_DETECTION,
    SOURCES,
    DetectionClaim,
    HttpRangeReader,
    LatLonCell,
    MiniRfWindow,
    WaterIceError,
    build_detection_claim_observation,
    build_neutron_flux_observation,
    build_radar_cpr_observation,
    build_water_equivalent_hydrogen_observation,
    decode_mini_rf_window,
    find_cell,
    hydrogen_ice_claim,
    mini_rf_window,
    radar_ice_claim,
    read_hend_epithermal_ascii,
    read_lend_ald_binary,
    read_lp_hydrogen_ascii,
    sha256_file,
    summarize_window,
    validate_water_ice_observation,
)

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "pipelines" / "pilots" / "d04_water_ice_planetary"
LP = SOURCES["lp-ns-hydrogenhd"]
MINIRF = SOURCES["lro-minirf-cpr-128ppd"]
HEND = SOURCES["mro-hend-epithermal-020917"]


def lp_cell(value: float | None, *, lat: float = -89.75, lon: float = -157.25) -> LatLonCell:
    return LatLonCell(
        west=lon - 0.25,
        south=lat - 0.25,
        east=lon + 0.25,
        north=lat + 0.25,
        latitude_deg=lat,
        longitude_deg=lon,
        value=value,
    )


def weh_observation(value: float = 147.1) -> TypedObservation:
    return build_water_equivalent_hydrogen_observation(
        area_id="moon-test-cell",
        cell=lp_cell(value),
        source=LP,
        source_asset_id="lp:source",
        member_reference_ids=["moon-test-ref"],
        source_manifest="fixtures/manifest.json",
    )


def test_lp_ascii_keeps_physical_zero_and_missing_distinct(tmp_path: Path):
    path = tmp_path / "hydrogenhd.txt"
    path.write_text(
        "# header\n-90.00,-89.50,-157.50,-157.00,0.00\n-90.00,-89.50,-157.00,-156.50,**********\n",
        encoding="utf-8",
    )
    cells = read_lp_hydrogen_ascii(path)
    assert cells[0].value == 0.0
    assert cells[0].is_missing is False
    assert cells[1].value is None
    assert cells[1].is_missing is True

    zero = build_water_equivalent_hydrogen_observation(
        area_id="zero",
        cell=cells[0],
        source=LP,
        source_asset_id="lp:source",
        member_reference_ids=["ref"],
        source_manifest="fixtures/manifest.json",
    )
    assert zero.state == "detected"
    assert zero.value == 0.0
    with pytest.raises(WaterIceError, match="missing cell"):
        build_water_equivalent_hydrogen_observation(
            area_id="missing",
            cell=cells[1],
            source=LP,
            source_asset_id="lp:source",
            member_reference_ids=["ref"],
            source_manifest="fixtures/manifest.json",
        )


def test_weh_conversion_is_water_equivalent_not_ice_fraction():
    observation = weh_observation(147.1)
    assert observation.feature_id == FEATURE_WATER_EQUIVALENT_HYDROGEN
    assert observation.unit == "percent"
    assert observation.value == pytest.approx(147.1 * 9.0 / 10_000.0, rel=1e-12)
    assert observation.value == pytest.approx(0.13239, abs=1e-9)
    assert observation.depth_support.kind == "depth_interval"
    assert observation.depth_support.min_m == 0.0
    assert observation.depth_support.max_m == 1.0
    assert "water_equivalent_hydrogen_not_ice_fraction" in observation.quality_flags
    assert "not_confirmed_ice" in observation.quality_flags
    assert (observation.legacy_provenance or {})["hydrogen_ppm_by_weight"] == 147.1
    assert validate_water_ice_observation(observation) == []
    # The same number must never be relabelled as an ice detection or a ratio.
    assert observation.feature_id != FEATURE_WATER_ICE_DETECTION
    assert "ice_fraction" not in observation.unit


def test_negative_hydrogen_abundance_is_not_emitted_as_physics():
    with pytest.raises(WaterIceError, match="negative hydrogen"):
        weh_observation(-5.0)


def test_radar_window_uses_native_pixels_and_planetocentric_frame():
    window = mini_rf_window(-89.76196621, -157.06719662)
    assert window.lines_total == 23040
    assert window.samples_total == 46080
    assert window.lines == 8
    bbox = window.window_bbox()
    assert bbox["west"] <= -157.06719662 < bbox["east"]
    assert bbox["south"] <= -89.76196621 < bbox["north"]
    assert window.byte_length == 8 * 46080 * 4

    values = [0.4] * 32 + [0.8] * 31 + [1.2]
    summary = summarize_window(values)
    observation = build_radar_cpr_observation(
        area_id="moon-radar-window",
        window=window,
        summary=summary,
        source=MINIRF,
        source_asset_id="minirf:window",
        member_reference_ids=["moon-test-ref"],
        source_manifest="fixtures/manifest.json",
    )
    assert observation.unit == "1"
    assert observation.depth_support.kind == "surface"
    assert observation.spatial_support.resolution_m is None
    assert observation.spatial_support.native_resolution_m == pytest.approx(236.90117518867)
    assert observation.spatial_support.window_size_m == pytest.approx(8 * 236.90117518867)
    assert observation.spatial_support.window_size_m != 1980.0
    assert "EPSG:4326" not in observation.spatial_support.crs
    assert validate_water_ice_observation(observation) == []


def test_mini_rf_decode_distinguishes_zero_missing_and_nan():
    window = MiniRfWindow(
        line_first=2,
        sample_first=2,
        lines=2,
        samples=2,
        lines_total=4,
        samples_total=4,
        row_bytes=16,
    )
    payload = bytearray(32)
    struct.pack_into("<4f", payload, 0, 0.0, -3.4e38, -1.0, -1.0)
    struct.pack_into("<4f", payload, 16, float("nan"), 1.5, -1.0, -1.0)
    values = decode_mini_rf_window(bytes(payload), window)
    assert values == [0.0, None, None, 1.5]
    summary = summarize_window(values)
    assert summary.pixel_count == 4
    assert summary.valid_count == 2
    assert summary.minimum == 0.0
    assert summary.median == 0.75
    assert summary.maximum == 1.5
    assert summary.count_at_or_above_one == 1
    assert summary.valid_fraction == 0.5


def test_radar_claim_separates_no_coverage_not_detected_and_unknown():
    empty = summarize_window([None] * 64)
    assert empty.valid_count == 0
    assert radar_ice_claim(empty).state == "no_coverage"

    partial = summarize_window([0.5] * 8 + [None] * 56)
    assert radar_ice_claim(partial).state == "unknown"

    below = summarize_window([0.4] * 64)
    below_claim = radar_ice_claim(below)
    assert below_claim.state == "not_detected"
    assert below_claim.reason_code == "no_cpr_at_or_above_unity"

    above = summarize_window([0.4] * 63 + [1.3])
    above_claim = radar_ice_claim(above)
    assert above_claim.state == "unknown"
    assert above_claim.reason_code == "cpr_at_or_above_unity_not_diagnostic"


def test_neutron_products_never_produce_an_ice_detection():
    for source in (LP, HEND):
        claim = hydrogen_ice_claim(source)
        assert claim.state == "unknown"
        assert claim.reason_code == "neutron_hydrogen_is_not_ice"


def test_all_five_detection_states_round_trip_without_a_value():
    cell = lp_cell(140.0)
    observations = []
    for state in ("detected", "not_detected", "no_coverage", "unknown", "not_applicable"):
        observation = build_detection_claim_observation(
            area_id=f"cell-{state}",
            body="Moon",
            claim=DetectionClaim(state="unknown", reason_code="test", notes="test claim"),
            source=LP,
            source_asset_id="lp:source",
            member_reference_ids=["ref"],
            source_manifest="fixtures/manifest.json",
            bbox=cell.bbox,
            valid_fraction=None,
            support_notes="test support",
        )
        if state == "detected":
            # A hypothetically detected claim must carry a value in the typed contract.
            observation = observation.model_copy(
                update={"state": "detected", "value": {"indicator": 1}}
            )
        else:
            observation = observation.model_copy(update={"state": state, "value": None})
        assert observation.state == state
        observations.append(observation)
    assert {item.state for item in observations} == {
        "detected",
        "not_detected",
        "no_coverage",
        "unknown",
        "not_applicable",
    }
    assert observations[0].value == {"indicator": 1}
    assert all(item.value is None for item in observations[1:])
    # A missing state may not smuggle an abundance value.
    payload = observations[2].model_dump(mode="json")
    payload["value"] = {"indicator": 0}
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TypedObservation.model_validate(payload)


def test_d04_validator_rejects_overclaiming_and_cross_quantity_units():
    cell = lp_cell(140.0)
    detected_claim = build_detection_claim_observation(
        area_id="cell",
        body="Moon",
        claim=DetectionClaim(state="unknown", reason_code="test", notes="overclaim"),
        source=LP,
        source_asset_id="lp:source",
        member_reference_ids=["ref"],
        source_manifest="fixtures/manifest.json",
        bbox=cell.bbox,
        valid_fraction=1.0,
        support_notes="test",
    ).model_copy(update={"state": "detected", "value": {"indicator": 1}})
    errors = validate_water_ice_observation(detected_claim)
    assert any("no product that supports" in error for error in errors)

    weh = weh_observation()
    wrong_unit = weh.model_copy(update={"unit": "1"})
    assert any("mass percent" in error for error in validate_water_ice_observation(wrong_unit))

    wrong_crs = weh.model_copy(
        update={
            "spatial_support": SpatialSupport(kind="point", crs="EPSG:4326", notes="earth fixture")
        }
    )
    assert any("Earth CRS" in error for error in validate_water_ice_observation(wrong_crs))

    radar = build_radar_cpr_observation(
        area_id="radar",
        window=mini_rf_window(-89.7, -157.0),
        summary=summarize_window([0.5] * 64),
        source=MINIRF,
        source_asset_id="minirf:window",
        member_reference_ids=["ref"],
        source_manifest="fixtures/manifest.json",
    )
    wrong_depth = radar.model_copy(
        update={"depth_support": DepthSupport(kind="depth_interval", min_m=0.0, max_m=10.0)}
    )
    assert any(
        "not a depth retrieval" in error for error in validate_water_ice_observation(wrong_depth)
    )


def test_not_applicable_quantity_is_explicit_and_valueless():
    observation = TypedObservation(
        observation_id="obs",
        area_id="area",
        body="Moon",
        dimension_code="D04",
        feature_id=FEATURE_WATER_ICE_DETECTION,
        value_kind="detection",
        state="not_applicable",
        value=None,
        unit="detection",
        spatial_support=SpatialSupport(
            kind="window",
            crs="IAU_MOON planetocentric lon/lat",
            native_resolution_m=15_100.0,
        ),
        temporal_support=TemporalSupport(kind="unknown"),
        depth_support=DepthSupport(kind="surface"),
        method_version="water-ice-evidence-0.1.0",
        dataset_ids=["lp-ns-hydrogenhd"],
        source_asset_ids=["lp:source"],
        quality_flags=["not_confirmed_ice"],
        score_role="evidence_only",
        notes="Liquid surface water is not stable in vacuum; snow depth is not applicable.",
    )
    assert observation.value is None
    assert validate_water_ice_observation(observation) == []
    assert FEATURE_V2_BY_ID[FEATURE_WATER_ICE_DETECTION].value_kind == "detection"


def test_hend_parser_validates_grid_and_units(tmp_path: Path):
    path = tmp_path / "hend.tab"
    lines = []
    for lat_index in range(45):
        for lon_index in range(90):
            lat = -88.0 + 4 * lat_index
            lon = -178.0 + 4 * lon_index
            value = 0.5 if (lat, lon) != (20.0, 78.0) else 0.733
            lines.append(f"{lon:12.7f},{lat:14.6f},{value:10.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cells = read_hend_epithermal_ascii(path)
    assert len(cells) == 4050
    cell = find_cell(cells, 18.2, 77.4)
    assert (cell.latitude_deg, cell.longitude_deg) == (20.0, 78.0)
    assert cell.value == pytest.approx(0.733)

    observation = build_neutron_flux_observation(
        area_id="mars-cell",
        cell=cell,
        source=HEND,
        source_asset_id="hend:source",
        member_reference_ids=["mars-jezero-ctx-window-01"],
        source_manifest="fixtures/manifest.json",
    )
    assert observation.value == pytest.approx(0.733)
    assert observation.unit == "1"
    assert observation.depth_support.kind == "depth_interval"
    assert observation.body == "Mars"
    assert "EPSG:4326" not in observation.spatial_support.crs
    assert validate_water_ice_observation(observation) == []

    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(WaterIceError, match="expected 4050"):
        read_hend_epithermal_ascii(path)


def test_lend_binary_parser_keeps_zero_and_missing_separate():
    def record(rate: float, error: float = 0.31) -> bytes:
        raw = bytearray(310)
        raw[0:23] = b"2009-09-15T00:00:00.000"
        raw[23:46] = b"2011-12-11T00:00:00.000"
        struct.pack_into(">f", raw, 46, -89.75)
        struct.pack_into(">f", raw, 50, 202.75)
        struct.pack_into(">f", raw, 58, 70.0)
        struct.pack_into(">f", raw, 62, 14.9)
        struct.pack_into(">f", raw, 66, 0.42)
        struct.pack_into(">f", raw, 70, 70.0)
        struct.pack_into(">f", raw, 74, 8.7)
        struct.pack_into(">f", raw, 78, 0.35)
        struct.pack_into(">f", raw, 82, 70.0)
        struct.pack_into(">f", raw, 86, 0.0)
        struct.pack_into(">f", raw, 90, 0.0)
        struct.pack_into(">f", raw, 94, 70.0)
        struct.pack_into(">f", raw, 98, 0.0)
        struct.pack_into(">f", raw, 102, 0.0)
        struct.pack_into(">f", raw, 106, 70.0)
        struct.pack_into(">f", raw, 110, rate)
        struct.pack_into(">f", raw, 114, error)
        return bytes(raw)

    parsed = read_lend_ald_binary(record(0.0, error=0.0) + record(-1.0, error=-1.0))
    assert parsed[0].csetn_rate == 0.0
    assert parsed[0].csetn_error == 0.0
    assert parsed[1].csetn_rate is None
    assert parsed[1].csetn_error is None
    assert parsed[0].start_utc == "2009-09-15T00:00:00.000"
    with pytest.raises(WaterIceError, match="multiple of the record length"):
        read_lend_ald_binary(b"\x00" * 311)


def test_source_matrix_has_explicit_blockers_for_unused_products():
    lend = SOURCES["lro-lend-alds-20090915"]
    assert lend.verification_status == "accessible"
    assert any("normalization constants" in blocker for blocker in lend.blockers)
    ids = {candidate["dataset_id"] for candidate in CANDIDATE_SOURCES}
    assert {"lro-lamp-far-uv", "lcross-cabeus-impact", "mro-sharad-radargrams"} <= ids
    for candidate in CANDIDATE_SOURCES:
        assert candidate["blocker"].strip()
        assert candidate["verification_status"] in {"accessible", "identified"}


def test_committed_artifact_is_internally_consistent_and_honest():
    manifest = json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))
    observations = [
        TypedObservation.model_validate(json.loads(line))
        for line in (PILOT / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(observations) == manifest["counts"]["observations"]
    assert sha256_file(PILOT / "observations.jsonl") == manifest["observations_sha256"]
    assert sha256_file(PILOT / "plan.json") == manifest["plan_sha256"]
    assert sha256_file(PILOT / "assets.json") == manifest["assets_sha256"]

    for observation in observations:
        assert validate_water_ice_observation(observation) == []
        assert observation.score_role == "evidence_only"
        assert observation.body in {"Moon", "Mars"}
        assert observation.dimension_code == "D04"
        if observation.spatial_support.native_resolution_m is not None:
            assert observation.spatial_support.native_resolution_m != 90.0
    assert not any(
        item.state == "detected"
        for item in observations
        if item.feature_id == FEATURE_WATER_ICE_DETECTION
    )
    states = {item.state for item in observations if item.feature_id == FEATURE_WATER_ICE_DETECTION}
    assert states <= {"not_detected", "no_coverage", "unknown", "not_applicable"}
    assert "no_coverage" in states
    assert "not_detected" in states
    assert "unknown" in states
    assert "detected" not in states

    # Every existing Moon/Mars reference window is linked to at least one native
    # source observation, and every native area keeps its real resolution.
    linked: set[str] = set()
    for observation in observations:
        provenance = observation.legacy_provenance or {}
        linked.update(provenance.get("member_reference_ids", []))
    moon_manifests = sorted(
        (ROOT / "pipelines" / "planetary" / "manifests").glob("lola_*reference*.json")
    )
    moon_ids = set()
    for path in moon_manifests:
        moon_ids.update(ref["reference_id"] for ref in json.loads(path.read_text())["references"])
    mars = json.loads(
        (ROOT / "pipelines" / "planetary" / "manifests" / "ctx_mars_references.json").read_text()
    )
    mars_ids = {ref["reference_id"] for ref in mars["references"]}
    assert moon_ids | mars_ids <= linked
    assert len(moon_ids) == 18
    assert len(mars_ids) == 5

    for observation in observations:
        if observation.feature_id == FEATURE_RADAR_CPR:
            assert observation.spatial_support.native_resolution_m == pytest.approx(236.90117518867)
            assert observation.spatial_support.window_size_m is not None
            assert 1800.0 < observation.spatial_support.window_size_m < 2200.0
        if observation.feature_id in {
            FEATURE_WATER_EQUIVALENT_HYDROGEN,
            FEATURE_EPITHERMAL_NEUTRON_FLUX,
        }:
            assert observation.unit in {"percent", "1"}
            assert observation.depth_support.kind == "depth_interval"

    counts = manifest["counts"]
    assert counts["tasks_failed"] == 0
    assert counts["detected_claims"] == 0
    assert counts["claims_by_state"]["no_coverage"] == 5
    assert sum(counts["by_state"].values()) == len(observations)
    assert all(
        item not in {"similarity", "filter"} for item in {obs.score_role for obs in observations}
    )


def test_plan_is_bounded_and_never_uses_earth_frames():
    plan = json.loads((PILOT / "plan.json").read_text(encoding="utf-8"))
    assert len(plan["tasks"]) == 30
    for task in plan["tasks"]:
        assert task["body"] in {"Moon", "Mars"}
        assert "EPSG:4326" not in task["area"]["crs"]
        assert task["area"]["coordinate_frame"] in {
            "lunar_planetocentric",
            "mars_planetocentric",
        }
        if task["source_id"] == "lro-minirf-cpr-128ppd":
            assert int(task["parameters"]["byte_length"]) < 2_000_000
    assert plan["totals"]["by_dimension"] == {"D04": 30}


def test_math_is_finite_for_every_emitted_number():
    observations = [
        TypedObservation.model_validate(json.loads(line))
        for line in (PILOT / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for observation in observations:
        if observation.value is not None:
            assert isinstance(observation.value, (int, float))
            assert math.isfinite(float(observation.value))


@pytest.mark.network
def test_network_sources_are_reachable_opt_in(tmp_path: Path):
    if os.environ.get("RUN_WATER_ICE_NETWORK_SMOKE") != "1":
        pytest.skip("set RUN_WATER_ICE_NETWORK_SMOKE=1 to reach the public PDS products")
    target = PILOT / "manifest.json"
    assert target.is_file()
    # Mini-RF byte-range access is the only product that must support ranges.
    reader = HttpRangeReader(MINIRF.url)
    window = mini_rf_window(-89.76196621, -157.06719662)
    data = reader.read(window.byte_offset, window.byte_offset + window.byte_length - 1)
    values = decode_mini_rf_window(data, window)
    assert any(value is not None for value in values)
    # Small ASCII products are fetched in bounded pieces.
    for url in (LP.url, HEND.url):
        request = urllib.request.Request(url, headers={"Range": "bytes=0-2047"})
        with urllib.request.urlopen(request, timeout=60) as response:
            assert response.status in {200, 206}
            assert response.read()
