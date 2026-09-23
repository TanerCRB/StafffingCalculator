"""SC-4-01, K-05 and K-11 — who reaches a scenario's rule and revenue, and what the answer carries.

- **K-05** a rule or revenue of a scenario outside the caller's scope is a `404` indistinguishable
  from a scenario that does not exist — for the read **and for the write**, and before any `409`
  could be reached (ADR-0005, addendum 2026-09-23 SC-4-01, point 1).
- **K-11** the answer carries no cost field — proven by **equality of the field set** at every level
  of the payload, not by a `not in` over names somebody thought of (point 3) — and each of the two
  new permissions has a refusal test in which the refused caller holds **every other permission**,
  `STAFFING_*`, `CATALOG_*` and `PERSONNEL_COSTS_READ` included (point 2).
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.commercial_terms import COMMERCIAL_TERMS_NOT_FOUND_DETAIL
from app.core.identity import Permission
from app.models import CommercialTerms, ScenarioStatus, TmTerms
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    caller_holding,
    commercial_terms_path,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
)

MAR = date(2026, 3, 1)
TM = {"model_type": "time_and_material"}


def _count_rules(session: Session) -> int:
    session.expire_all()
    return session.execute(sa.select(sa.func.count()).select_from(CommercialTerms)).scalar_one()


def _count_details(session: Session) -> int:
    return session.execute(sa.select(sa.func.count()).select_from(TmTerms)).scalar_one()


def _priced_scenario(session: Session, *, month: date = MAR, project_name: str = "Aurora"):
    project = make_project(session, name=project_name, accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    dimensions = make_dimension_tuple(session, suffix=f" {project_name}")
    position = make_staffing_position(session, scenario, dimensions, start_date=month)
    make_allocation(session, position, period_month=month)
    make_rate(
        session,
        dimensions,
        effective_from=date(2026, 1, 1),
        default_cost_rate=Decimal("120.0000"),
        default_selling_rate=Decimal("200.0000"),
        currency="PLN",
    )
    return project, scenario, position


# --- K-05: out of scope is indistinguishable from absent, for the read and the write --------------


def test_k_05_the_rule_of_a_scenario_outside_the_callers_scope_is_the_same_404_as_no_scenario(
    client: TestClient, db_session: Session
) -> None:
    """K-05, read and write — four addresses, one answer: status, body and length identical.

    1. a scenario of a project the caller has no access to — with a **rule already set**, so a
       leaking read would have something to show;
    2. a scenario id that does not exist;
    3. a scenario of a project in scope, addressed through **another** project in scope;
    4. a project id that does not exist.

    The contrast is the caller's own scenario: `200` on the read, so the `404`s are about scope and
    not about a broken path. Mutation: a `select(Scenario)` of the path's own instead of
    `scenario_in_scope` — address 1 then answers `200` with someone else's revenue.
    """
    mine_project, mine, _ = _priced_scenario(db_session)
    theirs_project = make_project(
        db_session, name="Borealis", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    theirs = make_scenario(db_session, theirs_project, name="Theirs")
    make_commercial_terms(db_session, theirs)
    other_mine_project = make_project(db_session, name="Cassiopeia", accessible_to=(IN_SCOPE_USER,))
    other_mine = make_scenario(db_session, other_mine_project, name="Mine too")

    addresses = [
        commercial_terms_path(theirs_project.id, theirs.id),
        commercial_terms_path(mine_project.id, uuid.uuid4()),
        commercial_terms_path(mine_project.id, other_mine.id),
        commercial_terms_path(uuid.uuid4(), mine.id),
    ]
    reads = [client.get(path, headers=as_caller(IN_SCOPE_USER)) for path in addresses]
    rules_before = _count_rules(db_session)
    writes = [client.post(path, json=TM, headers=as_caller(IN_SCOPE_USER)) for path in addresses]

    for response in (*reads, *writes):
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": COMMERCIAL_TERMS_NOT_FOUND_DETAIL}
    assert len({response.content for response in (*reads, *writes)}) == 1
    assert _count_rules(db_session) == rules_before, "a refused write wrote a rule"

    contrast = client.get(
        commercial_terms_path(mine_project.id, mine.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert contrast.status_code == 200, contrast.text


def test_k_05_a_write_to_an_approved_scenario_outside_the_scope_is_404_not_409(
    client: TestClient, db_session: Session
) -> None:
    """K-05's precedence — the `409`s of the write path cannot confirm a scenario exists.

    Two scenarios outside the caller's scope, each of which would earn a `409` if it were in scope:
    one `approved`, one that already has a rule. Both answer the same `404` as a scenario that does
    not exist. The contrast is the same two states inside the scope: `409`, so the refusals exist
    and the `404` above is the precedence, not their absence. Mutation: the scope resolved after the
    guarded insert (a diagnosis naming "approved" for an invisible scenario).
    """
    theirs_project = make_project(
        db_session, name="Borealis", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    theirs_approved = make_scenario(
        db_session, theirs_project, name="Approved", status=ScenarioStatus.APPROVED
    )
    theirs_with_rule = make_scenario(db_session, theirs_project, name="Has a rule")
    make_commercial_terms(db_session, theirs_with_rule)
    mine_project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    mine_approved = make_scenario(
        db_session, mine_project, name="Approved", status=ScenarioStatus.APPROVED
    )
    mine_with_rule = make_scenario(db_session, mine_project, name="Has a rule")
    make_commercial_terms(db_session, mine_with_rule)
    nowhere = client.post(
        commercial_terms_path(theirs_project.id, uuid.uuid4()),
        json=TM,
        headers=as_caller(IN_SCOPE_USER),
    )

    for scenario in (theirs_approved, theirs_with_rule):
        response = client.post(
            commercial_terms_path(theirs_project.id, scenario.id),
            json=TM,
            headers=as_caller(IN_SCOPE_USER),
        )
        assert response.status_code == 404, response.text
        assert response.content == nowhere.content

    for scenario in (mine_approved, mine_with_rule):
        response = client.post(
            commercial_terms_path(mine_project.id, scenario.id),
            json=TM,
            headers=as_caller(IN_SCOPE_USER),
        )
        assert response.status_code == 409, response.text


# --- K-11: no cost field, by equality of the field set --------------------------------------------

RESPONSE_FIELDS = {"scenario_id", "scenario_status", "commercial_terms", "revenue"}
TERMS_FIELDS = {"id", "model_type", "updated_at"}
REVENUE_FIELDS = {"state", "amount", "currency", "assumptions_used"}
ASSUMPTIONS_FIELDS = {
    "model_type",
    "hours_source",
    "vendor_axis",
    "rate_source",
    "rate_windows",
    "unresolved_months",
    "currencies",
}
WINDOW_FIELDS = {
    "source_rate_id",
    "effective_from",
    "effective_to",
    "default_selling_rate",
    "currency",
}
UNRESOLVED_FIELDS = {"position_id", "period_month"}


def _assert_field_sets(body: dict[str, Any]) -> None:
    """Every level of the payload, compared by **equality** with the decided field set.

    A cost, a profit, a margin, a `default_cost_rate` — or anything else nobody decided — added at
    any level fails here the day it is added (ADR-0005, addendum 2026-09-23 SC-4-01, point 3).
    """
    assert set(body) == RESPONSE_FIELDS
    if body["commercial_terms"] is not None:
        assert set(body["commercial_terms"]) == TERMS_FIELDS
    assert set(body["revenue"]) == REVENUE_FIELDS
    assumptions = body["revenue"]["assumptions_used"]
    assert set(assumptions) == ASSUMPTIONS_FIELDS
    for window in assumptions["rate_windows"]:
        assert set(window) == WINDOW_FIELDS
    for month in assumptions["unresolved_months"]:
        assert set(month) == UNRESOLVED_FIELDS


def test_k_11_the_rule_and_revenue_carry_no_cost_field_on_the_write_and_on_the_read(
    client: TestClient, db_session: Session
) -> None:
    """K-11 — the field set, by equality, on a calculated revenue **and** on a named state.

    The catalogue rate behind it has a cost (120) different from its selling rate (200); neither the
    cost figure nor any field that could carry it appears. Both shapes are checked because the
    named-state shape carries `unresolved_months` and the calculated one carries `rate_windows` —
    either could grow a field the other does not have. The caller holds `PERSONNEL_COSTS_READ` here,
    so the absence is a property of the schema, not of a gate that happened to be closed.
    """
    project, scenario, position = _priced_scenario(db_session)
    everything = frozenset(Permission)
    with caller_holding(*everything):
        written = client.post(commercial_terms_path(project.id, scenario.id), json=TM)
        read = client.get(commercial_terms_path(project.id, scenario.id))

    for response in (written, read):
        assert response.status_code in (200, 201), response.text
        body = response.json()
        _assert_field_sets(body)
        assert body["revenue"]["state"] == "calculated"
        assert body["revenue"]["assumptions_used"]["rate_windows"]
        assert "120.0000" not in response.text

    # The named-state shape: a month in a catalogue gap.
    make_allocation(db_session, position, period_month=date(2025, 12, 1))
    with caller_holding(*everything):
        unpriced = client.get(commercial_terms_path(project.id, scenario.id))
    body = unpriced.json()
    _assert_field_sets(body)
    assert body["revenue"]["state"] == "no_rate"
    assert body["revenue"]["assumptions_used"]["unresolved_months"]


def test_k_11_commercial_read_is_refused_to_a_caller_holding_every_other_permission(
    client: TestClient, db_session: Session
) -> None:
    """K-11 — `COMMERCIAL_READ` is its own permission: every other one, and still `403`.

    The contrast is `COMMERCIAL_READ` **alone**: `200`. That is also the proof of ADR-0005's point 3
    — no conjunction with `PERSONNEL_COSTS_READ` (nor with `STAFFING_READ`/`CATALOG_READ`): the
    revenue is readable without any of them. Mutation: `require_permission(STAFFING_READ)` or
    `PROJECT_READ` on the endpoint — the refused caller holds both and gets through.
    """
    project, scenario, _ = _priced_scenario(db_session)
    make_commercial_terms(db_session, scenario)
    path = commercial_terms_path(project.id, scenario.id)

    with caller_holding(*(set(Permission) - {Permission.COMMERCIAL_READ})):
        refused = client.get(path)
    with caller_holding(Permission.COMMERCIAL_READ):
        allowed = client.get(path)

    assert refused.status_code == 403, refused.text
    assert "200.0000" not in refused.text
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["revenue"]["amount"] == "20000.00"


def test_k_11_commercial_write_is_refused_to_a_caller_holding_every_other_permission(
    client: TestClient, db_session: Session
) -> None:
    """K-11 — `COMMERCIAL_WRITE` is its own permission, and a refused write writes nothing.

    Every other permission, `COMMERCIAL_READ` included, and still `403` with zero rules and zero
    details rows. The contrast is `COMMERCIAL_WRITE` alone: `201`. Mutation: the endpoint declaring
    `COMMERCIAL_READ` (or `STAFFING_WRITE`) instead.
    """
    project, scenario, _ = _priced_scenario(db_session)
    path = commercial_terms_path(project.id, scenario.id)

    with caller_holding(*(set(Permission) - {Permission.COMMERCIAL_WRITE})):
        refused = client.post(path, json=TM)
    assert refused.status_code == 403, refused.text
    assert (_count_rules(db_session), _count_details(db_session)) == (0, 0)

    with caller_holding(Permission.COMMERCIAL_WRITE):
        allowed = client.post(path, json=TM)
    assert allowed.status_code == 201, allowed.text
    assert (_count_rules(db_session), _count_details(db_session)) == (1, 1)


def test_the_request_carries_nothing_but_the_model(client: TestClient, db_session: Session) -> None:
    """A rate, a cap or a day length in the body is a `422`, not a field silently ignored (ADR-0003,
    points 4 and 7) — and an unknown model is a `422` naming the field. Nothing is written."""
    project, scenario, _ = _priced_scenario(db_session)
    path = commercial_terms_path(project.id, scenario.id)

    for body in (
        {**TM, "hourly_rate": "250.00"},
        {**TM, "hours_per_billable_day": "8"},
        {"model_type": "fixed_price"},
        {},
    ):
        response = client.post(path, json=body, headers=as_caller(IN_SCOPE_USER))
        assert response.status_code == 422, (body, response.text)
    assert _count_rules(db_session) == 0
