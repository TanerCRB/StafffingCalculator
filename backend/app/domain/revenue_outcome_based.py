"""Outcome-based revenue: fixed fee + binary bonus + per-unit rate, bounded by min/max
(F-06.3; ADR-0003, addendum 2026-09-25 SC-4-03).

A pure function of what the data layer already read — no `Session`, no clock, no catalogue. **Does
not import anything from another commercial model or from any cost calculation** (F-06: independent
calculation per model) and receives nothing about staffing, allocation or the catalogue: the input
type `OutcomeTermsInput` has no field for that.

Formula (points 2, 5, 6):

    component_k  = success_bonus · [k ∈ {achieved, exceeded}] + unit_rate · units_k
    r_k          = bound(fixed_fee + component_k, min, max)        — unrounded
    guaranteed   = bound(fixed_fee, min, max)                      — rounded once
    expected     = Σ_k (p_k / 100) · r_k                           — rounded once, at the end

A component that is absent (`None`) is not a `0` entered by the user, but within the sum means "no
component"; for min/max, `None` means "no bound on that side", never "bound to zero".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.core.money import round_money
from app.domain.revenue import (
    CURRENCY_MISMATCH,
    EXPECTED_CALCULATED,
    HOURS_SOURCE_NOT_APPLICABLE,
    INCOMPLETE_COMMERCIAL_TERMS,
    NO_PROBABILITIES,
    RATE_SOURCE_NOT_APPLICABLE,
    VENDOR_AXIS_NOT_APPLICABLE,
    AssumptionsUsed,
    CategoryRevenue,
    RevenueAnswer,
    RevenueResult,
    RevenueUnavailable,
)
from app.models.commercial_terms import MODEL_TYPE_OUTCOME_BASED

BONUS_CATEGORIES: Final = frozenset({"achieved", "exceeded"})
"""Categories for which the binary bonus is paid — "partial" **does not** get it (D-1)."""

_HUNDRED: Final = Decimal("100")


@dataclass(frozen=True)
class OutcomeCategoryInput:
    """One result category: how many units were achieved (a manual entry) and with what
    probability (percent, optional). `units` is `None` when the rule has no per-unit rate and the
    user did not give any units — never `0` for absence."""

    category: str
    units: Decimal | None
    probability: Decimal | None


@dataclass(frozen=True)
class OutcomeTermsInput:
    """An Outcome-based rule exactly as the user entered it — nothing beyond the scenario."""

    currency: str
    fixed_fee: Decimal
    success_bonus: Decimal | None
    unit_rate: Decimal | None
    revenue_min: Decimal | None
    revenue_max: Decimal | None
    categories: Sequence[OutcomeCategoryInput]


def outcome_assumptions(currencies: tuple[str, ...] = ()) -> AssumptionsUsed:
    """The assumptions of an Outcome-based revenue: no rate source, no hours source, no vendor axis.

    `assumptions_used` names only what the calculation actually reads (F-06.5; ADR-0003, addendum
    SC-4-03, point 8) — hence `not_applicable` in the three source fields, instead of pretending a
    catalogue lookup.
    """
    return AssumptionsUsed(
        model_type=MODEL_TYPE_OUTCOME_BASED,
        rate_source=RATE_SOURCE_NOT_APPLICABLE,
        hours_source=HOURS_SOURCE_NOT_APPLICABLE,
        vendor_axis=VENDOR_AXIS_NOT_APPLICABLE,
        currencies=currencies,
    )


def _bounded(value: Decimal, terms: OutcomeTermsInput) -> Decimal:
    """Bound the **entire** revenue to [min, max] (D-5); `None` means no bound from that side."""
    if terms.revenue_min is not None and value < terms.revenue_min:
        value = terms.revenue_min
    if terms.revenue_max is not None and value > terms.revenue_max:
        value = terms.revenue_max
    return value


def _category_revenue(terms: OutcomeTermsInput, category: OutcomeCategoryInput) -> Decimal:
    """The unrounded revenue of one category after the min/max bound."""
    variable = Decimal("0")
    if terms.success_bonus is not None and category.category in BONUS_CATEGORIES:
        variable += terms.success_bonus
    if terms.unit_rate is not None and category.units is not None:
        variable += terms.unit_rate * category.units
    return _bounded(terms.fixed_fee + variable, terms)


def outcome_based_revenue(
    terms: OutcomeTermsInput, *, scenario_currency: str | None
) -> RevenueAnswer:
    """The guaranteed revenue (as `revenue`), the expected one and per category — or a named state.

    1. **The rule's currency differs from the scenario's currency** (when the latter is set) →
       `currency_mismatch`, with no amount in any field and no conversion (point 7). The
       comparison with the cost currency, when the scenario has no currency, belongs to
       `app.domain.scenario_results` (R-01).
    1a. **A per-unit rate with no unit count for any category** →
       `incomplete_commercial_terms`, never a multiplication by `0`. The database does not allow
       this (`ck_outcome_terms_units_given_with_unit_rate`); this branch protects the pure
       function against input from outside the database.
    2. Guaranteed revenue: the fixed fee after the min/max bound, rounded once (point 5a, 6).
    3. Expected revenue: from the **unrounded** r_k, rounded once, at the end (point 5b). No
       probabilities (the database guarantees "all or none") → the named state
       `no_probabilities`, never `0` and never a copy of the guaranteed one (point 5c).
    """
    assumptions = outcome_assumptions((terms.currency,))
    if scenario_currency is not None and terms.currency != scenario_currency:
        return RevenueUnavailable(reason=CURRENCY_MISMATCH, assumptions_used=assumptions)
    if terms.unit_rate is not None and any(
        category.units is None for category in terms.categories
    ):
        return RevenueUnavailable(
            reason=INCOMPLETE_COMMERCIAL_TERMS, assumptions_used=assumptions
        )

    unrounded = [(category, _category_revenue(terms, category)) for category in terms.categories]
    guaranteed = round_money(_bounded(terms.fixed_fee, terms))
    category_revenues = tuple(
        CategoryRevenue(
            category=category.category,
            units=category.units,
            probability=category.probability,
            revenue=round_money(revenue),
        )
        for category, revenue in unrounded
    )
    if any(category.probability is None for category in terms.categories):
        return RevenueResult(
            revenue=guaranteed,
            currency=terms.currency,
            assumptions_used=assumptions,
            expected_state=NO_PROBABILITIES,
            category_revenues=category_revenues,
        )
    expected = sum(
        (
            category.probability / _HUNDRED * revenue
            for category, revenue in unrounded
            if category.probability is not None  # always true here; narrows the type
        ),
        Decimal("0"),
    )
    return RevenueResult(
        revenue=guaranteed,
        currency=terms.currency,
        assumptions_used=assumptions,
        expected_revenue=round_money(expected),
        expected_state=EXPECTED_CALCULATED,
        category_revenues=category_revenues,
    )
