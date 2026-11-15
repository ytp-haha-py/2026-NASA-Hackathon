#!/usr/bin/env python
"""Build D02 representative query evidence from the committed typed export.

Reads pipelines/geology/typed/observations.jsonl only (offline, no network).
Outputs pipelines/geology/representative-queries.json.
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    typed = ROOT / "pipelines/geology/typed"
    obs = [
        json.loads(line)
        for line in (typed / "observations.jsonl").read_text().splitlines()
        if line.strip()
    ]

    def find(body=None, feature_id=None, prefix=None, area_id=None):
        out = []
        for o in obs:
            if body and o["body"] != body:
                continue
            if feature_id and o["feature_id"] != feature_id:
                continue
            if prefix and not o["feature_id"].startswith(prefix):
                continue
            if area_id and o["area_id"] != area_id:
                continue
            item = {
                "observation_id": o["observation_id"],
                "area_id": o["area_id"],
                "body": o["body"],
                "feature_id": o["feature_id"],
                "state": o.get("state"),
                "value_kind": o.get("value_kind"),
                "unit": o.get("unit"),
                "dataset_ids": o.get("dataset_ids"),
                "score_role": o.get("score_role"),
                "quality_flags": o.get("quality_flags"),
                "notes": o.get("notes"),
            }
            v = o.get("value")
            if isinstance(v, dict):
                item["value_summary"] = {
                    k: v.get(k)
                    for k in [
                        "code",
                        "name",
                        "overlap_fraction",
                        "scheme",
                        "process_code",
                        "description",
                    ]
                    if k in v
                }
                if "unified_rocks" in v:
                    item["value_summary"]["unified_rocks"] = v.get("unified_rocks")
            else:
                item["value_summary"] = v
            out.append(item)
        return out

    queries = [
        {
            "query_id": "Q-EARTH-availability",
            "body": "Earth",
            "description": "Representative query: Earth D02 geology/mineral analysis availability records (evidence-only).",
            "filters": {"body": "Earth", "feature_id": "geology_analysis_availability"},
            "results": find(body="Earth", feature_id="geology_analysis_availability"),
        },
        {
            "query_id": "Q-EARTH-macrostrat-units",
            "body": "Earth",
            "description": "Representative query: Macrostrat point-query map units for three Earth fine candidates (evidence-only, no crosswalk).",
            "filters": {"body": "Earth", "feature_id": "geologic_unit"},
            "results": find(body="Earth", feature_id="geologic_unit"),
        },
        {
            "query_id": "Q-MOON-mapped-units",
            "body": "Moon",
            "description": "Representative query: Moon mapped geologic unit/process evidence in indexed Shackleton ROI.",
            "filters": {"body": "Moon", "feature_id_in": ["geologic_unit", "geologic_process"]},
            "results": find(body="Moon", feature_id="geologic_unit")
            + find(body="Moon", feature_id="geologic_process"),
        },
        {
            "query_id": "Q-MOON-mineral-availability-blocker",
            "body": "Moon",
            "description": "Representative query: Moon mineral/analysis availability record showing blocked/unverified state (no detection claim).",
            "filters": {"body": "Moon", "feature_id": "geology_analysis_availability"},
            "results": find(body="Moon", feature_id="geology_analysis_availability"),
        },
        {
            "query_id": "Q-MARS-jezero-mapped-units",
            "body": "Mars",
            "description": "Representative query: Mars mapped geologic units/processes for bounded Jezero test AOI (floor).",
            "filters": {
                "body": "Mars",
                "feature_id_in": ["geologic_unit", "geologic_process"],
                "area_id": "mars-test-jezero-floor",
            },
            "results": [
                r
                for r in (
                    find(body="Mars", feature_id="geologic_unit")
                    + find(body="Mars", feature_id="geologic_process")
                )
                if r["area_id"] == "mars-test-jezero-floor"
            ],
        },
        {
            "query_id": "Q-MARS-crism-mineral-detections",
            "body": "Mars",
            "description": "Representative query: Mars mineral detection records from CRISM context product (all unknown, not not_detected, no abundance).",
            "filters": {"body": "Mars", "feature_id_prefix": "mineral_"},
            "results": find(body="Mars", prefix="mineral_"),
        },
        {
            "query_id": "Q-MARS-crism-spectral-context",
            "body": "Mars",
            "description": "Representative query: Mars CRISM spectral parameter context values (dimensionless, evidence-only context).",
            "filters": {
                "body": "Mars",
                "feature_id_prefix": "context_",
                "area_id": "mars-test-jezero-western-delta",
            },
            "results": find(
                body="Mars", prefix="context_", area_id="mars-test-jezero-western-delta"
            ),
        },
    ]
    for q in queries:
        q["result_count"] = len(q["results"])
        q["states"] = sorted({r.get("state") for r in q["results"]})

    out = {
        "artifact": "d02-representative-queries",
        "version": "1.0.0",
        "dimension": "D02",
        "scope": "bounded_indexed_typed_export_evidence_only",
        "global_claim": False,
        "notes": [
            "Queries run over the committed bounded typed D02 export (pipelines/geology/typed/observations.jsonl), not a global database scan.",
            "Results are evidence-only; no similarity ranking, no hard filtering role, no abundance claims.",
            "Earth/Moon/Mars each have representative query results where indexed data exists for this dimension pilot.",
        ],
        "source": {
            "typed_observations": "pipelines/geology/typed/observations.jsonl",
            "typed_manifest": "pipelines/geology/typed/manifest.json",
        },
        "queries": queries,
    }
    path = ROOT / "pipelines/geology/representative-queries.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(
        f"wrote {path} queries={len(queries)} "
        + " ".join(f"{q['query_id']}={q['result_count']}" for q in queries)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
