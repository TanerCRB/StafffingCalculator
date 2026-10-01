"""SC-7-01, K-01/K-02/K-04/K-05 — a scenario's whole-life profit, margin and markup (F-10, Issue
#12), read through the real endpoint `GET …/results` by a caller for whom the personnel-cost gate
is **open** — `PERSONNEL_COSTS_READ` held (through `caller_holding`, the only way: the placeholder
does not grant it) **and** the `project_access` flag set on the scenario's project. The gate itself
is K-06's subject, in `tests/test_scenario_results_access.py`; here it must not be the reason a
figure is missing.

**Why the fixtures look the way they do.** Every scenario in this file is built from three already
proven components, composed rather than reimplemented:

- revenue: one position, one month, `billable_hours` × the selling rate (T&M, SC-4-01);
- base personnel cost: the same month's `planned_allocation_hours` × the cost rate (SC-5-01);
- paid-absence cost: a second, non-statutory absence type with `generates_cost=True`, booked for a
  controllable number of working days — never `0` by construction, so it is a real, perturbable
  third component and not a component this file leaves untested (SC-5-06). The catalogue's *one*
  `is_statutory_leave` row (a database-enforced singleton) is flagged with `generates_cost=False`
  once per test (`_ensure_statutory_bypass`) so the leave-budget top-up resolves to `0.00` without
  a budget row to build — a simplification of the fixture, not of the formula: the manual part
  still runs through the real capacity/day-count machinery;
- additional cost: one scenario-level, one-off cost (SC-5-05).

`AC-01`'s own numbers are the default fixture: 100 billable hours at PLN 200 (revenue 20000.00), 100
planned hours at PLN 120 (base cost 12000.00), no absence booked by default (paid-absence cost
0.00), an additional cost of PLN 2000.00 — `included_cost` 14000.00, `profit` 6000.00, `margin`
30.00, `markup` 42.86.
"""

import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models.catalog import AbsenceType
from app.models.exchange_rate import ExchangeRate
from app.models.project import Project
from app.models.scenario import Scenario
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    DimensionTuple,
    caller_holding,
    make_absence,
    make_absence_type,
    make_additional_cost,
    make_allocation,
    make_commercial_terms,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
)

MAR = date(2026, 3, 1)
COST_RATE = Decimal("120.0000")
SELLING_RATE = Decimal("200.0000")
PLANNED_HOURS = Decimal("100.00")
BILLABLE_HOURS = Decimal("100.00")
ADDITIONAL_AMOUNT = Decimal("2000.00")

EVERYTHING = frozenset(Permission)


def results_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/results"


def _ensure_statutory_bypass(session: Session, *, name: str = "Statutory (no cost)") -> AbsenceType:
    """Flag the catalogue's *one* `is_statutory_leave` row (a database-enforced singleton — at most
    one across the whole test transaction) with `generates_cost=False`.

    Every scenario built afterwards, in the same test, gets a paid-absence cost whose budget part
    resolves to `0.00` without any `absence_budget` row: `app.domain.paid_absence_cost` takes the
    `statutory_generates_cost is False` branch before it would otherwise need a resolved budget
    (`month_paid_absence_hours`). This is a simplification of *this fixture*, never of the formula:
    the manual part (a real, booked, cost-generating absence) still runs through the real
    day-counting machinery — see `_full_scenario`'s `absence_period`.

    Call this **once** per test, before building any scenario: a second call in the same test would
    violate the singleton and fail the database's own `EXCLUDE`/unique index.
    """
    return make_absence_type(
        session, name=name, generates_cost=False, generates_revenue=False, is_statutory_leave=True
    )


def _full_scenario(
    session: Session,
    *,
    name: str,
    cost_visible: bool = True,
    planned_hours: Decimal = PLANNED_HOURS,
    billable_hours: Decimal = BILLABLE_HOURS,
    additional_amount: Decimal = ADDITIONAL_AMOUNT,
    additional_currency: str = "PLN",
    absence_period: tuple[date, date] | None = None,
    mid_month_cost_change: bool = False,
    create_commercial_terms: bool = True,
    currency: str = "PLN",
    rate_currency: str | None = None,
) -> tuple[Project, Scenario, DimensionTuple]:
    """One scenario with all three components wired: a rate, a T&M rule, one additional cost — and,
    when `absence_period` is given, one booked, cost-generating absence (the paid-absence cost's
    controllable, perturbable part).

    `mid_month_cost_change=True` splits the catalogue rate into two windows over March, same
    selling rate (200), different cost rate (120 then 130): `month_is_priced` stays true (one
    selling rate) while `month_has_cost_rate` becomes false (two cost rates) — the one way to make
    the base cost unresolvable (`no_cost_rate`) while the revenue still resolves, needed by K-05.
    """
    project = make_project(
        session,
        name=name,
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,) if cost_visible else (),
    )
    scenario = make_scenario(session, project, name="Baseline", currency=currency)
    calendar = make_working_calendar(session, name=f"Calendar {name}")
    dimensions = make_dimension_tuple(session, suffix=f" {name}", calendar=calendar)
    position = make_staffing_position(session, scenario, dimensions, start_date=MAR)
    make_allocation(
        session,
        position,
        period_month=MAR,
        planned_allocation_hours=planned_hours,
        billable_hours=billable_hours,
    )
    if mid_month_cost_change:
        make_rate(
            session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 15),
            default_cost_rate=COST_RATE, default_selling_rate=SELLING_RATE,
            currency=rate_currency or currency,
        )
        make_rate(
            session, dimensions, effective_from=date(2026, 3, 16),
            default_cost_rate=Decimal("130.0000"), default_selling_rate=SELLING_RATE,
            currency=rate_currency or currency,
        )
    else:
        make_rate(
            session, dimensions, effective_from=date(2026, 1, 1),
            default_cost_rate=COST_RATE, default_selling_rate=SELLING_RATE,
            currency=rate_currency or currency,
        )
    if create_commercial_terms:
        make_commercial_terms(session, scenario)
    category = make_cost_category(session, name=f"Licences {name}")
    make_additional_cost(
        session, scenario, category, amount=additional_amount, start_month=MAR,
        currency=additional_currency,
    )
    if absence_period is not None:
        holiday = make_absence_type(
            session, name=f"Holiday {name}", generates_cost=True, generates_revenue=False,
        )
        make_absence(
            session, position, holiday, start_date=absence_period[0], end_date=absence_period[1]
        )
    return project, scenario, position


def _results(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    """The result as a caller with both gates open reads it — every permission, flag set by
    `_full_scenario` (`cost_visible=True` by default)."""
    with caller_holding(*EVERYTHING):
        response = client.get(results_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()


# --- K-01: AC-01 arithmetic, on a live call, catching a margin/markup argument swap ---------------


def test_k_01_ac_01_arithmetic_on_a_live_call_catches_a_margin_markup_argument_swap(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — AC-01's own numbers, read through the real endpoint.

    `margin` (30.00) and `markup` (42.86) are two *different* values computed from the same
    `profit`: a test that checked only one of them would not catch `ratio_percent(profit, revenue)`
    and `ratio_percent(profit, included_cost)` swapped between the two fields. Contrast: raising the
    additional cost from 2000 to 5000 answers differently (a different `included_cost`, `profit`
    and `margin`), so the first result is not a hard-coded default.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="Aurora")

    body = _results(client, project.id, scenario.id)

    assert body["revenue"]["amount"] == "20000.00"
    assert body["personnel_cost"]["amount"] == "12000.00"
    assert body["personnel_cost"]["paid_absence_amount"] == "0.00"
    assert body["additional_cost"]["amount"] == "2000.00"
    assert body["included_cost"] == "14000.00"
    assert body["profit"] == "6000.00"
    assert body["margin"] == "30.00"
    assert body["markup"] == "42.86"
    assert body["margin"] != body["markup"]

    project2, scenario2, _ = _full_scenario(
        db_session, name="Borealis", additional_amount=Decimal("5000.00")
    )
    body2 = _results(client, project2.id, scenario2.id)
    assert body2["included_cost"] == "17000.00"
    assert body2["profit"] == "3000.00"
    assert body2["margin"] == "15.00"


def test_exchange_rate_composes_revenue_personnel_and_additional_cost_in_scenario_currency(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, dimensions = _full_scenario(
        db_session,
        name="Converted",
        currency="USD",
        rate_currency="PLN",
        additional_currency="PLN",
        absence_period=(date(2026, 3, 2), date(2026, 3, 4)),
    )
    scenario.start_date = MAR
    make_staffing_position(
        db_session,
        scenario,
        dimensions,
        headcount=1,
        cost_basis="fixed_amount",
        fixed_amount=Decimal("1000.00"),
        fixed_amount_currency="PLN",
    )
    fte_position = make_staffing_position(
        db_session,
        scenario,
        dimensions,
        cost_basis="assigned_fte",
        assigned_fte=Decimal("1.00"),
    )
    make_allocation(
        db_session,
        fte_position,
        period_month=MAR,
        planned_allocation_hours=Decimal("100.00"),
        billable_hours=Decimal("0.00"),
    )
    rate = ExchangeRate(
        source_currency="PLN",
        target_currency="USD",
        effective_from=MAR,
        effective_to=date(2026, 3, 31),
        rate=Decimal("0.5000000000"),
        source="Test rate",
    )
    db_session.add(rate)
    db_session.flush()

    body = _results(client, project.id, scenario.id)

    assert body["revenue"]["amount"] == "10000.00", body["revenue"]
    assert body["revenue"]["currency"] == "USD"
    assert body["personnel_cost"]["amount"] == "6000.00"
    assert body["personnel_cost"]["currency"] == "USD"
    assert body["personnel_cost"]["paid_absence_amount"] == "1350.00"
    assert body["personnel_cost"]["fully_loaded_amount"] == "6000.00"
    assert body["personnel_cost"]["paid_absence_fully_loaded_amount"] == "1350.00"
    assert body["personnel_cost"]["fixed_amount_amount"] == "500.00"
    assert body["personnel_cost"]["fixed_amount_currency"] == "USD"
    assert body["personnel_cost"]["assigned_fte_amount"] == "9900.00"
    assert body["personnel_cost"]["assigned_fte_currency"] == "USD"
    assert body["additional_cost"]["amount"] == "1000.00"
    assert body["additional_cost"]["currency"] == "USD"
    assert body["included_cost"] == "8350.00"
    assert body["profit"] == "1650.00"

    with caller_holding(*EVERYTHING):
        approved = client.post(f"/projects/{project.id}/scenarios/{scenario.id}/approve")
    assert approved.status_code == 200, approved.text
    rate.rate = Decimal("0.8000000000")
    db_session.flush()

    frozen = _results(client, project.id, scenario.id)
    assert frozen["revenue"]["amount"] == "10000.00"
    assert frozen["personnel_cost"]["amount"] == "6000.00"
    assert frozen["additional_cost"]["amount"] == "1000.00"
    assert frozen["profit"] == "1650.00"


def test_no_scenario_currency_does_not_sum_mixed_currency_additional_costs(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="No target currency")
    scenario.currency = None
    category = make_cost_category(db_session, name="Foreign expense")
    make_additional_cost(
        db_session,
        scenario,
        category,
        amount=Decimal("100.00"),
        start_month=MAR,
        currency="USD",
    )

    body = _results(client, project.id, scenario.id)

    assert body["additional_cost"]["state"] == "currency_mismatch"
    assert body["profit"] == "n/a"
    assert body["profitability_state"] == "not_applicable"


# --- K-02: zero revenue is margin "n/a", profit and markup still numeric --------------------------


def test_k_02_zero_revenue_leaves_margin_not_applicable_but_profit_and_markup_numeric(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — proof only through the live endpoint (a unit test of `ratio_percent` proves nothing
    about this endpoint's wiring). `billable_hours=0.00` makes the revenue a **stated** `0.00`
    (`state == "calculated"`), not a named unresolvable state — the distinction AC-05 is about.

    Mutation this kills: `ratio_percent(profit, revenue)` replaced by a bare `profit / revenue *
    100` at the point this endpoint assembles its answer — with `revenue == 0` that raises, and the
    call above would get a `500` instead of `200` with `"n/a"`.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session, name="ZeroRevenue", billable_hours=Decimal("0.00")
    )

    body = _results(client, project.id, scenario.id)

    assert body["revenue"]["state"] == "calculated"
    assert body["revenue"]["amount"] == "0.00"
    assert body["margin"] == "n/a"
    assert body["included_cost"] == "14000.00"
    assert body["profit"] == "-14000.00"
    assert body["markup"] == "-100.00"

    project2, scenario2, _ = _full_scenario(db_session, name="NonZeroRevenue")
    body2 = _results(client, project2.id, scenario2.id)
    assert body2["margin"] == "30.00"


# --- K-04: perturb one component (or two), profit moves by exactly -delta -------------------------


def test_k_04_perturbing_the_base_personnel_cost_moves_profit_by_exactly_minus_delta(
    client: TestClient, db_session: Session
) -> None:
    """K-04 (a) — 10 extra planned hours at 120/h is +1200.00 of base cost. Mutation killed: the
    base cost added twice (profit would move by -2400.00, not -1200.00)."""
    _ensure_statutory_bypass(db_session)
    base_project, base_scenario, _ = _full_scenario(db_session, name="BaseA")
    more_project, more_scenario, _ = _full_scenario(
        db_session, name="BaseAPlus", planned_hours=Decimal("110.00")
    )

    base = _results(client, base_project.id, base_scenario.id)
    more = _results(client, more_project.id, more_scenario.id)

    assert more["revenue"]["amount"] == base["revenue"]["amount"]
    assert more["additional_cost"]["amount"] == base["additional_cost"]["amount"]
    assert Decimal(more["personnel_cost"]["amount"]) - Decimal(
        base["personnel_cost"]["amount"]
    ) == Decimal("1200.00")
    assert Decimal(more["profit"]) - Decimal(base["profit"]) == Decimal("-1200.00")


def test_k_04_perturbing_the_paid_absence_cost_moves_profit_by_exactly_minus_delta(
    client: TestClient, db_session: Session
) -> None:
    """K-04 (b) — one extra working day of a cost-generating absence is +900.00 (7.50h × 120).
    Mutation killed: `paid_absence_amount` left out of `included_cost` (profit would not move at
    all)."""
    _ensure_statutory_bypass(db_session)
    base_project, base_scenario, _ = _full_scenario(
        db_session, name="AbsenceA", absence_period=(date(2026, 3, 2), date(2026, 3, 2))
    )
    more_project, more_scenario, _ = _full_scenario(
        db_session, name="AbsenceAPlus", absence_period=(date(2026, 3, 2), date(2026, 3, 3))
    )

    base = _results(client, base_project.id, base_scenario.id)
    more = _results(client, more_project.id, more_scenario.id)

    assert more["revenue"]["amount"] == base["revenue"]["amount"]
    assert more["personnel_cost"]["amount"] == base["personnel_cost"]["amount"]
    assert more["additional_cost"]["amount"] == base["additional_cost"]["amount"]
    assert Decimal(more["personnel_cost"]["paid_absence_amount"]) - Decimal(
        base["personnel_cost"]["paid_absence_amount"]
    ) == Decimal("900.00")
    assert Decimal(more["profit"]) - Decimal(base["profit"]) == Decimal("-900.00")


def test_k_04_perturbing_the_additional_cost_moves_profit_by_exactly_minus_delta(
    client: TestClient, db_session: Session
) -> None:
    """K-04 (c) — +1000.00 of additional cost. Mutation killed: the additional cost left out of
    `included_cost` (profit would not move at all)."""
    _ensure_statutory_bypass(db_session)
    base_project, base_scenario, _ = _full_scenario(db_session, name="ExtraA")
    more_project, more_scenario, _ = _full_scenario(
        db_session, name="ExtraAPlus", additional_amount=Decimal("3000.00")
    )

    base = _results(client, base_project.id, base_scenario.id)
    more = _results(client, more_project.id, more_scenario.id)

    assert more["revenue"]["amount"] == base["revenue"]["amount"]
    assert more["personnel_cost"]["amount"] == base["personnel_cost"]["amount"]
    assert Decimal(more["additional_cost"]["amount"]) - Decimal(
        base["additional_cost"]["amount"]
    ) == Decimal("1000.00")
    assert Decimal(more["profit"]) - Decimal(base["profit"]) == Decimal("-1000.00")


def test_k_04_perturbing_two_components_at_once_moves_profit_by_minus_the_sum_of_both_deltas(
    client: TestClient, db_session: Session
) -> None:
    """K-04, linearity — base cost +1200.00 *and* additional cost +1000.00 at once: profit moves by
    exactly -2200.00, never -1200.00, -1000.00 or -2400.00 (a formula that only ever applied one of
    the two deltas, or double-counted one of them, fails this one even if it passed the two single-
    component tests above by coincidence)."""
    _ensure_statutory_bypass(db_session)
    base_project, base_scenario, _ = _full_scenario(db_session, name="ComboA")
    more_project, more_scenario, _ = _full_scenario(
        db_session, name="ComboAPlus", planned_hours=Decimal("110.00"),
        additional_amount=Decimal("3000.00"),
    )

    base = _results(client, base_project.id, base_scenario.id)
    more = _results(client, more_project.id, more_scenario.id)

    assert more["revenue"]["amount"] == base["revenue"]["amount"]
    assert Decimal(more["profit"]) - Decimal(base["profit"]) == Decimal("-2200.00")


# --- K-05: a named unresolvable source withholds the aggregate and names itself -------------------


@pytest.mark.parametrize(
    ("break_component", "expected_reason"),
    [
        ("revenue", "no_commercial_terms"),
        ("base_cost", "no_cost_rate"),
            ("additional_cost", "missing_exchange_rate"),
    ],
)
def test_k_05_one_named_unresolvable_source_withholds_the_aggregate_and_names_only_itself(
    client: TestClient, db_session: Session, break_component: str, expected_reason: str
) -> None:
    """K-05 — one source broken at a time, the other two still `calculated`, the aggregate withheld
    as `"n/a"` on all four fields and the broken source named by its own, unmodified `state`.

    Mutation killed: collapsing the four named reasons into one shared sentinel — this test's three
    parametrisations would then be unable to tell "revenue is missing its rule" from "the cost rate
    changed mid-month" from "the additional cost is in the wrong currency", because a shared
    sentinel carries none of that by construction.
    """
    _ensure_statutory_bypass(db_session)
    kwargs: dict[str, Any] = {"name": f"Broken {break_component}"}
    if break_component == "revenue":
        kwargs["create_commercial_terms"] = False
    elif break_component == "base_cost":
        kwargs["mid_month_cost_change"] = True
    else:
        kwargs["additional_currency"] = "USD"
    project, scenario, _ = _full_scenario(db_session, **kwargs)

    body = _results(client, project.id, scenario.id)

    assert body["profit"] == "n/a"
    assert body["margin"] == "n/a"
    assert body["markup"] == "n/a"
    assert body["included_cost"] == "n/a"

    if break_component == "revenue":
        assert body["revenue"]["state"] == expected_reason
        assert body["personnel_cost"]["state"] == "calculated"
        assert body["additional_cost"]["state"] == "calculated"
    elif break_component == "base_cost":
        assert body["personnel_cost"]["state"] == expected_reason
        assert body["revenue"]["state"] == "calculated"
        assert body["additional_cost"]["state"] == "calculated"
    else:
        assert body["additional_cost"]["state"] == expected_reason
        assert body["revenue"]["state"] == "calculated"
        assert body["personnel_cost"]["state"] == "calculated"


def test_k_05_the_same_scenario_with_its_source_resolved_answers_a_real_number(
    client: TestClient, db_session: Session
) -> None:
    """K-05's contrast — the same shape of scenario, its commercial terms present, answers a real
    `profit`: the `"n/a"` above is about the missing rule, not about the endpoint being broken."""
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="Resolved", create_commercial_terms=True)

    body = _results(client, project.id, scenario.id)

    assert body["profit"] == "6000.00"


# --- structural: the composition layer, and only it, sits above all three ------------------------


def _imports_of(relative_path: str) -> set[str]:
    """Every module a source file imports, from its syntax tree — the pattern `tests/
    test_personnel_cost.py` and `tests/test_additional_cost.py` already use, copied rather than
    imported: each file keeps its own so one file's proof does not depend on another's helper."""
    tree = ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _source_of(module: str) -> str | None:
    """The file of an `app.*` module, relative to the backend root — or `None` for a package or a
    module outside `app`."""
    path = Path(BACKEND_ROOT, *module.split(".")).with_suffix(".py")
    if path.is_file():
        return path.relative_to(BACKEND_ROOT).as_posix()
    package = Path(BACKEND_ROOT, *module.split("."), "__init__.py")
    return package.relative_to(BACKEND_ROOT).as_posix() if package.is_file() else None


def _reachable_from(module: str) -> set[str]:
    reached: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        source = _source_of(current)
        if source is None:
            continue
        pending.extend(name for name in _imports_of(source) if name.startswith("app."))
    return reached - {module}


REVENUE_MODULES = {
    "app.data.commercial_terms", "app.domain.revenue", "app.domain.revenue_time_and_material",
}
COST_MODULES = {"app.data.personnel_cost", "app.domain.personnel_cost"}
ADDITIONAL_COST_MODULES = {"app.data.additional_cost", "app.domain.additional_cost"}
RESULTS_MODULES = {"app.data.scenario_results", "app.domain.scenario_results"}


def test_k_struct_the_composition_layer_is_the_only_one_that_imports_all_three() -> None:
    """ADR-0004 "fits" (confirmed at gate 1 for SC-7-01), control analogous to
    `test_personnel_cost.py::test_c5_rate_windows_shares_geometry_never_a_rate_column` — extended to
    the new composition layer instead of the shared geometry module.

    1. None of the three original modules (revenue, base cost, additional cost) imports either new
       module, directly or through any chain (rule 10 of the Invariant Guardian: the composition
       layer sits *above*, and nothing below may reach up into it).
    2. The contrast: the new composition layer **does** reach into all three, at some depth — so
       this test cannot pass by reading two files that import nothing.

    Mutation killed: a shortcut added to any of the three modules that imports
    `app.data.scenario_results` or `app.domain.scenario_results` (which would let a lower layer
    depend on the layer built on top of it), or the composition layer stopping calling one of the
    three (a "fifth path" that reads the scenario or its snapshot again).
    """
    for module in sorted(REVENUE_MODULES | COST_MODULES | ADDITIONAL_COST_MODULES):
        reachable = _reachable_from(module)
        crossing = reachable & RESULTS_MODULES
        assert crossing == set(), f"{module} reaches {sorted(crossing)}"

    # The contrast, as a union rather than per-module: `app.data.scenario_results` reaches every
    # data and domain module of the three (it calls their `*_for_caller` functions, which import
    # their own domain modules in turn); `app.domain.scenario_results` reaches only the three
    # *domain* answer types it dispatches on, never a data module (a domain module importing a data
    # module would be its own, separate defect). Together the two reach all seven — proof this test
    # cannot pass by reading two files that import nothing.
    reached_by_either = _reachable_from("app.data.scenario_results") | _reachable_from(
        "app.domain.scenario_results"
    )
    expected = REVENUE_MODULES | COST_MODULES | ADDITIONAL_COST_MODULES
    assert expected <= reached_by_either, sorted(expected - reached_by_either)
