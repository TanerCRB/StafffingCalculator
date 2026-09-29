"""SC-6-08, K-05 - writes to a scenario's risks, reserves and a cost event's risk link under an
approved scenario, the per-row marker, and the race (ADR-0021, point 6; ADR-0004, ADR-0007 addenda
SC-6-08; the SC-5-05 K-06 construction applied to two new tables and one new column).

- **Refused under `approved`, in the statement that writes**: `INSERT`, `UPDATE` and `DELETE` of a
  risk and of a reserve, and the two ways of writing a cost event's risk link (create with
  `risk_id`, edit `risk_id`) - each leaving the rows exactly as they were. The contrast is the same
  writes against a draft.
- **A race with the approval on two real connections leaves nothing written after the approval
  began** - run for each of the seven write statements, because each is built by a separate
  function and the seam could be present in one and missing in another.
- **A stale `updated_at` is a `409` told apart from `409 approved`**, and the marker is **per row**.
- **A non-existent id under `approved` is a `404`, not a `409`** - the R-01 order.

`approved` is reached through `make_scenario(status=APPROVED)` (a direct write, the repository's
standing limit of proof) for the refusal tests and through the **real approval endpoint** for the
races. The marker and race tests use `committing_client`: inside one test transaction `now()` is
constant, so a marker could not move at all.
"""

import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models import AdditionalCost, RiskReserve, ScenarioRisk, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    additional_cost_path,
    additional_cost_payload,
    additional_costs_path,
    as_caller,
    make_additional_cost,
    make_cost_category,
    make_project,
    make_reserve,
    make_risk,
    make_scenario,
    reserve_path,
    reserves_path,
    risk_path,
    risks_path,
)
from tests.test_staffing_approved_guards import (
    _race_a_child_write_against_the_approval,
    _scenario_is_approved,
)

MAR = date(2026, 3, 1)
HEADERS = as_caller(IN_SCOPE_USER)
RESERVE_BODY = {
    "amount": "10.0000",
    "currency": "EUR",
    "reserve_type": "one_off",
    "start_month": "2026-03-01",
}


def _scenario_with_rows(session: Session, *, status: ScenarioStatus, name: str) -> dict[str, Any]:
    """A scenario with a risk to rename, a risk to delete, a spare risk to link to, one reserve and
    one cost event - every target of the seven writes."""
    project = make_project(session, name=name, accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", status=status, currency="EUR")
    category = make_cost_category(session, name=f"Licences {name}")
    return {
        "project": project,
        "scenario": scenario,
        "category": category,
        "risk_edit": make_risk(session, scenario, name="to rename"),
        "risk_delete": make_risk(session, scenario, name="to delete"),
        "risk_spare": make_risk(session, scenario, name="spare"),
        "reserve": make_reserve(session, scenario, amount=Decimal("100.0000"), start_month=MAR),
        "cost": make_additional_cost(
            session, scenario, category, amount=Decimal("100.0000"), start_month=MAR
        ),
    }


def _tokens(client: TestClient, fixture: dict[str, Any]) -> dict[str, str]:
    """The current marker of every target row, read through the endpoints under test."""
    project_id, scenario_id = fixture["project"].id, fixture["scenario"].id
    risks = client.get(risks_path(project_id, scenario_id), headers=HEADERS).json()["risks"]
    reserves = client.get(reserves_path(project_id, scenario_id), headers=HEADERS).json()[
        "reserves"
    ]
    costs = client.get(additional_costs_path(project_id, scenario_id), headers=HEADERS).json()[
        "costs"
    ]
    return {
        "risk_edit": next(
            r["updated_at"] for r in risks if r["id"] == str(fixture["risk_edit"].id)
        ),
        "risk_delete": next(
            r["updated_at"] for r in risks if r["id"] == str(fixture["risk_delete"].id)
        ),
        "reserve": reserves[0]["updated_at"],
        "cost": costs[0]["updated_at"],
    }


def _seven_writes(
    client: TestClient, fixture: dict[str, Any], tokens: dict[str, str]
) -> dict[str, Callable[[], Any]]:
    """The seven statements K-05 is about, each as a zero-argument call."""
    project_id, scenario_id = fixture["project"].id, fixture["scenario"].id
    spare = str(fixture["risk_spare"].id)

    def request(method: str, path: str, body: dict[str, Any]) -> Callable[[], Any]:
        return lambda: client.request(method, path, json=body, headers=HEADERS)

    return {
        "risk insert": request("POST", risks_path(project_id, scenario_id), {"name": "new risk"}),
        "risk update": request(
            "PATCH",
            risk_path(project_id, scenario_id, fixture["risk_edit"].id),
            {"updated_at": tokens["risk_edit"], "name": "renamed"},
        ),
        "risk delete": request(
            "DELETE",
            risk_path(project_id, scenario_id, fixture["risk_delete"].id),
            {"updated_at": tokens["risk_delete"]},
        ),
        "reserve insert": request("POST", reserves_path(project_id, scenario_id), RESERVE_BODY),
        "reserve update": request(
            "PATCH",
            reserve_path(project_id, scenario_id, fixture["reserve"].id),
            {"updated_at": tokens["reserve"], "amount": "999.0000"},
        ),
        "reserve delete": request(
            "DELETE",
            reserve_path(project_id, scenario_id, fixture["reserve"].id),
            {"updated_at": tokens["reserve"]},
        ),
        "cost link update": request(
            "PATCH",
            additional_cost_path(project_id, scenario_id, fixture["cost"].id),
            {"updated_at": tokens["cost"], "risk_id": spare},
        ),
        "cost link insert": request(
            "POST",
            additional_costs_path(project_id, scenario_id),
            additional_cost_payload(fixture["category"].id, risk_id=spare),
        ),
    }


def _snapshot(session: Session) -> tuple[Any, ...]:
    """Every risk, reserve and cost row as stored - compared before and after, so "nothing was
    written" is a claim about the tables and not about a status code."""
    session.expire_all()
    return (
        sorted(
            tuple(r)
            for r in session.execute(
                sa.select(ScenarioRisk.id, ScenarioRisk.name, ScenarioRisk.updated_at)
            ).all()
        ),
        sorted(
            tuple(r)
            for r in session.execute(
                sa.select(
                    RiskReserve.id, RiskReserve.amount, RiskReserve.risk_id, RiskReserve.updated_at
                )
            ).all()
        ),
        sorted(
            tuple(r)
            for r in session.execute(
                sa.select(AdditionalCost.id, AdditionalCost.risk_id, AdditionalCost.updated_at)
            ).all()
        ),
    )


# --- K-05: every write under `approved` is refused and writes nothing ----------------------------


def test_k_05_every_write_to_risks_reserves_and_the_cost_link_under_approved_is_refused(
    client: TestClient, db_session: Session
) -> None:
    """K-05 (R-04) - the eight writes against an approved scenario (risk insert/update/delete,
    reserve insert/update/delete, cost link on create and on edit) each answer `409` naming
    "approved" and leave the three tables **exactly** as they were. The contrast is the same eight
    writes against a draft of the same shape: `201, 200, 204, 201, 200, 204, 200, 201`. Mutation:
    removing `unapproved_scenario` from any one statement makes that write land here."""
    approved = _scenario_with_rows(db_session, status=ScenarioStatus.APPROVED, name="Frozen")
    draft = _scenario_with_rows(db_session, status=ScenarioStatus.DRAFT, name="Open")
    frozen_writes = _seven_writes(client, approved, _tokens(client, approved))
    open_writes = _seven_writes(client, draft, _tokens(client, draft))

    before = _snapshot(db_session)
    refused = {name: write() for name, write in frozen_writes.items()}
    for name, response in refused.items():
        assert response.status_code == 409, (name, response.text)
        assert "approved" in response.json()["detail"], name
    assert _snapshot(db_session) == before

    accepted = {name: write().status_code for name, write in open_writes.items()}
    assert accepted == {
        "risk insert": 201,
        "risk update": 200,
        "risk delete": 204,
        "reserve insert": 201,
        "reserve update": 200,
        "reserve delete": 204,
        "cost link update": 200,
        "cost link insert": 201,
    }
    assert _snapshot(db_session) != before


def test_k_05_an_id_not_in_the_approved_scenario_is_a_404_not_a_409(
    client: TestClient, db_session: Session
) -> None:
    """K-05 - "does the target exist?" before "may it be written?". Under an approved scenario a
    `PATCH`/`DELETE` of a risk or reserve id that is not the scenario's (a random id, and the id of
    another scenario's row) answers `404` with the one body - never `409 "approved"`; the same for a
    reserve or a cost `POST` linking a risk of another scenario. The contrast is the scenario's own
    row: `409 "approved"`."""
    approved = _scenario_with_rows(db_session, status=ScenarioStatus.APPROVED, name="Frozen")
    elsewhere = _scenario_with_rows(db_session, status=ScenarioStatus.DRAFT, name="Elsewhere")
    project_id, scenario_id = approved["project"].id, approved["scenario"].id
    tokens = _tokens(client, approved)

    bodies = []
    for missing_risk in (uuid.uuid4(), elsewhere["risk_edit"].id):
        path = risk_path(project_id, scenario_id, missing_risk)
        responses = [
            client.patch(
                path, json={"updated_at": tokens["risk_edit"], "name": "x"}, headers=HEADERS
            ),
            client.request(
                "DELETE", path, json={"updated_at": tokens["risk_edit"]}, headers=HEADERS
            ),
            client.post(
                reserves_path(project_id, scenario_id),
                json=RESERVE_BODY | {"risk_id": str(missing_risk)},
                headers=HEADERS,
            ),
            client.post(
                additional_costs_path(project_id, scenario_id),
                json=additional_cost_payload(approved["category"].id, risk_id=str(missing_risk)),
                headers=HEADERS,
            ),
            client.patch(
                additional_cost_path(project_id, scenario_id, approved["cost"].id),
                json={"updated_at": tokens["cost"], "risk_id": str(missing_risk)},
                headers=HEADERS,
            ),
        ]
        bodies += responses
    for missing_reserve in (uuid.uuid4(), elsewhere["reserve"].id):
        path = reserve_path(project_id, scenario_id, missing_reserve)
        bodies += [
            client.patch(
                path, json={"updated_at": tokens["reserve"], "amount": "1"}, headers=HEADERS
            ),
            client.request("DELETE", path, json={"updated_at": tokens["reserve"]}, headers=HEADERS),
        ]
    for response in bodies:
        assert response.status_code == 404, response.text

    own = client.patch(
        risk_path(project_id, scenario_id, approved["risk_edit"].id),
        json={"updated_at": tokens["risk_edit"], "name": "x"},
        headers=HEADERS,
    )
    assert own.status_code == 409 and "approved" in own.json()["detail"]


# --- K-05: the marker - stale is a 409 of its own, and it is per row -----------------------------


def _committed(engine: Engine, *, name: str = "Markers") -> dict[str, Any]:
    """A committed draft with the rows every write targets - the marker and race tests need real
    commits (`now()` is constant inside one test transaction)."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        fixture = _scenario_with_rows(setup, status=ScenarioStatus.DRAFT, name=name)
        second_risk = make_risk(setup, fixture["scenario"], name="second")
        second_reserve = make_reserve(
            setup, fixture["scenario"], amount=Decimal("200.0000"), start_month=MAR
        )
        setup.commit()
        return fixture | {
            "project_id": fixture["project"].id,
            "draft_id": fixture["scenario"].id,
            "risk_second": second_risk,
            "reserve_second": second_reserve,
        }


def test_k_05_a_stale_marker_is_a_409_told_apart_from_the_approved_409(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05 (ADR-0007) - for a risk, a reserve and a cost's risk link: edit with the marker (200,
    the
    marker moves), then again with the **old** marker: `409` naming the concurrency marker and not
    "approved" - on `PATCH` and on `DELETE` - and the row keeps the first edit. Mutation: the marker
    comparison dropped from the `UPDATE` (or the `DELETE`) - the stale write succeeds."""
    state = _committed(engine)
    project_id, scenario_id = state["project_id"], state["draft_id"]
    stale = _tokens(committing_client, state)
    targets = {
        "risk": (
            risk_path(project_id, scenario_id, state["risk_edit"].id),
            stale["risk_edit"],
            {"name": "first edit"},
            {"name": "second edit"},
        ),
        "reserve": (
            reserve_path(project_id, scenario_id, state["reserve"].id),
            stale["reserve"],
            {"amount": "150.0000"},
            {"amount": "175.0000"},
        ),
        "cost link": (
            additional_cost_path(project_id, scenario_id, state["cost"].id),
            stale["cost"],
            {"risk_id": str(state["risk_spare"].id)},
            {"risk_id": None},
        ),
    }
    for label, (path, token, first_edit, second_edit) in targets.items():
        first = committing_client.patch(
            path, json={"updated_at": token} | first_edit, headers=HEADERS
        )
        assert first.status_code == 200, (label, first.text)
        assert first.json()["updated_at"] != token, label
        second = committing_client.patch(
            path, json={"updated_at": token} | second_edit, headers=HEADERS
        )
        deleted = committing_client.request(
            "DELETE", path, json={"updated_at": token}, headers=HEADERS
        )
        assert (second.status_code, deleted.status_code) == (409, 409), label
        for refused in (second, deleted):
            assert "concurrency marker" in refused.json()["detail"], label
            assert "approved" not in refused.json()["detail"], label
    with Session(bind=engine) as check:
        assert check.get(ScenarioRisk, state["risk_edit"].id).name == "first edit"
        assert check.get(RiskReserve, state["reserve"].id).amount == Decimal("150.0000")
        assert check.get(AdditionalCost, state["cost"].id).risk_id == state["risk_spare"].id


def test_k_05_the_marker_is_per_row_editing_one_leaves_the_other_editable(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05 (ADR-0007, addendum SC-6-08) - two risks and two reserves of one scenario. Editing A
    moves
    A's marker (the contrast: A's old marker is now refused) and leaves B's exactly as it was, so
    B's edit with the marker read *before* A's edit succeeds. Mutation: the scenario's `updated_at`
    (or a table-wide one) as the token - A's edit would invalidate B's marker."""
    state = _committed(engine)
    project_id, scenario_id = state["project_id"], state["draft_id"]
    for label, first_path, second_path, field in (
        (
            "risk",
            risk_path(project_id, scenario_id, state["risk_edit"].id),
            risk_path(project_id, scenario_id, state["risk_second"].id),
            "name",
        ),
        (
            "reserve",
            reserve_path(project_id, scenario_id, state["reserve"].id),
            reserve_path(project_id, scenario_id, state["reserve_second"].id),
            "amount",
        ),
    ):
        listing = "risks" if label == "risk" else "reserves"
        listing_path = (
            risks_path(project_id, scenario_id)
            if label == "risk"
            else reserves_path(project_id, scenario_id)
        )

        def read(listing_path: str = listing_path, listing: str = listing) -> dict[str, str]:
            rows = committing_client.get(listing_path, headers=HEADERS).json()[listing]
            return {row["id"]: row["updated_at"] for row in rows}

        before = read()
        first_id, second_id = first_path.rsplit("/", 1)[1], second_path.rsplit("/", 1)[1]
        new_value = "renamed" if field == "name" else "321.0000"
        edited = committing_client.patch(
            first_path, json={"updated_at": before[first_id], field: new_value}, headers=HEADERS
        )
        assert edited.status_code == 200, (label, edited.text)
        after = read()
        assert after[first_id] != before[first_id], label
        assert after[second_id] == before[second_id], label

        other_value = "renamed second" if field == "name" else "654.0000"
        other = committing_client.patch(
            second_path, json={"updated_at": before[second_id], field: other_value}, headers=HEADERS
        )
        assert other.status_code == 200, (label, other.text)


# --- K-05: the race with the approval, on two connections ----------------------------------------


def _counts(engine: Engine, scenario_id: uuid.UUID) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE scenario_id = :id"),
                {"id": scenario_id},
            ).scalar_one()
            for table in ("scenario_risk", "risk_reserve", "additional_cost")
        }


def _content(engine: Engine, scenario_id: uuid.UUID) -> tuple[Any, ...]:
    """What the writes change, read from a separate connection."""
    with engine.connect() as connection:
        return (
            sorted(
                connection.execute(
                    sa.text("SELECT id, name FROM scenario_risk WHERE scenario_id = :id"),
                    {"id": scenario_id},
                ).all()
            ),
            sorted(
                connection.execute(
                    sa.text("SELECT id, amount FROM risk_reserve WHERE scenario_id = :id"),
                    {"id": scenario_id},
                ).all()
            ),
            sorted(
                connection.execute(
                    sa.text("SELECT id, risk_id FROM additional_cost WHERE scenario_id = :id"),
                    {"id": scenario_id},
                ).all()
            ),
        )


@pytest.mark.parametrize(
    "write_name",
    [
        "risk insert",
        "risk update",
        "risk delete",
        "reserve insert",
        "reserve update",
        "reserve delete",
        "cost link update",
        "cost link insert",
    ],
)
def test_k_05_an_approval_committing_concurrently_with_each_write_leaves_nothing_written(
    committing_client: TestClient, engine: Engine, write_name: str
) -> None:
    """K-05 (R-04), the race - one test per write statement, on two real connections: the write runs
    while the **real approval endpoint** holds the scenario row lock.

    The postcondition is the pair: "the scenario is approved" and "the write's effect is visible"
    never hold together, and the writer's own answer is `409` naming "approved". `blocked` is
    asserted - a run in which the write never waited says nothing about serialisation, and
    `approved` itself is asserted so the pair is not satisfied vacuously by a failed approval.
    Mutation: dropping `FOR UPDATE` from `app.data.scenario_guard.unapproved_scenario` (or not
    embedding it in this statement) - the write does not block, reads `draft` and commits into a
    calculation approved a moment later."""
    state = _committed(engine, name=f"Race {write_name}")
    tokens = _tokens(committing_client, state)
    write = _seven_writes(committing_client, state, tokens)[write_name]
    before = _content(engine, state["draft_id"])
    counts_before = _counts(engine, state["draft_id"])

    outcome = _race_a_child_write_against_the_approval(committing_client, engine, state, write)

    assert outcome["approval"].status_code == 200, outcome["approval"].text
    assert outcome["blocked"], f"the {write_name} never waited for a lock"
    assert _scenario_is_approved(engine, state["draft_id"]), "the approval itself failed"
    assert _content(engine, state["draft_id"]) == before, f"the {write_name} landed after approval"
    assert _counts(engine, state["draft_id"]) == counts_before
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]
