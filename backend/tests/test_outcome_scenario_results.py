"""SC-4-03, K-07 — `/results` (SC-7-01) for an Outcome-based scenario and the race guard after
introducing `rate_source = not_applicable` (ADR-0003, addendum 2026-09-25 SC-4-03, points 5a and 8;
O-5).

- an approved outcome scenario → `200`, profit calculated from the **guaranteed** revenue (20000),
  not from the expected one (23000); revenue's `rate_source` is `not_applicable`, cost's is
  `approved_snapshot`;
- an approval landing between the outcome revenue read and the cost read → `200`, not `409`: the
  outcome revenue persists no moment of status, so different `rate_source` values are not proof of
  a race;
- **a real T&M scenario approval race still gives `409`** — the guard was not weakened for
  status-dependent sources;
- what-if (SC-6-04), the second place comparing `rate_source`, for an outcome draft → `200`.

Real endpoints; races on two connections and a separate thread, as in
`tests/test_scenario_results_race.py`.
"""

import threading
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
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
    make_outcome_terms,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
)
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)
from tests.test_scenario_results_race import _committed_scenario
from tests.test_scenario_what_if import what_if_path

MAR = date(2026, 3, 1)
PROBABILITIES_70_30 = {
    "not_achieved_probability": Decimal("70"),
    "partial_probability": Decimal("0"),
    "achieved_probability": Decimal("30"),
    "exceeded_probability": Decimal("0"),
}


def _results(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID):
    with caller_holding(*EVERYTHING):
        return client.get(results_path(project_id, scenario_id))


def test_k_07_an_approved_outcome_scenario_answers_200_with_profit_from_the_guaranteed_revenue(
    client: TestClient, db_session: Session
) -> None:
    """K-07 — guaranteed value 20000, included cost 15000 (12000 personnel + 3000 additional) →
    profit 5000, margin 25.00, markup 33.33; the expected value 23000 given alongside, but **not**
    entering the profit.

    Mutations: a guard comparing `rate_source` by mere equality (`not_applicable` ≠
    `approved_snapshot` → `409`); profit from the expected value (8000.00); `amount` = the expected
    value.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session,
        name="Outcome results",
        create_commercial_terms=False,
        additional_amount=Decimal("3000.00"),
    )
    make_outcome_terms(db_session, scenario, **PROBABILITIES_70_30)
    approval = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    assert approval.status_code == 200, approval.text

    response = _results(client, project.id, scenario.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenario_status"] == "Approved"
    assert (body["revenue"]["amount"], body["revenue"]["expected_amount"]) == (
        "20000.00",
        "23000.00",
    )
    assert body["revenue"]["assumptions_used"]["rate_source"] == "not_applicable"
    assert body["personnel_cost"]["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert body["included_cost"] == "15000.00"
    assert (body["profit"], body["margin"], body["markup"]) == ("5000.00", "25.00", "33.33")


def _committed_outcome_scenario(engine: Engine) -> dict[str, uuid.UUID]:
    """An outcome draft committed to the database with a cost: 100 planned hours at 120 (12000), an
    AC-08 rule (guaranteed value 20000), no additional cost — profit 8000. The shape of
    `_committed_scenario` from `tests/test_scenario_results_race.py`, with an outcome rule instead
    of T&M."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(
            setup,
            name="Outcome race",
            accessible_to=(IN_SCOPE_USER,),
            cost_visible_to=(IN_SCOPE_USER,),
        )
        scenario = make_scenario(setup, project, name="Baseline", currency="PLN")
        calendar = make_working_calendar(setup, name="Outcome race calendar")
        make_absence_type(
            setup,
            name="Outcome race statutory (no cost)",
            generates_cost=False,
            generates_revenue=False,
            is_statutory_leave=True,
        )
        dimensions = make_dimension_tuple(setup, suffix=" Outcome race", calendar=calendar)
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
        make_outcome_terms(setup, scenario)
        state = {"project_id": project.id, "scenario_id": scenario.id}
        setup.commit()
    return state


def _results_with_an_approval_after(
    committing_client: TestClient, engine: Engine, state: dict[str, uuid.UUID], marker: str
) -> tuple[Any, dict[str, Any], list[str]]:
    """`GET …/results`, and within it — after the first statement naming `marker` (the revenue
    path's last read) — a real approval through the endpoint, on a separate thread and connection,
    committed before `/results` reads the cost."""
    fired: list[str] = []
    outcome: dict[str, Any] = {}

    def approve_once(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if fired or marker not in statement.lower():
            return
        fired.append(statement)

        def approve() -> None:
            try:
                outcome["approval"] = committing_client.post(
                    approve_path(state["project_id"], state["scenario_id"]),
                    headers=as_caller(IN_SCOPE_USER),
                )
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=approve, daemon=True)
        thread.start()
        thread.join(timeout=30)
        outcome["thread"] = thread

    event.listen(Engine, "after_cursor_execute", approve_once)
    try:
        with caller_holding(*EVERYTHING):
            response = committing_client.get(
                results_path(state["project_id"], state["scenario_id"])
            )
    finally:
        event.remove(Engine, "after_cursor_execute", approve_once)

    assert fired, f"the revenue path never issued a statement naming {marker!r}"
    assert not outcome["thread"].is_alive(), "the approval never finished"
    assert "error" not in outcome, outcome.get("error")
    assert outcome["approval"].status_code == 200, outcome["approval"].text
    return response, outcome, fired


def test_k_07_a_real_tm_approval_race_between_the_two_reads_is_still_409(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-07, second half — a real T&M scenario approval race: the approval commits after the query
    for selling rates (revenue from the live catalogue), and before the cost read (from the
    snapshot). Still `409` and none of the numbers 20000/12000/8000 — `not_applicable` did not
    weaken the guard for status-dependent sources. Mutation: a guard that skips the comparison
    whenever a source is not known (or at all) → `200` with mixed halves."""
    state = _committed_scenario(engine)

    response, _, _ = _results_with_an_approval_after(
        committing_client, engine, state, "selling_rate"
    )

    assert response.status_code == 409, response.text
    for figure in ("20000.00", "12000.00", "8000.00"):
        assert figure not in response.text


def test_k_07_an_approval_between_the_outcome_revenue_and_the_cost_read_is_not_a_race(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-07, contrast for outcome — the same insertion of an approval between the revenue read
    (here: the query for `outcome_terms`) and the cost read: `200`. The outcome revenue reads only
    the scenario's own rows, guarded by the write guard, so it is the same before and after the
    approval; the cost comes from the snapshot made at that approval, from the same rates. Profit
    8000 is consistent, not mixed."""
    state = _committed_outcome_scenario(engine)

    response, _, _ = _results_with_an_approval_after(
        committing_client, engine, state, "outcome_terms"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["assumptions_used"]["rate_source"] == "not_applicable"
    assert body["personnel_cost"]["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert (body["revenue"]["amount"], body["personnel_cost"]["amount"], body["profit"]) == (
        "20000.00",
        "12000.00",
        "8000.00",
    )


def test_k_07_the_what_if_of_an_outcome_draft_is_200_not_a_race(
    client: TestClient, db_session: Session
) -> None:
    """The second place comparing `rate_source` by equality (ADR-0003, addendum SC-4-03, point 8:
    "an explicit review of every place") — what-if SC-6-04 for an outcome draft: revenue
    `not_applicable`, cost `live_catalog` → `200` with the guaranteed revenue and the cost after a
    10% increase (12000 → 13200). Mutation: what-if with its own comparison by equality → `409`."""
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session, name="Outcome what-if", create_commercial_terms=False
    )
    make_outcome_terms(db_session, scenario)

    with caller_holding(*EVERYTHING, Permission.PERSONNEL_COSTS_READ):
        response = client.get(what_if_path(project.id, scenario.id, "10"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["amount"] == "20000.00"
    assert body["revenue"]["assumptions_used"]["rate_source"] == "not_applicable"
    assert body["personnel_cost"]["amount"] == "13200.00"
