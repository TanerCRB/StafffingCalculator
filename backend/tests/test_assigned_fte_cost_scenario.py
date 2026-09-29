"""SC-5-04, K-01..K-06 and FA-2..FA-11 — the assigned-FTE cost of a scenario, read through the real
endpoints against a real PostgreSQL (F-07; Issue #79; ADR-0013, addendum 2026-09-29 SC-5-04).

The pure formula and the import structure are `tests/test_assigned_fte_cost.py`; the constraints and
the migration are `tests/test_assigned_fte_schema.py`; who may write and see the figure is
`tests/test_assigned_fte_api.py`. This file proves the **wiring**: the dispatch at the formula
input, the rate freeze, the frozen calendar, the what-if.

**Every expected figure is a literal worked out by hand**, never derived from the code under test.
The fixture calendar is 7.50 h/day, Monday to Saturday (March 2026: 26 working days, **195 basis
hours** — not 8 hours a day, and Saturday is a working day). With an FTE of 0.3333:

    hour rate 100      -> 0.3333 x 195 x 100  = 6499.35   (the chained 64.99 x 100 gives 6499.00)
    day rate 1000      -> 0.3333 x 26 x 1000  = 8665.80
    month rate 10000   -> 0.3333 x 10000      = 3333.00

The **hour** rate is the trap-closing one: at a `day` rate the day length cancels, at a `month` rate
both it and the day count do, so a wrong calendar is visible only at an hourly rate (the fixtures of
every test below that prove a calendar claim use one).
"""

import uuid
from datetime import date
from decimal import Decimal
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
from app.models.staffing import StaffingPosition, StaffingPositionAllocation
from tests.conftest import (
    IN_SCOPE_USER,
    MONDAY_TO_FRIDAY,
    MONDAY_TO_SATURDAY,
    DimensionTuple,
    approve_path,
    as_caller,
    caller_holding,
    make_absence,
    make_absence_type,
    make_allocation,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    staffing_path,
)
from tests.test_cost_rate_unit import _calendar, _frozen_window
from tests.test_scenario_what_if import what_if_path

MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
FTE = Decimal("0.3333")


def personnel_cost_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/personnel-cost"


# --- fixtures ------------------------------------------------------------------------------------


def _project(session: Session, suffix: str):
    return make_project(
        session, name=f"FTE {suffix}", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )


def _saturday_calendar(session: Session, suffix: str) -> WorkingCalendar:
    return _calendar(session, hours="7.50", name=f"Saturday {suffix}", pattern=MONDAY_TO_SATURDAY)


def _position(
    session: Session,
    scenario,
    dimensions: DimensionTuple,
    *,
    basis: str = "assigned_fte",
    fte: str | None = str(FTE),
    months: tuple[date, ...] = (MAR,),
    planned: str = "1.00",
    headcount: int = 2,
    fixed: tuple[str, str] | None = None,
) -> StaffingPosition:
    """A position on `basis` with one allocation row per month. `planned` is deliberately unrelated
    to the FTE, so a formula that read it would be visible."""
    position = make_staffing_position(
        session, scenario, dimensions, headcount=headcount, start_date=months[0] if months else MAR,
        cost_basis=basis,
        assigned_fte=None if fte is None else Decimal(fte),
        fixed_amount=None if fixed is None else Decimal(fixed[0]),
        fixed_amount_currency=None if fixed is None else fixed[1],
    )
    for month in months:
        make_allocation(session, position, period_month=month,
                        planned_allocation_hours=Decimal(planned))
    return position


def _plan(
    session: Session,
    *,
    suffix: str,
    fte: str | None = str(FTE),
    basis: str = "assigned_fte",
    unit: str = "hour",
    rate: str = "100.0000",
    calendar: WorkingCalendar | None | str = "saturday",
    months: tuple[date, ...] = (MAR,),
    planned: str = "1.00",
    headcount: int = 2,
    surcharge: str = "0",
    project=None,
    currency: str = "PLN",
    rate_currency: str = "PLN",
) -> dict[str, Any]:
    """A draft scenario declaring PLN, one `assigned_fte` position on a fresh tuple whose location
    uses `calendar` (`"saturday"`: the module's fixture calendar), and one open-ended cost window in
    `unit`."""
    if project is None:
        project = _project(session, suffix)
    scenario = make_scenario(session, project, name=f"Baseline {suffix}", currency=currency)
    if calendar == "saturday":
        calendar = _saturday_calendar(session, suffix)
    dimensions = make_dimension_tuple(session, suffix=f" {suffix}", calendar=calendar)
    position = _position(session, scenario, dimensions, basis=basis, fte=fte, months=months,
                         planned=planned, headcount=headcount)
    window = make_rate(
        session, dimensions, effective_from=date(2026, 1, 1), currency=rate_currency,
        default_cost_rate=Decimal(rate), default_selling_rate=Decimal("200.0000"),
        cost_rate_unit=unit, surcharge_percent=Decimal(surcharge),
    )
    return {
        "project": project, "scenario": scenario, "dimensions": dimensions, "position": position,
        "window": window, "calendar": calendar,
    }


def _cost(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    with caller_holding(*Permission):
        response = client.get(personnel_cost_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()["personnel_cost"]


def _fte(cost: dict[str, Any]) -> tuple[str, str, str | None]:
    return cost["assigned_fte_state"], cost["assigned_fte_amount"], cost["assigned_fte_currency"]


def _approve(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> None:
    response = client.post(approve_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text


def _update(session: Session, model: type, row_id: uuid.UUID, **values: object) -> None:
    session.execute(sa.update(model).where(model.id == row_id).values(**values))
    session.flush()
    session.expire_all()


def _frozen_rates(session: Session, scenario_id: uuid.UUID) -> list[Any]:
    session.expire_all()
    return list(
        session.execute(
            sa.select(ApprovedSnapshotCatalogDefaultRate).where(
                ApprovedSnapshotCatalogDefaultRate.scenario_id == scenario_id
            )
        ).scalars()
    )


# --- FA-2 / FA-3 / K-02: the figure, through the real reader -------------------------------------


@pytest.mark.parametrize(
    ("unit", "rate", "expected"),
    [
        pytest.param("hour", "100.0000", "6499.35", id="hour"),
        pytest.param("day", "1000.0000", "8665.80", id="day"),
        pytest.param("month", "10000.0000", "3333.00", id="month"),
    ],
)
def test_fa_2_the_endpoint_prices_the_fte_by_the_rate_unit(
    client: TestClient, db_session: Session, unit: str, rate: str, expected: str
) -> None:
    """FA-2/K-02 — `hour` = 0.3333 x 26 x 7.5 x 100, `day` = 0.3333 x 26 x 1000, `month` =
    0.3333 x 10000, on the calendar the reader really loads (7.5 h, Mon-Sat). The hour case is
    6499.35, **not** the chained 6499.00 (FA-3: the exact product, one rounding).

    Mutations killed: unit ignored; a constant 8-hour day (6932.64); `weekday() < 5` (5499.45); the
    hours rounded before pricing (6499.00); a second rounding of the month amount."""
    plan = _plan(db_session, suffix=f"fa2-{unit}", unit=unit, rate=rate)

    cost = _cost(client, plan["project"].id, plan["scenario"].id)

    assert _fte(cost) == ("calculated", expected, "PLN")
    assert cost["assigned_fte_state"] != "no_calendar"


def test_fa_2_the_planned_hours_of_the_grid_are_not_an_input(
    client: TestClient, db_session: Session
) -> None:
    """K-01/FA-2 — the same FTE with 1 planned hour or with 500 in its allocation row costs the same
    6499.35: grid hours and stored FTE may disagree and nothing reconciles them (point 4). Mutation:
    the formula reading `planned_allocation_hours`."""
    figures = {}
    for planned in ("1.00", "500.00"):
        plan = _plan(db_session, suffix=f"fa2-hours-{planned}", planned=planned)
        figures[planned] = _fte(_cost(client, plan["project"].id, plan["scenario"].id))

    assert figures == {
        "1.00": ("calculated", "6499.35", "PLN"),
        "500.00": ("calculated", "6499.35", "PLN"),
    }


def test_fa_2_the_days_of_the_location_calendar_are_data(
    client: TestClient, db_session: Session
) -> None:
    """FA-2 — one Tuesday marked `NON_WORKING` leaves 25 working days: 0.3333 x 25 x 7.5 x 100 =
    6249.375 -> 6249.38. A Monday-to-Friday twin (22 days) is 5499.45. Both are the calendar's own
    data, read by the real reader. Mutations: the exceptional days ignored (6499.35); the pattern
    ignored."""
    holiday = _plan(db_session, suffix="fa2-holiday")
    make_calendar_day(db_session, holiday["calendar"], day=date(2026, 3, 10),
                      kind=WorkingCalendarDayKind.NON_WORKING)
    weekdays = _plan(
        db_session, suffix="fa2-weekdays",
        calendar=_calendar(db_session, hours="7.50", name="Weekdays fa2", pattern=MONDAY_TO_FRIDAY),
    )

    assert _fte(_cost(client, holiday["project"].id, holiday["scenario"].id))[1] == "6249.38"
    assert _fte(_cost(client, weekdays["project"].id, weekdays["scenario"].id))[1] == "5499.45"


def test_fa_2_headcount_is_no_factor_and_a_value_above_it_is_priced_as_stored(
    client: TestClient, db_session: Session
) -> None:
    """Point 2 — the stored FTE is a position total. headcount 1 and headcount 5 give the same
    6499.35; 2.5 FTE on a one-person position is 2.5 x 195 x 100 = 48750.00 (accepted without bound,
    never clamped, never flagged). Mutation: `x headcount`, or clamping at the headcount."""
    figures = {}
    for headcount in (1, 5):
        plan = _plan(db_session, suffix=f"fa2-hc-{headcount}", headcount=headcount)
        figures[headcount] = _fte(_cost(client, plan["project"].id, plan["scenario"].id))[1]
    above = _plan(db_session, suffix="fa2-above", fte="2.5000", headcount=1)

    assert figures == {1: "6499.35", 5: "6499.35"}
    assert _fte(_cost(client, above["project"].id, above["scenario"].id))[1] == "48750.00"


def test_fa_3_months_are_summed_unrounded_and_rounded_once_through_the_endpoint(
    client: TestClient, db_session: Session
) -> None:
    """FA-3 — 0.1 FTE at 0.03 per hour over March and April (195 h basis each): each month is
    19.5 h x 0.03 = 0.585, the sum 1.17. Rounding each month first is 1.18. Mutation: `round_money`
    per month."""
    plan = _plan(db_session, suffix="fa3-months", fte="0.1000", rate="0.0300", months=(MAR, APR))

    assert _fte(_cost(client, plan["project"].id, plan["scenario"].id)) == (
        "calculated", "1.17", "PLN",
    )


# --- FA-4: exclusive dispatch — both directions --------------------------------------------------


def _mixed_scenario(session: Session, suffix: str) -> dict[str, Any]:
    """One `worked_time` position (120 planned hours at 100 per hour -> 12000.00) beside the FTE
    position of `_plan`; both on the same tuple's catalogue rate."""
    plan = _plan(session, suffix=suffix, planned="200.00")
    worked_dimensions = make_dimension_tuple(session, suffix=f" {suffix} worked")
    make_rate(session, worked_dimensions, effective_from=date(2026, 1, 1), currency="PLN",
              default_cost_rate=Decimal("100.0000"), default_selling_rate=Decimal("200.0000"))
    worked = _position(session, plan["scenario"], worked_dimensions, basis="worked_time", fte=None,
                       planned="120.00")
    return plan | {"worked": worked}


def test_fa_4_an_fte_position_with_allocation_rows_is_not_priced_by_the_worked_time_formula(
    client: TestClient, db_session: Session
) -> None:
    """FA-4, direction one — the FTE position has an allocation row of 200 planned hours **and a
    catalogue rate**, so the worked-time formula would price it (200 x 100 = 20000.00) if it were
    handed the row. The base cost is the worked position's 12000.00 alone, the fully loaded figure
    likewise, and the FTE figure is its own 6499.35. Mutation: the dispatch removed (base
    32000.00)."""
    plan = _mixed_scenario(db_session, "fa4-one")

    cost = _cost(client, plan["project"].id, plan["scenario"].id)

    assert (cost["state"], cost["amount"], cost["fully_loaded_amount"]) == (
        "calculated", "12000.00", "12000.00",
    )
    assert _fte(cost) == ("calculated", "6499.35", "PLN")


def test_fa_4_the_dispatch_is_by_basis_not_by_having_allocation_rows(
    client: TestClient, db_session: Session
) -> None:
    """FA-4, direction two — the contrast that kills the over-correction "no position with
    allocation rows reaches the worked-time formula". The same FTE row is switched to `worked_time`
    (raw SQL, keeping its allocation row): now the base cost is 12000.00 + 200 x 100 = 32000.00 and
    the FTE component is `0.00` of the scenario's currency."""
    plan = _mixed_scenario(db_session, "fa4-two")
    as_fte = _cost(client, plan["project"].id, plan["scenario"].id)

    db_session.execute(
        sa.update(StaffingPosition).where(StaffingPosition.id == plan["position"].id)
        .values(cost_basis="worked_time", assigned_fte=None)
    )
    db_session.flush()
    db_session.expire_all()
    as_worked = _cost(client, plan["project"].id, plan["scenario"].id)

    assert (as_fte["amount"], _fte(as_fte)[1]) == ("12000.00", "6499.35")
    assert (as_worked["amount"], _fte(as_worked)) == ("32000.00", ("calculated", "0.00", "PLN"))


def test_fa_4_a_fixed_amount_position_with_allocation_rows_is_not_priced_twice(
    client: TestClient, db_session: Session
) -> None:
    """FA-4 / H-1 — the existing defect, fixed in this task: a `fixed_amount` position (stated
    500.00) with an allocation row of 200 hours and a catalogue rate was also priced by the
    worked-time formula (20000.00 on top). Now the base cost is the worked position's 12000.00 and
    the fixed component is 500.00. Mutation: the dispatch removed for `fixed_amount` (base
    32000.00)."""
    plan = _mixed_scenario(db_session, "fa4-fixed")
    db_session.execute(
        sa.update(StaffingPosition).where(StaffingPosition.id == plan["position"].id)
        .values(cost_basis="fixed_amount", assigned_fte=None, fixed_amount=Decimal("500.0000"),
                fixed_amount_currency="PLN")
    )
    db_session.flush()
    db_session.expire_all()

    cost = _cost(client, plan["project"].id, plan["scenario"].id)

    assert (cost["state"], cost["amount"]) == ("calculated", "12000.00")
    assert (cost["fixed_amount_state"], cost["fixed_amount_amount"]) == ("calculated", "500.00")
    assert _fte(cost) == ("calculated", "0.00", "PLN")


def test_fa_4_a_fixed_amount_position_month_without_a_rate_no_longer_withholds_the_base_cost(
    client: TestClient, db_session: Session
) -> None:
    """FA-4 / H-1 — the same defect seen from the other side: a `fixed_amount` position whose tuple
    has **no catalogue rate at all** made every month of it `no_cost_rate` and withheld the whole
    worked-time cost. It is not a worked-time position, so it can no longer do that."""
    plan = _mixed_scenario(db_session, "fa4-norate")
    unrated = make_dimension_tuple(db_session, suffix=" fa4-norate fixed")
    _position(db_session, plan["scenario"], unrated, basis="fixed_amount", fte=None,
              fixed=("500.0000", "PLN"))

    cost = _cost(client, plan["project"].id, plan["scenario"].id)

    assert (cost["state"], cost["amount"]) == ("calculated", "12000.00")
    assert cost["fixed_amount_amount"] == "500.00"
    assert cost["assumptions_used"]["unresolved_months"] == []


def test_fa_4_the_predicate_and_the_grid_statement_do_not_know_the_cost_basis() -> None:
    """FA-4 (structural) — the dispatch is at the formula input, not inside `month_has_cost_rate` or
    `costed_month_windows`, which is what keeps the approval's rate freeze covering FTE positions.
    Neither function's source mentions a cost basis; the dispatcher does. Mutation: the basis filter
    moved into the predicate's statement."""
    import inspect

    from app.data import personnel_cost as module

    for function in (module.month_has_cost_rate, module.costed_month_windows):
        source = inspect.getsource(function)
        assert "cost_basis" not in source and "assigned_fte" not in source, function.__name__
    assert "COST_BASIS_ASSIGNED_FTE" in inspect.getsource(module.dispatch_cost_inputs)


def test_fa_4_the_approval_freezes_the_rate_of_an_fte_only_scenario(
    client: TestClient, db_session: Session
) -> None:
    """FA-4 — a scenario whose **only** position is on the FTE basis: the approval still freezes its
    catalogue window (the copier asks the same predicate about every position's rate), the approved
    figure is the draft's 6499.35 read from the snapshot, and a later catalogue edit (rate 999, unit
    `month`) does not move it while a draft twin does. Mutation: FTE positions excluded from the
    predicate — nothing is frozen and the approved component becomes `no_cost_rate`."""
    plan = _plan(db_session, suffix="fa4-freeze")
    twin = _plan(db_session, suffix="fa4-freeze-twin", project=plan["project"])
    draft = _cost(client, plan["project"].id, plan["scenario"].id)
    twin_before = _fte(_cost(client, plan["project"].id, twin["scenario"].id))

    _approve(client, plan["project"].id, plan["scenario"].id)

    frozen = _frozen_rates(db_session, plan["scenario"].id)
    assert len(frozen) == 1 and frozen[0].default_cost_rate == Decimal("100.0000")
    approved = _cost(client, plan["project"].id, plan["scenario"].id)
    assert approved["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert _fte(approved) == _fte(draft) == ("calculated", "6499.35", "PLN")

    for window in (plan["window"], twin["window"]):
        _update(db_session, CatalogDefaultRate, window.id, default_cost_rate=Decimal("999.0000"),
                cost_rate_unit="month")
    assert _fte(_cost(client, plan["project"].id, plan["scenario"].id)) == _fte(draft)
    assert _fte(_cost(client, plan["project"].id, twin["scenario"].id)) != twin_before


# --- FA-5: no allocation rows ---------------------------------------------------------------------


def test_fa_5_an_fte_position_without_allocation_rows_is_no_planned_months_never_zero(
    client: TestClient, db_session: Session
) -> None:
    """FA-5 — a stored FTE and no month: `no_planned_months`, `"n/a"`, no currency — never `0.00`.
    Only this component is withheld (the worked position's 12000.00 stays). Contrast: the same
    position with one allocation row is calculated. Mutation: an empty grid summed to `0.00`."""
    plan = _mixed_scenario(db_session, "fa5")
    db_session.execute(
        sa.delete(StaffingPositionAllocation).where(
            StaffingPositionAllocation.position_id == plan["position"].id
        )
    )
    db_session.flush()
    db_session.expire_all()

    without = _cost(client, plan["project"].id, plan["scenario"].id)
    make_allocation(db_session, plan["position"], period_month=MAR)
    with_row = _cost(client, plan["project"].id, plan["scenario"].id)

    assert _fte(without) == ("no_planned_months", "n/a", None)
    assert (without["state"], without["amount"]) == ("calculated", "12000.00")
    assert _fte(with_row) == ("calculated", "6499.35", "PLN")


# --- FA-6: calendar states for an hourly FTE position, precedence, "only this component" ----------


def test_fa_6_an_hourly_fte_position_without_a_calendar_is_no_calendar_and_only_it_is_withheld(
    client: TestClient, db_session: Session
) -> None:
    """FA-6 / H-3 — the trap: at an hour rate the worked-time basis reads no calendar at all, so a
    reader that decided "needs a calendar" by the unit would price this FTE position with no hours.
    The FTE component is `no_calendar` (no amount, never 0) — while the **worked-time** position
    on an hourly rate with no calendar in the same scenario is calculated (its 12000.00), which is
    the contrast: the two bases differ on purpose (point 5)."""
    plan = _plan(db_session, suffix="fa6-nocal", calendar=None, unit="hour", planned="200.00")
    worked_dimensions = make_dimension_tuple(db_session, suffix=" fa6-nocal worked", calendar=None)
    make_rate(db_session, worked_dimensions, effective_from=date(2026, 1, 1), currency="PLN",
              default_cost_rate=Decimal("100.0000"))
    _position(db_session, plan["scenario"], worked_dimensions, basis="worked_time", fte=None,
              planned="120.00")

    cost = _cost(client, plan["project"].id, plan["scenario"].id)

    assert _fte(cost) == ("no_calendar", "n/a", None)
    assert (cost["state"], cost["amount"]) == ("calculated", "12000.00")


@pytest.mark.parametrize("unit", ["hour", "day", "month"])
def test_fa_6_a_calendar_with_no_working_day_is_no_working_days_for_every_unit(
    client: TestClient, db_session: Session, unit: str
) -> None:
    """FA-6 / H-3 — `D = 0`: the state is named before any division, for each unit (the worked-time
    basis would price a `day` rate here from `S` alone). Contrast: the same plan on a working
    calendar is calculated."""
    closed = _calendar(db_session, hours="7.50", name=f"Closed {unit}", pattern="0000000")
    plan = _plan(db_session, suffix=f"fa6-closed-{unit}", unit=unit, rate="100.0000",
                 calendar=closed)
    open_plan = _plan(db_session, suffix=f"fa6-open-{unit}", unit=unit, rate="100.0000")

    closed_cost = _cost(client, plan["project"].id, plan["scenario"].id)
    open_cost = _cost(client, open_plan["project"].id, open_plan["scenario"].id)

    assert _fte(closed_cost) == ("no_working_days", "n/a", None)
    assert _fte(open_cost)[0] == "calculated"


def test_fa_6_precedence_between_calendar_currency_and_rate_states(
    client: TestClient, db_session: Session
) -> None:
    """FA-6 (point 5), through the endpoint — `no_calendar` before `no_working_days` when one
    scenario has both; `currency_mismatch` before `no_calendar`; `no_cost_rate` before
    `currency_mismatch`. Each earlier state is present alongside a later one in the same
    scenario."""
    both = _plan(db_session, suffix="fa6-both", calendar=None)
    closed = _calendar(db_session, hours="7.50", name="Closed both", pattern="0000000")
    closed_dimensions = make_dimension_tuple(db_session, suffix=" fa6-both closed", calendar=closed)
    _position(db_session, both["scenario"], closed_dimensions)
    make_rate(db_session, closed_dimensions, effective_from=date(2026, 1, 1), currency="PLN")

    mismatch = _plan(db_session, suffix="fa6-mismatch", calendar=None, rate_currency="EUR")

    unrated = _plan(db_session, suffix="fa6-unrated", calendar=None, rate_currency="EUR")
    bare = make_dimension_tuple(db_session, suffix=" fa6-unrated bare")
    _position(db_session, unrated["scenario"], bare)

    assert _fte(_cost(client, both["project"].id, both["scenario"].id))[0] == "no_calendar"
    assert _fte(_cost(client, mismatch["project"].id, mismatch["scenario"].id))[0] == (
        "currency_mismatch"
    )
    assert _fte(_cost(client, unrated["project"].id, unrated["scenario"].id))[0] == "no_cost_rate"


# --- K-03 / H-4: gross of absences ----------------------------------------------------------------


def test_k_03_the_fte_component_is_gross_of_absences_while_capacity_moves(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / H-4 — booking a week of absence changes the position's capacity (the contrast: the
    absence reached a calculation) and does **not** change the FTE component, which stays
    6499.35. The paid-absence component keeps its rule for an FTE position (H-4): the overcount a
    cost-generating booking causes on top of a gross FTE is named in the report, not repaired.
    Mutation: the FTE basis hours reduced by absence days."""
    plan = _plan(db_session, suffix="k03")
    paid = make_absence_type(db_session, name="Paid k03", generates_cost=True,
                             generates_revenue=False)
    before = _cost(client, plan["project"].id, plan["scenario"].id)
    with caller_holding(*Permission):
        capacity_before = client.get(
            staffing_path(plan["project"].id, plan["scenario"].id)
        ).json()["positions"][0]["allocations"][0]["derived_capacity_hours"]

    make_absence(db_session, plan["position"], paid, start_date=date(2026, 3, 9),
                 end_date=date(2026, 3, 13))
    db_session.expire_all()
    after = _cost(client, plan["project"].id, plan["scenario"].id)
    with caller_holding(*Permission):
        capacity_after = client.get(
            staffing_path(plan["project"].id, plan["scenario"].id)
        ).json()["positions"][0]["allocations"][0]["derived_capacity_hours"]

    assert _fte(before) == _fte(after) == ("calculated", "6499.35", "PLN")
    assert capacity_after != capacity_before


# --- FA-7 / K-05: approved — frozen calendar and frozen rate --------------------------------------


def _set_hours(session, plan):
    _update(session, WorkingCalendar, plan["calendar"].id, standard_hours_per_day=Decimal("6.50"))


def _set_pattern(session, plan):
    _update(session, WorkingCalendar, plan["calendar"].id, week_pattern=MONDAY_TO_FRIDAY)


def _add_holiday(session, plan):
    make_calendar_day(session, plan["calendar"], day=date(2026, 3, 11),
                      kind=WorkingCalendarDayKind.NON_WORKING)
    session.expire_all()


def _detach_calendar(session, plan):
    _update(session, CatalogLocation, plan["dimensions"].location_id, calendar_id=None)


def _raise_rate(session, plan):
    _update(session, CatalogDefaultRate, plan["window"].id, default_cost_rate=Decimal("999.0000"))


def _change_unit(session, plan):
    _update(session, CatalogDefaultRate, plan["window"].id, cost_rate_unit="month")


@pytest.mark.parametrize(
    "edit",
    [_set_hours, _set_pattern, _add_holiday, _detach_calendar, _raise_rate, _change_unit],
    ids=lambda edit: edit.__name__.strip("_"),
)
def test_fa_7_no_live_edit_moves_an_approved_fte_figure(
    client: TestClient, db_session: Session, edit
) -> None:
    """FA-7 / K-05 — carries FTE-6. An FTE scenario approved at 6499.35; then, one at a time, the
    source calendar's day length, its week pattern, a holiday, the location's `calendar_id`, the
    catalogue rate and the catalogue unit are edited. The approved figure is unchanged after each,
    and the draft twin of the same plan **does** move after the same edit (the contrast that the
    edit reached a calculation). Mutations: the FTE component reading `basis_by_location` for an
    approved scenario (moves on the first four), or the live rate (the last two)."""
    plan = _plan(db_session, suffix=f"fa7-{edit.__name__}")
    twin = _plan(db_session, suffix=f"fa7-twin-{edit.__name__}", project=plan["project"],
                 calendar=plan["calendar"])
    _approve(client, plan["project"].id, plan["scenario"].id)
    twin_before = _fte(_cost(client, plan["project"].id, twin["scenario"].id))
    assert _fte(_cost(client, plan["project"].id, plan["scenario"].id)) == (
        "calculated", "6499.35", "PLN",
    )

    edit(db_session, plan)
    edit_twin = {"_detach_calendar": lambda: _detach_calendar(db_session, twin),
                 "_raise_rate": lambda: _raise_rate(db_session, twin),
                 "_change_unit": lambda: _change_unit(db_session, twin)}
    if edit.__name__ in edit_twin:
        edit_twin[edit.__name__]()  # per-tuple edits are per-plan; calendar edits are shared

    approved = _cost(client, plan["project"].id, plan["scenario"].id)
    assert _fte(approved) == ("calculated", "6499.35", "PLN"), f"{edit.__name__} moved the figure"
    assert approved["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert _fte(_cost(client, plan["project"].id, twin["scenario"].id)) != twin_before


def test_fa_7_a_missing_frozen_calendar_key_is_no_calendar_never_a_live_lookup(
    client: TestClient, db_session: Session
) -> None:
    """FA-7 — an approved scenario whose snapshot holds the frozen **rate** but no calendar row for
    the position's location, while the live location has a working calendar: `no_calendar`. A reader
    that filled the missing key from the live calendar would answer 6499.35. Contrast: the same
    snapshot with the frozen calendar present is calculated from it (7.5 h, Mon-Sat, 195 h)."""
    project = _project(db_session, "fa7-missing")
    live = _saturday_calendar(db_session, "fa7-missing live")
    results = {}
    for name, with_calendar in (("missing", False), ("present", True)):
        scenario = make_scenario(db_session, project, name=f"Approved {name}",
                                 status=ScenarioStatus.APPROVED, currency="PLN")
        dimensions = make_dimension_tuple(db_session, suffix=f" fa7-{name}", calendar=live)
        _position(db_session, scenario, dimensions)
        db_session.add(_frozen_window(scenario.id, dimensions, cost_rate_unit="hour",
                                      default_cost_rate=Decimal("100.0000")))
        if with_calendar:
            db_session.add(ApprovedSnapshotWorkingCalendar(
                id=uuid.uuid4(), scenario_id=scenario.id, source_calendar_id=live.id,
                source_location_id=dimensions.location_id, name="Frozen",
                standard_hours_per_day=Decimal("7.50"), week_pattern=MONDAY_TO_SATURDAY,
            ))
        db_session.flush()
        db_session.expire_all()  # the project's cached `scenarios` would miss the second one
        results[name] = _fte(_cost(client, project.id, scenario.id))

    assert results["missing"] == ("no_calendar", "n/a", None)
    assert results["present"] == ("calculated", "6499.35", "PLN")


# --- FA-8: what-if -------------------------------------------------------------------------------


def test_fa_8_a_salary_raise_reaches_the_fte_component_once_and_persists_nothing(
    client: TestClient, db_session: Session
) -> None:
    """FA-8 — a 10% raise scales the catalogue rate once through the shared structure:
    0.3333 x 195 x 110 = 7149.285 -> 7149.29 (half up); a 0% raise reproduces 6499.35 exactly;
    the real endpoint still answers 6499.35 afterwards and the stored rate is still 100.0000. A
    `fixed_amount` position with allocation rows next to it is neither raised nor double-counted.
    Mutations: the FTE component not recomputed (6499.35 at 10%), or raised twice (7864.21)."""
    plan = _plan(db_session, suffix="fa8", planned="200.00")
    fixed_dimensions = make_dimension_tuple(db_session, suffix=" fa8 fixed")
    make_rate(db_session, fixed_dimensions, effective_from=date(2026, 1, 1), currency="PLN")
    _position(db_session, plan["scenario"], fixed_dimensions, basis="fixed_amount", fte=None,
              fixed=("500.0000", "PLN"), planned="200.00")

    with caller_holding(*Permission):
        raised = client.get(what_if_path(plan["project"].id, plan["scenario"].id, "10"))
        zero = client.get(what_if_path(plan["project"].id, plan["scenario"].id, "0"))
    assert raised.status_code == zero.status_code == 200
    real = _cost(client, plan["project"].id, plan["scenario"].id)

    raised_cost = raised.json()["personnel_cost"]
    assert _fte(raised_cost) == ("calculated", "7149.29", "PLN")
    assert raised_cost["assumptions_used"]["rate_source"] == "what_if_hypothetical"
    assert (raised_cost["state"], raised_cost["amount"]) == ("calculated", "0.00")
    assert raised_cost["fixed_amount_amount"] == "500.00"
    zero_cost = zero.json()["personnel_cost"]
    assert _fte(zero_cost) == _fte(real) == ("calculated", "6499.35", "PLN")
    assert zero_cost["assigned_fte_assumptions_used"] == real["assigned_fte_assumptions_used"]
    db_session.expire_all()
    assert db_session.get(CatalogDefaultRate, plan["window"].id).default_cost_rate == Decimal(
        "100.0000"
    )


# --- FA-10: no surcharge, no fully loaded figure --------------------------------------------------


def test_fa_10_a_surcharge_does_not_reach_the_fte_component_and_fully_loaded_figures_do_not_move(
    client: TestClient, db_session: Session
) -> None:
    """FA-10 — the FTE position's rate carries a 10% surcharge that the worked-time basis would
    apply: the FTE figure is 6499.35 all the same (no surcharge). The worked position beside it has
    a fully loaded figure of 13200.00 (surcharge 1200.00) with and without the FTE position —
    adding an FTE position moves neither `fully_loaded_amount` nor `surcharge_amount`. Mutation:
    the surcharge applied to the FTE amount (7149.29), or the FTE amount added into the loaded
    figure."""
    plan = _plan(db_session, suffix="fa10", surcharge="10.00")
    worked_dimensions = make_dimension_tuple(db_session, suffix=" fa10 worked")
    make_rate(db_session, worked_dimensions, effective_from=date(2026, 1, 1), currency="PLN",
              default_cost_rate=Decimal("100.0000"), surcharge_percent=Decimal("10.00"))
    _position(db_session, plan["scenario"], worked_dimensions, basis="worked_time", fte=None,
              planned="120.00")
    without_fte = make_scenario(db_session, plan["project"], name="Without FTE", currency="PLN")
    _position(db_session, without_fte, worked_dimensions, basis="worked_time", fte=None,
              planned="120.00")

    with_cost = _cost(client, plan["project"].id, plan["scenario"].id)
    without_cost = _cost(client, plan["project"].id, without_fte.id)

    assert _fte(with_cost) == ("calculated", "6499.35", "PLN")
    for cost in (with_cost, without_cost):
        assert (cost["amount"], cost["fully_loaded_amount"], cost["surcharge_amount"]) == (
            "12000.00", "13200.00", "1200.00",
        )
    assert not [
        key for key in with_cost
        if key.startswith("assigned_fte") and ("surcharge" in key or "loaded" in key)
    ]


# --- FA-11: revenue is unmoved --------------------------------------------------------------------


def test_fa_11_time_and_material_revenue_is_byte_identical_whatever_the_cost_basis(
    client: TestClient, db_session: Session
) -> None:
    """FA-11 — a T&M rule on a position billing 100 h at 200: revenue 20000.00. The whole
    `GET …/commercial-terms` body is compared as bytes with the position on `worked_time`, after it
    is switched to `assigned_fte`, and after its FTE is changed to 5.0000. The cost side moves
    (the contrast that the switch reached a calculation). Mutation: the FTE leaking into the
    revenue path."""
    plan = _plan(db_session, suffix="fa11", fte=None, basis="worked_time")
    created = client.post(
        f"/projects/{plan['project'].id}/scenarios/{plan['scenario'].id}/commercial-terms",
        json={"model_type": "time_and_material"}, headers=as_caller(IN_SCOPE_USER),
    )
    assert created.status_code == 201, created.text

    def revenue_bytes() -> bytes:
        response = client.get(
            f"/projects/{plan['project'].id}/scenarios/{plan['scenario'].id}/commercial-terms",
            headers=as_caller(IN_SCOPE_USER),
        )
        assert response.status_code == 200, response.text
        return response.content

    before = revenue_bytes()
    worked_cost = _cost(client, plan["project"].id, plan["scenario"].id)["amount"]
    for fte in ("0.3333", "5.0000"):
        db_session.execute(
            sa.update(StaffingPosition).where(StaffingPosition.id == plan["position"].id)
            .values(cost_basis="assigned_fte", assigned_fte=Decimal(fte))
        )
        db_session.flush()
        db_session.expire_all()
        assert revenue_bytes() == before

    assert _cost(client, plan["project"].id, plan["scenario"].id)["amount"] != worked_cost
    assert b"20000.00" in before


# --- QA contrast tests (SC-5-04) ------------------------------------------------------------------


def _calendar_reads(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> int:
    """How many `SELECT`s of one `personnel-cost` read touch a working-calendar table."""
    from sqlalchemy import Engine, event

    reads: list[str] = []

    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        normalised = " ".join(statement.lower().split())
        if normalised.startswith("select") and (
            "from working_calendar" in normalised or "join working_calendar" in normalised
        ):
            reads.append(normalised)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        _cost(client, project_id, scenario_id)
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    return len(reads)


def test_qa_a_fixed_amount_position_never_makes_the_reader_look_up_a_calendar(
    client: TestClient, db_session: Session
) -> None:
    """QA (ADR-0013 SC-5-04, `_worked_months`: "A `fixed_amount` position never does — no formula
    reads its months"). The figure is identical whether or not the calendar is read, so only the
    statements can tell. Other components of the read consult calendars of their own, so the count
    is compared, not asserted absolute: an hourly `worked_time` position needs no calendar (the
    baseline), the same plan at a `day` rate needs one (more reads — the probe is alive), and the
    `day` plan switched to `fixed_amount` needs none again (back to the baseline). Mutation: the
    `not in not_worked_time_ids` guard removed from `needs_calendar`."""
    hourly = _plan(db_session, suffix="qa-cal-hour", fte=None, basis="worked_time", unit="hour")
    daily = _plan(db_session, suffix="qa-cal-day", fte=None, basis="worked_time", unit="day",
                  rate="1000.0000")
    baseline = _calendar_reads(client, hourly["project"].id, hourly["scenario"].id)
    needed = _calendar_reads(client, daily["project"].id, daily["scenario"].id)
    db_session.execute(
        sa.update(StaffingPosition).where(StaffingPosition.id == daily["position"].id)
        .values(cost_basis="fixed_amount", fixed_amount=Decimal("500.0000"),
                fixed_amount_currency="PLN")
    )
    db_session.flush()
    db_session.expire_all()

    fixed = _calendar_reads(client, daily["project"].id, daily["scenario"].id)

    assert needed > baseline, "the probe does not see a calendar read even where one is needed"
    assert fixed == baseline


def test_qa_the_pairs_of_named_states_hold_through_the_endpoint_including_no_planned_months(
    client: TestClient, db_session: Session
) -> None:
    """QA (FA-6, point 5) — the states of the pure formula, asked through the real reader with both
    members of each pair present in one scenario, so a wiring that reorders or pre-empts them is
    visible at scenario level too. `no_planned_months` is the *last* named state: a second FTE
    position without any allocation row sits next to a position that carries the earlier state, and
    the earlier state wins in every pair. Contrast: the row-less position alone is
    `no_planned_months` (FA-5), and the other members of each pair alone are their own states.
    Mutation: the `no_planned_months` branch moved above any of the four, at either level."""
    closed = _calendar(db_session, hours="7.50", name="Closed qa", pattern="0000000")
    cases = {
        "no_working_days": _plan(db_session, suffix="qa-pair-days", calendar=closed),
        "no_calendar": _plan(db_session, suffix="qa-pair-nocal", calendar=None),
        "currency_mismatch": _plan(db_session, suffix="qa-pair-cur", rate_currency="EUR"),
        "no_cost_rate": _plan(db_session, suffix="qa-pair-rate"),
    }
    db_session.execute(sa.delete(CatalogDefaultRate).where(
        CatalogDefaultRate.id == cases["no_cost_rate"]["window"].id))
    db_session.flush()
    db_session.expire_all()
    alone = {
        name: _fte(_cost(client, plan["project"].id, plan["scenario"].id))[0]
        for name, plan in cases.items()
    }
    for name, plan in cases.items():
        rowless = make_dimension_tuple(db_session, suffix=f" qa-pair-{name} rowless")
        _position(db_session, plan["scenario"], rowless, months=())

    together = {
        name: _fte(_cost(client, plan["project"].id, plan["scenario"].id))[0]
        for name, plan in cases.items()
    }

    assert alone == {name: name for name in cases}
    assert together == alone


def test_qa_two_fte_positions_are_each_priced_from_their_own_stored_value(
    client: TestClient, db_session: Session
) -> None:
    """QA (K-02) — the scenario-level twin of the formula's two-position test: 0.3333 FTE on one
    tuple (6499.35) and 0.5000 on another (9750.00 = 0.5 x 195 x 100), same calendar and rate. The
    component is **16249.35**, and the `assumptions_used` lines carry each position's own value.
    Contrast: each position alone is its own figure. Mutation: one FTE used for every position."""
    plan = _plan(db_session, suffix="qa-two")
    second_dimensions = make_dimension_tuple(db_session, suffix=" qa-two second",
                                             calendar=plan["calendar"])
    make_rate(db_session, second_dimensions, effective_from=date(2026, 1, 1), currency="PLN",
              default_cost_rate=Decimal("100.0000"), default_selling_rate=Decimal("200.0000"),
              cost_rate_unit="hour")
    alone = _fte(_cost(client, plan["project"].id, plan["scenario"].id))
    second = _position(db_session, plan["scenario"], second_dimensions, fte="0.5000")

    both = _cost(client, plan["project"].id, plan["scenario"].id)

    assert alone == ("calculated", "6499.35", "PLN")
    assert _fte(both) == ("calculated", "16249.35", "PLN")
    lines = {line["position_id"]: line["assigned_fte"]
             for line in both["assigned_fte_assumptions_used"]["lines"]}
    assert lines == {str(plan["position"].id): "0.3333", str(second.id): "0.5000"}
