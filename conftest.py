"""Shared pytest fixtures.

DB-backed tests use TEST_DATABASE_URL (the `study_test` database in the compose stack).
The schema is dropped and rebuilt once per test session, so never point this at real data.
"""
from __future__ import annotations

import os

import psycopg
import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def db_url() -> str:
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set (run `make test` to use the compose Postgres)")
    if "test" not in TEST_DB.rsplit("/", 1)[-1]:
        pytest.exit(f"refusing to reset non-test database {TEST_DB!r}")
    from migrate import migrate
    from seed import seed

    migrate(TEST_DB, reset=True)
    seed(TEST_DB)
    return TEST_DB


@pytest.fixture
def conn(db_url):
    with psycopg.connect(db_url, autocommit=True) as c:
        yield c


@pytest.fixture
def clean(conn):
    """Empty the per-test tables (keeps cohorts/instances seed)."""
    conn.execute("truncate events, sessions, instance_downtime, match_events, components restart identity cascade")
    yield conn


@pytest.fixture
def client(db_url, clean, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    from fastapi.testclient import TestClient

    from ingest.main import app

    with TestClient(app) as c:
        yield c
