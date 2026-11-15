"""Contract tests for the optional PostGIS candidate repository.

The default run exercises the SQLite-compatible subset plus PostgreSQL SQL
compilation. A live PostgreSQL/PostGIS contract test is opt-in via
``ANALOG_TEST_POSTGIS_URL`` and is skipped otherwise; a skipped test is not
evidence that PostGIS is deployed.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict

import pytest
from analog_api.adapters.db import Base, Candidate, Dataset, Meta
from analog_api.adapters.postgis import (
    POSTGIS_BBOX_INDEX_DDL,
    PostgisCandidateRepository,
    PostgisUnavailableError,
    bbox_predicate,
    build_scan_statement,
    connect_postgis,
    repository_for_session,
)
from analog_api.adapters.repository import SqlAlchemyRepository
from analog_api.domain.entities import BBox
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

POSTGIS_URL = os.getenv("ANALOG_TEST_POSTGIS_URL")
FIXTURE_DATASET = "fixture-dem"
FIXTURE_ROWS = (
    ("earth-fixture-a", 10.0, 45.0, 12.0, 47.0),
    ("earth-fixture-b", 20.0, 10.0, 22.0, 12.0),
    ("earth-fixture-c", 10.5, 45.5, 11.0, 46.0),
)


def _seed(session: Session) -> None:
    session.add(
        Dataset(
            dataset_id=FIXTURE_DATASET,
            provider="TEST-ONLY fixture provider",
            product_id="fixture",
            title="Synthetic contract fixture, not observed data",
            url="https://example.invalid/fixture",
            license="TEST-ONLY",
            license_url="https://example.invalid/license",
            access_timestamp="2026-11-15T00:00:00Z",
            source_crs="EPSG:4326",
            native_resolution_m=90.0,
            processing_version="fixture-1.0.0",
            checksum=None,
            source_kind="synthetic",
            notes="TEST-ONLY",
        )
    )
    for location_id, west, south, east, north in FIXTURE_ROWS:
        session.add(
            Candidate(
                location_id=location_id,
                display_name=location_id,
                lat=(south + north) / 2,
                lon=(west + east) / 2,
                west=west,
                south=south,
                east=east,
                north=north,
                dataset_id=FIXTURE_DATASET,
                window_size_m=1980.0,
                analysis_resolution_m=90.0,
                crs="EPSG:32632",
                valid_fraction=1.0,
                quality_flags=["test_fixture"],
                notes="TEST-ONLY",
                data_kind="synthetic",
                area_km2=1.0,
                geometry=None,
                origin="indexed",
            )
        )
    session.add(Meta(key="data_version", value="dv-test-fixture"))
    session.commit()


@pytest.fixture()
def sqlite_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    _seed(session)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _repo(session: Session) -> PostgisCandidateRepository:
    return PostgisCandidateRepository(session, allow_sqlite_subset=True)


def test_adapter_implements_repository_protocol_with_identical_entities(sqlite_session: Session):
    baseline = SqlAlchemyRepository(sqlite_session)
    adapter = _repo(sqlite_session)

    assert [asdict(item) for item in adapter.list_candidates()] == [
        asdict(item) for item in baseline.list_candidates()
    ]
    assert adapter.candidate_count() == baseline.candidate_count() == 3
    assert adapter.data_version() == baseline.data_version() == "dv-test-fixture"
    assert [asdict(item) for item in adapter.list_datasets()] == [
        asdict(item) for item in baseline.list_datasets()
    ]
    assert adapter.get_candidate("earth-fixture-b") == baseline.get_candidate("earth-fixture-b")
    assert adapter.get_candidate("missing") is None
    assert adapter.get_dataset(FIXTURE_DATASET) == baseline.get_dataset(FIXTURE_DATASET)
    assert list(adapter.list_references()) == list(baseline.list_references())


def test_regional_scan_is_bounded_deterministic_and_not_global(sqlite_session: Session):
    scan = _repo(sqlite_session).scan_candidates(BBox(west=10.0, south=45.0, east=11.5, north=46.5))
    assert [candidate.location_id for candidate in scan.candidates] == [
        "earth-fixture-a",
        "earth-fixture-c",
    ]
    assert scan.matched_count == 2
    assert scan.global_coverage is False
    assert scan.scope == "regional_bbox_subset_not_global"
    assert scan.backend == "sqlite"
    assert scan.as_dict()["returned_count"] == 2

    page = _repo(sqlite_session).scan_candidates(
        BBox(west=10.0, south=45.0, east=11.5, north=46.5), limit=1, offset=1
    )
    assert [candidate.location_id for candidate in page.candidates] == ["earth-fixture-c"]
    assert page.matched_count == 2


def test_touching_boundary_is_excluded_like_the_half_open_predicate(sqlite_session: Session):
    scan = _repo(sqlite_session).scan_candidates(BBox(west=12.0, south=45.0, east=20.0, north=47.0))
    # Candidate A ends exactly at west=12 and candidate B starts at east=20.
    assert scan.candidates == ()
    assert scan.matched_count == 0


def test_scan_rejects_oversized_and_invalid_bounds(sqlite_session: Session):
    repo = _repo(sqlite_session)
    with pytest.raises(ValueError, match="bounded maximum"):
        repo.scan_candidates(BBox(west=-50.0, south=-40.0, east=50.0, north=40.0))
    with pytest.raises(ValueError, match="west < east"):
        repo.scan_candidates(BBox(west=10.0, south=45.0, east=9.0, north=46.0))
    with pytest.raises(ValueError, match="limit"):
        repo.scan_candidates(BBox(west=10.0, south=45.0, east=11.0, north=46.0), limit=0)
    with pytest.raises(ValueError, match="offset"):
        repo.scan_candidates(BBox(west=10.0, south=45.0, east=11.0, north=46.0), offset=-1)


def test_postgis_sql_uses_envelope_intersects_and_index_operator():
    statement = build_scan_statement(
        BBox(west=10.0, south=45.0, east=11.0, north=46.0),
        dialect_name="postgresql",
        limit=10,
        offset=5,
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "ST_MakeEnvelope" in sql
    assert "ST_Intersects" in sql
    assert "&&" in sql
    assert "ORDER BY candidates.location_id" in sql
    assert "LIMIT 10" in sql
    assert "OFFSET 5" in sql
    assert "4326" in sql
    assert "USING gist" in POSTGIS_BBOX_INDEX_DDL
    assert "ST_MakeEnvelope(west, south, east, north, 4326)" in POSTGIS_BBOX_INDEX_DDL


def test_sqlite_subset_sql_never_claims_postgis_functions():
    predicate = bbox_predicate(
        BBox(west=10.0, south=45.0, east=11.0, north=46.0),
        dialect_name="sqlite",
    )
    sql = str(predicate.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    assert "ST_" not in sql
    assert "candidates.east >" in sql


def test_non_postgres_session_requires_explicit_test_subset(sqlite_session: Session):
    with pytest.raises(PostgisUnavailableError, match="allow_sqlite_subset"):
        PostgisCandidateRepository(sqlite_session)


def test_repository_for_session_keeps_sqlite_default(sqlite_session: Session):
    assert isinstance(repository_for_session(sqlite_session), SqlAlchemyRepository)


def test_connect_postgis_rejects_non_postgres_url_without_network():
    with (
        pytest.raises(PostgisUnavailableError, match="postgresql"),
        connect_postgis("sqlite+pysqlite:///:memory:"),
    ):
        pass


@pytest.mark.postgis
@pytest.mark.skipif(
    not POSTGIS_URL,
    reason="set ANALOG_TEST_POSTGIS_URL to a disposable PostGIS database for the live contract test",
)
def test_live_postgis_contract_matches_sqlite_fixture():
    """Opt-in live contract test. It creates and drops its own schema only."""
    assert POSTGIS_URL is not None
    admin = create_engine(POSTGIS_URL, future=True)
    schema = f"analog_contract_{uuid.uuid4().hex[:10]}"
    sqlite_engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            POSTGIS_URL,
            future=True,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        try:
            with engine.begin() as connection:
                assert connection.execute(text("SELECT PostGIS_Version()")).scalar_one()
                Base.metadata.create_all(connection)
            factory = sessionmaker(bind=engine, expire_on_commit=False)

            Base.metadata.create_all(sqlite_engine)
            sqlite_factory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
            with factory() as postgres_session, sqlite_factory() as sqlite_session:
                _seed(postgres_session)
                _seed(sqlite_session)
                baseline = SqlAlchemyRepository(sqlite_session)
                postgres_repo = PostgisCandidateRepository(postgres_session)

                assert [asdict(item) for item in postgres_repo.list_candidates()] == [
                    asdict(item) for item in baseline.list_candidates()
                ]
                assert [asdict(item) for item in postgres_repo.list_datasets()] == [
                    asdict(item) for item in baseline.list_datasets()
                ]
                assert list(postgres_repo.list_references()) == list(baseline.list_references())
                assert postgres_repo.candidate_count() == baseline.candidate_count()
                assert postgres_repo.data_version() == baseline.data_version()

                scan = postgres_repo.scan_candidates(
                    BBox(west=10.0, south=45.0, east=11.5, north=46.5)
                )
                assert [candidate.location_id for candidate in scan.candidates] == [
                    "earth-fixture-a",
                    "earth-fixture-c",
                ]
                assert scan.backend == "postgresql"
                assert scan.matched_count == 2
                assert scan.global_coverage is False
        finally:
            engine.dispose()
    finally:
        sqlite_engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
