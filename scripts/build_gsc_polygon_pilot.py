#!/usr/bin/env python
"""Build true polygon area fractions from GSC Open File 5529."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from analog_pipelines.geology.gsc_polygon import (
    ZIP_BYTES,
    ZIP_SHA256,
    ZIP_URL,
    GscPolygonError,
    verify_artifact,
    write_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
AREAS = ROOT / "pipelines/geology/areas.json"
DEFAULT_CACHE = ROOT / "var/cache/gsc-world"
DEFAULT_OUTPUT = ROOT / "pipelines/geology/gsc-world/polygon"
DEFAULT_AREAS = ("earth-atacama-desert", "earth-lanzarote")
ARCHIVE_MEMBERS = (
    "data/ArcMap9_ArcReader9/AllThemesMerged/roxworld.shp",
    "data/ArcMap9_ArcReader9/AllThemesMerged/roxworld.shx",
    "data/ArcMap9_ArcReader9/AllThemesMerged/roxworld.dbf",
    "data/ArcMap9_ArcReader9/AllThemesMerged/roxworld.prj",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(cache: Path) -> Path:
    import urllib.request

    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "of_5529.zip"
    if archive.is_file() and archive.stat().st_size == ZIP_BYTES and _sha256(archive) == ZIP_SHA256:
        return archive
    print(f"downloading {ZIP_URL} ({ZIP_BYTES} bytes)")
    request = urllib.request.Request(ZIP_URL, headers={"User-Agent": "planetary-analog/1.0"})
    with urllib.request.urlopen(request, timeout=1800) as response, archive.open("wb") as handle:
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)
    if archive.stat().st_size != ZIP_BYTES:
        raise SystemExit(f"size mismatch: {archive.stat().st_size} != {ZIP_BYTES}")
    if _sha256(archive) != ZIP_SHA256:
        raise SystemExit("SHA-256 mismatch for GSC OF 5529 ZIP")
    return archive


def _extract(archive: Path, cache: Path) -> tuple[Path, Path]:
    target = cache / "extracted"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        for member in ARCHIVE_MEMBERS:
            if member not in names:
                raise SystemExit(f"missing archive member: {member}")
            destination = target / Path(member).name
            destination.write_bytes(bundle.read(member))
        dbf_dir = target / "dbf"
        dbf_dir.mkdir(exist_ok=True)
        for member in names:
            if member.startswith("data/DBase_Files/rudbf/") and member.endswith(".dbf"):
                (dbf_dir / Path(member).name).write_bytes(bundle.read(member))
    return target / "roxworld.shp", target / "dbf"


def _selected_areas(area_ids: tuple[str, ...]) -> list[dict]:
    payload = json.loads(AREAS.read_text(encoding="utf-8"))["areas"]
    by_id = {area["area_id"]: area for area in payload}
    selected = []
    for area_id in area_ids:
        area = by_id.get(area_id)
        if area is None or area["body"] != "Earth":
            raise SystemExit(f"unknown Earth AOI: {area_id}")
        selected.append(area)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, help="path to roxworld.shp")
    parser.add_argument("--dbf-dir", type=Path, help="path to GSC rudbf directory")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--download", action="store_true", help="download/verify the global ZIP")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--area", action="append", dest="areas", default=None)
    parser.add_argument("--generated-at", default="2026-11-15T00:00:00Z")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        try:
            summary = verify_artifact(args.output)
        except (OSError, KeyError, ValueError, GscPolygonError) as exc:
            print(f"ERROR: {exc}")
            return 2
        print(
            f"OK release={summary['release_version']} areas={summary['areas']} "
            f"observations={summary['observations']} categories={summary['by_category']} "
            f"coverage={summary['min_coverage']:.6f}..{summary['max_coverage']:.6f}"
        )
        return 0

    area_ids = tuple(args.areas or DEFAULT_AREAS)
    areas = _selected_areas(area_ids)
    if args.dry_run:
        print("source: GSC Open File 5529 roxworld polygons (1:35,000,000)")
        print("license: Open Government Licence - Canada 2.0")
        print(f"area(s): {', '.join(area_ids)}")
        print("method: true polygon intersection, explicit make_valid repair, local LAEA")
        print(f"output: {args.output}")
        return 0

    source = args.source
    dbf_dir = args.dbf_dir
    if source is None or dbf_dir is None:
        if not args.download and not (args.cache / "of_5529.zip").is_file():
            print("ERROR: provide --source/--dbf-dir or use --download")
            return 2
        archive = _download(args.cache) if args.download else args.cache / "of_5529.zip"
        source, dbf_dir = _extract(archive, args.cache)

    try:
        manifest = write_artifact(
            root=args.output,
            areas=areas,
            shapefile=source,
            dbf_dir=dbf_dir,
            generated_at=args.generated_at,
        )
    except (OSError, KeyError, ValueError, GscPolygonError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(
        f"release={manifest['release_version']} areas={manifest['counts']['areas']} "
        f"observations={manifest['counts']['observations']} "
        f"categories={manifest['counts']['by_category']} "
        f"coverage={manifest['counts']['min_coverage']:.6f}..{manifest['counts']['max_coverage']:.6f} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
