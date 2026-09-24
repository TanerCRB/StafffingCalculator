"""Reading working calendars and absence types (F-05, SC-3-02).

**No guard function here, and that absence is decided rather than convenient** (ADR-0001, addendum
2026-09-22, point 1). `app.data.project_reads.project_for_caller` exists so that a *scope predicate*
cannot be forgotten; a calendar, its days and an absence type belong to no project, no user and no
tenant, so there is no predicate to forget. A wrapper named symmetrically to `project_reads` would
advertise a filter that is not there — the dangerous direction to be wrong in, and the argument
`app.data.catalog` already makes for itself.

`catalog_locations.calendar_id` does not end that exemption: it names another organisational row,
not a subject the caller acts for, exactly as `vendor_id` does (ADR-0005, addendum 2026-09-22,
point 2). The expiry condition is unchanged and structural — the first *per-caller* predicate on any
of these tables.

**What this module produces for the calculation is a value object, not an ORM row.**
`app.domain.capacity.CalendarBasis` is what the capacity formula consumes, and it is deliberately
the same shape whether it was assembled from the live tables (a `draft` scenario, here) or from an
approval snapshot (`approved_snapshot_working_calendar`, read by `frozen_basis_by_location` since
SC-5-06). A calculation that took ORM rows would have to be rewritten the day it reads a snapshot
instead.
"""

import uuid
from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session, selectinload

from app.domain.capacity import CalendarBasis
from app.models.approved_snapshot import (
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
)
from app.models.catalog import AbsenceType, CatalogLocation, WorkingCalendar


def list_calendars(session: Session) -> Sequence[WorkingCalendar]:
    """Every working calendar with its exceptional days — the same rows for every caller.

    Ordered by name and then by id, so a list read twice comes back the same way; the days are
    ordered by the relationship itself. `selectinload` rather than a lazy load per row: the days of
    N calendars are one extra statement, not N.

    No pagination, unlike `app.data.catalog.list_rates`: the number of calendars an organisation has
    is bounded by the number of places it works in, and the days of one calendar by the days in a
    year. That reasoning is the whole justification, and it expires the moment a calendar is created
    per team or per subcontractor.
    """
    statement = (
        sa.select(WorkingCalendar)
        .options(selectinload(WorkingCalendar.days))
        .order_by(WorkingCalendar.name, WorkingCalendar.id)
    )
    return list(session.execute(statement).scalars().all())


def list_absence_types(session: Session) -> Sequence[AbsenceType]:
    """Every absence type, ordered by name then id. No caller filter — see the module docstring."""
    statement = sa.select(AbsenceType).order_by(AbsenceType.name, AbsenceType.id)
    return list(session.execute(statement).scalars().all())


def basis_by_location(
    session: Session, location_ids: Sequence[uuid.UUID]
) -> Mapping[uuid.UUID, CalendarBasis]:
    """The calendar basis of each of those locations — **absent from the mapping when there is
    none**.

    The return type is the whole point of this function's shape. A location whose `calendar_id` is
    `NULL` is simply not a key here, so the caller has to handle its absence explicitly; a mapping
    that answered `CalendarBasis(standard_hours_per_day=Decimal(0))` or raised `KeyError` would be
    the two mutations criterion K-23 names — a silent zero and an unhandled error. The named state
    itself is produced one layer up, by `app.domain.capacity.month_capacity(None, …)`.

    One statement for every location the caller asked about, joined to the calendar and its days.
    `selectinload` on the days for the same reason as above: the alternative is one query per
    location per request, which is the shape NF-03 is about even though nothing here measures it.
    """
    if not location_ids:
        return {}
    statement = (
        sa.select(CatalogLocation.id, WorkingCalendar)
        .join(WorkingCalendar, CatalogLocation.calendar_id == WorkingCalendar.id)
        .options(selectinload(WorkingCalendar.days))
        .where(CatalogLocation.id.in_(set(location_ids)))
    )
    return {
        location_id: basis_of(calendar)
        for location_id, calendar in session.execute(statement).all()
    }


def frozen_basis_by_location(
    session: Session, scenario_id: uuid.UUID
) -> Mapping[uuid.UUID, CalendarBasis]:
    """The calendar basis of each location **as one approved scenario froze it** — the snapshot's
    counterpart of `basis_by_location`, and the first reader of `approved_snapshot_working_calendar`
    and `approved_snapshot_working_calendar_day` (ADR-0004, aneks 2026-09-23 SC-5-06, point 1).

    The same return shape as the live function, for the same reason: a location that had no
    calendar at approval froze no row, so it is absent here and the caller produces the named
    `no_calendar` state from the absence of a key — never a zero and never a lookup in the live
    catalogue to fill it in.

    **Only this scenario's rows, by `scenario_id`, in both statements** (control M-2): two approved
    scenarios of one location may have frozen two different versions of one calendar, and a reader
    that lost the scenario condition would merge their days into one mapping. The live tables are
    not read at all; editing the calendar, its days or a location's `calendar_id` after the approval
    moves nothing here (control M-1).
    """
    calendars = session.execute(
        sa.select(ApprovedSnapshotWorkingCalendar).where(
            ApprovedSnapshotWorkingCalendar.scenario_id == scenario_id
        )
    ).scalars().all()
    if not calendars:
        return {}
    days: dict[uuid.UUID, dict] = {}
    for row in session.execute(
        sa.select(ApprovedSnapshotWorkingCalendarDay).where(
            ApprovedSnapshotWorkingCalendarDay.scenario_id == scenario_id
        )
    ).scalars():
        days.setdefault(row.source_calendar_id, {})[row.day] = row.kind
    return {
        calendar.source_location_id: CalendarBasis(
            calendar_id=calendar.source_calendar_id,
            name=calendar.name,
            standard_hours_per_day=calendar.standard_hours_per_day,
            week_pattern=calendar.week_pattern,
            exceptional_days=days.get(calendar.source_calendar_id, {}),
        )
        for calendar in calendars
    }


def basis_of(calendar: WorkingCalendar) -> CalendarBasis:
    """One ORM calendar row as the value object the capacity formula consumes.

    The exceptional days become a mapping keyed by day — which only makes sense because
    `UNIQUE (calendar_id, day)` guarantees at most one row per day (criterion K-03). Without that
    constraint this dictionary comprehension would silently keep whichever row the database happened
    to return last, and "is this a working day?" would have two answers and no rule to choose
    between them.
    """
    return CalendarBasis(
        calendar_id=calendar.id,
        name=calendar.name,
        standard_hours_per_day=calendar.standard_hours_per_day,
        week_pattern=calendar.week_pattern,
        exceptional_days={row.day: row.kind for row in calendar.days},
    )
