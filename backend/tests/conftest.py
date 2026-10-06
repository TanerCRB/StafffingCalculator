"""Test fixtures.

The database is a real PostgreSQL in a container, migrated with Alembic — not SQLite and not a
mock. ADR-0001 is explicit about this: a stand-in proves nothing about the mechanisms
(constraints, enum types, isolation behaviour) being tested. Running the actual migration also
means the tests exercise the migration file, not `metadata.create_all`.

Every write in these fixtures is a direct database write. SC-1-05 is read-only: there is no
create/edit/archive endpoint yet, and these tests must not pretend otherwise.
"""

import os
import time
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
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.deps import get_caller_identity  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.identity import CallerIdentity, Permission  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AbsenceBudget,
    AbsenceType,
    AdditionalCost,
    ApprovedSnapshotAbsenceBudget,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotCatalogDefaultRate,
    ApprovedSnapshotExchangeRate,
    ApprovedSnapshotOrganizationDefaults,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
    AuditLog,
    CatalogCostCategory,
    CatalogDefaultRate,
    CatalogEngagementType,
    CatalogLocation,
    CatalogRole,
    CatalogSeniority,
    CatalogVendor,
    CommercialTerms,
    ExchangeRate,
    FixedPriceAdjustment,
    FixedPriceTerms,
    OrganizationDefaults,
    OutcomeTerms,
    Person,
    Project,
    ProjectAccess,
    ProjectStatus,
    RiskReserve,
    Scenario,
    ScenarioDeliverySegment,
    ScenarioRisk,
    ScenarioStatus,
    StaffingPosition,
    StaffingPositionAbsence,
    StaffingPositionAllocation,
    StoryPointsTerms,
    TmTerms,
    WorkingCalendar,
    WorkingCalendarDay,
    WorkingCalendarDayKind,
)

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

POSTGRES_IMAGE = (
    "postgres:16.15-alpine@sha256:"
    "3c5c8892d184f738f4fe282d14ddaa613a38f00f4189d2d94725ebe6f2909ddb"
)
"""Pinned by digest, not by a moving tag: `postgres:16-alpine` silently becomes a different
build, and then a suite that passed yesterday says nothing about what it ran against today.
Bumping this line is a deliberate, reviewable change."""

IN_SCOPE_USER = "synthetic-00000000-0000-4000-8000-000000000001"
OUT_OF_SCOPE_USER = "pm-bartek"
UNKNOWN_USER = "pm-celina"
"""A caller who holds no `project_access` row at all — neither granted nor explicitly denied."""


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway PostgreSQL. `TEST_DATABASE_URL` overrides it for a locally running server.

    !!! THIS DATABASE GETS WIPED. Every test using the `committing_client` fixture ends by
    deleting **all** rows from `staffing_position_allocation`, `staffing_position`,
    `project_access`, `scenarios`, `projects`, `catalog_default_rates`
    and the five catalogue dictionaries — unconditionally, with no check of what it is connected
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

    **Destructive teardown.** Afterwards every row of the two staffing tables, `project_access`,
    `scenarios`, `projects`, `catalog_default_rates` and the five dictionaries is deleted on a
    separate connection — all of them, not only the ones this test created, because a committed row
    is no longer distinguishable from pre-existing data by the time the fixture ends. Nothing else
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
            connection.execute(sa.delete(ExchangeRate))
            # Staffing first, grandchild before child (SC-3-01): every foreign key in
            # `app.models.staffing` is `NO ACTION`, so the database refuses to empty
            # `staffing_position` while an allocation row still points at one — and refuses to empty
            # `scenarios` while a position does. That refusal is the intended behaviour (deleting a
            # position is out of scope for SC-3-01), which makes this order part of the fixture
            # rather than a detail. The same reasoning as for rates before dimension entries below.
            #
            # The absence rows join the grandchildren (SC-3-02), and the snapshot rows have to go
            # before `scenarios` for the same reason a position does: their only foreign key points
            # there and carries no `ON DELETE` action, deliberately.
            #
            # SC-5-05: the additional costs before everything they point at — a position (the
            # composite `fk_additional_cost_position_same_scenario`), a scenario and a category, all
            # three with no `ON DELETE` action.
            connection.execute(sa.delete(AdditionalCost))
            # SC-6-08: the reserves, then the declared risks - both cost events and reserves point
            # at a risk through a composite foreign key with no `ON DELETE` action (ADR-0021,
            # Q-8 = A), and a risk points at `scenarios` the same way.
            connection.execute(sa.delete(RiskReserve))
            connection.execute(sa.delete(ScenarioRisk))
            connection.execute(sa.delete(StaffingPositionAbsence))
            connection.execute(sa.delete(StaffingPositionAllocation))
            connection.execute(sa.delete(StaffingPosition))
            # SC-2-06: the person register after the positions that point at it —
            # `fk_staffing_position_person_id` has no `ON DELETE` action, deliberately (ADR-0019,
            # point 7: a person assigned to a position cannot be physically deleted).
            connection.execute(sa.delete(Person))
            connection.execute(sa.delete(ApprovedSnapshotWorkingCalendarDay))
            connection.execute(sa.delete(ApprovedSnapshotWorkingCalendar))
            connection.execute(sa.delete(ApprovedSnapshotAbsenceType))
            connection.execute(sa.delete(ApprovedSnapshotAbsenceBudget))
            connection.execute(sa.delete(ApprovedSnapshotOrganizationDefaults))
            connection.execute(sa.delete(ApprovedSnapshotExchangeRate))
            # SC-4-01: the rate snapshot like every snapshot table, and the commercial rule — its
            # details row first, because `tm_terms` points at `commercial_terms` with no `ON
            # DELETE` action, and the rule points at `scenarios` the same way. SC-4-04:
            # `story_points_terms` is the same shape as `tm_terms`, so it goes first too.
            connection.execute(sa.delete(ApprovedSnapshotCatalogDefaultRate))
            # SC-8-01: the history row, before `scenarios`/`projects` for the same reason as every
            # other table above — both of its foreign keys are `ON DELETE RESTRICT`, deliberately
            # (`app.models.audit_log`), so the database refuses to empty either parent while a
            # history row still names it.
            connection.execute(sa.delete(AuditLog))
            connection.execute(sa.delete(TmTerms))
            connection.execute(sa.delete(StoryPointsTerms))
            # SC-4-03: Outcome-based details, before its rule, same reason as `tm_terms`.
            connection.execute(sa.delete(OutcomeTerms))
            connection.execute(sa.delete(FixedPriceAdjustment))
            # SC-4-02: the Fixed Price details row, before its rule for the same reason as
            # `tm_terms` (a composite foreign key with no `ON DELETE` action).
            connection.execute(sa.delete(FixedPriceTerms))
            connection.execute(sa.delete(CommercialTerms))
            # SC-1-11: the delivery segment points at `scenarios` with no `ON DELETE` action too.
            connection.execute(sa.delete(ScenarioDeliverySegment))
            connection.execute(sa.delete(ExchangeRate))
            connection.execute(sa.delete(ProjectAccess))
            connection.execute(sa.delete(Scenario))
            connection.execute(sa.delete(Project))
            # Rates first: the five dictionaries are referenced by foreign keys with no `ON DELETE`
            # action, so the database refuses to empty them while a rate still points at one. That
            # refusal is the intended behaviour (deleting a dimension entry in use is out of scope
            # for SC-2-01), which makes the order here part of the fixture, not a detail.
            connection.execute(sa.delete(CatalogDefaultRate))
            # Budgets before the dictionaries for the same reason as rates: `absence_budget`
            # references `working_calendar` and `catalog_engagement_types` with no `ON DELETE`
            # action, so the database refuses to empty either while a budget still points at one
            # (SC-3-03).
            connection.execute(sa.delete(AbsenceBudget))
            # An absence type is referenced by an absence row, which is already gone above; a
            # calendar is referenced by `catalog_locations.calendar_id`, which is why the locations
            # have to be emptied before the calendars and its days before it.
            connection.execute(sa.delete(AbsenceType))
            for dimension in (
                CatalogRole,
                CatalogSeniority,
                CatalogLocation,
                CatalogEngagementType,
                # Vendors last for the same reason rates come before the dictionaries: a rate row
                # may still reference a vendor, and `fk_catalog_default_rates_vendor_id` carries no
                # `ON DELETE` action (SC-2-03).
                CatalogVendor,
                # SC-5-05: referenced only by `additional_cost.category_id`, emptied above.
                CatalogCostCategory,
            ):
                connection.execute(sa.delete(dimension))
            connection.execute(sa.delete(WorkingCalendarDay))
            connection.execute(sa.delete(WorkingCalendar))
            # The organisation's defaults (SC-1-10): at most one row, referenced by nothing. No
            # migration seeds it (gate 1, P-D), so emptying it restores the migrated state exactly.
            connection.execute(sa.delete(OrganizationDefaults))


def as_caller(user_id: str) -> dict[str, str]:
    """Request headers carrying the placeholder caller identity (ADR-0005, addendum)."""
    return {settings.caller_id_header: user_id}


@contextmanager
def caller_holding(*permissions: Permission, user_id: str = IN_SCOPE_USER) -> Iterator[None]:
    """Run the enclosed requests as `user_id` holding exactly `permissions`, and nothing else.

    The only way to reach a caller with `PERSONNEL_COSTS_READ`, and the only way to reach one
    *without* a permission the placeholder grants: `PLACEHOLDER_PERMISSIONS` is a fixed set, and
    widening it would both trip the set-equality canary and widen ADR-0005's dated deviation
    (addendum 2026-09-19, point 5; addendum "first dataset without project scope", point 6).

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


def make_dimension_tuple(
    session: Session, *, suffix: str = "", calendar: "WorkingCalendar | None" = None
) -> DimensionTuple:
    """Insert one entry in each of the four dictionaries and return their ids. Direct write.

    `suffix` keeps the names unique when a test needs a second, differing entry of every dimension
    (`uq_<table>_name` refuses a repeat).

    `calendar` points the *location* at a working calendar (SC-3-02). **Its default is `None`, and
    that is deliberate**: a location with no calendar is the named `no_calendar` state (criterion
    K-23), it is the state every row created before this task is in, and a fixture that quietly
    attached a calendar to every location would make that state unreachable from most tests — which
    is the state the mutation "treat a missing calendar as 8 hours a day" hides in.
    """
    role = CatalogRole(id=uuid.uuid4(), name=f"Backend Engineer{suffix}")
    seniority = CatalogSeniority(id=uuid.uuid4(), name=f"Senior{suffix}")
    location = CatalogLocation(
        id=uuid.uuid4(),
        name=f"Poland{suffix}",
        calendar_id=None if calendar is None else calendar.id,
    )
    engagement = CatalogEngagementType(id=uuid.uuid4(), name=f"Full-time{suffix}")
    session.add_all([role, seniority, location, engagement])
    session.flush()
    return DimensionTuple(
        role_id=role.id,
        seniority_id=seniority.id,
        location_id=location.id,
        engagement_type_id=engagement.id,
    )


# --- working calendars, absence types and absences (F-05, SC-3-02) ------------------------------

MONDAY_TO_FRIDAY = "1111100"
MONDAY_TO_SATURDAY = "1111110"
"""Week patterns as data, Monday first (`app.models.catalog.WEEK_PATTERN_EXPRESSION`).

`MONDAY_TO_SATURDAY` exists because criterion K-02 requires at least one test calendar whose week
is not Monday-to-Friday: without it, an implementation that read `day.weekday() < 5` instead of the
stored pattern would pass every test in this suite."""

FORBIDDEN_FIXTURE_HOURS = Decimal("8.00")
"""The one value no calendar fixture may carry — criterion K-01's mutation, made unreachable.

The mutation K-01 names is "the calendar read is replaced by a module constant, e.g. 8". If any
fixture calendar had a standard day of exactly eight hours, that mutation would keep some tests
green by coincidence. `make_working_calendar` refuses this value, so the coincidence cannot be
introduced by a future fixture either."""


def make_working_calendar(
    session: Session,
    *,
    name: str = "Poland 2026",
    standard_hours_per_day: Decimal = Decimal("7.50"),
    week_pattern: str = MONDAY_TO_FRIDAY,
) -> "WorkingCalendar":
    """Insert one working calendar directly — no endpoint, no request schema.

    Deliberately bypasses the API: SC-3-02 ships **no** write endpoint for a calendar (ADR-0007,
    addendum 2026-09-22, point 3 — no form), so this fixture is the only way to reach the rows these
    criteria are about, exactly as `make_scenario(status=APPROVED)` was for SC-3-01. That is a limit
    of the proof, named here so it is not mistaken for an equivalence.

    The default standard day is 7.50 hours and never 8.00 — see `FORBIDDEN_FIXTURE_HOURS`.
    """
    if standard_hours_per_day == FORBIDDEN_FIXTURE_HOURS:
        raise ValueError(
            "No calendar fixture may have a standard day of 8.00 hours: it is the value criterion "
            "K-01's mutation (a hard-coded constant instead of the calendar) would use, and a "
            "fixture carrying it would make that mutation survive."
        )
    calendar = WorkingCalendar(
        id=uuid.uuid4(),
        name=name,
        standard_hours_per_day=standard_hours_per_day,
        week_pattern=week_pattern,
    )
    session.add(calendar)
    session.flush()
    return calendar


def make_calendar_day(
    session: Session,
    calendar: "WorkingCalendar",
    *,
    day: date,
    kind: WorkingCalendarDayKind,
) -> WorkingCalendarDay:
    """Insert one exceptional day of one calendar. A holiday or an extra working day (K-02)."""
    row = WorkingCalendarDay(
        id=uuid.uuid4(), calendar_id=calendar.id, day=day, kind=kind
    )
    session.add(row)
    session.flush()
    return row


def make_absence_type(
    session: Session,
    *,
    name: str = "Paid holiday",
    generates_cost: bool = True,
    generates_revenue: bool = False,
    is_statutory_leave: bool = False,
) -> AbsenceType:
    """Insert one absence type directly, with all three flags set independently (K-11, SC-3-03).

    The defaults are the realistic asymmetric case — paid holiday costs money and earns none — so a
    test that does not care about the flags still exercises two *different* values, and an
    implementation aliasing one to the other would be visible rather than hidden behind two equal
    defaults.

    `is_statutory_leave` defaults to `False`, and that default is deliberate in the same way
    `make_dimension_tuple`'s `calendar=None` is: a dictionary with no flagged type is the named
    state of ADR-0008's addendum (2026-09-22 SC-3-03, point 8b), it is the state every row created
    before SC-3-03 is in, and a fixture that quietly flagged every type would make it unreachable
    from most tests — which is the state an implementation guessing "the first type alphabetically"
    hides in.
    """
    entry = AbsenceType(
        id=uuid.uuid4(),
        name=name,
        generates_cost=generates_cost,
        generates_revenue=generates_revenue,
        is_statutory_leave=is_statutory_leave,
    )
    session.add(entry)
    session.flush()
    return entry


# --- absence budgets (F-05, SC-3-03) ------------------------------------------------------------

STATUTORY_LEAVE_TYPE_NAME = "Statutory annual leave"
"""The name every fixture gives the type flagged `is_statutory_leave`, and it is **not** first
alphabetically among the names this suite uses (`Paid holiday`, `Sick leave`, `Training` all sort
before it in at least one test each, and the K-04 fixtures make that explicit).

Criterion K-04's mutation (b) is "pick the type by name instead of by the flag"; a fixture whose
flagged type happened to be the first one alphabetically would let that mutation pass."""

BUDGET_SOURCE = "Staff regulations §12 (2026 edition)"
"""The source text every budget fixture carries: a rule, never a person (ADR-0008, addendum
2026-09-22 SC-3-03, point 6). Spelled once so a test asserting "the source came back whole" and a
test asserting "a blank source is refused" cannot disagree about what a valid one looks like."""


def make_absence_budget(
    session: Session,
    calendar: "WorkingCalendar",
    engagement_type_id: uuid.UUID,
    *,
    budget_days: Decimal,
    effective_from: date,
    effective_to: date,
    source: str = BUDGET_SOURCE,
    unit: str = "day",
) -> AbsenceBudget:
    """Insert one budget window directly — no endpoint, no request schema.

    Deliberately bypasses `AbsenceBudgetCreateRequest`: the constraints under test (the `EXCLUDE`,
    the non-blank source, the closed and month-aligned window, the unit CHECK) are claims about the
    *database*, and a path through Pydantic would prove only that Pydantic refused first (criterion
    K-02's first mutation).

    `budget_days` and the window are required with no defaults, because every criterion that uses
    this fixture varies one of them and a default would make "26 days over twelve months" look like
    a property of the system rather than of the row.

    Flushes rather than commits, so the row lives in the test's transaction; the tests that need a
    committed row (the two-connection races) commit for themselves.
    """
    budget = AbsenceBudget(
        id=uuid.uuid4(),
        calendar_id=calendar.id,
        engagement_type_id=engagement_type_id,
        budget_days=budget_days,
        unit=unit,
        source=source,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    session.add(budget)
    session.flush()
    return budget


def budget_payload(
    calendar_id: uuid.UUID, engagement_type_id: uuid.UUID, **overrides: object
) -> dict[str, object]:
    """A valid `POST /catalog/absence-budgets` body. Days as a string, never a JSON float."""
    body: dict[str, object] = {
        "calendar_id": str(calendar_id),
        "engagement_type_id": str(engagement_type_id),
        "budget_days": "26.00",
        "unit": "day",
        "source": BUDGET_SOURCE,
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
    }
    return body | overrides


def count_absence_budgets(session: Session | sa.Connection) -> int:
    """Budget rows visible to that connection — used to prove a refused write wrote nothing, not
    merely that the response said no."""
    return session.execute(
        sa.select(sa.func.count()).select_from(AbsenceBudget)
    ).scalar_one()


def make_absence(
    session: Session,
    position: StaffingPosition,
    absence_type: AbsenceType,
    *,
    start_date: date,
    end_date: date,
) -> StaffingPositionAbsence:
    """Insert one absence directly — no endpoint, no request schema.

    Bypasses `StaffingAbsenceCreateRequest` for the reason `make_allocation` does: the constraints
    under test (the ordered period, the foreign keys, the *absence* of a person column) are claims
    about the database, and a path through Pydantic would prove only that Pydantic refused first.
    """
    absence = StaffingPositionAbsence(
        id=uuid.uuid4(),
        position_id=position.id,
        absence_type_id=absence_type.id,
        start_date=start_date,
        end_date=end_date,
    )
    session.add(absence)
    session.flush()
    return absence


def absences_path(
    project_id: uuid.UUID, scenario_id: uuid.UUID, position_id: uuid.UUID
) -> str:
    """The nested address of one position's absences — the project id is what carries the scope."""
    return f"{staffing_path(project_id, scenario_id)}/{position_id}/absences"


def absence_path(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    absence_id: uuid.UUID,
) -> str:
    return f"{absences_path(project_id, scenario_id, position_id)}/{absence_id}"


def approve_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    """The address of the approval action (ADR-0004, SC-3-02)."""
    return f"/projects/{project_id}/scenarios/{scenario_id}/approve"


def count_absences(session: Session) -> int:
    """Absence rows visible in the test transaction — used to prove a refused write wrote nothing,
    not merely that the response said no."""
    return session.execute(
        sa.select(sa.func.count()).select_from(StaffingPositionAbsence)
    ).scalar_one()


SNAPSHOT_MODELS = (
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotAbsenceBudget,
    # The fifth, SC-1-10 (ADR-0012, point 6) — the same deliberate canary growth as the fourth.
    ApprovedSnapshotOrganizationDefaults,
    # The sixth, SC-4-01 (ADR-0004, addendum 2026-09-23 SC-4-01, point 2d: the canary "a copy of an
    # approved scenario holds zero snapshot rows" must cover the new table).
    ApprovedSnapshotCatalogDefaultRate,
    ApprovedSnapshotExchangeRate,
)
"""The five snapshot tables, as models — so a test counting "every snapshot row" cannot count some
of the five and look green (criteria K-17, K-18, K-19 of SC-3-02; K-07 of SC-3-03; K-05 of SC-1-10).

The fourth joined in SC-3-03, and this tuple growing is a **deliberate** canary change: ADR-0004's
addendum of 2026-09-22 (SC-3-03, point 4) requires a new snapshot table to be covered by the same
canaries as the three existing ones, "because the registry is silent about omissions"."""


def count_snapshot_rows(connection: sa.Connection | Session, scenario_id: uuid.UUID) -> int:
    """Every `approved_snapshot_*` row belonging to one scenario, across all three tables.

    Takes a `Connection` **or** a `Session`, because the criteria that use it read the state from a
    *separate* connection on purpose: "the transaction under test says it wrote nothing" and
    "nothing was committed" are two different claims, and only the second one is the one K-18 and
    K-19 make.
    """
    return sum(
        connection.execute(
            sa.select(sa.func.count())
            .select_from(model)
            .where(model.scenario_id == scenario_id)
        ).scalar_one()
        for model in SNAPSHOT_MODELS
    )


def count_audit_log_rows(connection: sa.Connection | Session, scenario_id: uuid.UUID) -> int:
    """Every `audit_log` row naming one scenario — the count K-01 and its duplication canary read.

    Takes a `Connection` or a `Session` for the same reason `count_snapshot_rows` does: some
    criteria read committed state from a separate connection, others read it from the same
    transactional session as the write under test."""
    return connection.execute(
        sa.select(sa.func.count()).select_from(AuditLog).where(AuditLog.scenario_id == scenario_id)
    ).scalar_one()


def wait_until_a_lock_request_is_pending(engine: Engine, *, timeout: float = 5.0) -> bool:
    """Block until PostgreSQL reports an ungranted lock request, or give up.

    Asked of `pg_locks` rather than guessed at with a `sleep`: "the other transaction is waiting for
    the scenario row" is a fact the server knows, and a fixed sleep would make the test either slow
    or flaky depending on the machine.

    Returns whether anything was ever observed waiting, so the caller can assert it — a run in which
    nothing blocked proves nothing about serialisation and must not be mistaken for a pass.

    Lives here rather than in one test module because two files now race on the same seam
    (`app.data.scenario_guard`): a child write against an approval
    (`tests/test_staffing_approved_guards.py`, K-20) and an approval against a second approval
    (`tests/test_scenario_approval.py`, R-02). One waiting rule, so a run that silently observed
    nothing cannot be a pass in one file and a failure in the other.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as observer:
            pending = observer.execute(
                sa.text("SELECT count(*) FROM pg_locks WHERE NOT granted")
            ).scalar_one()
        if pending:
            return True
        time.sleep(0.05)
    return False


def make_vendor(session: Session, *, name: str = "Contoso Sp. z o.o.") -> CatalogVendor:
    """Insert one vendor dictionary entry directly — no endpoint, no request schema (SC-2-03).

    A vendor is a company and nothing else on this table: an id and a name, exactly like the other
    four dictionaries. Tests that need two vendors pass two names, because the normalised-name index
    refuses a repeat.
    """
    vendor = CatalogVendor(id=uuid.uuid4(), name=name)
    session.add(vendor)
    session.flush()
    return vendor


def make_rate(
    session: Session,
    dimensions: DimensionTuple,
    *,
    effective_from: date,
    effective_to: date | None = None,
    vendor_id: uuid.UUID | None = None,
    default_cost_rate: Decimal = Decimal("100.0000"),
    default_selling_rate: Decimal = Decimal("150.0000"),
    currency: str = "EUR",
    unit: str = "hour",
    surcharge_percent: Decimal = Decimal("0"),
    includes_surcharge: bool = False,
    cost_rate_unit: str = "hour",
) -> CatalogDefaultRate:
    """Insert one rate row directly — no endpoint, no request schema.

    Deliberately bypasses `CatalogRateCreateRequest`: the constraints under test (the `EXCLUDE`, the
    unit CHECK) are claims about the *database*, and a path that went through Pydantic would prove
    only that Pydantic refused first. `unit` is a parameter for the same reason.

    Flushes rather than commits, so the row lives in the test's transaction; the tests that need a
    committed row (the two-connection race) commit for themselves.

    `vendor_id` defaults to `None`, i.e. an internal rate — the same default the production write
    path has, and the same meaning the column carries (SC-2-03).
    """
    rate = CatalogDefaultRate(
        id=uuid.uuid4(),
        role_id=dimensions.role_id,
        seniority_id=dimensions.seniority_id,
        location_id=dimensions.location_id,
        engagement_type_id=dimensions.engagement_type_id,
        vendor_id=vendor_id,
        default_cost_rate=default_cost_rate,
        default_selling_rate=default_selling_rate,
        currency=currency,
        unit=unit,
        effective_from=effective_from,
        effective_to=effective_to,
        surcharge_percent=surcharge_percent,
        includes_surcharge=includes_surcharge,
        cost_rate_unit=cost_rate_unit,
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
    overload_threshold_percent: Decimal | None = None,
) -> Scenario:
    """Insert a scenario row directly. Anything left at `None` is a missing input by design.

    `overload_threshold_percent` (SC-1-10) is the scenario level of the assumption chain, and like
    `target_margin_percent` it is written here and nowhere else: no endpoint creates or edits a
    scenario (SC-1-10, out of scope), so the scenario-level override is fixture-only.
    """
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
        overload_threshold_percent=overload_threshold_percent,
    )
    session.add(scenario)
    session.flush()
    return scenario


# --- scenario staffing (SC-3-01) ----------------------------------------------------------------
#
# `status=ScenarioStatus.APPROVED` on `make_scenario` above is the **only** way to reach an approved
# scenario anywhere in this suite, and it is a direct database write on purpose: no production path
# sets that status (the approval transition and the endpoint that would create a scenario are both
# out of scope — plan entry SC-3-01, "fundament nieudowodniony", points 1-2). Every proof about the
# refusal of a write to an approved scenario therefore stands on this fixture rather than on a full
# cycle through the running system. That is a limit of the proof, named here so it is not mistaken
# for an equivalence.


def make_staffing_position(
    session: Session,
    scenario: Scenario,
    dimensions: "DimensionTuple",
    *,
    headcount: int = 2,
    start_date: date = date(2026, 3, 1),
    end_date: date | None = None,
    cost_basis: str = "worked_time",
    fixed_amount: Decimal | None = None,
    fixed_amount_currency: str | None = None,
    assigned_fte: Decimal | None = None,
) -> StaffingPosition:
    """Insert one staffing position directly — no endpoint, no request schema.

    Deliberately bypasses `StaffingPositionCreateRequest`: the constraints under test (the month
    CHECK, the unique constraint, the non-negativity of hours, the foreign keys) are claims about
    the *database*, and a path that went through Pydantic would prove only that Pydantic refused
    first (criterion K-04).

    Flushes rather than commits, so the row lives in the test's transaction; the tests that need a
    committed row commit for themselves.
    """
    position = StaffingPosition(
        id=uuid.uuid4(),
        scenario_id=scenario.id,
        role_id=dimensions.role_id,
        seniority_id=dimensions.seniority_id,
        location_id=dimensions.location_id,
        engagement_type_id=dimensions.engagement_type_id,
        headcount=headcount,
        start_date=start_date,
        end_date=end_date,
        cost_basis=cost_basis,
        fixed_amount=fixed_amount,
        fixed_amount_currency=fixed_amount_currency,
        assigned_fte=assigned_fte,
    )
    session.add(position)
    session.flush()
    return position


FICTITIOUS_PERSON_NAME = "Testowa Osoba-Fikcyjna"
"""The default name of every person fixture — **fictitious by construction** (ADR-0019, point 8:
fictitious data only, in every database and fixture, until the deletion Story and the other
conditions are met). Distinctive enough that `not in response.text` cannot pass by coincidence."""


def make_person(session: Session, *, full_name: str = FICTITIOUS_PERSON_NAME) -> Person:
    """Insert one person into the register directly — no endpoint, no request schema (SC-2-06).

    The only way to reach a person in the running system's shape: the placeholder identity holds no
    `PEOPLE_WRITE` (ADR-0005, addendum 2026-09-27, point 4), so no request can create one without
    `dependency_overrides`. Flushes rather than commits, like every fixture here."""
    person = Person(id=uuid.uuid4(), full_name=full_name)
    session.add(person)
    session.flush()
    return person


def assign_person_directly(
    session: Session, position: StaffingPosition, person: Person | None
) -> None:
    """Set `staffing_position.person_id` with a direct write — bypassing the API and its guards.

    For tests whose subject is *reading* an assignment (visibility, copying, calculations), so that
    the fixture does not depend on the write path it is not about. The write path itself is tested
    through the endpoint. Still subject to the database's own rules (`headcount = 1`, the foreign
    key), which is the point of having them there."""
    position.person_id = None if person is None else person.id
    session.flush()


def count_people(session: Session | sa.Connection) -> int:
    """Rows of the person register — to prove a refused write wrote nothing, and a copy copied no
    person."""
    return session.execute(sa.select(sa.func.count()).select_from(Person)).scalar_one()


def make_allocation(
    session: Session,
    position: StaffingPosition,
    *,
    period_month: date,
    availability_hours: Decimal = Decimal("160.00"),
    planned_allocation_hours: Decimal = Decimal("120.00"),
    billable_hours: Decimal = Decimal("100.00"),
) -> StaffingPositionAllocation:
    """Insert one month of one position's grid directly.

    The three defaults are three *different* values on purpose: a fixture whose hours were all equal
    would let an aliasing defect (`billable := planned`) pass unnoticed in every test that used it,
    which is the mutation criterion K-05 exists to kill.
    """
    allocation = StaffingPositionAllocation(
        id=uuid.uuid4(),
        position_id=position.id,
        period_month=period_month,
        availability_hours=availability_hours,
        planned_allocation_hours=planned_allocation_hours,
        billable_hours=billable_hours,
    )
    session.add(allocation)
    session.flush()
    return allocation


def staffing_position_payload(
    dimensions: "DimensionTuple", **overrides: object
) -> dict[str, object]:
    """A valid `POST …/staffing-positions` body: hours as strings, never JSON floats (ADR-0002).

    Kept here so the access-control tests and the criterion tests send the *same* request and a
    refusal can never be an accident of a malformed body.
    """
    body: dict[str, object] = {
        "role_id": str(dimensions.role_id),
        "seniority_id": str(dimensions.seniority_id),
        "location_id": str(dimensions.location_id),
        "engagement_type_id": str(dimensions.engagement_type_id),
        "headcount": 2,
        "start_date": "2026-03-01",
        "end_date": "2026-04-30",
        "allocations": [
            {
                "period_month": "2026-03-01",
                "availability_hours": "160.00",
                "planned_allocation_hours": "120.00",
                "billable_hours": "100.00",
            }
        ],
    }
    return body | overrides


def staffing_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    """The nested address of a scenario's staffing (ADR-0001, addendum 2026-09-19).

    Spelled once: the scope of this path comes from `project_id` via `project_for_caller`, so a test
    that built the URL without it would be testing a different endpoint from the one that exists.
    """
    return f"/projects/{project_id}/scenarios/{scenario_id}/staffing-positions"


def allocation_path(
    project_id: uuid.UUID, scenario_id: uuid.UUID, position_id: uuid.UUID, period_month: date
) -> str:
    return (
        f"{staffing_path(project_id, scenario_id)}/{position_id}"
        f"/allocations/{period_month.isoformat()}"
    )


def count_positions(session: Session) -> int:
    """Position rows visible in the test transaction — used to prove a refused write wrote nothing,
    not merely that the response said no."""
    return session.execute(sa.select(sa.func.count()).select_from(StaffingPosition)).scalar_one()


def count_allocations(session: Session) -> int:
    return session.execute(
        sa.select(sa.func.count()).select_from(StaffingPositionAllocation)
    ).scalar_one()


# --- the assumption chain (F-02, SC-1-10) -------------------------------------------------------


def set_organization_defaults(
    session: Session | sa.Connection,
    *,
    target_margin_percent: Decimal | None = None,
    overload_threshold_percent: Decimal | None = None,
) -> None:
    """Write the organisation's one defaults row — insert it, or overwrite the one that exists.

    Direct write, no endpoint: SC-1-10 ships no path that edits the organisation's defaults (out of
    scope, named at gate 1), so this is the only way to reach the row. An upsert on the pinned key
    rather than an `INSERT`, because the criteria *change* the default after an approval (K-05) and
    the table admits exactly one row.

    Takes a `Connection` too, because the AC-04 criteria change the default on a separate, committed
    connection and then read the approved scenario back.
    """
    values = {
        "id": 1,
        "target_margin_percent": target_margin_percent,
        "overload_threshold_percent": overload_threshold_percent,
    }
    statement = pg_insert(OrganizationDefaults).values(**values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[OrganizationDefaults.id],
            set_={
                "target_margin_percent": statement.excluded.target_margin_percent,
                "overload_threshold_percent": statement.excluded.overload_threshold_percent,
            },
        )
    )
    if isinstance(session, Session):
        session.flush()


def set_project_overrides(
    session: Session | sa.Connection,
    project_id: uuid.UUID,
    *,
    target_margin_percent: Decimal | None = None,
    overload_threshold_percent: Decimal | None = None,
) -> None:
    """Set (or clear, with `None`) both project-level overrides directly — a fixture write.

    The production path is `PATCH /projects/{id}` (criterion K-07), and the criteria about *that*
    path use it. This exists for the criteria about the resolution rule, which must not depend on
    the edit endpoint being green.
    """
    session.execute(
        sa.update(Project)
        .where(Project.id == project_id)
        .values(
            target_margin_percent=target_margin_percent,
            overload_threshold_percent=overload_threshold_percent,
        )
    )
    if isinstance(session, Session):
        session.flush()
        session.expire_all()


def assumptions_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    """The address of one scenario's resolved assumptions — the reader of SC-1-10 (gate 1, P-B)."""
    return f"/projects/{project_id}/scenarios/{scenario_id}/assumptions"


# --- the commercial rule and its revenue (F-06.1, SC-4-01) ---------------------------------------


def commercial_terms_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    """The address of one scenario's commercial rule and revenue — the project id carries the
    scope."""
    return f"/projects/{project_id}/scenarios/{scenario_id}/commercial-terms"


def make_commercial_terms(
    session: Session,
    scenario: Scenario,
    *,
    with_details: bool = True,
    scope_ref: uuid.UUID | None = None,
) -> CommercialTerms:
    """Insert a T&M rule directly — and, unless told otherwise, its `tm_terms` row.

    `with_details=False` is the only way to reach the named `incomplete_commercial_terms` state: the
    production write path creates both rows in one statement, and the database enforces the *type*
    of a details row, not its existence (ADR-0003, point 3).

    `scope_ref` (SC-4-05) defaults to `None` — a whole-scenario rule, the only shape before this
    task. A `ScenarioDeliverySegment.id` of the *same* scenario scopes the rule to that segment.
    """
    terms = CommercialTerms(
        id=uuid.uuid4(),
        scenario_id=scenario.id,
        model_type="time_and_material",
        scope_ref=scope_ref,
    )
    session.add(terms)
    session.flush()
    if with_details:
        session.add(TmTerms(commercial_terms_id=terms.id, model_type="time_and_material"))
        session.flush()
    return terms


def make_story_points_terms(
    session: Session,
    scenario: Scenario,
    *,
    with_details: bool = True,
    price_per_point: Decimal = Decimal("1000.0000"),
    accepted_points: int = 25,
    currency: str = "PLN",
    scope_ref: uuid.UUID | None = None,
) -> CommercialTerms:
    """Insert a Story Points rule directly — and, unless told otherwise, its `story_points_terms`
    row (SC-4-04). The counterpart of `make_commercial_terms` for the second real commercial model
    (criterion K-03): a test creating both in the same database calls one of each, never a
    `monkeypatch` of the registries.

    `scope_ref` (SC-4-05) — see `make_commercial_terms`.
    """
    terms = CommercialTerms(
        id=uuid.uuid4(), scenario_id=scenario.id, model_type="story_points", scope_ref=scope_ref
    )
    session.add(terms)
    session.flush()
    if with_details:
        session.add(
            StoryPointsTerms(
                commercial_terms_id=terms.id,
                model_type="story_points",
                price_per_point=price_per_point,
                accepted_points=accepted_points,
                currency=currency,
            )
        )
        session.flush()
    return terms


def make_outcome_terms(
    session: Session,
    scenario: Scenario,
    *,
    with_details: bool = True,
    scope_ref: uuid.UUID | None = None,
    **details: object,
) -> CommercialTerms:
    """Insert an Outcome-based rule directly (SC-4-03) — and, unless stated otherwise, its
    `outcome_terms` row.

    Defaults to AC-08 with no probabilities: a fixed fee of 20000 PLN, a success bonus of 10000 PLN,
    zero units in every category. `details` overrides the details row's columns by name. A write
    that bypasses the API — the production path creates both rows in one guarded statement.

    `scope_ref` (SC-4-05) — as in `make_commercial_terms`: `None` is a whole-scenario rule.
    """
    terms = CommercialTerms(
        id=uuid.uuid4(), scenario_id=scenario.id, model_type="outcome_based", scope_ref=scope_ref
    )
    session.add(terms)
    session.flush()
    if with_details:
        values: dict[str, object] = {
            "currency": "PLN",
            "fixed_fee": Decimal("20000"),
            "success_bonus": Decimal("10000"),
            "not_achieved_units": Decimal("0"),
            "partial_units": Decimal("0"),
            "achieved_units": Decimal("0"),
            "exceeded_units": Decimal("0"),
        }
        values.update(details)
        session.add(OutcomeTerms(commercial_terms_id=terms.id, **values))
        session.flush()
    return terms


def outcome_payload(
    *,
    probabilities: tuple[str | None, str | None, str | None, str | None] | None = None,
    units: tuple[str | None, str | None, str | None, str | None] = ("0", "0", "0", "0"),
    **overrides: object,
) -> dict[str, object]:
    """The `POST …/commercial-terms` body for Outcome-based (SC-4-03) — defaults to AC-08 with no
    probabilities: a fixed fee of 20000 PLN, a success bonus of 10000 PLN.

    `probabilities` and `units` go in the order `not_achieved`, `partial`, `achieved`, `exceeded`;
    `None` in `probabilities` and in `units` omits the category's field (does not send `0`). Amounts
    as strings — the way the API
    returns them, with no pass through `float`.
    """
    categories: dict[str, dict[str, object]] = {}
    for index, category in enumerate(("not_achieved", "partial", "achieved", "exceeded")):
        entry: dict[str, object] = {}
        if units[index] is not None:
            entry["units"] = units[index]
        if probabilities is not None and probabilities[index] is not None:
            entry["probability"] = probabilities[index]
        categories[category] = entry
    payload: dict[str, object] = {
        "model_type": "outcome_based",
        "currency": "PLN",
        "fixed_fee": "20000",
        "success_bonus": "10000",
        "categories": categories,
    }
    payload.update(overrides)
    return payload


def count_outcome_rows(connection: sa.Connection | Session) -> tuple[int, int]:
    """(rules, `outcome_terms` rows) across the whole database — for "zero rows" proofs."""
    rules = connection.execute(
        sa.select(sa.func.count()).select_from(CommercialTerms)
    ).scalar_one()
    details = connection.execute(sa.select(sa.func.count()).select_from(OutcomeTerms)).scalar_one()
    return rules, details


# --- scenario delivery segments (F-02, F-06; SC-1-11, ADR-0016) ---------------------------------


def make_scenario_delivery_segment(
    session: Session, scenario: Scenario, *, name: str = "Phase 1"
) -> ScenarioDeliverySegment:
    """Insert one delivery segment directly — no endpoint, no request schema (ADR-0016, point 8):
    the constraints under test are claims about the database."""
    segment = ScenarioDeliverySegment(id=uuid.uuid4(), scenario_id=scenario.id, name=name)
    session.add(segment)
    session.flush()
    return segment


def count_scenario_delivery_segments(session: Session) -> int:
    """Segment rows visible in the test transaction — used to prove a refused write wrote nothing,
    not merely that the caller was told no."""
    return session.execute(
        sa.select(sa.func.count()).select_from(ScenarioDeliverySegment)
    ).scalar_one()


def make_fixed_price_terms(
    session: Session,
    scenario: Scenario,
    *,
    agreed_price: Decimal | None = Decimal("150000.0000"),
    currency: str = "PLN",
    scope_ref: uuid.UUID | None = None,
) -> CommercialTerms:
    """Insert a Fixed Price rule directly — and, unless `agreed_price=None`, its price row
    (SC-4-02).

    `agreed_price=None` is the only way to reach the Fixed Price `incomplete_commercial_terms`
    state: the production write path creates both rows in one statement, and the API refuses a
    Fixed Price rule without a price (`422`). `scope_ref` (SC-4-05) — see `make_commercial_terms`.
    """
    terms = CommercialTerms(
        id=uuid.uuid4(), scenario_id=scenario.id, model_type="fixed_price", scope_ref=scope_ref
    )
    session.add(terms)
    session.flush()
    if agreed_price is not None:
        session.add(
            FixedPriceTerms(
                commercial_terms_id=terms.id,
                model_type="fixed_price",
                agreed_price=agreed_price,
                currency=currency,
            )
        )
        session.flush()
    return terms


# --- additional costs (F-08, SC-5-05) -----------------------------------------------------------


def additional_costs_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    """The address of one scenario's additional costs — the project id carries the scope."""
    return f"/projects/{project_id}/scenarios/{scenario_id}/additional-costs"


def additional_cost_path(
    project_id: uuid.UUID, scenario_id: uuid.UUID, cost_id: uuid.UUID
) -> str:
    return f"{additional_costs_path(project_id, scenario_id)}/{cost_id}"


def make_cost_category(session: Session, *, name: str = "Licences") -> CatalogCostCategory:
    """Insert one cost category directly — the migration seeds none (ADR-0014, point 2)."""
    category = CatalogCostCategory(id=uuid.uuid4(), name=name)
    session.add(category)
    session.flush()
    return category


def make_additional_cost(
    session: Session,
    scenario: Scenario,
    category: CatalogCostCategory,
    *,
    amount: Decimal,
    start_month: date,
    end_month: date | None = None,
    cost_type: str | None = None,
    position: StaffingPosition | None = None,
    currency: str = "EUR",
    funding_source: str = "internal",
) -> AdditionalCost:
    """Insert one additional cost directly — no endpoint, no request schema.

    Bypasses `AdditionalCostCreateRequest` for the reason `make_allocation` does: the constraints
    under test are claims about the *database*. `cost_type` defaults from the shape of the period —
    `one_off` without an end month, `recurring` with one — so a test states the period and the type
    follows, unless the test is about the two disagreeing.
    """
    cost = AdditionalCost(
        id=uuid.uuid4(),
        scenario_id=scenario.id,
        position_id=None if position is None else position.id,
        category_id=category.id,
        amount=amount,
        currency=currency,
        cost_type=cost_type or ("one_off" if end_month is None else "recurring"),
        start_month=start_month,
        end_month=end_month,
        funding_source=funding_source,
    )
    session.add(cost)
    session.flush()
    return cost


def additional_cost_payload(category_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    """A valid `POST …/additional-costs` body: a one-off scenario-level cost, amount as a string."""
    body: dict[str, object] = {
        "category_id": str(category_id),
        "amount": "1200.0000",
        "currency": "EUR",
        "cost_type": "one_off",
        "start_month": "2026-03-01",
        "funding_source": "internal",
    }
    return body | overrides


def count_additional_costs(session: Session | sa.Connection) -> int:
    """Cost rows visible to that connection — used to prove a refused write wrote nothing."""
    return session.execute(
        sa.select(sa.func.count()).select_from(AdditionalCost)
    ).scalar_one()


# --- risks and reserves (F-09 pt 4-5, SC-6-08) --------------------------------------------------


def risks_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    """The address of one scenario's declared risks - the project id carries the scope."""
    return f"/projects/{project_id}/scenarios/{scenario_id}/risks"


def risk_path(project_id: uuid.UUID, scenario_id: uuid.UUID, risk_id: uuid.UUID) -> str:
    return f"{risks_path(project_id, scenario_id)}/{risk_id}"


def reserves_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/risk-reserves"


def reserve_path(project_id: uuid.UUID, scenario_id: uuid.UUID, reserve_id: uuid.UUID) -> str:
    return f"{reserves_path(project_id, scenario_id)}/{reserve_id}"


def make_risk(session: Session, scenario: Scenario, *, name: str = "Vendor delay") -> ScenarioRisk:
    """Insert one declared risk directly - no endpoint, no request schema: the constraints under
    test are claims about the database."""
    risk = ScenarioRisk(id=uuid.uuid4(), scenario_id=scenario.id, name=name)
    session.add(risk)
    session.flush()
    return risk


def make_reserve(
    session: Session,
    scenario: Scenario,
    *,
    amount: Decimal,
    start_month: date,
    end_month: date | None = None,
    reserve_type: str | None = None,
    risk: ScenarioRisk | None = None,
    currency: str = "EUR",
) -> RiskReserve:
    """Insert one reserve directly. `reserve_type` follows the period's shape unless the test is
    about the two disagreeing (the `make_additional_cost` convention)."""
    reserve = RiskReserve(
        id=uuid.uuid4(),
        scenario_id=scenario.id,
        risk_id=None if risk is None else risk.id,
        amount=amount,
        currency=currency,
        reserve_type=reserve_type or ("one_off" if end_month is None else "recurring"),
        start_month=start_month,
        end_month=end_month,
    )
    session.add(reserve)
    session.flush()
    return reserve


def link_cost_to_risk(session: Session, cost: AdditionalCost, risk: ScenarioRisk | None) -> None:
    """Point an existing cost event at a risk (or unlink it) by a direct write - the linking
    mechanism the K-01/K-02 contrasts use without going through the endpoint under test."""
    session.execute(
        sa.update(AdditionalCost)
        .where(AdditionalCost.id == cost.id)
        .values(risk_id=None if risk is None else risk.id)
    )
    session.flush()
    session.expire(cost)


def count_reserves(session: Session | sa.Connection) -> int:
    """Reserve rows visible to that connection - proves a refused write wrote nothing."""
    return session.execute(sa.select(sa.func.count()).select_from(RiskReserve)).scalar_one()


def count_risks(session: Session | sa.Connection) -> int:
    """Risk rows visible to that connection - proves a refused write wrote nothing."""
    return session.execute(sa.select(sa.func.count()).select_from(ScenarioRisk)).scalar_one()
