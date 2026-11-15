"""Contract tests for the optional PostGIS candidate repository.

The default run exercises the SQLite-compatible subset plus PostgreSQL SQL
compilation. A live PostgreSQL/PostGIS contract test is opt-in via
``ANALOG_TEST_POSTGIS_URL`` and is skipped otherwise; a skipped test is not
evidence that PostGIS is deployed.

Both the offline subset test and the live test call the same
``assert_repository_parity`` helper on the same fixture, so "identical results"
is one assertion implementation rather than two hand-written lists that could
drift apart.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict

import pytest
from analog_api.adapters.db import Base, Candidate, Dataset, Meta, PlanetaryReference
from analog_api.adapters.postgis import (
    POSTGIS_BBOX_INDEX_DDL,
    PostgisCandidateRepository,
    PostgisUnavailableError,
    bbox_predicate,
    build_scan_count_statement,
    build_scan_statement,
    connect_postgis,
    repository_for_session,
)
from analog_api.adapters.repository import SqlAlchemyRepository
from analog_api.domain.entities import BBox
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

POSTGIS_URL = os.getenv("ANALOG_TEST_POSTGIS_URL")
FIXTURE_DATASET = "fixture-dem"
FIXTURE_REFERENCE = "fixture-reference"
FIXTURE_ROWS = (
    # location_id, west, south, east, north
    ("earth-fixture-a", 10.0, 45.0, 12.0, 47.0),
    ("earth-fixture-b", 20.0, 10.0, 22.0, 12.0),
    ("earth-fixture-c", 10.5, 45.5, 11.0, 46.0),
    ("earth-fixture-d", 10.9, 45.9, 11.1, 46.1),
)
OVERLAPPING_SCAN = BBox(west=10.0, south=45.0, east=11.5, north=46.5)
EMPTY_SCAN = BBox(west=0.0, south=0.0, east=1.0, north=1.0)
BOUNDARY_SCAN = BBox(west=12.0, south=45.0, east=20.0, north=47.0)
EXACT_SCAN = BBox(west=20.0, south=10.0, east=22.0, north=12.0)
SCAN_CASES = (OVERLAPPING_SCAN, EMPTY_SCAN, BOUNDARY_SCAN, EXACT_SCAN)


def _seed(session: Session) -> None:
    # Keep strong references: a pending object without one can be collected before
    # the flush, which SQLite tolerated but PostgreSQL rejects via the FK constraint.
    dataset = Dataset(
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
    session.add(dataset)
    # There is no ORM relationship between Dataset and Candidate, only a raw FK, so
    # SQLAlchemy does not order these inserts for us. SQLite (FK checks off by default)
    # tolerated child-first inserts; PostgreSQL correctly rejects them, so flush the
    # parent explicitly.
    session.flush()
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
    reference = PlanetaryReference(
        reference_id=FIXTURE_REFERENCE,
        body="Moon",
        display_name="TEST-ONLY fixture reference",
        dataset_id=FIXTURE_DATASET,
        window_size_m=1980.0,
        analysis_resolution_m=90.0,
        crs="IAU_2015:30100",
        valid_fraction=1.0,
        quality_flags=["test_fixture"],
        notes="TEST-ONLY fixture; not a real planetary reference site",
    )
    meta = Meta(key="data_version", value="dv-test-fixture")
    session.add_all([reference, meta])
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


def _protocol_snapshot(repo, candidate_ids: list[str]) -> dict[str, object]:
    """Every read method of ``CandidateRepositoryFull``, normalised to plain data."""
    return {
        "candidates": [asdict(item) for item in repo.list_candidates()],
        "candidate_count": repo.candidate_count(),
        "data_version": repo.data_version(),
        "datasets": [asdict(item) for item in repo.list_datasets()],
        "datasets_by_id": {
            dataset_id: repo.get_dataset(dataset_id) for dataset_id in [FIXTURE_DATASET]
        },
        "references": [asdict(item) for item in repo.list_references()],
        "references_by_id": {
            reference_id: repo.get_reference(reference_id) for reference_id in [FIXTURE_REFERENCE]
        },
        "candidates_by_id": {
            candidate_id: repo.get_candidate(candidate_id) for candidate_id in candidate_ids
        },
        "missing_candidate": repo.get_candidate("no-such-candidate"),
        "missing_dataset": repo.get_dataset("no-such-dataset"),
        "missing_reference": repo.get_reference("no-such-reference"),
    }


def assert_repository_parity(adapter, baseline, *, expected_backend: str) -> None:
    """Assert adapter/baseline parity for the whole Protocol and the bounded scan.

    Used by the offline SQLite-subset test and by the opt-in live PostGIS test so
    both backends are checked with exactly the same expectations.
    """
    candidate_ids = [row[0] for row in FIXTURE_ROWS]
    baseline_snapshot = _protocol_snapshot(baseline, candidate_ids)
    adapter_snapshot = _protocol_snapshot(adapter, candidate_ids)
    assert adapter_snapshot == baseline_snapshot
    # Ordering must be explicit, not incidental: both sides sort by location_id.
    assert [item["location_id"] for item in adapter_snapshot["candidates"]] == sorted(candidate_ids)
    assert adapter_snapshot["candidate_count"] == len(FIXTURE_ROWS)
    # The fixture itself must be non-trivial, so parity cannot pass by being empty.
    assert len(adapter_snapshot["datasets"]) == 1
    assert len(adapter_snapshot["references"]) == 1
    assert adapter_snapshot["data_version"] == "dv-test-fixture"
    assert all(adapter_snapshot["candidates_by_id"].values())

    for bbox in SCAN_CASES:
        first = adapter.scan_candidates(bbox)
        second = adapter.scan_candidates(bbox)
        assert first.backend == expected_backend
        assert first.scope == "regional_bbox_subset_not_global"
        assert first.global_coverage is False
        assert first.max_area_deg2 > 0
        ids = [candidate.location_id for candidate in first.candidates]
        # Deterministic ordering, and a repeated call returns the same rows.
        assert ids == sorted(ids)
        assert [candidate.location_id for candidate in second.candidates] == ids
        assert first.matched_count == second.matched_count
        assert len(ids) == first.matched_count
        assert first.as_dict()["returned_count"] == len(ids)

        # Covering page(s) reassemble the full result set with no gaps or repeats.
        paged: list[str] = []
        offset = 0
        while offset < max(first.matched_count, 1):
            page = adapter.scan_candidates(bbox, limit=2, offset=offset)
            assert page.matched_count == first.matched_count
            paged.extend(candidate.location_id for candidate in page.candidates)
            offset += 2
        assert paged == ids
        assert len(set(paged)) == len(paged)

        if first.matched_count > 1:
            tail = adapter.scan_candidates(bbox, limit=1, offset=1)
            assert [candidate.location_id for candidate in tail.candidates] == ids[1:2]

    overlap = adapter.scan_candidates(OVERLAPPING_SCAN)
    assert [candidate.location_id for candidate in overlap.candidates] == [
        "earth-fixture-a",
        "earth-fixture-c",
        "earth-fixture-d",
    ]
    assert overlap.matched_count == 3

    # Boundary contact stays excluded on both backends (half-open semantics).
    assert adapter.scan_candidates(BOUNDARY_SCAN).candidates == ()
    assert adapter.scan_candidates(BOUNDARY_SCAN).matched_count == 0
    # A query box equal to a footprint still matches it.
    assert [c.location_id for c in adapter.scan_candidates(EXACT_SCAN).candidates] == [
        "earth-fixture-b"
    ]
    assert adapter.scan_candidates(EMPTY_SCAN).matched_count == 0

    # Truncation never changes the matched (coverage) count.
    truncated = adapter.scan_candidates(OVERLAPPING_SCAN, limit=1)
    assert len(truncated.candidates) == 1
    assert truncated.matched_count == 3


def test_adapter_implements_repository_protocol_with_identical_entities(sqlite_session: Session):
    assert_repository_parity(
        _repo(sqlite_session),
        SqlAlchemyRepository(sqlite_session),
        expected_backend="sqlite",
    )


def test_regional_scan_is_bounded_deterministic_and_not_global(sqlite_session: Session):
    scan = _repo(sqlite_session).scan_candidates(OVERLAPPING_SCAN)
    assert scan.global_coverage is False
    assert scan.scope == "regional_bbox_subset_not_global"
    assert scan.backend == "sqlite"
    assert scan.as_dict()["returned_count"] == len(scan.candidates)


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
    with pytest.raises(ValueError, match="limit"):
        repo.scan_candidates(BBox(west=10.0, south=45.0, east=11.0, north=46.0), limit=501)


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
    # Half-open boundary contract: a shared edge is not an overlap.
    assert "candidates.west <" in sql
    assert "candidates.east >" in sql
    assert "candidates.south <" in sql
    assert "candidates.north >" in sql
    assert "ORDER BY candidates.location_id" in sql
    assert "LIMIT 10" in sql
    assert "OFFSET 5" in sql
    assert "4326" in sql
    assert "USING gist" in POSTGIS_BBOX_INDEX_DDL
    assert "ST_MakeEnvelope(west, south, east, north, 4326)" in POSTGIS_BBOX_INDEX_DDL

    count_sql = str(
        build_scan_count_statement(
            BBox(west=10.0, south=45.0, east=11.0, north=46.0),
            dialect_name="postgresql",
        ).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "ST_Intersects" in count_sql
    assert "count(*)" in count_sql


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
            # The image ships PostGIS in ``public``; keep it there and put the
            # contract tables in a throwaway schema that is first in search_path.
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            POSTGIS_URL,
            future=True,
            connect_args={"options": f"-csearch_path={schema},public"},
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
                assert postgres_repo.backend == "postgresql"

                assert_repository_parity(postgres_repo, baseline, expected_backend="postgresql")

                # The session-based factory must pick the PostGIS adapter, never the SQLite one.
                assert isinstance(
                    repository_for_session(postgres_session), PostgisCandidateRepository
                )
                # connect_postgis() must see the same rows through its own connection, so
                # the throwaway schema is passed as a libpq search_path option.
                live_url = make_url(POSTGIS_URL).update_query_dict(
                    {"options": f"-csearch_path={schema},public"}
                )
                # ``str(url)`` masks the password as ``***``, so render it explicitly.
                with connect_postgis(live_url.render_as_string(hide_password=False)) as live_repo:
                    assert live_repo.backend == "postgresql"
                    assert live_repo.candidate_count() == len(FIXTURE_ROWS)
                    live_ids = [candidate.location_id for candidate in live_repo.list_candidates()]
                    assert live_ids == [row[0] for row in FIXTURE_ROWS]
                    assert live_repo.data_version() == "dv-test-fixture"
                # Contract tables live in the throwaway schema only.
                assert (
                    postgres_session.execute(text("SELECT current_schema()")).scalar_one() == schema
                )
        finally:
            engine.dispose()
    finally:
        sqlite_engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


SYNTHETIC_GRID_ROWS = 20_000
_SYNTHETIC_GRID_SQL = """
INSERT INTO candidates (
    location_id, display_name, lat, lon, west, south, east, north, dataset_id,
    window_size_m, analysis_resolution_m, crs, valid_fraction, quality_flags,
    notes, data_kind, area_km2, geometry, origin, created_at
)
SELECT
    'scan-fixture-' || lpad(g::text, 5, '0'),
    'scan-fixture-' || lpad(g::text, 5, '0'),
    -40.0 + (g / 100) * 0.05 + 0.025,
    -100.0 + (mod(g, 100)) * 0.05 + 0.025,
    -100.0 + (mod(g, 100)) * 0.05,
    -40.0 + (g / 100) * 0.05,
    -100.0 + (mod(g, 100)) * 0.05 + 0.05,
    -40.0 + (g / 100) * 0.05 + 0.05,
    :dataset_id, 1980.0, 90.0, 'EPSG:4326', 1.0, '["synthetic_fixture"]'::json,
    'TEST-ONLY synthetic contract fixture; not observed data', 'synthetic', 25.0,
    NULL, 'indexed', now()
FROM generate_series(0, :last_row) AS g
"""


@pytest.mark.postgis
@pytest.mark.skipif(
    not POSTGIS_URL,
    reason="set ANALOG_TEST_POSTGIS_URL to a disposable PostGIS database for the live contract test",
)
def test_live_postgis_expression_index_serves_the_half_open_bbox_scan():
    """The documented GiST expression index is usable and the predicate stays exact.

    Uses a TEST-ONLY synthetic 100 x 200 grid of 0.05 degree cells (no real terrain
    values) purely to make the planner's choice and the boundary semantics observable.
    """
    assert POSTGIS_URL is not None
    admin = create_engine(POSTGIS_URL, future=True)
    schema = f"analog_contract_{uuid.uuid4().hex[:10]}"
    try:
        with admin.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            POSTGIS_URL,
            future=True,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        try:
            with engine.begin() as connection:
                Base.metadata.create_all(connection)
                connection.execute(
                    text(
                        "INSERT INTO datasets (dataset_id, provider, product_id, title, url, "
                        "license, license_url, access_timestamp, source_crs, "
                        "native_resolution_m, processing_version, checksum, source_kind, notes) "
                        "VALUES (:dataset_id, 'TEST-ONLY fixture provider', 'fixture', "
                        "'Synthetic contract fixture, not observed data', "
                        "'https://example.invalid/fixture', 'TEST-ONLY', "
                        "'https://example.invalid/license', '2026-11-15T00:00:00Z', "
                        "'EPSG:4326', 90.0, 'fixture-1.0.0', NULL, 'synthetic', 'TEST-ONLY')"
                    ),
                    {"dataset_id": FIXTURE_DATASET},
                )
                connection.execute(
                    text(_SYNTHETIC_GRID_SQL),
                    {"dataset_id": FIXTURE_DATASET, "last_row": SYNTHETIC_GRID_ROWS - 1},
                )
                # The exact DDL the module documents for large candidate tables.
                connection.execute(text(POSTGIS_BBOX_INDEX_DDL))
                connection.execute(text("ANALYZE candidates"))
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                repo = PostgisCandidateRepository(session)
                assert repo.candidate_count() == SYNTHETIC_GRID_ROWS

                bbox = BBox(west=-99.0, south=-39.0, east=-98.0, north=-38.0)
                page = repo.scan_candidates(bbox, limit=500)
                self_boxes = {
                    row.location_id: (row.west, row.south, row.east, row.north)
                    for row in session.scalars(select(Candidate)).all()
                }
                expected = sorted(
                    location_id
                    for location_id, (west, south, east, north) in self_boxes.items()
                    if west < bbox.east
                    and east > bbox.west
                    and south < bbox.north
                    and north > bbox.south
                )
                assert [candidate.location_id for candidate in page.candidates] == expected[:500]
                assert page.matched_count == len(expected)
                assert page.matched_count > 0

                plan = " ".join(
                    str(line)
                    for line in session.execute(
                        text(
                            "EXPLAIN "
                            + str(
                                build_scan_statement(
                                    bbox, dialect_name="postgresql", limit=500
                                ).compile(
                                    dialect=postgresql.dialect(),
                                    compile_kwargs={"literal_binds": True},
                                )
                            )
                        )
                    ).scalars()
                )
                assert "candidates_bbox_envelope_gix" in plan, plan

                # A cell that only touches the query box is not a match, even with the
                # index in play (half-open contract, same as the SQLite subset).
                touching = sorted(self_boxes)[0]
                west, south, east, north = self_boxes[touching]
                edge = BBox(west=east, south=south, east=east + 0.5, north=north + 0.5)
                edge_scan = repo.scan_candidates(edge, limit=500)
                assert touching not in {candidate.location_id for candidate in edge_scan.candidates}
                assert edge_scan.matched_count == sum(
                    1
                    for cell_west, cell_south, cell_east, cell_north in self_boxes.values()
                    if cell_east > edge.west
                    and cell_west < edge.east
                    and cell_north > edge.south
                    and cell_south < edge.north
                )
        finally:
            engine.dispose()
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
