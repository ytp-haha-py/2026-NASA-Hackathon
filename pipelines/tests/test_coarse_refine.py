"""Offline tests for bounded coarse-to-90 m refinement (Issue #37).

No test here touches the network. Determinism is checked against the committed 168-cell
coarse pilot index; the 90 m provider is an injected deterministic fixture whose values
are explicitly marked ``synthetic_fixture``.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest
from analog_api.domain.features import CONTEXT_METHOD_VERSION, METHOD_VERSION
from analog_pipelines.coarse_index import CoarseIndexRepository
from analog_pipelines.refine import (
    FOCUS_WINDOW_M,
    LINK_METHOD_VERSION,
    ProviderFailure,
    RefinedMeasurement,
    RefineError,
    build_report,
    derive_focus_window,
    refine_hits,
    render_markdown,
    select_hits,
)
from analog_pipelines.refine_earth import (
    NATIVE_RES_DEG,
    RefineEarthProvider,
    _mosaic_origin,
    _tile_origins,
)

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "pipelines/coarse/real-pilot/index"
SNAPSHOT = "geomorpho250-real-pilot-20261115"
EXPECTED_IDS = (
    "coarse-v1:249d33516a874855",  # pilot-alps, top hit
    "coarse-v1:e0d4c209d928961c",  # pilot-grand-canyon, top hit
    "coarse-v1:95046a41aeb31ca9",  # pilot-sahara, top hit
)


@pytest.fixture(scope="module")
def repository() -> CoarseIndexRepository:
    return CoarseIndexRepository(INDEX, snapshot_id=SNAPSHOT)


@pytest.fixture(scope="module")
def selection(repository):
    return select_hits(repository, limit=3)


def _fixture_measurement(focus) -> RefinedMeasurement:
    return RefinedMeasurement(
        features={
            "slope_mean_deg": 3.5,
            "slope_p90_deg": 6.0,
            "terrain_relief_p95_p05_m": 40.0,
            "roughness_std_m": 5.0,
            "flat_terrain_fraction": 0.5,
        },
        analysis_crs="EPSG:32632",
        valid_fraction=0.99,
        window_size_m=FOCUS_WINDOW_M,
        resolution_m=90.0,
        checksum=f"sha256:fixture-{focus.coarse_candidate_id}",
        datasets=(
            {
                "dataset_id": "fixture-copernicus-glo90",
                "provider": "Offline fixture provider",
                "product_id": "fixture-glo90",
                "title": "Offline fixture DEM",
                "url": "https://example.invalid/fixture-dem",
                "license": "CC0-1.0",
                "license_url": "https://example.invalid/licence",
                "access_timestamp": "2026-11-15T00:00:00Z",
                "source_crs": "EPSG:4326",
                "native_resolution_m": 90.0,
                "processing_version": METHOD_VERSION,
                "checksum": None,
                "source_kind": "earth_dem",
                "notes": "Synthetic fixture used only by offline tests.",
            },
        ),
        provider_status={"fixture-earth": {"status": "verified"}},
        quality_flags=("synthetic_fixture",),
        warnings=(),
        context_observations={
            "landcover_bare_sparse_fraction": {
                "value": 0.5,
                "unit": "fraction",
                "method_version": CONTEXT_METHOD_VERSION,
                "source_resolution_m": 10.0,
                "valid_fraction": 0.99,
                "quality": "synthetic_fixture",
                "class_code": 60,
                "provider": "Offline fixture provider",
                "dataset_id": "fixture-worldcover",
            }
        },
        method_versions={
            "extractor": "offline-fixture",
            "terrain": METHOD_VERSION,
            "flat": "flat-terrain-fraction-1.0.0",
            "context": CONTEXT_METHOD_VERSION,
        },
        area_km2=FOCUS_WINDOW_M**2 / 1e6,
        center_lon=focus.center_lon,
        center_lat=focus.center_lat,
        bbox_wgs84=focus.focus_bbox_wgs84,
        data_kind="synthetic_fixture",
    )


def _build(repository, selection, links, failures):
    return build_report(
        selection=selection,
        links=links,
        failures=failures,
        repository=repository,
        index_path=INDEX,
        run_metrics={"elapsed_s": 0.0, "max_rss_mb": 1.0},
        provider_label="injected",
        generated_at="2026-11-15T00:00:00Z",
    )


def test_selection_is_deterministic_and_covers_three_regions(repository, selection):
    again = select_hits(repository, limit=3)
    assert selection.rule == "top-per-region"
    assert [hit["candidate_id"] for hit in selection.hits] == list(EXPECTED_IDS)
    assert [hit["candidate_id"] for hit in again.hits] == list(EXPECTED_IDS)
    assert [hit["region_id"] for hit in selection.hits] == [
        "pilot-alps",
        "pilot-grand-canyon",
        "pilot-sahara",
    ]
    assert all(hit["coarse_rank"] == 1 for hit in selection.hits)
    assert all(hit["coarse_rank_scope"] == "within_region" for hit in selection.hits)
    assert selection.indexed_candidate_count == 168
    assert selection.eligible_candidate_count == 168


def test_top_overall_selection_is_bounded_and_deterministic(repository):
    first = select_hits(repository, limit=3, selection="top-overall")
    second = select_hits(repository, limit=3, selection="top-overall")
    assert [hit["candidate_id"] for hit in first.hits] == [
        hit["candidate_id"] for hit in second.hits
    ]
    assert all(hit["coarse_rank_scope"] == "global" for hit in first.hits)
    distances = [hit["distance"] for hit in first.hits]
    assert distances == sorted(distances)


def test_focus_windows_are_metric_centred_and_inside_each_coarse_cell(selection):
    for hit in selection.hits:
        focus = derive_focus_window(hit)
        assert focus.window_m == FOCUS_WINDOW_M
        width = focus.focus_bbox_metric["east"] - focus.focus_bbox_metric["west"]
        height = focus.focus_bbox_metric["north"] - focus.focus_bbox_metric["south"]
        assert width == pytest.approx(FOCUS_WINDOW_M, abs=1e-6)
        assert height == pytest.approx(FOCUS_WINDOW_M, abs=1e-6)
        assert all(value >= -1e-6 for value in focus.margins_m.values())
        assert (
            focus.coarse_bbox_metric["west"]
            <= focus.focus_bbox_metric["west"]
            < focus.focus_bbox_metric["east"]
            <= focus.coarse_bbox_metric["east"]
        )
        assert (
            focus.coarse_bbox_metric["south"]
            <= focus.focus_bbox_metric["south"]
            < focus.focus_bbox_metric["north"]
            <= focus.coarse_bbox_metric["north"]
        )
        assert derive_focus_window(hit).to_payload() == focus.to_payload()


def test_three_hit_refinement_is_deterministic_with_fixture_provider(repository, selection):
    links, failures = refine_hits(
        selection, _fixture_measurement, coarse_snapshot_id=repository.snapshot_id
    )
    again, again_failures = refine_hits(
        select_hits(repository, limit=3),
        _fixture_measurement,
        coarse_snapshot_id=repository.snapshot_id,
    )
    assert failures == []
    assert again_failures == []
    assert [link["coarse"]["candidate_id"] for link in links] == list(EXPECTED_IDS)
    area_ids = [link["refined"]["area_id"] for link in links]
    assert len(set(area_ids)) == 3
    assert all(area_id.startswith("earth-refine-") for area_id in area_ids)
    assert area_ids == [link["refined"]["area_id"] for link in again]
    assert [link["refined"]["checksum"] for link in links] == [
        link["refined"]["checksum"] for link in again
    ]
    assert all(link["link_method_version"] == LINK_METHOD_VERSION for link in links)
    assert all(link["refined"]["data_kind"] == "synthetic_fixture" for link in links)
    assert all(
        link["refined"]["features"]["slope_mean_deg"]["quality"] == "synthetic_fixture"
        for link in links
    )

    report = _build(repository, selection, links, failures)
    assert report["counts"] == {
        "coarse_indexed": 168,
        "coarse_eligible": 168,
        "coarse_selected": 3,
        "refined_succeeded": 3,
        "refined_failed": 0,
        "refined_no_coverage": 0,
        "fine_feature_observations": 15,
        "context_observations": 3,
        "links": 3,
        "failures": 0,
    }
    assert report["recall_estimate"]["value"] is None
    assert report["coverage"]["scope"] == "local_coarse_index_not_global_exhaustive"
    for link in report["links"]:
        assert link["scores"]["coarse"]["rank"] == 1
        assert link["scores"]["fine"]["rank"] is None
        assert link["scores"]["coarse_and_fine_scores_interchangeable"] is False
    markdown = render_markdown(report)
    assert "No fine rank is computed" in markdown
    assert "Recall estimate: none" in markdown
    assert "not global coverage" in markdown


def test_provider_failure_is_recorded_without_zero_fill(repository, selection):
    calls = 0

    def provider(focus):
        nonlocal calls
        calls += 1
        if focus.coarse_region_id == "pilot-sahara":
            raise ProviderFailure("source_fetch_failed", "fixture provider unavailable")
        return _fixture_measurement(focus)

    links, failures = refine_hits(selection, provider, coarse_snapshot_id=repository.snapshot_id)
    assert calls == 3
    assert len(links) == 2
    assert len(failures) == 1
    failure = failures[0]
    assert failure["status"] == "failed"
    assert failure["stage"] == "provider"
    assert failure["error"]["code"] == "source_fetch_failed"
    assert failure["refined_area_id"].startswith("earth-refine-")
    assert "refined" not in failure
    assert "features" not in failure["error"]

    report = _build(repository, selection, links, failures)
    assert report["counts"]["refined_succeeded"] == 2
    assert report["counts"]["refined_failed"] == 1
    assert report["counts"]["refined_no_coverage"] == 0
    assert report["counts"]["fine_feature_observations"] == 10
    assert "source_fetch_failed" in render_markdown(report)


def test_no_coverage_is_distinct_from_failure(repository, selection):
    def provider(focus):
        raise ProviderFailure(
            "insufficient_valid_data", "valid fraction below threshold", no_coverage=True
        )

    links, failures = refine_hits(selection, provider, coarse_snapshot_id=repository.snapshot_id)
    assert links == []
    assert all(failure["status"] == "no_coverage" for failure in failures)
    report = _build(repository, selection, links, failures)
    assert report["counts"]["refined_failed"] == 0
    assert report["counts"]["refined_no_coverage"] == 3
    assert report["counts"]["fine_feature_observations"] == 0
    assert "no_coverage at `provider`" in render_markdown(report)


def test_invalid_provider_value_is_rejected_instead_of_recorded(repository, selection):
    def provider(focus):
        measurement = _fixture_measurement(focus)
        return replace(
            measurement,
            features={**measurement.features, "slope_mean_deg": float("nan")},
        )

    links, failures = refine_hits(selection, provider, coarse_snapshot_id=repository.snapshot_id)
    assert links == []
    assert len(failures) == 3
    assert all(failure["error"]["code"] == "provider_invalid_result" for failure in failures)
    assert all(failure["status"] == "failed" for failure in failures)


def test_focus_derivation_fails_closed_for_an_impossible_window(selection):
    hit = dict(selection.hits[0])
    hit["east"] = hit["west"] + 0.0001
    with pytest.raises(RefineError) as caught:
        derive_focus_window(hit)
    assert caught.value.code in {"focus_outside_coarse", "invalid_coarse_bbox"}


def test_degree_seam_focus_uses_two_copernicus_tiles(selection):
    hit = next(hit for hit in selection.hits if hit["region_id"] == "pilot-grand-canyon")
    focus = derive_focus_window(hit)
    # The focus straddles the 36 N parallel, so the provider must mosaic two tiles.
    assert _tile_origins(focus) == (35, 36, -113, -113)


def test_report_is_json_serialisable(repository, selection):
    links, failures = refine_hits(
        selection, _fixture_measurement, coarse_snapshot_id=repository.snapshot_id
    )
    report = _build(repository, selection, links, failures)
    encoded = json.dumps(report, sort_keys=True)
    assert '"coarse-refine-report-1.0.0"' in encoded
    assert len(encoded) > 1_000


def test_mosaic_origin_matches_copernicus_pixel_centre_convention():
    # Tile E010 / N24: the first pixel centre is at (10, 25) and the outer edge is
    # half a pixel west/north of it.
    west, north = _mosaic_origin(round((10.0 + 180.0) / NATIVE_RES_DEG), 0)
    assert west == pytest.approx(10.0 - NATIVE_RES_DEG / 2.0, abs=1e-12)
    assert north == pytest.approx(90.0 + NATIVE_RES_DEG / 2.0, abs=1e-12)
    west, north = _mosaic_origin(
        round((10.0 + 180.0) / NATIVE_RES_DEG),
        round((90.0 - 25.0) / NATIVE_RES_DEG),
    )
    assert west == pytest.approx(10.0 - NATIVE_RES_DEG / 2.0, abs=1e-12)
    assert north == pytest.approx(25.0 + NATIVE_RES_DEG / 2.0, abs=1e-12)


def test_cli_dry_run_is_offline_and_writes_nothing(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location(
        "refine_coarse_hits", ROOT / "pipelines/refine_coarse_hits.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "nested" / "refinement.json"
    report = tmp_path / "nested" / "COARSE_REFINE_PILOT.md"
    code = module.main(["--dry-run", "--out", str(out), "--report", str(report)])
    captured = capsys.readouterr().out
    assert code == 0
    assert "DRY RUN" in captured
    assert captured.count("refined area: earth-refine-") == 3
    assert "coarse_recorded_metric_bbox_centre_v1" in captured
    assert not out.exists()
    assert not report.exists()


def test_real_provider_is_constructible_offline_with_committed_datasets():
    provider = RefineEarthProvider(timeout=5)
    assert provider.earth_dataset.dataset_id == "copernicus-dem-glo90"
    assert provider.earth_dataset.license
    assert provider.worldcover_dataset.dataset_id == "esa-worldcover-2021-v200"
    assert provider.worldcover_dataset.license
