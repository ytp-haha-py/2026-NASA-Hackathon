"""Configurable local data paths; explicit CLI paths take precedence over defaults.

Reuse the operator .env loader. Never create directories merely by resolving paths.
Environment variables win over .env; ANALOG_SKIP_DOTENV=1 isolates offline tests.
"""

from __future__ import annotations

import os
from pathlib import Path

from analog_pipelines.storage import load_local_env

REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULTS = {
    "raw": "data/raw",
    "cache": "data/cache",
    "processed": "data/processed",
    "tmp": "data/tmp",
}


def data_dir(kind: str) -> Path:
    if kind not in _DEFAULTS:
        raise ValueError(f"Unknown data directory kind: {kind}")
    load_local_env(REPO_ROOT / ".env")
    value = os.environ.get(f"ANALOG_{kind.upper()}_DIR", _DEFAULTS[kind])
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def cache_dir(provider: str) -> Path:
    if not provider or Path(provider).name != provider or provider in {".", ".."}:
        raise ValueError("Provider must be a single directory name")
    return data_dir("cache") / provider
