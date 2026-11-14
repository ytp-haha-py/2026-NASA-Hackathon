from pathlib import Path

import pytest
from analog_pipelines.paths import REPO_ROOT, cache_dir, data_dir


def test_configured_external_paths(monkeypatch, tmp_path):
    for kind in ("raw", "cache", "processed", "tmp"):
        path = tmp_path / kind
        monkeypatch.setenv(f"ANALOG_{kind.upper()}_DIR", str(path))
        assert data_dir(kind) == path
        assert not path.exists()  # Resolution is read-only.
    assert cache_dir("lola") == tmp_path / "cache" / "lola"


def test_relative_paths_resolve_from_project(monkeypatch):
    monkeypatch.setenv("ANALOG_CACHE_DIR", "scratch/cache")
    assert cache_dir("copernicus") == REPO_ROOT / "scratch/cache/copernicus"


def test_default_and_invalid_paths(monkeypatch):
    monkeypatch.delenv("ANALOG_CACHE_DIR", raising=False)
    assert data_dir("cache") == REPO_ROOT / "data/cache"
    with pytest.raises(ValueError):
        data_dir("credentials")
    with pytest.raises(ValueError):
        cache_dir("../outside")
    assert isinstance(data_dir("raw"), Path)
