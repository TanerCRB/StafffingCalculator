"""The fixed-amount personnel cost basis of a scenario
(F-07, SC-5-03; ADR-0013, addendum 2026-09-25).

A **second, independent formula** beside `app.domain.personnel_cost`'s worked-time one — not a
branch inside it. ADR-0013's addendum names the reason directly: "Two bases... have SEPARATE sets
of named states resolved by SEPARATE predicates, dispatched by the position's `cost_basis` —
never one shared predicate reading both sources at once." This module therefore:

- **reads nothing but a position's own `fixed_amount`/`fixed_amount_currency`** — no
  `planned_allocation_hours`, no catalogue rate, no window (criterion K-01);
- **imports nothing of the worked-time formula** (`app.domain.personnel_cost`,
  `app.data.personnel_cost`) and nothing of the revenue path (`app.data.commercial_terms`,
  `app.domain.revenue*`) — asserted structurally by `tests/test_fixed_amount_cost.py`'s mirror of
  control C-5 (ADR-0004, addendum 2026-09-23 SC-5-01). The states below (`CURRENCY_MISMATCH`,
  `NO_COST_CURRENCY`) are spelled again here, as their own constants, rather than imported from the
  worked-time module — the two mean the same idea but are two independent named vocabularies, so a
  change to one cannot silently reach into the other's answer.

**Two shapes, never a third** (ADR-0013, point 2; addendum 2026-09-25 SC-5-03, point 1, which
applies "two shapes, never a third" to this basis by direct quotation of ADR-0014 point 7): a
`FixedAmountCostResult` (an amount, its currency, what it depends on) or a
`FixedAmountCostUnavailable` (a named reason and what caused it). No amount lives on the second
shape, so "no currency to state it in" can never be read as a cost of `0`.

**Nothing here decides who may see the figure** (ADR-0005, addendum 2026-09-25 SC-5-03) — that is
`app.api.response_shaping`'s conjunction, applied to whatever `app.data.personnel_cost` builds from
this module's answer, identically to the worked-time amount.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.core.money import round_money

CURRENCY_MISMATCH: Final = "currency_mismatch"
"""The fixed amounts of this scenario's `fixed_amount` positions carry more than one currency
between them, or one other than `scenarios.currency` (when declared). No conversion — `ADR-0006`'s
`exchange_rates` does not exist and `1:1` would be invented (mirrors `ADR-0013`, point 2)."""

NO_COST_CURRENCY: Final = "no_cost_currency"
"""No `fixed_amount` position at all, in a scenario with no `scenarios.currency` — there is no
figure to take a currency from and the scenario declares none, so `0.00` would be `0.00` of
nothing, which a result may not be (ADR-0013, addendum 2026-09-25 SC-5-03, point 1, quoting ADR-0014
point 7 and mirroring the worked-time basis's `no_cost_currency`, addendum 2026-09-23 SC-5-01)."""

CALCULATED: Final = "calculated"
"""Not a named state — the label the API gives a `FixedAmountCostResult`."""

COST_BASIS_FIXED_AMOUNT: Final = "fixed_amount"
"""The value of `staffing_position.cost_basis` this formula answers for — spelled here, not
imported from `app.models.staffing`, so this module's only dependency on the ORM stays absent
(it is handed already-read rows, never a `Session` or a model class)."""


@dataclass(frozen=True)
class FixedAmountLine:
    """One `fixed_amount` position's contribution, as this formula sees it — nothing more.

    No `headcount`, no hours, no dimension tuple: at `headcount = 1` this amount already **is**
    the position's whole personnel cost (ADR-0005, addendum 2026-09-25 SC-5-03, point 2 — the
    reasoning that keeps `cost_basis`/`fixed_amount` out of the dimension-only staffing-positions
    response), so nothing else may enter the sum this formula does not already read.
    """

    position_id: uuid.UUID
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class FixedAmountAssumptionsUsed:
    """What a fixed-amount figure — or its absence — depends on, present on both shapes of the
    answer (F-06.5 applied to this basis, mirroring `CostAssumptionsUsed`)."""

    lines: tuple[FixedAmountLine, ...] = ()
    currencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class FixedAmountCostResult:
    """A stated fixed-amount cost: rounded once, through `app.core.money.round_money`."""

    cost: Decimal
    currency: str
    assumptions_used: FixedAmountAssumptionsUsed
    basis: str = COST_BASIS_FIXED_AMOUNT


@dataclass(frozen=True)
class FixedAmountCostUnavailable:
    """A named state: no fixed-amount cost can be stated, and `reason` says why. No amount here."""

    reason: str
    assumptions_used: FixedAmountAssumptionsUsed
    basis: str = COST_BASIS_FIXED_AMOUNT


FixedAmountCostAnswer = FixedAmountCostResult | FixedAmountCostUnavailable


def fixed_amount_cost(
    lines: Sequence[FixedAmountLine], *, scenario_currency: str | None
) -> FixedAmountCostAnswer:
    """The sum of a scenario's `fixed_amount` positions, or the named state that withholds it.

    Unlike the worked-time formula there is no "unresolved month" branch here at all: a
    `fixed_amount` position always carries an amount and a currency together (the database's own
    `fixed_amount_required_for_its_basis` CHECK makes the alternative unwritable — K-06), so the
    only two questions left are the ones about *currency*, in the same order the worked-time
    formula and `app.domain.additional_cost` already ask them:

    1. **More than one currency among the lines, or one other than the scenario's →
       `currency_mismatch`.** Nothing is converted (ADR-0006).
    2. **No lines at all** — the sum is `0.00` in the scenario's currency when it declares one,
       otherwise the named state `no_cost_currency`.
    3. Otherwise the sum, in `Decimal` with no intermediate rounding, rounded **once** through
       `app.core.money.round_money` (ADR-0002) — never per position.
    """
    currencies = tuple(sorted({line.currency for line in lines}))
    assumptions = FixedAmountAssumptionsUsed(lines=tuple(lines), currencies=currencies)

    if len(currencies) > 1 or (
        scenario_currency is not None and currencies and currencies != (scenario_currency,)
    ):
        return FixedAmountCostUnavailable(reason=CURRENCY_MISMATCH, assumptions_used=assumptions)

    currency = currencies[0] if currencies else scenario_currency
    if currency is None:
        return FixedAmountCostUnavailable(reason=NO_COST_CURRENCY, assumptions_used=assumptions)

    total = sum((line.amount for line in lines), Decimal("0"))
    return FixedAmountCostResult(
        cost=round_money(total), currency=currency, assumptions_used=assumptions
    )
