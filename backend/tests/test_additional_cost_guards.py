"""SC-5-05, K-06 — writes to an approved scenario's costs, the per-row marker, and the race.

- **Refused under `approved`, in the statement that writes** (ADR-0004, addendum SC-5-05, point 3):
  `INSERT`, `UPDATE` and `DELETE` alike, each leaving the rows exactly as they were.
- **A race with the approval on two connections leaves no row written after the approval began** —
  mandatory, not optional (K-06), and run for each of the three statements, because each is built
  by a separate function and the seam could be present in one and missing in another.
- **A stale `updated_at` is a `409` told apart from `409 approved`**, and the marker is **per
  cost row** (ADR-0007, addendum SC-5-05): editing cost A does not invalidate cost B of the
  same position.
- **A non-existent id under `approved` is a `404`, not a `409`** — the R-01 order.

`approved` is reached through `make_scenario(status=APPROVED)` (a direct write, the repository's
standing limit of proof) for the refusal tests, and through the **real approval endpoint** for the
races. The marker and race tests use `committing_client`: inside one test transaction `now()` is
constant, so a marker could not move at all and a stale-marker test there would prove nothing.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models import AdditionalCost, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    additional_cost_path,
    additional_cost_payload,
    additional_costs_path,
    as_caller,
    count_additional_costs,
    make_additional_cost,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
)
from tests.test_staffing_approved_guards import (
    _race_a_child_write_against_the_approval,
    _scenario_is_approved,
)

MAR = date(2026, 3, 1)
HEADERS = as_caller(IN_SCOPE_USER)


def _costs(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> list[dict]:
    response = client.get(additional_costs_path(project_id, scenario_id), headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["costs"]


def _scenario_with_a_cost(session: Session, *, status: ScenarioStatus, name: str) -> dict[str, Any]:
    project = make_project(session, name=name, accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", status=status)
    position = make_staffing_position(
        session, scenario, make_dimension_tuple(session, suffix=f" {name}"), start_date=MAR
    )
    category = make_cost_category(session, name=f"Licences {name}")
    cost = make_additional_cost(
        session, scenario, category, amount=Decimal("100.0000"), start_month=MAR,
        position=position,
    )
    return {
        "project": project, "scenario": scenario, "position": position,
        "category": category, "cost": cost,
    }


def _stored_amount(session: Session, cost_id: uuid.UUID) -> Decimal | None:
    session.expire_all()
    return session.execute(
        sa.select(AdditionalCost.amount).where(AdditionalCost.id == cost_id)
    ).scalar_one_or_none()


# --- K-06: every write under `approved` is refused and writes nothing ----------------------------


def test_k_06_insert_update_and_delete_under_an_approved_scenario_are_refused_and_write_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-06 (D-5) — four writes against an approved scenario that has one position cost of 100:

    - `POST` a scenario-level cost, `POST` a cost on the position → `409` naming "approved";
    - `PATCH` the existing cost with its **current** marker → `409` naming "approved";
    - `DELETE` it with its current marker → `409` naming "approved".

    Afterwards: still exactly one row, still 100.0000. The contrast is the same four writes against
    a draft of the same shape — 201, 201, 200, 204 — so the refusal is about the status and not
    about a broken path. Mutation: removing `unapproved_scenario` from any one of the three
    statements makes the corresponding write land here.
    """
    approved = _scenario_with_a_cost(db_session, status=ScenarioStatus.APPROVED, name="Frozen")
    draft = _scenario_with_a_cost(db_session, status=ScenarioStatus.DRAFT, name="Open")

    def writes(fixture: dict[str, Any]) -> list[Any]:
        project_id, scenario_id = fixture["project"].id, fixture["scenario"].id
        token = _costs(client, project_id, scenario_id)[0]["updated_at"]
        cost_path = additional_cost_path(project_id, scenario_id, fixture["cost"].id)
        return [
            client.post(
                additional_costs_path(project_id, scenario_id),
                json=additional_cost_payload(fixture["category"].id),
                headers=HEADERS,
            ),
            client.post(
                additional_costs_path(project_id, scenario_id),
                json=additional_cost_payload(
                    fixture["category"].id, position_id=str(fixture["position"].id)
                ),
                headers=HEADERS,
            ),
            client.patch(
                cost_path, json={"updated_at": token, "amount": "999.0000"}, headers=HEADERS
            ),
            client.request("DELETE", cost_path, json={"updated_at": token}, headers=HEADERS),
        ]

    before = count_additional_costs(db_session)
    refused = writes(approved)

    for response in refused:
        assert response.status_code == 409, response.text
        assert "approved" in response.json()["detail"]
    assert count_additional_costs(db_session) == before
    assert _stored_amount(db_session, approved["cost"].id) == Decimal("100.0000")

    # Contrast: a draft accepts every one of the same four writes.
    accepted = writes(draft)
    assert [response.status_code for response in accepted] == [201, 201, 200, 204]


def test_k_06_a_cost_id_not_in_the_approved_scenario_is_a_404_not_a_409(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — "does the target exist?" before "may it be written?" (the R-01 order).

    Under an approved scenario, a `PATCH` and a `DELETE` of an id that is no cost of it — a random
    id, and the id of a cost of *another* scenario of the caller's — answer `404` with the one body,
    never `409 "approved"`: advising a copy would be advice that cannot work (the same request
    against the copy is a `404` as well). The same for a `POST` naming a position that is not the
    scenario's. The contrast is the existing cost of the scenario itself: `409 "approved"`.
    """
    approved = _scenario_with_a_cost(db_session, status=ScenarioStatus.APPROVED, name="Frozen")
    elsewhere = _scenario_with_a_cost(db_session, status=ScenarioStatus.DRAFT, name="Elsewhere")
    project_id, scenario_id = approved["project"].id, approved["scenario"].id
    token = _costs(client, project_id, scenario_id)[0]["updated_at"]

    for missing in (uuid.uuid4(), elsewhere["cost"].id):
        path = additional_cost_path(project_id, scenario_id, missing)
        edited = client.patch(path, json={"updated_at": token, "amount": "1"}, headers=HEADERS)
        deleted = client.request("DELETE", path, json={"updated_at": token}, headers=HEADERS)
        assert edited.status_code == 404, edited.text
        assert deleted.status_code == 404, deleted.text
    foreign_position = client.post(
        additional_costs_path(project_id, scenario_id),
        json=additional_cost_payload(
            approved["category"].id, position_id=str(elsewhere["position"].id)
        ),
        headers=HEADERS,
    )
    assert foreign_position.status_code == 404, foreign_position.text

    own = client.patch(
        additional_cost_path(project_id, scenario_id, approved["cost"].id),
        json={"updated_at": token, "amount": "1"},
        headers=HEADERS,
    )
    assert own.status_code == 409 and "approved" in own.json()["detail"]
    assert _stored_amount(db_session, elsewhere["cost"].id) == Decimal("100.0000")


# --- K-06: the marker — stale is a 409 of its own, and it is per cost row -------------------------


def _committed_pair(engine: Engine) -> dict[str, Any]:
    """A committed draft with **two** costs on the same position — the case the marker's granularity
    is about (ADR-0007, addendum SC-5-05, point 1)."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        fixture = _scenario_with_a_cost(setup, status=ScenarioStatus.DRAFT, name="Markers")
        second = make_additional_cost(
            setup, fixture["scenario"], fixture["category"], amount=Decimal("200.0000"),
            start_month=MAR, position=fixture["position"],
        )
        setup.commit()
        return {
            "project_id": fixture["project"].id,
            "draft_id": fixture["scenario"].id,
            "position_id": fixture["position"].id,
            "category_id": fixture["category"].id,
            "first": fixture["cost"].id,
            "second": second.id,
        }


def _tokens(client: TestClient, state: dict[str, Any]) -> dict[uuid.UUID, str]:
    return {
        uuid.UUID(cost["id"]): cost["updated_at"]
        for cost in _costs(client, state["project_id"], state["draft_id"])
    }


def test_k_06_a_stale_marker_is_a_409_told_apart_from_the_approved_409(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06 (ADR-0007) — edit cost A with its marker (200, the marker moves), then edit it again
    with the **old** marker: `409`, naming the concurrency marker and **not** "approved", and the
    row keeps the first edit. Mutation: the marker comparison dropped from the `UPDATE` — the second
    edit succeeds and overwrites."""
    state = _committed_pair(engine)
    path = additional_cost_path(state["project_id"], state["draft_id"], state["first"])
    stale = _tokens(committing_client, state)[state["first"]]

    first = committing_client.patch(
        path, json={"updated_at": stale, "amount": "150.0000"}, headers=HEADERS
    )
    assert first.status_code == 200, first.text
    assert first.json()["updated_at"] != stale

    second = committing_client.patch(
        path, json={"updated_at": stale, "amount": "175.0000"}, headers=HEADERS
    )
    deleted = committing_client.request(
        "DELETE", path, json={"updated_at": stale}, headers=HEADERS
    )

    for refused in (second, deleted):
        assert refused.status_code == 409, refused.text
        assert "concurrency marker" in refused.json()["detail"]
        assert "approved" not in refused.json()["detail"]
    with Session(bind=engine) as check:
        assert _stored_amount(check, state["first"]) == Decimal("150.0000")


def test_k_06_the_marker_is_per_cost_row_editing_one_cost_leaves_the_other_editable(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06 (ADR-0007, addendum SC-5-05, point 1) — two costs of **one position**. Editing A
    moves A's marker (the contrast: the same old marker for A is now refused) and leaves B's
    marker exactly as it was, so B's edit with the marker read *before* A's edit succeeds.

    Mutation: the position's `updated_at` as the token (the absence model) — A's edit would
    invalidate B's marker and B's edit would be a `409`.
    """
    state = _committed_pair(engine)
    before = _tokens(committing_client, state)

    edited = committing_client.patch(
        additional_cost_path(state["project_id"], state["draft_id"], state["first"]),
        json={"updated_at": before[state["first"]], "amount": "150.0000"},
        headers=HEADERS,
    )
    assert edited.status_code == 200, edited.text
    after = _tokens(committing_client, state)
    assert after[state["first"]] != before[state["first"]]
    assert after[state["second"]] == before[state["second"]]

    other = committing_client.patch(
        additional_cost_path(state["project_id"], state["draft_id"], state["second"]),
        json={"updated_at": before[state["second"]], "amount": "250.0000"},
        headers=HEADERS,
    )
    assert other.status_code == 200, other.text


# --- K-06: the race with the approval, on two connections ----------------------------------------


def _count(engine: Engine, scenario_id: uuid.UUID) -> int:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT count(*) FROM additional_cost WHERE scenario_id = :id"),
            {"id": scenario_id},
        ).scalar_one()


def test_k_06_an_approval_committing_concurrently_with_a_cost_insert_leaves_no_row(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06 (D-5), the race, path 1 of 3 — `INSERT` while the real approval holds the scenario lock.

    The postcondition is the pair: "the scenario is approved" and "a row was written after the
    approval began" never hold together. `blocked` is asserted — a run in which the insert never
    waited says nothing about serialisation. Mutation: dropping `FOR UPDATE` from
    `app.data.scenario_guard.unapproved_scenario` (or not embedding it) — the insert does not
    block, reads `draft` and commits a cost into a calculation approved a moment later.
    """
    state = _committed_pair(engine)
    before = _count(engine, state["draft_id"])

    def insert_a_cost() -> Any:
        return committing_client.post(
            additional_costs_path(state["project_id"], state["draft_id"]),
            json=additional_cost_payload(state["category_id"], amount="77.0000"),
            headers=HEADERS,
        )

    outcome = _race_a_child_write_against_the_approval(
        committing_client, engine, state, insert_a_cost
    )

    assert outcome["approval"].status_code == 200, outcome["approval"].text
    assert outcome["blocked"], "the cost insert never waited for a lock"
    approved = _scenario_is_approved(engine, state["draft_id"])
    after = _count(engine, state["draft_id"])
    assert not (approved and after > before), (
        f"approved={approved} and the cost count went {before} → {after}"
    )
    assert approved, "the approval itself failed — the pair above is satisfied vacuously"
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]


def test_k_06_an_approval_committing_concurrently_with_a_cost_edit_changes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, the race, path 2 of 3 — `UPDATE` with a valid marker while the approval is in flight.
    The forbidden outcome is "approved **and** the amount changed"."""
    state = _committed_pair(engine)
    token = _tokens(committing_client, state)[state["first"]]

    def edit_the_cost() -> Any:
        return committing_client.patch(
            additional_cost_path(state["project_id"], state["draft_id"], state["first"]),
            json={"updated_at": token, "amount": "999.0000"},
            headers=HEADERS,
        )

    outcome = _race_a_child_write_against_the_approval(
        committing_client, engine, state, edit_the_cost
    )

    assert outcome["approval"].status_code == 200, outcome["approval"].text
    assert outcome["blocked"], "the cost edit never waited for a lock"
    approved = _scenario_is_approved(engine, state["draft_id"])
    with Session(bind=engine) as check:
        amount = _stored_amount(check, state["first"])
    assert not (approved and amount == Decimal("999.0000")), (
        "a cost of a calculation that was already being approved was changed anyway"
    )
    assert approved
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]


def test_k_06_an_approval_committing_concurrently_with_a_cost_delete_leaves_the_row_in_place(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, the race, path 3 of 3 — `DELETE` while the approval is in flight. The sharper case: the
    forbidden outcome "approved **and** the row is gone" takes its own evidence with it."""
    state = _committed_pair(engine)
    token = _tokens(committing_client, state)[state["first"]]
    before = _count(engine, state["draft_id"])
    assert before == 2

    def delete_the_cost() -> Any:
        return committing_client.request(
            "DELETE",
            additional_cost_path(state["project_id"], state["draft_id"], state["first"]),
            json={"updated_at": token},
            headers=HEADERS,
        )

    outcome = _race_a_child_write_against_the_approval(
        committing_client, engine, state, delete_the_cost
    )

    assert outcome["approval"].status_code == 200, outcome["approval"].text
    assert outcome["blocked"], "the cost delete never waited for a lock"
    approved = _scenario_is_approved(engine, state["draft_id"])
    after = _count(engine, state["draft_id"])
    assert not (approved and after < before), (
        f"approved={approved} and the cost count went {before} → {after}"
    )
    assert approved
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]
