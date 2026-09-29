"""SC-5-08, K-01..K-05 — daily and monthly cost rates (F-07; Issue #80; ADR-0013, ADR-0004 and
ADR-0005, addenda 2026-09-29).

Every figure is read through the real endpoints (`GET …/personnel-cost`, `GET …/what-if`), by a
caller for whom the cost gate is **open** (`caller_holding(*Permission)` plus the `project_access`
flag), against a real PostgreSQL. **The expected values are literals**, fixed in Issue #80 and
worked out by hand in the comments — never derived from the code under test:

| case                                   | arithmetic                          | result     |
|----------------------------------------|-------------------------------------|------------|
| hour, 160 h at 100 per hour            | 160 × 100                           | 16000.00   |
| day, 8 h/day, 20 h at 800 per day      | 20 / 8 × 800                        | 2000.00    |
| month (May 2026: 21 days × 8 h = 168)  | 10500 × 84 / 168                    | 5250.00    |
|   … 168 h (full capacity)              | 10500 × 168 / 168                   | 10500.00   |
|   … 176 h (overtime, not capped)       | 10500 × 176 / 168                   | 11000.00   |
|   … 0 h (calendar present)             | 10500 × 0 / 168                     | 0.00       |
| day, 7.5 h/day, 10 h at 1000 per day  | 10 / 7.5 × 1000 = 1333.333…         | 1333.33    |

The standard March 2026 fixture of the paid-absence tests (calendar 7.5 h/day Monday–Friday, 22
working days, so a month is 165 h; 120 planned hours; paid-absence hours 97.50, of which 30.00 from
the budget) gives, per unit: hour at 120 → base 14400.00; **day at 120 per day** → base 1920.00,
paid absence 1560.00 (budget part 480.00); **month at 3300** → base 2400.00, paid absence 1950.00
(budget part 600.00); with a 10% surcharge the month figures are 2640.00 / 240.00 and 2145.00 /
195.00; a 10% what-if raise makes them 2640.00 and 2145.00.

The calendar of the 8-hour cases is built directly (`_calendar`): `make_working_calendar` refuses a
standard day of 8.00 hours on purpose (criterion K-01 of SC-3-02), and the Issue's literals need it;
the 7.5-hour cases keep that guarantee, so a hard-coded `8` in the formula fails them.
"""

import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models import (
    ApprovedSnapshotCatalogDefaultRate,
    ApprovedSnapshotWorkingCalendar,
    CatalogDefaultRate,
    CatalogLocation,
    ScenarioStatus,
    WorkingCalendar,
    WorkingCalendarDayKind,
)
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    MONDAY_TO_FRIDAY,
    DimensionTuple,
    approve_path,
    as_caller,
    caller_holding,
    make_allocation,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
)
from tests.test_paid_absence_cost import _catalog, _scenario, _update
from tests.test_personnel_cost import _revenue, _set_rule
from tests.test_scenario_what_if import what_if_path

MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
MAY = date(2026, 5, 1)

NEVER_WORKING = "0000000"


def personnel_cost_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/personnel-cost"


# --- fixtures ------------------------------------------------------------------------------------


def _calendar(
    session: Session, *, hours: str, name: str, pattern: str = MONDAY_TO_FRIDAY
) -> WorkingCalendar:
    """A calendar written directly — including the 8.00-hour day `make_working_calendar` refuses."""
    calendar = WorkingCalendar(
        id=uuid.uuid4(),
        name=name,
        standard_hours_per_day=Decimal(hours),
        week_pattern=pattern,
    )
    session.add(calendar)
    session.flush()
    return calendar


def _plan(
    session: Session,
    *,
    unit: str,
    rate: str,
    hours: tuple[str, ...],
    months: tuple[date, ...] = (MAR,),
    calendar: WorkingCalendar | None = None,
    suffix: str,
    surcharge: str = "0",
    project=None,
) -> tuple[Any, Any, DimensionTuple, Any, CatalogDefaultRate]:
    """A project in scope with the cost flag set, a draft scenario, one position on a fresh tuple
    (whose location uses `calendar`, or none) with one allocation per month, and one open-ended PLN
    cost rate window in `unit`. `hours[i]` are the planned hours of `months[i]`."""
    if project is None:
        project = make_project(
            session, name=f"Aurora {suffix}", accessible_to=(IN_SCOPE_USER,),
            cost_visible_to=(IN_SCOPE_USER,),
        )
    scenario = make_scenario(session, project, name=f"Baseline {suffix}")
    dimensions = make_dimension_tuple(session, suffix=f" {suffix}", calendar=calendar)
    position = make_staffing_position(
        session, scenario, dimensions, headcount=2, start_date=months[0]
    )
    for month, planned in zip(months, hours, strict=True):
        make_allocation(session, position, period_month=month,
                        planned_allocation_hours=Decimal(planned))
    window = make_rate(
        session, dimensions, effective_from=date(2026, 1, 1), currency="PLN",
        default_cost_rate=Decimal(rate), default_selling_rate=Decimal("200.0000"),
        cost_rate_unit=unit, surcharge_percent=Decimal(surcharge),
    )
    return project, scenario, dimensions, position, window


def _cost(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    with caller_holding(*Permission):
        response = client.get(personnel_cost_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()["personnel_cost"]


def _approve(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    response = client.post(approve_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()


def _frozen_rates(
    session: Session, scenario_id: uuid.UUID
) -> list[ApprovedSnapshotCatalogDefaultRate]:
    session.expire_all()
    return list(
        session.execute(
            sa.select(ApprovedSnapshotCatalogDefaultRate).where(
                ApprovedSnapshotCatalogDefaultRate.scenario_id == scenario_id
            )
        ).scalars()
    )


# --- K-01: the three units price the same hours by their own rule --------------------------------


@pytest.mark.parametrize(
    ("unit", "rate", "hours", "calendar_hours", "month", "expected"),
    [
        pytest.param("hour", "100.0000", "160.00", None, MAR, "16000.00", id="hour-160h-at-100"),
        pytest.param("day", "800.0000", "20.00", "8.00", MAR, "2000.00", id="day-8h-20h-at-800"),
        pytest.param("month", "10500.0000", "84.00", "8.00", MAY, "5250.00", id="month-half"),
        pytest.param("month", "10500.0000", "168.00", "8.00", MAY, "10500.00", id="month-full"),
        pytest.param("month", "10500.0000", "176.00", "8.00", MAY, "11000.00", id="month-overtime"),
        pytest.param("month", "10500.0000", "0.00", "8.00", MAY, "0.00", id="month-zero-hours"),
        pytest.param("day", "1000.0000", "10.00", "7.50", MAR, "1333.33", id="day-7.5h-10h"),
    ],
)
def test_k_01_each_unit_prices_the_issue_literals(
    client: TestClient, db_session: Session, unit: str, rate: str, hours: str,
    calendar_hours: str | None, month: date, expected: str,
) -> None:
    """K-01 — the literals of Issue #80. Mutations they kill: unit ignored (every case would be
    `hours × rate`), day and month formulas swapped, hours capped at capacity (176 h → 10500.00), a
    hard-coded 8-hour day (the 7.5-hour case), the zero-hour month read as `no_cost_rate`."""
    calendar = (
        None if calendar_hours is None
        else _calendar(db_session, hours=calendar_hours, name=f"Cal {unit} {hours}")
    )
    project, scenario, *_ = _plan(
        db_session, unit=unit, rate=rate, hours=(hours,), months=(month,), calendar=calendar,
        suffix=f"{unit}-{hours}",
    )

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"], cost["currency"]) == ("calculated", expected, "PLN")


def test_k_01_the_same_hours_priced_in_the_three_units_give_three_results(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — 120 planned hours in March 2026 (calendar 7.5 h/day, 22 working days): hour at 120 is
    14400.00, day at 120 is 120 / 7.5 × 120 = 1920.00, month at 3300 is 3300 × 120 / 165 = 2400.00.
    Mutation: day and month formulas swapped (day would read 3300 × 120 / 165 = 2400.00 and the
    month 1920.00 — the two rates are chosen so a swap moves both figures)."""
    calendar = _calendar(db_session, hours="7.50", name="Cal three units")
    figures = {}
    for unit, rate in (("hour", "120.0000"), ("day", "120.0000"), ("month", "3300.0000")):
        project, scenario, *_ = _plan(
            db_session, unit=unit, rate=rate, hours=("120.00",), calendar=calendar,
            suffix=f"three-{unit}",
        )
        figures[unit] = _cost(client, project.id, scenario.id)["amount"]

    assert figures == {"hour": "14400.00", "day": "1920.00", "month": "2400.00"}


def test_k_01_rounding_happens_once_at_the_end_never_per_day_or_per_month(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — a day rate of 1000 at 7.5 h/day over two months of 1 planned hour each: every month is
    1 / 7.5 × 1000 = 133.3333…, the sum is 266.6667 and rounds to **266.67**. Rounding each month
    first would give 133.33 + 133.33 = 266.66, rounding the per-day figure 1 / 7.5 → 0.13 first
    would give 260.00. A single month of 10 h is 1333.33 (not 1330.00), see the literals above."""
    calendar = _calendar(db_session, hours="7.50", name="Cal rounding")
    project, scenario, *_ = _plan(
        db_session, unit="day", rate="1000.0000", hours=("1.00", "1.00"), months=(MAR, APR),
        calendar=calendar, suffix="rounding",
    )

    cost = _cost(client, project.id, scenario.id)

    assert cost["amount"] == "266.67"


def test_k_01_a_month_rate_is_rounded_once_at_the_end_never_per_month(
    client: TestClient, db_session: Session
) -> None:
    """K-01 (QA, SC-5-08) - the month-unit twin of the day-unit rounding test above. A month rate of
    1001 at 7.5 h/day over March and April 2026 (22 working days each, capacity 165 h) with 1
    planned hour per month: every month is 1001 / 165 = 6.0666..., the sum is 12.1333... and
    rounds to **12.13**. Rounding each month first gives 6.07 + 6.07 = **12.14**. Mutation:
    `round_money` applied to the month formula inside `priced_amount` survived every earlier test,
    because the only multi-month rounding figure was stated for a day rate."""
    calendar = _calendar(db_session, hours="7.50", name="Cal month rounding")
    project, scenario, *_ = _plan(
        db_session, unit="month", rate="1001.0000", hours=("1.00", "1.00"), months=(MAR, APR),
        calendar=calendar, suffix="month-rounding",
    )

    cost = _cost(client, project.id, scenario.id)

    assert cost["amount"] == "12.13"


# --- K-02: a day/month position needs its calendar, and only such a position does ----------------


def test_k_02_a_day_position_without_a_calendar_withholds_the_whole_cost_hourly_positions_too(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — one scenario, two positions: an hourly one (120 h at 100 =
    12000.00, in a location with no calendar — it never needs one) and a day-rate one in a location
    with no calendar. The state is `no_calendar`, the amount `n/a` and **not** 12000.00 (no partial
    sum, ADR-0013 point 2), and
    every fully loaded figure is withheld with it. Mutations: the missing calendar skipped or costed
    as 0 (amount would be 12000.00)."""
    project, scenario, *_ = _plan(
        db_session, unit="hour", rate="100.0000", hours=("120.00",), suffix="k02-hourly"
    )
    second = make_dimension_tuple(db_session, suffix=" k02 daily")
    position = make_staffing_position(db_session, scenario, second, headcount=2, start_date=MAR)
    make_allocation(db_session, position, period_month=MAR,
                    planned_allocation_hours=Decimal("10.00"))
    make_rate(db_session, second, effective_from=date(2026, 1, 1), currency="PLN",
              default_cost_rate=Decimal("800.0000"), cost_rate_unit="day")

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"], cost["currency"]) == ("no_calendar", "n/a", None)
    assert (cost["fully_loaded_amount"], cost["surcharge_amount"]) == ("n/a", "n/a")


def test_k_02_a_scenario_of_hourly_positions_only_needs_no_calendar(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the contrast — the hourly half of the scenario above, alone, in a location with no
    calendar: `calculated`, 12000.00. Mutation: a calendar required for hour rows."""
    project, scenario, *_ = _plan(
        db_session, unit="hour", rate="100.0000", hours=("120.00",), suffix="k02-hourly-only"
    )

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"]) == ("calculated", "12000.00")


@pytest.mark.parametrize("unit", ["day", "month"])
def test_k_02_a_zero_hour_position_needs_a_calendar_all_the_same(
    client: TestClient, db_session: Session, unit: str
) -> None:
    """K-02 / Q-E — zero planned hours at a day or month rate: without a calendar the state is
    `no_calendar`, **not** 0.00; with a calendar, 0.00 (`calculated`). The pair is the whole claim:
    a zero is only legal where the calculation could have been made."""
    project, scenario, *_ = _plan(
        db_session, unit=unit, rate="800.0000", hours=("0.00",), suffix=f"k02-zero-{unit}"
    )
    without = _cost(client, project.id, scenario.id)

    calendar = _calendar(db_session, hours="7.50", name=f"Cal zero {unit}")
    project2, scenario2, *_ = _plan(
        db_session, unit=unit, rate="800.0000", hours=("0.00",), calendar=calendar,
        suffix=f"k02-zero-cal-{unit}",
    )
    with_calendar = _cost(client, project2.id, scenario2.id)

    assert (without["state"], without["amount"]) == ("no_calendar", "n/a")
    assert (with_calendar["state"], with_calendar["amount"]) == ("calculated", "0.00")


def test_k_02_a_month_rate_in_a_month_with_no_working_days_is_a_named_state_a_day_rate_is_not(
    client: TestClient, db_session: Session
) -> None:
    """K-02 / Q-D — a calendar with no working day at all: a month-rate position is
    `no_working_days` (amount `n/a`, no division error, not 0.00); a **day**-rate position on the
    same calendar is priced from `standard_hours_per_day` alone: 10 / 7.5 × 1000 = 1333.33.
    Mutations: zero working days divides (a 500), yields 0.00, or the day rate consults the
    count."""
    calendar = _calendar(db_session, hours="7.50", name="Cal never", pattern=NEVER_WORKING)
    month_project, month_scenario, *_ = _plan(
        db_session, unit="month", rate="3300.0000", hours=("120.00",), calendar=calendar,
        suffix="k02-nwd-month",
    )
    day_project, day_scenario, *_ = _plan(
        db_session, unit="day", rate="1000.0000", hours=("10.00",), calendar=calendar,
        suffix="k02-nwd-day",
    )

    month_cost = _cost(client, month_project.id, month_scenario.id)
    day_cost = _cost(client, day_project.id, day_scenario.id)

    assert (month_cost["state"], month_cost["amount"], month_cost["currency"]) == (
        "no_working_days", "n/a", None,
    )
    assert (day_cost["state"], day_cost["amount"]) == ("calculated", "1333.33")


def test_k_02_the_order_of_named_states_is_no_cost_rate_then_currency_then_calendar(
    client: TestClient, db_session: Session
) -> None:
    """K-02 / Q-C — three scenarios, each with a day-rate position in a location with no calendar
    (which alone is `no_calendar`): (a) plus a currency mismatch (the scenario declares EUR, the
    rate is PLN) → `currency_mismatch`; (b) plus a second position with no rate at all →
    `no_cost_rate`;
    (c) alone → `no_calendar`. Mutation: the calendar check placed before the currency check."""
    project_a, scenario_a, *_ = _plan(
        db_session, unit="day", rate="800.0000", hours=("10.00",), suffix="k02-order-a"
    )
    scenario_a.currency = "EUR"
    db_session.flush()

    project_b, scenario_b, *_ = _plan(
        db_session, unit="day", rate="800.0000", hours=("10.00",), suffix="k02-order-b"
    )
    unrated = make_dimension_tuple(db_session, suffix=" k02 order b unrated")
    position = make_staffing_position(db_session, scenario_b, unrated, start_date=MAR)
    make_allocation(db_session, position, period_month=MAR)

    project_c, scenario_c, *_ = _plan(
        db_session, unit="day", rate="800.0000", hours=("10.00",), suffix="k02-order-c"
    )

    states = [
        _cost(client, project.id, scenario.id)["state"]
        for project, scenario in (
            (project_a, scenario_a), (project_b, scenario_b), (project_c, scenario_c)
        )
    ]

    assert states == ["currency_mismatch", "no_cost_rate", "no_calendar"]


def test_k_02_no_calendar_is_named_before_no_working_days_when_both_occur(
    client: TestClient, db_session: Session
) -> None:
    """K-02 / Q-C (QA, SC-5-08) - one scenario with a day-rate position in a location with no
    calendar (alone: `no_calendar`) and a month-rate position on a calendar with no working day
    (alone: `no_working_days`, see the test above). Both calendar states occur; the one a person
    fixes first, `no_calendar`, is named. Mutation: `_month_amounts` checking `no_working_days`
    first survived every earlier test, since no scenario had both states at once."""
    project, scenario, *_ = _plan(
        db_session, unit="day", rate="800.0000", hours=("10.00",), suffix="k02-both-nocal"
    )
    never = _calendar(db_session, hours="7.50", name="Cal never both", pattern=NEVER_WORKING)
    second = make_dimension_tuple(db_session, suffix=" k02 both nwd", calendar=never)
    position = make_staffing_position(db_session, scenario, second, headcount=2, start_date=MAR)
    make_allocation(db_session, position, period_month=MAR,
                    planned_allocation_hours=Decimal("10.00"))
    make_rate(db_session, second, effective_from=date(2026, 1, 1), currency="PLN",
              default_cost_rate=Decimal("3300.0000"), cost_rate_unit="month")

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"]) == ("no_calendar", "n/a")


# --- K-03: the unit is part of the cost predicate, frozen with the rate ---------------------------


def _split_march(session: Session, dimensions: DimensionTuple, *, first: str, second: str) -> None:
    """Two windows over March with the same amount and currency and the units given: the unit is
    the only thing that changes on the 16th."""
    make_rate(session, dimensions, effective_from=date(2026, 1, 1),
              effective_to=date(2026, 3, 15), currency="PLN",
              default_cost_rate=Decimal("3300.0000"), cost_rate_unit=first)
    make_rate(session, dimensions, effective_from=date(2026, 3, 16), currency="PLN",
              default_cost_rate=Decimal("3300.0000"), cost_rate_unit=second)


def _bare_plan(session: Session, *, suffix: str, calendar: WorkingCalendar | None = None):
    project = make_project(session, name=f"Aurora {suffix}", accessible_to=(IN_SCOPE_USER,),
                           cost_visible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name=f"Baseline {suffix}")
    dimensions = make_dimension_tuple(session, suffix=f" {suffix}", calendar=calendar)
    position = make_staffing_position(session, scenario, dimensions, headcount=2, start_date=MAR)
    make_allocation(session, position, period_month=MAR, planned_allocation_hours=Decimal("120.00"))
    return project, scenario, dimensions, position


def test_k_03_a_unit_change_inside_a_month_is_no_cost_rate_a_boundary_without_one_is_not(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — the same amount and currency on both sides of the 16th, the unit alone changing (month
    → day): `no_cost_rate`, naming March. The contrast — the same split with the unit unchanged
    (month → month) — resolves and costs 2400.00. Mutation: `cost_rate_unit` missing from
    `month_has_cost_
    rate` (the month would be costed at the first window's unit)."""
    calendar = _calendar(db_session, hours="7.50", name="Cal k03 split")
    project, scenario, dimensions, position = _bare_plan(
        db_session, suffix="k03-split", calendar=calendar
    )
    _split_march(db_session, dimensions, first="month", second="day")
    twin_project, twin, twin_dimensions, _ = _bare_plan(
        db_session, suffix="k03-split-same", calendar=calendar
    )
    _split_march(db_session, twin_dimensions, first="month", second="month")

    changed = _cost(client, project.id, scenario.id)
    same = _cost(client, twin_project.id, twin.id)

    assert (changed["state"], changed["amount"]) == ("no_cost_rate", "n/a")
    assert changed["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-03-01"}
    ]
    assert (same["state"], same["amount"]) == ("calculated", "2400.00")


def test_k_03_each_frozen_window_of_a_unit_split_month_keeps_its_own_unit(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — the copier on a unit-split month (month → day on the 16th, same amount): the windows
    are frozen (the selling predicate prices the month — the revenue reads them — so the copier
    keeps them), **each with its own unit**, and the approved scenario is `no_cost_rate` exactly as
    the draft was, because the reader re-asks the cost predicate of the frozen rows. Mutation: the
    unit dropped from the copier's column list (both rows would read `hour`, and the split would
    vanish — the approved cost would be a costed month)."""
    calendar = _calendar(db_session, hours="7.50", name="Cal k03 copier")
    project, scenario, dimensions, _ = _bare_plan(
        db_session, suffix="k03-copier", calendar=calendar
    )
    _split_march(db_session, dimensions, first="month", second="day")
    draft = _cost(client, project.id, scenario.id)

    _approve(client, project.id, scenario.id)

    frozen = sorted(_frozen_rates(db_session, scenario.id), key=lambda row: row.effective_from)
    assert [row.cost_rate_unit for row in frozen] == ["month", "day"]
    approved = _cost(client, project.id, scenario.id)
    assert (draft["state"], draft["amount"]) == ("no_cost_rate", "n/a")
    assert (approved["state"], approved["amount"]) == ("no_cost_rate", "n/a")


def test_k_03_a_month_rate_is_frozen_as_month_and_a_later_edit_of_the_unit_moves_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / SU-1 — a month-rate window (3300, 7.5 h/day) approved: the snapshot row carries
    `month` (**killed separately**: unit dropped from the copier's column list leaves the column's
    `DEFAULT 'hour'` there), the approved cost equals the draft's before the approval (2400.00), and
    after the catalogue's unit is edited to `hour` (and the amount to 999) the approved cost still
    reads 2400.00 while a draft twin of the same plan moves (**killed separately**: the reader
    taking the live unit, or hard-coding `hour`)."""
    calendar = _calendar(db_session, hours="7.50", name="Cal k03 freeze")
    project, scenario, dimensions, _, window = _plan(
        db_session, unit="month", rate="3300.0000", hours=("120.00",), calendar=calendar,
        suffix="k03-freeze",
    )
    _, twin, twin_dimensions, _, twin_window = _plan(
        db_session, unit="month", rate="3300.0000", hours=("120.00",), calendar=calendar,
        suffix="k03-freeze-twin", project=project,
    )
    before = _cost(client, project.id, scenario.id)
    assert before["amount"] == "2400.00"

    _approve(client, project.id, scenario.id)

    frozen = _frozen_rates(db_session, scenario.id)
    assert [(row.cost_rate_unit, row.default_cost_rate) for row in frozen] == [
        ("month", Decimal("3300.0000"))
    ]
    assert _cost(client, project.id, scenario.id)["amount"] == "2400.00"
    twin_before = _cost(client, project.id, twin.id)["amount"]

    for rate_id in (window.id, twin_window.id):
        _update(db_session, CatalogDefaultRate, rate_id, cost_rate_unit="hour",
                default_cost_rate=Decimal("999.0000"))

    assert _cost(client, project.id, scenario.id)["amount"] == "2400.00", (
        "an edit of the catalogue's unit moved an approved cost (AC-04)"
    )
    assert _frozen_rates(db_session, scenario.id)[0].cost_rate_unit == "month"
    assert _cost(client, project.id, twin.id)["amount"] != twin_before, (
        "the contrast failed: the catalogue edit did not reach the draft twin"
    )


def _frozen_window(scenario_id: uuid.UUID, dimensions: DimensionTuple, **values: Any):
    """One frozen rate window written directly — for the reader tests that must not depend on the
    copier."""
    defaults: dict[str, Any] = dict(
        id=uuid.uuid4(), scenario_id=scenario_id, source_rate_id=uuid.uuid4(),
        source_role_id=dimensions.role_id, source_seniority_id=dimensions.seniority_id,
        source_location_id=dimensions.location_id,
        source_engagement_type_id=dimensions.engagement_type_id, source_vendor_id=None,
        default_cost_rate=Decimal("3300.0000"), default_selling_rate=Decimal("200.0000"),
        currency="PLN", unit="hour", effective_from=date(2026, 1, 1), effective_to=None,
        surcharge_percent=Decimal("0"), includes_surcharge=False, cost_rate_unit="month",
    )
    return ApprovedSnapshotCatalogDefaultRate(**(defaults | values))


def _approved_by_hand(session: Session, *, suffix: str, units: tuple[str, str] | None):
    """An approved scenario with one March allocation, a frozen calendar (7.5 h/day, Mon–Fri) but
    **no live calendar** for its location, and frozen windows written directly: one open-ended
    window in
    `units[0]` when `units[1]` is the same, otherwise two windows split on the 16th."""
    project = make_project(session, name=f"Frozen {suffix}", accessible_to=(IN_SCOPE_USER,),
                           cost_visible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name=f"Approved {suffix}",
                             status=ScenarioStatus.APPROVED)
    dimensions = make_dimension_tuple(session, suffix=f" {suffix}")
    position = make_staffing_position(session, scenario, dimensions, headcount=2, start_date=MAR)
    make_allocation(session, position, period_month=MAR, planned_allocation_hours=Decimal("120.00"))
    session.add(ApprovedSnapshotWorkingCalendar(
        id=uuid.uuid4(), scenario_id=scenario.id, source_calendar_id=uuid.uuid4(),
        source_location_id=dimensions.location_id, name="Frozen",
        standard_hours_per_day=Decimal("7.50"), week_pattern=MONDAY_TO_FRIDAY,
    ))
    first, second = units
    if first == second:
        session.add(_frozen_window(scenario.id, dimensions, cost_rate_unit=first))
    else:
        session.add(_frozen_window(scenario.id, dimensions, cost_rate_unit=first,
                                   effective_to=date(2026, 3, 15)))
        session.add(_frozen_window(scenario.id, dimensions, cost_rate_unit=second,
                                   effective_from=date(2026, 3, 16)))
    session.flush()
    return project, scenario


def test_k_03_the_snapshot_reader_asks_the_unit_predicate_of_the_frozen_rows(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / ADR-0004 addendum point 4 — frozen rows written directly, so the copier is out of the
    picture: two frozen windows over March, same amount and currency, units month and day →
    `no_cost_rate` for the approved scenario; the same two windows both `month` → 2400.00, priced
    from the **frozen** calendar (the location has no live one). Mutation: the unit missing from the
    predicate as the *reader* applies it to the snapshot branch."""
    split_project, split = _approved_by_hand(db_session, suffix="split", units=("month", "day"))
    same_project, same = _approved_by_hand(db_session, suffix="same", units=("month", "month"))

    split_cost = _cost(client, split_project.id, split.id)
    same_cost = _cost(client, same_project.id, same.id)

    assert (split_cost["state"], split_cost["amount"]) == ("no_cost_rate", "n/a")
    assert (same_cost["state"], same_cost["amount"]) == ("calculated", "2400.00")


def test_k_03_an_approved_cost_is_read_from_the_frozen_calendar_never_a_live_one(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / SU-4 — a month-rate scenario approved at 7.5 h/day and 22 working days: 2400.00. Then
    the source calendar's standard day is edited (6.5), a holiday is added, and the location's
    `calendar_id` is cleared: the approved cost is still 2400.00, while a draft twin of the same
    plan moves. Mutation: the base cost reading `basis_by_location` for an approved scenario."""
    calendar = _calendar(db_session, hours="7.50", name="Cal k03 frozen basis")
    project, scenario, dimensions, _, _ = _plan(
        db_session, unit="month", rate="3300.0000", hours=("120.00",), calendar=calendar,
        suffix="k03-basis",
    )
    _, twin, _, _, _ = _plan(
        db_session, unit="month", rate="3300.0000", hours=("120.00",), calendar=calendar,
        suffix="k03-basis-twin", project=project,
    )
    _approve(client, project.id, scenario.id)
    assert _cost(client, project.id, scenario.id)["amount"] == "2400.00"
    twin_before = _cost(client, project.id, twin.id)["amount"]

    _update(db_session, WorkingCalendar, calendar.id, standard_hours_per_day=Decimal("6.50"))
    make_calendar_day(db_session, calendar, day=date(2026, 3, 11),
                      kind=WorkingCalendarDayKind.NON_WORKING)
    _update(db_session, CatalogLocation, dimensions.location_id, calendar_id=None)

    assert _cost(client, project.id, scenario.id)["amount"] == "2400.00", (
        "a live calendar edit moved an approved cost"
    )
    twin_after = _cost(client, project.id, twin.id)
    assert twin_after["amount"] != twin_before or twin_after["state"] != "calculated"


def test_k_03_a_location_without_a_calendar_at_approval_stays_no_calendar_for_ever(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / ADR-0004 addendum point 5a (named, not repaired) — approved with no calendar on the
    location: `no_calendar`; a calendar assigned afterwards does not change the approved answer (the
    snapshot has no `UPDATE` path and no live lookup fills a missing key), while a draft twin picks
    the new calendar up."""
    project, scenario, dimensions, _, _ = _plan(
        db_session, unit="month", rate="3300.0000", hours=("120.00",), suffix="k03-late-cal"
    )
    _, twin, twin_dimensions, _, _ = _plan(
        db_session, unit="month", rate="3300.0000", hours=("120.00",), suffix="k03-late-twin",
        project=project,
    )
    _approve(client, project.id, scenario.id)
    assert _cost(client, project.id, scenario.id)["state"] == "no_calendar"

    late = _calendar(db_session, hours="7.50", name="Cal late")
    _update(db_session, CatalogLocation, dimensions.location_id, calendar_id=late.id)
    _update(db_session, CatalogLocation, twin_dimensions.location_id, calendar_id=late.id)

    assert _cost(client, project.id, scenario.id)["state"] == "no_calendar"
    assert _cost(client, project.id, twin.id)["amount"] == "2400.00"


# --- K-04: fully loaded, paid absence and what-if all apply the unit ------------------------------


def _paid_fixture(db_session: Session, *, unit: str, rate: str, surcharge: str = "0.000"):
    """The standard March paid-absence fixture with its rate restated in `unit`."""
    catalog = _catalog(db_session, suffix=f" {unit}")
    project, scenario, position = _scenario(db_session, catalog, name=f"Unit {unit}")
    _update(db_session, CatalogDefaultRate, catalog.rate.id, cost_rate_unit=unit,
            default_cost_rate=Decimal(rate), surcharge_percent=Decimal(surcharge))
    return project, scenario


@pytest.mark.parametrize(
    ("unit", "rate", "base", "paid", "paid_budget"),
    [
        pytest.param("hour", "120.0000", "14400.00", "11700.00", "3600.00", id="hour"),
        pytest.param("day", "120.0000", "1920.00", "1560.00", "480.00", id="day"),
        pytest.param("month", "3300.0000", "2400.00", "1950.00", "600.00", id="month"),
    ],
)
def test_k_04_the_paid_absence_component_prices_its_hours_by_the_unit(
    client: TestClient, db_session: Session, unit: str, rate: str, base: str, paid: str,
    paid_budget: str,
) -> None:
    """K-04 — the standard fixture (97.50 paid hours, 30.00 from the budget, 7.5 h/day, 22 working
    days). Day: 97.5 / 7.5 × 120 = 1560.00, budget 30 / 7.5 × 120 = 480.00. Month: 3300 × 97.5 / 165
    = 1950.00, budget 3300 × 30 / 165 = 600.00. Mutation: `hour` hard-coded in the paid-absence path
    alone (the base cost stays right, so the base figure is asserted beside it)."""
    project, scenario = _paid_fixture(db_session, unit=unit, rate=rate)

    cost = _cost(client, project.id, scenario.id)

    assert (cost["amount"], cost["paid_absence_amount"], cost["paid_absence_budget_amount"]) == (
        base, paid, paid_budget
    )


def test_k_04_the_fully_loaded_costs_apply_the_surcharge_after_the_unit_conversion(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — month rate 3300 with a 10% surcharge: base 2400.00, fully loaded 2640.00 (surcharge
    240.00); paid absence 1950.00, fully loaded 2145.00 (surcharge 195.00). Mutation: the surcharge
    applied to the raw rate (3300 × 1.1 × 120 hours = 435600.00), or an hourly base inside the
    fully loaded pass."""
    project, scenario = _paid_fixture(
        db_session, unit="month", rate="3300.0000", surcharge="10.000"
    )

    cost = _cost(client, project.id, scenario.id)

    assert (cost["amount"], cost["fully_loaded_amount"], cost["surcharge_amount"]) == (
        "2400.00", "2640.00", "240.00",
    )
    assert (
        cost["paid_absence_amount"], cost["paid_absence_fully_loaded_amount"],
        cost["paid_absence_surcharge_amount"],
    ) == ("1950.00", "2145.00", "195.00")


def test_k_04_the_what_if_raise_scales_a_monthly_rate_and_leaves_its_unit_alone(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — a 10% raise on the month-rate fixture: base 2400.00 → 2640.00, paid absence 1950.00 →
    2145.00 (a raise is a percentage and scales the amount whatever the unit), and the raise writes
    nothing: the catalogue row is still `month` at 3300 afterwards. Mutation: `hour` hard-coded in
    the what-if path alone (the raised month would be priced 3630 × 120 = 435600.00)."""
    project, scenario = _paid_fixture(db_session, unit="month", rate="3300.0000")

    with caller_holding(*Permission):
        response = client.get(what_if_path(project.id, scenario.id, "10"))
    assert response.status_code == 200, response.text
    hypothetical = response.json()["personnel_cost"]

    assert (hypothetical["amount"], hypothetical["paid_absence_amount"]) == ("2640.00", "2145.00")
    real = _cost(client, project.id, scenario.id)
    assert (real["amount"], real["paid_absence_amount"]) == ("2400.00", "1950.00")
    row = db_session.execute(sa.select(CatalogDefaultRate)).scalars().all()
    assert {(r.cost_rate_unit, r.default_cost_rate) for r in row} == {
        ("month", Decimal("3300.0000"))
    }


# --- domain-level: the two paths a fixture cannot reach cheaply -----------------------------------


def test_k_04_a_paid_absence_month_rate_over_zero_working_days_is_no_working_days() -> None:
    """K-02/K-04 (domain) — the paid-absence component's own `no_working_days`: a month-unit rate
    over a calendar with no working day, with resolved hours, is a named state — never a division —
    and it comes after `currency_mismatch` (a PLN rate in a EUR scenario reports the mismatch)."""
    from app.domain.absence_budget import RESOLVED, BudgetShare
    from app.domain.capacity import CalendarBasis
    from app.domain.paid_absence_cost import PaidAbsenceMonth, paid_absence_cost
    from app.domain.personnel_cost import MonthCostRate

    basis = CalendarBasis(
        calendar_id=uuid.uuid4(), name="Never", standard_hours_per_day=Decimal("7.50"),
        week_pattern=NEVER_WORKING, exceptional_days={},
    )
    rate = MonthCostRate(
        cost_rate=Decimal("3300"), currency="PLN", windows=(), surcharge_percent=Decimal("0"),
        includes_surcharge=False, cost_rate_unit="month",
    )
    month = PaidAbsenceMonth(
        position_id=uuid.uuid4(), period_month=MAR, basis=basis, absences=(),
        budget=BudgetShare(state=RESOLVED, hours=Decimal("0.00")),
        statutory_generates_cost=False, rate=rate,
    )

    assert paid_absence_cost([month], scenario_currency="PLN").reason == "no_working_days"
    assert paid_absence_cost([month], scenario_currency="EUR").reason == "currency_mismatch"


# --- K-05: revenue and the selling-rate unit are untouched ----------------------------------------


def test_k_05_a_month_cost_rate_leaves_the_t_and_m_revenue_where_an_hour_cost_rate_had_it(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — two scenarios on the same plan (100 billable hours, selling rate 200 per hour), one
    with an hourly cost rate, one with a monthly one: T&M revenue is 20000.00 in both, and their
    revenue answers are identical. Mutation: the cost unit leaking into the revenue read."""
    calendar = _calendar(db_session, hours="7.50", name="Cal k05")
    hourly = _plan(db_session, unit="hour", rate="120.0000", hours=("120.00",),
                   calendar=calendar, suffix="k05-hour")
    monthly = _plan(db_session, unit="month", rate="3300.0000", hours=("120.00",),
                    calendar=calendar, suffix="k05-month")
    answers = []
    for project, scenario, *_ in (hourly, monthly):
        _set_rule(client, project.id, scenario.id)
        answers.append(_revenue(client, project.id, scenario.id))

    assert [answer["amount"] for answer in answers] == ["20000.00", "20000.00"]
    assert answers[0]["state"] == answers[1]["state"] == "calculated"
    assert answers[0]["currency"] == answers[1]["currency"]
    # The cost differs, so the equality above is not two identical scenarios:
    assert _cost(client, hourly[0].id, hourly[1].id)["amount"] != _cost(
        client, monthly[0].id, monthly[1].id
    )["amount"]


REVENUE_MODULES = (
    "app/data/commercial_terms.py",
    "app/domain/revenue.py",
    "app/domain/revenue_time_and_material.py",
    "app/domain/revenue_story_points.py",
    "app/domain/revenue_fixed_price.py",
    "app/domain/revenue_outcome_based.py",
)
COST_MODULES = (
    "app/data/personnel_cost.py",
    "app/domain/personnel_cost.py",
    "app/data/paid_absence_cost.py",
    "app/domain/paid_absence_cost.py",
)


def _module_name(relative_path: str) -> str:
    return relative_path.removesuffix(".py").replace("/", ".")


def _imports_of(relative_path: str) -> set[str]:
    tree = ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_k_05_no_revenue_module_names_the_unit_and_no_cost_module_imports_revenue() -> None:
    """K-05 / U-7 (structural, mirror of C-5) — the revenue modules never spell `cost_rate_unit`
    (not as a name, an attribute or a string), and the cost modules import none of them. The
    contrast keeps the test from passing by reading empty files: the cost formula does spell the
    column."""
    for path in REVENUE_MODULES:
        source = Path(BACKEND_ROOT, path).read_text(encoding="utf-8")
        assert "cost_rate_unit" not in source, f"{path} references cost_rate_unit"
    revenue_names = {_module_name(path) for path in REVENUE_MODULES}
    for path in COST_MODULES:
        assert not (_imports_of(path) & revenue_names), f"{path} imports a revenue module"
    assert "cost_rate_unit" in Path(BACKEND_ROOT, "app/domain/personnel_cost.py").read_text(
        encoding="utf-8"
    )
    assert "cost_rate_unit" in Path(BACKEND_ROOT, "app/data/personnel_cost.py").read_text(
        encoding="utf-8"
    )


def test_k_05_the_selling_rate_unit_stays_pinned_to_hour_in_the_database(
    db_session: Session,
) -> None:
    """K-05 — `unit` (the selling rate's) is untouched: a row with `unit = 'day'` is still refused
    by `unit_is_hour`, whatever its `cost_rate_unit` (contrast: `cost_rate_unit = 'day'` is accepted
    on an hourly-selling row)."""
    dimensions = make_dimension_tuple(db_session, suffix=" k05 unit")
    make_rate(db_session, dimensions, effective_from=date(2026, 1, 1), cost_rate_unit="day")
    other = make_dimension_tuple(db_session, suffix=" k05 unit 2")
    with pytest.raises(sa.exc.IntegrityError, match="unit_is_hour"):
        make_rate(db_session, other, effective_from=date(2026, 1, 1), unit="day",
                  cost_rate_unit="day")


# --- R-05: the calendar states in a mixed scenario ----------------------------------------------


def _domain_month(unit: str, *, calendar: str | None, rate: bool = True, hours: str = "10.00"):
    from app.domain.capacity import CalendarBasis
    from app.domain.personnel_cost import MonthCostRate, WorkedMonth

    basis = None if calendar is None else CalendarBasis(
        calendar_id=uuid.uuid4(), name="c", standard_hours_per_day=Decimal("7.50"),
        week_pattern=calendar, exceptional_days={},
    )
    month_rate = MonthCostRate(
        cost_rate=Decimal("3300"), currency="PLN", windows=(), surcharge_percent=Decimal("0"),
        includes_surcharge=False, cost_rate_unit=unit,
    ) if rate else None
    return WorkedMonth(
        position_id=uuid.uuid4(), period_month=MAR, planned_allocation_hours=Decimal(hours),
        rate=month_rate, basis=basis,
    )


def test_r_05_a_mixed_scenario_reports_the_named_states_in_the_decided_order(
) -> None:
    """R-05 (base cost) — one scenario, several failing positions: a position with no calendar and a
    month-unit position over zero working days → `no_calendar` (never `no_working_days`); the
    zero-working-days position alone → `no_working_days`; adding a position with no rate →
    `no_cost_rate`; a EUR scenario over the PLN rates → `currency_mismatch` before both calendar
    states. The ordering the ADR (Q-C, U-11) records, in one place."""
    from app.domain.personnel_cost import base_personnel_cost

    no_calendar = _domain_month("day", calendar=None)
    no_days = _domain_month("month", calendar=NEVER_WORKING)
    unrated = _domain_month("day", calendar=None, rate=False)

    def state(months, currency="PLN"):
        return base_personnel_cost(
            months, rate_source="live_catalog", scenario_currency=currency
        ).reason

    assert state([no_days, no_calendar]) == "no_calendar"
    assert state([no_days]) == "no_working_days"
    assert state([no_days, no_calendar, unrated]) == "no_cost_rate"
    assert state([no_days, no_calendar], currency="EUR") == "currency_mismatch"


def test_r_05_the_paid_absence_component_orders_its_states_the_same_way() -> None:
    """R-05 (paid-absence component) — a month with no calendar and a month-unit month over zero
    working days → `no_calendar`; the second alone → `no_working_days`; adding an unrated month →
    `no_cost_rate`; a EUR scenario → `currency_mismatch` before `no_working_days`."""
    from app.domain.absence_budget import RESOLVED, BudgetShare
    from app.domain.capacity import CalendarBasis
    from app.domain.paid_absence_cost import PaidAbsenceMonth, paid_absence_cost
    from app.domain.personnel_cost import MonthCostRate

    rate = MonthCostRate(
        cost_rate=Decimal("3300"), currency="PLN", windows=(), surcharge_percent=Decimal("0"),
        includes_surcharge=False, cost_rate_unit="month",
    )
    basis = CalendarBasis(
        calendar_id=uuid.uuid4(), name="Never", standard_hours_per_day=Decimal("7.50"),
        week_pattern=NEVER_WORKING, exceptional_days={},
    )
    budget = BudgetShare(state=RESOLVED, hours=Decimal("0.00"))

    def month(*, with_basis: bool, with_rate: bool) -> PaidAbsenceMonth:
        return PaidAbsenceMonth(
            position_id=uuid.uuid4(), period_month=MAR, basis=basis if with_basis else None,
            absences=(), budget=budget, statutory_generates_cost=False,
            rate=rate if with_rate else None,
        )

    no_days = month(with_basis=True, with_rate=True)
    no_calendar = month(with_basis=False, with_rate=True)
    unrated = month(with_basis=True, with_rate=False)

    def state(months, currency="PLN"):
        return paid_absence_cost(months, scenario_currency=currency).reason

    assert state([no_days, no_calendar]) == "no_calendar"
    assert state([no_days]) == "no_working_days"
    assert state([no_days, unrated]) == "no_cost_rate"
    assert state([no_days], currency="EUR") == "currency_mismatch"


# --- Security R-01: the new states are shown, the figures are not -------------------------------


@pytest.mark.parametrize(
    ("unit", "pattern", "state"),
    [("month", None, "no_calendar"), ("month", NEVER_WORKING, "no_working_days")],
)
@pytest.mark.parametrize("blind", ["without_permission", "without_project_flag"])
def test_security_r_01_a_blind_caller_sees_the_calendar_state_and_no_figure(
    client: TestClient, db_session: Session, unit: str, pattern: str | None, state: str,
    blind: str,
) -> None:
    """Security R-01 (ADR-0005 addendum 2026-09-29, point 7, Q-A) — for a caller lacking
    `PERSONNEL_COSTS_READ`, and separately for one holding it but without the project's cost flag,
    `no_calendar` and `no_working_days` are reported as the `state` (accepted, named) while
    `amount`,
    `fully_loaded_amount`, `surcharge_amount` and `assumptions_used` are `null`. Contrast: the
    caller
    with the conjunction reads `n/a` for the amounts and a populated `assumptions_used`."""
    calendar = None if pattern is None else _calendar(
        db_session, hours="7.50", name=f"Cal blind {state} {blind}", pattern=pattern
    )
    flagged = blind == "without_permission"
    project = make_project(
        db_session, name=f"Blind {state} {blind}", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,) if flagged else (),
    )
    scenario = _plan(
        db_session, unit=unit, rate="3300.0000", hours=("120.00",), calendar=calendar,
        suffix=f"blind-{state}-{blind}", project=project,
    )[1]
    permissions = set(Permission) - ({Permission.PERSONNEL_COSTS_READ} if flagged else set())

    with caller_holding(*permissions):
        response = client.get(personnel_cost_path(project.id, scenario.id))
    assert response.status_code == 200, response.text
    cost = response.json()["personnel_cost"]

    assert cost["state"] == state
    for field in ("amount", "fully_loaded_amount", "surcharge_amount", "assumptions_used"):
        assert cost[field] is None, f"{field} reached a blind caller"

    if flagged:
        open_cost = _cost(client, project.id, scenario.id)
        assert (open_cost["state"], open_cost["amount"]) == (state, "n/a")
        assert open_cost["assumptions_used"] is not None
