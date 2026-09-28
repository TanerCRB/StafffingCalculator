"""SC-4-02, K-05 — every Fixed Price write path refuses `approved` in the statement that writes, a
scope miss is a `404` before any `409`, a price-less Fixed Price rule is a `422`, and a stale marker
is a `409` told apart from the `approved` one (ADR-0004, addendum 2026-09-25 SC-4-02, point 1;
ADR-0007; control FPS-1).

**Two write paths, each with its refusal and its race** — the creation with a price (`POST`) and
the price edit (`PATCH`, D-6 = A):

1. the plain refusal against an `approved` scenario, with a draft as the contrast;
2. the approval committed immediately before the guarded statement runs (a `before_cursor_execute`
   hook on another connection) — the window a Python status check would leave open;
3. the two-connection race against the real approval endpoint, which holds the scenario's row lock
   while the write is in flight: the write must **wait** (`blocked` is asserted), and the
   postcondition is the pair — "approved" and "the write landed" never both hold. The mutation is
   removing `FOR UPDATE` from `app.data.scenario_guard.unapproved_scenario` (or a Python status
   check in place of the predicate): the write then does not block and lands under an approved
   scenario.

Everything runs on **committed** transactions and is counted from a separate connection.
`tests/test_commercial_terms_access.py::test_the_request_carries_nothing_but_the_model` was
re-armed, not weakened (human decision of 2026-09-25 on Issue #66): its unknown-model body is
`{"model_type": "not_a_model"}` — no longer `{"model_type": "fixed_price"}`, a real model since
SC-4-02, whose `422` would come from the missing price rather than from the unknown model — and it
asserts the discriminator's refusal (`union_tag_invalid` on `model_type`), not only the `422`
status. This file adds the Fixed Price `422`s (a Fixed Price rule without its price); it does not
replace that test.
"""

import threading
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.api.commercial_terms import COMMERCIAL_TERMS_NOT_FOUND_DETAIL
from app.core.identity import Permission
from app.models import ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    approve_path,
    as_caller,
    caller_holding,
    commercial_terms_path,
    make_commercial_terms,
    make_fixed_price_terms,
    make_project,
    make_scenario,
    wait_until_a_lock_request_is_pending,
)

FIXED_PRICE = {"model_type": "fixed_price", "agreed_price": "150000", "currency": "PLN"}


def _committed_project(engine: Engine, *, priced_draft: bool = False) -> dict[str, uuid.UUID]:
    """A committed project in scope with one `draft` and one `approved` scenario — and, when asked,
    a committed Fixed Price rule of 150000 PLN on **each** of the two."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Baseline")
        approved = make_scenario(
            setup, project, name="Approved v1", status=ScenarioStatus.APPROVED
        )
        if priced_draft:
            make_fixed_price_terms(setup, draft)
            make_fixed_price_terms(setup, approved)
        state = {"project_id": project.id, "draft_id": draft.id, "approved_id": approved.id}
        setup.commit()
    return state


def _rows_of(engine: Engine, scenario_id: uuid.UUID) -> tuple[int, int]:
    """(rules, Fixed Price details rows) of one scenario, from a separate connection."""
    with engine.connect() as connection:
        rules = connection.execute(
            sa.text("SELECT count(*) FROM commercial_terms WHERE scenario_id = :id"),
            {"id": scenario_id},
        ).scalar_one()
        details = connection.execute(
            sa.text(
                "SELECT count(*) FROM fixed_price_terms f JOIN commercial_terms c"
                " ON c.id = f.commercial_terms_id WHERE c.scenario_id = :id"
            ),
            {"id": scenario_id},
        ).scalar_one()
    return rules, details


def _price_of(engine: Engine, scenario_id: uuid.UUID) -> tuple[Decimal, str, object]:
    """(agreed price, currency, the rule's marker) of one scenario, committed rows only."""
    with engine.connect() as connection:
        return tuple(  # type: ignore[return-value]
            connection.execute(
                sa.text(
                    "SELECT f.agreed_price, f.currency, c.updated_at FROM fixed_price_terms f"
                    " JOIN commercial_terms c ON c.id = f.commercial_terms_id"
                    " WHERE c.scenario_id = :id"
                ),
                {"id": scenario_id},
            ).one()
        )


def _is_approved(engine: Engine, scenario_id: uuid.UUID) -> bool:
    with engine.connect() as connection:
        return (
            connection.execute(
                sa.text("SELECT status FROM scenarios WHERE id = :id"), {"id": scenario_id}
            ).scalar_one()
            == "approved"
        )


def _marker(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    response = client.get(
        commercial_terms_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()["commercial_terms"]["updated_at"]


def _approve_just_before(engine: Engine, fragment: str, scenario_id: uuid.UUID) -> Callable:
    """A `before_cursor_execute` hook committing the approval on another connection the first time
    a statement containing `fragment` is about to run."""
    fired: list[str] = []

    def approve_first(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if fired or fragment not in statement.lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text("UPDATE scenarios SET status = 'approved' WHERE id = :id"),
                {"id": scenario_id},
            )

    approve_first.fired = fired  # type: ignore[attr-defined]
    return approve_first


def _race_against_the_approval(
    committing_client: TestClient,
    engine: Engine,
    state: dict[str, uuid.UUID],
    write: Callable[[], Any],
) -> dict[str, Any]:
    """Run `write` in a background thread while the real approval holds the scenario's row lock —
    the shape of `tests/test_commercial_terms_guards.py::_race_the_write_against_the_approval`,
    parameterised by the write path."""
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
                outcome["response"] = write()
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
    assert not thread.is_alive(), "the write never finished — it is still holding a lock"
    assert "error" not in outcome, outcome.get("error")
    return outcome


# --- K-05, path 1: creating a Fixed Price rule with its price ------------------------------------


def test_k_05_creating_a_fixed_price_rule_in_an_approved_scenario_is_refused_and_writes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, creation, first run — `409` naming *approved*, zero rules and zero price rows. The
    contrast is the same request on the draft: `201`, both rows written with the price."""
    state = _committed_project(engine)

    refused = committing_client.post(
        commercial_terms_path(state["project_id"], state["approved_id"]),
        json=FIXED_PRICE,
        headers=as_caller(IN_SCOPE_USER),
    )
    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"]
    assert _rows_of(engine, state["approved_id"]) == (0, 0)

    accepted = committing_client.post(
        commercial_terms_path(state["project_id"], state["draft_id"]),
        json=FIXED_PRICE,
        headers=as_caller(IN_SCOPE_USER),
    )
    assert accepted.status_code == 201, accepted.text
    assert _rows_of(engine, state["draft_id"]) == (1, 1)
    assert _price_of(engine, state["draft_id"])[:2] == (Decimal("150000.0000"), "PLN")


def test_k_05_an_approval_committed_just_before_the_creation_still_refuses_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, creation, second run — the approval commits after the scope read saw `draft` and
    immediately before the guarded `INSERT`. Mutation: a Python status check followed by a plain
    insert — it read `draft`, so a priced rule lands in an approved calculation."""
    state = _committed_project(engine)
    hook = _approve_just_before(engine, "insert into commercial_terms", state["draft_id"])

    event.listen(Engine, "before_cursor_execute", hook)
    try:
        response = committing_client.post(
            commercial_terms_path(state["project_id"], state["draft_id"]),
            json=FIXED_PRICE,
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", hook)

    assert hook.fired, "the approval never landed inside the window"  # type: ignore[attr-defined]
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"]
    assert _rows_of(engine, state["draft_id"]) == (0, 0)


def test_k_05_an_approval_committing_concurrently_with_the_creation_leaves_no_price_under_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, creation, third run — two connections, the real approval endpoint, the pair."""
    state = _committed_project(engine)

    outcome = _race_against_the_approval(
        committing_client,
        engine,
        state,
        lambda: committing_client.post(
            commercial_terms_path(state["project_id"], state["draft_id"]),
            json=FIXED_PRICE,
            headers=as_caller(IN_SCOPE_USER),
        ),
    )

    assert outcome["approval"].status_code == 200, outcome["approval"].text
    assert outcome["blocked"], (
        "the creation never waited for a lock — nothing serialises it against the approval"
    )
    approved = _is_approved(engine, state["draft_id"])
    rules, details = _rows_of(engine, state["draft_id"])
    assert not (approved and (rules or details)), (
        f"approved={approved} with {rules} rule(s) and {details} price row(s)"
    )
    assert approved, "the approval itself failed — the pair above is satisfied vacuously"
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]


# --- K-05, path 2: editing the price (D-6 = A) ---------------------------------------------------


def test_k_05_editing_the_price_of_an_approved_scenario_is_refused_and_changes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, edit, first run — the approved scenario's price edit is `409` naming *approved*, with
    its **current** marker (so the refusal is not the marker's), and the price and the marker are
    unchanged. Contrast: the same edit on the draft is `200`, the price changes and the marker
    rotates."""
    state = _committed_project(engine, priced_draft=True)
    project_id = state["project_id"]
    approved_before = _price_of(engine, state["approved_id"])

    refused = committing_client.patch(
        commercial_terms_path(project_id, state["approved_id"]),
        json={
            "updated_at": _marker(committing_client, project_id, state["approved_id"]),
            "agreed_price": "175000",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"]
    assert _price_of(engine, state["approved_id"]) == approved_before

    draft_before = _price_of(engine, state["draft_id"])
    accepted = committing_client.patch(
        commercial_terms_path(project_id, state["draft_id"]),
        json={
            "updated_at": _marker(committing_client, project_id, state["draft_id"]),
            "agreed_price": "175000",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert accepted.status_code == 200, accepted.text
    draft_after = _price_of(engine, state["draft_id"])
    assert draft_after[:2] == (Decimal("175000.0000"), "PLN")
    assert draft_after[2] != draft_before[2], "the edit did not rotate the rule's marker"
    assert accepted.json()["revenue"]["amount"] == "175000.00"


def test_k_05_an_approval_committed_just_before_the_price_edit_still_refuses_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, edit, second run — the approval commits immediately before the guarded `UPDATE`. The
    marker sent is current, so only the `approved` predicate inside the statement can refuse it.
    Mutation: a Python status check before a plain `UPDATE` — it read `draft` and rewrites the
    price of an approved scenario."""
    state = _committed_project(engine, priced_draft=True)
    before = _price_of(engine, state["draft_id"])
    marker = _marker(committing_client, state["project_id"], state["draft_id"])
    hook = _approve_just_before(engine, "update commercial_terms set", state["draft_id"])

    event.listen(Engine, "before_cursor_execute", hook)
    try:
        response = committing_client.patch(
            commercial_terms_path(state["project_id"], state["draft_id"]),
            json={"updated_at": marker, "agreed_price": "175000"},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", hook)

    assert hook.fired, "the approval never landed inside the window"  # type: ignore[attr-defined]
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"]
    assert _price_of(engine, state["draft_id"]) == before


def test_k_05_an_approval_committing_concurrently_with_the_price_edit_leaves_the_price_unchanged(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, edit, third run — two connections, the real approval endpoint holding the lock, the
    pair: "approved" and "the price changed" never hold together."""
    state = _committed_project(engine, priced_draft=True)
    before = _price_of(engine, state["draft_id"])
    marker = _marker(committing_client, state["project_id"], state["draft_id"])

    outcome = _race_against_the_approval(
        committing_client,
        engine,
        state,
        lambda: committing_client.patch(
            commercial_terms_path(state["project_id"], state["draft_id"]),
            json={"updated_at": marker, "agreed_price": "175000"},
            headers=as_caller(IN_SCOPE_USER),
        ),
    )

    assert outcome["approval"].status_code == 200, outcome["approval"].text
    assert outcome["blocked"], (
        "the price edit never waited for a lock — nothing serialises it against the approval"
    )
    approved = _is_approved(engine, state["draft_id"])
    after = _price_of(engine, state["draft_id"])
    assert not (approved and after != before), (
        f"approved={approved} and the price moved from {before[:2]} to {after[:2]}"
    )
    assert approved, "the approval itself failed — the pair above is satisfied vacuously"
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]


def test_k_05_contrast_a_price_edited_before_the_approval_is_approved_with_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05's contrast — an edit that commits first is part of the calculation being approved, so
    the race above is not satisfied by a guard refusing every edit whatever the order."""
    state = _committed_project(engine, priced_draft=True)
    project_id, draft_id = state["project_id"], state["draft_id"]

    edited = committing_client.patch(
        commercial_terms_path(project_id, draft_id),
        json={"updated_at": _marker(committing_client, project_id, draft_id), "currency": "EUR"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert edited.status_code == 200, edited.text
    approval = committing_client.post(
        approve_path(project_id, draft_id), headers=as_caller(IN_SCOPE_USER)
    )

    assert approval.status_code == 200, approval.text
    assert _is_approved(engine, draft_id)
    assert _price_of(engine, draft_id)[:2] == (Decimal("150000.0000"), "EUR")


# --- K-05: the stale marker, told apart from `approved` ------------------------------------------


def test_k_05_a_stale_marker_is_a_409_distinguishable_from_the_approved_409(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05 / ADR-0007 — two editors read the same marker; the first edit wins, the second is `409`
    naming the **concurrency marker** and not *approved*, and the first editor's price stays. The
    approved refusal (previous tests) names *approved* and not the marker — two mechanisms, two
    messages. Mutation: the marker left out of the `WHERE` — the second edit overwrites the first
    (a lost update)."""
    state = _committed_project(engine, priced_draft=True)
    project_id, draft_id = state["project_id"], state["draft_id"]
    shared_marker = _marker(committing_client, project_id, draft_id)

    first = committing_client.patch(
        commercial_terms_path(project_id, draft_id),
        json={"updated_at": shared_marker, "agreed_price": "160000"},
        headers=as_caller(IN_SCOPE_USER),
    )
    second = committing_client.patch(
        commercial_terms_path(project_id, draft_id),
        json={"updated_at": shared_marker, "agreed_price": "170000"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert "concurrency marker" in detail and "approved" not in detail
    assert _price_of(engine, draft_id)[:2] == (Decimal("160000.0000"), "PLN")

    approved = committing_client.patch(
        commercial_terms_path(project_id, state["approved_id"]),
        json={"updated_at": shared_marker, "agreed_price": "170000"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert approved.status_code == 409, approved.text
    assert "approved" in approved.json()["detail"]
    assert "concurrency marker" not in approved.json()["detail"]


# --- K-05: 404 before 409 -------------------------------------------------------------------------


def test_k_05_a_fixed_price_write_outside_the_scope_is_the_same_404_before_any_409(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — out of scope, both write paths: an `approved` scenario and a priced draft of another
    caller's project, a scenario that does not exist and one addressed through the wrong project
    all answer the **same** `404` body, and nothing is written. Every one of the in-scope twins
    earns its `409` (contrast), so the `404`s are the precedence and not the absence of the
    refusals. Mutation: the scope decided after the guarded statement."""
    theirs_project = make_project(db_session, name="Borealis", accessible_to=(OUT_OF_SCOPE_USER,))
    theirs_approved = make_scenario(
        db_session, theirs_project, name="Approved", status=ScenarioStatus.APPROVED
    )
    make_fixed_price_terms(db_session, theirs_approved)
    theirs_priced = make_scenario(db_session, theirs_project, name="Priced")
    make_fixed_price_terms(db_session, theirs_priced)
    mine_project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    mine_approved = make_scenario(
        db_session, mine_project, name="Approved", status=ScenarioStatus.APPROVED
    )
    make_fixed_price_terms(db_session, mine_approved)
    mine_priced = make_scenario(db_session, mine_project, name="Priced")
    make_fixed_price_terms(db_session, mine_priced)
    stale = "2000-01-01T00:00:00+00:00"
    edit = {"updated_at": stale, "agreed_price": "1"}

    addresses = [
        commercial_terms_path(theirs_project.id, theirs_approved.id),
        commercial_terms_path(theirs_project.id, theirs_priced.id),
        commercial_terms_path(mine_project.id, uuid.uuid4()),
        commercial_terms_path(mine_project.id, theirs_priced.id),
        commercial_terms_path(uuid.uuid4(), mine_priced.id),
    ]
    responses = [
        *(
            client.post(path, json=FIXED_PRICE, headers=as_caller(IN_SCOPE_USER))
            for path in addresses
        ),
        *(client.patch(path, json=edit, headers=as_caller(IN_SCOPE_USER)) for path in addresses),
    ]
    for response in responses:
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": COMMERCIAL_TERMS_NOT_FOUND_DETAIL}
    assert len({response.content for response in responses}) == 1

    # The edits first: the last contrast (a second rule, refused by the unique index) rolls the
    # shared test session back, and with it the fixture rows every later request would need.
    contrasts = [
        client.patch(
            commercial_terms_path(mine_project.id, scenario.id),
            json=edit,
            headers=as_caller(IN_SCOPE_USER),
        )
        for scenario in (mine_approved, mine_priced)
    ] + [
        client.post(
            commercial_terms_path(mine_project.id, scenario.id),
            json=FIXED_PRICE,
            headers=as_caller(IN_SCOPE_USER),
        )
        for scenario in (mine_approved, mine_priced)
    ]
    assert [response.status_code for response in contrasts] == [409, 409, 409, 409], [
        response.text for response in contrasts
    ]


def test_k_05_editing_the_price_of_a_scenario_without_a_fixed_price_rule_is_a_404(
    client: TestClient, db_session: Session
) -> None:
    """In scope, but nothing to edit: no rule, a T&M rule, or a Fixed Price rule without its price
    row — `404` naming the missing Fixed Price terms, even under `approved` (copying would not
    help), and never the scope body. `model_type` is not editable: sending it is a `422`."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    no_rule = make_scenario(db_session, project, name="No rule")
    time_and_material = make_scenario(db_session, project, name="T&M")
    make_commercial_terms(db_session, time_and_material)
    incomplete = make_scenario(db_session, project, name="Incomplete")
    make_fixed_price_terms(db_session, incomplete, agreed_price=None)
    approved_tm = make_scenario(
        db_session, project, name="Approved T&M", status=ScenarioStatus.APPROVED
    )
    make_commercial_terms(db_session, approved_tm)
    priced = make_scenario(db_session, project, name="Priced")
    make_fixed_price_terms(db_session, priced)
    edit = {"updated_at": "2000-01-01T00:00:00+00:00", "agreed_price": "1"}

    for scenario in (no_rule, time_and_material, incomplete, approved_tm):
        response = client.patch(
            commercial_terms_path(project.id, scenario.id),
            json=edit,
            headers=as_caller(IN_SCOPE_USER),
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] != COMMERCIAL_TERMS_NOT_FOUND_DETAIL
        assert "Fixed Price" in response.json()["detail"]

    changes_the_model = client.patch(
        commercial_terms_path(project.id, priced.id),
        json={**edit, "model_type": "time_and_material"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert changes_the_model.status_code == 422, changes_the_model.text


# --- K-05: a Fixed Price rule without a price is a 422 --------------------------------------------


def test_k_05_a_fixed_price_rule_without_a_price_or_with_an_invalid_one_is_a_422_writing_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — no price, no currency, a `null` price, a negative price, a fifth decimal place
    (ADR-0002: refused, never rounded), a string that is not a number, a lower-case currency, and
    fields that do not exist (a milestone, an adjustment — D-1 = A, D-3 = C): each a `422`, nothing
    written. The contrast is the complete body: `201`."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    path = commercial_terms_path(project.id, scenario.id)

    for body in (
        {"model_type": "fixed_price", "currency": "PLN"},
        {"model_type": "fixed_price", "agreed_price": "150000"},
        {"model_type": "fixed_price", "agreed_price": None, "currency": "PLN"},
        {**FIXED_PRICE, "agreed_price": "-0.01"},
        {**FIXED_PRICE, "agreed_price": "150000.12345"},
        {**FIXED_PRICE, "agreed_price": "a lot"},
        {**FIXED_PRICE, "currency": "pln"},
        {**FIXED_PRICE, "milestones": []},
        {**FIXED_PRICE, "adjustments": [{"kind": "bonus", "amount": "1000"}]},
        {"model_type": "time_and_material", "agreed_price": "150000", "currency": "PLN"},
    ):
        response = client.post(path, json=body, headers=as_caller(IN_SCOPE_USER))
        assert response.status_code == 422, (body, response.text)

    db_session.expire_all()
    assert db_session.execute(
        sa.text("SELECT count(*) FROM commercial_terms WHERE scenario_id = :id"),
        {"id": scenario.id},
    ).scalar_one() == 0

    accepted = client.post(path, json=FIXED_PRICE, headers=as_caller(IN_SCOPE_USER))
    assert accepted.status_code == 201, accepted.text


def test_k_05_an_edit_body_without_a_change_or_with_a_null_is_a_422(
    client: TestClient, db_session: Session
) -> None:
    """The edit's own `422`s: only a marker, a `null` price or currency, a naive marker, a fifth
    decimal place. Contrast: a valid edit is `200`."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    make_fixed_price_terms(db_session, scenario)
    path = commercial_terms_path(project.id, scenario.id)
    marker = _marker(client, project.id, scenario.id)

    for body in (
        {"updated_at": marker},
        {"updated_at": marker, "agreed_price": None},
        {"updated_at": marker, "currency": None},
        {"updated_at": "2026-09-25T10:00:00", "agreed_price": "1"},
        {"updated_at": marker, "agreed_price": "1.00001"},
        {"agreed_price": "1"},
    ):
        response = client.patch(path, json=body, headers=as_caller(IN_SCOPE_USER))
        assert response.status_code == 422, (body, response.text)

    accepted = client.patch(
        path, json={"updated_at": marker, "agreed_price": "1"}, headers=as_caller(IN_SCOPE_USER)
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["commercial_terms"]["agreed_price"] == "1.0000"


def test_k_05_the_price_edit_is_refused_to_a_caller_holding_every_permission_but_commercial_write(
    client: TestClient, db_session: Session
) -> None:
    """The edit declares `COMMERCIAL_WRITE` (ADR-0005, addendum SC-4-01, point 2): every other
    permission — `COMMERCIAL_READ`, `STAFFING_WRITE`, `PERSONNEL_COSTS_READ` included — is a `403`
    that changes nothing; `COMMERCIAL_WRITE` alone is a `200`. Mutation: the endpoint declaring
    `COMMERCIAL_READ` or `STAFFING_WRITE`."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    make_fixed_price_terms(db_session, scenario)
    path = commercial_terms_path(project.id, scenario.id)
    body = {"updated_at": _marker(client, project.id, scenario.id), "agreed_price": "1"}

    with caller_holding(*(set(Permission) - {Permission.COMMERCIAL_WRITE})):
        refused = client.patch(path, json=body)
    assert refused.status_code == 403, refused.text
    assert _marker(client, project.id, scenario.id) == body["updated_at"]

    with caller_holding(Permission.COMMERCIAL_WRITE):
        allowed = client.patch(path, json=body)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["commercial_terms"]["agreed_price"] == "1.0000"
