"""
ObservaGuard — Database Setup
SQLite via SQLAlchemy. No external DB required.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = "sqlite:///./observaguard.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a DB session and closes it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables on startup."""
    from models import LogEntry, AlertEvent, MetricSnapshot  # noqa: F401
    Base.metadata.create_all(bind=engine)
    print("[DB] SQLite initialized — all tables ready.")
