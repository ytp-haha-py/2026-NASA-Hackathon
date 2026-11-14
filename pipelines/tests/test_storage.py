from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from analog_pipelines.storage import (
    DEFAULT_LOCAL_ROOT,
    InvalidObjectKey,
    LocalObjectStore,
    ObjectNotFound,
    ObjectStoreNotConfigured,
    normalize_key,
    store_from_env,
)


def test_local_store_roundtrip_and_checksum(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "object-store")
    key = "snapshots/snap-example/artifacts/summary.json"
    payload = b'{"hello":"world"}\n'

    assert store.put_bytes(key, payload, content_type="application/json") == key
    assert store.exists(key) is True
    assert store.get_bytes(key) == payload
    expected = hashlib.sha256(payload).hexdigest()
    assert store.checksum(key) == expected
    sidecar = store.root / f"{key}.sha256"
    assert sidecar.read_text(encoding="utf-8").strip() == expected
    assert store.describe()["backend"] == "local"

    manifest = store.manifest("snapshots")
    assert manifest["files"] == [{"key": key, "sha256": expected, "bytes": len(payload)}]


def test_local_store_atomic_overwrite(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    store.put_bytes("a/b.bin", b"first")
    store.put_bytes("a/b.bin", b"second")
    assert store.get_bytes("a/b.bin") == b"second"
    assert store.checksum("a/b.bin") == hashlib.sha256(b"second").hexdigest()
    assert not list(store.root.rglob("*.tmp"))


def test_local_store_self_ignores_artifacts(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    store.put_bytes("one.json", b"{}")
    ignore = (store.root / ".gitignore").read_text(encoding="utf-8")
    assert "*" in ignore
    assert "!.gitignore" in ignore


def test_local_store_missing_key(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    with pytest.raises(ObjectNotFound):
        store.get_bytes("missing.json")
    assert store.exists("missing.json") is False


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../escape.json",
        "/etc/passwd",
        "a/../../b.json",
        "a//b.json",
        "./a.json",
        "a/./b.json",
        "a/",
        "a\\b.json",
        "https://example.invalid/object.json",
    ],
)
def test_key_traversal_and_unsafe_keys_rejected(tmp_path: Path, key: str) -> None:
    store = LocalObjectStore(tmp_path)
    with pytest.raises(InvalidObjectKey):
        store.put_bytes(key, b"nope")
    with pytest.raises(InvalidObjectKey):
        normalize_key(key)
    assert not list(tmp_path.rglob("*.json"))


def test_store_from_env_defaults_to_local_without_creating_directories() -> None:
    store = store_from_env({})
    assert isinstance(store, LocalObjectStore)
    assert Path(store.root) == Path(DEFAULT_LOCAL_ROOT)
    assert store.describe()["backend"] == "local"


def test_store_from_env_honours_explicit_local_root(tmp_path: Path) -> None:
    store = store_from_env(
        {
            "ANALOG_OBJECT_STORE_BACKEND": "local",
            "ANALOG_OBJECT_STORE_ROOT": str(tmp_path / "local-store"),
        }
    )
    assert isinstance(store, LocalObjectStore)
    assert store.root == tmp_path / "local-store"


def test_s3_without_credentials_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ANALOG_OBJECT_STORE_ENDPOINT",
        "ANALOG_OBJECT_STORE_BUCKET",
        "ANALOG_OBJECT_STORE_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ObjectStoreNotConfigured) as excinfo:
        store_from_env({"ANALOG_OBJECT_STORE_BACKEND": "s3"})
    message = str(excinfo.value)
    assert "ANALOG_OBJECT_STORE_ENDPOINT" in message
    assert "AWS_ACCESS_KEY_ID" in message
    assert "prompted" in message


def test_s3_incomplete_config_lists_missing_variables() -> None:
    with pytest.raises(ObjectStoreNotConfigured) as excinfo:
        store_from_env(
            {
                "ANALOG_OBJECT_STORE_ENDPOINT": "https://example.invalid",
                "ANALOG_OBJECT_STORE_BUCKET": "example-bucket",
            }
        )
    message = str(excinfo.value)
    assert "ANALOG_OBJECT_STORE_REGION" in message
    assert "AWS_SECRET_ACCESS_KEY" in message


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ObjectStoreNotConfigured, match="unknown ANALOG_OBJECT_STORE_BACKEND"):
        store_from_env({"ANALOG_OBJECT_STORE_BACKEND": "ftp"})
