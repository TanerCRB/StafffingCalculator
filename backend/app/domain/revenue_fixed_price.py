"""Fixed Price revenue: the price agreed for the whole project (F-06.2; SC-4-02, Issue #66).

A pure function of the one row the data layer read — no `Session`, no clock, no catalogue, no hours.
F-06.2: "Increasing effort or staffing shall not automatically increase revenue", and AC-07: a
fixed-price contract of 150000 whose staffing cost rises from 100000 to 120000 still earns 150000.
What makes that true here is not a rule this module follows but what it **cannot see**: its input
is an `AgreedPrice` — an amount and a currency — and nothing that could carry an allocation, a
`billable_hours` figure, a rate window or a cost (ADR-0003, addendum 2026-09-25 SC-4-02, point 3).

**Imports nothing of another commercial model, nothing of `app.data.rate_windows` and nothing of
any cost calculation, at any depth** (F-06: independent calculation per model; rule 10 of the
Invariant Guardian; control FP-4) — asserted over the whole import graph by
`tests/test_fixed_price_revenue.py`. The shared vocabulary is `app.domain.revenue`.

**Out of scope, and said so in the answer** (gate 1, D-3 = C): price adjustments — bonuses,
penalties, scope changes. `assumptions_used.price_adjustments` is `not_included` on every answer,
and the figure is described as the agreed price (`price_basis`), never as "price + approved
adjustments". Milestones (D-1 = A) and the allocation of the revenue to periods (SC-4-05) are out
of scope as well: the answer is one whole-project figure.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.core.money import round_money
from app.domain.revenue import (
    CURRENCY_MISMATCH,
    INCOMPLETE_COMMERCIAL_TERMS,
    FixedPriceAssumptionsUsed,
    RevenueAnswer,
    RevenueResult,
    RevenueUnavailable,
)
from app.models.commercial_terms import MODEL_TYPE_FIXED_PRICE


@dataclass(frozen=True)
class AgreedPrice:
    """The Fixed Price details row as the formula sees it: an amount and its ISO 4217 currency.

    Nothing else travels on this type, on purpose — a formula that cannot see hours, rates or costs
    cannot let them move the revenue (F-06.2; criterion K-01).
    """

    amount: Decimal
    currency: str


def fixed_price_revenue(
    price: AgreedPrice | None,
    *,
    scenario_currency: str | None,
) -> RevenueAnswer:
    """The Fixed Price revenue of one scenario, or the named state that withholds it.

    1. **No details row → `incomplete_commercial_terms`** (ADR-0003, point 3; addendum SC-4-02,
       point 1): the database enforces the *type* of a details row, not its existence. Never `0`.
    2. **The price's currency differs from `scenarios.currency` (when that is set) →
       `currency_mismatch`** (addendum SC-4-02, point 4). Nothing is converted — `exchange_rates`
       (ADR-0006) does not exist. `no_rate` and `no_revenue_currency` are unreachable for this model
       (no rates; the price always carries its currency) and no new state is invented for them.
    3. Otherwise the agreed price, rounded **once** through `app.core.money.round_money` (ADR-0002,
       rule 2) — the only arithmetic here, and the same single rounding point every model uses.

    `assumptions_used` names the rule's own row as the source (`rate_source = fixed_price_terms`)
    and `not_applicable` for hours and the vendor axis, on every answer and whatever the scenario's
    status (the SC-4-04 pattern; decision of 2026-09-25 on Issue #66) — the defaults of
    `FixedPriceAssumptionsUsed`, so no caller can pass anything else.
    """
    assumptions = FixedPriceAssumptionsUsed(
        model_type=MODEL_TYPE_FIXED_PRICE,
        currencies=() if price is None else (price.currency,),
    )
    if price is None:
        return RevenueUnavailable(reason=INCOMPLETE_COMMERCIAL_TERMS, assumptions_used=assumptions)
    if scenario_currency is not None and price.currency != scenario_currency:
        return RevenueUnavailable(reason=CURRENCY_MISMATCH, assumptions_used=assumptions)
    return RevenueResult(
        revenue=round_money(price.amount), currency=price.currency, assumptions_used=assumptions
    )
