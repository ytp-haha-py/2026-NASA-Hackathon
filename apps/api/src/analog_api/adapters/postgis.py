"""Optional PostGIS candidate repository and bounded regional scan.

The adapter implements the existing ``CandidateRepositoryFull`` Protocol without
changing the API domain. It is deliberately optional: SQLite remains the default
runtime store, and nothing in this module claims that a PostgreSQL/PostGIS
deployment has been provisioned or verified in this repository.

PostGIS path
------------
Candidate footprints are currently stored as four WGS84 columns (``west``,
``south``, ``east``, ``north``) rather than a geometry column. PostgreSQL queries
therefore build an envelope from those columns and use ``ST_Intersects`` plus the
``&&`` bounding-box operator. Add the expression index in
``POSTGIS_BBOX_INDEX_DDL`` for large candidate tables.

SQLite path
-----------
``allow_sqlite_subset=True`` is a contract-test and local-development subset only.
It executes the equivalent half-open numeric bbox predicate so the same repository
methods can be compared against ``SqlAlchemyRepository``. It is not a spatial index
and must never be reported as a PostGIS verification.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from analog_api.adapters.db import Candidate, Dataset, Meta, PlanetaryReference
from analog_api.adapters.mapping import (
    candidate_entity,
    dataset_entity,
    reference_entity,
)
from analog_api.adapters.repository import SqlAlchemyRepository
from analog_api.domain.entities import BBox, CandidateEntity, DatasetEntity, ReferenceEntity
from analog_api.ports.repository import CandidateRepositoryFull

POSTGIS_DIALECT = "postgresql"
SQLITE_TEST_SUBSET = "sqlite"
DEFAULT_MAX_SCAN_AREA_DEG2 = 25.0
MAX_SCAN_LIMIT = 500

POSTGIS_BBOX_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS candidates_bbox_envelope_gix
ON candidates
USING gist (ST_MakeEnvelope(west, south, east, north, 4326));
""".strip()


class PostgisUnavailableError(RuntimeError):
    """Raised when an optional PostGIS deployment or driver is unavailable."""


@dataclass(frozen=True, slots=True)
class RegionalCandidateScan:
    """A bounded regional result set, never an exhaustive or global survey."""

    bbox: BBox
    candidates: tuple[CandidateEntity, ...]
    matched_count: int
    backend: str
    max_area_deg2: float
    scope: str = "regional_bbox_subset_not_global"
    global_coverage: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "bbox": self.bbox.as_dict(),
            "matched_count": self.matched_count,
            "returned_count": len(self.candidates),
            "backend": self.backend,
            "scope": self.scope,
            "global_coverage": self.global_coverage,
            "candidate_ids": [candidate.location_id for candidate in self.candidates],
        }


def _validate_bbox(bbox: BBox, max_area_deg2: float) -> None:
    if not (-180 <= bbox.west < bbox.east <= 180):
        raise ValueError("bbox must satisfy -180 <= west < east <= 180")
    if not (-90 <= bbox.south < bbox.north <= 90):
        raise ValueError("bbox must satisfy -90 <= south < north <= 90")
    area = (bbox.east - bbox.west) * (bbox.north - bbox.south)
    if area > max_area_deg2:
        raise ValueError(
            f"regional scan bbox is {area:.3f} square degrees; "
            f"the bounded maximum is {max_area_deg2:.3f}"
        )


def candidate_envelope() -> ColumnElement[object]:
    return func.ST_MakeEnvelope(
        Candidate.west,
        Candidate.south,
        Candidate.east,
        Candidate.north,
        4326,
    )


def query_envelope(bbox: BBox) -> ColumnElement[object]:
    return func.ST_MakeEnvelope(bbox.west, bbox.south, bbox.east, bbox.north, 4326)


def bbox_predicate(bbox: BBox, *, dialect_name: str = POSTGIS_DIALECT) -> ColumnElement[bool]:
    """Return a half-open footprint predicate for the active backend."""
    if dialect_name == POSTGIS_DIALECT:
        envelope = candidate_envelope()
        requested = query_envelope(bbox)
        # ``&&`` is the index-friendly bounding-box test; ST_Intersects is the exact
        # predicate and also protects against a stale or missing index.
        #
        # PostGIS counts a shared edge as intersecting, while the documented contract
        # (and the SQLite subset below) is half-open: a footprint that merely touches
        # the query box is excluded. The explicit comparisons restore that contract;
        # they are evaluated only on envelope candidates.
        return (
            envelope.op("&&")(requested)
            & func.ST_Intersects(envelope, requested)
            & (Candidate.west < bbox.east)
            & (Candidate.east > bbox.west)
            & (Candidate.south < bbox.north)
            & (Candidate.north > bbox.south)
        )
    if dialect_name == SQLITE_TEST_SUBSET:
        return (
            (Candidate.east > bbox.west)
            & (Candidate.west < bbox.east)
            & (Candidate.north > bbox.south)
            & (Candidate.south < bbox.north)
        )
    raise PostgisUnavailableError(
        f"no bbox predicate for dialect {dialect_name!r}; "
        "use PostgreSQL/PostGIS or the explicit SQLite contract-test subset"
    )


def build_scan_statement(
    bbox: BBox,
    *,
    dialect_name: str = POSTGIS_DIALECT,
    limit: int = 100,
    offset: int = 0,
):
    if not 1 <= limit <= MAX_SCAN_LIMIT:
        raise ValueError(f"limit must be within 1..{MAX_SCAN_LIMIT}")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    return (
        select(Candidate)
        .where(bbox_predicate(bbox, dialect_name=dialect_name))
        .order_by(Candidate.location_id)
        .limit(limit)
        .offset(offset)
    )


def build_scan_count_statement(bbox: BBox, *, dialect_name: str = POSTGIS_DIALECT):
    return (
        select(func.count())
        .select_from(Candidate)
        .where(bbox_predicate(bbox, dialect_name=dialect_name))
    )


class PostgisCandidateRepository:
    """Repository with the same read methods plus a bounded regional scan.

    On PostgreSQL the scan uses PostGIS geometry functions. ``allow_sqlite_subset``
    enables only the documented SQLite fallback for contract tests and local
    development; it does not make SQLite a spatial backend.
    """

    def __init__(
        self,
        session: Session,
        *,
        allow_sqlite_subset: bool = False,
        max_scan_area_deg2: float = DEFAULT_MAX_SCAN_AREA_DEG2,
    ) -> None:
        self.session = session
        self.backend = session.get_bind().dialect.name
        if self.backend != POSTGIS_DIALECT and (
            self.backend != SQLITE_TEST_SUBSET or not allow_sqlite_subset
        ):
            raise PostgisUnavailableError(
                "PostGIS adapter requires a PostgreSQL session; "
                "set allow_sqlite_subset=True only for contract tests"
            )
        if max_scan_area_deg2 <= 0:
            raise ValueError("max_scan_area_deg2 must be positive")
        self.max_scan_area_deg2 = max_scan_area_deg2

    def list_candidates(self) -> Sequence[CandidateEntity]:
        rows = self.session.scalars(select(Candidate).order_by(Candidate.location_id)).all()
        return [candidate_entity(row) for row in rows]

    def get_candidate(self, location_id: str) -> CandidateEntity | None:
        row = self.session.get(Candidate, location_id)
        return candidate_entity(row) if row else None

    def candidate_count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(Candidate)) or 0

    def data_version(self) -> str:
        meta = self.session.get(Meta, "data_version")
        return meta.value if meta else "dv-empty"

    def list_datasets(self) -> Sequence[DatasetEntity]:
        rows = self.session.scalars(select(Dataset).order_by(Dataset.dataset_id)).all()
        return [dataset_entity(row) for row in rows]

    def get_dataset(self, dataset_id: str) -> DatasetEntity | None:
        row = self.session.get(Dataset, dataset_id)
        return dataset_entity(row) if row else None

    def list_references(self) -> Sequence[ReferenceEntity]:
        rows = self.session.scalars(
            select(PlanetaryReference).order_by(PlanetaryReference.reference_id)
        ).all()
        return [reference_entity(row) for row in rows]

    def get_reference(self, reference_id: str) -> ReferenceEntity | None:
        row = self.session.get(PlanetaryReference, reference_id)
        return reference_entity(row) if row else None

    def scan_candidates(
        self,
        bbox: BBox,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> RegionalCandidateScan:
        """Return a bounded, deterministic subset of overlapping candidates."""
        _validate_bbox(bbox, self.max_scan_area_deg2)
        statement = build_scan_statement(
            bbox,
            dialect_name=self.backend,
            limit=limit,
            offset=offset,
        )
        rows = self.session.scalars(statement).all()
        matched = self.session.scalar(build_scan_count_statement(bbox, dialect_name=self.backend))
        return RegionalCandidateScan(
            bbox=bbox,
            candidates=tuple(candidate_entity(row) for row in rows),
            matched_count=int(matched or 0),
            backend=self.backend,
            max_area_deg2=self.max_scan_area_deg2,
        )


def postgis_version(session: Session) -> str:
    """Return the server PostGIS version, or raise a clear unavailability error."""
    try:
        version = session.execute(text("SELECT PostGIS_Version()")).scalar_one()
    except Exception as exc:  # noqa: BLE001 - optional extension/driver boundary
        raise PostgisUnavailableError(
            "PostGIS is not available on this PostgreSQL connection; "
            "install/enable the extension before using the adapter"
        ) from exc
    return str(version)


def _require_psycopg() -> None:
    if importlib.util.find_spec("psycopg") is None:
        raise PostgisUnavailableError(
            "the optional PostgreSQL driver is not installed; "
            "install the project extra 'postgis' (psycopg[binary]) first"
        )


@contextmanager
def connect_postgis(
    database_url: str,
    *,
    echo: bool = False,
) -> Iterator[PostgisCandidateRepository]:
    """Open and verify a PostgreSQL/PostGIS session.

    This only connects and checks ``PostGIS_Version()``. Schema creation, seeding,
    and deployment remain separate and unverified operator steps.
    """
    if not database_url.startswith(("postgresql://", "postgresql+psycopg")):
        raise PostgisUnavailableError(
            "connect_postgis requires a postgresql+psycopg:// database URL"
        )
    _require_psycopg()
    from sqlalchemy import create_engine

    engine: Engine = create_engine(database_url, future=True, echo=echo)
    session = Session(engine, expire_on_commit=False)
    try:
        postgis_version(session)
        yield PostgisCandidateRepository(session)
    finally:
        session.close()
        engine.dispose()


def repository_for_session(session: Session) -> CandidateRepositoryFull:
    """Select the adapter from the bound dialect; SQLite remains the default."""
    if session.get_bind().dialect.name == POSTGIS_DIALECT:
        return PostgisCandidateRepository(session)
    return SqlAlchemyRepository(session)
