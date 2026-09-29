"""SC-6-08, QA pass - proofs added after the developer's own mutations, each for a mutation that
survived the first suite (see the QA report of SC-6-08 for the record of what survived and why).

- **K-04 / K-07** a risk of *another scenario of the same project* is a `404` on **every** write
  that can carry a risk link (cost create, cost edit, reserve create, reserve edit) - the first
  suite proved it for the reserve `POST` only, and the composite foreign key underneath turns
  the other three into a `409`, so a forgotten `WHERE` there was invisible.
- **K-06** the remap join is proven **independently of physical row order**: a source risk with no
  same-named risk in the copy must map to nothing - whichever row the planner returns last.
- **K-02** the what-if endpoint (`/what-if`) is byte-identical across every step of becoming
  double-represented; the first suite covered `/results` and `/compare` only, and the what-if path
  had a structural import test alone.
"""

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data.risk_copy import copied_risk_ids, remapped
from app.models import AdditionalCost, RiskReserve
from tests.conftest import (
    IN_SCOPE_USER,
    additional_cost_path,
    additional_cost_payload,
    additional_costs_path,
    as_caller,
    caller_holding,
    count_additional_costs,
    count_reserves,
    link_cost_to_risk,
    make_additional_cost,
    make_cost_category,
    make_project,
    make_reserve,
    make_risk,
    make_scenario,
    reserve_path,
    reserves_path,
    risks_path,
)
from tests.test_scenario_results import EVERYTHING, _ensure_statutory_bypass, _full_scenario
from tests.test_scenario_what_if import what_if_path

MAR = date(2026, 3, 1)
HEADERS = as_caller(IN_SCOPE_USER)
RESERVE_BODY = {
    "amount": "10.0000",
    "currency": "EUR",
    "reserve_type": "one_off",
    "start_month": "2026-03-01",
}


# --- K-04 / K-07: the link target must be a risk of THIS scenario, on all four link writes -------


def _link_writes(
    client: TestClient, fixture: dict[str, Any], risk_id: uuid.UUID
) -> list[tuple[str, Any]]:
    project_id, scenario_id = fixture["project"].id, fixture["scenario"].id
    cost, reserve = fixture["cost"], fixture["reserve"]
    return [
        (
            "cost create",
            client.post(
                additional_costs_path(project_id, scenario_id),
                json=additional_cost_payload(fixture["category"].id, risk_id=str(risk_id)),
                headers=HEADERS,
            ),
        ),
        (
            "cost edit",
            client.patch(
                additional_cost_path(project_id, scenario_id, cost.id),
                json={"updated_at": cost.updated_at.isoformat(), "risk_id": str(risk_id)},
                headers=HEADERS,
            ),
        ),
        (
            "reserve create",
            client.post(
                reserves_path(project_id, scenario_id),
                json=RESERVE_BODY | {"risk_id": str(risk_id)},
                headers=HEADERS,
            ),
        ),
        (
            "reserve edit",
            client.patch(
                reserve_path(project_id, scenario_id, reserve.id),
                json={"updated_at": reserve.updated_at.isoformat(), "risk_id": str(risk_id)},
                headers=HEADERS,
            ),
        ),
    ]


def _two_scenarios(session: Session) -> dict[str, Any]:
    project = make_project(session, name="Aurora QA", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", currency="EUR")
    sibling = make_scenario(session, project, name="Variant", currency="EUR")
    category = make_cost_category(session, name="Licences QA")
    return {
        "project": project,
        "scenario": scenario,
        "category": category,
        "own_risk": make_risk(session, scenario, name="Vendor delay"),
        "sibling_risk": make_risk(session, sibling, name="Vendor delay"),
        "cost": make_additional_cost(
            session, scenario, category, amount=Decimal("100.0000"), start_month=MAR
        ),
        "reserve": make_reserve(session, scenario, amount=Decimal("5.0000"), start_month=MAR),
    }


def test_k_04_a_risk_of_a_sibling_scenario_is_a_404_on_all_four_link_writes_and_own_risk_is_2xx(
    client: TestClient, db_session: Session
) -> None:
    """K-04 / K-07 - same project, same caller, same four requests; the **only** difference between
    the two runs is whose risk the id names. The sibling scenario's risk (deliberately the same
    name) is a `404` on cost create, cost edit, reserve create and reserve edit, and nothing is
    written; the scenario's own risk succeeds on all four. Mutation: dropping the
    `scenario_id = :scenario_id` narrowing (create) or the `EXISTS` (edit) from any one of the four
    statements turns that `404` into the composite foreign key's `409`."""
    fixture = _two_scenarios(db_session)
    costs_before, reserves_before = (
        count_additional_costs(db_session),
        count_reserves(db_session),
    )
    markers = (fixture["cost"].updated_at, fixture["reserve"].updated_at)

    foreign = _link_writes(client, fixture, fixture["sibling_risk"].id)
    assert [(name, response.status_code) for name, response in foreign] == [
        ("cost create", 404),
        ("cost edit", 404),
        ("reserve create", 404),
        ("reserve edit", 404),
    ], [response.text for _, response in foreign]
    db_session.expire_all()
    assert (count_additional_costs(db_session), count_reserves(db_session)) == (
        costs_before,
        reserves_before,
    )
    assert (fixture["cost"].updated_at, fixture["reserve"].updated_at) == markers
    assert fixture["cost"].risk_id is None and fixture["reserve"].risk_id is None

    own = _link_writes(client, fixture, fixture["own_risk"].id)
    assert [(name, response.status_code) for name, response in own] == [
        ("cost create", 201),
        ("cost edit", 200),
        ("reserve create", 201),
        ("reserve edit", 200),
    ], [response.text for _, response in own]
    assert all(response.json()["risk_id"] == str(fixture["own_risk"].id) for _, response in own)


# --- K-06: the remap join, independent of physical row order -------------------------------------


def test_k_06_a_source_risk_with_no_same_named_risk_in_the_copy_maps_to_nothing(
    db_session: Session,
) -> None:
    """K-06 - deterministic form of the decoy test (the first suite's needed the copy's risk to sit
    in the middle of the heap). Here the copy has **no** risk of the source risk's name, while the
    source itself and an unrelated scenario both hold one: the correct mapping is empty, whichever
    row a join without the copy's scenario id would return last. Contrast: once the copy has its
    own risk of that name, the mapping is exactly `{source: copy's own}`."""
    project = make_project(db_session, name="Remap QA", accessible_to=(IN_SCOPE_USER,))
    source = make_scenario(db_session, project, name="A", currency="EUR")
    copy = make_scenario(db_session, project, name="A copy", currency="EUR")
    other = make_scenario(db_session, project, name="B", currency="EUR")
    source_risk = make_risk(db_session, source, name="Vendor delay")
    make_risk(db_session, other, name="Vendor delay")

    assert copied_risk_ids(db_session, source.id, copy.id) == {}

    own = make_risk(db_session, copy, name="Vendor delay")
    assert copied_risk_ids(db_session, source.id, copy.id) == {source_risk.id: own.id}


def test_k_06_remapped_keeps_none_maps_known_links_and_fails_loudly_on_an_unmapped_one() -> None:
    """K-06 - the loud branch of the remap. A link whose risk has no copy is a broken cascade
    (risk copier not run first), and must raise, not silently become an unlinked row - the
    canaries catch a reordering only while this branch stays loud. Contrast: `None` stays `None` and
    a mapped id is mapped, with the same call shape."""
    source, copy = uuid.uuid4(), uuid.uuid4()
    assert remapped({source: copy}, None) is None
    assert remapped({source: copy}, source) == copy
    with pytest.raises(RuntimeError, match="risk copier must run before"):
        remapped({}, source)
    with pytest.raises(RuntimeError):
        remapped({source: copy}, uuid.uuid4())


# --- K-02: the what-if path is byte-identical across double representation -----------------------


def test_k_02_what_if_is_byte_identical_across_becoming_double_represented_and_back(
    client: TestClient, db_session: Session
) -> None:
    """K-02 (R-02) - `GET .../what-if?salary_raise_percent=10` on a complete scenario, captured
    (a) before, (b) with an unlinked reserve in a foreign currency, (c) with a risk linked to the
    cost event **and** to a reserve (`both`, verified through the risk read), (d) after unlinking
    the cost event. All four captures are byte-identical. Contrast: a genuine additional cost added
    afterwards **does** change the what-if bytes, so the capture is live, not constant.
    Mutation: a what-if that drops risk-linked cost events from its additional cost ("de-duplicate
    the double representation") or folds a reserve in changes (b) or (c)."""
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="WhatIfQA")
    cost = db_session.execute(
        sa.select(AdditionalCost).where(AdditionalCost.scenario_id == scenario.id)
    ).scalar_one()

    def capture() -> bytes:
        with caller_holding(*EVERYTHING):
            response = client.get(what_if_path(project.id, scenario.id, "10"))
        assert response.status_code == 200, response.text
        return response.content

    def representation() -> str:
        with caller_holding(*EVERYTHING):
            body = client.get(risks_path(project.id, scenario.id)).json()
        return {risk["name"]: risk["representation"] for risk in body["risks"]}["Vendor delay"]

    before = capture()
    assert json.loads(before)["additional_cost"]["amount"] == "2000.00"

    reserve = make_reserve(
        db_session, scenario, amount=Decimal("777.00"), start_month=MAR, currency="PLN"
    )
    assert capture() == before, "an unlinked reserve moved the what-if"

    risk = make_risk(db_session, scenario, name="Vendor delay")
    link_cost_to_risk(db_session, cost, risk)
    db_session.execute(
        sa.update(RiskReserve).where(RiskReserve.id == reserve.id).values(risk_id=risk.id)
    )
    db_session.flush()
    assert representation() == "both"
    assert capture() == before, "becoming double-represented moved the what-if"

    link_cost_to_risk(db_session, cost, None)
    assert representation() == "reserve"
    assert capture() == before, "undoing the double representation moved the what-if"

    extra = make_cost_category(db_session, name="Extra QA")
    make_additional_cost(
        db_session, scenario, extra, amount=Decimal("1.00"), start_month=MAR, currency="PLN"
    )
    assert capture() != before, "the capture is constant: it would not notice any change"
