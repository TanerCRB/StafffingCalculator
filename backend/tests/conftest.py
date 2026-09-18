"""Test fixtures.

The database is a real PostgreSQL in a container, migrated with Alembic — not SQLite and not a
mock. ADR-0001 is explicit about this: a stand-in proves nothing about the mechanisms
(constraints, enum types, isolation behaviour) being tested. Running the actual migration also
means the tests exercise the migration file, not `metadata.create_all`.

Every write in these fixtures is a direct database write. SC-1-05 is read-only: there is no
create/edit/archive endpoint yet, and these tests must not pretend otherwise.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

# The placeholder caller identity refuses to run unless a run opts into it explicitly
# (`app.api.deps.assert_identity_mechanism_allowed`). The test suite is exactly such a run and
# says so here — before anything imports `app.main` and builds `Settings`. `setdefault` leaves a
# value already present in the environment untouched.
os.environ.setdefault("APP_ALLOW_PLACEHOLDER_IDENTITY", "true")
os.environ.setdefault("APP_ENVIRONMENT", "test")

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Project,
    ProjectAccess,
    ProjectStatus,
    Scenario,
    ScenarioStatus,
)

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

POSTGRES_IMAGE = (
    "postgres:16.15-alpine@sha256:"
    "3c5c8892d184f738f4fe282d14ddaa613a38f00f4189d2d94725ebe6f2909ddb"
)
"""Pinned by digest, not by a moving tag: `postgres:16-alpine` silently becomes a different
build, and then a suite that passed yesterday says nothing about what it ran against today.
Bumping this line is a deliberate, reviewable change."""

IN_SCOPE_USER = "pm-anna"
OUT_OF_SCOPE_USER = "pm-bartek"
UNKNOWN_USER = "pm-celina"
"""A caller who holds no `project_access` row at all — neither granted nor explicitly denied."""


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway PostgreSQL. `TEST_DATABASE_URL` overrides it for a locally running server."""
    existing = os.environ.get("TEST_DATABASE_URL")
    if existing:
        yield existing
        return

    try:
        from testcontainers.community.postgres import PostgresContainer
    except ModuleNotFoundError:  # testcontainers < 4.15
        from testcontainers.postgres import PostgresContainer

    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    alembic_config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    alembic_config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    engine = sa.create_engine(database_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """One transaction per test, rolled back afterwards — tests never see each other's rows."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False, future=True)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """The API under test, bound to the test transaction instead of the application engine."""
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def as_caller(user_id: str) -> dict[str, str]:
    """Request headers carrying the placeholder caller identity (ADR-0005, addendum)."""
    return {settings.caller_id_header: user_id}


def make_project(
    session: Session,
    *,
    name: str,
    status: ProjectStatus = ProjectStatus.ACTIVE,
    accessible_to: tuple[str, ...] = (),
    client_name: str = "Northwind",
    owner: str = "Anna Kowalska",
) -> Project:
    """Insert a project row, plus its `project_access` rows. Direct write, no endpoint."""
    project = Project(
        id=uuid.uuid4(),
        name=name,
        client=client_name,
        owner=owner,
        delivery_period_start=date(2026, 1, 1),
        delivery_period_end=date(2026, 12, 31),
        reporting_currency="EUR",
        description=f"{name} description",
        status=status,
    )
    session.add(project)
    session.flush()
    for user_id in accessible_to:
        session.add(ProjectAccess(user_id=user_id, project_id=project.id))
    session.flush()
    return project


def make_scenario(
    session: Session,
    project: Project,
    *,
    name: str,
    status: ScenarioStatus = ScenarioStatus.DRAFT,
    start_date: date | None = None,
    end_date: date | None = None,
    working_calendar: str | None = None,
    full_time_hours_per_week: Decimal | None = None,
    currency: str | None = None,
    target_margin_percent: Decimal | None = None,
) -> Scenario:
    """Insert a scenario row directly. Anything left at `None` is a missing input by design."""
    scenario = Scenario(
        id=uuid.uuid4(),
        project_id=project.id,
        name=name,
        status=status,
        start_date=start_date,
        end_date=end_date,
        working_calendar=working_calendar,
        full_time_hours_per_week=full_time_hours_per_week,
        currency=currency,
        target_margin_percent=target_margin_percent,
    )
    session.add(scenario)
    session.flush()
    return scenario
