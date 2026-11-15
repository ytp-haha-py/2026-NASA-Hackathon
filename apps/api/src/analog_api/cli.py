from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session

from analog_api.adapters.aoi_store import persist_aoi_result
from analog_api.adapters.db import Candidate, Dataset, PlanetaryReference
from analog_api.adapters.earth_aoi import EarthAoiProvider
from analog_api.adapters.seed import (
    ManifestError,
    discover_manifests,
    ensure_seeded,
    make_engine,
    seed_manifests,
    seed_typed_bundles,
)
from analog_api.config import Settings
from analog_api.domain.aoi import validate_aoi_request
from analog_api.domain.errors import AppError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m analog_api.cli",
        description="Seed and inspect the Planetary Analog Explorer index.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", help="Load pipeline manifests into the database")
    seed.add_argument("--database-url", default=None)
    seed.add_argument(
        "--include-synthetic",
        action="store_true",
        help="Include the committed TEST-ONLY synthetic fixture (loudly labelled in the API).",
    )
    seed.add_argument(
        "--include-typed",
        action="store_true",
        help="Import committed typed-v2 pilot releases and the D02 geology bundle.",
    )
    seed.add_argument(
        "--keep-existing",
        action="store_true",
        help="Upsert without clearing the existing index (default: reset for reproducibility).",
    )
    seed.add_argument("--manifest-dir", default=None)
    seed.add_argument("--manifest-dir-planetary", default=None)
    seed.add_argument("--manifest-dir-context", default=None)
    status = sub.add_parser("status", help="Print index counts and data version")
    status.add_argument("--database-url", default=None)
    aoi = sub.add_parser(
        "analyze-aoi",
        help="Analyze one bounded Earth bbox on demand and persist the derived observations",
    )
    aoi.add_argument("--west", type=float, required=True)
    aoi.add_argument("--south", type=float, required=True)
    aoi.add_argument("--east", type=float, required=True)
    aoi.add_argument("--north", type=float, required=True)
    aoi.add_argument("--feature", action="append", dest="features", default=None)
    aoi.add_argument("--database-url", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    if getattr(args, "manifest_dir", None):
        settings = settings.model_copy(update={"manifest_dir": args.manifest_dir})
    if getattr(args, "manifest_dir_planetary", None):
        settings = settings.model_copy(
            update={"manifest_dir_planetary": args.manifest_dir_planetary}
        )
    if getattr(args, "manifest_dir_context", None):
        settings = settings.model_copy(update={"manifest_dir_context": args.manifest_dir_context})
    if getattr(args, "include_synthetic", False):
        settings = settings.model_copy(update={"seed_include_synthetic": True})
    if getattr(args, "include_typed", False):
        settings = settings.model_copy(update={"seed_include_typed": True})

    if settings.sqlite_path is not None:
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)

    if args.command == "seed":
        try:
            manifests = discover_manifests(settings)
        except ManifestError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if not manifests:
            print(
                "No manifests found. Run the Earth/planetary pipeline first or pass "
                "--include-synthetic for demo data.",
                file=sys.stderr,
            )
            return 2
        version = seed_manifests(engine, manifests, reset=not args.keep_existing)
        typed = seed_typed_bundles(engine, settings) if settings.seed_include_typed else None
        with Session(engine) as session:
            candidates = session.query(Candidate).count()
            datasets = session.query(Dataset).count()
            references = session.query(PlanetaryReference).count()
        print(
            f"Seeded {len(manifests)} manifest(s). Index: {candidates} candidate(s), "
            f"{datasets} dataset(s), {references} reference(s). data_version={version}"
        )
        if typed is not None:
            print(
                f"Typed: {typed['typed_observations']} observation(s) from "
                f"{len(typed['sources'])} committed release(s)/bundle(s)"
            )
        return 0

    if args.command == "analyze-aoi":
        ensure_seeded(engine, settings)
        try:
            validated = validate_aoi_request(
                bbox={
                    "west": args.west,
                    "south": args.south,
                    "east": args.east,
                    "north": args.north,
                },
                geometry=None,
                requested_features=args.features,
            )
            result = EarthAoiProvider().analyze(validated)
        except AppError as exc:
            print(f"ERROR: {exc.code}: {exc.message}", file=sys.stderr)
            return 2
        with Session(engine) as session:
            version = persist_aoi_result(session, result)
        print(f"area_id={result.area_id} data_version={version}")
        print(
            "features="
            + ", ".join(
                f"{feature_id}={value:.6f}" for feature_id, value in result.features.items()
            )
        )
        print(
            f"context_observations={len(result.context_observations)} "
            f"provider_status={result.provider_status}"
        )
        return 0

    with Session(engine) as session:
        print(
            "candidates="
            f"{session.query(Candidate).count()} "
            f"datasets={session.query(Dataset).count()} "
            f"references={session.query(PlanetaryReference).count()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
