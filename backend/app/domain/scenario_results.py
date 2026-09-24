"""One scenario's whole-life profit, margin and markup — the sum for the whole scenario, never per
month (F-10, ADR-0002; SC-7-01, Issue #12).

    included_cost = base_personnel_cost + paid_absence_cost + additional_cost
    profit        = revenue − included_cost
    margin        = profit / revenue × 100%        (`app.core.money.ratio_percent`)
    markup        = profit / included_cost × 100%  (`app.core.money.ratio_percent`)

A pure function over the **answers** the three already-proven calculations return — no `Session`,
no clock, no catalogue lookup, and nothing recomputed: `revenue`, `base_cost`, `paid_absence` and
`additional_cost` are handed in exactly as `app.domain.revenue`, `app.domain.personnel_cost`,
`app.domain.paid_absence_cost` and `app.domain.additional_cost` produced them.

**This module sits above all four and is the only one that imports every one of them together**
(ADR-0004, "fits", confirmed at gate 1 for SC-7-01) — not a fifth calculation, a composition of the
four that already exist. None of the four imports this module or one another (rule 10 of the
Invariant Guardian; the structural test of this task extends the existing ones in
`tests/test_personnel_cost.py` and `tests/test_additional_cost.py`).

**Never a number where a component is unavailable.** If any of the four answers is not its
`*Result` shape, the whole aggregate is `NOT_APPLICABLE` — never a partial sum and never `0`
(ADR-0002, aneks SC-7-01). *Which* component and *why* is not repeated here: each answer already
carries its own named state, and the API layer reports each one's `state` on its own payload
(`app.api.response_shaping.shape_scenario_results`) rather than folding four reasons into one.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.core.money import NOT_APPLICABLE, ratio_percent, round_money
from app.domain.additional_cost import AdditionalCostAnswer, AdditionalCostResult
from app.domain.paid_absence_cost import PaidAbsenceCostAnswer, PaidAbsenceCostResult
from app.domain.personnel_cost import PersonnelCostAnswer, PersonnelCostResult
from app.domain.revenue import RevenueAnswer, RevenueResult

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


def scenario_profitability(
    revenue: RevenueAnswer,
    base_cost: PersonnelCostAnswer,
    paid_absence: PaidAbsenceCostAnswer,
    additional_cost: AdditionalCostAnswer,
) -> ScenarioProfitability:
    """Profit, margin, markup and the included cost — or `NOT_APPLICABLE` on all four.

    1. **Any of the four not `calculated` → `NOT_APPLICABLE` on all four fields.** Never the sum of
       the components that did resolve (the same rule every one of the four already applies to
       itself, applied once more at this layer).
    2. **`included_cost`** — the three cost components, already each rounded once in their own
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
    ):
        return ScenarioProfitability(
            profit=NOT_APPLICABLE,
            margin=NOT_APPLICABLE,
            markup=NOT_APPLICABLE,
            included_cost=NOT_APPLICABLE,
        )

    included_cost = round_money(base_cost.cost + paid_absence.cost + additional_cost.amount)
    profit = round_money(revenue.revenue - included_cost)
    return ScenarioProfitability(
        profit=profit,
        margin=ratio_percent(profit, revenue.revenue),
        markup=ratio_percent(profit, included_cost),
        included_cost=included_cost,
    )
