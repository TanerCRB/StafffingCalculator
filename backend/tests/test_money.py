from decimal import Decimal

from app.core.money import NOT_APPLICABLE, ratio_percent, round_money


def test_round_money_half_up() -> None:
    assert round_money(Decimal("10.005")) == Decimal("10.01")


def test_ratio_percent_ac01() -> None:
    """AC-01: 100 billable hours at PLN 200/h, personnel cost 12000, additional cost 2000
    -> revenue 20000, total cost 14000, profit 6000, margin 30%."""
    revenue = Decimal("20000")
    cost = Decimal("14000")
    profit = revenue - cost
    assert profit == Decimal("6000")
    assert ratio_percent(profit, revenue) == Decimal("30.00")


def test_ratio_percent_zero_denominator_is_not_applicable() -> None:
    """AC-05: revenue is zero -> margin is marked 'n/a', not a numeric value."""
    assert ratio_percent(Decimal("-500"), Decimal("0")) == NOT_APPLICABLE
