"""Directed exchange-rate selection and Decimal conversion (F-02, ADR-0006)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.money import round_money


@dataclass(frozen=True)
class EffectiveExchangeRate:
    source_currency: str
    target_currency: str
    effective_from: date
    effective_to: date | None
    value: Decimal
    source: str
    scope: str


@dataclass(frozen=True)
class PeriodMoney:
    period: date | None
    amount: Decimal
    currency: str


def rate_for_period(
    rates: Sequence[EffectiveExchangeRate],
    *,
    source_currency: str,
    target_currency: str,
    period: date,
) -> EffectiveExchangeRate | None:
    """Resolve the most specific direct pair effective on `period`; never invert a pair."""
    matches = [
        rate
        for rate in rates
        if rate.source_currency == source_currency
        and rate.target_currency == target_currency
        and rate.effective_from <= period
        and (rate.effective_to is None or period <= rate.effective_to)
    ]
    precedence = {"scenario": 3, "project": 2, "organization": 1}
    return max(matches, key=lambda item: precedence[item.scope], default=None)


def convert_period_amounts(
    amounts_by_period: Sequence[tuple[date, Decimal]],
    rates: Sequence[EffectiveExchangeRate],
    *,
    source_currency: str,
    target_currency: str,
) -> Decimal | None:
    """Convert every dated amount or return no total when any period has no applicable rate."""
    converted: list[Decimal] = []
    for period, amount in amounts_by_period:
        rate = rate_for_period(
            rates,
            source_currency=source_currency,
            target_currency=target_currency,
            period=period,
        )
        if rate is None:
            return None
        converted.append(amount * rate.value)
    return round_money(sum(converted, Decimal("0")))


def convert_component_periods(
    amounts: Sequence[PeriodMoney],
    rates: Sequence[EffectiveExchangeRate],
    *,
    target_currency: str,
    fallback_period: date | None,
) -> Decimal | None:
    """Convert a complete component, using its own periods or the approved fallback date."""
    converted: list[Decimal] = []
    for item in amounts:
        if item.currency == target_currency:
            converted.append(item.amount)
            continue
        period = item.period if item.period is not None else fallback_period
        if period is None:
            return None
        rate = rate_for_period(
            rates,
            source_currency=item.currency,
            target_currency=target_currency,
            period=period,
        )
        if rate is None:
            return None
        converted.append(item.amount * rate.value)
    return round_money(sum(converted, Decimal("0")))


def convert_amount(
    amount: Decimal,
    currency: str,
    rates: Sequence[EffectiveExchangeRate],
    *,
    target_currency: str,
    period: date | None,
    fallback_period: date | None,
) -> Decimal | None:
    """Convert one amount at its own date, or the scenario's approved fallback date."""
    return convert_component_periods(
        (PeriodMoney(period=period, amount=amount, currency=currency),),
        rates,
        target_currency=target_currency,
        fallback_period=fallback_period,
    )
