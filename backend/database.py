"""SQLite database engine and session helpers.

Uses SQLAlchemy 2.x with the async-compatible synchronous driver for
simplicity in a local-first deployment.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import settings

# Ensure data directory exists
_db_path = settings.database_url.replace("sqlite:///", "")
Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, echo=settings.debug)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GameRecord(Base):
    """Persisted game schema — the normalised rulebook representation."""

    __tablename__ = "games"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False, index=True)
    source = Column(String(32), nullable=False, default="search")  # search | ocr | manual
    schema_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    @property
    def schema_data(self) -> dict[str, Any]:
        return json.loads(self.schema_json) if self.schema_json else {}


class GameSession(Base):
    """Active or completed game moderation session."""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, nullable=False, index=True)
    players_json = Column(Text, nullable=False, default="[]")
    state_json = Column(Text, nullable=False, default="{}")
    status = Column(String(16), nullable=False, default="setup")  # setup | playing | finished
    house_rules_json = Column(Text, nullable=False, default="[]")
    history_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class DisputeRecord(Base):
    """Logged dispute and its AI-provided resolution."""

    __tablename__ = "disputes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, nullable=False, index=True)
    description = Column(Text, nullable=False)
    ruling_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=_utcnow)


def init_db() -> None:
    """Create all tables if they do not exist."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:  # type: ignore[type-arg]
    """FastAPI dependency that yields a DB session."""
    db = SessionLocal()
    try:
        yield db  # type: ignore[misc]
    finally:
        db.close()
