"""Bounded, resumable source cache and checksummed, relocatable offline snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from analog_pipelines.geology.models import Artifact, Manifest, canonical, digest


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload)
    os.replace(temporary, path)


def checksum(path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"{algorithm}:{h.hexdigest()}"


def artifact(path: Path, url: str, role: str, expected: str | None = None) -> Artifact:
    if expected and checksum(path, expected.split(":")[0]) != expected:
        raise ValueError(f"provider checksum mismatch: {path.name}")
    return Artifact(
        role=role,
        source_url=url,
        checksum=checksum(path),
        size_bytes=path.stat().st_size,
        provider_checksum=expected,
    )


class Cache:
    def __init__(self, root: Path, offline: bool = False):
        self.root = root.resolve()
        self.offline = offline
        self.root.mkdir(parents=True, exist_ok=True)

    def fetch(
        self,
        url: str,
        name: str | None = None,
        expected: str | None = None,
        authenticated: bool = False,
    ) -> Path:
        if not url.startswith("https://"):
            raise ValueError("sources must use HTTPS")
        name = name or hashlib.sha256(url.encode()).hexdigest() + ".json"
        if Path(name).name != name:
            raise ValueError("cache name must be a basename")
        target = self.root / name
        if target.exists():
            if expected and checksum(target, expected.split(":")[0]) != expected:
                raise ValueError(f"corrupt cached file: {name}; retained for inspection")
            return target
        if self.offline:
            raise FileNotFoundError(f"offline cache miss: {name}")
        partial = target.with_suffix(target.suffix + ".part")
        command = [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "2",
            "--connect-timeout",
            "15",
            "--max-time",
            "300",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--continue-at",
            "-",
            "--output",
            str(partial),
        ]
        if authenticated:
            # curl scopes netrc credentials to hosts; no credentials in logs/manifests.
            command += ["--netrc"]
        command.append(url)
        completed = subprocess.run(command, capture_output=True, timeout=940, check=False)
        if completed.returncode:
            # Do not include server bodies, redirects, signed URLs or credentials.
            raise RuntimeError(f"source download failed (curl exit {completed.returncode}): {name}")
        if expected and checksum(partial, expected.split(":")[0]) != expected:
            raise ValueError(f"download checksum mismatch: {name}")
        os.replace(partial, target)
        return target

    def json(self, url: str, name: str | None = None) -> tuple[dict, Artifact]:
        path = self.fetch(url, name)
        return json.loads(path.read_text()), artifact(path, url, "source_metadata")

    def unzip(self, archive: Path, directory: str) -> Path:
        destination = self.root / directory
        marker = destination / ".geology-extracted.json"
        identity = checksum(archive)
        if marker.exists() and json.loads(marker.read_text())["checksum"] == identity:
            return destination
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                path = (destination / info.filename).resolve()
                if not path.is_relative_to(destination.resolve()):
                    raise ValueError("archive path escapes cache")
                # Retain complete GIS database + metadata; skip supplied terrain rasters.
                if "/Rasters/" in info.filename or "/__MACOSX/" in "/" + info.filename:
                    continue
                z.extract(info, destination)
        write_json(marker, {"checksum": identity})
        return destination


def build_snapshot(root: Path, files: list[str]) -> dict:
    entries = []
    for name in sorted(files):
        path = safe_path(root, name)
        entries.append(
            {"path": name, "checksum": checksum(path), "size_bytes": path.stat().st_size}
        )
    identity = {"snapshot_version": "geology-snapshot-1.0.0", "files": entries}
    snapshot_id = "geo-snap-" + digest(identity).split(":")[1][:16]
    return {
        **identity,
        "snapshot_id": snapshot_id,
        "object_prefix": f"geology/snapshots/{snapshot_id}/",
        "created_at": datetime.now(UTC).isoformat(),
        "limitations": [
            "Independent geology subset; no production pointer or terrain snapshot writes."
        ],
    }


def safe_path(root: Path, name: str) -> Path:
    if "\\" in name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("snapshot paths must be relative without traversal")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("snapshot path escapes root")
    return path


def verify_snapshot(root: Path, snapshot: dict) -> Manifest:
    if snapshot["snapshot_version"] != "geology-snapshot-1.0.0":
        raise ValueError("unsupported snapshot version")
    rebuilt = build_snapshot(root, [e["path"] for e in snapshot["files"]])
    for field in ("snapshot_id", "files", "object_prefix"):
        if canonical(rebuilt[field]) != canonical(snapshot[field]):
            raise ValueError(f"snapshot {field} checksum/identity mismatch")
    manifests = [e["path"] for e in snapshot["files"] if e["path"].startswith("records/")]
    if len(manifests) != 1:
        raise ValueError("snapshot must pin exactly one geology manifest")
    manifest = Manifest.model_validate_json(safe_path(root, manifests[0]).read_text())
    if manifest.data_kind != "source_derived":
        raise ValueError("portable snapshot must not promote synthetic fixtures")
    return manifest
