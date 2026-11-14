#!/usr/bin/env python
"""Export additive v2 typed contracts to committed JSON Schema files."""

from __future__ import annotations

import json
from pathlib import Path

from analog_pipelines.typed_contracts import (
    AreaV2,
    DatasetV2,
    ProcessingRunV2,
    SnapshotMetadataV2,
    SourceAssetV2,
    TypedObservation,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "contracts" / "v2"
MODELS = {
    "area": AreaV2,
    "dataset": DatasetV2,
    "source-asset": SourceAssetV2,
    "typed-observation": TypedObservation,
    "processing-run": ProcessingRunV2,
    "snapshot-metadata": SnapshotMetadataV2,
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        (OUT / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(f"Wrote {len(MODELS)} schemas to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
