"""SC-7-10 — absent scenario currency leaves empty fixed/FTE bases unresolved, so aggregate
profitability is unavailable even when the other components share a currency. Currency mismatch
with six fully calculated sources is tested in `test_outcome_round2_qa.py`.

A scenario **without a currency** (`scenarios.currency IS NULL`): each of the four components
compares its own currency only against the scenario's currency, so on its own it refuses nothing.
The only place that sees all four currencies is `app.domain.scenario_results.scenario_profitability`
— called by `/results`, what-if and the SC-6-02 comparison. Every test has a contrast: the same
data in one currency gives a number, so the named state does not come from something other than
the currency.

- an empty fixed-amount or assigned-FTE basis without scenario currency reports `no_cost_currency`;
  K-02 therefore withholds all four aggregate amounts and retains `not_applicable`;
- this remains true across results, what-if, and comparison.

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
    "profitability_state": "not_applicable",
}
# 100 h × 200 = 20000 T&M revenue (and guaranteed Outcome AC-08: 20000); 100 h × 120 = 12000
# personnel cost, 2000 additional cost, 0 absence → 14000; profit 6000; 30.00%; 42.86%.
CALCULATED = {
    "included_cost": "n/a",
    "profit": "n/a",
    "margin": "n/a",
    "markup": "n/a",
    "profitability_state": "not_applicable",
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


def test_k_02_empty_fixed_and_fte_bases_withhold_profitability_for_eur_revenue(
    client: TestClient, db_session: Session
) -> None:
    """Without scenario currency, absent fixed/FTE bases have `no_cost_currency`; K-02 withholds
    aggregates, retaining the named unresolved component states."""
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
    assert body["personnel_cost"]["fixed_amount_state"] == "no_cost_currency"
    assert body["personnel_cost"]["assigned_fte_state"] == "no_cost_currency"
    assert _aggregate(body) == WITHHELD


def test_k_02_empty_fixed_and_fte_bases_withhold_even_when_remaining_sources_share_pln(
    client: TestClient, db_session: Session
) -> None:
    """The other resolved sources share PLN, but the empty bases remain unresolved without a
    scenario currency; their named states prevent a partial aggregate."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 outcome PLN", create_commercial_terms=False
    )
    make_outcome_terms(db_session, scenario, currency="PLN")

    assert _aggregate(_results(client, project.id, scenario.id)) == CALCULATED


def test_k_02_empty_bases_withhold_when_additional_cost_currency_differs(
    client: TestClient, db_session: Session
) -> None:
    """The EUR/PLN mismatch cannot produce a numeric aggregate while fixed/FTE components are
    unresolved; those named states take precedence under K-02."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 TM additional EUR", additional_currency="EUR"
    )

    body = _results(client, project.id, scenario.id)

    assert body["revenue"]["state"] == "calculated"
    assert body["additional_cost"]["currency"] == "EUR"
    assert _aggregate(body) == WITHHELD


def test_k_02_tm_components_in_pln_still_withhold_without_scenario_currency(
    client: TestClient, db_session: Session
) -> None:
    """Even when T&M components are all in PLN, the empty fixed/FTE components cannot state their
    zero amounts in a currency, so K-02 withholds the total."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(db_session, name="R01 TM PLN")

    assert _aggregate(_results(client, project.id, scenario.id)) == CALCULATED


def test_k_02_what_if_and_comparison_withhold_for_unresolved_empty_bases(
    client: TestClient, db_session: Session
) -> None:
    """The what-if and comparison preserve the same K-02 outcome as `/results`."""
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


def test_k_02_unresolved_aggregate_state_is_not_gated_while_figures_are(
    client: TestClient, db_session: Session
) -> None:
    """`profitability_state` remains visible while the four numeric fields use the existing gate."""
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
        "profitability_state": "not_applicable",
    }
    assert Decimal(body["revenue"]["amount"]) == Decimal("20000.00")
