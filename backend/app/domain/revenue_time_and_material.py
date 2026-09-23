"""Time & Material revenue: Σ (`billable_hours` × selling rate) over positions and months (F-06.1).

A pure function of what the data layer read — no `Session`, no clock, no catalogue lookup. Whether a
month is priced, and at what, was decided *in SQL* by `app.data.commercial_terms` (one selling rate
over the whole month — ADR-0003, point 5, as corrected at gate 2, R-01), identically for the live
catalogue and for the approval snapshot; this module only multiplies, adds and names what could not
be priced.

**Imports nothing of another commercial model and nothing of any cost calculation** (F-06:
independent calculation per model). The shared vocabulary is `app.domain.revenue`.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.money import round_money
from app.domain.revenue import (
    CURRENCY_MISMATCH,
    NO_RATE,
    NO_REVENUE_CURRENCY,
    AssumptionsUsed,
    MonthPrice,
    RateWindow,
    RevenueAnswer,
    RevenueResult,
    RevenueUnavailable,
    UnresolvedMonth,
)
from app.models.commercial_terms import MODEL_TYPE_TIME_AND_MATERIAL


@dataclass(frozen=True)
class BillableMonth:
    """One allocation row as the T&M formula sees it: whose, which month, how many billable hours,
    and the price of the month — or `None` when no single selling rate covers the whole month.

    `billable_hours` is the planner's own figure for the **whole position** (headcount included), so
    it is never multiplied by the headcount again (ADR-0003, point 6). No planned or available hours
    travel on this type at all: a formula that cannot see them cannot derive billable hours from
    them (criterion K-02).
    """

    position_id: uuid.UUID
    period_month: date
    billable_hours: Decimal
    price: MonthPrice | None


def time_and_material_revenue(
    months: Sequence[BillableMonth],
    *,
    rate_source: str,
    scenario_currency: str | None,
) -> RevenueAnswer:
    """The T&M revenue of one scenario, or the named state that withholds it.

    The order of the checks is the order of what a reader can act on:

    1. **Any month not priced by one selling rate over the whole month → `no_rate`**, for the whole
       revenue, naming every (position, month) that caused it. Never the sum of the months that did
       resolve — that is a silently understated revenue (ADR-0003, point 9; criterion K-10) — and
       never a `0` product for the missing month. A month with zero billable hours still needs its
       rate: "is this month priced" is a question about the catalogue, not about the hours
       (pinned by `test_r_04_a_month_with_zero_billable_hours_and_no_rate_is_still_no_rate`).
    2. **More than one currency among the priced months, or one other than the scenario's →
       `currency_mismatch`** (point 8). Nothing is converted.
    3. **No allocation row at all** — nothing unpriced and nothing to add. The sum is `0.00`, but a
       result must name its currency (ADR-0003, point 9): the scenario's own when it declares one
       (`calculated`), otherwise the named state `no_revenue_currency` — there is no rate to take a
       currency from and nothing else to take it from either (R-04, a decision named in the report).
    4. Otherwise the sum, in `Decimal` with no intermediate rounding, rounded **once** through
       `app.core.money.round_money` (ADR-0002, rule 2).
    """
    windows = _distinct_windows(months)
    currencies = tuple(sorted({month.price.currency for month in months if month.price}))
    unresolved = tuple(
        UnresolvedMonth(position_id=month.position_id, period_month=month.period_month)
        for month in months
        if month.price is None
    )
    assumptions = AssumptionsUsed(
        model_type=MODEL_TYPE_TIME_AND_MATERIAL,
        rate_source=rate_source,
        rate_windows=windows,
        unresolved_months=unresolved,
        currencies=currencies,
    )
    if unresolved:
        return RevenueUnavailable(reason=NO_RATE, assumptions_used=assumptions)
    if len(currencies) > 1 or (
        scenario_currency is not None and currencies and currencies != (scenario_currency,)
    ):
        return RevenueUnavailable(reason=CURRENCY_MISMATCH, assumptions_used=assumptions)
    currency = currencies[0] if currencies else scenario_currency
    if currency is None:
        return RevenueUnavailable(reason=NO_REVENUE_CURRENCY, assumptions_used=assumptions)

    total = sum(
        (
            month.billable_hours * month.price.selling_rate
            for month in months
            if month.price is not None  # always true here; narrows the type
        ),
        Decimal("0"),
    )
    return RevenueResult(
        revenue=round_money(total), currency=currency, assumptions_used=assumptions
    )


def _distinct_windows(months: Sequence[BillableMonth]) -> tuple[RateWindow, ...]:
    """Every window used, once, in a stable order (start of the window, then its source id).

    Many months read one window and, since R-01, one month may read several; `assumptions_used`
    names each window once (ADR-0003, point 9), in an order that does not depend on the order rows
    came back in, so two reads are comparable.
    """
    distinct = {
        window.source_rate_id: window
        for month in months
        if month.price is not None
        for window in month.price.windows
    }
    return tuple(
        sorted(distinct.values(), key=lambda window: (window.effective_from, window.source_rate_id))
    )
