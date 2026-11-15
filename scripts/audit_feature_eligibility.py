#!/usr/bin/env python
"""Print the Issue #116 feature-eligibility audit from the local database.

The audit is derived from stored observations and the exact-ID join to fine candidates. It never
downloads data and never rewrites source records.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from analog_api.adapters.postgis import repository_for_session
from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.config import Settings
from analog_api.domain.eligibility import ROLE_HARD_FILTER, ROLE_SIMILARITY
from analog_api.domain.feature_service import assemble_snapshot
from analog_api.main import create_app


def _role_text(entry) -> str:
    roles = [
        name
        for name in (
            "similarity",
            "hard_filter",
            "category_match",
            "evidence",
            "uncertainty",
            "context",
        )
        if entry.roles[name].supported
    ]
    return ", ".join(roles)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default="sqlite+pysqlite:///./var/analog.db",
        help="Database to audit (default: ./var/analog.db).",
    )
    parser.add_argument(
        "--output", type=Path, help="Write Markdown to this path as well as stdout."
    )
    args = parser.parse_args()

    settings = Settings(database_url=args.database_url, seed_include_typed=True)
    app = create_app(settings)
    with app.state.session_factory() as session:
        repo = repository_for_session(session)
        store = TypedObservationStore(session)
        snapshot = assemble_snapshot(
            rows=store.list_observations(),
            areas=store.list_areas(),
            candidates=repo.list_candidates(),
            references=repo.list_references(),
        )
        data_version = repo.data_version()

    lines: list[str] = []
    lines.append("# Feature eligibility audit")
    lines.append("")
    lines.append(f"- registry_version: `{snapshot.registry and 'feature-eligibility-1.0.0'}`")
    lines.append(f"- data_version: `{data_version}`")
    lines.append(f"- typed_observations: {len(snapshot.rows)}")
    lines.append(f"- fine_candidates: {len(snapshot.candidates)}")
    lines.append("")
    join = snapshot.join
    lines.append("## Join audit")
    lines.append("")
    lines.append(f"- join key: `{join['join_key']}`")
    lines.append(f"- proximity join used: `{join['proximity_join_used']}`")
    lines.append(f"- typed areas: {join['typed_area_count']}")
    lines.append(
        "- typed areas exactly matching a fine candidate: "
        f"{join['typed_areas_exactly_matching_fine_candidate']} "
        f"({', '.join(join['matched_fine_candidate_ids'])})"
    )
    lines.append(
        "- typed areas exactly matching a planetary reference: "
        f"{join['typed_areas_exactly_matching_planetary_reference']} "
        f"({', '.join(join['matched_planetary_reference_ids'])})"
    )
    lines.append("")

    lines.append("## Role summary")
    lines.append("")
    lines.append(
        "| Dimension | Features | Similarity | Hard filter | Evidence | Unsupported for scoring |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|")
    summary: dict[str, dict[str, int]] = {}
    for entry in snapshot.registry.values():
        stats = summary.setdefault(
            entry.dimension_id,
            {
                "features": 0,
                "similarity": 0,
                "hard_filter": 0,
                "evidence": 0,
                "unsupported_for_scoring": 0,
            },
        )
        stats["features"] += 1
        stats["similarity"] += int(entry.roles[ROLE_SIMILARITY].supported)
        stats["hard_filter"] += int(entry.roles[ROLE_HARD_FILTER].supported)
        stats["evidence"] += int(entry.roles["evidence"].supported)
        stats["unsupported_for_scoring"] += int(not entry.roles[ROLE_SIMILARITY].supported)
    for dimension, stats in sorted(summary.items()):
        lines.append(
            f"| {dimension} | {stats['features']} | {stats['similarity']} | "
            f"{stats['hard_filter']} | {stats['evidence']} | {stats['unsupported_for_scoring']} |"
        )
    lines.append("")

    lines.append("## Feature table")
    lines.append("")
    lines.append(
        "| Feature | Dim | Bodies | Obs | Areas | Gated candidates | Legal roles | Unit | Selector | Unsupported reason |"
    )
    lines.append("|---|---|---|---:|---:|---:|---|---|---|---|")
    for feature_id in sorted(snapshot.registry):
        entry = snapshot.registry[feature_id]
        coverage = snapshot.coverage.get(feature_id)
        observed = coverage.observed if coverage else snapshot.observed.get(feature_id)
        if observed is None:
            obs = areas = gated = 0
            bodies = "-"
        else:
            obs = observed.observation_count
            areas = len(observed.area_ids)
            gated = len(coverage.gated_candidate_ids) if coverage else 0
            bodies = ", ".join(sorted(observed.bodies)) or "-"
        selector = entry.selector.get("depth_label") or entry.selector.get("kind")
        reason = (entry.unsupported_reason or "").replace("|", "/")
        lines.append(
            f"| `{feature_id}` | {entry.dimension_id} | {bodies} | {obs} | {areas} | "
            f"{gated} | {_role_text(entry)} | {entry.unit} | {selector} | {reason} |"
        )
    lines.append("")
    lines.append(
        "Gated candidates are exact-ID-joined, gate-passing values. They are not counts of "
        "six-dimension observations and not a global survey."
    )
    lines.append("")

    text = "\n".join(lines)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
