"""SC-4-01, K-06 — a rule cannot be written into an `approved` scenario, and the refusal is the
statement's own (ADR-0004, addendum 2026-09-23 SC-4-01, point 1a).

Three runs, all on **committed** transactions and counted from a separate connection — "the endpoint
said no" and "no row exists" are different claims, and only the second is the criterion's:

1. **the plain refusal** against an `approved` scenario, with the draft of the same project as the
   contrast (`201`, both rows written);
2. **the approval committed in the window a Python check would leave open** — immediately before the
   guarded statement runs, by a `before_cursor_execute` hook on another connection. A status read
   followed by an insert passes this; the predicate inside the `INSERT … SELECT` does not;
3. **the two-connection race against the real approval endpoint**, which holds the scenario's row
   lock while the write is in flight: the write must wait, and the postcondition is checked as a
   pair — "the scenario is approved" **and** "the rule exists" never hold together for a write that
   began after the approval did. The mutation is removing the lock (`FOR UPDATE` in
   `app.data.scenario_guard.unapproved_scenario`): the write then does not block (`blocked` is
   asserted), reads a still-`draft` scenario and lands under an approved one.
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
    wait_until_a_lock_request_is_pending,
)

TM = {"model_type": "time_and_material"}

Listener = Any


def _committed_project(engine: Engine) -> dict[str, uuid.UUID]:
    """A committed project in scope with one `draft` and one `approved` scenario, no rules."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Baseline")
        approved = make_scenario(
            setup, project, name="Approved v1", status=ScenarioStatus.APPROVED
        )
        state = {"project_id": project.id, "draft_id": draft.id, "approved_id": approved.id}
        setup.commit()
    return state


def _rows_of(engine: Engine, scenario_id: uuid.UUID) -> tuple[int, int]:
    """(rules, details rows) of one scenario, from a separate connection — committed rows only."""
    with engine.connect() as connection:
        rules = connection.execute(
            sa.text("SELECT count(*) FROM commercial_terms WHERE scenario_id = :id"),
            {"id": scenario_id},
        ).scalar_one()
        details = connection.execute(
            sa.text(
                "SELECT count(*) FROM tm_terms t JOIN commercial_terms c"
                " ON c.id = t.commercial_terms_id WHERE c.scenario_id = :id"
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


def test_k_06_writing_a_rule_into_an_approved_scenario_is_refused_and_writes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, first run — `409` naming *approved*, zero rules and zero details rows.

    The contrast is the same request against the draft of the same project: `201`, and **both** rows
    exist — so the refusal cannot be an accident of the payload, the permission or the scope, and
    the write path really creates the details row (ADR-0003, point 3).
    """
    state = _committed_project(engine)

    refused = committing_client.post(
        commercial_terms_path(state["project_id"], state["approved_id"]),
        json=TM,
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"]
    assert _rows_of(engine, state["approved_id"]) == (0, 0)

    accepted = committing_client.post(
        commercial_terms_path(state["project_id"], state["draft_id"]),
        json=TM,
        headers=as_caller(IN_SCOPE_USER),
    )

    assert accepted.status_code == 201, accepted.text
    assert _rows_of(engine, state["draft_id"]) == (1, 1)


def test_k_06_a_second_rule_for_one_scenario_is_refused_by_the_database_and_names_the_constraint(
    committing_client: TestClient, engine: Engine
) -> None:
    """One rule per scenario (ADR-0003, point 1) — the second write is a `409` from
    `uq_commercial_terms_scenario_id`, quoting no value (NF-11), and the first rule is untouched."""
    state = _committed_project(engine)
    path = commercial_terms_path(state["project_id"], state["draft_id"])
    first = committing_client.post(path, json=TM, headers=as_caller(IN_SCOPE_USER))
    assert first.status_code == 201, first.text

    second = committing_client.post(path, json=TM, headers=as_caller(IN_SCOPE_USER))

    assert second.status_code == 409, second.text
    assert "uq_commercial_terms_scenario_id" in second.json()["detail"]
    assert _rows_of(engine, state["draft_id"]) == (1, 1)
    assert committing_client.get(path, headers=as_caller(IN_SCOPE_USER)).json()[
        "commercial_terms"
    ]["id"] == first.json()["commercial_terms"]["id"]


def test_k_06_an_approval_committed_just_before_the_insert_still_refuses_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, second run — the guard is the statement, not a status read followed by an insert.

    The approval commits on another connection after `create_commercial_terms` has resolved the
    scenario through the scope-filtered read (which saw `draft`) and immediately before the guarded
    statement runs. Mutation: `if scenario.status == APPROVED: raise` in Python followed by a plain
    insert — it read `draft`, so it inserts, and a rule lands in an approved calculation.
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
            json=TM,
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", approve_first)

    assert fired, "the approval never landed inside the window — nothing here is about the race"
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"]
    assert _rows_of(engine, state["draft_id"]) == (0, 0)


def _race_the_write_against_the_approval(
    committing_client: TestClient, engine: Engine, state: dict[str, uuid.UUID]
) -> dict[str, Any]:
    """Run the rule write in a background thread while the real approval holds the row lock.

    The approval is paused just before its status update — after its snapshot, while it holds the
    lock `draft_scenario` took as its first statement — the writer is started, and the test waits
    until PostgreSQL itself reports a waiting lock request (`wait_until_a_lock_request_is_pending`).
    The shape of `tests/test_staffing_approved_guards.py` (K-20 of SC-3-02), for this write path.
    """
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
                    json=TM,
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
        outcome["approval"] = committing_client.post(
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
    return outcome


def test_k_06_an_approval_committing_concurrently_with_the_rule_write_leaves_no_rule_under_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, third run — two connections, the real approval endpoint, the pair postcondition."""
    state = _committed_project(engine)

    outcome = _race_the_write_against_the_approval(committing_client, engine, state)

    assert outcome["approval"].status_code == 200, outcome["approval"].text
    assert outcome["blocked"], (
        "the rule write never waited for a lock — nothing serialises it against the approval, and "
        "this run says nothing about the race"
    )
    approved = _is_approved(engine, state["draft_id"])
    rules, details = _rows_of(engine, state["draft_id"])
    assert not (approved and (rules or details)), (
        f"approved={approved} with {rules} rule(s) and {details} details row(s): a rule landed in "
        "a calculation that was already being approved"
    )
    assert approved, "the approval itself failed — the pair above is satisfied vacuously"
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]


def test_k_06_contrast_a_rule_written_before_the_approval_is_approved_with_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06's contrast — a write that commits first is part of the calculation being approved.

    Without it, the race above would be satisfied by a guard that refuses every write whatever the
    order. The approval then succeeds and the rule stays; a later write is refused.
    """
    state = _committed_project(engine)
    path = commercial_terms_path(state["project_id"], state["draft_id"])

    written = committing_client.post(path, json=TM, headers=as_caller(IN_SCOPE_USER))
    assert written.status_code == 201, written.text
    approval = committing_client.post(
        approve_path(state["project_id"], state["draft_id"]), headers=as_caller(IN_SCOPE_USER)
    )

    assert approval.status_code == 200, approval.text
    assert _is_approved(engine, state["draft_id"])
    assert _rows_of(engine, state["draft_id"]) == (1, 1)
    read = committing_client.get(path, headers=as_caller(IN_SCOPE_USER)).json()
    assert read["scenario_status"] == "Approved"
    assert read["commercial_terms"] is not None
