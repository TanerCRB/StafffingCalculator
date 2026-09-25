"""SC-5-06, K-01..K-07 — the cost of paid absences as a named component beside the base personnel
cost (F-07, F-05; ADR-0013 aneks 2026-09-23 SC-5-06, controls N-1..N-4; ADR-0004 aneks 2026-09-23
SC-5-06, controls M-1, M-2).

Every figure is read through the real endpoint, `GET …/personnel-cost`, by a caller for whom the
cost gate is **open** (`caller_holding(*Permission)` plus the `project_access` flag), except in the
K-05 tests, whose subject is the gate.

**The standard March fixture, and why its numbers are what they are.** Calendar 7.50 h/day,
Monday–Friday (March 2026: 22 working days); one position of **headcount 2**, 120 planned hours;
cost rate 120 PLN, selling rate 200 PLN. Three absence types, all three flags set independently:

| type                   | cost  | revenue | statutory | booked      | working days |
|------------------------|-------|---------|-----------|-------------|--------------|
| Paid holiday           | true  | false   | no        | 9–15 March  | 5 (7 cal.)   |
| Training               | false | true    | no        | 16–20 March | 5            |
| Statutory annual leave | true  | false   | yes       | 2–5 March   | 4            |

(`cost` = `generates_cost`, `revenue` = `generates_revenue`.)

Budget: 26 days per FTE over 2026, so 52 for the position; the top-up is 52 − 4 booked = 48 days
= 360.00 h over twelve months = **30.00 h in March**. The component: manual 5 + 4 days = 67.50 h,
budget 30.00 h, 97.50 h × 120 = **11700.00**, of which the budget part is **3600.00**. The base cost
stays **14400.00** (120 × 120).

Each K-01 mutation lands on a different wrong number: every type counted (training too) 16200.00;
the filter on `generates_revenue` instead of `generates_cost` 8100.00; `× headcount` on the manual
part 19800.00; the selling rate 19500.00; calendar days instead of working days 13500.00; the
component mixed into the base amount — a base different from 14400.00.
"""

import ast
import uuid
from dataclasses import dataclass
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
    AbsenceBudget,
    AbsenceType,
    CatalogDefaultRate,
    CatalogLocation,
    Project,
    Scenario,
    StaffingPosition,
    WorkingCalendar,
    WorkingCalendarDayKind,
)
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    STATUTORY_LEAVE_TYPE_NAME,
    DimensionTuple,
    approve_path,
    as_caller,
    caller_holding,
    commercial_terms_path,
    grant_personnel_cost_visibility,
    make_absence,
    make_absence_budget,
    make_absence_type,
    make_allocation,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
    staffing_path,
)

FEB = date(2026, 2, 1)
MAR = date(2026, 3, 1)
YEAR_2026 = tuple(date(2026, month, 1) for month in range(1, 13))

COST = Decimal("120.0000")
SELLING = Decimal("200.0000")
EVERYTHING = frozenset(Permission)
WITHOUT_COST_PERMISSION = EVERYTHING - {Permission.PERSONNEL_COSTS_READ}

PAID_ABSENCE_GATED = ("paid_absence_amount", "paid_absence_budget_amount",
                      "paid_absence_assumptions_used")


def personnel_cost_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/personnel-cost"


# --- fixtures ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Catalog:
    """The organisational half: a calendar, a tuple in a location using it, three absence types,
    a budget and a rate — shared by every scenario a test builds on it."""

    calendar: WorkingCalendar
    dimensions: DimensionTuple
    holiday: AbsenceType
    training: AbsenceType
    statutory: AbsenceType
    budget: AbsenceBudget | None
    rate: CatalogDefaultRate


def _catalog(
    session: Session,
    *,
    suffix: str = "",
    statutory_generates_cost: bool = True,
    flag_statutory: bool = True,
    budget_days: Decimal | None = Decimal("26.00"),
    with_calendar: bool = True,
) -> Catalog:
    calendar = make_working_calendar(session, name=f"Poland 2026{suffix}")
    dimensions = make_dimension_tuple(
        session, suffix=f" paid{suffix}", calendar=calendar if with_calendar else None
    )
    holiday = make_absence_type(
        session, name=f"Paid holiday{suffix}", generates_cost=True, generates_revenue=False
    )
    training = make_absence_type(
        session, name=f"Training{suffix}", generates_cost=False, generates_revenue=True
    )
    statutory = make_absence_type(
        session,
        name=f"{STATUTORY_LEAVE_TYPE_NAME}{suffix}",
        generates_cost=statutory_generates_cost,
        generates_revenue=False,
        is_statutory_leave=flag_statutory,
    )
    budget = None
    if budget_days is not None:
        budget = make_absence_budget(
            session, calendar, dimensions.engagement_type_id, budget_days=budget_days,
            effective_from=date(2026, 1, 1), effective_to=date(2026, 12, 31),
        )
    rate = make_rate(
        session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
    )
    return Catalog(calendar, dimensions, holiday, training, statutory, budget, rate)


def _scenario(
    session: Session,
    catalog: Catalog,
    *,
    name: str = "Aurora",
    project: Project | None = None,
    headcount: int = 2,
    months: tuple[date, ...] = (MAR,),
    standard_absences: bool = True,
) -> tuple[Project, Scenario, StaffingPosition]:
    """A draft scenario with one position on the catalogue's tuple; with `standard_absences`, the
    three bookings of the module docstring. The caller is in scope with the cost flag set."""
    if project is None:
        project = make_project(
            session, name=name, accessible_to=(IN_SCOPE_USER,), cost_visible_to=(IN_SCOPE_USER,)
        )
    scenario = make_scenario(session, project, name=f"{name} scenario")
    position = make_staffing_position(
        session, scenario, catalog.dimensions, headcount=headcount, start_date=months[0]
    )
    for month in months:
        make_allocation(session, position, period_month=month)
    if standard_absences:
        make_absence(session, position, catalog.holiday,
                     start_date=date(2026, 3, 9), end_date=date(2026, 3, 15))
        make_absence(session, position, catalog.training,
                     start_date=date(2026, 3, 16), end_date=date(2026, 3, 20))
        make_absence(session, position, catalog.statutory,
                     start_date=date(2026, 3, 2), end_date=date(2026, 3, 5))
    return project, scenario, position


def _cost(
    client: TestClient,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    permissions: frozenset[Permission] = EVERYTHING,
) -> dict[str, Any]:
    with caller_holding(*permissions):
        response = client.get(personnel_cost_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()["personnel_cost"]


def _paid(cost: dict[str, Any]) -> dict[str, Any]:
    """The component's five fields — what the approval tests compare."""
    return {key: value for key, value in cost.items() if key.startswith("paid_absence_")}


def _update(session: Session, model: type, row_id: uuid.UUID, **values: object) -> None:
    """A direct catalogue edit (no endpoint edits calendars or absence types), then every ORM object
    expired so the next request reads the row back rather than a cached copy."""
    session.execute(sa.update(model).where(model.id == row_id).values(**values))
    session.flush()
    session.expire_all()


def _approve(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> None:
    response = client.post(approve_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text


# --- K-01: generates_cost only, working days, no × headcount, the cost rate, base untouched ---


def test_k_01_the_component_is_manual_paid_hours_plus_the_budget_top_up_at_the_cost_rate(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — 11700.00, of which 3600.00 from the budget; the base cost 14400.00, unchanged.

    Six mutations, six different numbers (module docstring): every type counted (16200.00), the
    `generates_revenue` filter (8100.00), `× headcount` (19800.00), the selling rate (19500.00),
    calendar days (13500.00), and the component added into `amount` (≠ 14400.00). The per-month
    hours name both parts, so a right total reached by a wrong split fails too.
    """
    catalog = _catalog(db_session)
    project, scenario, position = _scenario(db_session, catalog)

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"], cost["currency"]) == ("calculated", "14400.00", "PLN")
    assert (
        cost["paid_absence_state"],
        cost["paid_absence_amount"],
        cost["paid_absence_budget_amount"],
        cost["paid_absence_currency"],
    ) == ("calculated", "11700.00", "3600.00", "PLN")
    assert cost["cost_basis"] == "base"
    assert cost["paid_absence_assumptions_used"] == {
        "hours_source": "paid_absences_and_leave_budget_top_up",
        "months": [
            {
                "position_id": str(position.id),
                "period_month": "2026-03-01",
                "manual_hours": "67.50",
                "budget_hours": "30.00",
                "budget_part": "applied",
            }
        ],
        "unresolved_months": [],
        "currencies": ["PLN"],
    }


def test_k_01_the_base_amount_is_the_same_with_and_without_paid_absences(
    client: TestClient, db_session: Session
) -> None:
    """K-01, "koszt bazowy SC-5-01 nietknięty" as a contrast rather than a constant: the same plan
    without any booking and with all three — `amount` and `assumptions_used` identical, the
    component moving from the budget alone (52 days × 7.50 = 390.00 h, March 32.50 h → 3900.00) to
    11700.00. Mutation: the component summed into the base amount."""
    catalog = _catalog(db_session)
    bare_project, bare, _ = _scenario(db_session, catalog, name="Bare", standard_absences=False)
    booked_project, booked, _ = _scenario(db_session, catalog, name="Booked")

    without = _cost(client, bare_project.id, bare.id)
    with_absences = _cost(client, booked_project.id, booked.id)

    assert without["paid_absence_amount"] == "3900.00"
    assert with_absences["paid_absence_amount"] == "11700.00"
    for field in ("state", "amount", "currency"):
        assert without[field] == with_absences[field], field
    assert without["amount"] == "14400.00"


def test_k_01_the_sum_is_rounded_once_at_the_end_never_per_month(
    client: TestClient, db_session: Session
) -> None:
    """K-01's rounding point — one booked holiday day in February and one in March, 7.50 h each, at
    100.0006 per hour; the statutory type named and **not** cost-generating, so no budget enters.

    Once at the end: 15.00 × 100.0006 = 1500.009 → **1500.01**. Per month (the mutation): 750.0045
    → 750.00 twice = 1500.00.
    """
    catalog = _catalog(db_session, statutory_generates_cost=False)
    _update(db_session, CatalogDefaultRate, catalog.rate.id,
            default_cost_rate=Decimal("100.0006"))
    project, scenario, position = _scenario(
        db_session, catalog, months=(FEB, MAR), standard_absences=False
    )
    make_absence(db_session, position, catalog.holiday,
                 start_date=date(2026, 2, 2), end_date=date(2026, 2, 2))
    make_absence(db_session, position, catalog.holiday,
                 start_date=date(2026, 3, 2), end_date=date(2026, 3, 2))

    cost = _cost(client, project.id, scenario.id)

    assert (cost["paid_absence_state"], cost["paid_absence_amount"]) == ("calculated", "1500.01")
    assert cost["paid_absence_budget_amount"] == "0.00"


def test_n_1_a_type_without_generates_cost_contributes_nothing_and_its_flag_is_what_decides(
    client: TestClient, db_session: Session
) -> None:
    """N-1 — one booking of Training (16–20 March, `generates_cost = false`, `generates_revenue =
    true`), the statutory type named and not cost-generating, so the budget part is a decided 0.

    1. `calculated`, **0.00** — a real zero, not a named state: nothing here costs.
    2. The same dates, the same row, the type's `generates_cost` flipped to true: 5 × 7.50 × 120 =
       **4500.00**. The contrast that the booking reached the component at all.
    3. `generates_revenue` then flipped to false: still 4500.00 — the revenue flag is not the
       filter.
    """
    catalog = _catalog(db_session, statutory_generates_cost=False)
    project, scenario, position = _scenario(db_session, catalog, standard_absences=False)
    make_absence(db_session, position, catalog.training,
                 start_date=date(2026, 3, 16), end_date=date(2026, 3, 20))

    not_costed = _cost(client, project.id, scenario.id)
    assert (not_costed["paid_absence_state"], not_costed["paid_absence_amount"]) == (
        "calculated", "0.00"
    )
    assert not_costed["paid_absence_assumptions_used"]["months"][0]["budget_part"] == (
        "statutory_leave_not_cost_generating"
    )

    _update(db_session, AbsenceType, catalog.training.id, generates_cost=True)
    assert _cost(client, project.id, scenario.id)["paid_absence_amount"] == "4500.00"

    _update(db_session, AbsenceType, catalog.training.id, generates_revenue=False)
    assert _cost(client, project.id, scenario.id)["paid_absence_amount"] == "4500.00"


# --- K-02: statutory leave in the cost = manual rows + the budget's top-up, never both whole ------


def test_k_02_statutory_leave_is_costed_as_max_of_budget_and_bookings_never_their_sum(
    client: TestClient, db_session: Session
) -> None:
    """K-02 / N-2 — a whole year planned (twelve allocation rows), headcount 1, cost rate 120, the
    budget 26 days, only statutory leave booked.

    1. **20 days booked in July** (1–28 July): 20 × 7.50 = 150.00 h manual, top-up 6 days = 45.00 h
       over the year — **195.00 h = 26 days**, × 120 = **23400.00**, of which the budget 5400.00.
       The mutation "the whole budget plus the bookings" gives 150 + 195 = 345 h = 41400.00 — the
       SC-3-03 R-02 "46 days instead of 26", on a cost.
    2. **30 days booked** (1 July – 11 August), more than the budget: 225.00 h, top-up **0** —
       27000.00, budget part 0.00. The year's statutory cost is `max(budget, bookings)`.

    The twelve months' budget hours are asserted to add up to the top-up exactly (45.00 and 0.00):
    the cost uses the capacity's share, remainder distribution included, not a re-derived one.
    """
    catalog = _catalog(db_session)
    rate_120 = COST

    results = {}
    for label, end in (("twenty", date(2026, 7, 28)), ("thirty", date(2026, 8, 11))):
        project, scenario, position = _scenario(
            db_session, catalog, name=f"Year {label}", headcount=1, months=YEAR_2026,
            standard_absences=False,
        )
        make_absence(db_session, position, catalog.statutory,
                     start_date=date(2026, 7, 1), end_date=end)
        cost = _cost(client, project.id, scenario.id)
        months = cost["paid_absence_assumptions_used"]["months"]
        results[label] = (
            cost["paid_absence_amount"],
            cost["paid_absence_budget_amount"],
            sum(Decimal(month["manual_hours"]) for month in months),
            sum(Decimal(month["budget_hours"]) for month in months),
        )

    assert results["twenty"] == ("23400.00", "5400.00", Decimal("150.00"), Decimal("45.00"))
    assert results["thirty"] == ("27000.00", "0.00", Decimal("225.00"), Decimal("0.00"))
    assert Decimal(results["twenty"][0]) == (Decimal("150.00") + Decimal("45.00")) * rate_120


# --- K-03: the budget follows the statutory type's generates_cost; "not applied" is named ---------


def test_k_03_the_budget_part_follows_the_statutory_types_generates_cost(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — the standard March fixture, the statutory type's `generates_cost` flipped.

    - true: 11700.00, budget 3600.00 (K-01);
    - false: the budget part is a decided **0.00** (`statutory_leave_not_cost_generating`) and the
      four booked statutory days no longer cost either — only the holiday: 5 × 7.50 × 120 =
      **4500.00**. Mutation: the budget applied whatever the flag says (8100.00).
    """
    catalog = _catalog(db_session)
    project, scenario, _ = _scenario(db_session, catalog)
    assert _cost(client, project.id, scenario.id)["paid_absence_budget_amount"] == "3600.00"

    _update(db_session, AbsenceType, catalog.statutory.id, generates_cost=False)
    cost = _cost(client, project.id, scenario.id)

    assert (cost["paid_absence_state"], cost["paid_absence_amount"],
            cost["paid_absence_budget_amount"]) == ("calculated", "4500.00", "0.00")
    month = cost["paid_absence_assumptions_used"]["months"][0]
    assert (month["manual_hours"], month["budget_hours"], month["budget_part"]) == (
        "37.50", "0.00", "statutory_leave_not_cost_generating"
    )


def test_k_03_a_budget_that_cannot_be_applied_is_a_named_state_never_a_silent_zero(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / N-3 — the two ways the budget is "not applied", each a named state of the component,
    each next to the base amount that stays 14400.00:

    1. **no type flagged statutory** (the state of a freshly migrated database): the budget exists
       and nobody can say whether its days cost anything → `no_statutory_leave_type`, `"n/a"`;
    2. **the statutory type costs, no budget covers the month** → `no_budget`, `"n/a"`.

    The contrast: the statutory type named and **not** cost-generating, no budget at all →
    `calculated` (4500.00, the holiday alone) — a missing budget is only a hole when the budget
    would have cost something. Mutations: the missing budget read as 0 hours (8100.00 in case 2),
    the unflagged budget applied anyway or skipped silently.
    """
    unflagged = _catalog(db_session, suffix=" A", flag_statutory=False)
    p1, s1, _ = _scenario(db_session, unflagged, name="Unflagged")
    no_budget = _catalog(db_session, suffix=" B", budget_days=None, flag_statutory=False)
    p2, s2, _ = _scenario(db_session, no_budget, name="No budget")
    decided = _catalog(
        db_session, suffix=" C", budget_days=None, statutory_generates_cost=False,
        flag_statutory=False,
    )
    p3, s3, _ = _scenario(db_session, decided, name="Decided")

    # One flagged type in the catalogue at a time (`uq_absence_type_statutory_leave`): each case is
    # read with its own catalogue's statutory type flagged, and only that one.
    def only_flagged(flagged: AbsenceType | None) -> None:
        for catalog in (unflagged, no_budget, decided):
            _update(db_session, AbsenceType, catalog.statutory.id, is_statutory_leave=False)
        if flagged is not None:
            _update(db_session, AbsenceType, flagged.id, is_statutory_leave=True)

    only_flagged(None)
    first = _cost(client, p1.id, s1.id)
    only_flagged(no_budget.statutory)
    second = _cost(client, p2.id, s2.id)
    only_flagged(decided.statutory)
    third = _cost(client, p3.id, s3.id)

    for cost, state in ((first, "no_statutory_leave_type"), (second, "no_budget")):
        assert (cost["paid_absence_state"], cost["paid_absence_amount"],
                cost["paid_absence_budget_amount"]) == (state, "n/a", "n/a")
        assert cost["paid_absence_amount"] not in {"0.00", "0", "4500.00"}
        assert cost["paid_absence_assumptions_used"]["unresolved_months"][0]["reason"] == state
        assert (cost["state"], cost["amount"]) == ("calculated", "14400.00")
    assert (third["paid_absence_state"], third["paid_absence_amount"]) == ("calculated", "4500.00")


# --- K-04: no calendar / no cost rate — a named state of the component, never 0, never partial ----


def test_k_04_no_calendar_is_a_named_state_of_the_component_and_never_a_partial_sum(
    client: TestClient, db_session: Session
) -> None:
    """K-04 / N-3 — two positions in one scenario: one in the standard fixture (11700.00 on its
    own), one in a location with **no calendar**. The component is `no_calendar` and `"n/a"` — not
    11700.00 (the partial sum of the position that did resolve) and not 0.00. The base amount is its
    own answer and is not touched: 2 × 14400.00 = 28800.00, `calculated`."""
    catalog = _catalog(db_session)
    project, scenario, _ = _scenario(db_session, catalog)
    no_calendar = _catalog(
        db_session, suffix=" nocal", with_calendar=False, flag_statutory=False
    )
    uncounted = make_staffing_position(db_session, scenario, no_calendar.dimensions, start_date=MAR)
    make_allocation(db_session, uncounted, period_month=MAR)

    cost = _cost(client, project.id, scenario.id)

    assert (cost["paid_absence_state"], cost["paid_absence_amount"]) == ("no_calendar", "n/a")
    assert cost["paid_absence_amount"] not in {"11700.00", "0.00"}
    assert cost["paid_absence_assumptions_used"]["unresolved_months"] == [
        {"position_id": str(uncounted.id), "period_month": "2026-03-01", "reason": "no_calendar"}
    ]
    assert (cost["state"], cost["amount"]) == ("calculated", "28800.00")


def test_k_04_a_month_without_a_cost_rate_withholds_the_whole_component(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — February and March planned; the rate re-cut so March has none. February's component
    would be its budget share alone (a partial sum); the answer is `no_cost_rate`, naming March.
    The contrast re-opens the window: `calculated`."""
    catalog = _catalog(db_session)
    project, scenario, position = _scenario(db_session, catalog, months=(FEB, MAR))
    _update(db_session, CatalogDefaultRate, catalog.rate.id, effective_to=date(2026, 2, 28))

    uncosted = _cost(client, project.id, scenario.id)
    assert (uncosted["paid_absence_state"], uncosted["paid_absence_amount"]) == (
        "no_cost_rate", "n/a"
    )
    assert uncosted["paid_absence_assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-03-01", "reason": "no_cost_rate"}
    ]

    _update(db_session, CatalogDefaultRate, catalog.rate.id, effective_to=None)
    assert _cost(client, project.id, scenario.id)["paid_absence_state"] == "calculated"


def test_k_04_an_empty_plan_is_zero_in_the_scenarios_currency_or_no_cost_currency(
    client: TestClient, db_session: Session
) -> None:
    """K-04 at the edge, the base cost's rule applied to the component so the two cannot disagree
    about an empty plan: a position with no month rows is `0.00 EUR` with a declared currency and
    `no_cost_currency` without one."""
    catalog = _catalog(db_session)
    results = {}
    for currency in ("EUR", None):
        project = make_project(
            db_session, name=f"Empty {currency}", accessible_to=(IN_SCOPE_USER,),
            cost_visible_to=(IN_SCOPE_USER,),
        )
        scenario = make_scenario(db_session, project, name="Empty", currency=currency)
        make_staffing_position(db_session, scenario, catalog.dimensions, start_date=MAR)
        cost = _cost(client, project.id, scenario.id)
        results[currency] = (
            cost["paid_absence_state"], cost["paid_absence_amount"], cost["paid_absence_currency"]
        )

    assert results == {
        "EUR": ("calculated", "0.00", "EUR"),
        None: ("no_cost_currency", "n/a", None),
    }


# --- K-05: the component under the conjunction; the budget in days/hours outside it -------------


def test_k_05_the_component_and_its_budget_part_are_present_only_under_the_conjunction(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — the four corners of `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs`, on the
    component's three gated fields; the state stays visible in every corner.

    Mutations: any of the three names dropped from `SCENARIO_COST_FIELDS` (its figure — 11700.00,
    3600.00, or the 30.00 budget hours of the assumptions — appears in rows 1–3); the component
    shaped outside `_without_scenario_personnel_costs`.
    """
    catalog = _catalog(db_session)
    project = make_project(db_session, name="Gated", accessible_to=(IN_SCOPE_USER,))
    project, scenario, _ = _scenario(db_session, catalog, project=project)

    def withheld(cost: dict[str, Any], text: str) -> None:
        assert cost["paid_absence_state"] == "calculated"
        assert cost["paid_absence_currency"] == "PLN"
        for field in PAID_ABSENCE_GATED:
            assert cost[field] is None, field
        for figure in ("11700.00", "3600.00", "67.50"):
            assert figure not in text, figure

    for permissions in (WITHOUT_COST_PERMISSION, EVERYTHING):
        with caller_holding(*permissions):
            response = client.get(personnel_cost_path(project.id, scenario.id))
        withheld(response.json()["personnel_cost"], response.text)

    grant_personnel_cost_visibility(db_session, project_id=project.id, user_id=IN_SCOPE_USER)
    with caller_holding(*WITHOUT_COST_PERMISSION):
        response = client.get(personnel_cost_path(project.id, scenario.id))
    withheld(response.json()["personnel_cost"], response.text)

    shown = _cost(client, project.id, scenario.id)
    assert (shown["paid_absence_amount"], shown["paid_absence_budget_amount"]) == (
        "11700.00", "3600.00"
    )
    assert shown["paid_absence_assumptions_used"]["months"][0]["budget_hours"] == "30.00"


def test_k_05_the_budget_in_days_and_hours_stays_outside_the_conjunction(
    client: TestClient, db_session: Session
) -> None:
    """K-05, the other half (ADR-0005 aneks SC-3-03 point 4, aneks SC-3-02 point 7): a caller
    **without** `PERSONNEL_COSTS_READ` and without the flag — the one refused the component above —
    still reads the budget as a number: `absence_budget_hours` 30.00 on the staffing grid and
    `budget_days` 26.00 in the catalogue. The contrast is the same caller's `null` component.
    Mutation: the new gate spread to the staffing or catalogue payloads."""
    catalog = _catalog(db_session)
    project = make_project(db_session, name="Days", accessible_to=(IN_SCOPE_USER,))
    project, scenario, _ = _scenario(db_session, catalog, project=project)

    with caller_holding(*WITHOUT_COST_PERMISSION):
        grid = client.get(staffing_path(project.id, scenario.id))
        budgets = client.get("/catalog/absence-budgets")
        cost = client.get(personnel_cost_path(project.id, scenario.id))

    assert grid.status_code == budgets.status_code == cost.status_code == 200
    month = grid.json()["positions"][0]["allocations"][0]
    assert (month["absence_budget_state"], month["absence_budget_hours"]) == ("resolved", "30.00")
    assert [
        row["budget_days"] for row in budgets.json()["budgets"]
        if row["id"] == str(catalog.budget.id)
    ] == ["26.00"]
    assert cost.json()["personnel_cost"]["paid_absence_budget_amount"] is None


# --- K-06 / M-1 / M-2: the approved component reads its own snapshot and nothing live ---------


def test_k_06_m_1_the_approved_component_equals_the_draft_and_no_catalogue_edit_moves_it(
    client: TestClient, db_session: Session
) -> None:
    """K-06 / M-1 — the standard fixture approved, then six catalogue edits, one at a time.

    Before and after the approval the component is identical, field for field (it carries no
    source label, so nothing *has* to differ). After each edit the approved component is still that
    answer, while a draft twin of the same plan — the contrast that the edit reached the catalogue —
    moves every time:

    1. the calendar's standard day 7.50 → 6.50 (the frozen `approved_snapshot_working_calendar`);
    2. 11 March declared a holiday (`approved_snapshot_working_calendar_day`);
    3. the budget 26 → 38 days (`approved_snapshot_absence_budget`);
    4. the holiday type's `generates_cost` → false (`approved_snapshot_absence_type`);
    5. `is_statutory_leave` moved from the statutory type to Training (the frozen flag);
    6. the cost rate 120 → 999 (the SC-5-01 rate snapshot — the component's rate is the base's).

    Mutation per edit: the reader of that one input left on the live table.
    """
    catalog = _catalog(db_session)
    project, scenario, _ = _scenario(db_session, catalog)
    _, twin, _ = _scenario(db_session, catalog, name="Twin", project=project)

    before = _cost(client, project.id, scenario.id)
    assert before["paid_absence_amount"] == "11700.00"
    _approve(client, project.id, scenario.id)
    approved = _paid(_cost(client, project.id, scenario.id))
    assert approved == _paid(before)

    edits = [
        lambda: _update(db_session, WorkingCalendar, catalog.calendar.id,
                        standard_hours_per_day=Decimal("6.50")),
        lambda: make_calendar_day(db_session, catalog.calendar, day=date(2026, 3, 11),
                                  kind=WorkingCalendarDayKind.NON_WORKING),
        lambda: _update(db_session, AbsenceBudget, catalog.budget.id,
                        budget_days=Decimal("38.00")),
        lambda: _update(db_session, AbsenceType, catalog.holiday.id, generates_cost=False),
        lambda: (
            _update(db_session, AbsenceType, catalog.statutory.id, is_statutory_leave=False),
            _update(db_session, AbsenceType, catalog.training.id, is_statutory_leave=True),
        ),
        lambda: _update(db_session, CatalogDefaultRate, catalog.rate.id,
                        default_cost_rate=Decimal("999.0000")),
    ]
    twin_values = [_cost(client, project.id, twin.id)["paid_absence_amount"]]
    for number, edit in enumerate(edits, start=1):
        edit()
        db_session.expire_all()
        assert _paid(_cost(client, project.id, scenario.id)) == approved, (
            f"edit {number} moved the approved paid-absence cost (AC-04)"
        )
        twin_values.append(_cost(client, project.id, twin.id)["paid_absence_amount"])
        assert twin_values[-1] != twin_values[-2], f"edit {number} did not reach the catalogue"


def test_k_06_an_approved_scenario_resolves_its_frozen_budget_windows_per_month(
    client: TestClient, db_session: Session
) -> None:
    """K-06, ADR-0004 aneks SC-5-06 point 2 (the condition of aneks SC-3-03 point 7c met): a plan
    over December 2026 and January 2027, two budget windows — 26 days in 2026, 20 in 2027 —
    headcount 1, no booking. December: 195.00 h, its twelfth by remainder distribution 16.25 h;
    January: 150.00 h, 12.50 h. 28.75 h × 120 = **3450.00**, before and after the approval, and
    after both budgets are raised to 40 days. A reader taking one frozen window per pair would cost
    both months at one of them."""
    catalog = _catalog(db_session)
    make_absence_budget(
        db_session, catalog.calendar, catalog.dimensions.engagement_type_id,
        budget_days=Decimal("20.00"), effective_from=date(2027, 1, 1),
        effective_to=date(2027, 12, 31),
    )
    project, scenario, _ = _scenario(
        db_session, catalog, headcount=1, months=(date(2026, 12, 1), date(2027, 1, 1)),
        standard_absences=False,
    )
    before = _cost(client, project.id, scenario.id)
    assert (before["paid_absence_amount"], before["paid_absence_budget_amount"]) == (
        "3450.00", "3450.00"
    )

    _approve(client, project.id, scenario.id)
    assert _paid(_cost(client, project.id, scenario.id)) == _paid(before)

    db_session.execute(sa.update(AbsenceBudget).values(budget_days=Decimal("40.00")))
    db_session.expire_all()
    assert _paid(_cost(client, project.id, scenario.id)) == _paid(before)


def test_m_2_an_approved_component_is_read_from_its_own_snapshot_never_from_another(
    client: TestClient, db_session: Session
) -> None:
    """M-2 — two scenarios of one tuple, one calendar and one budget, in two projects, approved on
    either side of a catalogue edit of **all three** snapshot inputs (standard day 7.50 → 6.50,
    budget 26 → 38 days, the holiday's `generates_cost` → false). The first froze 11700.00; the
    second froze its own, different answer. Each reads back exactly what it had before its own
    approval.

    Mutations: the `scenario_id` condition dropped from any of the frozen readers — the calendar
    (two rows for one location, one wins), the budget (two windows over one month, one wins), the
    flags (two answers per type, one wins) or the statutory type (two rows, an error): at least one
    of the two scenarios then answers the other's figure or fails.
    """
    catalog = _catalog(db_session)
    first_project, first, _ = _scenario(db_session, catalog, name="First")
    second_project, second, _ = _scenario(db_session, catalog, name="Second")

    first_before = _paid(_cost(client, first_project.id, first.id))
    _approve(client, first_project.id, first.id)
    _update(db_session, WorkingCalendar, catalog.calendar.id,
            standard_hours_per_day=Decimal("6.50"))
    _update(db_session, AbsenceBudget, catalog.budget.id, budget_days=Decimal("38.00"))
    _update(db_session, AbsenceType, catalog.holiday.id, generates_cost=False)
    second_before = _paid(_cost(client, second_project.id, second.id))
    _approve(client, second_project.id, second.id)

    assert first_before["paid_absence_amount"] == "11700.00"
    assert second_before["paid_absence_amount"] != first_before["paid_absence_amount"]
    assert _paid(_cost(client, first_project.id, first.id)) == first_before
    assert _paid(_cost(client, second_project.id, second.id)) == second_before


def test_m_2_the_frozen_calendar_days_are_read_from_the_own_snapshot_only(
    client: TestClient, db_session: Session
) -> None:
    """M-2 for the frozen **calendar days** (`approved_snapshot_working_calendar_day`), which the
    test above does not reach: it edits no day, so both snapshots hold the same (empty) set of
    exceptional days and a days reader without its `scenario_id` condition merges two identical
    sets — QA, 2026-09-23: that mutation survived the M-2 test above.

    Two scenarios of one calendar, approved on either side of **one** edit: Wednesday 11 March
    declared non-working. The first froze no exceptional day — its holiday (9–15 March) is 5 working
    days, 11700.00. The second froze 11 March — 4 days, 11700.00 − 7.50 × 120 = **10800.00** (the
    contrast that the edit reached the catalogue and the second snapshot). Only the day differs
    between the two snapshots, so a days reader merging them by `source_calendar_id` makes the
    first answer 10800.00.
    """
    catalog = _catalog(db_session)
    first_project, first, _ = _scenario(db_session, catalog, name="Days first")
    second_project, second, _ = _scenario(db_session, catalog, name="Days second")

    first_before = _paid(_cost(client, first_project.id, first.id))
    _approve(client, first_project.id, first.id)
    make_calendar_day(db_session, catalog.calendar, day=date(2026, 3, 11),
                      kind=WorkingCalendarDayKind.NON_WORKING)
    db_session.expire_all()
    second_before = _paid(_cost(client, second_project.id, second.id))
    _approve(client, second_project.id, second.id)

    assert first_before["paid_absence_amount"] == "11700.00"
    assert second_before["paid_absence_amount"] == "10800.00"
    assert _paid(_cost(client, first_project.id, first.id)) == first_before
    assert _paid(_cost(client, second_project.id, second.id)) == second_before


@pytest.mark.parametrize(
    ("budget_days", "month", "holiday"),
    [
        pytest.param(None, MAR, (date(2026, 3, 9), date(2026, 3, 15)), id="no_budget_at_all"),
        pytest.param(
            Decimal("26.00"), date(2027, 3, 1), (date(2027, 3, 8), date(2027, 3, 12)),
            id="plan_beyond_the_last_budget_window",
        ),
    ],
)
def test_r_01_m_1_a_named_non_costing_statutory_type_without_a_frozen_budget_survives_approval(
    client: TestClient, db_session: Session, budget_days: Decimal | None, month: date,
    holiday: tuple[date, date],
) -> None:
    """R-01 (invariant-guardian + reviewer, SC-5-06, High) — control M-1 in the one state no other
    test of this file reaches: the catalogue **names** a statutory type, its `generates_cost` is
    `false` (the column's default), and the approval freezes **no** budget window for the scenario's
    pair — either because no budget exists at all (a B2B-like pair) or because the plan lies beyond
    the last window the catalogue has — and nothing is booked against the statutory type.

    The draft answers `calculated`: the statutory type is named and costs nothing, so the budget
    part is a decided `0` (`statutory_leave_not_cost_generating`) and only the booked paid holiday
    costs — 5 working days × 7.50 h × 120 = **4500.00**. The approved scenario must answer the same,
    field for field, in the very request after the approval. The defect answers `no_budget`: the
    snapshot holds no row with `is_statutory_leave = true`, `frozen_statutory_leave_type` returns
    `None`, and the reader can no longer tell "named, not cost-generating" from "nobody named one".
    Nothing repairs it later — the snapshot has no `UPDATE` path.

    This is control M-3 of ADR-0004, aneks 2026-09-23 SC-5-06 (point 5, which changed contract S-02
    so that the flagged type is frozen whenever the scenario plans a month in a location with a
    calendar, budget or no budget). Its contrast — no calendar in the location, the type **not**
    frozen — is `test_scenario_approval_snapshot.py::test_k_07_m_3_the_flagged_type_is_frozen_by_
    the_location_calendar_not_by_a_frozen_budget` (second scenario) and `test_k_07_a_scenario_whose_
    location_has_no_calendar_freezes_neither_a_calendar_nor_a_budget`.
    """
    catalog = _catalog(
        db_session, budget_days=budget_days, statutory_generates_cost=False,
    )
    project, scenario, position = _scenario(
        db_session, catalog, months=(month,), standard_absences=False
    )
    make_absence(db_session, position, catalog.holiday, start_date=holiday[0], end_date=holiday[1])

    before = _paid(_cost(client, project.id, scenario.id))
    assert (before["paid_absence_state"], before["paid_absence_amount"],
            before["paid_absence_budget_amount"]) == ("calculated", "4500.00", "0.00"), (
        "the premise failed: the draft does not cost this plan as 'statutory type named, not "
        f"cost-generating': {before}"
    )

    _approve(client, project.id, scenario.id)
    db_session.expire_all()

    assert _paid(_cost(client, project.id, scenario.id)) == before, (
        "the approval changed the paid-absence component in the same request (control M-1): the "
        "named, non-cost-generating statutory type was not frozen because no budget was, and the "
        "approved reader took its absence for 'no statutory type named'"
    )


# --- K-07: the revenue does not move, byte for byte ---------------------------------------------


def test_k_07_the_revenue_answer_is_byte_for_byte_the_same_whatever_the_paid_absences(
    client: TestClient, db_session: Session
) -> None:
    """K-07 — a T&M rule on the standard plan: revenue 100 billable h × 200 = 20000.00, `calculated`
    and non-zero. The whole `GET …/commercial-terms` body is compared **as bytes**:

    1. before any booking;
    2. after the three bookings (the component moves 3900.00 → 11700.00 — the contrast that the
       bookings reached a calculation at all);
    3. after `generates_revenue` is flipped on every type (true ↔ false).

    Mutation: the component or the flags leaking into the revenue path in any way.
    """
    catalog = _catalog(db_session)
    project, scenario, position = _scenario(db_session, catalog, standard_absences=False)
    created = client.post(
        commercial_terms_path(project.id, scenario.id),
        json={"model_type": "time_and_material"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert created.status_code == 201, created.text

    def revenue_bytes() -> bytes:
        response = client.get(
            commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        assert response.status_code == 200, response.text
        return response.content

    before = revenue_bytes()
    revenue = client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    ).json()["revenue"]
    assert (revenue["state"], revenue["amount"]) == ("calculated", "20000.00")
    assert _cost(client, project.id, scenario.id)["paid_absence_amount"] == "3900.00"

    make_absence(db_session, position, catalog.holiday,
                 start_date=date(2026, 3, 9), end_date=date(2026, 3, 15))
    make_absence(db_session, position, catalog.training,
                 start_date=date(2026, 3, 16), end_date=date(2026, 3, 20))
    make_absence(db_session, position, catalog.statutory,
                 start_date=date(2026, 3, 2), end_date=date(2026, 3, 5))
    db_session.expire_all()
    assert _cost(client, project.id, scenario.id)["paid_absence_amount"] == "11700.00"
    assert revenue_bytes() == before

    for absence_type in (catalog.holiday, catalog.training, catalog.statutory):
        _update(db_session, AbsenceType, absence_type.id,
                generates_revenue=not absence_type.generates_revenue)
    assert revenue_bytes() == before


# --- N-4: the component and the revenue path never import each other ---------------------------

PAID_ABSENCE_MODULES = {"app.data.paid_absence_cost", "app.domain.paid_absence_cost"}
REVENUE_MODULES = {"app.data.commercial_terms", "app.domain.revenue",
                   "app.domain.revenue_time_and_material", "app.domain.revenue_story_points"}


def _imports_of(module: str) -> set[str]:
    """The `app.*` modules one module imports, read from its syntax tree."""
    path = Path(BACKEND_ROOT, *module.split(".")).with_suffix(".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return {name for name in imported if name.startswith("app.")}


def _reachable_from(module: str) -> set[str]:
    """Every `app.*` module reachable from `module` through imports — transitively, so a revenue
    import two hops away (through a shared helper) fails as surely as a direct one."""
    seen: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        for imported in _imports_of(current):
            if imported not in seen and Path(
                BACKEND_ROOT, *imported.split(".")
            ).with_suffix(".py").exists():
                seen.add(imported)
                pending.append(imported)
    return seen


def test_n_4_the_paid_absence_component_and_the_revenue_path_never_reach_each_other() -> None:
    """N-4 (rule 10 of the Invariant Guardian; the mirror of C-5) — structurally, transitively:

    - nothing reachable from either component module is a revenue module;
    - nothing reachable from any revenue module is a component module;
    - neither component module names `generates_revenue` in its code (docstrings aside).

    The contrast, so this cannot pass by reading files that import nothing: the component's data
    module does reach the base cost's domain module (its rate type) and the capacity's rules.
    """
    for module in PAID_ABSENCE_MODULES:
        assert not (_reachable_from(module) & REVENUE_MODULES), f"{module} reaches revenue"
    for module in REVENUE_MODULES:
        assert not (_reachable_from(module) & PAID_ABSENCE_MODULES), f"{module} reaches the cost"

    for module in PAID_ABSENCE_MODULES:
        tree = ast.parse(
            Path(BACKEND_ROOT, *module.split(".")).with_suffix(".py").read_text(encoding="utf-8")
        )
        names = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        assert "generates_revenue" not in names, module

    assert {"app.domain.personnel_cost", "app.domain.capacity", "app.data.staffing"} <= (
        _reachable_from("app.data.paid_absence_cost")
    )


# --- the named location of the snapshot readers (so a mutation has one place to go) ---------------


def test_m_1_the_approved_component_reads_no_live_catalogue_table(
    client: TestClient, db_session: Session
) -> None:
    """M-1, the strongest form: after the approval, the live calendar, budget and absence-type rows
    the scenario read are made **unreadable as values** — the location's calendar pointer removed,
    the budget deleted, every type unflagged — and the approved component is still the draft's. A
    reader falling back to any live table answers `no_calendar`, `no_budget` or
    `no_statutory_leave_type` here instead."""
    catalog = _catalog(db_session)
    project, scenario, _ = _scenario(db_session, catalog)
    before = _paid(_cost(client, project.id, scenario.id))
    _approve(client, project.id, scenario.id)

    _update(db_session, CatalogLocation, catalog.dimensions.location_id, calendar_id=None)
    db_session.execute(sa.delete(AbsenceBudget).where(AbsenceBudget.id == catalog.budget.id))
    db_session.execute(sa.update(AbsenceType).values(is_statutory_leave=False))
    db_session.flush()
    db_session.expire_all()

    assert _paid(_cost(client, project.id, scenario.id)) == before
