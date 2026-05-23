"""
Shared pytest fixtures for ObservaGuard tests.

All tests use an in-memory SQLite database (StaticPool so multiple
sessions share the same connection) — no files written to disk.
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

# Set test overrides BEFORE importing the app so module-level singletons
# (engine, WEBHOOK_URL) are initialised with the right values.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("OBSERVAGUARD_PORT", "9999")  # port unused during tests

from app.main import app, _received_webhooks       # noqa: E402
from app.database import Base, get_db              # noqa: E402

_TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture(scope="function")
def db_engine():
    """Fresh in-memory SQLite engine for every test function."""
    engine = create_engine(
        _TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    """SQLAlchemy session wired to the test engine."""
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(scope="function")
def client(db_engine):
    """
    FastAPI TestClient with get_db overridden to use the in-memory test DB.
    The in-memory webhook list is cleared before and after each test.
    """
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    _received_webhooks.clear()

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()
    _received_webhooks.clear()
