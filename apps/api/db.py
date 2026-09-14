"""SQLAlchemy engine/session setup."""

from __future__ import annotations

import os
from collections.abc import Callable, Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///./agentpgo.db")
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # Keep the engine available for create_tables and test teardown without
    # requiring callers to know SQLAlchemy internals.
    factory.engine = engine  # type: ignore[attr-defined]
    return factory


def create_tables(session_factory: sessionmaker[Session]) -> None:
    Base.metadata.create_all(session_factory.engine)  # type: ignore[attr-defined]


def get_session_factory() -> sessionmaker[Session]:
    return create_session_factory()


def session_dependency(session_factory: sessionmaker[Session]) -> Callable[[], Generator[Session, None, None]]:
    def get_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    return get_session
