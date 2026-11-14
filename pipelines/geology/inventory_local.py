"""Inventory existing Geology files without reading credentials or fetching assets.

Run from any directory: python pipelines/geology/inventory_local.py
    --data-root /external/geology --output /tmp/geology-inventory.json
Only paths, sizes and SHA-256 digests are recorded, never file contents.
"""

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    data = args.data_root.resolve(strict=True)
    if not data.is_dir() or data.is_relative_to(repo):
        parser.error("data-root must be an existing directory outside the repository")
    output = args.output.resolve()
    if output.is_relative_to(data):
        parser.error("output must be outside data-root so the inventory cannot include itself")
    portable_roots = [repo / "pipelines/geology", repo / "pipelines/src/analog_pipelines/geology"]
    portable = [p for root in portable_roots for p in root.rglob("*") if p.is_file()]
    portable.extend((repo / "pipelines/tests").glob("test_geology*.py"))
    portable = sorted(p for p in portable if "__pycache__" not in p.parts and p.suffix != ".pyc")
    external = sorted(p for p in data.rglob("*") if p.is_file())
    entries = []
    for path in external:
        relative = path.relative_to(data)
        kind = "raw_or_source_metadata"
        if relative.parts[0] == "prototype-layout-backup":
            kind = "superseded_prototype_output"
        elif path.name.startswith("glim-local-") or path.name == "analysis-checkpoint.json":
            kind = "local_derived_license_or_checkpoint_restricted"
        elif len(relative.parts) > 1:
            kind = "unpacked_source_or_auxiliary"
        entries.append(
            {
                "path": relative.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
                "kind": kind,
                "remote_status": "not_backed_up_by_this_handoff",
            }
        )
    payload = {
        "inventory_version": "1.0.0",
        "external_data_root_at_inventory": str(data),
        "portable_storage": "Git branch feat/41-geology-handoff; PR linked in docs/OPENCODE_HANDOFF.md",
        "provenance": "Portable records link dataset version/source URL and per-analysis processing method/artifact receipts; see catalog.json and records/.",
        "external_policy": "Not uploaded: raw datasets, unpacked sources, unverified-license GLiM values, checkpoints, and superseded prototype outputs. No unique shareable verified scientific result is external-only.",
        "portable_files": [
            {
                "path": p.relative_to(repo).as_posix(),
                "bytes": p.stat().st_size,
                "sha256": digest(p),
            }
            for p in portable
        ],
        "external_file_count": len(entries),
        "external_bytes": sum(entry["bytes"] for entry in entries),
        "external_files": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"Inventoried {len(portable)} portable and {len(entries)} external files; no uploads")


if __name__ == "__main__":
    main()
