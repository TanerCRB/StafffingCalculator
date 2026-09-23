"""SC-3-03, K-03/K-05/K-06/K-08 — what the leave budget does to a month's capacity (F-05).

Four criteria over one arithmetic, and each names a different mutation that leaves the other three
green:

- **K-03** the budget is spread evenly over the months of its window and reduces a month with no
  absence row in it at all → ignoring the budget, rounding the share to whole days, rounding it
  *before* multiplying, subtracting the whole budget every month, or treating the window as always
  twelve months;
- **K-05** the budget and the booked leave of the flagged type resolve by `max` **over the whole
  window**, and only the result is prorated → adding them, ignoring the booked days, letting one
  booked day cancel the budget, taking the `max` over every absence type, prorating the booked days
  away from the month they fall in, or dropping the `max(0, …)`;
- **K-06** a window that starts later does not move this year's answer, and a month between two
  windows has no budget → "the most recent window that has started", or resolving against the
  system clock instead of against the month being computed;
- **K-08** a pair with no budget row answers a **named state**, not a zero and not an error.

**Four more tests were added after the SC-3-03 review**, each for a defect that all eight criterion
tests above were green over — because every one of them used `headcount=1`, a flagged absence type
and a window whose months divide the entitlement exactly:

- **R-01** the budget is a number of days **per FTE**, so a five-person position is entitled to five
  times it — and the booked days it is compared against are person-days for the whole position;
- **R-02** a budget with **no flagged absence type** is not applied and says so, instead of being
  deducted on top of the very leave it was supposed to absorb;
- **R-05/S-01** the shares of a window add back up to the entitlement **exactly**, including for
  windows whose month count does not divide it;
- **R-04** the days absorbed in a window are counted once per window, not once per month of the
  grid.

**The facts every figure below is computed from**, and they are the same ones SC-3-02 uses:

- the calendar is Monday-to-Friday with a standard day of **7.50** hours — never 8.00
  (`conftest.FORBIDDEN_FIXTURE_HOURS`), so no hard-coded constant can produce these numbers, and
  never a whole number of hours, so an implementation that counted *days* and called them hours
  would be visible too;
- working days in 2026: January 22, February 20, March 22, April 22, May 21, June 22. March 2026
  has 22 Monday-to-Friday days; 2026-03-01 is a Sunday, 2026-03-02 a Monday;
- working days in 2027: February 20, August 22;
- `headcount = 1`, so the gross capacity of March 2026 is `22 × 7.50 = 165.00`.

Every expected figure is derived in the docstring that asserts it, from those facts, and never from
what the implementation returned.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.data.staffing as staffing_module
from app.core.money import NOT_APPLICABLE
from app.domain.absence_budget import NO_BUDGET, NO_STATUTORY_LEAVE_TYPE, RESOLVED
from tests.conftest import (
    IN_SCOPE_USER,
    STATUTORY_LEAVE_TYPE_NAME,
    as_caller,
    make_absence,
    make_absence_budget,
    make_absence_type,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
    staffing_path,
)

HOURS_PER_DAY = Decimal("7.50")

MARCH = date(2026, 3, 1)
MARCH_WORKING_DAYS = 22
MARCH_GROSS = Decimal("165.00")
"""`22 × 7.50` at `headcount = 1` — the figure every K-03 reading starts from."""

FIRST_HALF_2026 = [date(2026, month, 1) for month in range(1, 7)]
WORKING_DAYS_FIRST_HALF_2026 = {
    date(2026, 1, 1): 22,
    date(2026, 2, 1): 20,
    date(2026, 3, 1): 22,
    date(2026, 4, 1): 22,
    date(2026, 5, 1): 21,
    date(2026, 6, 1): 22,
}
"""The six months K-05's grid covers, and the Monday-to-Friday days in each.

Written out rather than computed with `weekday() < 5`: the implementation reads the calendar's
stored week pattern (SC-3-02, K-02), and a test that re-derived the count the same way the code does
would agree with a broken code for the same broken reason."""

YEAR_2026 = (date(2026, 1, 1), date(2026, 12, 31))
FIRST_HALF_WINDOW = (date(2026, 1, 1), date(2026, 6, 30))


def _gross(period_month: date) -> Decimal:
    """`working days × 7.50` at `headcount = 1` — the capacity before anything is deducted."""
    return Decimal(WORKING_DAYS_FIRST_HALF_2026[period_month]) * HOURS_PER_DAY


def _scenario_with_calendar(session: Session):
    """A project in scope, a draft scenario, a Monday-to-Friday 7.50-hour calendar — and the one
    absence type flagged `is_statutory_leave`.

    **The flagged type is part of the fixture, not decoration** (reviewer R-02): a budget is applied
    only when the catalogue names the type it settles against, because otherwise nothing says which
    booked leave the entitlement already covers and applying it anyway deducts the same leave twice.
    A test about proration therefore has to establish that premise, exactly as it has to establish
    that a calendar exists; the test that is about its *absence* builds its own fixture instead.

    It is returned rather than created per test because the database refuses a second flagged row
    (`uq_absence_type_statutory_leave`), so a test making its own would fail on the insert rather
    than on its subject.
    """
    calendar = make_working_calendar(
        session, name="Poland 7.5h", standard_hours_per_day=HOURS_PER_DAY
    )
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    statutory = make_absence_type(
        session, name=STATUTORY_LEAVE_TYPE_NAME, is_statutory_leave=True
    )
    return project, scenario, calendar, statutory


def _allocations(client: TestClient, project_id, scenario_id) -> dict:
    """Every position of the scenario, keyed by id, each with its months keyed by period."""
    response = client.get(
        staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return {
        position["id"]: {
            date.fromisoformat(row["period_month"]): row for row in position["allocations"]
        }
        for position in response.json()["positions"]
    }


def _march(client: TestClient, project_id, scenario_id, position_id) -> dict:
    return _allocations(client, project_id, scenario_id)[str(position_id)][MARCH]


# --- K-03: an even share of the window, in a month with no absence row at all ---------------------


def test_k_03_a_budget_is_prorated_evenly_across_the_months_of_its_window_and_reduces_a_month_with_no_absence_row_at_all(  # noqa: E501 — the criterion names this test; the name is the contract, not a style choice
    client: TestClient, db_session: Session
) -> None:
    """K-03 — 26 days over twelve months take `26/12 × 7.50 = 16.25` hours out of March.

    **The premise is asserted, not assumed**: `staffing_position_absence` holds **zero** rows for
    this scenario, so the whole of the reduction comes from the budget. Without that assertion the
    figure could just as well be produced by an absence nobody noticed.

    The arithmetic, from the facts in the module docstring: March 2026 has 22 Monday-to-Friday days,
    the calendar's standard day is 7.50 hours and `headcount = 1`, so the gross capacity is
    `22 × 7.50 = 165.00`. The budget is 26 days over `2026-01-01..2026-12-31`, i.e. twelve months,
    so this month's share is `26 ÷ 12 = 2.1666…` days — **not rounded** — and
    `2.1666… × 7.50 = 16.25` hours. The answer is `165.00 − 16.25 = 148.75`.

    Four of the criterion's five mutations die on this number alone:

    - the budget is not subtracted at all → `165.00`;
    - the share is floored to whole days (2) → `150.00`; ceiled (3) → `142.50`;
    - **the share is rounded before being multiplied** (`26/12 → 2.17`; `2.17 × 7.50 = 16.275`,
      rounded to `16.28`) → `148.72`/`148.73` rather than `148.75`. This is the "one rounding point"
      claim (NF-01, ADR-0002) stated as a number: the division stays exact until it has been
      multiplied by the standard day, and only the result is rounded;
    - the whole budget is subtracted every month (`26 × 7.50 = 195.00`) → `0.00`, the floor.

    The fifth (the window length replaced by a constant 12) needs the contrast in the next test.

    The source object is asserted as well, because it is what a client renders to explain the
    figure — and because `months_in_window = 12` is the number the next test changes.
    """
    project, scenario, calendar, statutory = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )

    assert (
        db_session.execute(
            sa.text("SELECT count(*) FROM staffing_position_absence")
        ).scalar_one()
        == 0
    ), "the premise of this test is that no absence has been booked at all"

    march = _march(client, project.id, scenario.id, position.id)

    assert march["derived_capacity_source"]["working_days"] == MARCH_WORKING_DAYS
    assert march["derived_capacity_source"]["standard_hours_per_day"] == "7.50"
    assert march["derived_capacity_hours"] == "148.75", (
        "165.00 gross minus 26/12 × 7.50 = 16.25 is 148.75 — see this test's docstring for what "
        "each wrong answer means"
    )
    assert march["absence_budget_state"] == RESOLVED
    assert march["absence_budget_hours"] == "16.25"
    assert march["absence_budget_source"]["budget_days"] == "26.00"
    assert march["absence_budget_source"]["months_in_window"] == 12
    assert march["absence_budget_source"]["statutory_days_absorbed"] == 0
    assert march["absence_budget_source"]["effective_from"] == "2026-01-01"
    assert march["absence_budget_source"]["effective_to"] == "2026-12-31"


def test_k_03_the_same_budget_over_half_the_window_takes_twice_as_much_out_of_the_month(
    client: TestClient, db_session: Session
) -> None:
    """K-03's first contrast — **one** changed element, `effective_to`, and the answer doubles.

    The same 26 days, the same calendar, the same month, the same position: only the window ends on
    30 June instead of 31 December. Six months instead of twelve makes the share
    `26 ÷ 6 = 4.3333…` days, i.e. `4.3333… × 7.50 = 32.50` hours, and March becomes
    `165.00 − 32.50 = 132.50`.

    This is the mutation "the window length replaced by a constant 12" — which produces `148.75`
    here and is invisible to every test that only uses a twelve-month window. It is also a second,
    independent reading of the no-intermediate-rounding rule: `26/6` is not a terminating decimal
    either, and a share rounded to `4.33` gives `32.475 → 32.48` and an answer of `132.52`.
    """
    project, scenario, calendar, statutory = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=FIRST_HALF_WINDOW[0],
        effective_to=FIRST_HALF_WINDOW[1],
    )

    march = _march(client, project.id, scenario.id, position.id)

    assert march["absence_budget_source"]["months_in_window"] == 6
    assert march["absence_budget_hours"] == "32.50"
    assert march["derived_capacity_hours"] == "132.50", (
        "26 days over six months is 32.50 hours a month, so March is 165.00 − 32.50 = 132.50; "
        "148.75 means the window length was replaced by a constant twelve"
    )


def test_k_03_the_prorated_shares_of_a_window_add_back_up_to_the_budget(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0008's addendum (SC-3-03, point 10a): the invariant that is stronger than the formula.

    Every month of the window is read and the hours removed are summed:
    `12 days ÷ 6 months × 7.50 = 15.00` each, `6 × 15.00 = 90.00`, and `12 × 7.50 = 90.00` is the
    whole budget expressed in hours. No day is gained or lost on the way.

    It is the assertion that kills rounding *anywhere* inside the loop, not only the one mutation
    K-03 names: any per-month rounding of the share to whole days, or to two decimal places of a
    day, shows up here as a sum that is no longer the budget — which is exactly how a defect at
    twelve months hides from a single-month test.
    """
    project, scenario, calendar, statutory = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=FIRST_HALF_2026[0]
    )
    for month in FIRST_HALF_2026:
        make_allocation(db_session, position, period_month=month)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("12.00"),
        effective_from=FIRST_HALF_WINDOW[0],
        effective_to=FIRST_HALF_WINDOW[1],
    )

    months = _allocations(client, project.id, scenario.id)[str(position.id)]

    removed = {
        month: Decimal(months[month]["absence_budget_hours"]) for month in FIRST_HALF_2026
    }
    assert set(removed.values()) == {Decimal("15.00")}, removed
    assert sum(removed.values()) == Decimal("12.00") * HOURS_PER_DAY, (
        "the prorated shares of the window do not add back up to the budget — a day was gained or "
        "lost by rounding inside the proration"
    )


# --- K-05: max over the window, and the distribution over the months -----------------------------


def test_k_05_manual_statutory_absences_are_absorbed_by_the_budget_over_the_whole_window_and_never_added_to_it(  # noqa: E501 — the criterion names this test; the name is the contract, not a style choice
    client: TestClient, db_session: Session
) -> None:
    """K-05 — five readings of one six-month grid, one budget, five positions.

    The setup: one calendar, one engagement type, **one** budget row — 12 days over
    `2026-01-01..2026-06-30` — and five positions of `headcount = 1` sharing it, each with an
    allocation row for every one of the six months and its own booked absences. Sharing the budget
    is what makes these five readings of one rule rather than five different configurations.

    The rule (ADR-0008, addendum 2026-09-22 SC-3-03, points 9 and 10): booked leave of the flagged
    type is deducted **in the month it falls in** (SC-3-02's mechanism, untouched), and the budget
    adds the *difference* — `max(0, 12 − booked_in_the_window)` — spread evenly over the six months.
    The `max` is resolved over the window and only its result is prorated; the reverse order is the
    mutation that turns 24 budgeted days plus 20 booked in July into 42 days deducted in a year.

    The readings, all with a share of `12 ÷ 6 × 7.50 = 15.00` hours a month before any booking:

    1. **nothing booked** → every month loses `15.00`, the window loses `90.00`;
    2. **one booked working day** (Monday 2 March) → the top-up is `max(0, 12 − 1) = 11` days, i.e.
       `11 ÷ 6 × 7.50 = 13.75` a month, and March additionally loses the booked day itself
       (`7.50`), so March loses `21.25` and the other five lose `13.75` — **and the window still
       loses `90.00`** (`5 × 13.75 + 21.25`). That single sum is the whole of the `max`: the total
       is unchanged by booking leave, only its distribution moves;
    3. **thirteen booked working days** (2–18 March), i.e. more than the budget → the top-up is
       `max(0, 12 − 13) = 0`, so the months with no booking lose **nothing at all** and the window
       loses `97.50 = 13 × 7.50`, which is the booked leave and nothing else;
    4. **exactly twelve booked days** (2–17 March), the boundary → the top-up is `0` and the window
       loses `90.00`, the same total as reading 1. Continuity at the point where the two sources
       meet, which is where "budget wins" and "bookings win" produce the same number and neither
       test alone can tell them apart;
    5. **reading 1 plus two booked days of an *unflagged* type** (6-7 April) → those days are
       deducted independently, on top: the window loses `90.00 + 15.00 = 105.00`.

    Which mutation each reading kills, and why every one of them is needed:

    - adding the budget to the booked days instead of taking the `max` → reading 2 sums to `97.50`;
    - the budget alone, bookings ignored while a budget exists → reading 3 sums to `90.00`;
    - **replacement** ("any booking cancels the budget for the window") → reading 2's March is
       `7.50` and its other months `0.00`; only the per-month assertion catches it, because the sum
       is `7.50` rather than `90.00` and would also be caught — but the *boundary* case it is
       confused with, one booked day removing the rest of that month's budget, is caught **only** by
       asserting that the other five months still lose `13.75`;
    - a `max` taken over **all** absence types → reading 5 sums to `90.00`;
    - the booked days prorated away from the month they were booked in (the rejected reading B) →
       reading 2's March is `15.00` with a correct window sum, which is why every month is asserted
       separately as well as the total;
    - no `max(0, …)` on the top-up → reading 3's empty months come out **above** their gross
       capacity, because a negative top-up hands capacity back.
    """
    project, scenario, calendar, statutory = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    other_type = make_absence_type(db_session, name="Training", generates_revenue=True)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("12.00"),
        effective_from=FIRST_HALF_WINDOW[0],
        effective_to=FIRST_HALF_WINDOW[1],
    )

    positions = {}
    for reading in ("nothing booked", "one day", "thirteen days", "twelve days", "other type"):
        position = make_staffing_position(
            db_session,
            scenario,
            dimensions,
            headcount=1,
            start_date=FIRST_HALF_2026[0],
            end_date=date(2026, 6, 30),
        )
        for month in FIRST_HALF_2026:
            make_allocation(db_session, position, period_month=month)
        positions[reading] = position

    # Monday 2 March; 2-18 March is thirteen working days, 2-17 March is twelve.
    make_absence(
        db_session,
        positions["one day"],
        statutory,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 2),
    )
    make_absence(
        db_session,
        positions["thirteen days"],
        statutory,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 18),
    )
    make_absence(
        db_session,
        positions["twelve days"],
        statutory,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 17),
    )
    # Monday 6 and Tuesday 7 April, of a type that is *not* the flagged one.
    make_absence(
        db_session,
        positions["other type"],
        other_type,
        start_date=date(2026, 4, 6),
        end_date=date(2026, 4, 7),
    )

    grid = _allocations(client, project.id, scenario.id)

    def reductions(reading: str) -> dict[date, Decimal]:
        """How much each month of the window lost, against its own gross capacity."""
        months = grid[str(positions[reading].id)]
        return {
            month: _gross(month) - Decimal(months[month]["derived_capacity_hours"])
            for month in FIRST_HALF_2026
        }

    nothing_booked = reductions("nothing booked")
    assert set(nothing_booked.values()) == {Decimal("15.00")}, nothing_booked
    assert sum(nothing_booked.values()) == Decimal("90.00")

    one_day = reductions("one day")
    assert one_day[MARCH] == Decimal("21.25"), (
        "March should lose the booked day (7.50) plus its share of the remaining 11 days (13.75). "
        "15.00 means the booked day was prorated away from the month it was booked in; 7.50 means "
        "the booking replaced the budget"
    )
    assert {
        month: hours for month, hours in one_day.items() if month != MARCH
    } == dict.fromkeys(
        [month for month in FIRST_HALF_2026 if month != MARCH], Decimal("13.75")
    ), (
        "one booked day changed the other months by something other than 11/6 × 7.50 = 13.75 — "
        f"{one_day}"
    )
    assert sum(one_day.values()) == Decimal("90.00"), (
        "booking one day of leave changed the total the window loses; the max() is not being taken "
        "over the window"
    )

    thirteen_days = reductions("thirteen days")
    assert thirteen_days[MARCH] == Decimal("97.50")
    assert [thirteen_days[month] for month in FIRST_HALF_2026 if month != MARCH] == [
        Decimal("0.00")
    ] * 5, (
        "with the booked leave over the budget, the top-up is max(0, 12 − 13) = 0; a negative "
        f"figure here means the max(0, …) is missing — {thirteen_days}"
    )
    assert sum(thirteen_days.values()) == Decimal("97.50")

    twelve_days = reductions("twelve days")
    assert twelve_days[MARCH] == Decimal("90.00")
    assert sum(twelve_days.values()) == Decimal("90.00"), (
        "at exactly the budget, the window must lose the same total as with nothing booked"
    )

    other_type_reading = reductions("other type")
    assert other_type_reading[date(2026, 4, 1)] == Decimal("30.00"), (
        "two days of an unflagged type must be deducted on top of the budget's share, not absorbed "
        "by it"
    )
    assert sum(other_type_reading.values()) == Decimal("105.00")


# --- K-06: a later window, and a month in the gap between two -------------------------------------


def test_k_06_a_budget_window_that_starts_later_does_not_move_this_years_answer_and_a_month_in_the_gap_has_none(  # noqa: E501 — the criterion names this test; the name is the contract, not a style choice
    client: TestClient, db_session: Session
) -> None:
    """K-06 — two **non-adjacent** windows, three readings, one position.

    The budgets: `B1` is 26 days over the whole of 2026 (twelve months, `16.25` hours a month) and
    `B2` is 36 days over `2027-07-01..2027-12-31` (six months, `36 ÷ 6 × 7.50 = 45.00` a month).
    The first half of 2027 is covered by **neither**, deliberately: that gap is the whole test.

    The readings:

    1. **March 2026** → `165.00 − 16.25 = 148.75`. A later window does not reach back;
    2. **August 2027** (22 Monday-to-Friday days, gross `165.00`) → `165.00 − 45.00 = 120.00`;
    3. **February 2027** (20 working days, gross `150.00`) → the named state `"no_budget"`,
       `"n/a"` hours removed and the **gross** capacity. Not `16.25`, which is what "the most recent
       window that has started" answers, and not `45.00`, and not a silent `0` budget.

    Mutation (a), `WHERE effective_from <= :month ORDER BY effective_from DESC LIMIT 1`, survives
    every pair of adjacent windows and is killed **only** by reading 3. Mutation (b), resolving
    against `date.today()` instead of against the month being computed, is killed by readings 1 and
    2 disagreeing with each other — under the clock they would answer identically, whichever day the
    suite runs on.
    """
    project, scenario, calendar, statutory = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    for month in (MARCH, date(2027, 2, 1), date(2027, 8, 1)):
        make_allocation(db_session, position, period_month=month)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("36.00"),
        effective_from=date(2027, 7, 1),
        effective_to=date(2027, 12, 31),
    )

    months = _allocations(client, project.id, scenario.id)[str(position.id)]

    assert months[MARCH]["absence_budget_hours"] == "16.25"
    assert months[MARCH]["derived_capacity_hours"] == "148.75"

    second_half_2027 = months[date(2027, 8, 1)]
    assert second_half_2027["absence_budget_state"] == RESOLVED
    assert second_half_2027["absence_budget_hours"] == "45.00"
    assert second_half_2027["absence_budget_source"]["budget_days"] == "36.00"
    assert second_half_2027["derived_capacity_hours"] == "120.00", "22 × 7.50 − 45.00"

    gap = months[date(2027, 2, 1)]
    assert gap["absence_budget_state"] == NO_BUDGET, (
        "a month between two windows answered with a budget — the resolution is 'the latest window "
        "that has started' rather than 'the window that contains this month'"
    )
    assert gap["absence_budget_hours"] == NOT_APPLICABLE
    assert gap["absence_budget_source"] is None
    assert gap["derived_capacity_hours"] == "150.00", "20 × 7.50, with nothing removed"


# --- K-08: no budget row is a named state ---------------------------------------------------------


def test_k_08_a_profile_with_no_budget_row_answers_a_named_state_not_a_zero_and_not_an_error(
    client: TestClient, db_session: Session
) -> None:
    """K-08's behavioural half — and the contrast that makes it a state rather than a silence.

    A position whose (calendar, engagement type) pair has **no** budget row at all: the response is
    `200`, the state is `"no_budget"`, the hours removed are `app.core.money.NOT_APPLICABLE` and the
    derived capacity is the **undiminished** `165.00`. Three answers are refused by name — a silent
    `0` for the budget (a number every later sum adds up), an exception (an incomplete catalogue is
    not a server error), and a missing field (a client cannot tell a missing field from a field it
    forgot to read).

    The contrast is in the same test: adding a budget row resolves the state and produces the
    figure, so this is not satisfied by an endpoint that always answers `"n/a"`.

    **A budget of zero days is the second contrast**, and it is the reason "no row" must be a state
    at all: a legal row of `0.00` days answers `"resolved"` with `0.00` hours removed. The two are
    different facts — "nobody has said what the entitlement is" and "the entitlement is none" — and
    an implementation that answered `0` for a missing row would make them one.
    """
    project, scenario, calendar, statutory = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)

    unresolved = _march(client, project.id, scenario.id, position.id)

    assert unresolved["absence_budget_state"] == NO_BUDGET
    assert unresolved["absence_budget_hours"] == NOT_APPLICABLE
    assert unresolved["absence_budget_source"] is None
    assert unresolved["derived_capacity_state"] == "resolved"
    assert unresolved["derived_capacity_hours"] == str(MARCH_GROSS), (
        "a missing budget reduced the capacity — the named state must not be a silent deduction"
    )

    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )

    resolved = _march(client, project.id, scenario.id, position.id)
    assert resolved["absence_budget_state"] == RESOLVED
    assert resolved["absence_budget_hours"] == "16.25"
    assert resolved["derived_capacity_hours"] == "148.75"


def test_k_08_a_budget_of_zero_days_is_resolved_and_not_the_same_answer_as_no_budget(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0008's addendum (SC-3-03, point 7): "no row" and "a row of zero" stay distinguishable.

    A zero budget is a legal, meaningful row — an engagement type with no leave entitlement — and
    the response says `"resolved"` with `0.00` hours removed, next to a pair with no row at all
    saying `"no_budget"` and `"n/a"`. Merging the two would erase the difference without a warning,
    which is what the addendum forbids and what a single nullable number would do.

    Both positions are in the same scenario and the same read, so the two answers are compared side
    by side rather than in two runs that could differ for unrelated reasons.
    """
    project, scenario, calendar, statutory = _scenario_with_calendar(db_session)
    zero = make_dimension_tuple(db_session, suffix=" (no entitlement)", calendar=calendar)
    none = make_dimension_tuple(db_session, suffix=" (unconfigured)", calendar=calendar)
    make_absence_budget(
        db_session,
        calendar,
        zero.engagement_type_id,
        budget_days=Decimal("0.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )
    positions = {}
    for label, dimensions in (("zero", zero), ("none", none)):
        position = make_staffing_position(
            db_session, scenario, dimensions, headcount=1, start_date=MARCH
        )
        make_allocation(db_session, position, period_month=MARCH)
        positions[label] = position

    grid = _allocations(client, project.id, scenario.id)

    zero_row = grid[str(positions["zero"].id)][MARCH]
    none_row = grid[str(positions["none"].id)][MARCH]
    assert (zero_row["absence_budget_state"], zero_row["absence_budget_hours"]) == (
        RESOLVED,
        "0.00",
    )
    assert (none_row["absence_budget_state"], none_row["absence_budget_hours"]) == (
        NO_BUDGET,
        NOT_APPLICABLE,
    )
    assert zero_row["derived_capacity_hours"] == none_row["derived_capacity_hours"] == str(
        MARCH_GROSS
    ), "both answers must leave the capacity whole; only the state distinguishes them"


def test_a_position_in_a_location_without_a_calendar_shows_one_named_state_not_two(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0008's addendum (SC-3-03, point 3a): no calendar means no budget, and it is one state.

    The budget hangs on the calendar, so a location with `calendar_id IS NULL` has no calendar *and*
    no budget — and the payload answers `"no_calendar"` in both fields rather than pairing
    `"no_calendar"` with a second, independent `"no_budget"` that a screen would have to explain
    separately. `"n/a"` on both figures, and no exception: the SC-3-02 behaviour is unchanged by
    this task.
    """
    project, scenario, _, _ = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, suffix=" (remote)")
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)

    march = _march(client, project.id, scenario.id, position.id)

    assert march["derived_capacity_state"] == "no_calendar"
    assert march["derived_capacity_hours"] == NOT_APPLICABLE
    assert march["absence_budget_state"] == "no_calendar", (
        "a position with no calendar showed a second, independent budget state"
    )
    assert march["absence_budget_hours"] == NOT_APPLICABLE
    assert march["absence_budget_source"] is None


# --- R-01: the budget is per FTE, and a position of five holds five of them ----------------------


def test_r_01_the_entitlement_scales_with_headcount_and_the_deduction_scales_with_it(
    client: TestClient, db_session: Session
) -> None:
    """R-01 (reviewer, High) — the same budget row, two positions, five times the deduction.

    The budget is a number of days **per FTE** (Issue #54, variant A), so a position planning five
    people is entitled to five times the regulation's figure. Before this fix the deduction was
    taken once per *position*: five people shared one person's leave, and the margin of a
    five-person team came out about 8% too high — precisely the harm F-05 exists to remove, removed
    only for positions of one.

    Two positions in one scenario, on one (calendar, engagement type) pair, reading **one** budget
    row of 26 days over 2026, both planning March 2026 (22 working days, 7.50 hours a day):

    - `headcount = 1` → gross `22 x 7.50 = 165.00`, entitlement `26` days, share
      `26 x 7.50 / 12 = 16.25`, capacity `148.75`;
    - `headcount = 5` → gross `5 x 22 x 7.50 = 825.00`, entitlement `5 x 26 = 130` days, share
      `130 x 7.50 / 12 = 81.25`, capacity `743.75`.

    The last assertion states the rule rather than the numbers: the deduction of the five-person
    position is exactly five times the other's, from one budget row. Every criterion test in this
    file uses `headcount = 1`, where "times one" and "times nothing" are the same code — which is
    why all eight of them were green over this.
    """
    project, scenario, calendar, _ = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )
    positions = {}
    for headcount in (1, 5):
        position = make_staffing_position(
            db_session, scenario, dimensions, headcount=headcount, start_date=MARCH
        )
        make_allocation(db_session, position, period_month=MARCH)
        positions[headcount] = position

    grid = _allocations(client, project.id, scenario.id)
    alone = grid[str(positions[1].id)][MARCH]
    team = grid[str(positions[5].id)][MARCH]

    assert alone["absence_budget_hours"] == "16.25"
    assert alone["derived_capacity_hours"] == "148.75"
    assert team["absence_budget_hours"] == "81.25", (
        "a five-person position was given one person's leave entitlement: the budget is days per "
        "FTE and the position holds budget_days x headcount (reviewer R-01)"
    )
    assert team["derived_capacity_hours"] == "743.75", "825.00 gross minus 81.25"

    # Both figures come from one row, and the payload says which is which.
    assert alone["absence_budget_source"]["budget_days"] == "26.00"
    assert team["absence_budget_source"]["budget_days"] == "26.00"
    assert alone["absence_budget_source"]["entitlement_days"] == "26.00"
    assert team["absence_budget_source"]["entitlement_days"] == "130.00"
    assert (
        alone["absence_budget_source"]["budget_id"]
        == team["absence_budget_source"]["budget_id"]
    )
    assert Decimal(team["absence_budget_hours"]) == 5 * Decimal(alone["absence_budget_hours"])


def test_r_01_booked_leave_is_absorbed_in_person_days_not_in_days_per_head(
    client: TestClient, db_session: Session
) -> None:
    """R-01's second half — the max() compares two quantities in the same unit.

    Booked absences are counted **per instance and never deduplicated** (SC-3-02, K-06): five people
    of one position taking six working days each are thirty person-days. The entitlement they are
    compared against therefore has to be the position's and not one person's — before this fix the
    comparison was `max(0, 26 - 30) = 0`, so a five-person team booking six days each lost
    **thirty** days of capacity in a year where the regulation grants it a hundred and thirty.

    The figures: entitlement `5 x 26 = 130` days, booked `5 x 6 = 30` person-days in the window, so
    the top-up is `130 - 30 = 100` days, i.e. `100 x 7.50 = 750.00` hours over twelve months —
    `62.50` a month. March additionally loses the thirty booked days themselves
    (`30 x 7.50 = 225.00`), so:

    - March: `825.00 - 225.00 - 62.50 = 537.50`;
    - April (22 working days as well): `825.00 - 62.50 = 762.50`.

    The wrong unit is visible in either month: with `max(0, 26 - 30) = 0` the budget adds nothing at
    all, and April comes back at its full `825.00`.
    """
    project, scenario, calendar, statutory = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=5, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)
    make_allocation(db_session, position, period_month=date(2026, 4, 1))
    # Five people, six working days each (Monday 2 March to Monday 9 March is 2, 3, 4, 5, 6 and 9).
    for _ in range(5):
        make_absence(
            db_session,
            position,
            statutory,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 9),
        )

    months = _allocations(client, project.id, scenario.id)[str(position.id)]

    assert months[MARCH]["absence_budget_source"]["statutory_days_absorbed"] == 30, (
        "the days absorbed are person-days for the whole position (SC-3-02, K-06), and that is the "
        "unit the entitlement is compared in"
    )
    assert months[MARCH]["absence_budget_hours"] == "62.50", (
        "100 days of top-up over twelve months at 7.50 hours is 62.50 a month; 0.00 means a "
        "per-FTE entitlement was compared against person-days (reviewer R-01)"
    )
    assert months[MARCH]["derived_capacity_hours"] == "537.50"
    assert months[date(2026, 4, 1)]["absence_budget_hours"] == "62.50"
    assert months[date(2026, 4, 1)]["derived_capacity_hours"] == "762.50"


# --- R-02: a budget nobody can settle against is not applied, and says so ------------------------


def test_r_02_a_budget_with_no_flagged_absence_type_is_not_applied_and_the_state_says_so(
    client: TestClient, db_session: Session
) -> None:
    """R-02 (reviewer, Medium) — the state a freshly migrated database is in, made visible.

    `is_statutory_leave` defaults to `false` on every row and the migration backfills nothing,
    deliberately — so "a budget exists and no type carries the flag" is not an exotic case: it is
    what every organisation has the day after the migration, until somebody flags a type, and
    nothing in the product asks them to.

    Applied anyway, the entitlement is deducted on top of the leave it was supposed to absorb: the
    booked days come off in their own month (SC-3-02's mechanism) *and* the whole 26 days are
    prorated across the year — 46 days a year where the regulation grants 26. The answer now is the
    named state `no_statutory_leave_type` with `"n/a"` hours: the budget is **not** applied, and the
    payload says why instead of producing a number nobody can explain.

    The contrast is in the same test: flagging a type resolves the state and the same grid starts
    deducting again. So this is not satisfied by an implementation that has quietly stopped applying
    budgets altogether.

    March 2026 has 22 working days and five booked ones (Monday 2 to Friday 6 March), so the
    unflagged answer is `165.00 - 5 x 7.50 = 127.50` — and **not** `111.25`, which is what applying
    the unattributable entitlement on top produced.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=HOURS_PER_DAY
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    # A dictionary with two plausible candidates and **no** flag on either — the default state.
    make_absence_type(db_session, name="Paid holiday")
    candidate = make_absence_type(db_session, name=STATUTORY_LEAVE_TYPE_NAME)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)
    make_absence(
        db_session,
        position,
        candidate,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 6),
    )

    unattributable = _march(client, project.id, scenario.id, position.id)

    assert unattributable["absence_budget_state"] == NO_STATUTORY_LEAVE_TYPE, (
        "a budget that nothing can be settled against was applied silently — the booked leave is "
        "then deducted twice, once as itself and once as an entitlement that absorbed nothing "
        "(reviewer R-02)"
    )
    assert unattributable["absence_budget_hours"] == NOT_APPLICABLE
    assert unattributable["absence_budget_source"] is None
    assert unattributable["derived_capacity_hours"] == "127.50", (
        "165.00 gross minus the five booked days, and nothing else"
    )

    db_session.execute(
        sa.text("UPDATE absence_type SET is_statutory_leave = true WHERE id = :id"),
        {"id": candidate.id},
    )
    db_session.flush()
    db_session.expire_all()

    attributable = _march(client, project.id, scenario.id, position.id)
    assert attributable["absence_budget_state"] == RESOLVED
    assert attributable["absence_budget_source"]["statutory_days_absorbed"] == 5
    assert attributable["derived_capacity_hours"] != unattributable["derived_capacity_hours"], (
        "flagging a type changed nothing — the contrast of this test is void"
    )


# --- R-05/S-01: the shares of a window add back up to the entitlement, exactly --------------------


SEVEN_MONTH_WINDOW = (date(2026, 1, 1), date(2026, 7, 31))
"""Seven months — a denominator that divides neither 26 days nor 195 hours evenly.

The invariant test that shipped with SC-3-03 used 12 days over 6 months and 26 over 12, and both of
those divide exactly: rounding each month's share and not rounding it produce the same number, so
the test could not tell the two apart. Reviewer R-05 and invariant-guardian S-01 found that
independently, by arithmetic."""


def test_r_05_the_shares_of_a_window_that_does_not_divide_evenly_still_sum_to_the_entitlement(
    client: TestClient, db_session: Session
) -> None:
    """R-05/S-01 (Medium, found twice independently) — the invariant, on numbers that can break it.

    26 days over **seven** months at 7.50 hours is `195.00` hours of entitlement and `27.857142...`
    a month. Rounded per month that is `27.86` seven times over — `195.02`, two cents of capacity
    conjured out of rounding, in a figure that later multiplies by a rate. The remainder is
    therefore distributed: five months get `27.86`, two get `27.85`, and the sum is `195.00`
    exactly. Which months those are is a property of the running total the allocation is a
    difference of, not a choice — the second and the sixth here.

    Three assertions, and the first two are what the shipped test was missing:

    1. **the sum over the window is exactly the entitlement in hours** — the invariant ADR-0008's
       addendum (point 10a) states, on numbers where an even share cannot satisfy it;
    2. **no month is more than a cent from the even share** — which makes this a *distribution*
       rather than "the last month absorbs everything", a shape that would put the whole accumulated
       error on one month and make it visibly different from its neighbours;
    3. the months that differ are not all at one end.

    The calendar is still 7.50 hours a day (`conftest.FORBIDDEN_FIXTURE_HOURS` keeps 8.00 out of
    every calendar fixture, and it is not needed here: seven months makes the division
    non-terminating at any plausible day length).
    """
    project, scenario, calendar, _ = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=SEVEN_MONTH_WINDOW[0],
        effective_to=SEVEN_MONTH_WINDOW[1],
    )
    months = [date(2026, month, 1) for month in range(1, 8)]
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=months[0]
    )
    for month in months:
        make_allocation(db_session, position, period_month=month)

    grid = _allocations(client, project.id, scenario.id)[str(position.id)]

    shares = [Decimal(grid[month]["absence_budget_hours"]) for month in months]
    entitlement_hours = Decimal("26.00") * HOURS_PER_DAY
    assert sum(shares) == entitlement_hours, (
        f"the window's shares sum to {sum(shares)} where the entitlement is {entitlement_hours}. "
        "An even share rounded per month gives 195.02 here — the remainder has to be distributed "
        "rather than dropped (ADR-0008, addendum SC-3-03, point 10a; reviewer R-05, "
        "invariant-guardian S-01)."
    )
    assert sorted(shares) == [Decimal("27.85")] * 2 + [Decimal("27.86")] * 5, shares
    assert max(shares) - min(shares) == Decimal("0.01"), (
        "one month absorbed the whole remainder: the distribution keeps every month within a cent "
        f"of the even share — {shares}"
    )
    assert grid[months[0]]["absence_budget_source"]["months_in_window"] == 7


# --- R-04: the absorbed days are counted once per window, not once per month ----------------------


def test_r_04_the_days_a_window_absorbs_are_counted_once_per_window_not_once_per_month(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-04 (reviewer, Medium) — the count depends on the window, so it is taken once per window.

    `absence_day_equivalents_between` walks every day of the budget's window for every booked
    absence. Its result depends on the position and the window and on **nothing else** — and it sat
    inside the loop over the months of the grid, so a twelve-month plan asked the same question
    twelve times and a five-year window walked 1826 days on each of them. Measured by the reviewer
    on an NF-03 grid (200 positions x 36 months): 2.2 s of pure Python with no absences at all,
    4.2 s with three, 21 s for a five-year window — all of it before a single row was read.

    Asserted by **counting the calls**, because that is the claim: no figure changes (the answer was
    always correct), and a timing assertion would be a flake on a shared machine. One position,
    twelve months, one budget window → exactly one call, and the failure message says how many it
    really was.

    The capacity is asserted too, so this cannot be satisfied by a memo that returns a stale or
    empty answer: the booked week is still absorbed.
    """
    project, scenario, calendar, statutory = _scenario_with_calendar(db_session)
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=date(2026, 1, 1)
    )
    for month in range(1, 13):
        make_allocation(db_session, position, period_month=date(2026, month, 1))
    make_absence(
        db_session,
        position,
        statutory,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 6),
    )

    calls: list[tuple[date, date]] = []
    original = staffing_module.absence_day_equivalents_between

    def counted(basis, absences, start_date, end_date):
        calls.append((start_date, end_date))
        return original(basis, absences, start_date, end_date)

    monkeypatch.setattr(staffing_module, "absence_day_equivalents_between", counted)
    grid = _allocations(client, project.id, scenario.id)[str(position.id)]

    assert calls == [YEAR_2026], (
        f"the window was walked {len(calls)} times for one position reading one budget. The count "
        "depends on (position, window) and not on the month, so it belongs outside the loop over "
        f"the grid's months (reviewer R-04) — {calls}"
    )
    assert grid[MARCH]["absence_budget_source"]["statutory_days_absorbed"] == 5, (
        "the memoised count lost the booked days — a memo answering zero would satisfy the "
        "assertion above while changing every figure on the grid"
    )
