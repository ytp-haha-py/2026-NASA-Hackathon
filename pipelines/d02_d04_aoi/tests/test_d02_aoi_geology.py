"""Offline checks for the bounded #170 Goal E D02 Moon/Mars AOI evidence.

These tests read only committed JSON, PDS labels and the existing geology typed
export. They do not use the network. The spatial recomputation test needs
Shapely and is skipped when it is not installed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GEOLOGY = ROOT / "pipelines/d02_d04_aoi/geology"
TYPED = ROOT / "pipelines/geology/typed"


def _read(name: str) -> dict:
    return json.loads((GEOLOGY / name).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _builder():
    spec = importlib.util.spec_from_file_location("d02_aoi_build", GEOLOGY / "build.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def evidence() -> dict:
    return _read("evidence.json")


@pytest.fixture(scope="module")
def products() -> list[dict]:
    return _read("products.json")["products"]


@pytest.fixture(scope="module")
def aoi() -> dict:
    return _read("aoi.json")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _read("manifest.json")


def test_scope_is_bounded_and_body_scoped(aoi: dict) -> None:
    areas = {item["area_id"]: item for item in aoi["areas"]}
    assert set(areas) == {
        "moon-shackleton-rim-site04-roi2",
        "mars-test-jezero-floor",
        "mars-test-jezero-western-delta",
    }
    assert areas["moon-shackleton-rim-site04-roi2"]["body"] == "Moon"
    assert areas["mars-test-jezero-floor"]["body"] == "Mars"
    assert areas["mars-test-jezero-western-delta"]["body"] == "Mars"
    assert areas["mars-test-jezero-floor"]["origin"] == "test_aoi"
    assert areas["mars-test-jezero-western-delta"]["origin"] == "test_aoi"
    assert aoi["source_sha256"] == _sha256(ROOT / "pipelines/geology/areas.json")
    assert aoi["config_sha256"] == _sha256(ROOT / "configs/areas.yaml")
    assert areas["moon-shackleton-rim-site04-roi2"]["center_lonlat"]["lat"] < -89.7


def test_products_have_official_identity_license_footprint_time_depth_resolution(
    products: list[dict],
) -> None:
    assert len(products) == 9
    for product in products:
        assert product["product_id"]
        assert product["dataset_id"]
        assert product["body"] in {"Moon", "Mars"}
        assert product["collection_id"]
        assert product["version"]
        assert "public domain" in product["license"]
        assert product["license_url"].startswith("https://")
        assert product["access_status"] == "open"
        assert product["footprint"]["type"] == "Polygon"
        assert len(product["footprint"]["coordinates"][0]) >= 4
        assert product["footprint_crs"]
        assert product["temporal_support"]["start"]
        assert product["temporal_support"]["end"]
        assert product["depth_support"] == {
            "kind": "surface",
            "min_m": None,
            "max_m": None,
        }
        assert product["resolution"]["status"]
        assert product["label"]["file"]
        label_path = GEOLOGY / product["label"]["file"]
        assert label_path.exists()
        assert _sha256(label_path) == product["label"]["sha256"]
        assert product["verification_status"] == "ode_metadata_and_pds_label_verified"


def test_crism_products_carry_verified_map_scale(products: list[dict]) -> None:
    crism = [item for item in products if item["dataset_id"] == "mars-crism-mtrdr"]
    assert len(crism) == 5
    for product in crism:
        native = product["resolution"]["native_m"]
        assert native in {18.0, 36.0}
        assert product["resolution"]["map_scale_m_per_pixel"] == native
        assert product["resolution"]["pixel_scale_px_per_deg"] > 0
        assert product["label"]["latitude_type"] == "Planetocentric"


def test_moon_m3_resolution_is_explicitly_not_invented(products: list[dict]) -> None:
    m3 = [item for item in products if item["dataset_id"] == "moon-m3-l2-reflectance"]
    assert len(m3) == 4
    for product in m3:
        assert product["resolution"]["native_m"] is None
        assert product["resolution"]["status"] == "not_stated_in_product_label"
        assert product["label"]["instrument_mode"] == "GLOBAL"
        assert product["label"]["bands"] == "85"


def test_queries_are_bounded_and_record_official_counts() -> None:
    files = sorted((GEOLOGY / "queries").glob("ode_*.json"))
    assert len(files) == 5
    by_id = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in files}
    m3 = by_id["ode_moon-shackleton-rim-site04-roi2-m3-refimg.json"]
    assert m3["status"] == "Success"
    assert m3["params"]["iid"] == "M3"
    assert m3["count"] == 100
    assert m3["count_query"]["count"] == 116
    assert m3["count_query"]["status"] == "Success"
    assert m3["products_stored"] == 4
    assert m3["trimmed"] is True
    assert m3["selection"]["selected_product_ids"]
    assert len(m3["products"]) == 4

    floor = by_id["ode_mars-test-jezero-floor-crism-mtrdr.json"]
    delta = by_id["ode_mars-test-jezero-western-delta-crism-mtrdr.json"]
    assert (floor["count"], floor["products_stored"]) == (5, 5)
    assert (delta["count"], delta["products_stored"]) == (3, 3)

    tio2 = by_id["ode_moon-shackleton-rim-site04-roi2-lroc-sdwtio.json"]
    assert tio2["count"] == 0 and tio2["products"] == []
    family = by_id["ode_moon_lroc_tio2_family.json"]
    assert family["count"] == 16
    assert family["tile_summary"]["latitude_min"] == -70.0
    assert family["tile_summary"]["latitude_max"] == 70.0

    for query in by_id.values():
        assert query["endpoint"] == "https://oderest.rsl.wustl.edu/live2/"
        assert query["service_version"].startswith("ODE V2.")
        assert query["request_url"].startswith("https://")
        assert query["raw_response_bytes"] > 0
        assert query["raw_response_sha256"].startswith("sha256:")
        assert query["products_stored"] <= 12


def test_no_mineral_detection_or_abundance_was_claimed(evidence: dict) -> None:
    allowed = {"unknown", "no_coverage"}
    for record in evidence["records"]:
        assert record["state"] in allowed
        assert record["value"] is None
        assert record["unit"] is None
        assert record["valid_fraction"] is None
        assert (
            record["coverage"]["fraction"] is None or 0.0 <= record["coverage"]["fraction"] <= 1.0
        )
        blob = json.dumps(record).lower()
        assert "wt%" not in blob
        assert 'abundance_exported": true' not in blob
    for summary in evidence["aoi_summaries"]:
        assert summary["observed_mineral_detection"] is False
        assert summary["abundance_exported"] is False


def test_no_coverage_is_explicit_and_not_zero_filled(evidence: dict) -> None:
    no_coverage = [record for record in evidence["records"] if record["state"] == "no_coverage"]
    assert {record["body"] for record in no_coverage} == {"Moon", "Mars"}
    for record in no_coverage:
        assert record["coverage"]["state"] == "no_coverage"
        assert record["coverage"]["fraction"] is None
        assert record["coverage"]["covered_area_m2"] is None
        assert record["value"] is None
        assert "no_coverage_is_not_absence" in record["quality_flags"]

    lunar = [record for record in no_coverage if record["dataset_id"] == "moon-lroc-wac-tio2"]
    assert len(lunar) == 1
    assert lunar[0]["product_id"] is None
    assert lunar[0]["provenance"]["query_count"] == 0
    assert lunar[0]["provenance"]["family_tile_latitude_max"] == 70.0


def test_complete_partial_and_no_coverage_fractions_are_consistent(evidence: dict) -> None:
    states = set()
    for record in evidence["records"]:
        coverage = record["coverage"]
        states.add(coverage["state"])
        if coverage["state"] == "complete":
            assert coverage["fraction"] == 1.0
            assert coverage["covered_area_m2"] == pytest.approx(coverage["aoi_area_m2"], rel=1e-9)
        elif coverage["state"] == "partial":
            assert 0.0 < coverage["fraction"] < 1.0
            assert coverage["covered_area_m2"] == pytest.approx(
                coverage["fraction"] * coverage["aoi_area_m2"], rel=1e-9
            )
        elif coverage["state"] == "no_coverage":
            assert coverage["fraction"] is None
    assert states == {"complete", "partial", "no_coverage"}


def test_coverage_is_computed_per_aoi_without_borrowing(evidence: dict) -> None:
    records = evidence["records"]
    floor = [
        record
        for record in records
        if record["area_id"] == "mars-test-jezero-floor"
        and record["product_id"] == "frt00005850_07_if167j_mtr3"
    ]
    delta = [
        record
        for record in records
        if record["area_id"] == "mars-test-jezero-western-delta"
        and record["product_id"] == "frt00005850_07_if167j_mtr3"
    ]
    assert len(floor) == 1 and floor[0]["state"] == "no_coverage"
    assert delta == []

    shared = [record for record in records if record["product_id"] == "frt000047a3_07_if166j_mtr3"]
    by_area = {record["area_id"]: record for record in shared}
    assert by_area["mars-test-jezero-floor"]["coverage"]["state"] == "partial"
    assert by_area["mars-test-jezero-western-delta"]["state"] == "no_coverage"

    for record in records:
        product_body = "Moon" if record["dataset_id"].startswith("moon") else "Mars"
        assert record["body"] == product_body
        assert record["area_id"].startswith("moon-" if product_body == "Moon" else "mars-")


def test_manifest_checksums_counts_and_typed_export_are_stable(manifest: dict) -> None:
    assert manifest["record_count"] == 13
    assert manifest["product_count"] == 9
    assert manifest["dataset_count"] == 3
    assert manifest["area_count"] == 3
    assert manifest["body_counts"] == {"Mars": 8, "Moon": 5}
    assert manifest["state_counts"] == {"no_coverage": 3, "unknown": 10}
    assert manifest["coverage_state_counts"] == {
        "complete": 6,
        "no_coverage": 3,
        "partial": 4,
    }
    for group in ("files", "queries", "labels"):
        for name, expected in manifest[group].items():
            path = GEOLOGY / name
            assert path.exists(), name
            assert path.stat().st_size == expected["bytes"], name
            assert _sha256(path) == expected["sha256"], name

    typed_manifest = json.loads((TYPED / "manifest.json").read_text(encoding="utf-8"))
    typed_observations = TYPED / "observations.jsonl"
    assert (
        typed_manifest["observations_sha256"]
        == hashlib.sha256(typed_observations.read_bytes()).hexdigest()
    )
    assert manifest["typed_manifest_sha256"] == _sha256(TYPED / "manifest.json")


def test_spatial_fractions_recompute_from_committed_geometry(
    evidence: dict, products: list[dict], aoi: dict
) -> None:
    pytest.importorskip("shapely")
    builder = _builder()
    areas = {item["area_id"]: item for item in aoi["areas"]}
    product_index = {item["product_id"]: item for item in products}
    for record in evidence["records"]:
        if record["product_id"] is None:
            continue
        area = areas[record["area_id"]]
        product = product_index[record["product_id"]]
        fraction, state, error = builder._coverage(
            area["geometry_lonlat"],
            product["footprint"],
            area["body"],
            area["analysis_crs"],
        )
        assert error is None
        assert state == record["coverage"]["state"]
        if state == "no_coverage":
            assert fraction == 0.0
            assert record["coverage"]["fraction"] is None
        elif fraction is None:
            assert record["coverage"]["fraction"] is None
        else:
            assert record["coverage"]["fraction"] == pytest.approx(fraction, abs=1e-12)
