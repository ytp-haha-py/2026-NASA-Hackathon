"""Bounded Earth lithology evidence from GSC Open File 5529.

The source is the Geological Survey of Canada *Generalized Geology of the World*
(Chorlton, 2007, Open File 5529, ~1:35,000,000).  It is exposed as a public WMS
by the USGS Mineral Resources Program.  This module queries a small point grid
inside an existing AOI and records the raw GSC attributes plus an explicitly
sampled area fraction.  It never computes a geology similarity score, never
converts a rock type into an abundance, and never claims aqueous alteration.

The WMS point grid is deliberately not described as polygon intersection: at
1:35,000,000 the source polygons are much coarser than a 1.98 km AOI, so the
fraction is a bounded sample estimate, flagged as such in every record.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

WMS_URL = "https://mrdata.usgs.gov/services/gscworld"
LAYER = "geology"
SOURCE_ID = "gsc-of-5529-world-geology"
DATASET_ID = "gsc-of-5529-world-geology"
METHOD_VERSION = "gsc-world-wms-point-grid-1.0.0"
RELEASE_VERSION = "gsc-world-lithology-pilot-1.0.0"
MAP_SCALE = "1:35,000,000"
LICENSE = "Open Government Licence - Canada 2.0"
LICENSE_URL = "https://open.canada.ca/en/open-government-licence-canada"
SOURCE_URL = "https://doi.org/10.4095/223767"
SERVICE_URL = "https://mrdata.usgs.gov/geology/gscworld/"

SOURCE = {
    "source_id": SOURCE_ID,
    "dataset_id": DATASET_ID,
    "provider": "Geological Survey of Canada / Natural Resources Canada; WMS by USGS Mineral Resources Program",
    "product": "Generalized Geology of the World: Bedrock Domains and Major Faults in GIS Format",
    "version": "Open File 5529 (2007), approximately 1:35,000,000",
    "url": SOURCE_URL,
    "service_url": SERVICE_URL,
    "wms_url": WMS_URL,
    "license": LICENSE,
    "license_url": LICENSE_URL,
    "native_crs": "EPSG:4326",
    "map_scale": MAP_SCALE,
    "access": "public WMS, no credentials",
    "redistribution_allowed": "yes",
}

CATEGORY_BY_RXTYp: dict[str, tuple[str, ...]] = {
    "sedimentary rocks": ("sedimentary",),
    "volcanic rocks": ("igneous",),
    "igneous rocks": ("igneous",),
    "crystalline metamorphic rocks": ("metamorphic",),
    "metamorphic rocks": ("metamorphic",),
}

FORBIDDEN_KEYS = {
    "abundance",
    "mineral_abundance",
    "geology_score",
    "combined_score",
    "similarity",
}


class GscWorldError(RuntimeError):
    """Raised when the bounded WMS pilot cannot be built or verified."""


def parse_feature_info(text: str) -> dict[str, str] | None:
    """Parse the MapServer text/plain GetFeatureInfo response.

    A response with no polygon at the sampled point is a real gap, not an
    error, and returns ``None``.
    """
    if "Search returned no results" in text:
        return None
    match = re.search(r"Feature\s+\d+\s*:\s*(.*)", text, re.S)
    if not match:
        return None
    attributes: dict[str, str] = {}
    for line in match.group(1).splitlines():
        item = re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*'([^']*)'\s*$", line)
        if item:
            attributes[item.group(1)] = item.group(2)
    return attributes or None


def mapped_categories(rxtyp: str | None) -> list[str]:
    """Map only documented GSC ``rxtyp`` values to broad, non-exclusive labels."""
    if not rxtyp:
        return []
    return list(CATEGORY_BY_RXTYp.get(rxtyp.strip().lower(), ()))


def _wms_url(bbox: Mapping[str, float], i: int, j: int, side: int) -> str:
    # WMS 1.3.0 with EPSG:4326 uses latitude,longitude axis order.
    params = {
        "service": "WMS",
        "version": "1.3.0",
        "request": "GetFeatureInfo",
        "layers": LAYER,
        "query_layers": LAYER,
        "crs": "EPSG:4326",
        "bbox": ",".join(
            str(value) for value in (bbox["south"], bbox["west"], bbox["north"], bbox["east"])
        ),
        "width": str(side),
        "height": str(side),
        "i": str(i),
        "j": str(j),
        "info_format": "text/plain",
    }
    return f"{WMS_URL}?{urllib.parse.urlencode(params)}"


def query_point(
    bbox: Mapping[str, float],
    i: int,
    j: int,
    *,
    side: int = 5,
    timeout: float = 45.0,
    retries: int = 3,
) -> dict[str, str] | None:
    """Query one point-grid cell; return raw GSC attributes or ``None``."""
    url = _wms_url(bbox, i, j, side)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "planetary-analog/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
            return parse_feature_info(text)
        except Exception as exc:  # noqa: BLE001 - bounded provider boundary
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(0.5 * (attempt + 1))
    raise GscWorldError(f"GSC WMS query failed after {retries} attempts: {last_error}")


QueryFunction = Callable[[Mapping[str, float], int, int], dict[str, str] | None]


def measure_bbox(
    *,
    area_id: str,
    bbox: Mapping[str, float],
    side: int = 5,
    query: QueryFunction | None = None,
) -> list[dict[str, Any]]:
    """Measure map-unit fractions over a bounded point grid inside one AOI.

    ``fraction`` is the share of all sample points assigned to a raw unit.  A
    no-result sample is a real coverage gap and lowers coverage; it is never
    zero-filled or hidden.
    """
    if side < 1:
        raise ValueError("side must be at least 1")
    query = query or (lambda box, i, j: query_point(box, i, j, side=side))
    samples: list[dict[str, str] | None] = []
    for j in range(side):
        for i in range(side):
            samples.append(query(bbox, i, j))
    total = len(samples)
    covered = [sample for sample in samples if sample is not None]
    keyed: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for sample in covered:
        key = (
            sample.get("gid", ""),
            sample.get("cover_id", ""),
            sample.get("runo_id2", ""),
        )
        keyed.setdefault(key, []).append(sample)
    if not covered:
        return [
            {
                "area_id": area_id,
                "body": "Earth",
                "source_id": SOURCE_ID,
                "dataset_id": DATASET_ID,
                "product": SOURCE["product"],
                "version": SOURCE["version"],
                "license": LICENSE,
                "license_url": LICENSE_URL,
                "map_scale": MAP_SCALE,
                "source_crs": "EPSG:4326",
                "method_version": METHOD_VERSION,
                "value_kind": "category",
                "state": "no_coverage",
                "score_role": "evidence_only",
                "raw": None,
                "mapped_categories": [],
                "fraction": None,
                "coverage_fraction": 0.0,
                "covered_samples": 0,
                "sample_count": total,
                "fraction_basis": f"wms_point_grid_{side}x{side}",
                "coverage_state": "no_coverage",
                "qa_flags": ["no_mapped_unit_at_sample_points"],
                "limitations": limitations(),
            }
        ]
    records: list[dict[str, Any]] = []
    for (gid, cover_id, runo_id2), entries in sorted(keyed.items()):
        sample = entries[0]
        rxtyp = sample.get("rxtyp")
        categories = mapped_categories(rxtyp)
        fraction = len(entries) / total
        coverage_state = "complete" if len(covered) == total else "partial"
        qa = [
            "map_scale_1_35m_coarser_than_aoi",
            "point_grid_not_polygon_intersection",
        ]
        if not categories:
            qa.append("unmapped_rxtyp")
        records.append(
            {
                "area_id": area_id,
                "body": "Earth",
                "source_id": SOURCE_ID,
                "dataset_id": DATASET_ID,
                "product": SOURCE["product"],
                "version": SOURCE["version"],
                "license": LICENSE,
                "license_url": LICENSE_URL,
                "map_scale": MAP_SCALE,
                "source_crs": "EPSG:4326",
                "method_version": METHOD_VERSION,
                "value_kind": "category",
                "state": "detected",
                "score_role": "evidence_only",
                "raw": {
                    "gid": gid,
                    "cover_id": cover_id,
                    "runo_id2": runo_id2,
                    "agerxtp": sample.get("agerxtp"),
                    "eon": sample.get("eon"),
                    "era": sample.get("era"),
                    "rxtyp": rxtyp,
                },
                "mapped_categories": categories,
                "fraction": fraction,
                "coverage_fraction": len(covered) / total,
                "covered_samples": len(covered),
                "sample_count": total,
                "fraction_basis": f"wms_point_grid_{side}x{side}",
                "coverage_state": coverage_state,
                "qa_flags": qa,
                "limitations": limitations(),
            }
        )
    return records


def limitations() -> list[str]:
    return [
        "GSC Open File 5529 is generalized bedrock at approximately 1:35,000,000, far coarser than a 1.98 km AOI.",
        "Fractions are from a bounded WMS point grid, not polygon area intersection.",
        "Map units are evidence only; no abundance, aqueous-alteration inference or geology similarity is produced.",
        "The WMS service is a USGS rendering of the GSC data; the Excel/attribute database and original GIS ZIP were not downloaded.",
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise GscWorldError(f"forbidden geology field: {key}")
            assert_no_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_forbidden_keys(item)


def verify_artifact(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    checksums = manifest.get("checksums") or {}
    for name in ("evidence.jsonl", "registry.json", "sources.json"):
        expected = checksums.get(f"{name.removesuffix('.jsonl').removesuffix('.json')}_sha256")
        if expected is None:
            continue
        actual = sha256_file(root / name)
        if actual != expected:
            raise GscWorldError(f"{name}: checksum mismatch")
    records = [
        json.loads(line)
        for line in (root / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(records) != int(manifest["counts"]["observations"]):
        raise GscWorldError("observation count does not match evidence.jsonl")
    if len({record["area_id"] for record in records}) != int(manifest["counts"]["areas"]):
        raise GscWorldError("area count does not match evidence.jsonl")
    for record in records:
        if record["state"] not in {"detected", "no_coverage"}:
            raise GscWorldError(f"unexpected state: {record['state']}")
        if record["state"] == "detected" and not record.get("raw"):
            raise GscWorldError("detected record is missing raw GSC attributes")
        if record["fraction"] is not None and not math.isfinite(float(record["fraction"])):
            raise GscWorldError("fraction must be finite or null")
    assert_no_forbidden_keys(records)
    return {
        "release_version": manifest["release_version"],
        "areas": manifest["counts"]["areas"],
        "observations": manifest["counts"]["observations"],
        "by_category": manifest["counts"].get("by_category", {}),
    }


def write_artifact(
    *,
    root: Path,
    areas: Sequence[Mapping[str, Any]],
    generated_at: str,
    side: int = 5,
    query: QueryFunction | None = None,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for area in areas:
        area_id = str(area["area_id"])
        bbox = area["bbox"]
        try:
            rows.extend(measure_bbox(area_id=area_id, bbox=bbox, side=side, query=query))
        except GscWorldError as exc:
            failures.append({"area_id": area_id, "error": str(exc)})
    if failures:
        raise GscWorldError(f"bounded WMS failures: {failures}")
    rows.sort(key=lambda row: (row["area_id"], row["raw"]["gid"] if row.get("raw") else ""))
    evidence = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    (root / "evidence.jsonl").write_text(evidence, encoding="utf-8")
    registry = {
        "registry_version": "gsc-world-lithology-registry-1.0.0",
        "source_id": SOURCE_ID,
        "non_exclusive": True,
        "mapping": [
            {
                "raw_rxtyp": raw,
                "categories": list(categories),
                "evidence_basis": "official GSC rxtyp attribute",
            }
            for raw, categories in sorted(CATEGORY_BY_RXTYp.items())
        ],
        "notes": [
            "Only documented rxtyp strings are mapped; unknown values stay unmapped.",
            "No code is mapped to aqueous alteration.",
            "Labels are non-exclusive and are not a similarity score.",
        ],
    }
    (root / "registry.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "sources.json").write_text(
        json.dumps([SOURCE], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    by_category: Counter[str] = Counter()
    for row in rows:
        by_category.update(row.get("mapped_categories") or [])
    manifest = {
        "release_version": RELEASE_VERSION,
        "generated_at": generated_at,
        "source": SOURCE,
        "aois": [area["area_id"] for area in areas],
        "sample_grid": f"{side}x{side}",
        "counts": {
            "areas": len({row["area_id"] for row in rows}),
            "observations": len(rows),
            "by_body": {"Earth": len(rows)},
            "by_category": dict(sorted(by_category.items())),
            "detected": sum(row["state"] == "detected" for row in rows),
            "no_coverage": sum(row["state"] == "no_coverage" for row in rows),
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
