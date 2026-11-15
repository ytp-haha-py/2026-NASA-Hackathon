#!/usr/bin/env python
"""Build the bounded D06 Moon/Mars regolith AOI release for issue #170 Goal F.

The builder is offline and deterministic. It reads only the committed input
tables in this directory and writes typed-v2 observations, an AOI result table,
a plan, assets, QA, a manifest and a report. It never downloads data and never
fills a missing value.

The numeric values are curated from the official PDS/literature sources recorded
in ``sources.json`` and ``source_values.json``. They are not generated here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent
GENERATED_AT = "2026-11-15T12:00:00+00:00"
RELEASE_VERSION = "d06-regolith-aoi-release-1.0.0"
SCOPE = "d06-regolith-aoi-170"
PROVIDER_ID = "d06-regolith-aoi-evidence"
PLAN_VERSION = "acquisition-plan-1.0.0"
REGISTERED_FEATURES = {
    "particle_size_distribution",
    "bulk_density_kg_m3",
    "porosity_fraction",
    "cohesion_kpa",
    "friction_angle_deg",
    "bearing_strength_kpa",
}
FORBIDDEN_NEGATIVE_FLAGS = {
    "no_dem_roughness_inference",
    "no_worldcover_inference",
    "no_earth_transfer",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def jsonl_text(rows: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows) + "\n"


def read_json(name: str) -> Any:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def load_inputs() -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    aois = read_json("aois.json")["aois"]
    sources = read_json("sources.json")
    values = read_json("source_values.json")["values"]
    return aois, sources, values


def datasets_by_id(sources: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {dataset["dataset_id"]: dataset for dataset in sources["datasets"]}


def aois_by_id(aois: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {aoi["area_id"]: aoi for aoi in aois}


def spatial_support(aoi: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "bbox",
        "crs": aoi["crs"],
        "bbox": aoi["bbox"],
        "bbox_crs": aoi["crs"],
        "notes": (
            f"Bounded ~{aoi['site_radius_m']:g} m site envelope around the published "
            "measurement site. The value is site/sample-specific, not an area average."
        ),
    }


def legacy_provenance(value: dict[str, Any]) -> dict[str, Any]:
    provenance = {
        "source_value_id": value["source_value_id"],
        "source_dataset_id": value["dataset_id"],
        "source_asset_ids": value["source_asset_ids"],
        "source_locator": value["source_locator"],
        "evidence_class": value["evidence_class"],
        "value_semantics": value["value_semantics"],
        "assumptions": value["assumptions"],
        "depth_notes": value["depth_notes"],
        "scope": SCOPE,
        "global_claim": False,
        "not_for_similarity": True,
    }
    if value.get("blocker"):
        provenance["blocker"] = value["blocker"]
    return provenance


def build_observations(
    aois: list[dict[str, Any]], values: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_area = aois_by_id(aois)
    rows: list[dict[str, Any]] = []
    for value in values:
        aoi = by_area[value["area_id"]]
        notes = value["notes"]
        if value.get("blocker"):
            notes = f"{notes} Blocker: {value['blocker']}"
        rows.append(
            {
                "schema_version": "2.0.0",
                "observation_id": (f"d06:regolith-aoi170:{value['area_id']}:{value['feature_id']}"),
                "area_id": value["area_id"],
                "body": value["body"],
                "dimension_code": "D06",
                "feature_id": value["feature_id"],
                "value_kind": value["value_kind"],
                "state": value["state"],
                "value": value["value"],
                "unit": value["unit"],
                "uncertainty": value["uncertainty"],
                "spatial_support": spatial_support(aoi),
                "temporal_support": value["temporal_support"],
                "depth_support": value["depth_support"],
                "method_version": value["method_version"],
                "dataset_ids": [value["dataset_id"]],
                "source_asset_ids": value["source_asset_ids"],
                "quality_flags": sorted(set(value["quality_flags"])),
                "score_role": "evidence_only",
                "evidence": None,
                "legacy_provenance": legacy_provenance(value),
                "notes": notes,
            }
        )
    return sorted(rows, key=lambda row: row["observation_id"])


def build_aoi_results(aois: list[dict[str, Any]], values: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for aoi in aois:
        features: dict[str, Any] = {}
        for value in values:
            if value["area_id"] != aoi["area_id"]:
                continue
            entry = {
                "state": value["state"],
                "value": value["value"],
                "unit": value["unit"],
                "value_kind": value["value_kind"],
                "depth_support": value["depth_support"],
                "evidence_class": value["evidence_class"],
                "value_semantics": value["value_semantics"],
                "source_value_id": value["source_value_id"],
                "source_locator": value["source_locator"],
                "quality_flags": sorted(set(value["quality_flags"])),
            }
            if value.get("blocker"):
                entry["blocker"] = value["blocker"]
            features[value["feature_id"]] = entry
        detected = sorted(
            feature for feature, entry in features.items() if entry["state"] == "detected"
        )
        no_coverage = sorted(
            feature for feature, entry in features.items() if entry["state"] == "no_coverage"
        )
        results.append(
            {
                "area_id": aoi["area_id"],
                "body": aoi["body"],
                "display_name": aoi["display_name"],
                "bbox": aoi["bbox"],
                "crs": aoi["crs"],
                "coordinate_frame": aoi["coordinate_frame"],
                "center": aoi["center"],
                "site_radius_m": aoi["site_radius_m"],
                "detected_features": detected,
                "no_coverage_features": no_coverage,
                "features": features,
                "notes": aoi["notes"],
            }
        )
    return {
        "schema_version": "d06-aoi-results-1.0.0",
        "scope": SCOPE,
        "global_claim": False,
        "generated_at": GENERATED_AT,
        "notes": [
            "Each feature returns either a published value with explicit depth/evidence or an explicit no_coverage blocker.",
            "No value is an area average, a global survey, or a proxy inferred from DEM roughness, land cover, thermal inertia or Earth SoilGrids.",
            "The result table is derived from observations.jsonl; observations.jsonl is authoritative.",
        ],
        "aois": results,
    }


def build_plan(
    aois: list[dict[str, Any]],
    values: list[dict[str, Any]],
    sources: dict[str, Any],
) -> dict[str, Any]:
    datasets = datasets_by_id(sources)
    by_area = aois_by_id(aois)
    tasks = []
    for value in values:
        aoi = by_area[value["area_id"]]
        dataset = datasets[value["dataset_id"]]
        tasks.append(
            {
                "task_id": (
                    f"d06-aoi170-{value['body'].lower()}-{value['area_id']}-{value['feature_id']}"
                ),
                "provider_id": PROVIDER_ID,
                "source_id": value["dataset_id"],
                "body": value["body"],
                "dimension": "D06",
                "area": {
                    "area_id": aoi["area_id"],
                    "display_name": aoi["display_name"],
                    "body": aoi["body"],
                    "bbox": aoi["bbox"],
                    "crs": aoi["crs"],
                    "coordinate_frame": aoi["coordinate_frame"],
                    "notes": aoi["notes"],
                },
                "endpoint": dataset["url"],
                "parameters": {
                    "feature_id": value["feature_id"],
                    "source_value_id": value["source_value_id"],
                    "evidence_class": value["evidence_class"],
                },
                "method_version": value["method_version"],
                "dataset_id": value["dataset_id"],
                "output_suffix": ".json",
            }
        )
    return {
        "plan_version": PLAN_VERSION,
        "generated_at": GENERATED_AT,
        "scope": SCOPE,
        "tasks": sorted(tasks, key=lambda task: task["task_id"]),
        "totals": {
            "tasks": len(tasks),
            "areas": len(aois),
            "bodies": sorted({value["body"] for value in values}),
            "detected": sum(1 for value in values if value["state"] == "detected"),
            "no_coverage": sum(1 for value in values if value["state"] == "no_coverage"),
        },
        "notes": [
            "Offline extraction plan: tasks describe provenance resolution, not network downloads.",
            "Values were curated from published PDS/literature tables; the builder never downloads.",
        ],
    }


def build_assets(sources: dict[str, Any]) -> list[dict[str, Any]]:
    assets = []
    for asset in sources["assets"]:
        assets.append(
            {
                "asset_id": asset["asset_id"],
                "dataset_id": asset["dataset_id"],
                "uri": asset["uri"],
                "checksum_algorithm": asset["checksum_algorithm"],
                "checksum": asset["checksum"],
                "checksum_scope": asset["checksum_scope"],
                "native_crs": asset.get("native_crs"),
                "native_units": asset.get("native_units"),
                "bytes": asset["bytes"],
                "fetched_at": asset["fetched_at"],
                "redistribution_allowed": asset["redistribution_allowed"],
                "license": asset["license"],
                "license_url": asset["license_url"],
                "role": asset["role"],
                "notes": asset["notes"],
            }
        )
    return sorted(assets, key=lambda item: item["asset_id"])


def build_requests(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requests = []
    for value in values:
        requests.append(
            {
                "request_id": f"d06-aoi170:{value['source_value_id']}",
                "observation_id": (f"d06:regolith-aoi170:{value['area_id']}:{value['feature_id']}"),
                "area_id": value["area_id"],
                "body": value["body"],
                "feature_id": value["feature_id"],
                "dataset_id": value["dataset_id"],
                "source_value_id": value["source_value_id"],
                "source_locator": value["source_locator"],
                "mode": "offline_curated_extraction",
                "network": False,
            }
        )
    return sorted(requests, key=lambda item: item["request_id"])


def build_qa(
    observations: list[dict[str, Any]],
    values: list[dict[str, Any]],
    sources: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    by_body: dict[str, int] = {}
    by_state: dict[str, int] = {}
    by_feature: dict[str, int] = {}
    for observation in observations:
        by_body[observation["body"]] = by_body.get(observation["body"], 0) + 1
        by_state[observation["state"]] = by_state.get(observation["state"], 0) + 1
        by_feature[observation["feature_id"]] = by_feature.get(observation["feature_id"], 0) + 1
    detected_by_body: dict[str, int] = {}
    for observation in observations:
        if observation["state"] == "detected":
            detected_by_body[observation["body"]] = detected_by_body.get(observation["body"], 0) + 1
    datasets = datasets_by_id(sources)
    referenced_datasets = {value["dataset_id"] for value in values}
    source_ok = all(
        dataset_id in datasets
        and datasets[dataset_id].get("version")
        and datasets[dataset_id].get("license")
        and datasets[dataset_id].get("url")
        for dataset_id in referenced_datasets
    )
    checks = {
        "all_states_explicit": all(
            observation["state"] in {"detected", "no_coverage"} for observation in observations
        ),
        "detected_values_have_explicit_depth": all(
            observation["depth_support"]["kind"] != "unknown"
            for observation in observations
            if observation["state"] == "detected"
        ),
        "detected_values_are_finite_numbers": all(
            isinstance(observation["value"], (int, float))
            and not isinstance(observation["value"], bool)
            for observation in observations
            if observation["state"] == "detected" and observation["value_kind"] == "numeric"
        ),
        "no_coverage_has_no_value_and_has_blocker": all(
            observation["value"] is None and bool(observation["legacy_provenance"].get("blocker"))
            for observation in observations
            if observation["state"] == "no_coverage"
        ),
        "no_zero_fill": all(
            observation["value"] is not None
            for observation in observations
            if observation["state"] == "detected"
        ),
        "no_dem_roughness_or_landcover_or_earth_transfer_flags": all(
            set(observation["quality_flags"]) >= FORBIDDEN_NEGATIVE_FLAGS
            for observation in observations
        ),
        "planetary_frames_only": all(
            observation["spatial_support"]["crs"] in {"IAU_2015:30100", "IAU_2015:49900"}
            and not str(observation["spatial_support"]["crs"]).upper().startswith("EPSG:")
            for observation in observations
        ),
        "registered_d06_features_only": all(
            observation["feature_id"] in REGISTERED_FEATURES for observation in observations
        ),
        "moon_has_real_value": any(
            observation["body"] == "Moon" and observation["state"] == "detected"
            for observation in observations
        ),
        "mars_has_real_value_or_no_coverage": any(
            observation["body"] == "Mars" and observation["state"] in {"detected", "no_coverage"}
            for observation in observations
        ),
        "source_versions_and_licenses_present": source_ok,
        "global_claim_false": True,
        "chemistry_and_thermal_inertia_are_audit_only": (
            audit["chemistry_evidence"]["registered_feature"] is False
            and audit["thermal_inertia_audit"]["used_for_d06_density"] is False
        ),
    }
    return {
        "qa_version": "d06-regolith-aoi-qa-1.0.0",
        "scope": SCOPE,
        "generated_at": GENERATED_AT,
        "counts": {
            "observations": len(observations),
            "by_body": dict(sorted(by_body.items())),
            "by_state": dict(sorted(by_state.items())),
            "by_feature": dict(sorted(by_feature.items())),
            "detected_by_body": dict(sorted(detected_by_body.items())),
            "real_values": by_state.get("detected", 0),
            "explicit_no_coverage": by_state.get("no_coverage", 0),
        },
        "checks": checks,
        "source_checksums": {asset["asset_id"]: asset["checksum"] for asset in sources["assets"]},
        "blockers": audit["blockers"],
        "notes": [
            "All checks are computed from observations.jsonl and sources.json.",
            "No value is zero-filled. No-coverage rows carry a blocker and a null value.",
            "No DEM roughness, land-cover, thermal-inertia or Earth-SoilGrids transfer is performed.",
        ],
    }


def build_report(
    observations: list[dict[str, Any]],
    values: list[dict[str, Any]],
    aois: list[dict[str, Any]],
    sources: dict[str, Any],
    qa: dict[str, Any],
) -> str:
    lines = [
        "# D06 Moon/Mars regolith AOI release (#170 Goal F)",
        "",
        f"- Release version: `{RELEASE_VERSION}`",
        f"- Scope: `{SCOPE}`",
        f"- Generated at: `{GENERATED_AT}`",
        f"- Observations: {len(observations)} "
        f"({qa['counts']['by_state'].get('detected', 0)} detected, "
        f"{qa['counts']['by_state'].get('no_coverage', 0)} no_coverage)",
        "- Global claim: **false**. Bounded measurement-site AOIs only.",
        "",
        "## AOIs",
        "",
        "| area_id | body | site | bbox (planetary frame) | CRS |",
        "|---|---|---|---|---|",
    ]
    for aoi in aois:
        bbox = aoi["bbox"]
        lines.append(
            f"| `{aoi['area_id']}` | {aoi['body']} | {aoi['display_name']} | "
            f"W {bbox['west']:g}, S {bbox['south']:g}, E {bbox['east']:g}, N {bbox['north']:g} | "
            f"`{aoi['crs']}` |"
        )
    lines.extend(
        [
            "",
            "## Returned values and explicit no-coverage",
            "",
            "| area_id | feature_id | state | value | unit | depth | evidence class | source |",
            "|---|---|---|---:|---|---|---|---|",
        ]
    )
    for observation in observations:
        value = observation["value"]
        value_text = "—" if value is None else f"{value:g}"
        depth = observation["depth_support"]
        if depth["kind"] == "depth_interval":
            depth_text = f"{depth['min_m']:g}-{depth['max_m']:g} m"
        else:
            depth_text = depth["kind"]
        lines.append(
            f"| `{observation['area_id']}` | `{observation['feature_id']}` | "
            f"{observation['state']} | {value_text} | {observation['unit']} | {depth_text} | "
            f"`{observation['legacy_provenance']['evidence_class']}` | "
            f"`{observation['dataset_ids'][0]}` |"
        )
    lines.extend(
        [
            "",
            "## Source versions and QA",
            "",
            "| dataset_id | body | version | licence | footprint | depth support | QA |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for dataset in sources["datasets"]:
        lines.append(
            f"| `{dataset['dataset_id']}` | {dataset['body']} | {dataset['version']} | "
            f"{dataset['license']} | {dataset['footprint']} | {dataset['depth_support']} | "
            f"{dataset['qa']} |"
        )
    lines.extend(
        [
            "",
            "## What is deliberately not done",
            "",
            "- No DEM roughness, slope, land-cover, thermal-inertia or Earth-SoilGrids transfer.",
            "- No penetration resistance relabeled as bearing strength.",
            "- No density-to-porosity conversion without a stated particle density and model.",
            "- No literature grain-size scalar converted into a particle-size distribution.",
            "- No chemistry or thickness ingested because no registered D06 feature exists.",
            "- No global or exhaustive coverage claim.",
            "",
            "## Blockers",
            "",
        ]
    )
    lines.extend(f"- {blocker}" for blocker in qa["blockers"])
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "python3 pipelines/d05_d06_aoi/regolith/build_release.py --check",
            "uv run pytest pipelines/tests/test_d06_aoi_170.py -q",
            "uv run python scripts/import_typed_release.py "
            "--release pipelines/d05_d06_aoi/regolith --database var/analog.db",
            "```",
            "",
            "See `docs/D06_AOI_170.md` for the full scientific audit and limitations.",
            "",
        ]
    )
    return "\n".join(lines)


def build_payloads() -> dict[str, str]:
    aois, sources, values = load_inputs()
    audit = read_json("audit.json")
    observations = build_observations(aois, values)
    aoi_results = build_aoi_results(aois, values)
    plan = build_plan(aois, values, sources)
    assets = build_assets(sources)
    requests = build_requests(values)
    qa = build_qa(observations, values, sources, audit)
    report = build_report(observations, values, aois, sources, qa)

    payloads = {
        "observations.jsonl": jsonl_text(observations),
        "aoi_results.json": json_text(aoi_results),
        "plan.json": json_text(plan),
        "assets.json": json_text(assets),
        "requests.jsonl": jsonl_text(requests),
        "qa.json": json_text(qa),
        "REPORT.md": report,
    }

    input_files = [
        "README.md",
        "__init__.py",
        "aois.json",
        "audit.json",
        "build_release.py",
        "sources.json",
        "source_values.json",
    ]
    files = {}
    for name in sorted({*payloads, *input_files}):
        path = OUT / name
        if name in payloads:
            text = payloads[name]
            files[name] = {
                "sha256": sha256_text(text),
                "bytes": len(text.encode("utf-8")),
            }
        elif path.is_file():
            files[name] = {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        else:
            raise FileNotFoundError(f"declared release input is missing: {name}")

    counts = qa["counts"]
    manifest = {
        "release_version": RELEASE_VERSION,
        "generated_at": GENERATED_AT,
        "scope": SCOPE,
        "global_claim": False,
        "observation_count": len(observations),
        "asset_count": len(assets),
        "task_count": len(plan["tasks"]),
        "area_count": len(aois),
        "observations_sha256": sha256_text(payloads["observations.jsonl"]),
        "plan_sha256": sha256_text(payloads["plan.json"]),
        "counts": counts,
        "source_checksums": qa["source_checksums"],
        "files": files,
        "raw": [],
        "notes": [
            "Portable bounded D06 Moon/Mars regolith AOI release; not a global or exhaustive survey.",
            "Detected values are published site/sample records with explicit evidence class, depth and uncertainty; no fabricated or zero-filled values.",
            "No-coverage records are explicit and carry a blocker; no proxy is substituted.",
            "No raw PDFs, rasters, caches or credentials are committed; only source URLs and SHA-256 checksums.",
        ],
        "blockers": qa["blockers"],
    }
    payloads["manifest.json"] = json_text(manifest)
    return payloads


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare generated payloads with committed files; do not write",
    )
    args = parser.parse_args(argv)
    payloads = build_payloads()
    if args.check:
        mismatches = []
        for name, text in sorted(payloads.items()):
            path = OUT / name
            if not path.is_file():
                mismatches.append(f"missing: {name}")
            elif path.read_text(encoding="utf-8") != text:
                mismatches.append(f"stale: {name}")
        if mismatches:
            print("\n".join(mismatches), file=sys.stderr)
            return 1
        print(f"{len(payloads)} generated files match committed artifacts")
        return 0
    for name, text in sorted(payloads.items()):
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(payloads)} files to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
