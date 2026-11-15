"""Offline verifier for the committed coarse -> fine refinement chain (Issues #108/#97).

Reads only committed artifacts:

- ``pipelines/coarse/real-pilot/index/snapshots/.../manifest.json`` plus its Parquet
  partitions (checksums verified through ``verify_index``);
- ``pipelines/coarse/real-pilot/refine/refinement.json``.

No database is mutated and no provider/network call is made. The script fails closed
with a machine-readable ``code``/``message`` whenever a link, count, method version or
support claim is inconsistent.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from analog_api.domain.features import (
    CONTEXT_METHOD_VERSION,
    FEATURE_BY_ID,
    FLAT_METHOD_VERSION,
    METHOD_VERSION,
)
from analog_pipelines.coarse_features import COARSE_METHOD_VERSION
from analog_pipelines.coarse_index import (
    INDEX_VERSION,
    CoarseIndexRepository,
    verify_index,
)
from analog_pipelines.refine import (
    FINE_METHOD_VERSIONS,
    FOCUS_WINDOW_M,
    LINK_METHOD_VERSION,
    REPORT_VERSION,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_ROOT = REPO_ROOT / "pipelines/coarse/real-pilot/index"
SNAPSHOT_ID = "geomorpho250-real-pilot-20261115"
REFINEMENT_PATH = REPO_ROOT / "pipelines/coarse/real-pilot/refine/refinement.json"
EXPECTED_CAPTURE = "coarse_recorded_metric_bbox_centre_v1"
EXPECTED_COVERAGE = "local_coarse_index_not_global_exhaustive"
CORE_PROVIDERS = ("copernicus-dem-glo90", "esa-worldcover-2021-v200")


class ChainError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ChainError(code, message)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_number(actual: Any, expected: Any, tolerance: float = 1e-7) -> bool:
    return (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and math.isfinite(float(actual))
        and abs(float(actual) - float(expected)) <= tolerance
    )


def _check_bbox(label: str, actual: Any, expected: Any, tolerance: float = 1e-7) -> None:
    _require(isinstance(actual, dict), "invalid_bbox_block", f"{label} must be an object")
    for key in ("west", "south", "east", "north"):
        _require(key in actual, "invalid_bbox_block", f"{label} is missing {key!r}")
    _require(
        _same_number(actual["west"], expected["west"], tolerance)
        and _same_number(actual["south"], expected["south"], tolerance)
        and _same_number(actual["east"], expected["east"], tolerance)
        and _same_number(actual["north"], expected["north"], tolerance),
        "link_bbox_mismatch",
        f"{label} {actual} does not match committed coarse bbox {expected}",
    )


def _inside(inner: dict[str, float], outer: dict[str, float], tolerance: float = 1e-9) -> bool:
    return (
        inner["west"] >= outer["west"] - tolerance
        and inner["east"] <= outer["east"] + tolerance
        and inner["south"] >= outer["south"] - tolerance
        and inner["north"] <= outer["north"] + tolerance
    )


def _check_index_manifest(report: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    index = report.get("input_index")
    _require(isinstance(index, dict), "invalid_input_index", "refinement.json has no input_index")
    _require(
        manifest.get("index_version") == INDEX_VERSION,
        "unsupported_index_version",
        f"committed index_version is {manifest.get('index_version')!r}, expected {INDEX_VERSION!r}",
    )
    _require(
        manifest.get("method_version") == COARSE_METHOD_VERSION,
        "coarse_method_mismatch",
        f"committed coarse method_version is {manifest.get('method_version')!r}",
    )
    _require(
        index.get("snapshot_id") == manifest.get("snapshot_id") == SNAPSHOT_ID,
        "snapshot_mismatch",
        "input_index snapshot_id, manifest snapshot_id and pinned snapshot disagree",
    )
    for field, expected in (
        ("index_version", manifest.get("index_version")),
        ("grid_version", manifest.get("grid_version")),
        ("method_version", manifest.get("method_version")),
        ("row_count", manifest.get("row_count")),
        ("coverage_label", manifest.get("coverage", {}).get("label")),
        ("regions", manifest.get("coverage", {}).get("regions")),
    ):
        _require(
            index.get(field) == expected,
            "index_manifest_mismatch",
            f"input_index.{field}={index.get(field)!r} does not match manifest {expected!r}",
        )
    manifest_path = Path(manifest["_snapshot_root"]) / "manifest.json"
    actual_manifest_sha = _sha256_file(manifest_path)
    _require(
        index.get("manifest_sha256") == actual_manifest_sha,
        "manifest_checksum_mismatch",
        "input_index.manifest_sha256 does not match the committed manifest bytes",
    )
    reported_partitions = {
        partition.get("path"): partition for partition in index.get("partitions") or []
    }
    _require(
        len(reported_partitions) == len(manifest.get("partitions") or []),
        "partition_mismatch",
        "input_index partition list length differs from the committed manifest",
    )
    for partition in manifest.get("partitions", []):
        reported = reported_partitions.get(partition.get("path"))
        _require(
            isinstance(reported, dict),
            "partition_mismatch",
            f"input_index is missing committed partition {partition.get('path')!r}",
        )
        for field in ("sha256", "rows", "bytes"):
            _require(
                reported.get(field) == partition.get(field),
                "partition_mismatch",
                f"partition {partition.get('path')!r} field {field!r} differs from manifest",
            )
    _require(
        manifest.get("coverage", {}).get("label") == EXPECTED_COVERAGE,
        "coverage_label_mismatch",
        "committed coarse index does not carry the non-global coverage label",
    )
    return {
        "snapshot_id": manifest["snapshot_id"],
        "row_count": manifest["row_count"],
        "regions": manifest["coverage"]["regions"],
        "bbox": manifest["coverage"]["bbox"],
        "method_version": manifest["method_version"],
        "grid_version": manifest["grid_version"],
        "index_version": manifest["index_version"],
        "coverage_label": manifest["coverage"]["label"],
        "partition_count": len(manifest["partitions"]),
        "manifest_sha256": actual_manifest_sha,
    }


def _check_report_versions(report: dict[str, Any]) -> None:
    _require(
        report.get("report_version") == REPORT_VERSION,
        "report_version_mismatch",
        f"refinement report_version is {report.get('report_version')!r}",
    )
    _require(
        report.get("link_method_version") == LINK_METHOD_VERSION,
        "link_method_mismatch",
        f"refinement link_method_version is {report.get('link_method_version')!r}",
    )
    versions = report.get("method_versions")
    _require(isinstance(versions, dict), "missing_method_versions", "method_versions is missing")
    expected = {
        "coarse": COARSE_METHOD_VERSION,
        "fine": METHOD_VERSION,
        "flat": FLAT_METHOD_VERSION,
        "context": CONTEXT_METHOD_VERSION,
        "link": LINK_METHOD_VERSION,
        "report": REPORT_VERSION,
    }
    for key, value in expected.items():
        _require(
            versions.get(key) == value,
            "method_version_mismatch",
            f"method_versions.{key}={versions.get(key)!r}, expected {value!r}",
        )
    _require(
        versions.get("coarse") != versions.get("fine"),
        "coarse_fine_versions_not_distinct",
        "coarse and fine method versions must be distinct",
    )
    _require(
        report.get("coverage", {}).get("scope") == EXPECTED_COVERAGE,
        "coverage_scope_mismatch",
        "refinement coverage scope does not carry the non-global label",
    )


def _check_selection_and_counts(report: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    selection = report.get("selection")
    counts = report.get("counts")
    links = report.get("links")
    failures = report.get("failures")
    _require(isinstance(selection, dict), "missing_selection", "selection block is missing")
    _require(isinstance(counts, dict), "missing_counts", "counts block is missing")
    _require(isinstance(links, list), "missing_links", "links must be a list")
    _require(isinstance(failures, list), "missing_failures", "failures must be a list")

    selected_ids = selection.get("selected_candidate_ids")
    _require(
        isinstance(selected_ids, list) and selected_ids,
        "missing_selected_ids",
        "selection.selected_candidate_ids must be a non-empty list",
    )
    _require(
        len(selected_ids) == len(set(selected_ids)),
        "duplicate_selected_candidate",
        "selected_candidate_ids contains duplicates",
    )
    _require(
        selection.get("limit") == len(selected_ids) == counts.get("coarse_selected"),
        "count_mismatch",
        "selection.limit, selected_candidate_ids and counts.coarse_selected disagree",
    )
    _require(
        counts.get("coarse_indexed")
        == manifest.get("row_count")
        == selection.get("indexed_candidate_count"),
        "count_mismatch",
        "indexed coarse count disagrees with the committed manifest",
    )
    _require(
        counts.get("coarse_eligible") == selection.get("eligible_candidate_count"),
        "count_mismatch",
        "eligible coarse count disagrees with selection",
    )
    _require(
        0 <= counts.get("coarse_eligible", -1) <= counts.get("coarse_indexed", -1),
        "count_mismatch",
        "eligible coarse count must be within 0..indexed",
    )

    succeeded = [link for link in links if link.get("status") == "succeeded"]
    failed = [failure for failure in failures if failure.get("status") == "failed"]
    no_coverage = [failure for failure in failures if failure.get("status") == "no_coverage"]
    _require(
        len(succeeded) == len(links),
        "status_inconsistent",
        "a link entry is not marked succeeded",
    )
    _require(
        len(failed) + len(no_coverage) == len(failures),
        "status_inconsistent",
        "a failure entry is neither failed nor no_coverage",
    )
    _require(
        len(succeeded) + len(failed) + len(no_coverage)
        == counts.get("coarse_selected")
        == len(selected_ids),
        "count_mismatch",
        "succeeded+failed+no_coverage does not equal the selected coarse count",
    )
    expected_counts = {
        "refined_succeeded": len(succeeded),
        "refined_failed": len(failed),
        "refined_no_coverage": len(no_coverage),
        "links": len(links),
        "failures": len(failures),
    }
    for key, expected in expected_counts.items():
        _require(
            counts.get(key) == expected,
            "count_mismatch",
            f"counts.{key}={counts.get(key)!r} but committed entries imply {expected}",
        )
    _require(
        counts.get("fine_feature_observations")
        == sum(len(link.get("refined", {}).get("features") or {}) for link in links),
        "count_mismatch",
        "fine_feature_observations does not match the refined feature blocks",
    )
    _require(
        counts.get("context_observations")
        == sum(len(link.get("refined", {}).get("context_observations") or {}) for link in links),
        "count_mismatch",
        "context_observations does not match the refined context blocks",
    )

    linked_ids = [link.get("coarse", {}).get("candidate_id") for link in links]
    failed_ids = [failure.get("coarse_candidate_id") for failure in failures]
    _require(
        sorted(linked_ids + failed_ids) == sorted(selected_ids),
        "selection_link_mismatch",
        "selected candidates do not equal the union of link and failure coarse IDs",
    )

    provider_counts = report.get("provider_counts") or {}
    for provider_id in CORE_PROVIDERS:
        bucket = provider_counts.get(provider_id)
        _require(
            isinstance(bucket, dict),
            "provider_counts_missing",
            f"provider_counts has no entry for {provider_id!r}",
        )
        _require(
            sum(int(value) for value in bucket.values()) == len(links),
            "count_mismatch",
            f"provider_counts[{provider_id!r}] does not sum to the link count",
        )

    return {
        "rule": selection.get("rule"),
        "rank_scope": selection.get("rank_scope"),
        "selected_candidate_ids": selected_ids,
        "counts": counts,
        "links": len(links),
        "failures": len(failures),
    }


def _check_coarse_candidate(
    link: dict[str, Any], repository: CoarseIndexRepository, manifest: dict[str, Any]
) -> dict[str, Any]:
    coarse = link.get("coarse")
    _require(isinstance(coarse, dict), "invalid_coarse_block", "link has no coarse block")
    candidate_id = coarse.get("candidate_id")
    _require(
        isinstance(candidate_id, str) and candidate_id,
        "invalid_candidate_id",
        "coarse candidate_id is missing",
    )
    committed = repository.get(candidate_id)
    _require(
        committed is not None,
        "candidate_missing_from_committed_index",
        f"coarse candidate {candidate_id!r} is not in the committed Parquet index",
    )
    _require(
        coarse.get("snapshot_id") == manifest.get("snapshot_id"),
        "link_snapshot_mismatch",
        f"{candidate_id}: coarse snapshot does not match the committed index",
    )
    _require(
        coarse.get("method_version") == COARSE_METHOD_VERSION,
        "coarse_method_mismatch",
        f"{candidate_id}: coarse method_version is not {COARSE_METHOD_VERSION!r}",
    )
    _require(
        coarse.get("grid_version") == manifest.get("grid_version"),
        "coarse_grid_mismatch",
        f"{candidate_id}: coarse grid_version does not match the manifest",
    )
    _require(
        coarse.get("region_id") == committed.get("region_id"),
        "link_region_mismatch",
        f"{candidate_id}: coarse region_id differs from the committed row",
    )
    _require(
        coarse.get("region_id") in manifest.get("coverage", {}).get("regions", []),
        "unknown_region",
        f"{candidate_id}: region is not listed in the committed coverage",
    )
    for field in ("dataset_id", "source_asset_id", "crs"):
        _require(
            coarse.get(field) == committed.get(field),
            "link_field_mismatch",
            f"{candidate_id}: coarse {field} differs from the committed row",
        )
    for field in ("valid_fraction", "window_size_m", "source_resolution_m"):
        _require(
            _same_number(coarse.get(field), committed.get(field)),
            "link_field_mismatch",
            f"{candidate_id}: coarse {field} differs from the committed row",
        )
    _require(
        coarse.get("source_resolution_m") == 250.0 and coarse.get("window_size_m") == 5000.0,
        "coarse_support_mismatch",
        f"{candidate_id}: coarse support is not the committed 5 km / 250 m support",
    )
    committed_bbox = {key: float(committed[key]) for key in ("west", "south", "east", "north")}
    _check_bbox(f"{candidate_id} coarse bbox", coarse.get("bbox_wgs84"), committed_bbox)

    link_features = coarse.get("features")
    _require(
        isinstance(link_features, dict),
        "invalid_coarse_features",
        f"{candidate_id}: coarse features are missing",
    )
    for link_key, committed_key in (
        ("coarse_slope_mean_deg", "slope_mean_deg"),
        ("coarse_slope_p90_deg", "slope_p90_deg"),
        ("coarse_flat_fraction_lt5deg", "flat_fraction"),
    ):
        _require(
            _same_number(link_features.get(link_key), committed.get(committed_key), 1e-9),
            "link_feature_mismatch",
            f"{candidate_id}: {link_key} differs from the committed row",
        )
    _require(
        coarse.get("coarse_rank") == 1,
        "coarse_rank_mismatch",
        f"{candidate_id}: top-per-region link must carry coarse_rank 1",
    )
    _require(
        coarse.get("coarse_rank_scope") == "within_region",
        "coarse_rank_scope_mismatch",
        f"{candidate_id}: expected within-region coarse rank scope",
    )
    _require(
        isinstance(coarse.get("distance"), (int, float))
        and math.isfinite(float(coarse["distance"])),
        "coarse_distance_invalid",
        f"{candidate_id}: coarse distance is not finite",
    )
    return {"candidate_id": candidate_id, "region_id": coarse.get("region_id")}


def _check_focus(link: dict[str, Any]) -> dict[str, Any]:
    coarse = link["coarse"]
    focus = link.get("focus")
    _require(isinstance(focus, dict), "invalid_focus_block", "link has no focus block")
    candidate_id = coarse["candidate_id"]
    _require(
        focus.get("coarse_candidate_id") == candidate_id,
        "focus_link_mismatch",
        f"{candidate_id}: focus is linked to a different coarse candidate",
    )
    _require(
        focus.get("coarse_region_id") == coarse.get("region_id"),
        "focus_link_mismatch",
        f"{candidate_id}: focus region differs from the coarse region",
    )
    _require(
        focus.get("placement_rule") == EXPECTED_CAPTURE,
        "focus_placement_mismatch",
        f"{candidate_id}: unexpected focus placement rule {focus.get('placement_rule')!r}",
    )
    _require(
        _same_number(focus.get("window_m"), FOCUS_WINDOW_M),
        "focus_window_mismatch",
        f"{candidate_id}: focus window is not {FOCUS_WINDOW_M:.0f} m",
    )
    _check_bbox(
        f"{candidate_id} focus coarse bbox", focus.get("coarse_bbox_wgs84"), coarse["bbox_wgs84"]
    )
    focus_raw = focus.get("focus_bbox_wgs84")
    _require(
        isinstance(focus_raw, dict)
        and all(
            _same_number(focus_raw.get(key), focus_raw.get(key), 0.0)
            for key in ("west", "south", "east", "north")
        ),
        "invalid_focus_bbox",
        f"{candidate_id}: focus bbox must contain four finite coordinates",
    )
    focus_bbox = {key: float(focus_raw[key]) for key in ("west", "south", "east", "north")}
    coarse_raw = focus["coarse_bbox_wgs84"]
    coarse_bbox = {key: float(coarse_raw[key]) for key in ("west", "south", "east", "north")}
    _require(
        _inside(focus_bbox, coarse_bbox),
        "focus_outside_coarse",
        f"{candidate_id}: focus bbox is not inside the coarse candidate bbox",
    )
    _require(
        focus_bbox["west"] < focus_bbox["east"] and focus_bbox["south"] < focus_bbox["north"],
        "invalid_focus_bbox",
        f"{candidate_id}: focus bbox is degenerate",
    )
    center_lon = float(focus.get("center_lon"))
    center_lat = float(focus.get("center_lat"))
    _require(
        focus_bbox["west"] <= center_lon <= focus_bbox["east"]
        and focus_bbox["south"] <= center_lat <= focus_bbox["north"],
        "focus_center_outside",
        f"{candidate_id}: focus centre lies outside the focus bbox",
    )
    margins = focus.get("margins_m")
    _require(isinstance(margins, dict), "invalid_margins", f"{candidate_id}: margins are missing")
    _require(
        all(float(value) >= -1e-6 for value in margins.values()),
        "focus_margin_negative",
        f"{candidate_id}: focus window has negative margin inside the coarse cell",
    )
    return {
        "focus_window_m": focus["window_m"],
        "focus_bbox_wgs84": focus_bbox,
        "center_lon": center_lon,
        "center_lat": center_lat,
    }


def _check_refined(link: dict[str, Any]) -> dict[str, Any]:
    coarse = link["coarse"]
    refined = link.get("refined")
    candidate_id = coarse["candidate_id"]
    _require(
        isinstance(refined, dict),
        "invalid_refined_block",
        f"{candidate_id}: succeeded link has no refined block",
    )
    area_id = refined.get("area_id")
    _require(
        isinstance(area_id, str) and area_id.startswith("earth-refine-"),
        "invalid_area_id",
        f"{candidate_id}: refined area_id {area_id!r} is not an earth-refine id",
    )
    _require(
        refined.get("origin") == "coarse_refinement",
        "refined_origin_mismatch",
        f"{candidate_id}: refined origin is not coarse_refinement",
    )
    _require(
        refined.get("data_kind") == "observed",
        "refined_data_kind_invalid",
        f"{candidate_id}: committed refinement must be observed data, not a fixture",
    )
    _require(
        _same_number(refined.get("resolution_m"), 90.0),
        "fine_resolution_mismatch",
        f"{candidate_id}: fine resolution is not 90 m",
    )
    _require(
        _same_number(refined.get("window_size_m"), FOCUS_WINDOW_M),
        "fine_window_mismatch",
        f"{candidate_id}: fine window is not {FOCUS_WINDOW_M:.0f} m",
    )
    valid_fraction = refined.get("valid_fraction")
    _require(
        isinstance(valid_fraction, (int, float))
        and not isinstance(valid_fraction, bool)
        and math.isfinite(float(valid_fraction))
        and 0.0 <= float(valid_fraction) <= 1.0,
        "fine_valid_fraction_invalid",
        f"{candidate_id}: refined valid_fraction is not within [0, 1]",
    )
    checksum = refined.get("checksum")
    _require(
        isinstance(checksum, str) and checksum.startswith("sha256:") and len(checksum) == 71,
        "fine_checksum_missing",
        f"{candidate_id}: refined checksum is missing or malformed",
    )
    _check_bbox(
        f"{candidate_id} refined bbox",
        refined.get("bbox_wgs84"),
        link["focus"]["focus_bbox_wgs84"],
    )

    versions = refined.get("method_versions")
    _require(isinstance(versions, dict), "fine_methods_missing", f"{candidate_id}: method_versions")
    for key, expected in (
        ("terrain", METHOD_VERSION),
        ("flat", FLAT_METHOD_VERSION),
        ("context", CONTEXT_METHOD_VERSION),
    ):
        _require(
            versions.get(key) == expected,
            "fine_method_mismatch",
            f"{candidate_id}: refined method_versions.{key}={versions.get(key)!r}, "
            f"expected {expected!r}",
        )

    features = refined.get("features")
    _require(isinstance(features, dict), "fine_features_missing", f"{candidate_id}: features")
    _require(
        set(features) == set(FINE_METHOD_VERSIONS),
        "fine_feature_set_mismatch",
        f"{candidate_id}: refined feature ids differ from the five fine terrain observations",
    )
    for feature_id, observation in features.items():
        _require(
            isinstance(observation, dict),
            "fine_feature_invalid",
            f"{candidate_id}: feature {feature_id} is not an object",
        )
        value = observation.get("value")
        definition = FEATURE_BY_ID[feature_id]
        _require(
            isinstance(value, (int, float))
            and math.isfinite(float(value))
            and definition.min_value <= float(value) <= definition.max_value,
            "fine_feature_value_invalid",
            f"{candidate_id}: {feature_id} value is missing, non-finite or outside its domain",
        )
        _require(
            observation.get("method_version") == FINE_METHOD_VERSIONS[feature_id],
            "fine_feature_method_mismatch",
            f"{candidate_id}: {feature_id} method_version is not the fine method",
        )
        _require(
            observation.get("extraction_scale_m") == 90,
            "fine_feature_scale_mismatch",
            f"{candidate_id}: {feature_id} was not observed at 90 m",
        )
        _require(
            observation.get("quality") == "verified",
            "fine_feature_quality_not_verified",
            f"{candidate_id}: {feature_id} quality is not verified",
        )

    context = refined.get("context_observations")
    _require(isinstance(context, dict), "context_missing", f"{candidate_id}: context observations")
    for context_id, observation in context.items():
        _require(
            observation.get("method_version") == CONTEXT_METHOD_VERSION,
            "context_method_mismatch",
            f"{candidate_id}: {context_id} is not WorldCover context",
        )
        _require(
            observation.get("quality") == "verified",
            "context_quality_not_verified",
            f"{candidate_id}: {context_id} quality is not verified",
        )
    _require(
        refined.get("context_observation_count") == len(context),
        "count_mismatch",
        f"{candidate_id}: context_observation_count differs from the context block",
    )

    provider_status = refined.get("provider_status")
    _require(
        isinstance(provider_status, dict),
        "provider_status_missing",
        f"{candidate_id}: provider_status is missing",
    )
    for provider_id in CORE_PROVIDERS:
        payload = provider_status.get(provider_id)
        _require(
            isinstance(payload, dict) and payload.get("status") == "verified",
            "provider_not_verified",
            f"{candidate_id}: provider {provider_id!r} is not verified",
        )
    datasets = refined.get("datasets")
    _require(isinstance(datasets, list), "datasets_missing", f"{candidate_id}: datasets missing")
    dataset_ids = {dataset.get("dataset_id") for dataset in datasets if isinstance(dataset, dict)}
    for provider_id in CORE_PROVIDERS:
        _require(
            provider_id in dataset_ids,
            "dataset_not_recorded",
            f"{candidate_id}: dataset {provider_id!r} is not recorded",
        )
    _require(
        all(dataset.get("license") for dataset in datasets),
        "dataset_license_missing",
        f"{candidate_id}: a refined dataset has no licence",
    )

    scores = link.get("scores")
    _require(isinstance(scores, dict), "scores_missing", f"{candidate_id}: scores missing")
    _require(
        scores.get("coarse_and_fine_scores_interchangeable") is False,
        "coarse_fine_interchangeable",
        f"{candidate_id}: coarse and fine scores must not be interchangeable",
    )
    _require(
        scores.get("coarse", {}).get("method_version") == COARSE_METHOD_VERSION,
        "coarse_method_mismatch",
        f"{candidate_id}: coarse score method differs",
    )
    _require(
        scores.get("fine", {}).get("method_version") == METHOD_VERSION,
        "fine_method_mismatch",
        f"{candidate_id}: fine score method differs",
    )
    _require(
        scores.get("fine", {}).get("rank") is None
        and scores.get("fine", {}).get("distance") is None,
        "fine_rank_claimed",
        f"{candidate_id}: the refinement pilot must not claim a fine rank",
    )
    _require(
        scores.get("coarse", {}).get("rank") == coarse.get("coarse_rank"),
        "coarse_rank_mismatch",
        f"{candidate_id}: score rank differs from the coarse rank",
    )

    return {
        "candidate_id": candidate_id,
        "region_id": coarse["region_id"],
        "area_id": area_id,
        "coarse_method_version": COARSE_METHOD_VERSION,
        "fine_method_version": METHOD_VERSION,
        "coarse_support": {
            "window_size_m": coarse.get("window_size_m"),
            "source_resolution_m": coarse.get("source_resolution_m"),
        },
        "fine_support": {
            "window_size_m": refined.get("window_size_m"),
            "resolution_m": refined.get("resolution_m"),
        },
        "fine_feature_count": len(features),
        "context_observation_count": len(context),
        "provider_status": {
            provider_id: provider_status[provider_id].get("status")
            for provider_id in CORE_PROVIDERS
        },
    }


def _check_failures(
    report: dict[str, Any], repository: CoarseIndexRepository
) -> list[dict[str, Any]]:
    summaries = []
    for failure in report.get("failures") or []:
        candidate_id = failure.get("coarse_candidate_id")
        _require(
            isinstance(candidate_id, str) and repository.get(candidate_id) is not None,
            "failure_candidate_missing",
            f"failure references an uncommitted coarse candidate {candidate_id!r}",
        )
        _require(
            failure.get("coarse", {}).get("candidate_id") == candidate_id,
            "failure_link_mismatch",
            f"failure {candidate_id!r}: coarse block does not match the failure candidate",
        )
        _require(
            failure.get("status") in {"failed", "no_coverage"},
            "status_inconsistent",
            f"failure {candidate_id!r}: unexpected status {failure.get('status')!r}",
        )
        _require(
            "refined" not in failure,
            "failed_refinement_has_values",
            f"failure {candidate_id!r}: a failed window must not carry refined values",
        )
        error = failure.get("error")
        _require(
            isinstance(error, dict) and error.get("code") and error.get("message"),
            "failure_error_missing",
            f"failure {candidate_id!r}: error code/message is missing",
        )
        area_id = failure.get("refined_area_id")
        _require(
            area_id is None or (isinstance(area_id, str) and area_id.startswith("earth-refine-")),
            "invalid_area_id",
            f"failure {candidate_id!r}: refined_area_id is malformed",
        )
        summaries.append(
            {
                "candidate_id": candidate_id,
                "status": failure["status"],
                "stage": failure.get("stage"),
                "code": error.get("code"),
            }
        )
    return summaries


def _scan_db_references(value: Any, path: str = "") -> list[str]:
    """Find any committed field that looks like a runtime database row reference."""
    matches: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = f"{path}.{key}" if path else key
            if any(token in str(key).lower() for token in ("db_row", "database", "sqlite")):
                matches.append(key_path)
            matches.extend(_scan_db_references(child, key_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(_scan_db_references(child, f"{path}[{index}]"))
    return matches


def run() -> dict[str, Any]:
    _require(REFINEMENT_PATH.is_file(), "missing_refinement_json", f"{REFINEMENT_PATH} not found")
    report = json.loads(REFINEMENT_PATH.read_text(encoding="utf-8"))
    _require(
        isinstance(report, dict), "invalid_refinement_json", "refinement.json is not an object"
    )

    manifest = verify_index(INDEX_ROOT, snapshot_id=SNAPSHOT_ID)
    manifest = dict(manifest)
    manifest["_snapshot_root"] = str(INDEX_ROOT / "snapshots" / SNAPSHOT_ID)
    repository = CoarseIndexRepository(INDEX_ROOT, snapshot_id=SNAPSHOT_ID)
    index_summary = _check_index_manifest(report, manifest)
    _check_report_versions(report)
    selection_summary = _check_selection_and_counts(report, manifest)

    links = report.get("links") or []
    area_ids: set[str] = set()
    link_summaries = []
    for link in links:
        _require(
            link.get("status") == "succeeded" and link.get("stage") == "complete",
            "status_inconsistent",
            "a linked coarse candidate is not a completed success",
        )
        _require(
            link.get("error") is None,
            "status_inconsistent",
            "a succeeded link carries an error payload",
        )
        _require(
            link.get("link_method_version") == LINK_METHOD_VERSION,
            "link_method_mismatch",
            "a link does not use the committed link method version",
        )
        _check_coarse_candidate(link, repository, manifest)
        focus_summary = _check_focus(link)
        refined_summary = _check_refined(link)
        area_id = refined_summary["area_id"]
        _require(
            area_id not in area_ids,
            "duplicate_refined_area",
            f"refined area_id {area_id!r} is linked more than once",
        )
        area_ids.add(area_id)
        _require(
            refined_summary["coarse_method_version"] != refined_summary["fine_method_version"],
            "coarse_fine_versions_not_distinct",
            f"{refined_summary['candidate_id']}: coarse and fine methods are not distinct",
        )
        _require(
            refined_summary["coarse_support"]["window_size_m"] == 5000.0
            and refined_summary["coarse_support"]["source_resolution_m"] == 250.0,
            "coarse_support_mismatch",
            f"{refined_summary['candidate_id']}: coarse support is not 5 km / 250 m",
        )
        _require(
            refined_summary["fine_support"]["window_size_m"] == FOCUS_WINDOW_M
            and refined_summary["fine_support"]["resolution_m"] == 90.0,
            "fine_support_mismatch",
            f"{refined_summary['candidate_id']}: fine support is not 1.98 km / 90 m",
        )
        link_summaries.append({**refined_summary, **focus_summary})

    failure_summaries = _check_failures(report, repository)

    _require(
        report.get("recall_estimate", {}).get("value") is None,
        "recall_claim_present",
        "the pilot must not claim a numeric recall estimate",
    )
    _require(
        "not global coverage" in " ".join(report.get("limitations") or []),
        "coverage_claim_missing",
        "refinement limitations must state that this is not global coverage",
    )

    limitations = list(report.get("limitations") or [])
    promotion_note = next(
        (
            limitation
            for limitation in limitations
            if "not promoted into the seedable index" in limitation
        ),
        None,
    )
    _require(
        promotion_note is not None,
        "persistence_limitation_missing",
        "refinement report does not state whether refined windows were persisted",
    )
    db_matches = _scan_db_references(report)
    persistence = {
        "committed_artifacts": [
            {
                "path": str(
                    (INDEX_ROOT / "snapshots" / SNAPSHOT_ID / "manifest.json").relative_to(
                        REPO_ROOT
                    )
                ),
                "sha256": index_summary["manifest_sha256"],
            },
            {
                "path": str(REFINEMENT_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256_file(REFINEMENT_PATH),
            },
        ],
        "db_rows_referenced": bool(db_matches),
        "db_fields_found": db_matches,
        "db_mutated": False,
        "network_used": False,
        "status": "committed_json_and_parquet_only",
        "report_limitation": promotion_note,
        "limitation": (
            "Issue #97 database persistence is NOT demonstrated by this committed artifact: "
            "the coarse->refined link exists only in the committed JSON report and is not a "
            "persisted DB observation."
        ),
    }

    return {
        "ok": True,
        "script": "scripts/check_global_refine_chain.py",
        "coarse_index": index_summary,
        "refinement": {
            "path": str(REFINEMENT_PATH.relative_to(REPO_ROOT)),
            "generated_at": report.get("generated_at"),
            "generated_by": report.get("generated_by"),
            "report_version": report.get("report_version"),
            "link_method_version": report.get("link_method_version"),
            "counts": report.get("counts"),
        },
        "selection": selection_summary,
        "coarse_to_fine": {
            "links_verified": len(link_summaries),
            "failures_verified": len(failure_summaries),
            "links": link_summaries,
            "failures": failure_summaries,
            "coarse_method_version": COARSE_METHOD_VERSION,
            "fine_method_version": METHOD_VERSION,
            "link_method_version": LINK_METHOD_VERSION,
            "coarse_support": "5 km window aggregated from ~250 m source cells",
            "fine_support": "1.98 km focus window at 90 m resolution",
            "coarse_and_fine_scores_interchangeable": False,
        },
        "persistence": persistence,
        "limitations": limitations,
    }


def main() -> int:
    try:
        summary = run()
    except ChainError as exc:
        print(
            json.dumps(
                {"ok": False, "error": {"code": exc.code, "message": exc.message}},
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary must stay auditable
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "unexpected_failure",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
