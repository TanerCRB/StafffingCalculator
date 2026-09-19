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
from contextlib import contextmanager
from dataclasses import dataclass
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

from app.api.deps import get_caller_identity  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.identity import CallerIdentity, Permission  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    CatalogDefaultRate,
    CatalogEngagementType,
    CatalogLocation,
    CatalogRole,
    CatalogSeniority,
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
    """A throwaway PostgreSQL. `TEST_DATABASE_URL` overrides it for a locally running server.

    !!! THIS DATABASE GETS WIPED. Every test using the `committing_client` fixture ends by
    deleting **all** rows from `project_access`, `scenarios`, `projects`, `catalog_default_rates`
    and the four catalogue dictionaries — unconditionally, with no check of what it is connected
    to, and the migrations are run against it on top of that. Never point `TEST_DATABASE_URL` at a
    database holding data you want to keep: a development database with hand-made projects or a
    hand-built rate catalogue in it is emptied by a single `pytest` run, with no prompt and no
    backup.

    The default path (no variable set) is a disposable container, which is why this is a warning
    and not a guard — but the variable turns "disposable" into whatever the reader typed.
    """
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
    """One transaction per test, rolled back afterwards — tests never see each other's rows.

    `join_transaction_mode="create_savepoint"`: SC-1-01 introduced a code path that *commits*
    (`app.data.project_writes.create_project`). Without this the commit would end the outer
    transaction and leak rows into the next test; with it, the session commits into a SAVEPOINT
    the rollback below still discards. The commit is real as far as the code under test is
    concerned — which is the point: a write path that never commits proves nothing about
    persistence.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )
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


@pytest.fixture
def client_serving_server_errors(db_session: Session) -> Iterator[TestClient]:
    """`client`, except that an unhandled exception comes back as a `500` response.

    `TestClient` re-raises server exceptions by default, which is the right default — a suite
    that swallowed them would report "the endpoint answered" where the process in fact crashed.
    It does make one claim untestable, though: that a failure the code deliberately does *not*
    handle is served as a `500` rather than quietly converted into something friendlier. That
    claim is the point of R-01 (reviewer 2026-09-19) — an unclassified write failure must not
    arrive as a `409` describing a conflict nobody observed."""
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def committing_client(engine: Engine) -> Iterator[TestClient]:
    """The API with requests running in real, committed transactions — not the rolled-back one.

    SC-1-01 is a task about *persistence*, and inside the shared `db_session` transaction a write
    that merely flushes is indistinguishable from one that commits: both are visible to every
    later read on the same connection. Here each request gets its own session straight from the
    engine, so a row outlives the request only if the code under test really committed, and a
    separate connection can be used to check.

    **Destructive teardown.** Afterwards every row of `project_access`, `scenarios`, `projects`,
    `catalog_default_rates` and the four catalogue dictionaries is deleted on a separate
    connection — all of them, not only the ones this test created, because a committed row is no
    longer distinguishable from pre-existing data by the time the fixture ends. Nothing else
    cleans up after these tests, and one leftover project breaks the `count_projects(...) == 0`
    assertions everywhere else — one leftover rate breaks every `EXCLUDE` assertion, since an
    overlap refusal would then have two possible causes. Read the warning on `database_url`
    before pointing `TEST_DATABASE_URL` at anything you care about."""

    def session_for_request() -> Iterator[Session]:
        with Session(bind=engine, expire_on_commit=False, future=True) as session:
            yield session

    app.dependency_overrides[get_session] = session_for_request
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)
        with engine.begin() as connection:
            connection.execute(sa.delete(ProjectAccess))
            connection.execute(sa.delete(Scenario))
            connection.execute(sa.delete(Project))
            # Rates first: the four dictionaries are referenced by foreign keys with no `ON DELETE`
            # action, so the database refuses to empty them while a rate still points at one. That
            # refusal is the intended behaviour (deleting a dimension entry in use is out of scope
            # for SC-2-01), which makes the order here part of the fixture, not a detail.
            connection.execute(sa.delete(CatalogDefaultRate))
            for dimension in (
                CatalogRole,
                CatalogSeniority,
                CatalogLocation,
                CatalogEngagementType,
            ):
                connection.execute(sa.delete(dimension))


def as_caller(user_id: str) -> dict[str, str]:
    """Request headers carrying the placeholder caller identity (ADR-0005, addendum)."""
    return {settings.caller_id_header: user_id}


@contextmanager
def caller_holding(*permissions: Permission, user_id: str = IN_SCOPE_USER) -> Iterator[None]:
    """Run the enclosed requests as `user_id` holding exactly `permissions`, and nothing else.

    The only way to reach a caller with `PERSONNEL_COSTS_READ`, and the only way to reach one
    *without* a permission the placeholder grants: `PLACEHOLDER_PERMISSIONS` is a fixed set, and
    widening it would both trip the set-equality canary and widen ADR-0005's dated deviation
    (addendum 2026-09-19, point 5; addendum "pierwszy zbiór danych bez zasięgu projektu", point 6).

    `user_id` stays a real user id, so the `project_access` rows and the scope filter are the real
    ones — this substitutes the permission set, not the subject.

    A sibling of the local helper in `test_project_personnel_cost_visibility.py`, which predates it;
    that file keeps its own copy so SC-1-08's proofs stay readable in one piece.
    """
    app.dependency_overrides[get_caller_identity] = lambda: CallerIdentity(
        user_id=user_id, permissions=frozenset(permissions)
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_caller_identity, None)


def project_payload(**overrides: object) -> dict[str, object]:
    """A valid `POST /projects` body: exactly F-01's project fields, nothing more.

    Kept here so the access-control tests and the SC-1-01 tests send the *same* request and a
    denial can never be an accident of a malformed body.
    """
    body: dict[str, object] = {
        "name": "Aurora migration",
        "client": "Northwind",
        "owner": "Anna Kowalska",
        "delivery_period": {"start": "2026-03-01", "end": "2026-11-30"},
        "reporting_currency": "EUR",
        "description": "Migration of the billing platform to the cloud.",
    }
    return body | overrides


def count_projects(session: Session) -> int:
    """Project rows visible in the test transaction — used to prove a refused write wrote
    nothing, not merely that the response said no."""
    return session.execute(sa.select(sa.func.count()).select_from(Project)).scalar_one()


def make_project(
    session: Session,
    *,
    name: str,
    status: ProjectStatus = ProjectStatus.ACTIVE,
    accessible_to: tuple[str, ...] = (),
    cost_visible_to: tuple[str, ...] = (),
    client_name: str = "Northwind",
    owner: str = "Anna Kowalska",
) -> Project:
    """Insert a project row, plus its `project_access` rows. Direct write, no endpoint.

    `cost_visible_to` sets `project_access.can_view_personnel_costs` on the listed users' grants
    (SC-1-08). It must be a subset of `accessible_to`: the flag lives *on* a grant, so there is no
    such thing as cost visibility without access, and naming a user here who is not in
    `accessible_to` would silently do nothing. Default empty, matching the column's `server_default`
    and the fact that no production path grants the flag.
    """
    unknown = sorted(set(cost_visible_to) - set(accessible_to))
    if unknown:
        raise ValueError(
            "cost_visible_to must be a subset of accessible_to; no grant exists for: "
            + ", ".join(unknown)
        )
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
        session.add(
            ProjectAccess(
                user_id=user_id,
                project_id=project.id,
                can_view_personnel_costs=user_id in cost_visible_to,
            )
        )
    session.flush()
    return project


def grant_personnel_cost_visibility(
    session: Session, *, project_id: uuid.UUID, user_id: str
) -> None:
    """Set `can_view_personnel_costs` on one existing grant. Direct write, no endpoint.

    There is no endpoint to call: SC-1-08 deliberately ships no path that grants this flag
    (ADR-0005, addendum 2026-09-19, point 4), so the positive branch of the gate is reachable from
    a test only. Used where the grant already exists because the endpoint under test created it —
    `POST /projects` grants access to its creator with the flag `false`.
    """
    updated = session.execute(
        sa.update(ProjectAccess)
        .where(ProjectAccess.user_id == user_id, ProjectAccess.project_id == project_id)
        .values(can_view_personnel_costs=True)
    )
    assert updated.rowcount == 1, "no project_access grant to set the flag on"
    session.flush()


# --- the catalogue (SC-2-01) --------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionTuple:
    """One full rate key: role, seniority, location, engagement type.

    All four together, because the full tuple *is* the key (gate-1 decision 3) — passing them around
    as four loose arguments is how a test ends up varying two dimensions while claiming to vary one.
    `replace(tuple, role_id=...)` is then the exact operation criterion K-06 is about.
    """

    role_id: uuid.UUID
    seniority_id: uuid.UUID
    location_id: uuid.UUID
    engagement_type_id: uuid.UUID

    def as_query(self) -> dict[str, str]:
        """The tuple as query parameters for `GET /catalog/rates/effective`."""
        return {
            "role_id": str(self.role_id),
            "seniority_id": str(self.seniority_id),
            "location_id": str(self.location_id),
            "engagement_type_id": str(self.engagement_type_id),
        }


def make_dimension_tuple(session: Session, *, suffix: str = "") -> DimensionTuple:
    """Insert one entry in each of the four dictionaries and return their ids. Direct write.

    `suffix` keeps the names unique when a test needs a second, differing entry of every dimension
    (`uq_<table>_name` refuses a repeat).
    """
    role = CatalogRole(id=uuid.uuid4(), name=f"Backend Engineer{suffix}")
    seniority = CatalogSeniority(id=uuid.uuid4(), name=f"Senior{suffix}")
    location = CatalogLocation(id=uuid.uuid4(), name=f"Poland{suffix}")
    engagement = CatalogEngagementType(id=uuid.uuid4(), name=f"Full-time{suffix}")
    session.add_all([role, seniority, location, engagement])
    session.flush()
    return DimensionTuple(
        role_id=role.id,
        seniority_id=seniority.id,
        location_id=location.id,
        engagement_type_id=engagement.id,
    )


def make_rate(
    session: Session,
    dimensions: DimensionTuple,
    *,
    effective_from: date,
    effective_to: date | None = None,
    default_cost_rate: Decimal = Decimal("100.0000"),
    default_selling_rate: Decimal = Decimal("150.0000"),
    currency: str = "EUR",
    unit: str = "hour",
) -> CatalogDefaultRate:
    """Insert one rate row directly — no endpoint, no request schema.

    Deliberately bypasses `CatalogRateCreateRequest`: the constraints under test (the `EXCLUDE`, the
    unit CHECK) are claims about the *database*, and a path that went through Pydantic would prove
    only that Pydantic refused first. `unit` is a parameter for the same reason.

    Flushes rather than commits, so the row lives in the test's transaction; the tests that need a
    committed row (the two-connection race) commit for themselves.
    """
    rate = CatalogDefaultRate(
        id=uuid.uuid4(),
        role_id=dimensions.role_id,
        seniority_id=dimensions.seniority_id,
        location_id=dimensions.location_id,
        engagement_type_id=dimensions.engagement_type_id,
        default_cost_rate=default_cost_rate,
        default_selling_rate=default_selling_rate,
        currency=currency,
        unit=unit,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    session.add(rate)
    session.flush()
    return rate


def rate_payload(dimensions: DimensionTuple, **overrides: object) -> dict[str, object]:
    """A valid `POST /catalog/rates` body. Decimal amounts as strings, never JSON floats."""
    body: dict[str, object] = {
        "role_id": str(dimensions.role_id),
        "seniority_id": str(dimensions.seniority_id),
        "location_id": str(dimensions.location_id),
        "engagement_type_id": str(dimensions.engagement_type_id),
        "default_cost_rate": "100.0000",
        "default_selling_rate": "150.0000",
        "currency": "EUR",
        "unit": "hour",
        "effective_from": "2026-01-01",
        "effective_to": "2026-06-30",
    }
    return body | overrides


def count_dimension_entries(session: Session, model: type) -> int:
    """Rows of one dimension dictionary visible in the test transaction."""
    return session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


def count_rates(session: Session) -> int:
    """Rate rows visible in the test transaction — used to prove a refused write wrote nothing,
    not merely that the response said no."""
    return session.execute(
        sa.select(sa.func.count()).select_from(CatalogDefaultRate)
    ).scalar_one()


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
