"""True polygon area fractions from GSC Open File 5529 rock-unit polygons.

This is the acceptance-grade companion to the bounded WMS point-grid pilot in
``gsc_world.py``.  It reads the original ``roxworld`` shapefile from the GSC
Open File 5529 ZIP, intersects real polygons with the existing AOIs, repairs
invalid source geometries explicitly, projects to a local equal-area LAEA CRS,
and joins raw ``RUNO`` attributes (``RUCODE``, ``RXTP``, ages) from the shipped
dBase tables.

The global ZIP is downloaded only to a gitignored cache; the repository keeps
the two-AOI clipped evidence and checksums, never the global dataset.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from analog_pipelines.geology.models import Area
from analog_pipelines.geology.spatial import VectorIndex

SOURCE_ID = "gsc-of-5529-world-geology"
DATASET_ID = "gsc-of-5529-world-geology"
METHOD_VERSION = "gsc-world-polygon-laea-1.0.0"
RELEASE_VERSION = "gsc-world-polygon-pilot-1.0.0"
MAP_SCALE = "1:35,000,000"
LICENSE = "Open Government Licence - Canada 2.0"
LICENSE_URL = "https://open.canada.ca/en/open-government-licence-canada"
SOURCE_URL = "https://doi.org/10.4095/223767"
ZIP_URL = (
    "https://ostr-backend-prod.azure.cloud.nrcan-rncan.gc.ca/server/api/core/"
    "bitstreams/a2141be1-5afb-41c7-9588-a135734c0f8a/content"
)
ZIP_BYTES = 577276750
ZIP_SHA256 = "7ff615c7b076ac07c58e38c541cbbc2ec2353d7f889cbc000788f4c12ea4b89e"
SOURCE_CRS = "EPSG:4322"
ACCEPTANCE_STATUS = "polygon_area_fraction_validated"
POLYGON_EXTRACT_STATUS = "obtained"

RXTP_CATEGORY: dict[str, tuple[str, ...]] = {
    "SEDIMENT": ("sedimentary",),
    "VOLCANIC": ("igneous",),
    "INTRUSIVE": ("igneous",),
    "SEDVOL": ("sedimentary", "igneous"),
    # CRYSTALLINE is a texture, ICE/TECTONIC are not process categories.
}

FORBIDDEN_KEYS = {
    "abundance",
    "mineral_abundance",
    "geology_score",
    "combined_score",
    "similarity",
}


class GscPolygonError(RuntimeError):
    """Raised when the polygon pilot cannot be built or verified."""


def categories_for_rxtp(values: Iterable[str]) -> list[str]:
    categories: set[str] = set()
    for value in values:
        categories.update(RXTP_CATEGORY.get(value.strip().upper(), ()))
    return sorted(categories)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_runo_attributes(dbf_dir: Path, runos: set[int]) -> dict[int, dict[str, Any]]:
    """Join RUNO to rock-unit code, rock types and age terms from shipped DBFs."""
    try:
        from dbfread import DBF
    except ImportError as exc:  # pragma: no cover - optional GIS dependency
        raise GscPolygonError(
            "dbfread is required; run with pipelines/geology/requirements-ingest.txt"
        ) from exc

    attributes: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"rucodes": set(), "rxtps": set(), "start_ages": set(), "end_ages": set()}
    )
    for name in ("rudt.dbf", "rutypedt.dbf", "ruagedt.dbf"):
        path = dbf_dir / name
        if not path.is_file():
            raise GscPolygonError(f"missing dBase table: {path}")
        for row in DBF(path, encoding="latin-1", load=False):
            runo = row.get("RUNO")
            if runo not in runos:
                continue
            entry = attributes[int(runo)]
            if name == "rudt.dbf" and row.get("RUCODE"):
                entry["rucodes"].add(str(row["RUCODE"]).strip())
            elif name == "rutypedt.dbf" and row.get("RXTP"):
                entry["rxtps"].add(str(row["RXTP"]).strip().upper())
            elif name == "ruagedt.dbf":
                for key, field in (("start_ages", "STARTAGE"), ("end_ages", "ENDAGE")):
                    value = row.get(field)
                    if value:
                        entry[key].add(str(value).strip())
    return {
        runo: {
            "rucodes": sorted(entry["rucodes"]),
            "rxtps": sorted(entry["rxtps"]),
            "start_ages": sorted(entry["start_ages"]),
            "end_ages": sorted(entry["end_ages"]),
        }
        for runo, entry in attributes.items()
    }


def _area(raw: Mapping[str, Any]) -> Area:
    return Area(
        area_id=str(raw["area_id"]),
        display_name=str(raw.get("display_name") or raw["area_id"]),
        body=str(raw["body"]),
        crs=str(raw["crs"]),
        geometry=raw["geometry"],
        origin=str(raw.get("origin") or "existing"),
        geometry_source=str(raw.get("geometry_source") or "pipelines/geology/areas.json"),
        limitations=list(raw.get("limitations", [])),
    )


def measure_polygons(
    *,
    areas: Sequence[Mapping[str, Any]],
    shapefile: Path,
    dbf_dir: Path,
    repair_invalid: bool = True,
) -> list[dict[str, Any]]:
    if not shapefile.is_file():
        raise GscPolygonError(f"shapefile not found: {shapefile}")
    index = VectorIndex(shapefile, repair_invalid=repair_invalid)
    records: list[dict[str, Any]] = []
    for raw in areas:
        area = _area(raw)
        result = index.measure(area)
        grouped: dict[int, dict[str, Any]] = {}
        for row in result.rows:
            runo = int(row.properties.get("RUNO"))
            entry = grouped.setdefault(
                runo,
                {
                    "group": row.properties.get("GROUP"),
                    "fraction": 0.0,
                    "area_m2": 0.0,
                    "polygon_count": 0,
                },
            )
            entry["fraction"] += row.fraction
            entry["area_m2"] += row.area_m2
            entry["polygon_count"] += 1
        attributes = load_runo_attributes(dbf_dir, set(grouped))
        if not grouped:
            records.append(
                {
                    "area_id": area.area_id,
                    "body": "Earth",
                    "state": "no_coverage",
                    "coverage_fraction": result.coverage,
                    "coverage_state": "no_coverage",
                    "fraction": None,
                    "acceptance_status": ACCEPTANCE_STATUS,
                }
            )
            continue
        for runo, entry in sorted(grouped.items()):
            source = attributes.get(runo, {})
            rxtps = source.get("rxtps", [])
            categories = categories_for_rxtp(rxtps)
            qa = ["map_scale_1_35m_coarser_than_aoi", "true_polygon_intersection"]
            if not categories:
                qa.append("unmapped_rxtp")
            if result.repaired_count:
                qa.append(f"repaired_source_geometries:{result.repaired_count}")
            records.append(
                {
                    "area_id": area.area_id,
                    "body": "Earth",
                    "source_id": SOURCE_ID,
                    "dataset_id": DATASET_ID,
                    "product": (
                        "Generalized Geology of the World: Bedrock Domains and "
                        "Major Faults in GIS Format"
                    ),
                    "version": "Open File 5529 (2007), approximately 1:35,000,000",
                    "license": LICENSE,
                    "license_url": LICENSE_URL,
                    "map_scale": MAP_SCALE,
                    "source_crs": SOURCE_CRS,
                    "metric_crs": result.metric_crs,
                    "method_version": METHOD_VERSION,
                    "value_kind": "category",
                    "state": "detected",
                    "score_role": "evidence_only",
                    "raw": {
                        "group": entry["group"],
                        "runo": runo,
                        "rucodes": source.get("rucodes", []),
                        "rxtps": rxtps,
                        "start_ages": source.get("start_ages", []),
                        "end_ages": source.get("end_ages", []),
                        "polygon_count": entry["polygon_count"],
                    },
                    "mapped_categories": categories,
                    "fraction": min(1.0, entry["fraction"]),
                    "area_m2": entry["area_m2"],
                    "aoi_area_m2": result.aoi_area_m2,
                    "coverage_fraction": result.coverage,
                    "coverage_state": "complete" if result.coverage >= 0.999999 else "partial",
                    "invalid_source_geometries": result.invalid_count,
                    "repaired_source_geometries": result.repaired_count,
                    "repair_mode": result.repair_mode,
                    "fraction_basis": "polygon_intersection_equal_area_laea",
                    "acceptance_status": ACCEPTANCE_STATUS,
                    "qa_flags": qa,
                    "limitations": limitations(),
                }
            )
    return records


def limitations() -> list[str]:
    return [
        "GSC Open File 5529 is generalized bedrock at approximately 1:35,000,000, far coarser than a 1.98 km AOI.",
        "Area fractions are true polygon intersections in a local equal-area LAEA CRS, but map-unit boundaries are generalized.",
        "Map units are evidence only; no abundance, aqueous-alteration or geology similarity is produced.",
        "Only two bounded Earth AOIs are included; the global dataset is cached outside Git.",
    ]


def assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise GscPolygonError(f"forbidden geology field: {key}")
            assert_no_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_forbidden_keys(item)


def write_artifact(
    *,
    root: Path,
    areas: Sequence[Mapping[str, Any]],
    shapefile: Path,
    dbf_dir: Path,
    generated_at: str,
    zip_sha256: str = ZIP_SHA256,
    zip_bytes: int = ZIP_BYTES,
) -> dict[str, Any]:
    records = measure_polygons(areas=areas, shapefile=shapefile, dbf_dir=dbf_dir)
    root.mkdir(parents=True, exist_ok=True)
    evidence = "".join(
        json.dumps(record, sort_keys=True) + "\n"
        for record in sorted(records, key=lambda row: (row["area_id"], str(row.get("raw") or "")))
    )
    (root / "evidence.jsonl").write_text(evidence, encoding="utf-8")
    registry = {
        "registry_version": "gsc-world-polygon-registry-1.0.0",
        "source_id": SOURCE_ID,
        "non_exclusive": True,
        "mapping": [
            {
                "raw_rxtp": raw,
                "categories": list(categories),
                "evidence_basis": "official GSC RXTP attribute",
            }
            for raw, categories in sorted(RXTP_CATEGORY.items())
        ],
        "notes": [
            "CRYSTALLINE is a texture, not metamorphic process evidence.",
            "SEDVOL maps to both sedimentary and igneous; labels are non-exclusive.",
            "No code is mapped to aqueous alteration.",
        ],
    }
    (root / "registry.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "sources.json").write_text(
        json.dumps(
            [
                {
                    "source_id": SOURCE_ID,
                    "dataset_id": DATASET_ID,
                    "provider": "Geological Survey of Canada / Natural Resources Canada",
                    "product": (
                        "Generalized Geology of the World: Bedrock Domains and "
                        "Major Faults in GIS Format"
                    ),
                    "version": "Open File 5529 (2007), approximately 1:35,000,000",
                    "url": SOURCE_URL,
                    "zip_url": ZIP_URL,
                    "zip_bytes": zip_bytes,
                    "zip_sha256": zip_sha256,
                    "license": LICENSE,
                    "license_url": LICENSE_URL,
                    "native_crs": "GCS_WGS_1972 (EPSG:4322)",
                    "map_scale": MAP_SCALE,
                    "access": "public Open Access ZIP; bounded AOI clip committed, global ZIP not committed",
                    "redistribution_allowed": "yes",
                }
            ],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    by_category: Counter[str] = Counter()
    for record in records:
        by_category.update(record.get("mapped_categories") or [])
    manifest = {
        "release_version": RELEASE_VERSION,
        "generated_at": generated_at,
        "aois": [area["area_id"] for area in areas],
        "counts": {
            "areas": len({record["area_id"] for record in records}),
            "observations": len(records),
            "by_body": {"Earth": len(records)},
            "by_category": dict(sorted(by_category.items())),
            "detected": sum(record["state"] == "detected" for record in records),
            "no_coverage": sum(record["state"] == "no_coverage" for record in records),
            "min_coverage": min(
                (record.get("coverage_fraction") or 0 for record in records), default=0
            ),
            "max_coverage": max(
                (record.get("coverage_fraction") or 0 for record in records), default=0
            ),
        },
        "acceptance": {
            "status": ACCEPTANCE_STATUS,
            "polygon_extract": POLYGON_EXTRACT_STATUS,
            "native_crs": "GCS_WGS_1972 (EPSG:4322)",
            "metric_crs": "local LAEA per AOI",
            "validity_repair": "invalid source geometries repaired with shapely make_valid and counted",
            "fraction_basis": "true polygon intersection",
            "source_zip_url": ZIP_URL,
            "source_zip_bytes": zip_bytes,
            "source_zip_sha256": zip_sha256,
        },
        "checksums": {
            "evidence_sha256": sha256_file(root / "evidence.jsonl"),
            "registry_sha256": sha256_file(root / "registry.json"),
            "sources_sha256": sha256_file(root / "sources.json"),
        },
        "method_version": METHOD_VERSION,
        "limitations": limitations(),
        "non_claims": [
            "No mineral abundance.",
            "No aqueous-alteration inference.",
            "No geology similarity or combined six-dimension score.",
            "No global or exhaustive lithology coverage.",
        ],
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def verify_artifact(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    acceptance = manifest.get("acceptance") or {}
    if acceptance.get("status") != ACCEPTANCE_STATUS:
        raise GscPolygonError("artifact is not marked polygon-validated")
    if acceptance.get("polygon_extract") != POLYGON_EXTRACT_STATUS:
        raise GscPolygonError("polygon extract was not obtained")
    for filename, key in (
        ("evidence.jsonl", "evidence_sha256"),
        ("registry.json", "registry_sha256"),
        ("sources.json", "sources_sha256"),
    ):
        if sha256_file(root / filename) != manifest["checksums"][key]:
            raise GscPolygonError(f"{filename}: checksum mismatch")
    records = [
        json.loads(line)
        for line in (root / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(records) != manifest["counts"]["observations"]:
        raise GscPolygonError("observation count mismatch")
    for record in records:
        if record.get("acceptance_status") != ACCEPTANCE_STATUS:
            raise GscPolygonError("record not marked polygon-validated")
        if record["state"] == "detected":
            if not record.get("raw") or not record["raw"].get("rxtps"):
                raise GscPolygonError("detected record missing raw RXTP")
            fraction = record.get("fraction")
            if not isinstance(fraction, (int, float)) or not 0 <= fraction <= 1.0000001:
                raise GscPolygonError("invalid fraction")
    assert_no_forbidden_keys(records)
    return {
        "release_version": manifest["release_version"],
        "areas": manifest["counts"]["areas"],
        "observations": manifest["counts"]["observations"],
        "by_category": manifest["counts"]["by_category"],
        "min_coverage": manifest["counts"]["min_coverage"],
        "max_coverage": manifest["counts"]["max_coverage"],
    }
