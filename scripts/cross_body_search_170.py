#!/usr/bin/env python3
"""Reproducible Moon/Mars D01 -> Earth candidate search (Issue #170, Goal G slice G2).

Run with the committed manifests and fixtures; no network access is required.

Examples
--------
    uv run python scripts/cross_body_search_170.py --body Moon --limit 5
    uv run python scripts/cross_body_search_170.py --body Mars --reference-id mars-jezero-ctx-window-01
    uv run python scripts/cross_body_search_170.py --body Moon \
        --weight slope_mean_deg=3 --weight roughness_std_m=1 --json

The script builds an offline SQLite index, loads the real fine Earth candidates and the
committed Moon/Mars D01 reference values, then emits weights, contributions, missing/
excluded candidates and provenance. It never turns a coarse 250 m planetary value into a
fine 90 m target and never claims a six-dimension cross-body score.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from analog_api.adapters.repository import SqlAlchemyRepository
from analog_api.adapters.seed import ensure_seeded, make_engine
from analog_api.adapters.typed_db import TypedObservationStore, init_typed_db
from analog_api.config import Settings
from analog_api.domain.cross_body_search import (
    CrossBodySearchError,
    TargetFeatureValue,
    execute_cross_body_search,
    target_from_reference,
)
from analog_api.domain.feature_service import assemble_snapshot
from analog_api.domain.multidim import DimensionControl
from sqlalchemy.orm import sessionmaker

DEFAULT_DATABASE = "var/cross-body-170.db"
DEFAULT_REFERENCES = {
    "Moon": "moon-lola-site01-roi1",
    "Mars": "mars-jezero-ctx-window-01",
}


def _parse_assignments(values: list[str], *, label: str) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for item in values:
        if "=" not in item:
            raise SystemExit(f"{label} must use FEATURE=VALUE syntax: {item!r}")
        key, raw = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"{label} has an empty feature id: {item!r}")
        try:
            parsed[key] = float(raw)
        except ValueError as exc:
            raise SystemExit(f"{label} value is not numeric: {item!r}") from exc
    return parsed


def _parse_dimension_overrides(
    weights: list[str], disabled: list[str]
) -> dict[str, DimensionControl]:
    controls: dict[str, DimensionControl] = {}
    for dimension_id, value in _parse_assignments(weights, label="--dimension-weight").items():
        controls[dimension_id] = DimensionControl(weight=value, enabled=True)
    for dimension_id in disabled:
        existing = controls.get(dimension_id, DimensionControl())
        controls[dimension_id] = DimensionControl(weight=existing.weight, enabled=False)
    return controls


def _load_snapshot(database: str):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{database}",
        manifest_dir="pipelines/earth/manifests",
        manifest_dir_planetary="pipelines/planetary/manifests",
        manifest_dir_context="pipelines/worldcover/manifests",
        seed_include_synthetic=False,
        seed_include_typed=False,
    )
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    ensure_seeded(engine, settings)
    init_typed_db(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    repo = SqlAlchemyRepository(session)
    typed = TypedObservationStore(session)
    snapshot = assemble_snapshot(
        rows=typed.list_observations(),
        areas=typed.list_areas(),
        candidates=repo.list_candidates(),
        references=repo.list_references(),
    )
    return snapshot, repo.data_version(), repo, session


def _with_overrides(target, overrides: dict[str, float]):
    values = dict(target.features)
    for feature_id, value in overrides.items():
        if feature_id not in values:
            raise SystemExit(
                f"--feature {feature_id} is not present on reference {target.source_id}; "
                "overrides only replace an existing D01 value"
            )
        current = values[feature_id]
        values[feature_id] = TargetFeatureValue(
            value=value,
            unit=current.unit,
            method_version=current.method_version,
            valid_fraction=current.valid_fraction,
            quality=current.quality,
        )
    return replace(target, features=values)


def _dataset_provenance(repo: SqlAlchemyRepository, dataset_ids: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for dataset_id in dataset_ids:
        dataset = repo.get_dataset(dataset_id)
        if dataset is None:
            result.append({"dataset_id": dataset_id, "available": False})
            continue
        result.append(
            {
                "dataset_id": dataset.dataset_id,
                "provider": dataset.provider,
                "product_id": dataset.product_id,
                "title": dataset.title,
                "url": dataset.url,
                "native_resolution_m": dataset.native_resolution_m,
                "processing_version": dataset.processing_version,
                "license": dataset.license,
                "available": True,
            }
        )
    return result


def _print_human(payload: dict[str, Any]) -> None:
    source = payload["source"]
    weights = payload["weights"]
    candidate = payload["candidate"]
    print(
        f"Cross-body D01 search: {source['body']} {source['source_id']} -> "
        f"Earth ({candidate['pool_size']} candidates, {candidate['eligible_count']} eligible)"
    )
    print(f"Claim scope: {payload['claim_scope']}")
    print("Target features (requested weight / normalised):")
    for feature_id, item in weights["feature_weights"].items():
        print(
            f"  - {feature_id}: value={item['value']:.6g} {item['unit']} "
            f"weight={item['weight']:.6g} normalised={item['normalized_weight']:.4g} "
            f"dim={item['dimension_id']} comparison={item['comparison']}"
        )
    for dimension_id, item in weights["dimension_weights"].items():
        print(
            f"  dimension {dimension_id}: weight={item['weight']} enabled={item['enabled']} "
            f"active={item['active']}"
        )
    rejected = payload["target_features"]["rejected"]
    print(f"Rejected target features: {len(rejected)}")
    for item in rejected:
        print(f"  - {item['feature_id']}: {item['reason']} ({item['message']})")
    print(f"Excluded Earth candidates: {payload['excluded_candidate_count']}")
    for item in payload["excluded"][:5]:
        print(f"  - {item['location_id']}: {item['reason']}")
    print("Top results:")
    for row in payload["results"]:
        print(f"  {row['rank']:>2}. {row['location_id']:<28} distance={row['distance']:.8f}")
    if payload["results"]:
        first = payload["results"][0]
        print("Top-1 contributions:")
        for item in first["contributions"]:
            print(
                f"  - {item['feature_id']}: target={item['target']:.6g} "
                f"candidate={item['candidate']:.6g} delta={item['delta']:.6g} "
                f"z={item['normalized_delta']:.6g} weight={item['weight']:.6g} "
                f"contribution={item['contribution']:.6g} "
                f"fraction={item['contribution_fraction']:.4f}"
            )
        for dimension_id, values in first["dimension_contributions"].items():
            print(
                f"  dimension {dimension_id}: contribution={values['contribution']:.6g} "
                f"q_d={values['mean_squared_normalized_delta']:.6g} "
                f"distance={values['distance']:.6g}"
            )
    print("Provenance:")
    print(f"  data_version={payload['data_version']} registry={payload['registry_version']}")
    print(f"  dataset_ids={source['dataset_ids']} source_kind={source['source_kind']}")
    print(
        f"  method_versions={sorted({item['method_version'] for item in weights['feature_weights'].values()})}"
    )
    print(f"  scoring_version={payload['scoring']['version']}")
    print(f"Limitations: {payload['limitations'][0]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body", choices=("Moon", "Mars"), default="Moon")
    parser.add_argument("--reference-id", default=None)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--feature", action="append", default=[], metavar="ID=VALUE")
    parser.add_argument("--weight", action="append", default=[], metavar="ID=VALUE")
    parser.add_argument("--dimension-weight", action="append", default=[], metavar="D01=VALUE")
    parser.add_argument("--disable-dimension", action="append", default=[], metavar="D01")
    parser.add_argument(
        "--scoring-version",
        choices=("dimension-weighted-rms-v1", "feature-weighted-euclidean-v1"),
        default="dimension-weighted-rms-v1",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="emit the full JSON payload only")
    parser.add_argument("--output", default=None, help="write the full JSON payload to this file")
    args = parser.parse_args(argv)

    reference_id = args.reference_id or DEFAULT_REFERENCES[args.body]
    feature_overrides = _parse_assignments(args.feature, label="--feature")
    weights = _parse_assignments(args.weight, label="--weight")
    dimension_weights = _parse_dimension_overrides(args.dimension_weight, args.disable_dimension)

    snapshot, data_version, repo, session = _load_snapshot(args.database)
    reference = repo.get_reference(reference_id)
    if reference is None:
        session.close()
        print(
            f"error: reference {reference_id!r} is not in this index. "
            f"Known references: {', '.join(item.reference_id for item in snapshot.references[:5])} ...",
            file=sys.stderr,
        )
        return 2
    if reference.body != args.body:
        session.close()
        print(
            f"error: reference {reference_id!r} is {reference.body}, not {args.body}.",
            file=sys.stderr,
        )
        return 2

    try:
        target = target_from_reference(reference)
        if feature_overrides:
            target = _with_overrides(target, feature_overrides)
        payload = execute_cross_body_search(
            snapshot=snapshot,
            target=target,
            feature_weights=weights or None,
            dimension_weights=dimension_weights or None,
            scoring_version=args.scoring_version,
            limit=args.limit,
            offset=args.offset,
            data_version=data_version,
        )
    except CrossBodySearchError as exc:
        session.close()
        error = {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        }
        print(json.dumps(error, indent=2))
        return 2

    payload["source"]["dataset_provenance"] = _dataset_provenance(
        repo, payload["source"]["dataset_ids"]
    )
    session.close()
    if args.output:
        Path(args.output).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_human(payload)
        print(f"\nFull JSON: {args.output or '(use --json or --output)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
