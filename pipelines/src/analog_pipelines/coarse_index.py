"""Partitioned Parquet coarse index + read-only DuckDB queries (Issues #33/#34).

Storage is local-only in this phase. DuckDB writes/reads Parquet; no pyarrow
dependency is required. Snapshot paths are immutable: a snapshot id maps to its own
directory and an existing manifest is never overwritten.
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
SNAPSHOTS_DIR = "snapshots"
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
    ("window_size_m", "DOUBLE"),
    ("stride_m", "DOUBLE"),
    ("source_resolution_m", "DOUBLE"),
    ("slope_mean_deg", "DOUBLE"),
    ("slope_p90_deg", "DOUBLE"),
    ("flat_fraction", "DOUBLE"),
    ("valid_fraction", "DOUBLE"),
    ("method_version", "VARCHAR"),
    ("grid_version", "VARCHAR"),
    ("dataset_id", "VARCHAR"),
    ("source_asset_id", "VARCHAR"),
    ("qa_flags", "VARCHAR"),
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
    window_size_m: float = 5000.0
    stride_m: float = 5000.0
    source_resolution_m: float = 250.0

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


def _validate_row(row: CoarseRow) -> None:
    if not row.candidate_id or "/" in row.candidate_id or "\\" in row.candidate_id:
        raise IndexError_(f"invalid candidate_id {row.candidate_id!r}")
    if not row.region_id or "/" in row.region_id or "\\" in row.region_id:
        raise IndexError_(f"invalid region_id {row.region_id!r}")
    if row.body != "Earth":
        raise IndexError_("coarse index currently supports Earth candidates only")
    if row.method_version != COARSE_METHOD_VERSION:
        raise IndexError_(f"unexpected coarse method_version {row.method_version!r}")
    if row.grid_version != GRID_VERSION:
        raise IndexError_(f"unexpected grid_version {row.grid_version!r}")
    if row.east <= row.west or row.north <= row.south:
        raise IndexError_(f"invalid bbox for {row.candidate_id}")
    if not (0 <= row.valid_fraction <= 1):
        raise IndexError_(f"valid_fraction outside [0,1] for {row.candidate_id}")
    if row.area_km2 <= 0:
        raise IndexError_(f"area_km2 must be positive for {row.candidate_id}")
    for field_name in ("window_size_m", "stride_m", "source_resolution_m"):
        if getattr(row, field_name) <= 0:
            raise IndexError_(f"{field_name} must be positive for {row.candidate_id}")
    if row.stride_m < row.window_size_m:
        raise IndexError_("overlapping coarse windows are not supported")
    for feature_id, value in (
        ("coarse_slope_mean_deg", row.slope_mean_deg),
        ("coarse_slope_p90_deg", row.slope_p90_deg),
        ("coarse_flat_fraction_lt5deg", row.flat_fraction),
    ):
        if value is None:
            continue
        definition = COARSE_FEATURE_BY_ID[feature_id]
        if not definition.min_value <= value <= definition.max_value:
            raise IndexError_(f"{feature_id} value outside domain for {row.candidate_id}")


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


def _duplicate_across_files(paths: Sequence[Path]) -> str | None:
    if not paths:
        return None
    escaped = ", ".join("'" + str(path).replace("'", "''") + "'" for path in paths)
    con = duckdb.connect()
    try:
        row = con.execute(
            f"SELECT candidate_id FROM read_parquet([{escaped}]) "
            "GROUP BY candidate_id HAVING count(*) > 1 LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


class CoarseIndexWriter:
    """Incremental, partition-bounded, immutable snapshot writer."""

    def __init__(
        self,
        *,
        root: str | Path,
        snapshot_id: str,
        source_datasets: list[dict[str, Any]] | None = None,
        max_rows_per_partition: int = 10_000,
    ) -> None:
        if not snapshot_id or "/" in snapshot_id or "\\" in snapshot_id:
            raise IndexError_("snapshot_id must be a non-empty path-safe string")
        if max_rows_per_partition < 1:
            raise IndexError_("max_rows_per_partition must be positive")
        self.root = Path(root)
        self.snapshot_id = snapshot_id
        self.source_datasets = source_datasets or []
        self.max_rows_per_partition = max_rows_per_partition
        self.snapshot_root = self.root / SNAPSHOTS_DIR / snapshot_id
        self.partition_root = self.root / "partitions"
        if (self.snapshot_root / "manifest.json").is_file():
            raise IndexError_(
                f"snapshot {snapshot_id!r} already exists at {self.snapshot_root}; "
                "published snapshots are immutable; publish a new snapshot id instead"
            )
        self._buffers: dict[str, list[CoarseRow]] = {}
        self._part_counts: dict[str, int] = {}
        self.partitions: list[dict[str, Any]] = []
        self.row_count = 0
        self._west = float("inf")
        self._south = float("inf")
        self._east = float("-inf")
        self._north = float("-inf")

    def add_row(self, row: CoarseRow) -> None:
        _validate_row(row)
        buffer = self._buffers.setdefault(row.region_id, [])
        buffer.append(row)
        self.row_count += 1
        self._west = min(self._west, row.west)
        self._south = min(self._south, row.south)
        self._east = max(self._east, row.east)
        self._north = max(self._north, row.north)
        if len(buffer) >= self.max_rows_per_partition:
            self._flush(row.region_id)

    def add_rows(self, rows: Iterable[CoarseRow]) -> None:
        for row in rows:
            self.add_row(row)

    def _flush(self, region_id: str) -> None:
        rows = self._buffers.get(region_id) or []
        if not rows:
            return
        import tempfile

        self.partition_root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{region_id}-", suffix=".parquet.tmp", dir=self.partition_root
        )
        os.close(fd)
        temp_path = Path(tmp_name)
        try:
            _write_partition(temp_path, rows)
            digest = _sha256(temp_path)
            final = self.partition_root / region_id / f"{digest}.parquet"
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.is_file():
                if _sha256(final) != digest:
                    raise IndexError_(f"content-addressed partition collision: {final}")
                temp_path.unlink()
            else:
                os.replace(temp_path, final)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        self._part_counts[region_id] = self._part_counts.get(region_id, 0) + 1
        self.partitions.append(
            {
                "region_id": region_id,
                "path": Path("partitions").joinpath(region_id, f"{digest}.parquet").as_posix(),
                "sha256": digest,
                "bytes": final.stat().st_size,
                "rows": len(rows),
            }
        )
        self._buffers[region_id] = []

    def finalize(self) -> dict[str, Any]:
        for region_id in list(self._buffers):
            self._flush(region_id)
        if not self.partitions:
            raise IndexError_("cannot publish an empty coarse index")
        paths = [self.root / partition["path"] for partition in self.partitions]
        duplicate = _duplicate_across_files(paths)
        if duplicate:
            raise IndexError_(f"duplicate candidate_id across partitions: {duplicate}")
        manifest = {
            "index_version": INDEX_VERSION,
            "parquet_schema_version": PARQUET_SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "created_at": datetime.now(UTC).isoformat(),
            "grid_version": GRID_VERSION,
            "method_version": COARSE_METHOD_VERSION,
            "row_count": self.row_count,
            "partitions": self.partitions,
            "coverage": {
                "bbox": {
                    "west": self._west,
                    "south": self._south,
                    "east": self._east,
                    "north": self._north,
                },
                "regions": sorted(self._part_counts),
                "label": "local_coarse_index_not_global_exhaustive",
            },
            "features": [feature.to_payload() for feature in COARSE_FEATURES],
            "source_datasets": self.source_datasets,
            "snapshot_root": str(self.snapshot_root),
        }
        _atomic_json(self.snapshot_root / "manifest.json", manifest)
        return manifest


def build_index(
    rows: Iterable[CoarseRow],
    *,
    root: str | Path,
    snapshot_id: str,
    source_datasets: list[dict[str, Any]] | None = None,
    max_rows_per_partition: int = 10_000,
) -> dict[str, Any]:
    writer = CoarseIndexWriter(
        root=root,
        snapshot_id=snapshot_id,
        source_datasets=source_datasets,
        max_rows_per_partition=max_rows_per_partition,
    )
    writer.add_rows(rows)
    return writer.finalize()


def resolve_index_root(root: str | Path, *, snapshot_id: str | None = None) -> Path:
    root = Path(root)
    if (root / "manifest.json").is_file():
        return root
    snapshots = root / SNAPSHOTS_DIR
    if snapshot_id:
        candidate = snapshots / snapshot_id
        if (candidate / "manifest.json").is_file():
            return candidate
        raise IndexError_(f"snapshot {snapshot_id!r} not found under {snapshots}")
    candidates = [p for p in snapshots.glob("*") if (p / "manifest.json").is_file()]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise IndexError_(f"no coarse snapshot manifest found under {root}")
    raise IndexError_(f"multiple coarse snapshots found under {snapshots}; specify snapshot_id")


def _index_base(snapshot_root: Path, root: Path) -> Path:
    # New layout: <base>/snapshots/<id>/manifest.json, shared <base>/partitions/...
    if snapshot_root.parent.name == SNAPSHOTS_DIR:
        return snapshot_root.parent.parent
    return root


def verify_index(root: str | Path, *, snapshot_id: str | None = None) -> dict[str, Any]:
    resolved = resolve_index_root(root, snapshot_id=snapshot_id)
    base = _index_base(resolved, Path(root))
    manifest_path = resolved / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("index_version") != INDEX_VERSION:
        raise IndexError_("unsupported coarse index version")
    rows = 0
    for partition in manifest.get("partitions", []):
        path = base / partition["path"]
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

    def __init__(
        self,
        root: str | Path,
        *,
        snapshot_id: str | None = None,
        verify: bool = True,
    ) -> None:
        self.root = resolve_index_root(root, snapshot_id=snapshot_id)
        self.base = _index_base(self.root, Path(root))
        if verify:
            self.manifest = verify_index(self.root)
        else:
            self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.snapshot_id = snapshot_id or self.manifest["snapshot_id"]
        if self.snapshot_id != self.manifest["snapshot_id"]:
            raise IndexError_("requested snapshot does not match the pinned index manifest")
        self.parquet_paths = [str(self.base / p["path"]) for p in self.manifest["partitions"]]

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
            "verified_index": True,
        }

    def get(self, candidate_id: str) -> dict[str, Any] | None:
        con = self._conn()
        try:
            row = con.execute(
                "SELECT * FROM coarse WHERE candidate_id = ?",
                [candidate_id],
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
        positive: dict[str, float] = {}
        for feature_id in target:
            weight = float(weights.get(feature_id, 1.0))
            if not (weight == weight) or weight in {float("inf"), float("-inf")} or weight < 0:
                raise CoarseExtractionError("invalid_weight", "weights must be finite and >=0")
            if weight > 0:
                positive[feature_id] = weight
        if not positive:
            raise CoarseExtractionError(
                "zero_total_weight", "at least one positive weight is required"
            )
        total_weight = sum(positive.values())
        terms: list[str] = []
        params: list[Any] = []
        where = ["method_version = ?"]
        where_params: list[Any] = [COARSE_METHOD_VERSION]
        for feature_id, weight in positive.items():
            if feature_id not in _FEATURE_COLUMNS:
                raise CoarseExtractionError("unsupported_feature", feature_id)
            column = _FEATURE_COLUMNS[feature_id]
            scale = COARSE_FEATURE_BY_ID[feature_id].scale
            terms.append(f"{weight} * power(({column} - ?) / {scale}, 2)")
            params.append(float(target[feature_id]))
            where.append(f"{column} IS NOT NULL")
        if region_id:
            where.append("region_id = ?")
            where_params.append(region_id)
        distance_sql = " + ".join(terms)
        sql = (
            f"SELECT candidate_id, grid_id, region_id, body, west, south, east, north, crs, "
            f"area_km2, window_size_m, stride_m, source_resolution_m, "
            f"slope_mean_deg, slope_p90_deg, flat_fraction, valid_fraction, "
            f"method_version, grid_version, dataset_id, source_asset_id, qa_flags, "
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
                "SELECT count(*) FROM coarse WHERE method_version = ?",
                [COARSE_METHOD_VERSION],
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
