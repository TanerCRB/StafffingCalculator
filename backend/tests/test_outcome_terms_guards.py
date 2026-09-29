"""SC-4-03, K-05 — an Outcome-based rule cannot be written to an `approved` scenario, and the
refusal belongs
to the write statement (ADR-0004, addendum 2026-09-25 SC-4-03, point 2).

The same construction as K-06 SC-4-01 (`tests/test_commercial_terms_guards.py`), applied to the
outcome write path, which now carries the domain columns in the same statement. Everything on
**committed** transactions and counted from a separate connection:

1. an outright refusal on `approved`, a contrast on `draft` of the same project (`201`, both rows);
2. an approval committed just before the `INSERT` statement (a `before_cursor_execute` hook on
   another connection) — reading the status in Python before the insert would let this through;
3. a race of two connections with the real approval endpoint, which holds the scenario row's lock:
   the write must wait (`blocked`), and the pair "scenario approved" and "rule exists" never holds
   at the same time. Mutation: dropping `FOR UPDATE` from
   `app.data.scenario_guard.unapproved_scenario`.
"""

import threading
import uuid
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.models import ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    commercial_terms_path,
    make_project,
    make_scenario,
    outcome_payload,
    wait_until_a_lock_request_is_pending,
)

OUTCOME = outcome_payload(probabilities=("70", "0", "30", "0"))


def _committed_project(engine: Engine) -> dict[str, uuid.UUID]:
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Outcome guard", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Baseline", currency="PLN")
        approved = make_scenario(
            setup, project, name="Approved v1", status=ScenarioStatus.APPROVED, currency="PLN"
        )
        state = {"project_id": project.id, "draft_id": draft.id, "approved_id": approved.id}
        setup.commit()
    return state


def _rows_of(engine: Engine, scenario_id: uuid.UUID) -> tuple[int, int]:
    """(rules, `outcome_terms` rows) of one scenario — committed rows only."""
    with engine.connect() as connection:
        rules = connection.execute(
            sa.text("SELECT count(*) FROM commercial_terms WHERE scenario_id = :id"),
            {"id": scenario_id},
        ).scalar_one()
        details = connection.execute(
            sa.text(
                "SELECT count(*) FROM outcome_terms o JOIN commercial_terms c"
                " ON c.id = o.commercial_terms_id WHERE c.scenario_id = :id"
            ),
            {"id": scenario_id},
        ).scalar_one()
    return rules, details


def _is_approved(engine: Engine, scenario_id: uuid.UUID) -> bool:
    with engine.connect() as connection:
        return (
            connection.execute(
                sa.text("SELECT status FROM scenarios WHERE id = :id"), {"id": scenario_id}
            ).scalar_one()
            == "approved"
        )


def test_k_05_writing_an_outcome_rule_into_an_approved_scenario_is_refused_and_writes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, first pass — `409` with "approved", zero rules and zero `outcome_terms` rows.

    Contrast: the same body to a `draft` scenario of the same project → `201` and **both** rows,
    with the domain columns (the expected 23000 proves that the probabilities were written in the
    same statement)."""
    state = _committed_project(engine)

    refused = committing_client.post(
        commercial_terms_path(state["project_id"], state["approved_id"]),
        json=OUTCOME,
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"]
    assert _rows_of(engine, state["approved_id"]) == (0, 0)

    accepted = committing_client.post(
        commercial_terms_path(state["project_id"], state["draft_id"]),
        json=OUTCOME,
        headers=as_caller(IN_SCOPE_USER),
    )

    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["revenue"]["expected_amount"] == "23000.00"
    assert _rows_of(engine, state["draft_id"]) == (1, 1)


def test_k_05_an_approval_committed_just_before_the_insert_still_refuses_the_outcome_rule(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, second pass — the guard is the statement, not a status read before the insert.

    The approval commits on another connection after the write has resolved scope (it saw
    `draft`), and just before the guarded statement. Mutation: `if scenario.status == APPROVED:
    raise` in Python + a plain `INSERT` — it read `draft`, so it inserts the rule into the
    calculation that has since been approved.
    """
    state = _committed_project(engine)
    fired: list[str] = []

    def approve_first(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if fired or "insert into commercial_terms" not in statement.lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text("UPDATE scenarios SET status = 'approved' WHERE id = :id"),
                {"id": state["draft_id"]},
            )

    event.listen(Engine, "before_cursor_execute", approve_first)
    try:
        response = committing_client.post(
            commercial_terms_path(state["project_id"], state["draft_id"]),
            json=OUTCOME,
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", approve_first)

    assert fired, "the approval never landed inside the window — nothing here is about the race"
    assert "insert into outcome_terms" in fired[0].lower(), (
        "the rule and its outcome details are not one statement — the guard may miss the second"
    )
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"]
    assert _rows_of(engine, state["draft_id"]) == (0, 0)


def test_k_05_an_approval_racing_the_outcome_rule_write_on_two_connections_leaves_no_rule_under_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, third pass — two connections, the real approval endpoint, the condition as a pair.

    The approval is stopped just before the status change (after the snapshot, holding the
    scenario row's lock); that is when the outcome rule write starts on a thread, and the test
    waits until PostgreSQL itself reports a pending lock request. The write must wait, and after
    the approval get a `409` and leave zero rows.
    """
    state = _committed_project(engine)
    outcome: dict[str, Any] = {}
    fired: list[str] = []

    def start_the_writer(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if fired or "update scenarios set status" not in statement.lower():
            return
        fired.append(statement)

        def writer() -> None:
            try:
                outcome["response"] = committing_client.post(
                    commercial_terms_path(state["project_id"], state["draft_id"]),
                    json=OUTCOME,
                    headers=as_caller(IN_SCOPE_USER),
                )
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        outcome["thread"] = thread
        outcome["blocked"] = wait_until_a_lock_request_is_pending(engine)

    event.listen(Engine, "before_cursor_execute", start_the_writer)
    try:
        approval = committing_client.post(
            approve_path(state["project_id"], state["draft_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", start_the_writer)

    assert fired, "the approval never reached its status update"
    thread = outcome["thread"]
    thread.join(timeout=30)
    assert not thread.is_alive(), "the rule write never finished — it is still holding a lock"
    assert "error" not in outcome, outcome.get("error")

    assert approval.status_code == 200, approval.text
    assert outcome["blocked"], (
        "the outcome rule write never waited for a lock — nothing serialises it against the "
        "approval, and this run says nothing about the race"
    )
    approved = _is_approved(engine, state["draft_id"])
    rules, details = _rows_of(engine, state["draft_id"])
    assert not (approved and (rules or details)), (
        f"approved={approved} with {rules} rule(s) and {details} outcome row(s)"
    )
    assert approved, "the approval itself failed — the pair above is satisfied vacuously"
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]


def test_k_05_contrast_an_outcome_rule_written_before_the_approval_is_approved_with_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, race contrast — a write committed first belongs to the calculation being approved:
    without this, the race above would satisfy a guard that refuses every write regardless of
    order. After the approval the rule stays, and its revenue is the same (no snapshot — ADR-0004,
    addendum SC-4-03, point 4)."""
    state = _committed_project(engine)
    path = commercial_terms_path(state["project_id"], state["draft_id"])

    written = committing_client.post(path, json=OUTCOME, headers=as_caller(IN_SCOPE_USER))
    assert written.status_code == 201, written.text
    approval = committing_client.post(
        approve_path(state["project_id"], state["draft_id"]), headers=as_caller(IN_SCOPE_USER)
    )

    assert approval.status_code == 200, approval.text
    assert _rows_of(engine, state["draft_id"]) == (1, 1)
    read = committing_client.get(path, headers=as_caller(IN_SCOPE_USER)).json()
    assert read["scenario_status"] == "Approved"
    assert read["revenue"] == written.json()["revenue"]
