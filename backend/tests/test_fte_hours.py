"""SC-3-07, A-K01..A-K07 - FTE to hours from the working calendar (F-04, F-05).

`app.domain.fte_hours` is a pure domain function taking an already-resolved `CalendarBasis`, so
almost everything here is proven without a database; the one test that reads a basis through the
data layer (A-K04's location with no calendar) uses the real PostgreSQL of the suite.

Not proven here, deliberately (FTE-6): that an *approved* scenario reads the snapshot rather than
the live calendar. There is no consumer yet, so no path to prove it on; the first consumer (#79
SC-5-04 or #80) does. A-K01's independence of `scenarios.full_time_hours_per_week` is by
construction (the functions take only a `CalendarBasis`), see the signature test.
FTE-7 (revenue unmoved by `standard_hours_per_day`) has no honest fixture: no revenue function
accepts a calendar, so such a test could not fail.

The facts every figure is computed from, in the docstring that asserts it and never from what the
implementation returned:

- March 2026: 2026-03-01 is a Sunday. Monday-to-Friday days: 22. Saturdays: 7, 14, 21, 28 (four).
  Wednesday 2026-03-11 is a pattern-working day.
- February 2026 has 28 days and 20 Monday-to-Friday days.
- No calendar carries a standard day of 8.00 (`conftest.FORBIDDEN_FIXTURE_HOURS`), so no constant
  can produce any figure below.
"""

import ast
import inspect
import pathlib
import uuid
from datetime import date
from decimal import Decimal
from typing import get_args

import pytest

import app.domain.fte_hours as fte_module
from app.core.money import NOT_APPLICABLE, round_money
from app.data.working_calendar import basis_by_location
from app.domain.capacity import (
    NO_CALENDAR,
    RESOLVED,
    AbsenceSpan,
    CalendarBasis,
    month_capacity,
)
from app.domain.fte_hours import (
    MAX_INPUT,
    NO_WORKING_DAYS,
    FteConversion,
    FteState,
    fte_to_hours,
    hours_to_fte_percent,
)
from app.domain.personnel_cost import (
    MonthCostRate,
    WorkedMonth,
    base_personnel_cost,
)
from app.models import WorkingCalendarDayKind
from tests.conftest import (
    MONDAY_TO_FRIDAY,
    MONDAY_TO_SATURDAY,
    make_dimension_tuple,
    make_working_calendar,
)

MARCH = date(2026, 3, 1)
FEBRUARY = date(2026, 2, 1)
NON_WORKING = WorkingCalendarDayKind.NON_WORKING
WORKING = WorkingCalendarDayKind.WORKING
WEDNESDAY = date(2026, 3, 11)
SATURDAY = date(2026, 3, 14)


def _basis(
    hours_per_day: str = "7.50",
    *,
    name: str = "Poland 7.5h",
    pattern: str = MONDAY_TO_FRIDAY,
    exceptions: dict | None = None,
) -> CalendarBasis:
    assert Decimal(hours_per_day) != Decimal("8.00"), "8.00 hides a hard-coded constant"
    return CalendarBasis(
        calendar_id=uuid.uuid4(),
        name=name,
        standard_hours_per_day=Decimal(hours_per_day),
        week_pattern=pattern,
        exceptional_days=exceptions or {},
    )


def _hours(basis, fte: str, month: date = MARCH):
    return fte_to_hours(basis, period_month=month, fte_fraction=Decimal(fte))


def _fte(basis, hours: str, month: date = MARCH):
    return hours_to_fte_percent(basis, period_month=month, hours=Decimal(hours))


def test_a_k01_hours_of_one_fte_come_from_the_calendar_not_a_constant() -> None:
    """A-K01 - 1 FTE in March 2026 is 22 x the calendar's own hours per day.

    7.50 x 22 = 165.00 and 6.00 x 22 = 132.00. The calendars share the week pattern and have no
    exceptional days, so only the hours per day can explain the difference. 22 x 8 = 176.00 (a
    fallback constant) and 40 / 5 = 8 are both excluded by the two literals.
    """
    seven_and_a_half = _basis("7.50", name="Poland 7.5h")
    six = _basis("6.00", name="Portugal 6h")

    first = _hours(seven_and_a_half, "1")
    second = _hours(six, "1")

    assert first.value == Decimal("165.00")
    assert second.value == Decimal("132.00")
    assert Decimal("176.00") not in (first.value, second.value)
    assert first.basis_hours == Decimal("165.00")
    assert _fte(seven_and_a_half, "165.00").value == Decimal("100.00")
    assert _fte(six, "132.00").value == Decimal("100.00")


def test_a_k02_the_figure_follows_working_days_at_unchanged_hours_per_day() -> None:
    """A-K02 - Monday-to-Saturday: 22 + 4 = 26 working days in March 2026.

    Baseline 26 x 7.50 = 195.00. A NON_WORKING exception on Wednesday 2026-03-11 (a pattern-working
    day) gives 25 days = 187.50; a WORKING exception on Saturday 2026-03-14 on the Mon-Fri calendar
    gives 23 days = 172.50 (against 165.00). Each delta is exactly one day x 7.50. `weekday() < 5`
    would give 22 for the Saturday-working pattern.
    """
    saturday_calendar = _basis(pattern=MONDAY_TO_SATURDAY)
    with_holiday = _basis(pattern=MONDAY_TO_SATURDAY, exceptions={WEDNESDAY: NON_WORKING})
    weekday_calendar = _basis()
    with_extra_saturday = _basis(exceptions={SATURDAY: WORKING})

    baseline = _hours(saturday_calendar, "1")
    holiday = _hours(with_holiday, "1")

    assert baseline.working_days == 26
    assert baseline.value == Decimal("195.00")
    assert holiday.working_days == 25
    assert holiday.value == Decimal("187.50")
    assert baseline.value - holiday.value == Decimal("7.50")

    plain = _hours(weekday_calendar, "1")
    extra = _hours(with_extra_saturday, "1")
    assert plain.value == Decimal("165.00")
    assert extra.value == Decimal("172.50")
    assert extra.value - plain.value == Decimal("7.50")
    assert extra.standard_hours_per_day == plain.standard_hours_per_day == Decimal("7.50")


def test_a_k03_the_conversion_is_gross_of_absences() -> None:
    """A-K03 - an absence on a pattern-working day changes capacity, never the conversion.

    One absence on Wednesday 2026-03-11: `derived_capacity_hours` goes from 165.00 to 157.50 (one
    day x 7.50 lower), while both conversions are identical with and without it. The conversion
    has no absence parameter, so the contrast is the capacity of the same basis.
    """
    basis = _basis()
    absence = AbsenceSpan(start_date=WEDNESDAY, end_date=WEDNESDAY)

    without = month_capacity(basis, headcount=1, period_month=MARCH)
    with_absence = month_capacity(basis, headcount=1, period_month=MARCH, absences=[absence])

    assert without.hours == Decimal("165.00")
    assert with_absence.hours == Decimal("157.50")
    assert without.hours - with_absence.hours == Decimal("7.50")
    assert _hours(basis, "1").value == Decimal("165.00")
    assert _fte(basis, "165.00").value == Decimal("100.00")


def test_a_k04_no_calendar_is_the_existing_named_state_and_never_a_number(db_session) -> None:
    """A-K04 - a location with no calendar: `no_calendar` and `"n/a"` in both directions.

    Read through the real data layer: a location whose `calendar_id` is NULL is absent from
    `basis_by_location`, which is what the caller turns into `None`. The state string is the very
    constant of `app.domain.capacity` and the value the very `NOT_APPLICABLE` of `app.core.money`.
    Contrast: the same conversion with a calendar returns a number. A mutation returning
    `Decimal(0)` or a default calendar fails the state/value assertions.
    """
    calendar = make_working_calendar(db_session, name="Poland 7.5h")
    without_calendar = make_dimension_tuple(db_session, suffix="-none")
    with_calendar = make_dimension_tuple(db_session, suffix="-cal", calendar=calendar)

    bases = basis_by_location(
        db_session, [without_calendar.location_id, with_calendar.location_id]
    )
    missing = bases.get(without_calendar.location_id)
    assert missing is None

    hours = _hours(missing, "1")
    fte = _fte(missing, "100.00")
    for result in (hours, fte):
        assert result.state == NO_CALENDAR == "no_calendar"
        assert result.value == NOT_APPLICABLE == "n/a"
        assert result.calendar_id is None and result.working_days is None

    resolved = _hours(bases[with_calendar.location_id], "1")
    assert resolved.state == RESOLVED
    assert resolved.value == Decimal("165.00")


def test_a_k04b_a_month_without_working_days_is_a_named_state_in_both_directions() -> None:
    """A-K04b - every day of March is NON_WORKING: the state is `no_working_days`.

    Both directions give the state `no_working_days` and `"n/a"` - no `ZeroDivisionError`, no `0`.
    The state is distinct from `no_calendar` (a consumer tells them apart by the state), and the
    basis fields still name the calendar. Contrast: the same calendar in February 2026 resolves
    (20 x 7.50 = 150.00).
    """
    holidays = {date(2026, 3, day): NON_WORKING for day in range(1, 32)}
    basis = _basis(exceptions=holidays)

    hours = _hours(basis, "1")
    fte = _fte(basis, "100.00")

    for result in (hours, fte):
        assert result.state == NO_WORKING_DAYS == "no_working_days"
        assert result.state not in (NO_CALENDAR, RESOLVED)
        assert result.value == NOT_APPLICABLE
        assert result.working_days == 0
        assert result.calendar_name == "Poland 7.5h"
    assert _hours(basis, "1", FEBRUARY).state == RESOLVED
    assert _hours(basis, "1", FEBRUARY).value == Decimal("150.00")


def test_a_k05_every_figure_names_its_basis() -> None:
    """A-K05 - calendar id, name, hours per day and working days, for each direction and each
    calendar; the two calendars' names differ and each result carries its own, not a shared one."""
    first = _basis("7.50", name="Poland 7.5h")
    second = _basis("6.00", name="Portugal 6h", exceptions={WEDNESDAY: NON_WORKING})

    for basis, days in ((first, 22), (second, 21)):
        for result in (_hours(basis, "1"), _fte(basis, "100.00")):
            assert result.calendar_id == basis.calendar_id
            assert result.calendar_name == basis.name
            assert result.standard_hours_per_day == basis.standard_hours_per_day
            assert result.working_days == days
            assert result.basis_hours == Decimal(days) * basis.standard_hours_per_day
    assert _hours(first, "1").calendar_name != _hours(second, "1").calendar_name
    assert _hours(first, "1").calendar_id != _hours(second, "1").calendar_id


def test_a_k06_hours_from_fte_round_once_half_up() -> None:
    """A-K06 - 7.50 x 21 = 157.50; x 0.35 = 55.125 -> 55.13 half-up (`round()` gives 55.12, banker
    rounding, and a float product is inexact). x 0.05 = 7.875 -> 7.88. A 7.55 calendar with 21 days
    = 158.55, x 0.5 = 79.275 -> 79.28.
    """
    basis = _basis(exceptions={WEDNESDAY: NON_WORKING})
    assert _hours(basis, "0.35").value == Decimal("55.13")
    assert _hours(basis, "0.05").value == Decimal("7.88")
    tie = _basis("7.55", exceptions={WEDNESDAY: NON_WORKING})
    assert _hours(tie, "0.5").value == Decimal("79.28")
    assert isinstance(_hours(basis, "0.35").value, Decimal)


def test_a_k06_the_worked_example_and_the_exact_fte_round_trip() -> None:
    """A-K06 - ADR-0002 addendum: 100.00 h over a 168 h basis is 59.52 %, and the *exact* FTE back
    is 100.00 h. 168 = 28 days x 6.00 (a `'1111111'` calendar in February 2026).

    Two-step rounding: the percent rounded to 59.52 converts back to 99.99 h, and an FTE rounded to
    two places before the multiplication (0.60 x 168 = 100.80) gives 100.80 - so the exact quotient
    passed through gives 100.00 and the mutated pipeline does not.
    """
    basis = _basis("6.00", pattern="1111111", name="Seven days")
    forward = _fte(basis, "100.00", FEBRUARY)
    assert forward.basis_hours == Decimal("168.00")
    assert forward.value == Decimal("59.52")

    exact_fte = Decimal("100.00") / Decimal("168.00")
    assert _hours(basis, str(exact_fte), FEBRUARY).value == Decimal("100.00")
    assert _hours(basis, "0.5952", FEBRUARY).value == Decimal("99.99")
    assert _hours(basis, "0.60", FEBRUARY).value == Decimal("100.80")


def test_a_k06_position_level_no_headcount_factor() -> None:
    """A-K06/FTE-3 - hours are a position total: 3 x 165.00 = 495.00 h is 300.00 % (3.00 FTE), and
    3.00 FTE is 495.00 h. The functions have no headcount parameter to multiply by."""
    basis = _basis()
    assert _fte(basis, "495.00").value == Decimal("300.00")
    assert _hours(basis, "3").value == Decimal("495.00")


def test_a_k06_the_module_has_no_own_rounding_float_or_weekday() -> None:
    """A-K06/FTE-5 and A-K02 - a structural check: no `round`, `quantize`, `float` call and no
    `weekday` attribute in the module, and no literal 8, 40 or 5. Weaker than the behavioural
    tests above; it only catches the spelling of the mutations they cover."""
    tree = ast.parse(pathlib.Path(fte_module.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    assert not called & {"round", "quantize", "float"}
    assert "weekday" not in {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    numbers = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
    assert not numbers & {8, 40, 5}


def test_a_k07_nothing_existing_moves() -> None:
    """A-K07 - a pinned dataset gives the same figures as on `main` (literals checked by running the
    same computation on the main checkout; the conversion is not on these paths).

    Capacity: March 2026, 7.50 h, headcount 2, one absence on 2026-03-11 -> 2 x 165.00 - 7.50 =
    322.50. Cost (SC-5-01): 120.00 planned hours x 120.00 = 14400.00. The import tripwire below
    covers the wiring; these literals cover the values.
    """
    basis = _basis()
    capacity = month_capacity(
        basis,
        headcount=2,
        period_month=MARCH,
        absences=[AbsenceSpan(start_date=WEDNESDAY, end_date=WEDNESDAY)],
    )
    assert capacity.hours == Decimal("322.50")

    month = WorkedMonth(
        position_id=uuid.uuid4(),
        period_month=MARCH,
        planned_allocation_hours=Decimal("120.00"),
        rate=MonthCostRate(
            cost_rate=Decimal("120.00"),
            currency="PLN",
            windows=(),
            surcharge_percent=Decimal("0"),
            includes_surcharge=False,
            cost_rate_unit="hour",
        ),
        basis=basis,
    )
    cost = base_personnel_cost([month], rate_source="live_catalog", scenario_currency="PLN")
    assert cost.cost == Decimal("14400.00")



APP_ROOT = pathlib.Path(fte_module.__file__).resolve().parents[1]


def _imported_modules(path: pathlib.Path, package: list[str] | None = None) -> set[str]:
    """Every module a file imports, dotted - `from app.domain import fte_hours` counts as
    `app.domain.fte_hours`, and a relative import (`from .fte_hours import x`, `from . import
    fte_hours`) is resolved against the file's package. A string constant that spells a dotted
    `app.` module (the argument of `importlib.import_module`) counts too. `package` is derived from
    the path under `app/` unless given (the matcher tests pass it for files outside the tree)."""
    if package is None:
        try:
            package = list(path.resolve().relative_to(APP_ROOT.parent).parent.parts)
        except ValueError:
            package = []
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            if module:
                names.add(module)
                names.update(f"{module}.{alias.name}" for alias in node.names)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("app.")
        ):
            names.add(node.value)
    return names


HOURS_AND_REVENUE_MODULES = (
    "domain/capacity.py",
    "domain/revenue.py",
    "domain/revenue_time_and_material.py",
    "domain/revenue_fixed_price.py",
    "domain/revenue_story_points.py",
    "domain/revenue_outcome_based.py",
    "domain/personnel_cost.py",
    "domain/paid_absence_cost.py",
    "domain/fixed_amount_cost.py",
    "domain/absence_budget.py",
    "domain/scenario_results.py",
    "data/personnel_cost.py",
    "data/commercial_terms.py",
    "data/scenario_results.py",
    "data/scenario_what_if.py",
)


def test_a_k07_tripwire_revenue_and_hours_modules_do_not_import_the_fte_conversion() -> None:
    """A-K07 - a TRIPWIRE, not the mechanism that keeps the conversion out of a calculation.

    The modules that compute billable hours, availability, planned allocation, derived capacity and
    revenue/cost do not import `app.domain.fte_hours`. Read from the AST, so a docstring mentioning
    the name is no hit and a first legitimate consumer elsewhere (#79, #80) needs no edit here. It
    catches only the cheapest wiring mistake; it is not the FTE-5 mechanism (that is the rounding
    and no-constant structure tests). The listed files must exist so a rename cannot empty the
    scan, and the scan is shown to see this file's own import.
    """
    app_root = pathlib.Path(fte_module.__file__).resolve().parents[1]
    for relative in HOURS_AND_REVENUE_MODULES:
        path = app_root / relative
        assert path.is_file(), f"{relative} moved: update the tripwire's list"
        assert "app.domain.fte_hours" not in _imported_modules(path), (
            f"{relative} imports the FTE conversion"
        )
    assert "app.domain.fte_hours" in _imported_modules(pathlib.Path(__file__))


def test_a_k06_hours_to_fte_percent_rounds_half_up_in_the_forward_direction() -> None:
    """A-K06 - the percent direction has its own rounding proof (FTE -> hours tests can't reach it).

    February 2026 has 20 Monday-to-Friday days. At 10.00 h/day the basis is 200.00 h, so
    0.01 h = 0.005 % -> 0.01 and 0.05 h = 0.025 % -> 0.03 (half-up; banker's rounding gives 0.00 and
    0.02, a truncation gives 0.00 and 0.02). At 7.50 h/day the basis is 150.00 h and 1.00 h =
    0.6666...% -> 0.67 (truncation gives 0.66).
    """
    ten = _basis("10.00")
    assert _fte(ten, "0.01", FEBRUARY).basis_hours == Decimal("200.00")
    assert _fte(ten, "0.01", FEBRUARY).value == Decimal("0.01")
    assert _fte(ten, "0.05", FEBRUARY).value == Decimal("0.03")
    assert _fte(_basis(), "1.00", FEBRUARY).value == Decimal("0.67")


def test_a_k03_the_conversions_take_no_absence_and_no_headcount_input() -> None:
    """A-K03 / FTE-3 - the gross basis is a property of the signature, not only of the defaults.

    An optional `absences=()` or `headcount=1` parameter leaves every call without it unchanged, so
    no value assertion can see it. The exact parameter list can, and passing either keyword is a
    `TypeError`. Contrast: `month_capacity` of the same basis does accept absences (see
    `test_a_k03_the_conversion_is_gross_of_absences`), so the difference is by design.

    The same parameter list is the proof of A-K01's independence of the scenario's
    `full_time_hours_per_week` (Q4 = A): the basis is independent of that column *by construction*,
    because the functions take only a `CalendarBasis` and no scenario is in reach (the `scenario`
    keyword is a `TypeError`). It is an argument from the signature, not a database contrast.
    """
    hours_params = list(inspect.signature(hours_to_fte_percent).parameters)
    fte_params = list(inspect.signature(fte_to_hours).parameters)
    assert hours_params == ["basis", "period_month", "hours"]
    assert fte_params == ["basis", "period_month", "fte_fraction"]
    basis = _basis()
    absence = [AbsenceSpan(start_date=WEDNESDAY, end_date=WEDNESDAY)]
    for extra in ({"absences": absence}, {"headcount": 2}, {"scenario": object()}):
        with pytest.raises(TypeError):
            fte_to_hours(basis, period_month=MARCH, fte_fraction=Decimal("1"), **extra)
        with pytest.raises(TypeError):
            hours_to_fte_percent(basis, period_month=MARCH, hours=Decimal("165"), **extra)
    assert "absences" in inspect.signature(month_capacity).parameters


def test_a_k02_the_year_of_the_month_is_read_leap_february() -> None:
    """A-K02 - a seven-day pattern at 6.00 h: February 2026 has 28 days = 168.00 h, February 2028
    (leap) has 29 days = 174.00 h. Only the year differs, so a hard-coded year or a fixed 28-day
    February fails one of the two."""
    basis = _basis("6.00", pattern="1111111")
    common = _hours(basis, "1", FEBRUARY)
    leap = _hours(basis, "1", date(2028, 2, 1))
    assert (common.working_days, common.value) == (28, Decimal("168.00"))
    assert (leap.working_days, leap.value) == (29, Decimal("174.00"))
    assert _fte(basis, "174.00", date(2028, 2, 1)).value == Decimal("100.00")


def test_a_k06_fte_exact_recovers_the_hours_and_a_percent_is_not_the_supported_input() -> None:
    """A-K06 - `fte_exact` is the unrounded quotient: 100.00 h over 168 h feeds back to 100.00 h,
    while the rounded percent 59.52 read as a fraction gives 99.99 h.

    The unit trap: `fte_fraction` is 1 = 1 FTE. Passing the percent `59.52` (rather than 0.5952)
    gives ~ 100 times the hours (9999.36) - that is not the supported path, `fte_exact` is.
    """
    basis = _basis("6.00", pattern="1111111", name="Seven days")
    forward = _fte(basis, "100.00", FEBRUARY)

    assert forward.unit == "percent" and forward.value == Decimal("59.52")
    assert isinstance(forward.fte_exact, Decimal)
    back = fte_to_hours(basis, period_month=FEBRUARY, fte_fraction=forward.fte_exact)
    assert back.unit == "hours" and back.value == Decimal("100.00")
    assert _hours(basis, "0.5952", FEBRUARY).value == Decimal("99.99")
    assert _hours(basis, "59.52", FEBRUARY).value == Decimal("9999.36")
    assert _hours(basis, "1", FEBRUARY).fte_exact is None


def test_a_k06_fte_exact_is_absent_when_unresolved() -> None:
    """A-K06 - no `fte_exact` without a calendar or without working days."""
    holidays = {date(2026, 3, day): NON_WORKING for day in range(1, 32)}
    for basis in (None, _basis(exceptions=holidays)):
        result = _fte(basis, "100.00")
        assert result.fte_exact is None and result.value == NOT_APPLICABLE


@pytest.mark.parametrize(
    "bad",
    [
        Decimal("-1"),
        Decimal("-0.01"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        MAX_INPUT + 1,
    ],
)
def test_r03_invalid_amounts_are_a_value_error_in_both_directions(bad: Decimal) -> None:
    """R-03 - negative, non-finite and over-bound inputs are a `ValueError` (never a
    `decimal.InvalidOperation`), even with no calendar or a calendar with no working days."""
    holidays = {date(2026, 3, day): NON_WORKING for day in range(1, 32)}
    for basis in (_basis(), None, _basis(exceptions=holidays)):
        with pytest.raises(ValueError):
            hours_to_fte_percent(basis, period_month=MARCH, hours=bad)
        with pytest.raises(ValueError):
            fte_to_hours(basis, period_month=MARCH, fte_fraction=bad)


def test_r03_zero_and_the_bound_itself_are_legitimate_and_a_float_is_a_type_error() -> None:
    """R-03 - zero is a real amount (0.00 h, 0.00 %); the bound is inclusive and computes without
    `InvalidOperation`; a `float` is refused rather than converted."""
    basis = _basis()
    assert _hours(basis, "0").value == Decimal("0.00")
    assert _fte(basis, "0").value == Decimal("0.00")
    assert _hours(basis, str(MAX_INPUT)).state == RESOLVED
    assert _fte(basis, str(MAX_INPUT)).state == RESOLVED
    with pytest.raises(TypeError):
        fte_to_hours(basis, period_month=MARCH, fte_fraction=1.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        hours_to_fte_percent(basis, period_month=MARCH, hours=165)  # type: ignore[arg-type]


def test_r05_state_and_value_cannot_be_paired_wrongly() -> None:
    """R-05 - the invalid pairs are unconstructible; the valid ones construct."""
    common = {"period_month": MARCH, "unit": "hours"}
    one = Decimal("1.00")
    with pytest.raises(ValueError):  # resolved with the sentinel
        FteConversion(state="resolved", value=NOT_APPLICABLE, basis_hours=one, **common)
    with pytest.raises(ValueError):  # unresolved with a number
        FteConversion(state="no_calendar", value=one, **common)
    with pytest.raises(ValueError):
        FteConversion(state="no_working_days", value=Decimal("0"), **common)
    with pytest.raises(ValueError):  # resolved without a basis
        FteConversion(state="resolved", value=one, **common)
    with pytest.raises(ValueError):  # a state that does not exist
        FteConversion(state="resolvd", value=NOT_APPLICABLE, **common)  # type: ignore[arg-type]
    with pytest.raises(ValueError):  # fte_exact on an hours result
        FteConversion(state="resolved", value=one, basis_hours=one, fte_exact=one, **common)
    with pytest.raises(ValueError):  # a resolved percent result without fte_exact
        FteConversion(
            state="resolved", value=one, basis_hours=one, period_month=MARCH, unit="percent"
        )
    ok = FteConversion(state="no_calendar", value=NOT_APPLICABLE, **common)
    assert ok.state == NO_CALENDAR


def test_r05_the_named_states_have_one_spelling() -> None:
    """R-05 - the `Literal` is the three constants, in `capacity`'s spelling."""
    assert get_args(FteState) == (RESOLVED, NO_CALENDAR, NO_WORKING_DAYS)


def test_r05_a_zero_hour_calendar_is_the_named_state_not_a_resolved_n_a() -> None:
    """R-05 - the source table refuses `standard_hours_per_day = 0` and the snapshot does not repeat
    the CHECK; if a zero arrived, both directions give `no_working_days`, not a `ZeroDivisionError`
    and not a resolved `"n/a"`. Contrast: 6.00 resolves in the same month."""
    zero = _basis("0")
    for result in (_hours(zero, "1"), _fte(zero, "100.00")):
        assert result.state == NO_WORKING_DAYS
        assert result.value == NOT_APPLICABLE and result.basis_hours is None
    assert _hours(_basis("6.00"), "1").state == RESOLVED


def test_r06_the_echoed_month_is_the_first_of_the_month_in_every_state() -> None:
    """R-06 - a mid-month date is normalised (not rejected), and gives the same figures as the 1st,
    in both directions and in the unresolved states."""
    mid = date(2026, 3, 17)
    basis = _basis()
    holidays = {date(2026, 3, day): NON_WORKING for day in range(1, 32)}
    for result in (
        _hours(basis, "1", mid),
        _fte(basis, "165.00", mid),
        _hours(None, "1", mid),
        _fte(_basis(exceptions=holidays), "1", mid),
    ):
        assert result.period_month == MARCH
    assert _hours(basis, "1", mid).value == _hours(basis, "1", MARCH).value == Decimal("165.00")
    assert _fte(basis, "165.00", mid).working_days == 22


@pytest.mark.parametrize(
    ("source", "package"),
    [
        ("import app.domain.fte_hours", ["app", "data"]),
        ("import app.domain.fte_hours as fte", ["app", "data"]),
        ("from app.domain.fte_hours import fte_to_hours", ["app", "data"]),
        ("from app.domain import fte_hours", ["app", "data"]),
        ("from .fte_hours import fte_to_hours", ["app", "domain"]),
        ("from . import fte_hours", ["app", "domain"]),
        ("from ..domain.fte_hours import fte_to_hours", ["app", "data"]),
        ("from .. import domain\nfrom ..domain import fte_hours", ["app", "data"]),
        ("import importlib\nimportlib.import_module('app.domain.fte_hours')", ["app", "data"]),
        ("def late():\n    from app.domain.fte_hours import fte_to_hours", ["app", "data"]),
    ],
)
def test_a_k07_the_import_matcher_sees_every_spelling_of_the_import(
    tmp_path: pathlib.Path, source: str, package: list[str]
) -> None:
    """A-K07 - the tripwire's matcher, tested on files it must flag. The tripwire scans real
    modules that (correctly) hold no such import, so it cannot show that the matcher would see one;
    a matcher that misses a spelling would pass for ever. Each spelling below is a way a first
    consumer could write the import."""
    planted = tmp_path / "planted.py"
    planted.write_text(source + "\n", encoding="utf-8")
    assert "app.domain.fte_hours" in _imported_modules(planted, package)


def test_a_k07_the_import_matcher_flags_nothing_it_should_not(tmp_path: pathlib.Path) -> None:
    """A-K07 - contrast for the matcher: a docstring, a comment and imports of the neighbours are
    no hit, so a first legitimate consumer's own module is the only edit ever needed."""
    clean = tmp_path / "clean.py"
    clean.write_text(
        '"""Mentions app.domain.fte_hours in prose, and from app.domain import fte_hours."""\n'
        "# import app.domain.fte_hours\n"
        "from app.domain.capacity import month_capacity\n"
        "from . import capacity\n"
        "from .capacity import working_days_in_month\n"
        "import app.domain.fte_hours_extra_not_this\n"
        "NOTE = 'app.domain.fte_hours is documented here, not imported'\n",
        encoding="utf-8",
    )
    found = _imported_modules(clean, ["app", "domain"])
    assert "app.domain.fte_hours" not in found
    assert {"app.domain.capacity", "app.domain"} <= found


def test_r05_the_unit_is_one_of_two_named_values() -> None:
    """R-05 - an unknown unit is unconstructible (`furlongs`), in a resolved and an unresolved
    result alike; the two real units construct."""
    for state, value, extra in (
        ("no_calendar", NOT_APPLICABLE, {}),
        ("resolved", Decimal("1.00"), {"basis_hours": Decimal("1.00")}),
    ):
        with pytest.raises(ValueError):
            FteConversion(period_month=MARCH, state=state, value=value, unit="furlongs", **extra)  # type: ignore[arg-type]
    for unit in ("percent", "hours"):
        built = FteConversion(
            period_month=MARCH, state="no_calendar", value=NOT_APPLICABLE, unit=unit  # type: ignore[arg-type]
        )
        assert built.unit == unit


def test_r05_an_unresolved_state_carries_only_the_sentinel() -> None:
    """R-05 - a string that is not `"n/a"` (`"0.00"`, `""`) is no value of an unresolved state,
    which the Decimal-pairing rule alone would let through."""
    for state in ("no_calendar", "no_working_days"):
        for text in ("0.00", "", "N/A"):
            with pytest.raises(ValueError):
                FteConversion(period_month=MARCH, state=state, value=text, unit="hours")  # type: ignore[arg-type]
        ok = FteConversion(period_month=MARCH, state=state, value=NOT_APPLICABLE, unit="hours")  # type: ignore[arg-type]
        assert ok.value == "n/a"


def test_r05_fte_exact_needs_both_a_resolved_state_and_the_percent_unit() -> None:
    """R-05 - `fte_exact` on an unresolved percent result is refused (the unit is right, the state
    is not), and on a resolved hours result is refused (the state is right, the unit is not).
    Contrast: a resolved percent result with `fte_exact` constructs."""
    one = Decimal("1.00")
    for state in ("no_calendar", "no_working_days"):
        with pytest.raises(ValueError):
            FteConversion(
                period_month=MARCH, state=state, value=NOT_APPLICABLE, unit="percent",  # type: ignore[arg-type]
                fte_exact=one,
            )
    with pytest.raises(ValueError):
        FteConversion(
            period_month=MARCH, state="resolved", value=one, unit="hours",
            basis_hours=one, fte_exact=one,
        )
    ok = FteConversion(
        period_month=MARCH, state="resolved", value=one, unit="percent",
        basis_hours=one, fte_exact=one,
    )
    assert ok.fte_exact == one


def test_r02_every_result_names_its_unit_in_every_state() -> None:
    """R-02 - `unit` is `percent` for `hours_to_fte_percent` and `hours` for `fte_to_hours`, also
    when there is no calendar or no working day (a consumer reads the unit of an `"n/a"`)."""
    holidays = {date(2026, 3, day): NON_WORKING for day in range(1, 32)}
    for basis in (_basis(), None, _basis(exceptions=holidays)):
        assert _fte(basis, "100.00").unit == "percent"
        assert _hours(basis, "1").unit == "hours"


def test_r03_the_bound_is_the_documented_billion_and_is_safe_for_the_widest_month() -> None:
    """R-03 - the bound is a contract (one billion), not merely a symbol the tests read back: the
    product with the widest month (31 days x 24 h) rounds without `InvalidOperation`, and a large
    but ordinary figure (a hundred million hours) is accepted in both directions."""
    assert MAX_INPUT == Decimal("1000000000")
    assert round_money(MAX_INPUT * Decimal(31 * 24)) == Decimal("744000000000.00")
    every_day = _basis("24.00", pattern="1111111")
    assert _hours(every_day, "100000000").state == RESOLVED
    assert _fte(every_day, "100000000").state == RESOLVED
