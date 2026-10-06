"""Period-attributed scenario results for SC-7-11.

Periodless amounts are kept in a separate unallocated object by the response shaper. This module
only aggregates existing dated component amounts; it does not invent a timing basis.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.money import NOT_APPLICABLE, ratio_percent, round_money


@dataclass(frozen=True)
class PeriodProfitability:
    revenue: Decimal | str
    personnel_cost: Decimal | str
    additional_cost: Decimal | str
    period_cost: Decimal | str
    profit: Decimal | str
    margin: Decimal | str
    state: str
    below_target_margin: bool | None
    negative_profit: bool | None


def month_starts(start: date | None, end: date | None, observed: set[date]) -> tuple[date, ...]:
    """Months intersecting the scenario interval; for incomplete dates, use observed months."""
    if start is None or end is None:
        return tuple(sorted(month.replace(day=1) for month in observed))
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    months: list[date] = []
    while cursor <= last:
        months.append(cursor)
        year = cursor.year + (cursor.month == 12)
        month = 1 if cursor.month == 12 else cursor.month + 1
        cursor = date(year, month, 1)
    return tuple(months)


def unallocated_amount(items: tuple[tuple[date | None, Decimal, str], ...]) -> Decimal:
    return round_money(
        sum((amount for period, amount, _currency in items if period is None), Decimal("0"))
    )


def period_profitability(
    *,
    revenue: Decimal,
    base_cost: Decimal,
    paid_absence_cost: Decimal,
    assigned_fte_cost: Decimal,
    additional_cost: Decimal,
    target_margin_percent: Decimal | None,
    source_states: tuple[bool, ...],
    currency_mismatch: bool,
    cost_currency_mismatch: bool,
) -> PeriodProfitability:
    (
        revenue_available,
        base_available,
        absence_available,
        assigned_available,
        additional_available,
    ) = source_states
    personnel_available = base_available and absence_available and assigned_available
    personnel = (
        round_money(base_cost + paid_absence_cost + assigned_fte_cost)
        if personnel_available
        else NOT_APPLICABLE
    )
    additional = round_money(additional_cost) if additional_available else NOT_APPLICABLE
    period_cost = (
        round_money(personnel + additional_cost)
        if personnel_available and additional_available and not cost_currency_mismatch
        else NOT_APPLICABLE
    )
    profit = (
        round_money(revenue - period_cost)
        if revenue_available
        and isinstance(period_cost, Decimal)
        and not currency_mismatch
        else NOT_APPLICABLE
    )
    margin = ratio_percent(profit, revenue) if isinstance(profit, Decimal) else NOT_APPLICABLE
    below_target = (
        margin < target_margin_percent
        if isinstance(margin, Decimal) and target_margin_percent is not None
        else None
    )
    state = (
        "not_applicable"
        if not all(source_states)
        else "currency_mismatch"
        if currency_mismatch or cost_currency_mismatch
        else "calculated"
    )
    return PeriodProfitability(
        revenue=round_money(revenue) if revenue_available else NOT_APPLICABLE,
        personnel_cost=personnel,
        additional_cost=additional,
        period_cost=period_cost,
        profit=profit,
        margin=margin,
        state=state,
        below_target_margin=below_target,
        negative_profit=(profit < 0) if isinstance(profit, Decimal) else None,
    )
