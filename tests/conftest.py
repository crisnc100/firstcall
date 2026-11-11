"""Shared fixtures: a throwaway Postgres database per test session, emptied per test."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Base
from firstcall.settings import Settings


def server_url() -> str:
    return os.environ.get("FIRSTCALL_DATABASE_URL") or Settings().database_url


def create_database(name: str) -> str:
    base = make_url(server_url())
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return base.set(database=name).render_as_string(hide_password=False)


def drop_database(name: str) -> None:
    admin = create_engine(make_url(server_url()), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def upgrade(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.attributes["url"] = url
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    name = f"firstcall_test_{uuid.uuid4().hex[:12]}"
    url = create_database(name)
    upgrade(url)
    yield url
    drop_database(name)


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url, pool_size=20, max_overflow=20)
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} CASCADE"))
    return sessionmaker(engine, expire_on_commit=False)


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 3, 2, 14, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: Any) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """A skipped or expected-to-fail test fails the run: verify allows zero skips."""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    stats: dict[str, list[Any]] = getattr(reporter, "stats", {})
    if exitstatus == 0 and (stats.get("skipped") or stats.get("xfailed")):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
