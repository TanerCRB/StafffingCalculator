"""SC-4-03, verification round 2 — QA tests closing the proof of R-01 and point 5 (units).

Each test is a contrast: the same situation with one thing changed and the result reversed.

- **R-01, each cost source separately**: `scenario_profitability` with four `calculated`
  components, of which **exactly one** has a different currency — in turn revenue, base cost,
  paid-absence cost, additional cost → `currency_mismatch`; the same four in one currency →
  numbers. The endpoint tests (`tests/test_profitability_currency.py`) only vary revenue and
  additional cost. The absence cost's currency cannot be made to diverge from the base cost's
  currency through the endpoint: it takes its rates from the base cost's monthly rates
  (`MonthCostRate`, `app.data.paid_absence_cost`), and a base cost in several currencies is itself
  `currency_mismatch` — so this source's share of the comparison is proved here, on the pure
  function (defence in depth, not a scenario reachable today).
- **R-01, rule order**: a component that is not `calculated` → `not_applicable`, even when the
  others have different currencies (the absence of a figure wins over a currency mismatch);
  through the endpoint — a scenario without a commercial rule has `profitability_state =
  not_applicable`, the same scenario with a rule — `calculated`.
- **Point 5, model and database**: the set of `CHECK` names on the `outcome_terms` table in the
  database (after migration) equals the set from the model — dropping
  `ck_outcome_terms_units_given_with_unit_rate` from only the model or only the migration is
  visible.
- **R-04, `revenue_max` on read**: a rule with an upper bound, without a lower one — the mirror of
  the developer's test.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.additional_cost import AdditionalCostResult
from app.domain.assigned_fte_cost import AssignedFteCostResult
from app.domain.fixed_amount_cost import FixedAmountCostResult
from app.domain.paid_absence_cost import PaidAbsenceCostResult, PaidAbsenceCostUnavailable
from app.domain.personnel_cost import PersonnelCostResult
from app.domain.revenue import RevenueResult
from app.domain.scenario_results import scenario_profitability
from app.models.commercial_terms import OutcomeTerms
from tests.conftest import caller_holding, outcome_payload
from tests.test_outcome_terms_read_and_units import _get, _post, _scenario
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)

_ASSUMPTIONS: object = object()
"""`scenario_profitability` does not read `assumptions_used` — a stand-in, not made-up
assumptions."""

_SOURCES = (
    "revenue",
    "base_cost",
    "paid_absence",
    "additional_cost",
    "fixed_amount",
    "assigned_fte",
)


def _components(**currencies: str) -> dict[str, object]:
    """Six `calculated` components, each in PLN unless `currencies` says otherwise."""
    currency = {source: currencies.get(source, "PLN") for source in _SOURCES}
    return {
        "revenue": RevenueResult(
            revenue=Decimal("20000.00"),
            currency=currency["revenue"],
            assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
        ),
        "base_cost": PersonnelCostResult(
            cost=Decimal("12000.00"),
            currency=currency["base_cost"],
            assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
        ),
        "paid_absence": PaidAbsenceCostResult(
            cost=Decimal("500.00"),
            budget_cost=Decimal("0.00"),
            currency=currency["paid_absence"],
            assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
        ),
        "additional_cost": AdditionalCostResult(
            amount=Decimal("1500.00"),
            currency=currency["additional_cost"],
            assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
        ),
        "fixed_amount": FixedAmountCostResult(
            cost=Decimal("0.00"), currency=currency["fixed_amount"], assumptions_used=_ASSUMPTIONS
        ),
        "assigned_fte": AssignedFteCostResult(
            cost=Decimal("0.00"), currency=currency["assigned_fte"], assumptions_used=_ASSUMPTIONS
        ),
    }


def _four(result) -> tuple[object, ...]:
    return (result.included_cost, result.profit, result.margin, result.markup, result.state)


WITHHELD_MISMATCH = ("n/a", "n/a", "n/a", "n/a", "currency_mismatch")


@pytest.mark.parametrize("odd_source", _SOURCES)
def test_r_01_one_source_in_another_currency_is_currency_mismatch_whichever_it_is(
    odd_source: str,
) -> None:
    """R-01 — exactly one of the four sources in EUR, the rest in PLN → `currency_mismatch` and
    `"n/a"` on four fields. Mutations: omitting any one source's currency from the comparison
    (e.g. only revenue vs base cost) — the parameter for that source gives a number."""
    result = scenario_profitability(**_components(**{odd_source: "EUR"}))  # type: ignore[arg-type]

    assert _four(result) == WITHHELD_MISMATCH


def test_r_01_contrast_the_same_four_sources_in_one_currency_give_numbers() -> None:
    """R-01, contrast — the same amounts, all in PLN (or all in EUR): numbers and
    `calculated`. Included cost 14000.00, profit 6000.00, margin 30.00, markup 42.86."""
    for currency in ("PLN", "EUR"):
        result = scenario_profitability(
            **_components(**dict.fromkeys(_SOURCES, currency))  # type: ignore[arg-type]
        )
        assert _four(result) == (
            Decimal("14000.00"),
            Decimal("6000.00"),
            Decimal("30.00"),
            Decimal("42.86"),
            "calculated",
        )


def test_r_01_a_component_not_calculated_is_not_applicable_even_beside_mixed_currencies() -> None:
    """R-01, rule order — the absence cost not `calculated`, and revenue in EUR beside costs in
    PLN: state `not_applicable`, not `currency_mismatch` — you cannot speak of the currency of a
    figure that does not exist. Mutation: checking currencies before the `calculated` gate (or a
    constant `currency_mismatch` in the "not calculated" branch) → `currency_mismatch`."""
    components = _components(revenue="EUR")
    components["paid_absence"] = PaidAbsenceCostUnavailable(
        reason="no_cost_rate",
        assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
    )

    result = scenario_profitability(**components)  # type: ignore[arg-type]

    assert _four(result) == ("n/a", "n/a", "n/a", "n/a", "not_applicable")


def test_r_01_a_scenario_without_a_commercial_rule_reads_profitability_state_not_applicable(
    client: TestClient, db_session: Session
) -> None:
    """R-01 through the endpoint — a scenario in PLN without a commercial rule: revenue
    `no_commercial_terms`, four fields `"n/a"`, `profitability_state = not_applicable`. Contrast in
    the same test: the same scenario **with** a T&M rule → `calculated`. The only difference is
    whether the rule exists.

    The first version of the proof (the developer's tests) never checked the `not_applicable`
    value anywhere — the mutation "every withholding described as `currency_mismatch`" survived."""
    _ensure_statutory_bypass(db_session)
    without, without_rule, _ = _full_scenario(
        db_session, name="R01 QA no rule", create_commercial_terms=False
    )
    with_rule_project, with_rule, _ = _full_scenario(db_session, name="R01 QA with rule")

    with caller_holding(*EVERYTHING):
        refused = client.get(results_path(without.id, without_rule.id))
        stated = client.get(results_path(with_rule_project.id, with_rule.id))

    assert refused.status_code == 200, refused.text
    body = refused.json()
    assert body["revenue"]["state"] == "no_commercial_terms"
    assert (
        body["included_cost"],
        body["profit"],
        body["margin"],
        body["markup"],
        body["profitability_state"],
    ) == ("n/a", "n/a", "n/a", "n/a", "not_applicable")

    assert stated.status_code == 200, stated.text
    assert stated.json()["profitability_state"] == "calculated"


def test_units_check_the_database_and_the_model_declare_the_same_outcome_terms_checks(
    db_session: Session,
) -> None:
    """Point 5 — the `CHECK` names on the `outcome_terms` table in the database after migration
    are exactly the names from the model (`OutcomeTerms.__table__`), including
    `ck_outcome_terms_units_given_with_unit_rate`. The expression-equality test
    (`test_the_model_and_the_outcome_migration_agree_on_every_sql_expression`) compares the
    constants, not whether the model and the migration declare a `CHECK` at all — dropping the
    declaration from just the model survived."""
    in_database = set(
        db_session.execute(
            sa.text(
                "SELECT conname FROM pg_constraint"
                " WHERE conrelid = 'outcome_terms'::regclass AND contype = 'c'"
            )
        ).scalars()
    )
    in_model = {
        constraint.name
        for constraint in OutcomeTerms.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }

    assert "ck_outcome_terms_units_given_with_unit_rate" in in_database
    assert in_database == in_model


def test_r_04_the_read_carries_revenue_max_when_it_was_written(
    client: TestClient, db_session: Session
) -> None:
    """R-04, the mirror of `test_r_04_the_read_carries_the_outcome_rule_parameters_as_written` —
    that one writes `revenue_min` without `revenue_max`, so `null` in `revenue_max` was the
    expected result there and the mutation "read always returns `revenue_max = null`" survived.
    Here it is the other way round: `revenue_max` given, `revenue_min` omitted →
    `"25000.0000"` and `null`. The only change from that test is which bound is given."""
    project, scenario = _scenario(db_session, "Outcome read max")

    written = _post(client, project, scenario, outcome_payload(revenue_max="25000"))
    assert written.status_code == 201, written.text
    read = _get(client, project, scenario)["commercial_terms"]["outcome_terms"]

    assert (read["revenue_min"], read["revenue_max"]) == (None, "25000.0000")
    assert written.json()["commercial_terms"]["outcome_terms"] == read
