#!/usr/bin/env python3
"""Recompute every committed terrain window before publishing an additive feature.

Reuse bounded provider caches. Stage/validate the complete revision before replacing
manifests. A failed source leaves all original manifests untouched. Runtime AOIs are
not reset; legacy AOIs retain their old observations and explicit missingness.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "pipelines/earth"), str(ROOT / "pipelines/planetary")]
from analog_api.domain.manifest import Manifest  # noqa: E402
from analog_pipelines.features import extract_features, make_observations  # noqa: E402
from analog_pipelines.paths import data_dir  # noqa: E402
from batch_ingest import candidates  # noqa: E402
from copernicus_glo90 import default_cache_dir as earth_cache  # noqa: E402
from copernicus_glo90 import extract_candidate, fetch_crop  # noqa: E402
from ctx_import import validate_source  # noqa: E402
from lola import LolaReferenceProvider  # noqa: E402
from lola import default_cache_dir as moon_cache  # noqa: E402


def projected_center(row):
    match = re.search(r"X=([-\d.]+) m.*?Y=([-\d.]+) m", row["notes"])
    if not match:
        raise ValueError("No authoritative source X/Y for " + row["reference_id"])
    return float(match[1]), float(match[2])


def main():
    import jsonschema
    import rasterio

    stage = data_dir("processed") / "terrain-flat-recompute"
    stage.mkdir(parents=True, exist_ok=True)
    earth_plan = {c.location_id: c for c in candidates()}
    schema = json.loads((ROOT / "contracts/pipeline-manifest.schema.json").read_text())
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    documents = {}
    rows_by_id = {}
    measurements = []
    try:
        for path in sorted((ROOT / "pipelines").glob("*/manifests/*.json")):
            document = json.loads(path.read_text())
            if any(d["source_kind"] == "landcover" for d in document["datasets"]):
                continue
            dataset_by_id = {d["dataset_id"]: d for d in document["datasets"]}
            for row in document["candidates"] + document["references"]:
                dataset = dataset_by_id[row["dataset_id"]]
                identifier = row.get("location_id", row.get("reference_id"))
                if "location_id" in row:
                    c = earth_plan[identifier]
                    crop = fetch_crop(c, earth_cache(), timeout=25, max_retries=1)
                    result = extract_candidate(c, crop)
                    dataset["access_timestamp"] = max(
                        dataset["access_timestamp"], crop.access_timestamp
                    )
                else:
                    center = projected_center(row)
                    if row["body"] == "Moon":
                        prepared = LolaReferenceProvider().prepare(
                            source=dataset["url"],
                            center_xy=center,
                            cache_dir=moon_cache(),
                            timeout=25,
                            max_retries=1,
                            max_window_bytes=30 * 1024 * 1024,
                        )
                        source = prepared.cache_path
                        dataset["access_timestamp"] = max(
                            dataset["access_timestamp"], prepared.access_timestamp
                        )
                    else:
                        source = data_dir("raw") / "mars" / "M20_JezeroCrater_CTXDEM_20m.tif"
                        with rasterio.open(source) as src:
                            validate_source(src)
                    result = extract_features(
                        str(source), center_xy=center, body=row["body"], source_label=dataset["url"]
                    )
                old = row["features"]
                delta = max(abs(result.features[k] - obs["value"]) for k, obs in old.items())
                row["features"] = make_observations(
                    result.features, valid_fraction=result.valid_fraction
                )
                row["valid_fraction"] = round(result.valid_fraction, 6)
                row["notes"] = re.sub(
                    r"window_checksum=sha256:[a-f0-9]{64}",
                    f"window_checksum={result.window_checksum}",
                    row["notes"],
                )
                row["notes"] += (
                    " Flat fraction: slope <5 degrees / valid complete Horn neighborhoods, same 90 m grid; outer rim excluded."
                )
                rows_by_id[identifier] = row
                measurements.append(
                    dict(
                        id=identifier,
                        status="verified",
                        flat=row["features"]["flat_terrain_fraction"]["value"],
                        max_previous_metric_delta=delta,
                    )
                )
                print(f"OK {identifier} flat={measurements[-1]['flat']:.4f}", flush=True)
            for dataset in document["datasets"]:
                dataset["processing_version"] = "terrain-flat-extension-1.0.0"
                if dataset["source_kind"] != "martian_dem":
                    dataset["checksum"] = (
                        "sha256:"
                        + hashlib.sha256(
                            json.dumps(
                                document["candidates"] + document["references"], sort_keys=True
                            ).encode()
                        ).hexdigest()
                    )
                    dataset["notes"] += (
                        " Updated checksum covers canonical derived records, including individual window checksums."
                    )
            document["generated_by"] = "pipelines/recompute_terrain.py"
            document["generated_at"] = now
            documents[path] = document
        for path in (ROOT / "pipelines/worldcover/manifests").glob("*.json"):
            doc = json.loads(path.read_text())
            for row in doc["candidates"]:
                row["features"] = rows_by_id[row["location_id"]]["features"]
            doc["generated_at"] = now
            documents[path] = doc
        for path, doc in documents.items():
            Manifest.model_validate(doc)
            jsonschema.validate(doc, schema)
            (stage / path.name).write_text(json.dumps(doc, indent=2) + "\n")
        for path in documents:
            path.write_text((stage / path.name).read_text())
        report = dict(status="verified", window_count=len(measurements), measurements=measurements)
        (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            f"Validated and promoted {len(documents)} manifests; {len(measurements)} terrain windows recalculated."
        )
    except Exception as exc:
        (stage / "report.json").write_text(
            json.dumps(dict(status="failed", error=str(exc), measurements=measurements), indent=2)
            + "\n"
        )
        print(f"FAILED; manifests were not promoted: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
