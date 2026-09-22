"""SC-3-02, K-04..K-08 — the arithmetic of absences against a working calendar (F-05).

Five criteria, and the reason they are five rather than one is that each names a *different*
mutation, and every one of those mutations leaves the other four tests green:

- **K-04** one absence removes one person-day whatever the headcount is → multiplying the deduction
  by `headcount`;
- **K-05** absence days outside the calendar's working days remove nothing → counting
  `(end_date - start_date).days + 1` instead of the intersection;
- **K-06** two overlapping absences are two people → deduplicating the union of days;
- **K-07** capacity is floored at zero → dropping the `max(0, …)`;
- **K-08** the derived figure travels beside the typed one → writing it into the column, or
  returning it under the typed one's name.

The calendar throughout is Monday-to-Friday with a standard day of **7.50** hours — never 8.00, so
a hard-coded constant cannot produce any of these numbers (`conftest.FORBIDDEN_FIXTURE_HOURS`), and
never a whole number of hours, so an implementation that dropped the calendar's basis entirely
(counting *days* and calling them hours) is visible too.

March 2026 has 22 Monday-to-Friday days. 2026-03-01 is a Sunday; 2026-03-02 is a Monday; 2026-03-07
and -08 are a Saturday and a Sunday. Every expected figure below is computed from those facts in the
docstring that asserts it, never from what the implementation returned.
"""

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import StaffingPosition, WorkingCalendarDayKind
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    make_absence,
    make_absence_type,
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
HOURS_PER_DAY = Decimal("7.50")
MARCH_WORKING_DAYS = 22


def _scenario_with_calendar(session: Session, *, headcount: int):
    """A project in scope, a draft scenario, a position in a location with a Mon-Fri calendar.

    Returns the pieces every test below needs. One helper rather than a fixture, so each test still
    names its own `headcount` — the parameter K-04 and K-06 vary on purpose.
    """
    calendar = make_working_calendar(
        session, name="Poland 7.5h", standard_hours_per_day=HOURS_PER_DAY
    )
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    dimensions = make_dimension_tuple(session, calendar=calendar)
    position = make_staffing_position(
        session, scenario, dimensions, headcount=headcount, start_date=MARCH
    )
    make_allocation(session, position, period_month=MARCH)
    absence_type = make_absence_type(session)
    return project, scenario, position, absence_type, calendar


def _march(client: TestClient, project_id, scenario_id, position_id=None) -> dict:
    """March's allocation row of one position, read through the API."""
    response = client.get(staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    positions = response.json()["positions"]
    if position_id is not None:
        positions = [row for row in positions if row["id"] == str(position_id)]
    [allocation] = [
        row for row in positions[0]["allocations"] if row["period_month"] == MARCH.isoformat()
    ]
    return allocation


# --- K-04: one absence is one person, not the whole position -------------------------------------


def test_k_04_one_absence_consumes_one_head_equivalent_per_working_day_not_the_whole_headcount(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — the deduction is one person's days, and `headcount` never multiplies it.

    The same absence — Monday 2 March to Friday 6 March, five working days — is measured against a
    position with `headcount = 3` and against one with `headcount = 1`, in the same scenario, with
    the same calendar, the same month and the same type.

    The **deducted amount is identical**: `5 × 7.50 = 37.50`. The capacities differ, because the
    gross capacities differ (`3 × 22 × 7.50 = 495.00` against `22 × 7.50 = 165.00`), and that is the
    contrast that makes the claim checkable — a test comparing only the two capacities would be
    satisfied by an implementation that multiplied the deduction by the headcount too.

    The mutation the criterion names — `absence_day_equivalents × headcount` — gives `112.50`
    deducted from the three-person position, i.e. `382.50`, and fails the first assertion. At least
    one run has `headcount > 1`, which is what makes that visible at all.
    """
    project, scenario, big, absence_type, calendar = _scenario_with_calendar(
        db_session, headcount=3
    )
    small = make_staffing_position(
        db_session,
        scenario,
        make_dimension_tuple(db_session, suffix=" (solo)", calendar=calendar),
        headcount=1,
        start_date=MARCH,
    )
    make_allocation(db_session, small, period_month=MARCH)
    for position in (big, small):
        make_absence(
            db_session,
            position,
            absence_type,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 6),
        )

    for_three = _march(client, project.id, scenario.id, big.id)
    for_one = _march(client, project.id, scenario.id, small.id)

    gross_three = Decimal(3 * MARCH_WORKING_DAYS) * HOURS_PER_DAY  # 495.00
    gross_one = Decimal(MARCH_WORKING_DAYS) * HOURS_PER_DAY  # 165.00
    deducted_from_three = gross_three - Decimal(for_three["derived_capacity_hours"])
    deducted_from_one = gross_one - Decimal(for_one["derived_capacity_hours"])

    assert deducted_from_three == deducted_from_one == Decimal("37.50"), (
        f"the deduction depends on the headcount ({deducted_from_three} against "
        f"{deducted_from_one}) — one absence is one person, whatever the position plans for"
    )
    assert for_three["derived_capacity_hours"] == "457.50"  # 495.00 − 37.50
    assert for_one["derived_capacity_hours"] == "127.50"  # 165.00 − 37.50
    assert for_three["derived_capacity_source"]["absence_day_equivalents"] == 5
    assert for_one["derived_capacity_source"]["absence_day_equivalents"] == 5


# --- K-05: an absence over a day nobody works removes nothing ------------------------------------


def test_k_05_absence_days_falling_outside_the_calendars_working_days_consume_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — the deduction is the *intersection* with the calendar, not the length of the range.

    Three reads, one position, one month:

    1. an absence over Saturday 7 and Sunday 8 March — two days the Monday-to-Friday calendar knows
       nothing about. The capacity is unchanged: `22 × 7.50 = 165.00`;
    2. the same absence, **shifted by one day** to Sunday 8 and Monday 9 March. Exactly one of the
       two days is now a working day, so the capacity drops by one standard day to `157.50`;
    3. a public holiday declared on Monday 9 March, with the absence left exactly where it was. The
       day stops being a working day, so the absence stops consuming it — and the capacity is the
       gross figure for 21 working days, `157.50`, arrived at from the other direction.

    The mutation the criterion names — `(end_date - start_date).days + 1` — deducts two days in read
    1 and read 2 alike, and fails both the first and the second assertion.

    Read 3 is what stops the test from being satisfied by an implementation that intersects with the
    *week pattern* only and ignores the exceptional-days table: with the holiday in place, the
    absence covers no working day at all, so the capacity has to equal the gross capacity of a month
    with 21 working days rather than that figure minus a day.
    """
    project, scenario, position, absence_type, calendar = _scenario_with_calendar(
        db_session, headcount=1
    )
    absence = make_absence(
        db_session,
        position,
        absence_type,
        start_date=date(2026, 3, 7),  # Saturday
        end_date=date(2026, 3, 8),  # Sunday
    )

    over_the_weekend = _march(client, project.id, scenario.id)
    assert over_the_weekend["derived_capacity_hours"] == "165.00"  # 22 × 7.50, nothing removed
    assert over_the_weekend["derived_capacity_source"]["absence_day_equivalents"] == 0

    absence.start_date = date(2026, 3, 8)  # Sunday
    absence.end_date = date(2026, 3, 9)  # Monday — one working day
    db_session.flush()
    db_session.expire_all()
    shifted = _march(client, project.id, scenario.id)
    assert shifted["derived_capacity_source"]["absence_day_equivalents"] == 1
    assert shifted["derived_capacity_hours"] == "157.50", shifted  # 165.00 − 7.50

    make_calendar_day(
        db_session, calendar, day=date(2026, 3, 9), kind=WorkingCalendarDayKind.NON_WORKING
    )
    db_session.expire_all()
    with_holiday = _march(client, project.id, scenario.id)
    assert with_holiday["derived_capacity_source"]["working_days"] == MARCH_WORKING_DAYS - 1
    assert with_holiday["derived_capacity_source"]["absence_day_equivalents"] == 0, (
        "an absence over a declared holiday still consumed a day — the intersection is taken "
        "against the week pattern only, not against the calendar"
    )
    assert with_holiday["derived_capacity_hours"] == "157.50"  # 21 × 7.50, nothing removed


# --- K-06: two overlapping absences are two people -----------------------------------------------


def test_k_06_two_overlapping_absences_on_one_position_are_two_people_not_one_counted_twice(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — the deduction is summed **per absence**, and the union of days is never taken.

    One position with `headcount = 2`, unchanged between the two reads, and the same three working
    days (Monday 2 to Wednesday 4 March):

    - two absences over those days → `2 × 3 × 7.50 = 45.00` removed;
    - one of them deleted → `22.50` removed, exactly half.

    The `headcount` is deliberately identical in both reads, so nothing but the number of absence
    rows can explain the difference. The mutation — `COUNT(DISTINCT day)` or a `set` of days, i.e.
    the union of the two ranges — answers `22.50` in both reads and fails the first assertion.

    Why this is a real case rather than a pathological one: a position with `headcount = 2` is two
    people, and two people can perfectly well be on holiday in the same week. Deduplicating them
    would report the team as more available than it is, on exactly the weeks when that matters.
    """
    project, scenario, position, absence_type, _ = _scenario_with_calendar(
        db_session, headcount=2
    )
    first = make_absence(
        db_session,
        position,
        absence_type,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 4),
    )
    make_absence(
        db_session,
        position,
        absence_type,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 4),
    )

    both = _march(client, project.id, scenario.id)
    gross = Decimal(2 * MARCH_WORKING_DAYS) * HOURS_PER_DAY  # 330.00
    assert both["derived_capacity_source"]["absence_day_equivalents"] == 6, (
        "two absences over the same three days were counted as three person-days — the union of "
        "the ranges was taken instead of a sum per absence"
    )
    assert Decimal(both["derived_capacity_hours"]) == gross - Decimal("45.00")

    db_session.delete(first)
    db_session.flush()
    db_session.expire_all()
    alone = _march(client, project.id, scenario.id)

    assert alone["derived_capacity_source"]["absence_day_equivalents"] == 3
    assert Decimal(alone["derived_capacity_hours"]) == gross - Decimal("22.50")
    assert Decimal(both["derived_capacity_hours"]) < Decimal(alone["derived_capacity_hours"])


# --- K-07: capacity never goes negative ----------------------------------------------------------


def test_k_07_capacity_floors_at_zero_when_absences_exceed_the_positions_hours(
    client: TestClient, db_session: Session
) -> None:
    """K-07 — a month absorbed **exactly** by absences answers a clean, non-negative `0.00`.

    **This run does not exercise the floor, and its first description said it did** (QA, 2026-09-22
    — the correction is the whole of this paragraph). One person, three absences that between them
    cover March once: 1–12, 13–22, 23–31, i.e. all 22 working days against a gross of
    `22 × 7.50 = 165.00`. The deduction is `22 × 7.50 = 165.00` too, so `net` comes out at exactly
    `0.00` **without** any flooring, and the three candidate implementations — `max(net, 0)`, a bare
    `net` and `abs(net)` — all answer `"0.00"` here. QA ran two of those mutations against this test
    and both survived it. The test that does reach the floor is
    `test_k_07_the_floor_is_exercised_by_a_deficit_of_exactly_one_working_day` (deficit: one working
    day) and `…_rather_than_wrapping_round` (deficit: three months' worth).

    The **name** of this test says "exceed" and is wrong for the same reason. It is left alone
    deliberately: a test name is read as a contract in this repository, renaming one is not a
    developer's call to make silently, and the correction is named in the SC-3-02 report instead.

    What this run still proves, and why it stays: the boundary where absence consumes the month
    exactly answers a **clean** zero — `"0.00"`, not `"-0.00"`, not `"0"`, not an empty figure.
    `Decimal("0.00")` and not `0` or `"0"`: the boundary serialises hours as fixed-point strings
    (ADR-0002), so the assertion is on the string a client really receives — and on the scale, which
    is what `app.core.money.round_money` guarantees and an ad-hoc `int()` would not.

    The contrast is the third absence removed: the capacity is positive again, so the zero above is
    a figure computed from the absences rather than an implementation that answers zero whenever
    there is any absence at all. The deducted amount in that read is asserted as "greater than zero"
    only, because the exact figure is K-04's and K-06's claim, not this one's.
    """
    project, scenario, position, absence_type, _ = _scenario_with_calendar(
        db_session, headcount=1
    )
    make_absence(
        db_session, position, absence_type, start_date=MARCH, end_date=date(2026, 3, 12)
    )
    make_absence(
        db_session,
        position,
        absence_type,
        start_date=date(2026, 3, 13),
        end_date=date(2026, 3, 22),
    )
    third = make_absence(
        db_session,
        position,
        absence_type,
        start_date=date(2026, 3, 23),
        end_date=date(2026, 3, 31),
    )

    whole_month = _march(client, project.id, scenario.id)
    assert whole_month["derived_capacity_source"]["absence_day_equivalents"] == MARCH_WORKING_DAYS
    assert whole_month["derived_capacity_hours"] == "0.00", (
        "a position whose absences cover the whole month did not floor at zero"
    )
    assert not whole_month["derived_capacity_hours"].startswith("-")

    db_session.delete(third)
    db_session.flush()
    db_session.expire_all()
    with_a_week_left = _march(client, project.id, scenario.id)

    assert Decimal(with_a_week_left["derived_capacity_hours"]) > Decimal("0.00"), (
        "removing one absence left the capacity at zero — the answer is zero for every absent "
        "position, not a floor"
    )


def test_k_07_an_over_absent_position_floors_at_zero_rather_than_wrapping_round(
    client: TestClient, db_session: Session
) -> None:
    """K-07's sharpest case — the absences exceed the month by a wide margin, not by a day.

    Four overlapping absences on a one-person position, each covering the whole of March: the raw
    arithmetic is `165.00 − 4 × 165.00 = −495.00`. A `max` floors it; an `abs()` (the other way a
    negative figure gets "handled") would answer `495.00`, three times the position's real capacity,
    and would pass a test that only checked "not negative".

    Both assertions are needed: the value is exactly zero, and it is not the absolute value of the
    deficit.
    """
    project, scenario, position, absence_type, _ = _scenario_with_calendar(
        db_session, headcount=1
    )
    for _ in range(4):
        make_absence(
            db_session, position, absence_type, start_date=MARCH, end_date=date(2026, 3, 31)
        )

    row = _march(client, project.id, scenario.id)

    assert row["derived_capacity_hours"] == "0.00"
    assert row["derived_capacity_source"]["absence_day_equivalents"] == 4 * MARCH_WORKING_DAYS


def test_k_07_the_floor_is_exercised_by_a_deficit_of_exactly_one_working_day(
    client: TestClient, db_session: Session
) -> None:
    """K-07 at its narrowest — added by QA, because the two tests above leave a hole between them.

    `test_k_07_capacity_floors_at_zero_when_absences_exceed_the_positions_hours` is named for a
    deficit and does not have one: its three absences cover March exactly once, so the deduction is
    22 person-days against 22 working days and `net` comes out at exactly `0.00`. Every
    implementation of the floor agrees on that number — `max(net, 0)`, a bare `net`, and `abs(net)`
    alike — so that test passes with no floor in the code at all. Both mutations were run and both
    survived it; only `…_rather_than_wrapping_round`, whose deficit is `−495.00`, killed them.

    That leaves the criterion resting on a single witness with a deficit three times the month's
    capacity, and nothing covering the boundary the floor actually sits on. This test is that
    boundary: **one working day over, and not a day more.**

    Two absences on a one-person position: the whole of March (22 working days) and Monday 2 March
    again (one more). 23 person-days against a gross of `22 × 7.50 = 165.00` gives
    `165.00 − 23 × 7.50 = −7.50` before the floor. The three candidate implementations now disagree,
    which is the whole point:

    - `max(net, 0)` → `"0.00"` — correct;
    - `net` → `"-7.50"` — a negative capacity, which multiplied by a rate is a negative cost;
    - `abs(net)` → `"7.50"` — an hour of capacity conjured out of a deficit, and the reading a test
      asserting only "not negative" would wave through.

    The contrast is the second absence removed: the deficit disappears and the answer is `"0.00"`
    again, this time by arithmetic rather than by the floor. Same position, same month, same
    calendar, one absence row different — so the pair separates "the floor works" from "the answer
    happens to be zero here", which is exactly what the existing pair cannot do.
    """
    project, scenario, position, absence_type, _ = _scenario_with_calendar(
        db_session, headcount=1
    )
    make_absence(
        db_session, position, absence_type, start_date=MARCH, end_date=date(2026, 3, 31)
    )
    one_day_over = make_absence(
        db_session,
        position,
        absence_type,
        start_date=date(2026, 3, 2),  # Monday — the 23rd person-day
        end_date=date(2026, 3, 2),
    )

    over = _march(client, project.id, scenario.id)

    source = over["derived_capacity_source"]
    assert source["working_days"] == MARCH_WORKING_DAYS
    assert source["absence_day_equivalents"] == MARCH_WORKING_DAYS + 1, (
        "the deficit this test exists for is not there — without it the floor is never reached"
    )
    gross = Decimal(source["working_days"]) * HOURS_PER_DAY
    unfloored = gross - Decimal(source["absence_day_equivalents"]) * HOURS_PER_DAY
    assert unfloored == Decimal("-7.50"), (
        f"the arithmetic before the floor is {unfloored}, so this run does not exercise the floor"
    )
    assert over["derived_capacity_hours"] == "0.00", (
        f"a deficit of one working day answered {over['derived_capacity_hours']} — "
        "'-7.50' is the missing floor, '7.50' is an abs() in its place"
    )

    db_session.delete(one_day_over)
    db_session.flush()
    db_session.expire_all()
    exactly_level = _march(client, project.id, scenario.id)

    assert exactly_level["derived_capacity_source"]["absence_day_equivalents"] == (
        MARCH_WORKING_DAYS
    )
    assert exactly_level["derived_capacity_hours"] == "0.00"


# --- K-08: derived beside typed, never instead of it ---------------------------------------------


def test_k_08_the_derived_figure_is_returned_beside_the_typed_one_and_never_replaces_it(
    client: TestClient, db_session: Session, engine
) -> None:
    """K-08 — two fields in the payload, and no `UPDATE` against the allocation table on a read.

    Two reads, each with a contrast the criterion names:

    1. a month whose typed `availability_hours` (`160.00`) differs from the derived figure
       (`165.00`): **both are present and different**. An implementation returning the derived value
       under the key `availability_hours` — the criterion's mutation (b) — makes the two equal and
       fails here;
    2. a month whose typed figure is set to the derived one (`165.00`): both present and equal, so
       the test is not satisfied by "the two are always different" either.

    The third assertion is at the level of the **statements issued**, not of the payload, and it is
    what kills mutation (a) — the resolver writing its answer into the column. Every statement the
    request emits is captured, and none of them may be an `UPDATE` (or an `INSERT`) against
    `staffing_position_allocation`. A payload assertion cannot see that mutation at all: a resolver
    that stored `165.00` and then returned it would satisfy every assertion about the body, and the
    typed figure — a planner's own assertion about availability — would have been silently
    overwritten by a derived one.
    """
    project, scenario, position, _, _ = _scenario_with_calendar(db_session, headcount=1)

    statements: list[str] = []

    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement.lstrip().lower())

    sa.event.listen(sa.Engine, "before_cursor_execute", record)
    try:
        different = _march(client, project.id, scenario.id)
    finally:
        sa.event.remove(sa.Engine, "before_cursor_execute", record)

    assert different["availability_hours"] == "160.00"
    assert different["derived_capacity_hours"] == "165.00"
    assert different["availability_hours"] != different["derived_capacity_hours"]

    writes = [
        statement
        for statement in statements
        if "staffing_position_allocation" in statement
        and (statement.startswith("update") or statement.startswith("insert"))
    ]
    assert writes == [], (
        "reading the staffing grid wrote to staffing_position_allocation — the derived capacity is "
        f"being stored instead of derived: {writes}"
    )
    assert statements, "no statement was captured — the listener never fired"

    db_session.execute(
        sa.text(
            "UPDATE staffing_position_allocation SET availability_hours = 165.00"
            " WHERE position_id = :id"
        ),
        {"id": position.id},
    )
    db_session.flush()
    db_session.expire_all()
    equal = _march(client, project.id, scenario.id)

    assert equal["availability_hours"] == "165.00"
    assert equal["derived_capacity_hours"] == "165.00"
    assert "derived_capacity_state" in equal and "availability_hours" in equal, (
        "one of the two figures disappeared when they happened to be equal"
    )


def test_k_08_the_stored_availability_hours_are_untouched_by_any_number_of_reads(
    client: TestClient, db_session: Session
) -> None:
    """K-08's other half, asserted against the stored row rather than against the statements.

    Three reads of the same grid, and the column still holds what the planner typed. Redundant with
    the statement assertion above **by design**: the statement capture proves no write was *issued*,
    this proves the value did not change — and an implementation that wrote through a path the
    listener does not see (a raw connection, a trigger) would fail this one and not that one.
    """
    project, scenario, position, _, _ = _scenario_with_calendar(db_session, headcount=1)

    for _ in range(3):
        _march(client, project.id, scenario.id)

    stored = db_session.execute(
        sa.text(
            "SELECT availability_hours FROM staffing_position_allocation WHERE position_id = :id"
        ),
        {"id": position.id},
    ).scalar_one()
    assert stored == Decimal("160.00"), "a read changed the typed availability of a month"
    assert (
        db_session.execute(
            sa.select(sa.func.count()).select_from(StaffingPosition)
        ).scalar_one()
        == 1
    )
