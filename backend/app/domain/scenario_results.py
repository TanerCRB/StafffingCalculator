"""One scenario's whole-life profit, margin and markup — the sum for the whole scenario, never per
month (F-10, ADR-0002; SC-7-01, Issue #12).

    included_cost = base_personnel_cost + paid_absence_cost + fixed_amount_cost
                    + assigned_fte_cost + additional_cost
    profit        = revenue − included_cost
    margin        = profit / revenue × 100%        (`app.core.money.ratio_percent`)
    markup        = profit / included_cost × 100%  (`app.core.money.ratio_percent`)

A pure function over the answers the component calculations return — no `Session`, no clock, no
catalogue lookup, and nothing recomputed.

**This module composes the independently calculated answers** (ADR-0004, "fits", confirmed at gate
1 for SC-7-01) — not a new cost calculation. None of the cost calculators imports this module or
one another (rule 10 of the
Invariant Guardian; the structural test of this task extends the existing ones in
`tests/test_personnel_cost.py` and `tests/test_additional_cost.py`).

**Never a number where a component is unavailable.** If any required answer is not its
`*Result` shape, the whole aggregate is `NOT_APPLICABLE` — never a partial sum and never `0`
(ADR-0002, addendum SC-7-01). *Which* component and *why* is not repeated here: each answer already
carries its own named state, and the API layer reports each one's `state` on its own payload
(`app.api.response_shaping.shape_scenario_results`) rather than folding component reasons into one.

**Never one number out of two currencies** (R-01 of the SC-4-03 verification, 2026-09-25). Each cost
component checks its currency only against the scenario's — and when the scenario
declares none (`NULL`), nothing below this layer compares the revenue's currency with the
costs', or the costs' with one another. Hence one rule here, in the one place that composes a
profit (`/results`, the what-if and the SC-6-02 comparison all call this function): four
`calculated` components in more than one currency → the state `currency_mismatch` and
`NOT_APPLICABLE` on all four fields — nothing converted (`exchange_rates`, ADR-0006, does not
exist).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from app.core.money import NOT_APPLICABLE, ratio_percent, round_money
from app.domain.additional_cost import AdditionalCostAnswer, AdditionalCostResult
from app.domain.assigned_fte_cost import AssignedFteCostAnswer, AssignedFteCostResult
from app.domain.fixed_amount_cost import FixedAmountCostAnswer, FixedAmountCostResult
from app.domain.paid_absence_cost import PaidAbsenceCostAnswer, PaidAbsenceCostResult
from app.domain.personnel_cost import PersonnelCostAnswer, PersonnelCostResult
from app.domain.revenue import (
    CALCULATED,
    CURRENCY_MISMATCH,
    EXPECTED_CALCULATED,
    RevenueAnswer,
    RevenueResult,
)

PROFITABILITY_NOT_APPLICABLE: Final = "not_applicable"
"""At least one of the four components is not `calculated` — which one and why is its own `state`
in the response, not repeated here."""

ProfitabilityState = Literal["calculated", "not_applicable", "currency_mismatch"]
"""Why the four aggregate fields are or are not numbers: `calculated`, `not_applicable` (a component
not stated) or `currency_mismatch` (all four stated, in more than one currency)."""

Amount = Decimal | str
"""A `Decimal` when calculable, otherwise `NOT_APPLICABLE` ("n/a") — never `None` and never `0`."""


@dataclass(frozen=True)
class ScenarioProfitability:
    """The scenario-wide aggregate, or `NOT_APPLICABLE` on every field when it cannot be stated.

    All four fields share one fate: `included_cost` sums three components, `profit` is the revenue
    minus that sum, and `margin`/`markup` are ratios of `profit` — so if any input is unavailable,
    none of the four can be a real number (a "profit" over an unknown cost is not a profit).
    """

    profit: Amount
    margin: Amount
    markup: Amount
    included_cost: Amount
    state: ProfitabilityState = CALCULATED
    """Why the four fields above are `NOT_APPLICABLE` — or `calculated` when they are numbers."""


@dataclass(frozen=True)
class ScenarioExpectedProfitability:
    """Expected profit and margin, when expected revenue and the included cost are both usable."""

    expected_profit: Amount
    expected_margin: Amount


def scenario_expected_profitability(
    revenue: RevenueAnswer, profitability: ScenarioProfitability
) -> ScenarioExpectedProfitability:
    """Calculate expected profit from the already-rounded expected revenue and included cost.

    The revenue answer owns the named expected-revenue state (`no_probabilities` or
    `not_applicable`). The composed profitability answer owns the cost availability and currency
    checks. Reuse both answers so expected profitability cannot introduce a second currency or
    component-availability rule.
    """
    if (
        not isinstance(revenue, RevenueResult)
        or revenue.expected_state != EXPECTED_CALCULATED
        or profitability.state != CALCULATED
        or not isinstance(revenue.expected_revenue, Decimal)
        or not isinstance(profitability.included_cost, Decimal)
    ):
        return ScenarioExpectedProfitability(
            expected_profit=NOT_APPLICABLE,
            expected_margin=NOT_APPLICABLE,
        )

    expected_profit = round_money(revenue.expected_revenue - profitability.included_cost)
    return ScenarioExpectedProfitability(
        expected_profit=expected_profit,
        expected_margin=ratio_percent(expected_profit, revenue.expected_revenue),
    )


def _withheld(state: ProfitabilityState) -> ScenarioProfitability:
    return ScenarioProfitability(
        profit=NOT_APPLICABLE,
        margin=NOT_APPLICABLE,
        markup=NOT_APPLICABLE,
        included_cost=NOT_APPLICABLE,
        state=state,
    )


def scenario_profitability(
    revenue: RevenueAnswer,
    base_cost: PersonnelCostAnswer,
    paid_absence: PaidAbsenceCostAnswer,
    additional_cost: AdditionalCostAnswer,
    fixed_amount: FixedAmountCostAnswer,
    assigned_fte: AssignedFteCostAnswer,
) -> ScenarioProfitability:
    """Profit, margin, markup and the included cost — or `NOT_APPLICABLE` on all four.

    1. **Any required component not `calculated` → `NOT_APPLICABLE` on all four fields.** Never
       the sum of the components that did resolve (the same rule each component applies to
       itself, applied once more at this layer). State `not_applicable`.
    1a. **Calculated components in more than one currency → `NOT_APPLICABLE` on all four fields**,
       state `currency_mismatch` (R-01, SC-4-03). Checked here, not in the component calculators
       modules: only this layer holds all four currencies, and with no scenario currency none of
       them has anything to compare against.
    2. **`included_cost`** — the five cost components, already each rounded once in their own
       module, summed and rounded once more through `round_money`: the sum of three exact
       two-decimal amounts needs no correction, but this is still the one place that finishes the
       figure this layer reports, and it is rounded here rather than left unrounded (Invariant
       Guardian rule 2).
    3. **`profit`** — `revenue − included_cost`, rounded once the same way.
    4. **`margin`/`markup`** — `app.core.money.ratio_percent(profit, …)`, which is itself the single
       place that turns a zero denominator into `NOT_APPLICABLE` rather than raising or returning
       `0` (criterion K-02: this function never re-implements that check).
    """
    if not (
        isinstance(revenue, RevenueResult)
        and isinstance(base_cost, PersonnelCostResult)
        and isinstance(paid_absence, PaidAbsenceCostResult)
        and isinstance(additional_cost, AdditionalCostResult)
        and isinstance(fixed_amount, FixedAmountCostResult)
        and isinstance(assigned_fte, AssignedFteCostResult)
    ):
        return _withheld(PROFITABILITY_NOT_APPLICABLE)
    currencies = {
        revenue.currency,
        base_cost.currency,
        paid_absence.currency,
        additional_cost.currency,
        fixed_amount.currency,
        assigned_fte.currency,
    }
    if len(currencies) != 1:
        return _withheld(CURRENCY_MISMATCH)

    included_cost = round_money(
        base_cost.cost
        + paid_absence.cost
        + additional_cost.amount
        + fixed_amount.cost
        + assigned_fte.cost
    )
    profit = round_money(revenue.revenue - included_cost)
    return ScenarioProfitability(
        profit=profit,
        margin=ratio_percent(profit, revenue.revenue),
        markup=ratio_percent(profit, included_cost),
        included_cost=included_cost,
    )
