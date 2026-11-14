"""Independent geology CLI. All source caches must live outside the Git repository."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from analog_pipelines.geology.areas import existing_areas, load_areas, polygon
from analog_pipelines.geology.io import Cache, build_snapshot, verify_snapshot, write_json
from analog_pipelines.geology.matching import match_categories
from analog_pipelines.geology.models import Area, Dataset, Manifest, digest, validate_areas
from analog_pipelines.geology.providers import (
    GLIM,
    base_analysis,
    glim,
    mars,
    mars_index,
    moon,
    moon_index,
)

REPO = Path(__file__).resolve().parents[4]
DEFAULT_ROOT = REPO / "pipelines/geology"


def test_areas() -> list[Area]:
    return [
        Area(
            area_id="mars-test-jezero-" + name,
            display_name="Jezero " + name + " (test AOI)",
            body="Mars",
            crs="+proj=longlat +R=3396190 +type=crs",
            geometry=polygon(*bounds),
            origin="test_aoi",
            geometry_source="Analyst-selected validation window inside official SIM3464 extent (76.8..77.8 E, 17.7..18.7 N).",
            limitations=[
                "Explicit test AOI, not an existing shared Mars reference or indexed production area."
            ],
        )
        for name, bounds in [
            ("western-delta", (77.35, 18.47, 77.38, 18.50)),
            ("floor", (77.40, 18.42, 77.44, 18.46)),
        ]
    ]


def selected_areas(path: Path) -> list[Area]:
    areas = load_areas(path)
    known = {a.area_id: a for a in existing_areas(REPO)}
    for area in areas:
        if (
            area.area_id in known
            and area.geometry_fingerprint != known[area.area_id].geometry_fingerprint
        ):
            raise ValueError(f"existing Area ID has changed geometry: {area.area_id}")
    return areas


def load_catalog(root: Path) -> list[Dataset]:
    return [
        Dataset.model_validate(row)
        for row in json.loads((root / "catalog.json").read_text())["datasets"]
    ]


def external_cache(path: Path, offline: bool) -> Cache:
    if path.resolve().is_relative_to(REPO):
        raise ValueError("large data/cache root must be outside the Git repository")
    return Cache(path, offline)


def audit(areas: list[Area], cache: Cache, output: Path) -> dict:
    from analog_pipelines.geology.mineralogy import CMR, emit_audit

    datasets = load_catalog(DEFAULT_ROOT)
    report = {
        "audited_at": datetime.now(UTC).isoformat(),
        "aoi_counts": dict(Counter(a.body for a in areas)),
        "test_aoi_counts": dict(Counter(a.body for a in areas if a.origin == "test_aoi")),
        "aoi_identities": {a.area_id: a.geometry_fingerprint for a in areas},
        "sequence": ["coverage_audit", "geological_maps", "mineralogy", "portable_snapshot"],
        "sources": [],
        "emit_coverage": [],
        "limitations": [
            "Metadata footprints do not establish valid observation coverage.",
            "Subset audit only; no global/exhaustive claim.",
        ],
    }
    for dataset in datasets:
        report["sources"].append(
            {
                "dataset_id": dataset.dataset_id,
                "body": dataset.body,
                "version": dataset.version,
                "source_url": dataset.source_url,
                "license": dataset.license,
                "redistribution": dataset.redistribution,
                "map_scale": dataset.map_scale,
                "native_resolution_m": dataset.native_resolution_m,
                "planned_download": {
                    "usgs-moon-geology-v2": "224,413,040 bytes full GIS, excluding optional terrain/hillshade products",
                    "usgs-jezero-sim3464": "Full official GIS package + map sheet; exact bytes in download receipts",
                    "lunar-polar-minerals-zenodo-v4": "63,028,597 bytes MAT + 14,640,014 bytes legacy RGB for format validation",
                    "glim-niveau-i-mirror": "Bounded AOI polygon queries only; no full global download",
                    "crism-mtrdr-context": "At most one selected summary + DP information cube per AOI; no full spectral cube",
                }.get(
                    dataset.dataset_id,
                    "CMR metadata only until Earthdata authentication is available",
                ),
            }
        )
    try:
        _, receipt = cache.json(GLIM + "?f=pjson", "glim-layer.json")
        report["glim_metadata"] = receipt.model_dump()
    except Exception as error:
        report["glim_metadata"] = {"status": "failed", "reason": str(error)}
    report["emit_collection_query"] = CMR + "/collections.json?short_name=EMITL2BMIN&page_size=30"
    for area in areas:
        if area.body != "Earth":
            continue
        for dataset in datasets:
            if dataset.product == "EMITL2BMIN":
                try:
                    _, entry = emit_audit(area, dataset, cache)
                except Exception as error:
                    entry = {
                        "area_id": area.area_id,
                        "version": dataset.version,
                        "status": "failed",
                        "reason": str(error),
                    }
                report["emit_coverage"].append(entry)
    write_json(output, report)
    return report


def summarize(manifest: Manifest) -> dict:
    by_body = {}
    for body in ("Earth", "Moon", "Mars"):
        areas = [a for a in manifest.areas if a.body == body]
        records = [r for r in manifest.analyses if r.body == body]
        mineral_areas = {
            r.area_id
            for r in records
            if any(
                m.quality == "verified"
                and m.state in {"detected", "not_detected_under_valid_observation"}
                for m in r.mineralogy
            )
        }
        by_body[body] = {
            "requested_aoi_count": len(areas),
            "existing_aoi_count": sum(a.origin == "existing" for a in areas),
            "test_aoi_count": sum(a.origin == "test_aoi" for a in areas),
            "portable_geological_map_success_aoi_count": len(
                {r.area_id for r in records if r.lithology}
            ),
            "verified_mineral_observation_aoi_count": len(mineral_areas),
            "verified_mineral_aoi_fraction": len(mineral_areas) / len(areas) if areas else None,
            "provider_status_counts": dict(Counter(r.status for r in records)),
        }
    return {
        "scope": manifest.scope,
        "by_body": by_body,
        "supported_categories": sorted(
            {p.category for r in manifest.analyses for p in r.geological_processes}
        ),
        "activated_candidate_features": sorted(
            {f for r in manifest.analyses for u in r.lithology for f in u.unified_rocks}
        ),
        "limitations": [
            "Mineral AOI fraction counts validated observations, not provider footprint intersections.",
            "GLiM local-only results are withheld pending rights verification and are excluded from portable success counts.",
        ],
    }


def analyze(args) -> dict:
    from analog_pipelines.geology.mineralogy import crism_context, emit_audit, lunar_audit

    root = args.output_dir.resolve()
    if root.is_relative_to(REPO) and not root.is_relative_to(DEFAULT_ROOT):
        raise ValueError("repository outputs must remain inside pipelines/geology")
    areas = selected_areas(args.areas)
    cache = external_cache(args.data_root, args.offline)
    audit_path = root / "coverage-audit.json"
    identities = {a.area_id: a.geometry_fingerprint for a in areas}
    audit_report = json.loads(audit_path.read_text()) if audit_path.exists() else None
    if not audit_report or audit_report.get("aoi_identities") != identities:
        audit_report = audit(areas, cache, audit_path)
    datasets = load_catalog(DEFAULT_ROOT)
    # Read-only resume by area geometry, source version and processing version.
    checkpoint_path = cache.root / "analysis-checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
    previous, prepared, analyses, inspections = checkpoint.get("records", {}), {}, [], {}
    for phase in ("geological_maps", "mineralogy"):
        print(phase, flush=True)
        for area in areas:
            for dataset in datasets:
                if area.body != dataset.body:
                    continue
                is_map = dataset.dataset_id in {
                    "glim-niveau-i-mirror",
                    "usgs-moon-geology-v2",
                    "usgs-jezero-sim3464",
                }
                if is_map != (phase == "geological_maps"):
                    continue
                key = digest(
                    {
                        "area": area.model_dump(),
                        "dataset": dataset.model_dump(),
                        "method": "geology-1.0.0",
                    }
                )
                if (
                    key in previous
                    and not args.refresh
                    and (
                        not args.retry_unavailable
                        or previous[key]["status"] in {"success", "context_only"}
                    )
                ):
                    from analog_pipelines.geology.models import Analysis

                    analyses.append(Analysis.model_validate(previous[key]))
                    print(
                        f"  cached {area.area_id} {dataset.dataset_id}: {analyses[-1].status}",
                        flush=True,
                    )
                    continue
                try:
                    if dataset.dataset_id == "glim-niveau-i-mirror":
                        record = glim(area, dataset, cache)
                    elif dataset.dataset_id in {"usgs-moon-geology-v2", "usgs-jezero-sim3464"}:
                        if dataset.dataset_id not in prepared:
                            prepared[dataset.dataset_id] = (
                                moon_index if area.body == "Moon" else mars_index
                            )(cache)
                        result = prepared[dataset.dataset_id]
                        record = (moon if area.body == "Moon" else mars)(area, dataset, result)
                    elif dataset.product == "EMITL2BMIN":
                        record, inspection = emit_audit(area, dataset, cache)
                        inspection["geometry_fingerprint"] = area.geometry_fingerprint
                        inspections[area.area_id + ":" + dataset.dataset_id] = inspection
                    elif dataset.dataset_id == "lunar-polar-minerals-zenodo-v4":
                        record, inspection = lunar_audit(area, dataset, cache)
                        inspection["geometry_fingerprint"] = area.geometry_fingerprint
                        inspections[area.area_id + ":" + dataset.dataset_id] = inspection
                    else:
                        record, inspection = crism_context(area, dataset, cache)
                        inspection["geometry_fingerprint"] = area.geometry_fingerprint
                        inspections[area.area_id + ":" + dataset.dataset_id] = inspection
                except Exception as error:
                    record = base_analysis(
                        area, dataset, status="failed", reason=f"{type(error).__name__}: {error}"
                    )
                previous[key] = record.model_dump(mode="json")
                write_json(
                    checkpoint_path,
                    {
                        "records": previous,
                        "inspections": {**checkpoint.get("inspections", {}), **inspections},
                    },
                )
                analyses.append(record)
                print(f"  {area.area_id} {dataset.dataset_id}: {record.status}", flush=True)
    manifest = Manifest(
        generated_at=max(r.accessed_at for r in analyses),
        data_kind="source_derived",
        areas=validate_areas(areas),
        datasets=datasets,
        analyses=analyses,
        scope="Five existing Earth AOIs, one existing lunar ROI, and explicitly marked Mars test AOIs, or an externally supplied bounded subset; never a global survey.",
        limitations=[
            "Independent geology dimension; not integrated with production terrain registry, API, SQLite or frontend.",
            "Every observation inherits area geometry/body and dataset source/version/scale/license via validated IDs.",
            "Unknown, failed and unavailable observations are not zero-filled.",
        ],
    )
    for dataset in manifest.datasets:
        crs_values = {
            r.source_crs for r in analyses if r.dataset_id == dataset.dataset_id and r.source_crs
        }
        if len(crs_values) == 1:
            dataset.source_crs = crs_values.pop()
        resolutions = {
            r.native_resolution_m
            for r in analyses
            if r.dataset_id == dataset.dataset_id and r.native_resolution_m
        }
        if len(resolutions) == 1:
            dataset.native_resolution_m = resolutions.pop()
        elif len(resolutions) > 1:
            dataset.native_resolution_m = None
    # Validation again after attaching source CRS from successfully read GIS metadata.
    manifest = Manifest.model_validate(manifest.model_dump())
    payload = manifest.model_dump(mode="json")
    identity = digest(payload).split(":")[1][:16]
    # */manifests/*.json belongs to the shared terrain contract/data gate.
    manifest_name = f"records/geology-{identity}.json"
    write_json(root / manifest_name, payload)
    files = [manifest_name]
    artifacts = {
        "catalog.json": {
            "catalog_version": "geology-catalog-1.0.0",
            "datasets": [d.model_dump() for d in manifest.datasets],
        },
        "areas.json": {"areas": [a.model_dump() for a in areas]},
        "summary.json": summarize(manifest),
        "source-inspections.json": {
            key: value
            for key, value in {**checkpoint.get("inspections", {}), **inspections}.items()
            if value.get("geometry_fingerprint") in identities.values()
        },
        "coverage-audit.json": audit_report,
        "manifest.schema.json": Manifest.model_json_schema(),
        "taxonomy.json": json.loads((DEFAULT_ROOT / "taxonomy.json").read_text()),
    }
    write_json(
        checkpoint_path, {"records": previous, "inspections": artifacts["source-inspections.json"]}
    )
    for name, value in artifacts.items():
        path = root / "artifacts" / (digest(value).split(":")[1][:16] + "-" + name)
        write_json(path, value)
        files.append(str(path.relative_to(root)))
    snapshot = build_snapshot(root, files)
    write_json(root / "snapshots" / (snapshot["snapshot_id"] + ".json"), snapshot)
    write_json(root / "snapshots/geology.snapshot.json", snapshot)
    verify_snapshot(root, snapshot)
    print(json.dumps(summarize(manifest), indent=2, ensure_ascii=False))
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export-areas")
    export.add_argument("--output", type=Path, default=DEFAULT_ROOT / "areas.json")
    export.add_argument("--with-test-aois", action="store_true")
    for name in ("audit", "analyze"):
        command = sub.add_parser(name)
        command.add_argument("--areas", type=Path, default=DEFAULT_ROOT / "areas.json")
        command.add_argument("--data-root", type=Path, default=os.environ.get("GEOLOGY_DATA_ROOT"))
        command.add_argument("--offline", action="store_true")
        if name == "audit":
            command.add_argument(
                "--output", type=Path, default=DEFAULT_ROOT / "coverage-audit.json"
            )
        else:
            command.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT)
            command.add_argument("--retry-unavailable", action="store_true")
            command.add_argument("--refresh", action="store_true")
    for name in ("verify", "match", "report"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=DEFAULT_ROOT)
        command.add_argument("--snapshot", type=Path)
        if name == "match":
            command.add_argument("--categories", nargs="+", default=[])
            command.add_argument("--rocks", nargs="+", default=[])
    args = parser.parse_args()
    if args.command == "export-areas":
        areas = existing_areas(REPO) + (test_areas() if args.with_test_aois else [])
        write_json(args.output, {"areas": [a.model_dump() for a in validate_areas(areas)]})
    elif args.command in {"audit", "analyze"}:
        if args.data_root is None:
            parser.error(
                "set --data-root or GEOLOGY_DATA_ROOT to a location outside the repository"
            )
        if args.command == "audit":
            report = audit(
                selected_areas(args.areas),
                external_cache(args.data_root, args.offline),
                args.output,
            )
            print(json.dumps(report["aoi_counts"]))
        else:
            analyze(args)
    else:
        snapshot_path = args.snapshot or args.root / "snapshots/geology.snapshot.json"
        snapshot = json.loads(snapshot_path.read_text())
        manifest = verify_snapshot(args.root, snapshot)
        if args.command == "verify":
            print(
                f"PASS {snapshot['snapshot_id']} {len(snapshot['files'])} pinned files; manifest validated; no source downloads"
            )
        elif args.command == "report":
            print(json.dumps(summarize(manifest), indent=2, ensure_ascii=False))
        else:
            print(
                json.dumps(
                    match_categories(manifest.analyses, args.categories, args.rocks),
                    indent=2,
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    main()
