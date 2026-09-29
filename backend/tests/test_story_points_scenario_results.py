"""Merge of SC-4-03 with SC-4-04 — `/results`, what-if and comparison for a Story Points scenario
after narrowing the race guard to the status-dependent sources (human decision 2026-09-25, point 2;
ADR-0003, addendum 2026-09-25 SC-4-03, point 8).

On `main` before this merge, `refuse_a_status_race` compared `rate_source` by plain equality, so
Story Points revenue (`story_points_terms`) and cost (`live_catalog`/`approved_snapshot`) always
differed — every read of `/results`, what-if and comparison for a Story Points scenario ended in a
constant `409` (a defect on `main`). After the merge:

- an approved Story Points scenario → `200`, profit from its revenue (25 × 1000 = 25000);
- a Story Points draft → `200` on `/results`, what-if and comparison;
- an approval landing between the Story Points revenue read and the cost read → `200`, a
  consistent profit;
- **a real approval race on a T&M scenario still gives `409`** — the guard has not been weakened
  for `live_catalog`/`approved_snapshot`;
- a Story Points rule in EUR + costs in PLN (a scenario without a currency) →
  `profitability_state = currency_mismatch`; the contrast in PLN → numbers.

Real PostgreSQL, real endpoints; races on two connections and a separate thread, as in
`tests/test_scenario_results_race.py` and `tests/test_outcome_scenario_results.py`.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import Permission
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    caller_holding,
    make_absence_type,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_story_points_terms,
    make_working_calendar,
)
from tests.test_outcome_scenario_results import _results_with_an_approval_after
from tests.test_profitability_currency import WITHHELD, _aggregate, _without_currency
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)
from tests.test_scenario_results_compare import compare_path
from tests.test_scenario_results_race import _committed_scenario
from tests.test_scenario_what_if import what_if_path

MAR = date(2026, 3, 1)

# 25 points × 1000 = 25000 Story Points revenue; 100 h × 120 = 12000 personnel cost, 2000
# additional cost, 0 absences → 14000; profit 11000; margin 44.00%; markup 78.57%.
SP_CALCULATED = {
    "included_cost": "14000.00",
    "profit": "11000.00",
    "margin": "44.00",
    "markup": "78.57",
    "profitability_state": "calculated",
}


def _results(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID):
    with caller_holding(*EVERYTHING):
        return client.get(results_path(project_id, scenario_id))


def _story_points_scenario(session: Session, *, name: str):
    """`_full_scenario` (PLN rate, additional cost 2000) with a Story Points rule instead of T&M."""
    _ensure_statutory_bypass(session)
    project, scenario, _ = _full_scenario(session, name=name, create_commercial_terms=False)
    make_story_points_terms(session, scenario)
    return project, scenario


def test_merge_an_approved_story_points_scenario_answers_200_with_profit_from_its_revenue(
    client: TestClient, db_session: Session
) -> None:
    """An approved Story Points scenario: revenue 25000.00 (`story_points_terms`), cost from the
    snapshot (`approved_snapshot`) → `200`, profit 11000.00.

    Mutation: a guard comparing `rate_source` by plain equality (the state of `main` before the
    merge) → `409` instead of `200`."""
    project, scenario = _story_points_scenario(db_session, name="SP results approved")
    approval = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    assert approval.status_code == 200, approval.text

    response = _results(client, project.id, scenario.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenario_status"] == "Approved"
    assert (body["revenue"]["state"], body["revenue"]["amount"]) == ("calculated", "25000.00")
    assert body["revenue"]["assumptions_used"]["rate_source"] == "story_points_terms"
    assert body["personnel_cost"]["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert _aggregate(body) == SP_CALCULATED


def test_merge_a_story_points_draft_answers_200_on_results_what_if_and_comparison(
    client: TestClient, db_session: Session
) -> None:
    """A Story Points draft: revenue `story_points_terms`, cost `live_catalog` — `/results`,
    what-if (`+10%`: 12000 → 13200) and comparison (SC-6-02) all answer `200` with numbers, not
    `409`.

    Mutation: any of the three places comparing `rate_source` by plain equality → `409`."""
    project, scenario = _story_points_scenario(db_session, name="SP results draft")

    with caller_holding(*EVERYTHING, Permission.PERSONNEL_COSTS_READ):
        results = client.get(results_path(project.id, scenario.id))
        what_if = client.get(what_if_path(project.id, scenario.id, "10"))
        compared = client.get(compare_path(project.id, scenario.id))

    assert results.status_code == 200, results.text
    assert results.json()["personnel_cost"]["assumptions_used"]["rate_source"] == "live_catalog"
    assert _aggregate(results.json()) == SP_CALCULATED
    assert what_if.status_code == 200, what_if.text
    assert what_if.json()["revenue"]["amount"] == "25000.00"
    assert what_if.json()["personnel_cost"]["amount"] == "13200.00"
    assert compared.status_code == 200, compared.text
    assert [_aggregate(row) for row in compared.json()["results"]] == [SP_CALCULATED]


def _committed_story_points_scenario(engine: Engine) -> dict[str, uuid.UUID]:
    """A Story Points draft committed to the database with cost: 100 planned hours at 120 (12000),
    the rule 25 × 1000 (25000), no additional cost — profit 13000. The shape of
    `_committed_scenario` from `tests/test_scenario_results_race.py`, with a Story Points rule
    instead of T&M."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(
            setup,
            name="Story Points race",
            accessible_to=(IN_SCOPE_USER,),
            cost_visible_to=(IN_SCOPE_USER,),
        )
        scenario = make_scenario(setup, project, name="Baseline", currency="PLN")
        calendar = make_working_calendar(setup, name="Story Points race calendar")
        make_absence_type(
            setup,
            name="Story Points race statutory (no cost)",
            generates_cost=False,
            generates_revenue=False,
            is_statutory_leave=True,
        )
        dimensions = make_dimension_tuple(setup, suffix=" Story Points race", calendar=calendar)
        position = make_staffing_position(setup, scenario, dimensions, start_date=MAR)
        make_allocation(
            setup,
            position,
            period_month=MAR,
            planned_allocation_hours=Decimal("100.00"),
            billable_hours=Decimal("100.00"),
        )
        make_rate(
            setup,
            dimensions,
            effective_from=date(2026, 1, 1),
            default_cost_rate=Decimal("120.0000"),
            default_selling_rate=Decimal("200.0000"),
            currency="PLN",
        )
        make_story_points_terms(setup, scenario)
        state = {"project_id": project.id, "scenario_id": scenario.id}
        setup.commit()
    return state


def test_merge_an_approval_between_the_story_points_revenue_and_the_cost_read_is_not_a_race(
    committing_client: TestClient, engine: Engine
) -> None:
    """An approval committed after the query for `story_points_terms` (the revenue read), and
    before the cost read: `200`, a consistent profit of 13000. Story Points revenue reads only its
    own, guarded scenario row, so it is the same before and after the approval."""
    state = _committed_story_points_scenario(engine)

    response, _, _ = _results_with_an_approval_after(
        committing_client, engine, state, "story_points_terms"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["assumptions_used"]["rate_source"] == "story_points_terms"
    assert body["personnel_cost"]["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert (body["revenue"]["amount"], body["personnel_cost"]["amount"], body["profit"]) == (
        "25000.00",
        "12000.00",
        "13000.00",
    )


def test_merge_a_real_tm_approval_race_between_the_two_reads_is_still_409(
    committing_client: TestClient, engine: Engine
) -> None:
    """Contrast — a real approval race on a T&M scenario (revenue from the live catalogue, cost
    from the snapshot) still `409` and none of the figures 20000/12000/8000. Mutation: a guard
    that stops comparing once any source outside `STATUS_DEPENDENT_SOURCES` is known to the
    system (or not known at all) → `200` with mismatched halves."""
    state = _committed_scenario(engine)

    response, _, _ = _results_with_an_approval_after(
        committing_client, engine, state, "selling_rate"
    )

    assert response.status_code == 409, response.text
    for figure in ("20000.00", "12000.00", "8000.00"):
        assert figure not in response.text


def test_merge_an_eur_story_points_rule_with_pln_costs_is_currency_mismatch(
    client: TestClient, db_session: Session
) -> None:
    """A Story Points rule in EUR, costs in PLN, a scenario without a currency: revenue 25000.00
    EUR `calculated`, and profit, margin, markup and included cost `"n/a"` with state
    `currency_mismatch`. Mutation: dropping the currency comparison in `scenario_profitability` →
    profit `11000.00`."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="SP currency EUR", create_commercial_terms=False
    )
    make_story_points_terms(db_session, scenario, currency="EUR")

    response = _results(client, project.id, scenario.id)

    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert (body["revenue"]["state"], body["revenue"]["currency"]) == ("calculated", "EUR")
    assert body["personnel_cost"]["currency"] == "PLN"
    assert _aggregate(body) == WITHHELD


def test_merge_contrast_the_same_story_points_rule_in_pln_gives_numbers(
    client: TestClient, db_session: Session
) -> None:
    """Contrast — the same rule in PLN, the same costs, a scenario without a currency: numbers.
    Proves that the state above comes from the rule's currency, not from the scenario's lack of a
    currency nor from the model."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="SP currency PLN", create_commercial_terms=False
    )
    make_story_points_terms(db_session, scenario, currency="PLN")

    response = _results(client, project.id, scenario.id)

    assert response.status_code == 200, response.text
    assert _aggregate(response.json()) == SP_CALCULATED
