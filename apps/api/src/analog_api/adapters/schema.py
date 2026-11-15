"""Additive SQLite schema versioning and fail-closed compatibility checks (Issue #70).

The MVP still creates tables with ``Base.metadata.create_all``. To make upgrades and
backup/restore drills observable, every database records a monotonic integer schema
version in the ``meta`` table. A database written by a *newer* schema is rejected before
any seeding or mutation, so an older binary can never silently downgrade user data.

Legacy databases without a version stamp are treated as the current version: the schema
history is purely additive, so create_all only adds tables/indexes. The stamp is written
after the next successful seed or explicit migration.
"""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from analog_api.adapters.db import Meta

SCHEMA_VERSION = 1
SCHEMA_VERSION_KEY = "schema_version"


class SchemaVersionError(RuntimeError):
    """The database schema is newer or malformed and must not be modified."""


def read_schema_version(engine: Engine) -> int | None:
    """Return the recorded schema version, or None for a legacy/uninitialised database."""
    inspector = inspect(engine)
    if "meta" not in inspector.get_table_names():
        return None
    with Session(engine) as session:
        row = session.get(Meta, SCHEMA_VERSION_KEY)
        if row is None:
            return None
        try:
            return int(row.value)
        except (TypeError, ValueError) as exc:
            raise SchemaVersionError(
                f"invalid {SCHEMA_VERSION_KEY} value {row.value!r}; refusing to modify the database"
            ) from exc


def ensure_schema_compatible(engine: Engine) -> int | None:
    """Fail closed if the database was written by a newer schema version."""
    version = read_schema_version(engine)
    if version is not None and version > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database schema version {version} is newer than this build supports "
            f"({SCHEMA_VERSION}); upgrade the application instead of downgrading data"
        )
    return version


def stamp_schema_version(engine: Engine) -> int:
    """Record the current schema version after a successful additive migration/seed."""
    ensure_schema_compatible(engine)
    with Session(engine) as session:
        session.merge(Meta(key=SCHEMA_VERSION_KEY, value=str(SCHEMA_VERSION)))
        session.commit()
    return SCHEMA_VERSION


__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_KEY",
    "SchemaVersionError",
    "ensure_schema_compatible",
    "read_schema_version",
    "stamp_schema_version",
]
