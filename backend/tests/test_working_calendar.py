"""SC-3-02, K-01, K-02 and K-23 — where the hours of a working day come from (F-05).

Three criteria, three different mechanisms, and each of them is killed by a different mutation:

- **K-01** the basis comes from the calendar of the *position's location*, not from a constant and
  not from the scenario's older `working_calendar`/`full_time_hours_per_week` columns;
- **K-02** which days are working days is **data** — a week pattern and a table of exceptional
  days — and an exceptional day works in both directions;
- **K-23** a location with no calendar answers with a named state, never a silent zero and never an
  error.

**No calendar in this file has a standard day of 8.00 hours** (`conftest.FORBIDDEN_FIXTURE_HOURS`
refuses one), so K-01's mutation — replacing the lookup with a module constant of 8 — cannot pass by
coincidence. And at least one calendar works Monday to **Saturday**, so K-02's second mutation —
`day.weekday() < 5` instead of the stored pattern — cannot pass either.

Every figure here is checked against a number computed in the test's own prose (March 2026 has 22
Monday-to-Friday days and 4 Saturdays), not against a number the implementation produced. A test
that asserted "the same value twice" would be satisfied by any formula at all.
"""

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import CatalogLocation, WorkingCalendar, WorkingCalendarDayKind
from tests.conftest import (
    IN_SCOPE_USER,
    MONDAY_TO_SATURDAY,
    as_caller,
    make_allocation,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
    staffing_path,
)

MARCH = date(2026, 3, 1)

MARCH_WEEKDAYS = 22
"""Monday-to-Friday days in March 2026 — 2026-03-01 is a Sunday and the month has 31 days."""

MARCH_SATURDAYS = 4
"""2026-03-07, -14, -21 and -28."""


def _capacity(client: TestClient, project_id, scenario_id, month: date = MARCH) -> dict:
    """The derived-capacity fields of one month, read through the API a client really uses."""
    response = client.get(staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    allocations = response.json()["positions"][0]["allocations"]
    [allocation] = [row for row in allocations if row["period_month"] == month.isoformat()]
    return allocation


def _point_location_at(session: Session, location_id, calendar: WorkingCalendar | None) -> None:
    """Repoint one location's calendar — the *only* thing that changes between two runs.

    A direct database write, because SC-3-02 ships no endpoint that sets `catalog_locations
    .calendar_id` (ADR-0007, addendum 2026-09-22, point 3 — these dictionaries get no form in this
    task). That is a named limit of the proof, not an equivalence: what is proven is that the
    reading path follows the column, not that a user can change it.
    """
    session.execute(
        sa.update(CatalogLocation)
        .where(CatalogLocation.id == location_id)
        .values(calendar_id=None if calendar is None else calendar.id)
    )
    session.flush()
    session.expire_all()


def test_k_01_derived_capacity_reads_the_hours_of_the_calendar_of_the_positions_location(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — the basis is the calendar of the location, and nothing else.

    One position, one month, **zero absences**. Between the two reads exactly one thing changes: the
    location's `calendar_id`, repointed from a calendar whose standard day is 7.50 hours to one
    whose standard day is 6.25. Everything else — the position, its headcount, the month, the week
    pattern, the (empty) set of exceptional days — is identical, so any difference in the answer can
    only have come from the calendar.

    Both figures are asserted against numbers computed here (22 working days × the calendar's own
    hours), never against each other: "the value changed" would also be satisfied by an
    implementation reading some *other* column of the calendar.

    The third assertion is what kills the mutation the criterion names. `22 × 8.00 = 176.00` is the
    answer a hard-coded constant would give, and it is not the answer either run produces — and
    `conftest.make_working_calendar` refuses to create a calendar with a standard day of 8.00, so no
    later fixture can make that coincidence possible.

    It also kills a quieter mutation: the scenario below carries the *older*, unconverted
    `working_calendar = "Poland"` and `full_time_hours_per_week = 40.00` columns (the named gap
    G-1). An implementation reading those would answer the same number for both runs.
    """
    seven_and_a_half = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    six_and_a_quarter = make_working_calendar(
        db_session, name="Portugal 6.25h", standard_hours_per_day=Decimal("6.25")
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(
        db_session,
        project,
        name="Baseline",
        working_calendar="Poland",
        full_time_hours_per_week=Decimal("40.00"),
    )
    dimensions = make_dimension_tuple(db_session, calendar=seven_and_a_half)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)

    first = _capacity(client, project.id, scenario.id)

    _point_location_at(db_session, dimensions.location_id, six_and_a_quarter)
    second = _capacity(client, project.id, scenario.id)

    assert first["derived_capacity_hours"] == "165.00", first  # 22 × 7.50
    assert first["derived_capacity_source"]["calendar_name"] == "Poland 7.5h"
    assert first["derived_capacity_source"]["standard_hours_per_day"] == "7.50"
    assert second["derived_capacity_hours"] == "137.50", second  # 22 × 6.25
    assert second["derived_capacity_source"]["calendar_name"] == "Portugal 6.25h"
    assert "176.00" not in (
        first["derived_capacity_hours"],
        second["derived_capacity_hours"],
    ), (
        "the capacity equals 22 working days × 8 hours — the basis is a constant, not the "
        "calendar of the position's location"
    )


def test_k_02_a_holiday_removes_a_working_day_and_an_extra_day_adds_one(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — the week pattern and the exceptional days are rows, and one row works both ways.

    The calendar works **Monday to Saturday** (`'1111110'`), which is what makes the first assertion
    a statement about stored data rather than about the calendar module: March 2026 has 22
    Monday-to-Friday days and 4 Saturdays, so the baseline is 26 working days and *not* 22. An
    implementation reading `day.weekday() < 5` answers 22 here and fails immediately.

    Then **one** `working_calendar_day` row moves the answer in both directions, which is the other
    half of the criterion:

    - as `non_working` on Tuesday 3 March it removes a day the pattern granted → 25;
    - re-pointed at Sunday 8 March as `working` it adds a day the pattern refused → 27.

    Both are the same row, edited — so an implementation that honoured holidays and ignored extra
    working days (a `set` of days to skip, the obvious first version) passes the first and fails the
    second.

    Ignoring the table altogether leaves the answer at 26 for all three reads, which fails two of
    the three assertions.
    """
    calendar = make_working_calendar(
        db_session,
        name="Poland six-day",
        standard_hours_per_day=Decimal("7.00"),
        week_pattern=MONDAY_TO_SATURDAY,
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)

    baseline = _capacity(client, project.id, scenario.id)
    assert baseline["derived_capacity_source"]["working_days"] == MARCH_WEEKDAYS + MARCH_SATURDAYS
    assert baseline["derived_capacity_hours"] == "182.00", baseline  # 26 × 7.00

    holiday = make_calendar_day(
        db_session,
        calendar,
        day=date(2026, 3, 3),  # a Tuesday the pattern makes a working day
        kind=WorkingCalendarDayKind.NON_WORKING,
    )
    db_session.expire_all()
    with_holiday = _capacity(client, project.id, scenario.id)
    assert with_holiday["derived_capacity_source"]["working_days"] == 25
    assert with_holiday["derived_capacity_hours"] == "175.00", with_holiday  # 25 × 7.00

    # The same row, re-pointed: a Sunday the pattern makes a day off, declared a working day.
    holiday.day = date(2026, 3, 8)
    holiday.kind = WorkingCalendarDayKind.WORKING
    db_session.flush()
    db_session.expire_all()
    with_extra_day = _capacity(client, project.id, scenario.id)
    assert with_extra_day["derived_capacity_source"]["working_days"] == 27, (
        "an extra working day declared in working_calendar_day did not add a day — the table is "
        "read as a set of days to skip rather than as a classification in both directions"
    )
    assert with_extra_day["derived_capacity_hours"] == "189.00", with_extra_day  # 27 × 7.00


def test_k_23_a_position_whose_locations_calendar_id_is_null_returns_a_named_no_calendar_state_not_zero_not_an_error(  # noqa: E501 — the criterion names this test; the name is the contract, not a style choice
    client: TestClient, db_session: Session
) -> None:
    """K-23 — a location with no calendar is a **named state**, not a zero and not a crash.

    The gate-1 resolution of G-2 (ADR-0008, addendum 2026-09-22, point 7): `catalog_locations
    .calendar_id` is nullable, and the omission means "this location has no calendar", never "assume
    eight hours" and never "assume nothing works here".

    Both mutations the criterion names are killed by one of the two assertion blocks:

    - **a silent zero** — `derived_capacity_hours` would be `"0.00"`, a number every later sum would
      add up. It is `"n/a"` instead, the sentinel this project already uses for a figure that cannot
      be computed (`app.core.money.NOT_APPLICABLE`), and `derived_capacity_state` says *why*;
    - **an exception** — the request would be a `500`. It is a `200`, with the rest of the position
      (headcount, period, the typed hours) intact, because an incomplete catalogue is something to
      fill in rather than a server error.

    The contrast is the same position, the same month and the same request, with a calendar attached
    to the location: a number, a `"resolved"` state and a source naming the calendar. Without it
    this test would be satisfied by an implementation that answered `"n/a"` to everything.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session, calendar=None)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=2, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)

    without = _capacity(client, project.id, scenario.id)

    assert without["derived_capacity_state"] == "no_calendar"
    assert without["derived_capacity_hours"] == "n/a", (
        "a position in a location with no calendar was given a number — a missing calendar is a "
        "named state, not a silent zero"
    )
    assert without["derived_capacity_source"] is None
    # The rest of the row is untouched: the typed figures are still there, so this is a named gap in
    # one field and not a refusal of the position.
    assert without["availability_hours"] == "160.00"

    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    _point_location_at(db_session, dimensions.location_id, calendar)
    with_calendar = _capacity(client, project.id, scenario.id)

    assert with_calendar["derived_capacity_state"] == "resolved"
    assert with_calendar["derived_capacity_hours"] == "330.00", with_calendar  # 2 × 22 × 7.50
    assert with_calendar["derived_capacity_source"]["calendar_id"] == str(calendar.id)


def test_the_typed_availability_and_the_derived_capacity_are_two_independent_numbers(
    client: TestClient, db_session: Session
) -> None:
    """Not an acceptance criterion — the reading that makes K-01 and K-08 about different things.

    A position whose planner typed `160.00` available hours in a month the calendar says is worth
    `165.00`. Both numbers come back, and they disagree. That disagreement is the information F-05
    exists to surface: until this task nothing in the system knew about a single day off
    (`app.models.staffing.StaffingPositionAllocation.availability_hours` said so in its own
    docstring), so the typed figure was an assertion nobody could check.

    Asserted here rather than left implicit, because "the derived figure equals the typed one" is
    the state an implementation that copied one into the other would produce, and every other test
    in this file uses figures that happen to differ for other reasons.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)

    row = _capacity(client, project.id, scenario.id)

    assert row["availability_hours"] == "160.00"
    assert row["derived_capacity_hours"] == "165.00"
    assert row["availability_hours"] != row["derived_capacity_hours"]
    assert row["derived_capacity_source"] == {
        "calendar_id": str(calendar.id),
        "calendar_name": "Poland 7.5h",
        "standard_hours_per_day": "7.50",
        "working_days": MARCH_WEEKDAYS,
        "absence_day_equivalents": 0,
    }
