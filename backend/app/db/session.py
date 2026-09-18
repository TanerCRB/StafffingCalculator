"""Engine and session plumbing.

The engine is built lazily, on first use, not at import time: importing `app.main` must not
require a reachable database (the health endpoint and the unit tests do not touch one).
"""

import threading
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

POOL_RECYCLE_SECONDS = 1800
"""Drop a pooled connection after 30 minutes. Shorter than the idle timeouts a managed
PostgreSQL or a connection proxy typically applies, so the pool retires a connection before the
server does it behind our back."""

_engine_lock = threading.RLock()
"""`lru_cache` returns a cached value atomically, but it does not stop two threads from running
the factory body concurrently on the first call — that would build two engines, publish one and
leak the other's pool. The lock makes first-call construction happen once. Re-entrant because
building the sessionmaker asks for the engine while already holding it."""


@lru_cache(maxsize=1)
def _build_engine() -> Engine:
    return create_engine(
        settings.database_url,
        future=True,
        # A connection killed by a database restart or an idle timeout is discovered by a
        # cheap liveness check, not by a request failing with a bare 500.
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
        # NF-11: a failed statement must not carry its bound values into the log. By default
        # SQLAlchemy appends `[parameters: (...)]` to the exception message, and on an INSERT
        # that is the whole row — for `projects` the owner's name and the description, printed
        # by whatever handles the unhandled exception (uvicorn's traceback, an error tracker,
        # a support ticket somebody pastes it into). The failure does not have to be exotic:
        # a dropped connection, a statement timeout or a NUL byte in a text field is enough.
        # This flag replaces the values with a fixed notice and changes nothing else in the
        # message, so the statement itself is still diagnosable.
        hide_parameters=True,
    )


@lru_cache(maxsize=1)
def _build_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def get_engine() -> Engine:
    with _engine_lock:
        return _build_engine()


def get_sessionmaker() -> sessionmaker[Session]:
    with _engine_lock:
        return _build_sessionmaker()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed when the request ends.

    Tests replace this dependency with one bound to a throwaway PostgreSQL container; nothing
    else in the codebase opens a session of its own.
    """
    with get_sessionmaker()() as session:
        yield session
