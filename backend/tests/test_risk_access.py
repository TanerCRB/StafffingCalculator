"""SC-6-08, K-07 - who reaches a scenario's risks and reserves, and what the risk read may show
(ADR-0021, points 7 and 9; gate 1 Q-6, G-1, G-2; ADR-0005 addendum SC-6-08).

- **Scope is a `404`, never a `403`**, proven with a caller holding **every** permission, so a
  refusal is provably about scope and not about permission; one body for every "nothing here for
  you" case on the risk *and* the reserve paths; decided before any `409` and before any paging
  `422` (ADR-0017, point 8). Path confusion separately: a risk or reserve id of another project,
  sent
  to a URL of a project in scope, is the same `404` and writes nothing.
- **Permissions**: `STAFFING_READ` / `STAFFING_WRITE`, scenario-level only, no
  `PERSONNEL_COSTS_READ` conjunction, no new permission (`PLACEHOLDER_PERMISSIONS` canary
  unchanged).
- **The risk read returns kinds and counts only** - no amount, and no position-level amount either -
  and the additive field on the additional-cost read is the linked risk id or `null`.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import PLACEHOLDER_PERMISSIONS
from app.api.risk import RISK_NOT_FOUND_DETAIL
from app.api.risk_reserve import RESERVE_NOT_FOUND_DETAIL
from app.core.identity import Permission
from app.models import AdditionalCost, RiskReserve, ScenarioRisk
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    additional_cost_payload,
    additional_costs_path,
    as_caller,
    caller_holding,
    link_cost_to_risk,
    make_additional_cost,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_reserve,
    make_risk,
    make_scenario,
    make_staffing_position,
    reserve_path,
    reserves_path,
    risk_path,
    risks_path,
)

MAR = date(2026, 3, 1)
EVERYTHING = frozenset(Permission)
HEADERS = as_caller(IN_SCOPE_USER)
RESERVE_BODY = {
    "amount": "10.0000",
    "currency": "EUR",
    "reserve_type": "one_off",
    "start_month": "2026-03-01",
}


def _project_with_rows(session: Session, *, name: str, user: str) -> dict[str, Any]:
    project = make_project(session, name=name, accessible_to=(user,))
    scenario = make_scenario(session, project, name="Baseline", currency="EUR")
    category = make_cost_category(session, name=f"Recruitment {name}")
    risk = make_risk(session, scenario, name="Vendor delay")
    return {
        "project": project,
        "scenario": scenario,
        "category": category,
        "risk": risk,
        "reserve": make_reserve(
            session, scenario, amount=Decimal("8765.4321"), start_month=MAR, risk=risk
        ),
    }


def _snapshot(session: Session) -> tuple[Any, ...]:
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
                sa.select(RiskReserve.id, RiskReserve.amount, RiskReserve.updated_at)
            ).all()
        ),
        sorted(
            tuple(r)
            for r in session.execute(
                sa.select(AdditionalCost.id, AdditionalCost.risk_id, AdditionalCost.updated_at)
            ).all()
        ),
    )


# --- K-07: out of scope is the same 404 as absent, for every operation ---------------------------


def test_k_07_every_operation_outside_the_callers_scope_is_the_same_404_never_a_403(
    client: TestClient, db_session: Session
) -> None:
    """K-07 - a caller holding **every** permission; four addresses (a project the caller has no
    access to, a scenario id that does not exist, a scenario of another project in scope, a project
    that does not exist), and on each of them every operation of the risk and of the reserve path -
    reads, creates, edits and deletes, plus the two ways of linking a cost event to a risk, the
    writes with the target row's real marker so the refusal cannot be a stale marker's. All answer
    `404`, **one body**, on both paths, and the tables are unchanged. Contrast: the caller's own
    scenario reads `200` and edits `200`. Mutation: a `select(Scenario)` of the new path's own
    instead of `scenario_in_scope`."""
    mine = _project_with_rows(db_session, name="Aurora", user=IN_SCOPE_USER)
    theirs = _project_with_rows(db_session, name="Borealis", user=OUT_OF_SCOPE_USER)
    other_mine = _project_with_rows(db_session, name="Cassiopeia", user=IN_SCOPE_USER)
    token = theirs["risk"].updated_at.isoformat()

    addresses = [
        (theirs["project"].id, theirs["scenario"].id, theirs["risk"].id, theirs["reserve"].id),
        (mine["project"].id, uuid.uuid4(), mine["risk"].id, mine["reserve"].id),
        (
            mine["project"].id,
            other_mine["scenario"].id,
            other_mine["risk"].id,
            other_mine["reserve"].id,
        ),
        (uuid.uuid4(), mine["scenario"].id, mine["risk"].id, mine["reserve"].id),
    ]
    before = _snapshot(db_session)
    responses = []
    cost_link_responses = []
    with caller_holding(*EVERYTHING):
        for project_id, scenario_id, risk_id, reserve_id in addresses:
            risks = risks_path(project_id, scenario_id)
            reserves = reserves_path(project_id, scenario_id)
            responses += [
                client.get(risks),
                client.post(risks, json={"name": "new"}),
                client.patch(
                    risk_path(project_id, scenario_id, risk_id),
                    json={"updated_at": token, "name": "x"},
                ),
                client.request(
                    "DELETE",
                    risk_path(project_id, scenario_id, risk_id),
                    json={"updated_at": token},
                ),
                client.get(reserves),
                client.post(reserves, json=RESERVE_BODY),
                client.patch(
                    reserve_path(project_id, scenario_id, reserve_id),
                    json={"updated_at": token, "amount": "1.0000"},
                ),
                client.request(
                    "DELETE",
                    reserve_path(project_id, scenario_id, reserve_id),
                    json={"updated_at": token},
                ),
            ]
            cost_link_responses.append(
                client.post(
                    additional_costs_path(project_id, scenario_id),
                    json=additional_cost_payload(mine["category"].id, risk_id=str(risk_id)),
                )
            )
        own_read = client.get(risks_path(mine["project"].id, mine["scenario"].id))
        own_edit = client.patch(
            risk_path(mine["project"].id, mine["scenario"].id, mine["risk"].id),
            json={"updated_at": own_read.json()["risks"][0]["updated_at"], "name": "renamed"},
        )

    for response in responses:
        assert response.status_code == 404, response.text
    assert RISK_NOT_FOUND_DETAIL == RESERVE_NOT_FOUND_DETAIL
    assert len({response.content for response in responses}) == 1
    assert responses[0].json() == {"detail": RISK_NOT_FOUND_DETAIL}
    # The cost event's own path keeps its own (already accepted) one body - a `404` all the same.
    assert [r.status_code for r in cost_link_responses] == [404] * 4
    assert len({response.content for response in cost_link_responses}) == 1
    assert (own_read.status_code, own_edit.status_code) == (200, 200)
    after = {row[0] for row in _snapshot(db_session)[0]}
    assert after == {row[0] for row in before[0]}, "a refused operation added or removed a risk"
    assert _snapshot(db_session)[1:] == before[1:]


def test_k_07_scope_is_decided_before_a_409_and_before_a_paging_422(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (R-06; ADR-0017, point 8) - an **approved** scenario of a project the caller cannot see:
    a write answers `404`, not `409 "approved"` (a `409` would confirm the scenario exists); and a
    read with an unparsable or out-of-range `limit`/`offset` answers `404`, not `422`. Contrast: the
    caller's own scenario answers the `422` for the same paging parameters. Mutation: validating the
    paging parameters (or reading the status) before the scope check."""
    theirs = _project_with_rows(db_session, name="Borealis", user=OUT_OF_SCOPE_USER)
    db_session.execute(
        sa.text("UPDATE scenarios SET status = 'approved' WHERE id = :id"),
        {"id": theirs["scenario"].id},
    )
    mine = _project_with_rows(db_session, name="Aurora", user=IN_SCOPE_USER)

    with caller_holding(*EVERYTHING):
        writes = [
            client.post(
                risks_path(theirs["project"].id, theirs["scenario"].id), json={"name": "x"}
            ),
            client.post(
                reserves_path(theirs["project"].id, theirs["scenario"].id), json=RESERVE_BODY
            ),
        ]
        reads = [
            client.get(base(theirs["project"].id, theirs["scenario"].id) + query)
            for base in (risks_path, reserves_path)
            for query in ("?limit=abc", "?limit=0", "?limit=99999", "?offset=-3")
        ]
        own = [
            client.get(base(mine["project"].id, mine["scenario"].id) + "?limit=abc")
            for base in (risks_path, reserves_path)
        ]

    for response in writes + reads:
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": RISK_NOT_FOUND_DETAIL}
    assert [response.status_code for response in own] == [422, 422]


def test_k_07_a_risk_or_reserve_id_of_another_project_sent_to_a_project_in_scope_is_a_404(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (path confusion) - the caller owns two projects; ids of the second project's risk and
    reserve sent to the first project's addresses are the same `404` and change nothing. Contrast:
    the same request addressed to the second project's own URL is a `200`."""
    first = _project_with_rows(db_session, name="Aurora", user=IN_SCOPE_USER)
    second = _project_with_rows(db_session, name="Cassiopeia", user=IN_SCOPE_USER)
    before = _snapshot(db_session)
    token = second["risk"].updated_at.isoformat()

    with caller_holding(*EVERYTHING):
        confused = [
            client.patch(
                risk_path(first["project"].id, first["scenario"].id, second["risk"].id),
                json={"updated_at": token, "name": "x"},
            ),
            client.request(
                "DELETE",
                risk_path(first["project"].id, first["scenario"].id, second["risk"].id),
                json={"updated_at": token},
            ),
            client.patch(
                reserve_path(first["project"].id, first["scenario"].id, second["reserve"].id),
                json={"updated_at": token, "amount": "1.0000"},
            ),
            client.post(
                reserves_path(first["project"].id, first["scenario"].id),
                json=RESERVE_BODY | {"risk_id": str(second["risk"].id)},
            ),
        ]
    assert [response.status_code for response in confused] == [404] * 4
    assert _snapshot(db_session) == before

    with caller_holding(*EVERYTHING):
        genuine = client.patch(
            risk_path(second["project"].id, second["scenario"].id, second["risk"].id),
            json={"updated_at": token, "name": "x"},
        )
    assert genuine.status_code == 200, genuine.text


# --- K-07: permissions ------------------------------


def test_k_07_staffing_read_and_staffing_write_are_the_only_permissions_and_no_cost_conjunction(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (Q-6 = A) - a caller with exactly `STAFFING_READ` reads and cannot write (`403`);
    exactly
    `STAFFING_WRITE` writes and cannot read (`403`); a caller with both and **without**
    `PERSONNEL_COSTS_READ` (on a project whose access row grants no cost visibility) reads and
    writes
    everything, with no field withheld. `PLACEHOLDER_PERMISSIONS` is the set it was: it holds the
    two
    staffing permissions, not `PERSONNEL_COSTS_READ`, and the `Permission` enum has no risk-specific
    member. Mutation: adding a `PERSONNEL_COSTS_READ` (or new-permission) dependency to any of the
    eight endpoints."""
    fixture = _project_with_rows(db_session, name="Aurora", user=IN_SCOPE_USER)
    project_id, scenario_id = fixture["project"].id, fixture["scenario"].id
    token = fixture["risk"].updated_at.isoformat()

    def outcomes() -> dict[str, int]:
        return {
            "read risks": client.get(risks_path(project_id, scenario_id)).status_code,
            "read reserves": client.get(reserves_path(project_id, scenario_id)).status_code,
            "write risk": client.post(
                risks_path(project_id, scenario_id), json={"name": f"n{uuid.uuid4()}"}
            ).status_code,
            "write reserve": client.post(
                reserves_path(project_id, scenario_id), json=RESERVE_BODY
            ).status_code,
            "patch risk": client.patch(
                risk_path(project_id, scenario_id, fixture["risk"].id),
                json={"updated_at": token, "name": "z"},
            ).status_code,
        }

    with caller_holding(Permission.STAFFING_READ):
        read_only = outcomes()
    with caller_holding(Permission.STAFFING_WRITE):
        write_only = outcomes()
    with caller_holding(Permission.STAFFING_READ, Permission.STAFFING_WRITE):
        both = outcomes()

    assert read_only == {
        "read risks": 200,
        "read reserves": 200,
        "write risk": 403,
        "write reserve": 403,
        "patch risk": 403,
    }
    assert write_only == {
        "read risks": 403,
        "read reserves": 403,
        "write risk": 201,
        "write reserve": 201,
        "patch risk": 200,
    }
    assert both["read risks"] == both["read reserves"] == 200
    assert Permission.PERSONNEL_COSTS_READ not in PLACEHOLDER_PERMISSIONS
    assert {Permission.STAFFING_READ, Permission.STAFFING_WRITE} <= PLACEHOLDER_PERMISSIONS
    assert not [p for p in Permission if "RISK" in p.name or "RESERVE" in p.name]


# --- K-07: what the risk read shows --------------------------------------------------------------


def test_k_07_the_risk_read_returns_kinds_and_counts_only_never_an_amount(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (G-1) - a risk with a linked reserve (8765.4321) and a linked **position-level** cost
    (4321.5000) on a `headcount = 1` position: the read carries exactly `id, name, representation,
    double_represented, cost_event_count, reserve_count, updated_at` - and the body contains neither
    amount, in any spelling, nor the position id. Contrast: the additional-cost and reserve reads of
    the same scenario **do** carry those amounts, so the absence is the risk read's and not the
    fixture's. Mutation: adding an `amount`/`total` field to the risk schema."""
    fixture = _project_with_rows(db_session, name="Aurora", user=IN_SCOPE_USER)
    scenario, project = fixture["scenario"], fixture["project"]
    position = make_staffing_position(
        db_session, scenario, make_dimension_tuple(db_session), headcount=1, start_date=MAR
    )
    cost = make_additional_cost(
        db_session,
        scenario,
        fixture["category"],
        amount=Decimal("4321.5000"),
        start_month=MAR,
        position=position,
    )
    link_cost_to_risk(db_session, cost, fixture["risk"])

    risk_read = client.get(risks_path(project.id, scenario.id), headers=HEADERS)
    cost_read = client.get(additional_costs_path(project.id, scenario.id), headers=HEADERS)
    reserve_read = client.get(reserves_path(project.id, scenario.id), headers=HEADERS)

    (risk,) = risk_read.json()["risks"]
    assert set(risk) == {
        "id",
        "name",
        "representation",
        "double_represented",
        "cost_event_count",
        "reserve_count",
        "updated_at",
    }
    assert risk["representation"] == "both" and risk["double_represented"] is True
    assert set(risk_read.json()) == {"scenario_id", "scenario_status", "total", "risks"}
    for forbidden in ("4321", "8765", str(position.id), "amount", "position"):
        assert forbidden not in risk_read.text, forbidden
    assert "4321.5000" in cost_read.text and "8765.4321" in reserve_read.text


def test_k_07_the_additive_field_on_the_additional_cost_read_is_the_linked_risk_id_or_null(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (G-2) - each cost of the additional-cost read gains exactly one key, `risk_id`: the
    linked risk's id, or `null` for an unlinked cost. Nothing else about the risk (name, count,
    state) or any reserve figure appears there. Mutation: returning the risk's name or
    representation in its place."""
    fixture = _project_with_rows(db_session, name="Aurora", user=IN_SCOPE_USER)
    scenario, project = fixture["scenario"], fixture["project"]
    linked = make_additional_cost(
        db_session, scenario, fixture["category"], amount=Decimal("11.0000"), start_month=MAR
    )
    make_additional_cost(
        db_session, scenario, fixture["category"], amount=Decimal("22.0000"), start_month=MAR
    )
    link_cost_to_risk(db_session, linked, fixture["risk"])

    response = client.get(additional_costs_path(project.id, scenario.id), headers=HEADERS)

    costs = {cost["amount"]: cost for cost in response.json()["costs"]}
    assert costs["11.0000"]["risk_id"] == str(fixture["risk"].id)
    assert costs["22.0000"]["risk_id"] is None
    assert set(costs["11.0000"]) == {
        "id",
        "category_id",
        "category_name",
        "position_id",
        "risk_id",
        "amount",
        "currency",
        "cost_type",
        "start_month",
        "end_month",
        "funding_source",
        "updated_at",
    }
    for leaked in ("Vendor delay", "8765", "representation", "reserve"):
        assert leaked not in response.text, leaked


def test_k_07_a_risk_and_a_reserve_carry_no_position_and_refuse_one_in_a_request(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (Q-6 = A, scenario-level only) - a request naming a `position_id` (or any personnel-cost
    or free-text field) on a risk or a reserve is a `422` (`extra="forbid"`), and the responses have
    no such key. Contrast: the same body without it is the `201`."""
    fixture = _project_with_rows(db_session, name="Aurora", user=IN_SCOPE_USER)
    project_id, scenario_id = fixture["project"].id, fixture["scenario"].id
    position_id = str(uuid.uuid4())

    for extra in (
        {"position_id": position_id},
        {"description": "note"},
        {"person": "x"},
        {"cost_rate": "1.00"},
    ):
        assert (
            client.post(
                risks_path(project_id, scenario_id), json={"name": "n"} | extra, headers=HEADERS
            ).status_code
            == 422
        ), extra
        assert (
            client.post(
                reserves_path(project_id, scenario_id), json=RESERVE_BODY | extra, headers=HEADERS
            ).status_code
            == 422
        ), extra

    risk = client.post(risks_path(project_id, scenario_id), json={"name": "clean"}, headers=HEADERS)
    reserve = client.post(
        reserves_path(project_id, scenario_id), json=RESERVE_BODY, headers=HEADERS
    )
    assert (risk.status_code, reserve.status_code) == (201, 201)
    assert "position_id" not in risk.json() and "position_id" not in reserve.json()
