#!/usr/bin/env python
"""Add two bounded Mars CTX reference windows outside the original five-window transect.

The source is the same official USGS M20 Jezero CTX 20 m DEM used by
``pipelines/planetary/ctx_import.py``. Raw raster remains outside the repository.

    uv run python scripts/build_ctx_expansion.py \
      --source /tmp/opencode/ctx/M20_JezeroCrater_CTXDEM_20m.tif \
      --output pipelines/planetary/manifests/ctx_mars_references_expansion_01.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from analog_api.domain.manifest import Manifest

ROOT = Path(__file__).resolve().parents[1]
CTX_IMPORT = ROOT / "pipelines/planetary/ctx_import.py"
CENTRES = (
    (4_340_000.0, 1_055_000.0),
    (4_405_000.0, 1_135_000.0),
)


def _load_ctx_import():
    spec = importlib.util.spec_from_file_location("ctx_import", CTX_IMPORT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {CTX_IMPORT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(source: Path, output: Path) -> dict:
    module = _load_ctx_import()
    doc = module.build(source, centres=CENTRES)
    for offset, ref in enumerate(doc["references"], start=6):
        ref["reference_id"] = f"mars-jezero-ctx-window-{offset:02d}"
        ref["display_name"] = f"Jezero CTX expansion window {offset:02d}"
    doc["generated_by"] = "scripts/build_ctx_expansion.py"
    Manifest.model_validate(doc)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("/tmp/opencode/ctx/M20_JezeroCrater_CTXDEM_20m.tif")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "pipelines/planetary/manifests/ctx_mars_references_expansion_01.json",
    )
    args = parser.parse_args()
    doc = build(args.source, args.output)
    print(json.dumps({"output": str(args.output), "references": len(doc["references"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
