"""Partitioned Parquet coarse index + read-only DuckDB queries (Issues #33/#34).

Storage is local-only in this phase. DuckDB writes/reads Parquet; no pyarrow
dependency is required. The manifest is the pinned interface and is verified before
queries. R2 publication is intentionally not performed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from analog_pipelines.coarse_features import (
    COARSE_FEATURE_BY_ID,
    COARSE_FEATURES,
    COARSE_METHOD_VERSION,
    CoarseExtractionError,
)
from analog_pipelines.coarse_grid import GRID_VERSION

INDEX_VERSION = "coarse-index-1.0.0"
PARQUET_SCHEMA_VERSION = "coarse-parquet-1.0.0"
_FEATURE_COLUMNS = {
    "coarse_slope_mean_deg": "slope_mean_deg",
    "coarse_slope_p90_deg": "slope_p90_deg",
    "coarse_flat_fraction_lt5deg": "flat_fraction",
}
_COLUMNS = [
    ("candidate_id", "VARCHAR"),
    ("grid_id", "VARCHAR"),
    ("region_id", "VARCHAR"),
    ("body", "VARCHAR"),
    ("west", "DOUBLE"),
    ("south", "DOUBLE"),
    ("east", "DOUBLE"),
    ("north", "DOUBLE"),
    ("crs", "VARCHAR"),
    ("area_km2", "DOUBLE"),
    ("slope_mean_deg", "DOUBLE"),
    ("slope_p90_deg", "DOUBLE"),
    ("flat_fraction", "DOUBLE"),
    ("valid_fraction", "DOUBLE"),
    ("method_version", "VARCHAR"),
    ("grid_version", "VARCHAR"),
    ("dataset_id", "VARCHAR"),
    ("source_asset_id", "VARCHAR"),
    ("qa_flags", "VARCHAR"),
    ("snapshot_id", "VARCHAR"),
]


class IndexError_(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CoarseRow:
    candidate_id: str
    grid_id: str
    region_id: str
    body: str
    west: float
    south: float
    east: float
    north: float
    crs: str
    area_km2: float
    slope_mean_deg: float | None
    slope_p90_deg: float | None
    flat_fraction: float | None
    valid_fraction: float
    method_version: str
    grid_version: str
    dataset_id: str
    source_asset_id: str
    qa_flags: list[str] = field(default_factory=list)
    snapshot_id: str = ""

    def to_tuple(self) -> tuple[Any, ...]:
        data = asdict(self)
        data["qa_flags"] = json.dumps(sorted(self.qa_flags))
        return tuple(data[column] for column, _ in _COLUMNS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _write_partition(path: Path, rows: Sequence[CoarseRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, dir=path.parent, suffix=".parquet.tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    con = duckdb.connect()
    try:
        con.execute(
            f"CREATE TABLE coarse ({', '.join(f'{name} {dtype}' for name, dtype in _COLUMNS)})"
        )
        if rows:
            con.executemany(
                f"INSERT INTO coarse VALUES ({', '.join('?' for _ in _COLUMNS)})",
                [row.to_tuple() for row in rows],
            )
        escaped = str(tmp).replace("'", "''")
        con.execute(f"COPY coarse TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        duplicates = con.execute(
            f"SELECT candidate_id FROM read_parquet('{escaped}') "
            "GROUP BY candidate_id HAVING count(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicates:
            raise IndexError_(f"duplicate candidate_id in partition: {duplicates[0]}")
    finally:
        con.close()
    os.replace(tmp, path)


def build_index(
    rows: Iterable[CoarseRow],
    *,
    root: str | Path,
    snapshot_id: str,
    source_datasets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = list(rows)
    if not rows:
        raise IndexError_("cannot build an empty coarse index")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    by_region: dict[str, list[CoarseRow]] = {}
    for row in rows:
        if row.method_version != COARSE_METHOD_VERSION:
            raise IndexError_(f"unexpected coarse method_version {row.method_version!r}")
        by_region.setdefault(row.region_id, []).append(row)
    partitions: list[dict[str, Any]] = []
    for region_id in sorted(by_region):
        region_rows = sorted(by_region[region_id], key=lambda row: row.candidate_id)
        relative = Path("regions") / region_id / "part-00000.parquet"
        path = root / relative
        _write_partition(path, region_rows)
        partitions.append(
            {
                "region_id": region_id,
                "path": relative.as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "rows": len(region_rows),
            }
        )
    west = min(row.west for row in rows)
    south = min(row.south for row in rows)
    east = max(row.east for row in rows)
    north = max(row.north for row in rows)
    manifest = {
        "index_version": INDEX_VERSION,
        "parquet_schema_version": PARQUET_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(UTC).isoformat(),
        "grid_version": GRID_VERSION,
        "method_version": COARSE_METHOD_VERSION,
        "row_count": len(rows),
        "partitions": partitions,
        "coverage": {
            "bbox": {"west": west, "south": south, "east": east, "north": north},
            "regions": sorted(by_region),
            "label": "local_coarse_index_not_global_exhaustive",
        },
        "features": [feature.to_payload() for feature in COARSE_FEATURES],
        "source_datasets": source_datasets or [],
    }
    _atomic_json(root / "manifest.json", manifest)
    return manifest


def verify_index(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise IndexError_(f"coarse index manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("index_version") != INDEX_VERSION:
        raise IndexError_("unsupported coarse index version")
    rows = 0
    for partition in manifest.get("partitions", []):
        path = root / partition["path"]
        if not path.is_file():
            raise IndexError_(f"missing partition {path}")
        if _sha256(path) != partition["sha256"]:
            raise IndexError_(f"partition checksum mismatch: {path}")
        rows += int(partition["rows"])
        con = duckdb.connect()
        try:
            count = con.execute(
                f"SELECT count(*), count(DISTINCT candidate_id) FROM read_parquet('{str(path).replace(chr(39), chr(39) * 2)}')"
            ).fetchone()
        finally:
            con.close()
        if count[0] != partition["rows"] or count[1] != count[0]:
            raise IndexError_(f"row/duplicate mismatch in {path}")
    if rows != manifest.get("row_count"):
        raise IndexError_("manifest row_count mismatch")
    return manifest


class CoarseIndexRepository:
    """Read-only DuckDB repository over a verified Parquet index."""

    def __init__(self, root: str | Path, *, snapshot_id: str | None = None) -> None:
        self.root = Path(root)
        self.manifest = verify_index(self.root)
        self.snapshot_id = snapshot_id or self.manifest["snapshot_id"]
        if self.snapshot_id != self.manifest["snapshot_id"]:
            raise IndexError_("requested snapshot does not match the pinned index manifest")
        self.parquet_paths = [str(self.root / p["path"]) for p in self.manifest["partitions"]]

    def _conn(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()
        escaped = ", ".join("'" + path.replace("'", "''") + "'" for path in self.parquet_paths)
        con.execute(f"CREATE VIEW coarse AS SELECT * FROM read_parquet([{escaped}])")
        return con

    def coverage(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "label": self.manifest["coverage"]["label"],
            "row_count": self.manifest["row_count"],
            "regions": self.manifest["coverage"]["regions"],
            "bbox": self.manifest["coverage"]["bbox"],
            "method_version": self.manifest["method_version"],
            "features": self.manifest["features"],
        }

    def get(self, candidate_id: str) -> dict[str, Any] | None:
        con = self._conn()
        try:
            row = con.execute(
                "SELECT * FROM coarse WHERE candidate_id = ? AND snapshot_id = ?",
                [candidate_id, self.snapshot_id],
            ).fetchone()
            columns = [item[0] for item in con.description]
        finally:
            con.close()
        if row is None:
            return None
        payload = dict(zip(columns, row, strict=True))
        payload["qa_flags"] = json.loads(payload["qa_flags"] or "[]")
        payload["bbox"] = {
            "west": payload["west"],
            "south": payload["south"],
            "east": payload["east"],
            "north": payload["north"],
        }
        return payload

    def search(
        self,
        *,
        target: dict[str, float],
        weights: dict[str, float],
        limit: int = 10,
        region_id: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 10:
            raise CoarseExtractionError("invalid_limit", "limit must be within 1..10")
        if not target:
            raise CoarseExtractionError("empty_target", "at least one coarse target is required")
        unknown = sorted(set(target) - set(COARSE_FEATURE_BY_ID))
        if unknown:
            raise CoarseExtractionError(
                "unknown_feature", f"unsupported coarse features: {unknown}"
            )
        total_weight = 0.0
        terms: list[str] = []
        params: list[Any] = []
        for feature_id, target_value in target.items():
            if feature_id not in _FEATURE_COLUMNS:
                raise CoarseExtractionError("unsupported_feature", feature_id)
            weight = float(weights.get(feature_id, 1.0))
            if weight < 0 or not (weight == weight) or weight == float("inf"):
                raise CoarseExtractionError("invalid_weight", "weights must be finite and >=0")
            total_weight += weight
            column = _FEATURE_COLUMNS[feature_id]
            scale = COARSE_FEATURE_BY_ID[feature_id].scale
            terms.append(f"{weight} * power(({column} - ?) / {scale}, 2)")
            params.append(float(target_value))
        if total_weight <= 0:
            raise CoarseExtractionError(
                "zero_total_weight", "at least one positive weight is required"
            )
        where = ["method_version = ?", "snapshot_id = ?"]
        where_params: list[Any] = [COARSE_METHOD_VERSION, self.snapshot_id]
        for feature_id in target:
            where.append(f"{_FEATURE_COLUMNS[feature_id]} IS NOT NULL")
        if region_id:
            where.append("region_id = ?")
            where_params.append(region_id)
        distance_sql = " + ".join(terms)
        sql = (
            f"SELECT candidate_id, grid_id, region_id, body, west, south, east, north, crs, "
            f"area_km2, slope_mean_deg, slope_p90_deg, flat_fraction, valid_fraction, "
            f"method_version, grid_version, dataset_id, source_asset_id, qa_flags, snapshot_id, "
            f"sqrt(({distance_sql}) / {total_weight}) AS distance "
            f"FROM coarse WHERE {' AND '.join(where)} "
            f"ORDER BY distance ASC, candidate_id ASC LIMIT ?"
        )
        con = self._conn()
        try:
            rows = con.execute(sql, [*params, *where_params, limit]).fetchall()
            columns = [item[0] for item in con.description]
            eligible = con.execute(
                f"SELECT count(*) FROM coarse WHERE {' AND '.join(where)}", where_params
            ).fetchone()[0]
            indexed = con.execute(
                "SELECT count(*) FROM coarse WHERE method_version = ? AND snapshot_id = ?",
                [COARSE_METHOD_VERSION, self.snapshot_id],
            ).fetchone()[0]
        finally:
            con.close()
        results = []
        for rank, row in enumerate(rows, start=1):
            payload = dict(zip(columns, row, strict=True))
            payload["qa_flags"] = json.loads(payload["qa_flags"] or "[]")
            payload["bbox"] = {
                "west": payload["west"],
                "south": payload["south"],
                "east": payload["east"],
                "north": payload["north"],
            }
            payload["rank"] = rank
            results.append(payload)
        return {
            "snapshot_id": self.snapshot_id,
            "method_version": COARSE_METHOD_VERSION,
            "indexed_candidate_count": indexed,
            "eligible_candidate_count": eligible,
            "excluded_candidate_count": indexed - eligible,
            "results": results,
        }
