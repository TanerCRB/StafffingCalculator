"""Story Points revenue: `price_per_point × accepted_points`, nothing else (F-06.4, SC-4-04).

A pure function — no `Session`, no clock, no catalogue lookup, no allocation row. Both inputs come
from one row of `story_points_terms` (`app.data.commercial_terms`), written once at rule creation
(ADR-0003 addendum 2026-09-25, D-5/A) — there is no per-month resolution to do here, unlike Time &
Material, because a Story Points rule prices no rate window and reads no date.

**Imports nothing of another commercial model and nothing of any cost calculation** (F-06:
independent calculation per model; rule 10 of the Invariant Guardian). In particular this module
does not import `app.domain.revenue_time_and_material`, and no argument here is an hour or a
catalogue rate — criterion K-02 (no Story Points ↔ hours conversion) is proven by this function
having no parameter billable hours could travel through.
"""

from decimal import Decimal

from app.core.money import round_money
from app.domain.revenue import (
    CURRENCY_MISMATCH,
    HOURS_SOURCE_NOT_APPLICABLE,
    RATE_SOURCE_STORY_POINTS_TERMS,
    VENDOR_AXIS_NOT_APPLICABLE,
    AssumptionsUsed,
    RevenueAnswer,
    RevenueResult,
    RevenueUnavailable,
)
from app.models.commercial_terms import MODEL_TYPE_STORY_POINTS


def story_points_assumptions(currencies: tuple[str, ...] = ()) -> AssumptionsUsed:
    """The one source triple of a Story Points answer — `story_points_terms` / `not_applicable` /
    `not_applicable` (ADR-0003, aneks SC-4-07, pkt 5a) — on **every** shape of it: a result, a
    `currency_mismatch`, and the caller's `incomplete_commercial_terms` for a rule without its
    details row (`app.data.commercial_terms._story_points`). One place, so the named state can never
    fall back to the T&M defaults of `AssumptionsUsed` (`billable_hours`/`internal`) — a hybrid this
    model does not have (weryfikacja SC-4-07, R-01). The counterpart of `outcome_assumptions`.
    """
    return AssumptionsUsed(
        model_type=MODEL_TYPE_STORY_POINTS,
        rate_source=RATE_SOURCE_STORY_POINTS_TERMS,
        hours_source=HOURS_SOURCE_NOT_APPLICABLE,
        vendor_axis=VENDOR_AXIS_NOT_APPLICABLE,
        currencies=currencies,
    )


def story_points_revenue(
    *,
    price_per_point: Decimal,
    accepted_points: int,
    currency: str,
    scenario_currency: str | None,
) -> RevenueAnswer:
    """The Story Points revenue of one rule — a result, or the one named state this model can reach.

    Unlike Time & Material there is no `no_rate` and no `no_revenue_currency` for this formula to
    reach: `price_per_point`, `accepted_points` and `currency` are `NOT NULL` columns of the one row
    this function is given, written by the same guarded statement that wrote the rule (ADR-0003,
    point 3) — there is no month, no window and no empty plan for this model to have a gap in
    (ADR-0003 point 9's ban on a partial revenue has nothing to apply to here: the whole computation
    is one multiplication of two values that are either both present or the rule is
    `incomplete_commercial_terms`, decided by the caller before this function runs — see
    `app.data.commercial_terms`).

    **`currency_mismatch` it can reach** (reviewer R-01 of SC-4-04, applying the rule already
    identical across `revenue_time_and_material.py`, `personnel_cost.py`, `paid_absence_cost.py` and
    `additional_cost.py` — point 8 of ADR-0003): a rule's own `currency` need not equal the
    scenario's declared `currency`, and nothing converts between them (`exchange_rates`, ADR-0006,
    does not exist). Left unchecked, `scenario_profitability` would silently subtract a Story Points
    revenue in one currency from costs in another — an incorrect-billing bug rather than a visible
    one, because `profit`/`margin`/`markup` all still look like ordinary numbers.

    No intermediate rounding: the one product is rounded once, through `app.core.money.round_money`
    (ADR-0002, rule 2; criterion K-01 — 25 accepted points × 1000 = exactly 25000.00).
    """
    currencies = (currency,)
    assumptions = story_points_assumptions(currencies)
    if scenario_currency is not None and currencies != (scenario_currency,):
        return RevenueUnavailable(reason=CURRENCY_MISMATCH, assumptions_used=assumptions)
    revenue = round_money(Decimal(accepted_points) * price_per_point)
    return RevenueResult(revenue=revenue, currency=currency, assumptions_used=assumptions)
