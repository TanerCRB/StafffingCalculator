"""Seed synthetic data into the local dev database — Issue #145.

A one-off local-dev convenience, **not** an application module: nothing under `app/` imports this
file, it is not wired into `app.main`, and it must never run as part of `pytest` or CI. Its only
job is to make the empty local dev database look like a system somebody has used, so the screens
that have no create/edit endpoint at all (Working calendars) or read a lot of rows to be useful
(Compare scenarios, Staffing plan) can be checked by eye instead of staring at an empty state.

Run it from `backend/`, against the database `APP_DATABASE_URL` names (see `backend/.env`;
`backend/.env.example` documents the variable)::

    python -m scripts.seed_dev_data --caller-user-id dev-anna

**Why a caller id is mandatory, and why there is no default (K-01).** F-13's project scope
(`app.data.project_reads.accessible_projects`) is a `project_access` row keyed by `user_id`, and the
running application reads that id from the `X-Caller-User-Id` header the frontend sends — the same
value as `VITE_CALLER_USER_ID` in `frontend/.env` (`frontend/src/api/client.ts`). A seed script that
granted access to an arbitrary or random id instead of that real value would insert rows nobody
running the frontend could ever see: `GET /projects` would return an empty list under the caller
identity that matters, which is *indistinguishable from the empty-database symptom this script
exists to fix* — the exact regression this module's tests guard against by mutation. So this script
never invents an identity: it reads the same value the frontend reads (`resolve_caller_user_id`
below), from the same places, and refuses to write a single row when it cannot find one.

**Why direct SQLAlchemy writes, and not `httpx` calls to a running server.** `working_calendar`,
`working_calendar_day` and `catalog_locations.calendar_id` have no creating endpoint at all — an
explicit, dated decision (`app.api.catalog`, module docstring: "a calendar, its days and an absence
type are created by a migration, a seed script or an import"). Using SQLAlchemy models directly for
everything else too keeps one mechanism in this file instead of two, and mirrors the pattern
`backend/tests/conftest.py`'s `make_*` fixtures already use for the same reason.

**Idempotency (K-02).** Every entity this script writes is looked up by its natural key before an
insert is attempted — a dictionary by its name, a calendar by its name, a calendar day by
`(calendar_id, day)`, a project by its name, a scenario by `(project_id, name)`, a rate window by
`(dimension tuple, vendor, effective_from)`, a budget window by `(calendar, engagement type,
effective_from)`, a staffing position by `(scenario, dimension tuple, start_date)`.
`working_calendar` does carry a database-level unique index on its normalised name
(`uq_working_calendar_name_normalized`), which this script's fixed names already satisfy on a
second run — but that index only guards against corruption, it does not make a second run a no-op:
without the lookup-before-insert here, a second run would crash on `IntegrityError` instead of
skipping cleanly.

**What this script deliberately does not do:** decide who may run it (that is
`assert_identity_mechanism_allowed`, reused rather than re-implemented, below), touch a
`Fixed Price` commercial model (F-10 names it, nothing in `backend/app/models` implements it), or
write anything that resembles real business data — every label below carries a `(seed)` marker and
no client name, amount or person is realistic.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.api.deps import PlaceholderIdentityNotAllowedError, assert_identity_mechanism_allowed
from app.core.config import settings
from app.core.identity import CallerIdentity
from app.data.absence_budget import create_budget
from app.data.catalog import create_dimension_entry, create_rate
from app.data.commercial_terms import create_commercial_terms
from app.data.project_writes import create_project
from app.data.scenario_approval import approve_scenario
from app.data.staffing import create_position
from app.db.session import get_sessionmaker
from app.models import (
    CatalogDefaultRate,
    CatalogEngagementType,
    CatalogLocation,
    CatalogRole,
    CatalogSeniority,
    CommercialTerms,
    Project,
    ProjectAccess,
    Scenario,
    ScenarioStatus,
    StaffingPosition,
    WorkingCalendar,
    WorkingCalendarDay,
    WorkingCalendarDayKind,
)
from app.models.catalog import RATE_UNIT_HOUR, AbsenceBudget, AbsenceType

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRONTEND_ENV_PATH = REPO_ROOT / "frontend" / ".env"
"""Where the frontend keeps `VITE_CALLER_USER_ID` locally (git-ignored, one per developer
machine) — `frontend/.env.example` documents the variable, never a value."""


class MissingCallerUserIdError(RuntimeError):
    """No caller id was given on the command line, in the environment, or in `frontend/.env`.

    Raised instead of falling back to a default: an empty or invented identity is precisely the
    failure this script's own docstring (K-01) exists to prevent — it would seed rows nobody
    running the frontend can see, silently, and the symptom would look identical to the empty
    database this script is meant to fix.
    """


def _read_env_file_value(env_path: Path, key: str) -> str | None:
    """`KEY=value` out of a `.env`-shaped file — quoted or not, comments and blanks ignored.

    Deliberately not `python-dotenv`: `backend/pyproject.toml` does not depend on it (the backend
    reads `.env` through `pydantic-settings`, which parses it internally), and adding a dependency
    to this one script for four lines of parsing is not worth a second package to pin.
    """
    if not env_path.is_file():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return None


def resolve_caller_user_id(
    cli_value: str | None,
    *,
    env: Mapping[str, str] | None = None,
    frontend_env_path: Path | None = None,
) -> str:
    """The caller id to seed under — CLI argument, then `VITE_CALLER_USER_ID`, then
    `frontend/.env`'s copy of it — or a refusal.

    The three sources are read in the order a developer would plausibly set them, but the point is
    not the order: it is that **every** source is the same identity the frontend itself would send
    (`frontend/src/api/client.ts`, `VITE_CALLER_USER_ID`), never a value this script invents. An
    empty string is treated exactly like a missing one — `VITE_CALLER_USER_ID=` with nothing after
    the `=` is `frontend/.env.example`'s own committed shape, and reading it as "configured" would
    seed under the empty-string identity, which is not a user anybody can send a header as.
    """
    active_env = os.environ if env is None else env
    candidates: list[str | None] = [cli_value, active_env.get("VITE_CALLER_USER_ID")]
    if frontend_env_path is not None:
        candidates.append(_read_env_file_value(frontend_env_path, "VITE_CALLER_USER_ID"))
    for candidate in candidates:
        if candidate is not None and candidate.strip():
            return candidate.strip()
    raise MissingCallerUserIdError(
        "No caller identity was given. Pass --caller-user-id, or set VITE_CALLER_USER_ID "
        "(the same variable frontend/.env uses), or put it in frontend/.env directly. Refusing to "
        "seed under an invented or empty identity — see this module's docstring (K-01)."
    )


# --- synthetic labels — every one of them carries "(seed)" and no realistic business data --------

PROJECT_NAME = "Seed Project Alpha (seed)"
ROLE_NAME = "Seed Role Backend Developer"
SENIORITY_NAME = "Seed Seniority Senior"
ENGAGEMENT_TYPE_NAME = "Seed Engagement Type B2B"
LOCATION_WARSAW_NAME = "Seed Location Warsaw"
LOCATION_KRAKOW_NAME = "Seed Location Krakow"
CALENDAR_WARSAW_NAME = "Seed Calendar PL-Warsaw"
CALENDAR_KRAKOW_NAME = "Seed Calendar PL-Krakow"
SCENARIO_TM_DRAFT_NAME = "Seed Scenario TM Draft"
SCENARIO_SP_DRAFT_NAME = "Seed Scenario Story Points Draft"
SCENARIO_TM_APPROVED_NAME = "Seed Scenario TM Approved"
ABSENCE_TYPE_STATUTORY_NAME = "Seed Absence Type Statutory Leave"
"""Flagged `is_statutory_leave=True` — required for a seeded `absence_budget` window to ever reach
the `"resolved"` state (`app.domain.absence_budget.month_budget_share`): with no absence type
carrying the flag, a budget row that covers the month still answers `"no_statutory_leave_type"`,
never `"resolved"`. Without this row K-04's "resolved" branch would be unreachable from seeded
data no matter how the budget window itself is set up."""

MONDAY_TO_FRIDAY = "1111100"
MONDAY_TO_SATURDAY = "1111110"
"""Week patterns, Monday first — the same convention `app.models.catalog.WEEK_PATTERN_EXPRESSION`
and `tests/conftest.py` use. The two calendars below deliberately differ on this string (K-06):
Warsaw works five days, Krakow six, so `working_days_in_month` — and with it
`derived_capacity_hours` — cannot come out equal by coincidence."""


@dataclass(frozen=True)
class _Tally:
    """How many rows this run inserted versus found already there, per kind of thing seeded."""

    created: Counter[str] = field(default_factory=Counter)
    existing: Counter[str] = field(default_factory=Counter)

    def record(self, label: str, *, created: bool) -> None:
        (self.created if created else self.existing)[label] += 1


@dataclass(frozen=True)
class SeedSummary:
    """What one run of `run_seed` did — enough to print, and enough for a test to assert on."""

    caller_user_id: str
    project_id: object
    scenario_ids: Mapping[str, object]
    tally: _Tally

    def describe(self) -> str:
        lines = [
            f"Seeded under caller identity: {self.caller_user_id!r}",
            f"Project: {self.project_id}",
        ]
        for label, count in sorted(self.tally.created.items()):
            lines.append(f"  created {count:>2} x {label}")
        for label, count in sorted(self.tally.existing.items()):
            lines.append(f"  already present {count:>2} x {label}")
        return "\n".join(lines)


# --- idempotent "get or create" helpers, one per entity this script writes -----------------------
#
# Each one looks the row up by its natural key first (K-02) and only then calls the data-layer
# function (or, for the two tables with no creating endpoint at all, builds the row directly) — the
# existence check is what makes a second run of *this script* a no-op; it is not a substitute for a
# database constraint and is never used where one exists to race against (this script runs
# single-threaded, once, by a developer).


def _get_or_create_dimension(session: Session, model: type, *, name: str) -> tuple[object, bool]:
    existing = session.execute(sa.select(model).where(model.name == name)).scalars().one_or_none()
    if existing is not None:
        return existing, False
    return create_dimension_entry(session, model, name=name), True


def _get_or_create_location(
    session: Session, *, name: str, calendar_id: object
) -> tuple[CatalogLocation, bool]:
    existing = (
        session.execute(sa.select(CatalogLocation).where(CatalogLocation.name == name))
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    location = CatalogLocation(id=uuid.uuid4(), name=name, calendar_id=calendar_id)
    session.add(location)
    session.commit()
    return location, True


def _get_or_create_working_calendar(
    session: Session, *, name: str, standard_hours_per_day: Decimal, week_pattern: str
) -> tuple[WorkingCalendar, bool]:
    existing = (
        session.execute(sa.select(WorkingCalendar).where(WorkingCalendar.name == name))
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    calendar = WorkingCalendar(
        id=uuid.uuid4(),
        name=name,
        standard_hours_per_day=standard_hours_per_day,
        week_pattern=week_pattern,
    )
    session.add(calendar)
    session.commit()
    return calendar, True


def _ensure_calendar_day(
    session: Session, calendar: WorkingCalendar, *, day: date, kind: WorkingCalendarDayKind
) -> tuple[WorkingCalendarDay, bool]:
    existing = (
        session.execute(
            sa.select(WorkingCalendarDay).where(
                WorkingCalendarDay.calendar_id == calendar.id, WorkingCalendarDay.day == day
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    row = WorkingCalendarDay(id=uuid.uuid4(), calendar_id=calendar.id, day=day, kind=kind)
    session.add(row)
    session.commit()
    return row, True


def _get_or_create_absence_type(
    session: Session,
    *,
    name: str,
    generates_cost: bool,
    generates_revenue: bool,
    is_statutory_leave: bool,
) -> tuple[AbsenceType, bool]:
    existing = (
        session.execute(sa.select(AbsenceType).where(AbsenceType.name == name))
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    absence_type = AbsenceType(
        id=uuid.uuid4(),
        name=name,
        generates_cost=generates_cost,
        generates_revenue=generates_revenue,
        is_statutory_leave=is_statutory_leave,
    )
    session.add(absence_type)
    session.commit()
    return absence_type, True


def _get_or_create_rate(
    session: Session,
    *,
    role_id: object,
    seniority_id: object,
    location_id: object,
    engagement_type_id: object,
    vendor_id: object | None,
    effective_from: date,
    effective_to: date | None,
    default_cost_rate: Decimal,
    default_selling_rate: Decimal,
    currency: str,
) -> tuple[CatalogDefaultRate, bool]:
    vendor_condition = (
        CatalogDefaultRate.vendor_id.is_(None)
        if vendor_id is None
        else CatalogDefaultRate.vendor_id == vendor_id
    )
    existing = (
        session.execute(
            sa.select(CatalogDefaultRate).where(
                CatalogDefaultRate.role_id == role_id,
                CatalogDefaultRate.seniority_id == seniority_id,
                CatalogDefaultRate.location_id == location_id,
                CatalogDefaultRate.engagement_type_id == engagement_type_id,
                vendor_condition,
                CatalogDefaultRate.effective_from == effective_from,
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    rate = create_rate(
        session,
        role_id=role_id,
        seniority_id=seniority_id,
        location_id=location_id,
        engagement_type_id=engagement_type_id,
        vendor_id=vendor_id,
        default_cost_rate=default_cost_rate,
        default_selling_rate=default_selling_rate,
        currency=currency,
        unit=RATE_UNIT_HOUR,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    return rate, True


def _get_or_create_absence_budget(
    session: Session,
    *,
    calendar_id: object,
    engagement_type_id: object,
    budget_days: Decimal,
    effective_from: date,
    effective_to: date,
    source: str,
) -> tuple[AbsenceBudget, bool]:
    existing = (
        session.execute(
            sa.select(AbsenceBudget).where(
                AbsenceBudget.calendar_id == calendar_id,
                AbsenceBudget.engagement_type_id == engagement_type_id,
                AbsenceBudget.effective_from == effective_from,
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    budget = create_budget(
        session,
        calendar_id=calendar_id,
        engagement_type_id=engagement_type_id,
        budget_days=budget_days,
        unit="day",
        source=source,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    return budget, True


def _get_or_create_project(
    session: Session, caller: CallerIdentity, *, name: str, **fields: object
) -> tuple[Project, bool]:
    existing = (
        session.execute(sa.select(Project).where(Project.name == name)).scalars().one_or_none()
    )
    if existing is not None:
        _ensure_project_access(session, project_id=existing.id, user_id=caller.user_id)
        return existing, False
    view = create_project(session, caller, name=name, **fields)
    return view.project, True


def _ensure_project_access(session: Session, *, project_id: object, user_id: str) -> bool:
    """Grant access if it is somehow missing — e.g. a project seeded once under a different
    caller id and re-seeded under a new one. Direct write, same category as `WorkingCalendar`/
    `AbsenceType` above: there is no grant endpoint at all. ADR-0005's addendum 2026-09-18, point 4
    establishes only that granting access is a deliberate, separate act, never an automatic side
    effect of another write (there it is copying) — it does not itself name seed/fixture scripts as
    an approved actor for that act. This script performing it, for a project it owns exclusively, is
    this script's own reasoned choice under the "no creating endpoint → direct write" precedent the
    Issue itself sets, not a case ADR-0005 already decided."""
    existing = (
        session.execute(
            sa.select(ProjectAccess).where(
                ProjectAccess.project_id == project_id, ProjectAccess.user_id == user_id
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return False
    session.add(ProjectAccess(user_id=user_id, project_id=project_id))
    session.commit()
    return True


def _get_or_create_scenario(
    session: Session, project: Project, *, name: str
) -> tuple[Scenario, bool]:
    existing = (
        session.execute(
            sa.select(Scenario).where(Scenario.project_id == project.id, Scenario.name == name)
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    scenario = Scenario(
        id=uuid.uuid4(), project_id=project.id, name=name, status=ScenarioStatus.DRAFT
    )
    session.add(scenario)
    session.commit()
    # `create_project` (called a few lines above, in `_get_or_create_project`) reads
    # `project.scenarios` while the project has none yet (`organization_level_for`), which caches an
    # *empty* collection on this identity-mapped `Project` instance. Writing the scenario as a plain
    # row (not through `project.scenarios.append(...)`) leaves that cached collection stale, and
    # `app.data.project_reads.project_for_caller`'s `selectinload(Project.scenarios)` does not
    # refresh an already-loaded relationship — every later scope check on this project would then
    # see zero scenarios. Expiring it here is what makes the next such read see this one.
    session.expire(project, ["scenarios"])
    return scenario, True


def _get_or_create_commercial_terms(
    session: Session,
    caller: CallerIdentity,
    project_id: object,
    scenario_id: object,
    *,
    model_type: str,
    domain_values: Mapping[str, object] | None = None,
) -> tuple[CommercialTerms, bool]:
    existing = (
        session.execute(
            sa.select(CommercialTerms).where(CommercialTerms.scenario_id == scenario_id)
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    view = create_commercial_terms(
        session,
        caller,
        project_id,
        scenario_id,
        model_type=model_type,
        domain_values=domain_values,
    )
    if view is None:  # pragma: no cover — the scenario was in the caller's scope a line above
        raise RuntimeError(
            "Scenario disappeared from the caller's scope while seeding commercial terms."
        )
    terms_statement = sa.select(CommercialTerms).where(CommercialTerms.scenario_id == scenario_id)
    terms = session.execute(terms_statement).scalars().one()
    return terms, True


def _get_or_create_staffing_position(
    session: Session,
    caller: CallerIdentity,
    project_id: object,
    scenario_id: object,
    *,
    role_id: object,
    seniority_id: object,
    location_id: object,
    engagement_type_id: object,
    headcount: int,
    start_date: date,
    end_date: date | None,
    allocations: Sequence[Mapping[str, object]],
) -> tuple[StaffingPosition, bool]:
    existing = (
        session.execute(
            sa.select(StaffingPosition).where(
                StaffingPosition.scenario_id == scenario_id,
                StaffingPosition.role_id == role_id,
                StaffingPosition.seniority_id == seniority_id,
                StaffingPosition.location_id == location_id,
                StaffingPosition.engagement_type_id == engagement_type_id,
                StaffingPosition.start_date == start_date,
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    view = create_position(
        session,
        caller,
        project_id,
        scenario_id,
        role_id=role_id,
        seniority_id=seniority_id,
        location_id=location_id,
        engagement_type_id=engagement_type_id,
        headcount=headcount,
        start_date=start_date,
        end_date=end_date,
        allocations=allocations,
    )
    if view is None:  # pragma: no cover — the scenario was in the caller's scope a line above
        raise RuntimeError("Scenario disappeared from the caller's scope while seeding a position.")
    return view.position, True


def _approve_if_draft(
    session: Session, caller: CallerIdentity, project_id: object, scenario_id: object
) -> bool:
    """Approve the scenario through the real guarded mechanism (`app.data.scenario_approval`) —
    never a raw `UPDATE scenarios SET status = 'approved'`, so the approval snapshot this script
    seeds is the same one the running application would have produced (K-05).

    Idempotent by construction: `approve_scenario` itself refuses a second approval
    (`ScenarioAlreadyApproved`), so this checks the current status first and skips quietly on a
    second run instead of letting that refusal surface as a script error.
    """
    status = session.execute(
        sa.select(Scenario.status).where(Scenario.id == scenario_id)
    ).scalar_one()
    if status == ScenarioStatus.APPROVED:
        return False
    result = approve_scenario(session, caller, project_id, scenario_id)
    if result is None:  # pragma: no cover — the scenario was in the caller's scope a line above
        raise RuntimeError("Scenario disappeared from the caller's scope while approving it.")
    return True


# --- orchestration ---------------------------------------------------------------------------


def run_seed(session: Session, *, caller_user_id: str) -> SeedSummary:
    """Seed one project's worth of synthetic data, idempotently, under `caller_user_id`.

    Every write below goes through the get-or-create helpers above; nothing here calls a model
    constructor or a data-layer create function a second time for a row already present. Money and
    rates are `Decimal` throughout — no float ever appears in this function, matching the project-
    wide rule (`backend/app/core/money.py`).
    """
    caller = CallerIdentity(user_id=caller_user_id, permissions=frozenset())
    tally = _Tally()

    role, created = _get_or_create_dimension(session, CatalogRole, name=ROLE_NAME)
    tally.record("catalog role", created=created)
    seniority, created = _get_or_create_dimension(session, CatalogSeniority, name=SENIORITY_NAME)
    tally.record("catalog seniority", created=created)
    engagement, created = _get_or_create_dimension(
        session, CatalogEngagementType, name=ENGAGEMENT_TYPE_NAME
    )
    tally.record("catalog engagement type", created=created)

    # Two calendars with different week patterns and standard hours (K-06) — each gets one
    # exceptional day so the "exception overrides the pattern" branch is reachable too.
    calendar_warsaw, created = _get_or_create_working_calendar(
        session,
        name=CALENDAR_WARSAW_NAME,
        standard_hours_per_day=Decimal("8.00"),
        week_pattern=MONDAY_TO_FRIDAY,
    )
    tally.record("working calendar", created=created)
    _, created = _ensure_calendar_day(
        session, calendar_warsaw, day=date(2026, 5, 1), kind=WorkingCalendarDayKind.NON_WORKING
    )
    tally.record("calendar exceptional day", created=created)

    calendar_krakow, created = _get_or_create_working_calendar(
        session,
        name=CALENDAR_KRAKOW_NAME,
        standard_hours_per_day=Decimal("7.50"),
        week_pattern=MONDAY_TO_SATURDAY,
    )
    tally.record("working calendar", created=created)
    _, created = _ensure_calendar_day(
        session, calendar_krakow, day=date(2026, 5, 10), kind=WorkingCalendarDayKind.WORKING
    )
    tally.record("calendar exceptional day", created=created)

    location_warsaw, created = _get_or_create_location(
        session, name=LOCATION_WARSAW_NAME, calendar_id=calendar_warsaw.id
    )
    tally.record("catalog location", created=created)
    location_krakow, created = _get_or_create_location(
        session, name=LOCATION_KRAKOW_NAME, calendar_id=calendar_krakow.id
    )
    tally.record("catalog location", created=created)

    # K-03: two windows for one dimension tuple with a real gap between them (March 2026 is
    # covered by neither) — resolving a rate on a day inside the gap must answer `None`.
    _, created = _get_or_create_rate(
        session,
        role_id=role.id,
        seniority_id=seniority.id,
        location_id=location_warsaw.id,
        engagement_type_id=engagement.id,
        vendor_id=None,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 2, 28),
        default_cost_rate=Decimal("100.0000"),
        default_selling_rate=Decimal("150.0000"),
        currency="PLN",
    )
    tally.record("catalog rate window", created=created)
    _, created = _get_or_create_rate(
        session,
        role_id=role.id,
        seniority_id=seniority.id,
        location_id=location_warsaw.id,
        engagement_type_id=engagement.id,
        vendor_id=None,
        effective_from=date(2026, 4, 1),
        effective_to=None,
        default_cost_rate=Decimal("110.0000"),
        default_selling_rate=Decimal("160.0000"),
        currency="PLN",
    )
    tally.record("catalog rate window", created=created)

    # Required for a covering budget window to ever answer "resolved" rather than
    # "no_statutory_leave_type" (see ABSENCE_TYPE_STATUTORY_NAME) — one flagged type, organisation
    # wide, exactly the shape `app.domain.absence_budget.month_budget_share` needs.
    _, created = _get_or_create_absence_type(
        session,
        name=ABSENCE_TYPE_STATUTORY_NAME,
        generates_cost=True,
        generates_revenue=False,
        is_statutory_leave=True,
    )
    tally.record("absence type", created=created)

    # K-04: a leave budget for (Warsaw calendar, seed engagement type) — and deliberately none for
    # (Krakow calendar, seed engagement type), so "resolved" and "no_budget" are both reachable
    # among the pairs the seeded positions below actually use.
    _, created = _get_or_create_absence_budget(
        session,
        calendar_id=calendar_warsaw.id,
        engagement_type_id=engagement.id,
        budget_days=Decimal("20.00"),
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        source="Seed staff regulation (seed)",
    )
    tally.record("absence budget", created=created)

    project, created = _get_or_create_project(
        session,
        caller,
        name=PROJECT_NAME,
        client="Seed Client (seed)",
        owner="Seed Owner (seed)",
        delivery_period_start=date(2026, 1, 1),
        delivery_period_end=date(2026, 12, 31),
        reporting_currency="PLN",
        description="Seeded by scripts/seed_dev_data.py for local manual verification. Not real "
        "business data.",
    )
    tally.record("project", created=created)

    scenario_tm_draft, created = _get_or_create_scenario(
        session, project, name=SCENARIO_TM_DRAFT_NAME
    )
    tally.record("scenario", created=created)
    scenario_sp_draft, created = _get_or_create_scenario(
        session, project, name=SCENARIO_SP_DRAFT_NAME
    )
    tally.record("scenario", created=created)
    scenario_tm_approved, created = _get_or_create_scenario(
        session, project, name=SCENARIO_TM_APPROVED_NAME
    )
    tally.record("scenario", created=created)

    # K-05: two commercial models (Time & Material, Story Points) across the three scenarios.
    # Fixed Price is never seeded — it has no implementation in this backend (F-10, confirmed by
    # grep against docs/architecture/capabilities.md before writing this script).
    _, created = _get_or_create_commercial_terms(
        session, caller, project.id, scenario_tm_draft.id, model_type="time_and_material"
    )
    tally.record("commercial terms", created=created)
    _, created = _get_or_create_commercial_terms(
        session,
        caller,
        project.id,
        scenario_sp_draft.id,
        model_type="story_points",
        domain_values={
            "price_per_point": Decimal("1000.0000"),
            "accepted_points": 25,
            "currency": "PLN",
        },
    )
    tally.record("commercial terms", created=created)
    _, created = _get_or_create_commercial_terms(
        session, caller, project.id, scenario_tm_approved.id, model_type="time_and_material"
    )
    tally.record("commercial terms", created=created)

    allocation_month = date(2026, 4, 1)
    standard_allocation = [
        {
            "period_month": allocation_month,
            "availability_hours": Decimal("160.00"),
            "planned_allocation_hours": Decimal("120.00"),
            "billable_hours": Decimal("100.00"),
        }
    ]

    # K-04 / K-06: one position per calendar, same headcount and same allocation figures, so the
    # only thing that can make their derived capacity differ is the calendar each one's location
    # points at — and the budget state, which differs by design (Warsaw resolved, Krakow no_budget).
    _, created = _get_or_create_staffing_position(
        session,
        caller,
        project.id,
        scenario_tm_draft.id,
        role_id=role.id,
        seniority_id=seniority.id,
        location_id=location_warsaw.id,
        engagement_type_id=engagement.id,
        headcount=1,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 5, 31),
        allocations=standard_allocation,
    )
    tally.record("staffing position", created=created)
    _, created = _get_or_create_staffing_position(
        session,
        caller,
        project.id,
        scenario_tm_draft.id,
        role_id=role.id,
        seniority_id=seniority.id,
        location_id=location_krakow.id,
        engagement_type_id=engagement.id,
        headcount=1,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 5, 31),
        allocations=standard_allocation,
    )
    tally.record("staffing position", created=created)

    # A position for the scenario about to be approved, so its approval snapshot actually freezes
    # a calendar and a rate window rather than approving an empty plan.
    _, created = _get_or_create_staffing_position(
        session,
        caller,
        project.id,
        scenario_tm_approved.id,
        role_id=role.id,
        seniority_id=seniority.id,
        location_id=location_warsaw.id,
        engagement_type_id=engagement.id,
        headcount=1,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 5, 31),
        allocations=standard_allocation,
    )
    tally.record("staffing position", created=created)

    approved = _approve_if_draft(session, caller, project.id, scenario_tm_approved.id)
    tally.record("scenario approval", created=approved)

    return SeedSummary(
        caller_user_id=caller_user_id,
        project_id=project.id,
        scenario_ids={
            "tm_draft": scenario_tm_draft.id,
            "sp_draft": scenario_sp_draft.id,
            "tm_approved": scenario_tm_approved.id,
        },
        tally=tally,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--caller-user-id",
        default=None,
        help="The identity to grant project access to — the same value as frontend/.env's "
        "VITE_CALLER_USER_ID. Falls back to that environment variable, then to frontend/.env "
        "itself, if omitted. Refuses to run if none is found.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        caller_user_id = resolve_caller_user_id(
            args.caller_user_id, frontend_env_path=DEFAULT_FRONTEND_ENV_PATH
        )
    except MissingCallerUserIdError as error:
        print(f"Refusing to seed: {error}", file=sys.stderr)
        return 2

    # The same fail-closed guard `app.main` runs at import time (ADR-0005, addendum 2026-09-18):
    # this script writes through the same models and the same identity mechanism the running
    # application does, so it must refuse under the identical conditions — never against a
    # database this project has not explicitly opted into treating as local dev/test.
    try:
        assert_identity_mechanism_allowed(
            allow_placeholder_identity=settings.allow_placeholder_identity,
            environment=settings.environment,
        )
    except PlaceholderIdentityNotAllowedError as error:
        print(f"Refusing to seed: {error}", file=sys.stderr)
        return 2

    session = get_sessionmaker()()
    try:
        summary = run_seed(session, caller_user_id=caller_user_id)
    finally:
        session.close()
    print(summary.describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
