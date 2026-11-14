from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """Runtime configuration. Every value has a safe local default."""

    model_config = SettingsConfigDict(env_prefix="ANALOG_", env_file=".env", extra="ignore")

    app_name: str = "Planetary Analog Explorer API"
    database_url: str = "sqlite+pysqlite:///./var/analog.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    manifest_dir: str = "pipelines/earth/manifests"
    manifest_dir_planetary: str = "pipelines/planetary/manifests"
    manifest_dir_context: str = "pipelines/worldcover/manifests"
    seed_include_synthetic: bool = False
    auto_seed: bool = True
    aoi_inline: bool = False
    aoi_workers: int = 2
    global_index_dir: str = "data/global-index"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sqlite_path(self) -> Path | None:
        """Return the filesystem path when using a file-backed SQLite URL."""
        prefix = "sqlite+pysqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix) :]
            if raw and raw != ":memory:":
                return Path(raw).expanduser()
        return None


def resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path
