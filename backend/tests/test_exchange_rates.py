from datetime import date
from decimal import Decimal

from app.domain.exchange_rates import EffectiveExchangeRate, convert_period_amounts, rate_for_period


def _rate(scope: str, start: date, end: date | None, value: str = "1.5") -> EffectiveExchangeRate:
    return EffectiveExchangeRate(
        source_currency="EUR",
        target_currency="USD",
        effective_from=start,
        effective_to=end,
        value=Decimal(value),
        source=f"{scope} input",
        scope=scope,
    )


def test_k_01_resolves_the_most_specific_direct_rate_for_each_effective_date() -> None:
    rates = [
        _rate("organization", date(2026, 1, 1), None, "1.1"),
        _rate("project", date(2026, 3, 1), date(2026, 4, 30), "1.2"),
        _rate("scenario", date(2026, 4, 1), date(2026, 4, 30), "1.3"),
    ]

    assert (
        rate_for_period(
            rates, source_currency="EUR", target_currency="USD", period=date(2026, 2, 1)
        )
        == rates[0]
    )
    assert (
        rate_for_period(
            rates, source_currency="EUR", target_currency="USD", period=date(2026, 3, 1)
        )
        == rates[1]
    )
    assert (
        rate_for_period(
            rates, source_currency="EUR", target_currency="USD", period=date(2026, 4, 1)
        )
        == rates[2]
    )
    assert (
        rate_for_period(
            rates, source_currency="EUR", target_currency="USD", period=date(2027, 1, 1)
        )
        == rates[0]
    )


def test_k_01_does_not_invert_a_pair_or_use_a_rate_outside_its_window() -> None:
    rates = [_rate("organization", date(2026, 1, 1), date(2026, 1, 31))]

    assert (
        rate_for_period(
            rates, source_currency="USD", target_currency="EUR", period=date(2026, 1, 15)
        )
        is None
    )
    assert (
        rate_for_period(
            rates, source_currency="EUR", target_currency="USD", period=date(2026, 2, 1)
        )
        is None
    )


def test_k_02_converts_each_period_and_rounds_the_complete_total_once() -> None:
    rates = [
        _rate("organization", date(2026, 1, 1), date(2026, 1, 31), "1.25"),
        _rate("organization", date(2026, 2, 1), date(2026, 2, 28), "1.5"),
    ]

    assert convert_period_amounts(
        [(date(2026, 1, 1), Decimal("10.005")), (date(2026, 2, 1), Decimal("10.005"))],
        rates,
        source_currency="EUR",
        target_currency="USD",
    ) == Decimal("27.51")


def test_k_04_one_missing_period_rate_withholds_the_whole_total() -> None:
    rates = [_rate("organization", date(2026, 1, 1), date(2026, 1, 31))]

    assert (
        convert_period_amounts(
            [(date(2026, 1, 1), Decimal("10")), (date(2026, 2, 1), Decimal("20"))],
            rates,
            source_currency="EUR",
            target_currency="USD",
        )
        is None
    )
