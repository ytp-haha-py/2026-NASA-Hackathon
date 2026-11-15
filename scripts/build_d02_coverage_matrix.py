#!/usr/bin/env python
"""Build the D02 (Geology & Mineralogy) Earth/Moon/Mars coverage matrix.

Reads committed bounded D02 artifacts only (no network, no source download).
Outputs pipelines/geology/d02-coverage-matrix.json.
"""

from __future__ import annotations

import collections
import datetime
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sha(rel: str) -> str:
    return "sha256:" + hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def main() -> int:
    typed_dir = ROOT / "pipelines/geology/typed"
    obs = [
        json.loads(line)
        for line in (typed_dir / "observations.jsonl").read_text().splitlines()
        if line.strip()
    ]
    datasets = {d["dataset_id"]: d for d in json.loads((typed_dir / "datasets.json").read_text())}
    manifest = json.loads((typed_dir / "manifest.json").read_text())
    source_audit = json.loads((ROOT / "docs/data-sources/d02-geology.json").read_text())

    features = [
        (
            "geologic_unit",
            "lithology",
            "category",
            "Map unit/category from geologic map overlay; categorical, no interpolation.",
            ["Earth", "Moon", "Mars"],
        ),
        (
            "geologic_process",
            "geological_process",
            "process_evidence",
            "Process interpretation evidence tied to mapped units/source descriptions.",
            ["Earth", "Moon", "Mars"],
        ),
        (
            "mineral_olivine",
            "mineral_spectral_detection",
            "detection",
            "Mineral/spectral detection state (no abundance).",
            ["Earth", "Moon", "Mars"],
        ),
        (
            "mineral_pyroxene",
            "mineral_spectral_detection",
            "detection",
            "Mineral/spectral detection state (no abundance).",
            ["Earth", "Moon", "Mars"],
        ),
        (
            "mineral_clay_minerals",
            "mineral_spectral_detection",
            "detection",
            "Mineral/spectral detection state (no abundance).",
            ["Earth", "Moon", "Mars"],
        ),
        (
            "mineral_hydrated_sulfates",
            "mineral_spectral_detection",
            "detection",
            "Mineral/spectral detection state (no abundance).",
            ["Earth", "Moon", "Mars"],
        ),
        (
            "mineral_carbonates",
            "mineral_spectral_detection",
            "detection",
            "Mineral/spectral detection state (no abundance).",
            ["Earth", "Moon", "Mars"],
        ),
        (
            "context_olindex3",
            "mineral_spectral_detection",
            "context_numeric",
            "CRISM spectral parameter (context only, not abundance/detection).",
            ["Mars"],
        ),
        (
            "context_lcpindex2",
            "mineral_spectral_detection",
            "context_numeric",
            "CRISM spectral parameter (context only, not abundance/detection).",
            ["Mars"],
        ),
        (
            "context_hcpindex2",
            "mineral_spectral_detection",
            "context_numeric",
            "CRISM spectral parameter (context only, not abundance/detection).",
            ["Mars"],
        ),
        (
            "context_d2300",
            "mineral_spectral_detection",
            "context_numeric",
            "CRISM spectral parameter (context only, not abundance/detection).",
            ["Mars"],
        ),
        (
            "context_bd1900_2",
            "mineral_spectral_detection",
            "context_numeric",
            "CRISM spectral parameter (context only, not abundance/detection).",
            ["Mars"],
        ),
        (
            "context_sindex2",
            "mineral_spectral_detection",
            "context_numeric",
            "CRISM spectral parameter (context only, not abundance/detection).",
            ["Mars"],
        ),
        (
            "context_cindex2",
            "mineral_spectral_detection",
            "context_numeric",
            "CRISM spectral parameter (context only, not abundance/detection).",
            ["Mars"],
        ),
        (
            "geology_analysis_availability",
            "quality",
            "availability",
            "Source/analysis availability and blockers (quality/evidence record).",
            ["Earth", "Moon", "Mars"],
        ),
    ]

    idx = collections.defaultdict(list)
    for o in obs:
        idx[(o["body"], o["feature_id"])].append(o)

    body_feature_datasets = {
        ("Earth", "geologic_unit"): ["glim-niveau-i-mirror"],
        ("Earth", "geologic_process"): ["glim-niveau-i-mirror"],
        ("Earth", "geology_analysis_availability"): [
            "emit-l2bmin-002",
            "emit-l2bmin-001",
            "glim-niveau-i-mirror",
        ],
        ("Earth", "mineral_olivine"): ["emit-l2bmin-002", "emit-l2bmin-001"],
        ("Earth", "mineral_pyroxene"): ["emit-l2bmin-002", "emit-l2bmin-001"],
        ("Earth", "mineral_clay_minerals"): ["emit-l2bmin-002", "emit-l2bmin-001"],
        ("Earth", "mineral_hydrated_sulfates"): ["emit-l2bmin-002", "emit-l2bmin-001"],
        ("Earth", "mineral_carbonates"): ["emit-l2bmin-002", "emit-l2bmin-001"],
        ("Moon", "geologic_unit"): ["usgs-moon-geology-v2"],
        ("Moon", "geologic_process"): ["usgs-moon-geology-v2"],
        ("Moon", "geology_analysis_availability"): [
            "lunar-polar-minerals-zenodo-v4",
            "usgs-moon-geology-v2",
        ],
        ("Moon", "mineral_olivine"): ["lunar-polar-minerals-zenodo-v4"],
        ("Moon", "mineral_pyroxene"): ["lunar-polar-minerals-zenodo-v4"],
        ("Moon", "mineral_clay_minerals"): ["lunar-polar-minerals-zenodo-v4"],
        ("Moon", "mineral_hydrated_sulfates"): ["lunar-polar-minerals-zenodo-v4"],
        ("Moon", "mineral_carbonates"): ["lunar-polar-minerals-zenodo-v4"],
        ("Mars", "geologic_unit"): ["usgs-jezero-sim3464"],
        ("Mars", "geologic_process"): ["usgs-jezero-sim3464"],
        ("Mars", "geology_analysis_availability"): ["crism-mtrdr-context", "usgs-jezero-sim3464"],
        ("Mars", "mineral_olivine"): ["crism-mtrdr-context"],
        ("Mars", "mineral_pyroxene"): ["crism-mtrdr-context"],
        ("Mars", "mineral_clay_minerals"): ["crism-mtrdr-context"],
        ("Mars", "mineral_hydrated_sulfates"): ["crism-mtrdr-context"],
        ("Mars", "mineral_carbonates"): ["crism-mtrdr-context"],
    }
    for c in [
        "context_olindex3",
        "context_lcpindex2",
        "context_hcpindex2",
        "context_d2300",
        "context_bd1900_2",
        "context_sindex2",
        "context_cindex2",
    ]:
        body_feature_datasets[("Mars", c)] = ["crism-mtrdr-context"]

    sa = {
        s.get("source_id") or s.get("id"): s
        for s in source_audit.get("sources", [])
        if s.get("source_id") or s.get("id")
    }
    ds_to_sa = {
        "glim-niveau-i-mirror": "arcgis-glim-vector-mirror",
        "usgs-moon-geology-v2": "usgs-moon-unified-geologic-map-v2",
        "usgs-jezero-sim3464": "usgs-mars-sim3464-jezero-geologic-map",
        "emit-l2bmin-002": "nasa-emit-l2b-mineral",
        "emit-l2bmin-001": "nasa-emit-l2b-mineral",
        "crism-mtrdr-context": "pds-crism-mtrdr",
        "lunar-polar-minerals-zenodo-v4": None,
    }

    rows = []
    for fid, subgroup, kind, desc, bodies in features:
        for body in ["Earth", "Moon", "Mars"]:
            applicable = body in bodies
            olist = idx.get((body, fid), [])
            states = sorted(set(o.get("state") for o in olist))
            if not applicable:
                actual = "not_applicable"
            elif not olist:
                actual = "unsupported_no_indexed_observation"
            elif fid.startswith("context_"):
                actual = "indexed_queryable_context_only_bounded"
            else:
                actual = "indexed_queryable_evidence_only_bounded"

            ds_ids = body_feature_datasets.get((body, fid), [])
            sources = []
            for did in ds_ids:
                d = datasets.get(did)
                if not d:
                    continue
                sad = sa.get(ds_to_sa.get(did, did)) or sa.get(did) or {}
                lic = d.get("license")
                if isinstance(sad.get("license"), dict):
                    lic = sad["license"].get("name") or lic
                sources.append(
                    {
                        "source_id": did,
                        "source_audit_id": ds_to_sa.get(did, did),
                        "provider": d.get("provider"),
                        "product": d.get("product_id"),
                        "version": d.get("version"),
                        "licence": lic,
                        "licence_url": d.get("license_url"),
                        "url": d.get("url"),
                        "verification_status": d.get("verification_status"),
                        "redistribution_allowed": d.get("redistribution_allowed"),
                        "crs_or_frame": d.get("native_crs"),
                        "native_resolution_m": d.get("native_resolution_m"),
                        "product_spatial_support": (sad.get("coverage") or {}).get("extent"),
                        "product_scope": (sad.get("coverage") or {}).get("scope"),
                        "temporal_support": d.get("temporal_support"),
                        "depth_support": d.get("depth_support"),
                        "qa": (
                            (sad.get("qa") or {}).get("notes")
                            if isinstance(sad.get("qa"), dict)
                            else sad.get("qa")
                        ),
                        "uncertainty": (
                            (sad.get("uncertainty") or {}).get("notes")
                            if isinstance(sad.get("uncertainty"), dict)
                            else sad.get("uncertainty")
                        ),
                        "source_gaps_blockers": sad.get("blockers") or [],
                        "source_audit_status": sad.get("status"),
                    }
                )

            qflags = sorted({f for o in olist for f in (o.get("quality_flags") or [])})
            notes = sorted({n for o in olist for n in [o.get("notes")] if n})
            unc = [
                o.get("uncertainty") for o in olist if o.get("uncertainty") not in (None, {}, [])
            ]
            valid_fracs = [
                o.get("spatial_support", {}).get("valid_fraction")
                for o in olist
                if o.get("spatial_support", {}).get("valid_fraction") is not None
            ]
            areas = sorted({o["area_id"] for o in olist})
            if fid.startswith("context_"):
                legal_role = "evidence_context_only_not_filter_not_similarity"
            elif fid == "geology_analysis_availability":
                legal_role = "quality_evidence_only"
            else:
                legal_role = "evidence_only_no_similarity_no_hard_filter"

            if not applicable:
                obs_class = "not_applicable"
            elif not olist:
                obs_class = "unsupported"
            elif set(states) == {"no_coverage"}:
                obs_class = "no_coverage"
            elif fid.startswith("context_"):
                obs_class = "derived_context_evidence"
            elif "detected" in states or "not_detected" in states:
                obs_class = "evidence_mapped_or_detected_state"
            else:
                obs_class = "evidence_unknown_or_blocked"

            row = {
                "dimension": "D02",
                "dimension_name": "Geology & Mineralogy",
                "body": body,
                "feature_id": fid,
                "subgroup": subgroup,
                "feature_kind": kind,
                "feature_description": desc,
                "observation_class": obs_class,
                "actual_indexed_queryable_coverage": actual,
                "indexed_observation_count": len(olist),
                "indexed_area_ids": areas,
                "states_present": states,
                "state_counts": dict(collections.Counter(o.get("state") for o in olist)),
                "sources": sources,
                "spatial_support_summary": {
                    "representative_bbox_crs": sorted(
                        {
                            (o.get("spatial_support") or {}).get("bbox_crs")
                            for o in olist
                            if (o.get("spatial_support") or {}).get("bbox_crs")
                        }
                    ),
                    "native_resolution_m_values": sorted(
                        {
                            (o.get("spatial_support") or {}).get("native_resolution_m")
                            for o in olist
                            if (o.get("spatial_support") or {}).get("native_resolution_m")
                            is not None
                        }
                    ),
                    "valid_fraction_values": valid_fracs,
                    "notes": sorted(
                        {
                            (o.get("spatial_support") or {}).get("notes")
                            for o in olist
                            if (o.get("spatial_support") or {}).get("notes")
                        }
                    ),
                },
                "temporal_support_summary": sorted(
                    {
                        (o.get("temporal_support") or {}).get("kind")
                        for o in olist
                        if (o.get("temporal_support") or {}).get("kind")
                    }
                ),
                "depth_support_summary": sorted(
                    {
                        (o.get("depth_support") or {}).get("kind")
                        for o in olist
                        if (o.get("depth_support") or {}).get("kind")
                    }
                ),
                "qa_flags": qflags,
                "qa_notes": notes,
                "uncertainty": unc if unc else None,
                "legal_score_filter_roles": {
                    "score_role": "evidence_only",
                    "similarity_weighted": False,
                    "hard_filter": False,
                    "context_filter_candidate": fid.startswith("context_"),
                    "role_note": legal_role,
                },
                "gaps": [],
                "rebuild_instructions": [
                    "Offline/read-only: uv run python -m analog_pipelines.geology verify",
                    "Offline/read-only: uv run python scripts/build_mineral_evidence.py",
                    "Offline/read-only (GIS env): uv run --with-requirements pipelines/geology/requirements-ingest.txt python -m analog_pipelines.geology area-fraction --report-only",
                    "Typed export (from committed record): uv run python scripts/export_geology_typed.py --record pipelines/geology/records/geology-e920e7e3ae79e176.json --output pipelines/geology/typed",
                    "Re-ingest/recompute from source caches (bounded, external data root, no global download): export GEOLOGY_DATA_ROOT=/absolute/path/outside/repo && uv run --with-requirements pipelines/geology/requirements-ingest.txt python -m analog_pipelines.geology analyze --offline",
                ],
            }

            gaps = []
            if row["observation_class"] in ("unsupported", "not_applicable"):
                gaps.append(
                    "No indexed observation for this body/feature in the bounded D02 export."
                )
            if row["observation_class"] == "no_coverage":
                gaps.append(
                    "Source query indicates no coverage for indexed AOIs; this is not zero and not absence."
                )
            if row["observation_class"] == "evidence_unknown_or_blocked":
                gaps.append(
                    "Records exist but state is unknown/blocked/unverified; no reliable detection/value claim."
                )
            for s in sources:
                if s.get("redistribution_allowed") in ("no", "unknown"):
                    gaps.append(
                        f"Redistribution/licence constraint for {s['source_id']}: {s.get('licence')} ({s.get('redistribution_allowed')})."
                    )
                for b in s.get("source_gaps_blockers") or []:
                    bid = b.get("blocker_id") if isinstance(b, dict) else b
                    detail = b.get("detail") if isinstance(b, dict) else ""
                    gaps.append(f"{s['source_id']} blocker {bid}: {detail}".strip())
            if body == "Earth" and fid in ("geologic_unit", "geologic_process"):
                gaps.append(
                    "No shareable Earth map-unit/process values exported; GLiM mirror licence/equivalence unverified and values withheld."
                )
            if body == "Moon" and fid.startswith("mineral_"):
                gaps.append(
                    "M3 registration/threshold validation missing; lunar research mineral matrix not spatially/quantitatively usable."
                )
            if body == "Mars" and fid.startswith("mineral_"):
                gaps.append(
                    "CRISM mineral detection not validated; all mineral states remain unknown (not not_detected, not abundance)."
                )
            if body == "Mars" and fid in ("geologic_unit", "geologic_process"):
                gaps.append(
                    "Mapped only for two explicitly marked Jezero test AOIs (1:75,000); not a Mars reference and not global."
                )
            if body == "Moon" and fid in ("geologic_unit", "geologic_process"):
                gaps.append(
                    "Mapped for one Shackleton ROI at 1:5,000,000; local lithology/composition not resolved."
                )
            row["gaps"] = sorted({g for g in gaps if g})
            rows.append(row)

    matrix = {
        "artifact": "d02-coverage-matrix",
        "version": "1.0.0",
        "dimension": "D02",
        "dimension_name": "Geology & Mineralogy",
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "scope_and_non_claims": {
            "bounded_pilot": True,
            "global_coverage_claimed": False,
            "exhaustive_survey_claimed": False,
            "similarity_score_claimed": False,
            "notes": [
                "Coverage is bounded to the indexed/queryable D02 typed export and cited audits in this repository/worktree.",
                "Categorical map units are never bilinearly interpolated; no crosswalk is invented where unjustified (mapped categories kept as evidence with original codes/descriptions).",
                "Detection/abundance/spectral index are separated: mineral_* are detection states (all unknown in this export), context_* are numeric spectral parameters (dimensionless_spectral_parameter, context-only), no abundance/wt% exported.",
                "Missing stays missing; no_coverage/unsupported/unknown/not_applicable are explicit and never zero-filled.",
                "All D02 records are evidence_only and excluded from similarity scoring and hard filters.",
            ],
        },
        "inputs": {
            "typed_manifest": "pipelines/geology/typed/manifest.json",
            "typed_manifest_sha256": sha("pipelines/geology/typed/manifest.json"),
            "typed_observations_sha256": manifest.get("observations_sha256"),
            "typed_observations_file_sha256": sha("pipelines/geology/typed/observations.jsonl"),
            "coverage_audit": "pipelines/geology/coverage-audit.json",
            "coverage_audit_sha256": sha("pipelines/geology/coverage-audit.json"),
            "source_audit": "docs/data-sources/d02-geology.json",
            "source_audit_sha256": sha("docs/data-sources/d02-geology.json"),
            "mineral_evidence": "pipelines/geology/mineral-evidence.json",
            "mineral_evidence_sha256": sha("pipelines/geology/mineral-evidence.json"),
            "area_fraction_evidence": "pipelines/geology/area-fraction/evidence.json",
            "area_fraction_evidence_sha256": sha("pipelines/geology/area-fraction/evidence.json"),
        },
        "body_area_index": {
            "Earth": sorted({o["area_id"] for o in obs if o["body"] == "Earth"}),
            "Moon": sorted({o["area_id"] for o in obs if o["body"] == "Moon"}),
            "Mars": sorted({o["area_id"] for o in obs if o["body"] == "Mars"}),
        },
        "rows": rows,
    }
    out = ROOT / "pipelines/geology/d02-coverage-matrix.json"
    out.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
