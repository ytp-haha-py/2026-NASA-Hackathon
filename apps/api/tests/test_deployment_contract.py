"""Deployment contract for committed planetary AOI fixtures (Issue #132)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "apps/api/Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
PLANETARY_COARSE = ROOT / "pipelines/planetary/coarse"


def test_api_image_copies_committed_planetary_fixtures():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY samples/planetary ./samples/planetary" in text
    assert "COPY --from=builder /app/samples/planetary /app/samples/planetary" in text
    ignore = DOCKERIGNORE.read_text(encoding="utf-8")
    assert "!samples/planetary/coarse/fixtures/*.tif" in ignore


def test_every_planetary_manifest_fixture_is_inside_packaged_samples_tree():
    manifests = sorted(PLANETARY_COARSE.glob("*.json"))
    assert manifests
    checked = 0
    for manifest_path in manifests:
        if manifest_path.name == "index.json":
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for cell in manifest.get("cells", []):
            fixture = Path(cell["fixture"]["path"])
            assert fixture.parts[:2] == ("samples", "planetary"), (manifest_path, fixture)
            assert (ROOT / fixture).is_file(), (manifest_path, fixture)
            checked += 1
    assert checked > 0
