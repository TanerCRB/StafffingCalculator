"""SC-5-04, FA-2/FA-3/FA-5/FA-6/FA-11 — the assigned-FTE cost formula, as a pure function, and the
structure of the modules around it (F-07; Issue #79; ADR-0013, addendum 2026-09-29 SC-5-04).

No database here: `app.domain.assigned_fte_cost` is handed values, never a `Session`. The scenario-
level proof (real PostgreSQL, real endpoints, the dispatch, the approval) is
`tests/test_assigned_fte_cost_scenario.py`. **Every expected figure is a literal worked out by hand
in the comment beside it**, never derived from the code under test.

The fixture calendar is 7.50 h/day, Monday to Saturday. March 2026 begins on a Sunday, so it has
31 - 5 = 26 working days and **195 basis hours** (April 2026 has 30 - 4 = 26 as well). Two
properties make it the right fixture: 7.50 is not 8 (a hard-coded 8-hour day is visible), and
Saturday is a working day (`weekday() < 5` is visible). With an FTE of 0.3333:

    hours = 0.3333 x 26 x 7.5 = 64.9935 exactly

and at 100 per hour the figure is **6499.35**, while the chained computation "round the hours to two
places first" gives 64.99 x 100 = **6499.00** — the cent that FA-3 exists to protect.
"""

import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.money import NOT_APPLICABLE
from app.domain.assigned_fte_cost import (
    AssignedFteCostResult,
    AssignedFteCostUnavailable,
    AssignedFteLine,
    AssignedFteMonth,
    assigned_fte_cost,
)
from app.domain.capacity import CalendarBasis
from app.domain.fte_hours import exact_fte_hours, fte_to_hours
from app.domain.personnel_cost import CostRateWindow, MonthCostRate
from app.models.catalog import WorkingCalendarDayKind
from tests.conftest import BACKEND_ROOT

MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
FTE = Decimal("0.3333")


def _basis(
    *, hours: str = "7.50", pattern: str = "1111110", exceptional: dict | None = None
) -> CalendarBasis:
    return CalendarBasis(
        calendar_id=uuid.uuid4(),
        name="Saturday calendar",
        standard_hours_per_day=Decimal(hours),
        week_pattern=pattern,
        exceptional_days=exceptional or {},
    )


def _rate(
    amount: str = "100.0000", *, unit: str = "hour", currency: str = "PLN"
) -> MonthCostRate:
    return MonthCostRate(
        cost_rate=Decimal(amount),
        currency=currency,
        windows=(
            CostRateWindow(
                source_rate_id=uuid.uuid4(),
                effective_from=date(2026, 1, 1),
                effective_to=None,
                cost_rate=Decimal(amount),
                currency=currency,
                surcharge_percent=Decimal("0"),
                includes_surcharge=False,
                cost_rate_unit=unit,
            ),
        ),
        surcharge_percent=Decimal("0"),
        includes_surcharge=False,
        cost_rate_unit=unit,
    )


def _cost(
    fte: str,
    months: list[tuple[date, MonthCostRate | None, CalendarBasis | None]],
    *,
    scenario_currency: str | None = "PLN",
    extra_lines: tuple[AssignedFteLine, ...] = (),
):
    position_id = uuid.uuid4()
    lines = (AssignedFteLine(position_id=position_id, assigned_fte=Decimal(fte)), *extra_lines)
    return assigned_fte_cost(
        lines,
        [
            AssignedFteMonth(position_id=position_id, period_month=month, rate=rate, basis=basis)
            for month, rate, basis in months
        ],
        rate_source="live_catalog",
        scenario_currency=scenario_currency,
    )


def _calculated(answer) -> Decimal:
    assert isinstance(answer, AssignedFteCostResult), answer
    return answer.cost


# --- FA-2: hour, day and month, on a calendar that is neither 8 h nor Monday-Friday ---------------


@pytest.mark.parametrize(
    ("unit", "rate", "expected"),
    [
        # hour:  0.3333 x 26 x 7.5 x 100   = 6499.35
        pytest.param("hour", "100.0000", "6499.35", id="hour"),
        # day:   0.3333 x 26 x 1000        = 8665.80   (S cancels: only D is visible here)
        pytest.param("day", "1000.0000", "8665.80", id="day"),
        # month: 0.3333 x 10000            = 3333.00   (D and S both cancel)
        pytest.param("month", "10000.0000", "3333.00", id="month"),
    ],
)
def test_fa_2_the_three_units_price_the_exact_fte_basis(
    unit: str, rate: str, expected: str
) -> None:
    """FA-2 — `hour` = fte x D x S x rate, `day` = fte x D x rate, `month` = fte x rate.

    Mutations killed: unit ignored (all three would read 6499.35 x rate/100), `day` and `month`
    swapped, a constant 8-hour day in the hour case (6932.64), `weekday() < 5` (22 days: 5499.45),
    the FTE rounded to hours first (6499.00 in the hour case)."""
    answer = _cost(str(FTE), [(MAR, _rate(rate, unit=unit), _basis())])

    assert _calculated(answer) == Decimal(expected)


def test_fa_2_the_hour_figure_is_where_a_wrong_day_count_or_day_length_is_visible() -> None:
    """FA-2, the unit-cancellation trap: at a `month` rate D and S cancel, at a `day` rate S
    cancels, so a wrong `S` is *only* visible at an `hour` rate. Three calendars, one FTE, one hour
    rate: the figure moves with the calendar's own hours and its own week — and 8 hours / Mon-Fri
    would have given a different number in each row.

      7.50 h, Mon-Sat (26 days) -> 0.3333 x 26 x 7.5 x 100 = 6499.35
      7.50 h, Mon-Fri (22 days) -> 0.3333 x 22 x 7.5 x 100 = 5499.45
      6.00 h, Mon-Sat (26 days) -> 0.3333 x 26 x 6   x 100 = 5199.48
    """
    figures = {
        (hours, pattern): _calculated(
            _cost(str(FTE), [(MAR, _rate("100.0000"), _basis(hours=hours, pattern=pattern))])
        )
        for hours, pattern in (("7.50", "1111110"), ("7.50", "1111100"), ("6.00", "1111110"))
    }

    assert figures == {
        ("7.50", "1111110"): Decimal("6499.35"),
        ("7.50", "1111100"): Decimal("5499.45"),
        ("6.00", "1111110"): Decimal("5199.48"),
    }


def test_fa_2_an_exceptional_holiday_removes_a_working_day_from_the_basis() -> None:
    """FA-2 — the days of the calendar are data: one Tuesday marked `NON_WORKING` leaves 25 days,
    0.3333 x 25 x 7.5 x 100 = 6249.375 -> 6249.38 (half up). Mutation: the exceptional days ignored
    (26 days, 6499.35)."""
    holiday = {date(2026, 3, 10): WorkingCalendarDayKind.NON_WORKING}

    answer = _cost(str(FTE), [(MAR, _rate("100.0000"), _basis(exceptional=holiday))])

    assert _calculated(answer) == Decimal("6249.38")


def test_fa_2_no_headcount_factor_and_a_value_above_the_headcount_is_priced_as_it_is() -> None:
    """FA-2/point 2 — the stored FTE is a position total. The formula has no headcount input at all
    (its line type carries none), and 2.5 FTE is priced as 2.5 x 26 x 7.5 x 100 = 48750.00, more
    than any single position's capacity — never clamped and never flagged (named limitation,
    ADR-0013 SC-5-04 point 2)."""
    assert set(AssignedFteLine.__dataclass_fields__) == {"position_id", "assigned_fte"}

    answer = _cost("2.5000", [(MAR, _rate("100.0000"), _basis())])

    assert _calculated(answer) == Decimal("48750.00")


# --- FA-3: the exact product, one rounding --------------------------------------------------------


def test_fa_3_the_unrounded_basis_hours_are_the_input_and_fte_to_hours_is_unchanged() -> None:
    """FA-3 — the cent. `exact_fte_hours` is 64.9935 (no rounding), `fte_to_hours` still answers
    64.99 (FTE-5, untouched), and the cost is 6499.35 — not 6499.00, which is what feeding the
    rounded hours into the price would give. Mutation: the cost built on
    `fte_to_hours(...).value`."""
    basis = _basis()

    assert exact_fte_hours(basis, period_month=MAR, fte_fraction=FTE) == Decimal("64.9935")
    assert fte_to_hours(basis, period_month=MAR, fte_fraction=FTE).value == Decimal("64.99")
    assert _calculated(_cost(str(FTE), [(MAR, _rate("100.0000"), _basis())])) == Decimal("6499.35")
    assert Decimal("64.99") * Decimal("100") == Decimal("6499.00")  # the chained figure, refused


def test_fa_3_the_exact_hours_name_a_state_never_a_number_when_they_cannot_be_stated() -> None:
    """`exact_fte_hours` — the states of `fte_to_hours` in the same words, decided before any
    multiplication: no calendar, and a calendar with no working day in the month. Contrast: a
    calendar that works only on Sundays still has five working days in March 2026."""
    assert exact_fte_hours(None, period_month=MAR, fte_fraction=FTE) == "no_calendar"
    never = _basis(pattern="0000000")
    assert exact_fte_hours(never, period_month=MAR, fte_fraction=FTE) == "no_working_days"
    sundays_only = _basis(pattern="0000001")
    assert exact_fte_hours(sundays_only, period_month=MAR, fte_fraction=FTE) == (
        Decimal("5") * Decimal("7.50") * FTE
    )


@pytest.mark.parametrize(
    ("bad", "error"),
    [
        pytest.param(Decimal("-0.0001"), ValueError, id="negative"),
        pytest.param(Decimal("NaN"), ValueError, id="nan"),
        pytest.param(Decimal("Infinity"), ValueError, id="infinite"),
        pytest.param(Decimal("1e30"), ValueError, id="beyond-the-bound"),
        pytest.param(0.5, TypeError, id="float"),
        pytest.param("0.5", TypeError, id="string"),
    ],
)
def test_qa_exact_fte_hours_validates_its_fraction_like_fte_to_hours_before_any_state(
    bad: object, error: type[Exception]
) -> None:
    """QA (FA-3) — `exact_fte_hours` states that its fraction is "validated like there". Nothing
    checked it: a negative FTE would price as negative money, a float would leak binary noise into
    a cent. Asked with a **missing calendar** on purpose — the validation must come *before* the
    named state, so an invalid value is never answered with `no_calendar`. Contrast: `0` is accepted
    (a zero FTE is a value, not an error), and `fte_to_hours` refuses the same inputs the same way.
    Mutation: `_require_amount(...)` removed from `exact_fte_hours`."""
    basis = _basis()
    with pytest.raises(error):
        exact_fte_hours(None, period_month=MAR, fte_fraction=bad)  # type: ignore[arg-type]
    with pytest.raises(error):
        exact_fte_hours(basis, period_month=MAR, fte_fraction=bad)  # type: ignore[arg-type]
    with pytest.raises(error):
        fte_to_hours(basis, period_month=MAR, fte_fraction=bad)  # type: ignore[arg-type]
    assert exact_fte_hours(basis, period_month=MAR, fte_fraction=Decimal("0")) == Decimal("0")


def test_fa_3_months_are_summed_unrounded_and_rounded_once_at_the_end() -> None:
    """FA-3 — 0.1 FTE at 0.03 per hour, March and April (26 days x 7.5 h = 195 h each): each month
    is 19.5 h x 0.03 = 0.585 exactly. The sum is 1.170 -> **1.17**. Rounding each month first gives
    0.59 + 0.59 = **1.18**. Mutation: `round_money` inside the month loop."""
    basis = _basis()
    answer = _cost(
        "0.1000",
        [(MAR, _rate("0.0300"), basis), (APR, _rate("0.0300"), basis)],
    )

    assert _calculated(answer) == Decimal("1.17")


# --- FA-5/FA-6: the named states, their order, and "never 0" --------------------------------------


def _state(answer) -> str:
    assert isinstance(answer, AssignedFteCostUnavailable), answer
    return answer.reason


def test_fa_5_a_position_without_allocation_rows_is_no_planned_months_never_zero() -> None:
    """FA-5 — a stored FTE and no month to price it in. Contrast: the same position with one month
    is calculated. Mutation: an empty month list summed to `0.00`."""
    without = _cost("0.5000", [])
    assert _state(without) == "no_planned_months"
    assert not hasattr(without, "cost")  # no amount on this shape at all

    with_month = _cost("0.5000", [(MAR, _rate("100.0000"), _basis())])
    assert _calculated(with_month) == Decimal("9750.00")  # 0.5 x 26 x 7.5 x 100


def test_fa_5_no_planned_months_of_one_position_withholds_the_whole_component() -> None:
    """FA-5/FA-6 — no partial sum: position B has months, position A has none, and the component is
    the named state, not B's figure."""
    other = uuid.uuid4()
    answer = assigned_fte_cost(
        [
            AssignedFteLine(position_id=uuid.uuid4(), assigned_fte=Decimal("1.0000")),
            AssignedFteLine(position_id=other, assigned_fte=Decimal("1.0000")),
        ],
        [AssignedFteMonth(position_id=other, period_month=MAR, rate=_rate(), basis=_basis())],
        rate_source="live_catalog",
        scenario_currency="PLN",
    )

    assert _state(answer) == "no_planned_months"


def test_fa_6_an_hourly_fte_position_needs_a_calendar() -> None:
    """FA-6 — at an `hour` rate `priced_amount` would not read the calendar; the hours of an FTE
    position come from it, so a missing calendar is `no_calendar` whatever the unit. Mutation: the
    calendar demanded only for `day`/`month` units (the worked-time basis's rule) would price this
    position with 0 hours or crash."""
    for unit in ("hour", "day", "month"):
        answer = _cost("0.5000", [(MAR, _rate("100.0000", unit=unit), None)])
        assert _state(answer) == "no_calendar", unit


def test_fa_6_a_month_without_working_days_is_no_working_days_for_every_unit() -> None:
    """FA-6 — D = 0: the hours of the month are zero and the state is decided before any division.
    A `day` rate would need no D in the worked-time basis (SC-5-08 Q-D); here every unit needs the
    calendar's month, so all three are `no_working_days`."""
    closed = _basis(pattern="0000000")
    for unit in ("hour", "day", "month"):
        answer = _cost("0.5000", [(MAR, _rate("100.0000", unit=unit), closed)])
        assert _state(answer) == "no_working_days", unit


def test_fa_6_the_order_of_named_states() -> None:
    """FA-6 (point 5) — `no_cost_rate`, `currency_mismatch`, `no_calendar`, `no_working_days`,
    `no_planned_months`, then `no_cost_currency`. Each pair below has both members true and the
    earlier one wins; each state is also shown alone, so the pairs prove the order and not merely
    that the states exist."""
    closed = _basis(pattern="0000000")
    a, b = uuid.uuid4(), uuid.uuid4()

    def component(months: list[AssignedFteMonth], lines: list[uuid.UUID], currency="PLN") -> str:
        return _state(
            assigned_fte_cost(
                [AssignedFteLine(position_id=p, assigned_fte=Decimal("1.0000")) for p in lines],
                months,
                rate_source="live_catalog",
                scenario_currency=currency,
            )
        )

    def month(position: uuid.UUID, *, rate, basis) -> AssignedFteMonth:
        return AssignedFteMonth(position_id=position, period_month=MAR, rate=rate, basis=basis)

    unresolved = month(a, rate=None, basis=_basis())
    euro_without_calendar = month(b, rate=_rate(currency="EUR"), basis=None)
    no_calendar = month(a, rate=_rate(), basis=None)
    no_days = month(b, rate=_rate(), basis=closed)

    assert component([unresolved], [a]) == "no_cost_rate"
    assert component([unresolved, euro_without_calendar], [a, b]) == "no_cost_rate"
    assert component([euro_without_calendar], [b]) == "currency_mismatch"
    assert component([no_calendar], [a]) == "no_calendar"
    assert component([no_calendar, no_days], [a, b]) == "no_calendar"
    assert component([no_days], [b]) == "no_working_days"
    assert component([no_days], [b, uuid.uuid4()]) == "no_working_days"
    assert component([], [a], currency=None) == "no_planned_months"
    assert component([], [], currency=None) == "no_cost_currency"


def test_fa_6_no_fte_position_is_zero_in_the_scenarios_currency_or_no_cost_currency() -> None:
    """Point 5, the empty component: `0.00` of a declared currency, a named state without one — the
    rule of the other components, and `0.00` of nothing is refused."""
    empty = assigned_fte_cost([], [], rate_source="live_catalog", scenario_currency="EUR")
    assert (_calculated(empty), empty.currency) == (Decimal("0.00"), "EUR")

    undeclared = assigned_fte_cost([], [], rate_source="live_catalog", scenario_currency=None)
    assert _state(undeclared) == "no_cost_currency"
    assert not hasattr(undeclared, "cost")
    assert NOT_APPLICABLE == "n/a"


def test_fa_6_currency_mismatch_between_months_and_against_the_scenario() -> None:
    """FA-6 — two currencies among the months, or one that is not the scenario's:
    `currency_mismatch`. Contrast: one matching currency is calculated."""
    basis = _basis()
    two = _cost(
        "0.5000",
        [(MAR, _rate(currency="PLN"), basis), (APR, _rate(currency="EUR"), basis)],
    )
    assert _state(two) == "currency_mismatch"
    foreign = _cost("0.5000", [(MAR, _rate(currency="EUR"), basis)], scenario_currency="PLN")
    assert _state(foreign) == "currency_mismatch"
    assert _calculated(_cost("0.5000", [(MAR, _rate(), basis)])) == Decimal("9750.00")


# --- FA-11: structure — independence from revenue and from the other two formulas -----------------

FORMULA = "app/domain/assigned_fte_cost.py"
REVENUE_MODULES = {
    "app.data.commercial_terms",
    "app.domain.revenue",
    "app.domain.revenue_time_and_material",
    "app.domain.revenue_story_points",
    "app.domain.revenue_fixed_price",
    "app.domain.revenue_outcome_based",
}


def _tree(relative_path: str) -> ast.Module:
    return ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))


def _imports_of(relative_path: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree(relative_path)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _module_file(module: str) -> Path | None:
    path = Path(BACKEND_ROOT, *module.split(".")).with_suffix(".py")
    return path if path.is_file() else None


def _reachable_from(module: str) -> set[str]:
    seen: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        path = _module_file(current)
        if path is None:
            continue
        for imported in _imports_of(path.relative_to(BACKEND_ROOT).as_posix()):
            if imported.startswith("app.") and imported not in seen:
                seen.add(imported)
                pending.append(imported)
    return seen


def test_fa_11_the_fte_formula_imports_no_revenue_module_and_neither_other_formula() -> None:
    """FA-11 (mirror of C-5 and of U-7) — the FTE formula module:

    - reaches no revenue module, directly or through any chain of imports;
    - imports neither other formula: not `app.domain.fixed_amount_cost`, no `app.data.*` module, and
      from `app.domain.personnel_cost` only the shared pricing primitives ADR-0013 names as the one
      place the unit formulas live (`priced_amount`, SC-5-08 point 6, and the resolved-rate value
      types) — never `base_personnel_cost` or `fully_loaded_personnel_cost`;
    - is imported by neither of the other two formula modules, nor by a revenue module.

    The contrast, so this cannot pass by reading files that import nothing: the formula does import
    the shared primitives and the FTE conversion, and the dispatcher imports all three formulas.
    """
    direct = _imports_of(FORMULA)
    assert not (_reachable_from("app.domain.assigned_fte_cost") & REVENUE_MODULES)
    assert "app.domain.fixed_amount_cost" not in direct
    assert not {name for name in direct if name.startswith("app.data")}
    borrowed = {
        alias.name
        for node in ast.walk(_tree(FORMULA))
        if isinstance(node, ast.ImportFrom) and node.module == "app.domain.personnel_cost"
        for alias in node.names
    }
    assert borrowed == {"CostRateWindow", "MonthCostRate", "priced_amount"}

    for other in ("app/domain/personnel_cost.py", "app/domain/fixed_amount_cost.py"):
        assert "app.domain.assigned_fte_cost" not in _imports_of(other), other
    for module in REVENUE_MODULES:
        path = _module_file(module)
        if path is not None:
            assert "app.domain.assigned_fte_cost" not in _reachable_from(module), module

    assert {"app.domain.fte_hours", "app.domain.personnel_cost"} <= direct
    dispatcher = _imports_of("app/data/personnel_cost.py")
    assert {
        "app.domain.personnel_cost",
        "app.domain.fixed_amount_cost",
        "app.domain.assigned_fte_cost",
    } <= dispatcher


def test_fa_11_the_fte_conversion_has_exactly_one_consumer_and_it_is_the_fte_formula() -> None:
    """A-K07's tripwire lists the modules that must not import `app.domain.fte_hours`; this is the
    other half: among every module under `app/`, the FTE formula is the only importer. Contrast: it
    does import it. Mutation: the dispatcher or the what-if importing the conversion directly."""
    importers = {
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in Path(BACKEND_ROOT, "app").rglob("*.py")
        if "app.domain.fte_hours"
        in _imports_of(path.relative_to(BACKEND_ROOT).as_posix())
    }

    assert importers == {FORMULA}


def test_fa_11_the_formula_cannot_see_the_planners_hours_or_the_selling_rate() -> None:
    """Points 3/4/6 — no `planned_allocation_hours`, `billable_hours`, `headcount` or selling rate
    name anywhere in the module's code (docstrings aside), and its month type carries only whose,
    which month, the rate and the calendar."""
    tree = _tree(FORMULA)
    names = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    fields = {
        node.target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    forbidden = {
        "planned_allocation_hours", "billable_hours", "availability_hours", "headcount",
        "default_selling_rate", "generates_revenue",
    }
    assert not (names | fields) & forbidden
    assert set(AssignedFteMonth.__dataclass_fields__) == {
        "position_id", "period_month", "rate", "basis",
    }


def test_fa_3_the_formula_rounds_once_and_has_no_calendar_rule_of_its_own() -> None:
    """FA-3/FA-2 (structural, weaker than the behaviour above): `round_money` is called exactly
    once, there is no `round`, `quantize` or `float`, no `weekday` and no literal 8, 40 or 5."""
    tree = _tree(FORMULA)
    calls = [
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    ]
    assert calls.count("round_money") == 1
    assert not set(calls) & {"round", "quantize", "float"}
    assert "weekday" not in {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)} & {8, 40, 5}


def test_qa_each_position_is_priced_with_its_own_fte_and_the_positions_are_summed() -> None:
    """QA (K-02) — every other figure of this file has one FTE position, so a formula that took
    "the" FTE from the first line for all months (or the last, or an average) gave the right cent
    everywhere. Two positions, different FTE, same rate (100 per hour, 195 basis hours): 0.5 x 195 x
    100 = 9750.00 and 0.25 x 195 x 100 = 4875.00, together **14625.00** — and each one alone is its
    own figure. Mutation: the FTE looked up from the first line for every month."""
    a, b = uuid.uuid4(), uuid.uuid4()
    calendar = _basis()

    def component(fte_by_id: dict[uuid.UUID, str]) -> Decimal:
        return _calculated(
            assigned_fte_cost(
                [AssignedFteLine(position_id=p, assigned_fte=Decimal(f))
                 for p, f in fte_by_id.items()],
                [AssignedFteMonth(position_id=p, period_month=MAR, rate=_rate("100.0000"),
                                  basis=calendar) for p in fte_by_id],
                rate_source="live_catalog",
                scenario_currency="PLN",
            )
        )

    assert component({a: "0.5000"}) == Decimal("9750.00")
    assert component({b: "0.2500"}) == Decimal("4875.00")
    assert component({a: "0.5000", b: "0.2500"}) == Decimal("14625.00")
    assert component({b: "0.2500", a: "0.5000"}) == Decimal("14625.00")
