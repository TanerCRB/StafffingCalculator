"""SC-4-03, verification round 2, R-01 — profit, margin and markup never from amounts in two
currencies.

A scenario **without a currency** (`scenarios.currency IS NULL`): each of the four components
compares its own currency only against the scenario's currency, so on its own it refuses nothing.
The only place that sees all four currencies is `app.domain.scenario_results.scenario_profitability`
— called by `/results`, what-if and the SC-6-02 comparison. Every test has a contrast: the same
data in one currency gives a number, so the named state does not come from something other than
the currency.

- an Outcome-based rule in EUR + costs in PLN → `profitability_state = currency_mismatch`, four
  fields `"n/a"`; revenue on its own `calculated` (EUR) — that is not its state;
- the same rule in PLN → numbers (profit 6000.00);
- T&M: additional cost in EUR + personnel cost in PLN → `currency_mismatch` (the case from before
  SC-4-03); everything in PLN → numbers;
- what-if and the comparison — the same answer.

Real PostgreSQL, real endpoints.
"""

import uuid
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import caller_holding, make_outcome_terms
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)
from tests.test_scenario_results_compare import compare_path
from tests.test_scenario_what_if import what_if_path

WITHHELD = {
    "included_cost": "n/a",
    "profit": "n/a",
    "margin": "n/a",
    "markup": "n/a",
    "profitability_state": "currency_mismatch",
}
# 100 h × 200 = 20000 T&M revenue (and guaranteed Outcome AC-08: 20000); 100 h × 120 = 12000
# personnel cost, 2000 additional cost, 0 absence → 14000; profit 6000; 30.00%; 42.86%.
CALCULATED = {
    "included_cost": "14000.00",
    "profit": "6000.00",
    "margin": "30.00",
    "markup": "42.86",
    "profitability_state": "calculated",
}


def _aggregate(body: dict[str, Any]) -> dict[str, Any]:
    return {field: body[field] for field in WITHHELD}


def _without_currency(session: Session, *, name: str, **kwargs: Any):
    """`_full_scenario` (PLN rate, additional cost 2000), then the scenario's currency removed —
    the components keep the currency of their own data, the scenario declares none."""
    project, scenario, _ = _full_scenario(session, name=name, **kwargs)
    scenario.currency = None
    session.flush()
    return project, scenario


def _results(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    with caller_holding(*EVERYTHING):
        response = client.get(results_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()


def test_r_01_an_eur_outcome_rule_with_pln_costs_is_currency_mismatch_never_a_number(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — an Outcome-based rule in EUR, costs in PLN, a scenario without a currency: revenue
    20000.00 EUR `calculated`, personnel and additional cost `calculated` in PLN — and profit,
    margin, markup and included cost are `"n/a"` with the state `currency_mismatch`. Mutation:
    removing the currency comparison in `scenario_profitability` → profit `6000.00`
    (20000 EUR − 14000 PLN)."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 outcome EUR", create_commercial_terms=False
    )
    make_outcome_terms(db_session, scenario, currency="EUR")

    body = _results(client, project.id, scenario.id)

    assert (body["revenue"]["state"], body["revenue"]["currency"]) == ("calculated", "EUR")
    assert (body["personnel_cost"]["state"], body["personnel_cost"]["currency"]) == (
        "calculated",
        "PLN",
    )
    assert (body["additional_cost"]["state"], body["additional_cost"]["currency"]) == (
        "calculated",
        "PLN",
    )
    assert _aggregate(body) == WITHHELD


def test_r_01_contrast_the_same_outcome_rule_in_pln_gives_numbers(
    client: TestClient, db_session: Session
) -> None:
    """R-01, contrast — the same rule and the same costs, the rule in PLN: numbers and the state
    `calculated`. Proves that the state above comes from the currency, not from the scenario
    lacking a currency nor from the model."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 outcome PLN", create_commercial_terms=False
    )
    make_outcome_terms(db_session, scenario, currency="PLN")

    assert _aggregate(_results(client, project.id, scenario.id)) == CALCULATED


def test_r_01_an_eur_additional_cost_with_pln_personnel_cost_is_currency_mismatch(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — the case from before SC-4-03, T&M: additional cost 2000 EUR alongside personnel cost
    and revenue in PLN, a scenario without a currency → `currency_mismatch`, not `6000.00` from
    summing PLN and EUR."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 TM additional EUR", additional_currency="EUR"
    )

    body = _results(client, project.id, scenario.id)

    assert body["revenue"]["state"] == "calculated"
    assert body["additional_cost"]["currency"] == "EUR"
    assert _aggregate(body) == WITHHELD


def test_r_01_contrast_tm_all_in_pln_without_a_scenario_currency_gives_numbers(
    client: TestClient, db_session: Session
) -> None:
    """R-01, contrast — T&M, everything in PLN, a scenario without a currency: numbers as before.
    Proves that the fix does not turn every scenario without a currency into a named state."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(db_session, name="R01 TM PLN")

    assert _aggregate(_results(client, project.id, scenario.id)) == CALCULATED


def test_r_01_the_what_if_and_the_comparison_answer_the_same_named_state(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — the what-if (SC-6-04, `+0`) and the comparison (SC-6-02) compose profit through the
    same function: an EUR rule + PLN costs → `currency_mismatch` in both; a PLN row in the same
    comparison — numbers."""
    _ensure_statutory_bypass(db_session)
    project, mismatched = _without_currency(
        db_session, name="R01 what-if EUR", create_commercial_terms=False
    )
    make_outcome_terms(db_session, mismatched, currency="EUR")

    with caller_holding(*EVERYTHING):
        what_if = client.get(what_if_path(project.id, mismatched.id, "0"))
        compared = client.get(compare_path(project.id, mismatched.id))

    assert what_if.status_code == 200, what_if.text
    assert _aggregate(what_if.json()) == WITHHELD
    assert compared.status_code == 200, compared.text
    assert [_aggregate(row) for row in compared.json()["results"]] == [WITHHELD]


def test_r_01_the_state_is_not_gated_while_the_four_figures_are(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — `profitability_state` is not a number, so it stays for a caller without the right to
    personnel costs; the four fields are `null` as before (the SC-7-01 gate unchanged)."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 gated", create_commercial_terms=False, cost_visible=False
    )
    make_outcome_terms(db_session, scenario, currency="EUR")

    body = _results(client, project.id, scenario.id)

    assert _aggregate(body) == {
        "included_cost": None,
        "profit": None,
        "margin": None,
        "markup": None,
        "profitability_state": "currency_mismatch",
    }
    assert Decimal(body["revenue"]["amount"]) == Decimal("20000.00")
