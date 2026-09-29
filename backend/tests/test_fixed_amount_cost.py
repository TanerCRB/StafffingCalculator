"""SC-5-03, K-01 and the fixed-amount basis's own currency states (F-07; ADR-0013, addendum
2026-09-25 SC-5-03).

K-01 has two parts, each with its own test here:

1. **The formula is independent of `planned_allocation_hours`** — trivially true by construction
   (`app.domain.fixed_amount_cost.fixed_amount_cost` takes no hours at all), and proved the way this
   repository proves "a formula cannot use a figure it cannot see": the type it is fed
   (`FixedAmountLine`) carries no hours field for it to read.
2. **A separate module from the worked-time formula, structurally** — the mirror of control C-5
   (`tests/test_personnel_cost.py::test_c5_rate_windows_shares_geometry_never_a_rate_column`,
   `test_k_02_the_cost_path_and_the_revenue_path_never_import_each_other`): neither formula module
   imports the other, and neither imports the revenue path.

The three currency controls (F-1/F-2/F-3 of ADR-0013's addendum) are proved twice: once directly
against the pure function (fast, and immune to anything the API/data layers might get wrong), and
once through the real endpoint (`test_personnel_cost_access.py`'s SC-5-03 section) so the wiring
between them is not merely assumed.
"""

import ast
import uuid
from decimal import Decimal
from pathlib import Path

from app.domain.fixed_amount_cost import (
    CALCULATED,
    CURRENCY_MISMATCH,
    NO_COST_CURRENCY,
    FixedAmountCostResult,
    FixedAmountCostUnavailable,
    FixedAmountLine,
    fixed_amount_cost,
)
from tests.conftest import BACKEND_ROOT

POSITION = uuid.uuid4()
OTHER_POSITION = uuid.uuid4()


def _imports_of(relative_path: str) -> set[str]:
    """Every module a source file imports, read from its syntax tree — the same helper
    `test_personnel_cost.py`'s C-5 mirror uses, not re-implemented differently here."""
    tree = ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


# --- K-01: independent of planned_allocation_hours, a separate module ----------------------------


def test_k_01_the_line_type_the_formula_reads_carries_no_hours_field_at_all() -> None:
    """K-01, part 1 — a formula cannot read a figure a type does not carry. `FixedAmountLine` has
    exactly three fields, and `planned_allocation_hours`/`billable_hours`/`availability_hours` are
    not among them (contrast with `app.domain.personnel_cost.WorkedMonth`, which carries the hours
    the *other* formula needs)."""
    fields = {field.name for field in FixedAmountLine.__dataclass_fields__.values()}
    assert fields == {"position_id", "amount", "currency"}


def test_k_01_two_formulas_two_modules_neither_imports_the_other_or_the_revenue_path() -> None:
    """K-01, part 2 — structurally, the mirror of `test_personnel_cost.py`'s C-5 and
    `test_k_02_the_cost_path_and_the_revenue_path_never_import_each_other`.

    Mutation this kills: a "shared" cost function reading both `WorkedMonth` and `FixedAmountLine`
    behind one `if cost_basis == …` inside a single module — that module would import (or *be*) the
    other formula's module, and this assertion would fail on the day it does.
    """
    revenue_modules = {
        "app.data.commercial_terms",
        "app.domain.revenue",
        "app.domain.revenue_time_and_material",
        "app.domain.revenue_story_points",
    }
    worked_time_modules = {"app.data.personnel_cost", "app.domain.personnel_cost"}

    fixed_amount_imports = _imports_of("app/domain/fixed_amount_cost.py")
    assert not (fixed_amount_imports & revenue_modules), (
        "the fixed-amount formula imports the revenue path"
    )
    assert not (fixed_amount_imports & worked_time_modules), (
        "the fixed-amount formula imports the worked-time formula's module(s)"
    )

    worked_time_domain_imports = _imports_of("app/domain/personnel_cost.py")
    assert "app.domain.fixed_amount_cost" not in worked_time_domain_imports, (
        "the worked-time formula imports the fixed-amount formula's module — K-01 requires "
        "neither direction"
    )

    # The dispatcher (`app.data.personnel_cost`) is explicitly the one place allowed to import both
    # (ADR-0013, addendum 2026-09-25 SC-5-03, point 5) — asserted as the contrast, so this test
    # cannot pass by reading files that import nothing (mirrors `test_k_02_…`'s own contrast on
    # `scenario_approval.py`).
    dispatcher_imports = _imports_of("app/data/personnel_cost.py")
    assert {"app.domain.personnel_cost", "app.domain.fixed_amount_cost"} <= dispatcher_imports


# --- F-1/F-2/F-3: the currency states, against the pure function ---------------------------------


def test_f_1_a_single_line_whose_currency_differs_from_the_scenarios_is_a_mismatch() -> None:
    """F-1 — one `fixed_amount` position in EUR, a scenario declared in PLN: `currency_mismatch`,
    never `0` and never the EUR amount silently treated as PLN. Contrast: the same line against a
    scenario declared in EUR is `calculated`."""
    line = FixedAmountLine(position_id=POSITION, amount=Decimal("500.0000"), currency="EUR")

    mismatched = fixed_amount_cost([line], scenario_currency="PLN")
    assert isinstance(mismatched, FixedAmountCostUnavailable)
    assert mismatched.reason == CURRENCY_MISMATCH

    matching = fixed_amount_cost([line], scenario_currency="EUR")
    assert isinstance(matching, FixedAmountCostResult)
    assert (matching.cost, matching.currency) == (Decimal("500.00"), "EUR")


def test_f_2_two_lines_in_different_currencies_are_a_mismatch_same_currency_is_calculated() -> None:
    """F-2 — two `fixed_amount` positions, PLN and EUR: `currency_mismatch`, both currencies named
    in `assumptions_used.currencies` for the caller who may see them. Contrast: the same two
    positions both in PLN sum to their total, rounded once."""
    mismatched_lines = [
        FixedAmountLine(position_id=POSITION, amount=Decimal("100.0000"), currency="PLN"),
        FixedAmountLine(position_id=OTHER_POSITION, amount=Decimal("50.0000"), currency="EUR"),
    ]
    mismatched = fixed_amount_cost(mismatched_lines, scenario_currency=None)
    assert isinstance(mismatched, FixedAmountCostUnavailable)
    assert mismatched.reason == CURRENCY_MISMATCH
    assert mismatched.assumptions_used.currencies == ("EUR", "PLN")

    matching_lines = [
        FixedAmountLine(position_id=POSITION, amount=Decimal("100.0050"), currency="PLN"),
        FixedAmountLine(position_id=OTHER_POSITION, amount=Decimal("50.0050"), currency="PLN"),
    ]
    matching = fixed_amount_cost(matching_lines, scenario_currency=None)
    assert isinstance(matching, FixedAmountCostResult)
    # Rounded once, at the end (ADR-0002): 150.0100 -> 150.01, not 100.01 + 50.01 = 150.02 rounded
    # per line first.
    assert matching.cost == Decimal("150.01")
    assert matching.currency == "PLN"


def test_f_3_an_empty_plan_is_zero_with_a_declared_currency_and_a_named_state_without_one() -> None:
    """F-3 — no `fixed_amount` positions at all. A scenario that declares a currency still gets a
    true, statable sum of `0.00` in it (`calculated`); a scenario with no currency and nothing to
    take one from gets the named state `no_cost_currency`, never `0.00` of nothing."""
    declared = fixed_amount_cost([], scenario_currency="PLN")
    assert isinstance(declared, FixedAmountCostResult)
    assert (declared.cost, declared.currency) == (Decimal("0.00"), "PLN")

    undeclared = fixed_amount_cost([], scenario_currency=None)
    assert isinstance(undeclared, FixedAmountCostUnavailable)
    assert undeclared.reason == NO_COST_CURRENCY


def test_calculated_is_not_a_named_state_it_is_the_label_of_a_result() -> None:
    """`CALCULATED` is not a value `fixed_amount_cost` ever returns as `.reason` — it is the API's
    label for a `FixedAmountCostResult`, applied by `app.api.response_shaping`. Guards against a
    typo that would make an unavailable answer indistinguishable from a calculated one."""
    result = fixed_amount_cost(
        [FixedAmountLine(position_id=POSITION, amount=Decimal("1.0000"), currency="PLN")],
        scenario_currency="PLN",
    )
    assert isinstance(result, FixedAmountCostResult)
    assert CALCULATED not in (CURRENCY_MISMATCH, NO_COST_CURRENCY)
