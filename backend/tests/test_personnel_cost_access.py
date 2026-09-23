"""SC-5-01, K-04/K-05/K-06 — who reaches a scenario's base personnel cost, and who sees the figure.

- **K-04** the amount **and** the cost rates in `assumptions_used` are present only under the
  conjunction `PERSONNEL_COSTS_READ` ∧ `project_access.can_view_personnel_costs` **for the project
  this scenario belongs to**; otherwise `200` with both `null` — never a `403` (ADR-0005, aneks
  2026-09-23 SC-5-01, points 1–2).
- **K-05** a scenario outside the caller's scope is a `404` indistinguishable from one that does not
  exist — proven with a caller holding every permission and the cost flag on their own projects.
- **K-06** the SC-4-01 revenue answer still carries no cost field **with the cost gate open** — the
  existing `test_k_11_*` proves it with the gate closed (flag unset), which says nothing about a
  caller for whom a cost field would be allowed.

The positive branch of the gate is reachable only through `dependency_overrides`
(`caller_holding`): the placeholder does not grant `PERSONNEL_COSTS_READ`, and SC-5-01 does not
change that (point 6). The flag is set directly in the database — no path grants it.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.personnel_cost import PERSONNEL_COST_NOT_FOUND_DETAIL
from app.api.response_shaping import (
    PERSONNEL_COST_FIELDS,
    SCENARIO_COST_FIELDS,
    shape_scenario_personnel_cost,
)
from app.core.identity import CallerIdentity, Permission
from app.data.personnel_cost import scenario_cost_for_caller
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    approve_path,
    as_caller,
    caller_holding,
    commercial_terms_path,
    grant_personnel_cost_visibility,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
)
from tests.test_commercial_terms_access import _assert_field_sets as assert_revenue_field_sets

MAR = date(2026, 3, 1)
COST_RATE_TEXT = "120.0000"
COST_AMOUNT_TEXT = "14400.00"
"""120 planned hours × 120 per hour. Both strings are what a leak would look like in the body."""

EVERYTHING = frozenset(Permission)
WITHOUT_COST_PERMISSION = EVERYTHING - {Permission.PERSONNEL_COSTS_READ}


def personnel_cost_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/personnel-cost"


def _costed_scenario(
    session: Session, *, name: str = "Aurora", cost_visible: bool = False
):
    """A project in scope (flag as asked), a draft scenario, one costed position: 14400.00 PLN."""
    project = make_project(
        session,
        name=name,
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,) if cost_visible else (),
    )
    scenario = make_scenario(session, project, name="Baseline")
    dimensions = make_dimension_tuple(session, suffix=f" {name}")
    position = make_staffing_position(session, scenario, dimensions, start_date=MAR)
    make_allocation(session, position, period_month=MAR)
    make_rate(
        session,
        dimensions,
        effective_from=date(2026, 1, 1),
        default_cost_rate=Decimal(COST_RATE_TEXT),
        default_selling_rate=Decimal("200.0000"),
        currency="PLN",
    )
    return project, scenario, position


def _read(client: TestClient, path: str, permissions: frozenset[Permission]):
    with caller_holding(*permissions):
        return client.get(path)


def _assert_withheld(response, state: str = "calculated") -> None:
    """`200`, the scenario and its state intact, the amount **and** the rates `null`, and neither
    figure anywhere in the body."""
    assert response.status_code == 200, response.text
    cost = response.json()["personnel_cost"]
    assert cost["state"] == state
    assert cost["cost_basis"] == "base"
    assert cost["amount"] is None
    assert cost["assumptions_used"] is None
    assert COST_RATE_TEXT not in response.text
    assert COST_AMOUNT_TEXT not in response.text


def _assert_visible(response) -> None:
    assert response.status_code == 200, response.text
    cost = response.json()["personnel_cost"]
    assert (cost["state"], cost["amount"]) == ("calculated", COST_AMOUNT_TEXT)
    assert [w["default_cost_rate"] for w in cost["assumptions_used"]["rate_windows"]] == [
        COST_RATE_TEXT
    ]


# --- K-04: the conjunction, on the amount and on the rates ----------------------------------------


def test_k_04_the_cost_and_its_rates_are_present_only_under_both_halves_of_the_conjunction(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — the four corners of the conjunction, on one scenario, the flag toggled in the
    database.

    | `PERSONNEL_COSTS_READ` | flag  | answer                       |
    |------------------------|-------|------------------------------|
    | no                     | false | `200`, amount and rates null |
    | yes                    | false | `200`, amount and rates null |
    | no                     | true  | `200`, amount and rates null |
    | yes                    | true  | `200`, 14400.00 and 120.0000 |

    Mutations killed: the gate reading the permission alone (row 2 leaks), the flag alone (row 3
    leaks), `or` for `and` (rows 2 and 3 leak); `SCENARIO_COST_FIELDS` emptied (every row leaks) or
    reduced to `{"amount"}` (the rate `120.0000` stays in `assumptions_used` in rows 1–3 — the same
    figure one division away). The last row is the contrast without which rows 1–3 would be
    satisfied by a gate that never opens.
    """
    project, scenario, _ = _costed_scenario(db_session)
    path = personnel_cost_path(project.id, scenario.id)

    _assert_withheld(_read(client, path, WITHOUT_COST_PERMISSION))
    _assert_withheld(_read(client, path, EVERYTHING))

    grant_personnel_cost_visibility(db_session, project_id=project.id, user_id=IN_SCOPE_USER)
    _assert_withheld(_read(client, path, WITHOUT_COST_PERMISSION))
    _assert_visible(_read(client, path, EVERYTHING))


def test_k_04_the_flag_is_the_one_on_the_scenarios_own_project_not_on_another_assignment(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — the caller has the flag on project B and **not** on project A (the scenario's). A
    cost of A is withheld; the contrast, a cost of B, is shown.

    B is created first, so "the caller's first assignment" and "any assignment with the flag" both
    point at B. Mutation: the flag resolved per caller (any/first `project_access` row) instead of
    per (caller, project) — the cost of A leaks.
    """
    project_b, scenario_b, _ = _costed_scenario(db_session, name="Borealis", cost_visible=True)
    project_a, scenario_a, _ = _costed_scenario(db_session, name="Aurora", cost_visible=False)

    _assert_withheld(
        _read(client, personnel_cost_path(project_a.id, scenario_a.id), EVERYTHING)
    )
    _assert_visible(_read(client, personnel_cost_path(project_b.id, scenario_b.id), EVERYTHING))


def test_k_04_a_refusal_is_of_the_field_never_of_the_scenario(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — "odmowa = `200` bez pola, nie `403`" and `STAFFING_READ` is the endpoint's permission.

    1. `STAFFING_READ` **alone** (no `PERSONNEL_COSTS_READ`, nothing else): `200`, withheld. Kills
       `require_permission(PERSONNEL_COSTS_READ)` on the endpoint (a `403` here) and the gate
       answering a closed conjunction with an exception.
    2. Every permission **except** `STAFFING_READ`, flag set: `403`, and no figure in the body.
       Kills the endpoint declaring anything else (`PROJECT_READ`, `COMMERCIAL_READ`, …) — the
       refused caller holds all of them.
    """
    project, scenario, _ = _costed_scenario(db_session, cost_visible=True)
    path = personnel_cost_path(project.id, scenario.id)

    _assert_withheld(_read(client, path, frozenset({Permission.STAFFING_READ})))

    refused = _read(client, path, EVERYTHING - {Permission.STAFFING_READ})
    assert refused.status_code == 403, refused.text
    assert COST_RATE_TEXT not in refused.text
    assert COST_AMOUNT_TEXT not in refused.text


def test_k_04_the_running_placeholder_never_sees_the_cost_even_where_the_flag_is_set(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — the production identity path: the header placeholder, flag set on the project.

    `PLACEHOLDER_PERMISSIONS` does not hold `PERSONNEL_COSTS_READ` (unchanged by SC-5-01, point 6),
    so every caller of the running system gets the withheld answer — the gate's positive branch is
    reachable from a test only. Mutation: `PERSONNEL_COSTS_READ` added to the placeholder.
    """
    project, scenario, _ = _costed_scenario(db_session, cost_visible=True)

    response = client.get(
        personnel_cost_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    _assert_withheld(response)


def test_k_04_a_named_state_and_an_approved_snapshot_are_gated_the_same_way(
    client: TestClient, db_session: Session
) -> None:
    """K-04 on the two other shapes a cost comes in.

    1. A named state (`no_cost_rate`): the state is shown, `assumptions_used` — which names the
       uncosted position and month **and** the cost rates of the months that did resolve — is not.
    2. An approved scenario, read from `approved_snapshot_catalog_default_rate` — the first reader
       of that table's `default_cost_rate` (ADR-0005, aneks SC-4-01, point 7): withheld without the
       flag, shown with it. Mutation: the snapshot path shaped by another function than the gate.
    """
    project, scenario, position = _costed_scenario(db_session)
    make_allocation(db_session, position, period_month=date(2025, 12, 1))
    path = personnel_cost_path(project.id, scenario.id)
    _assert_withheld(_read(client, path, EVERYTHING), state="no_cost_rate")

    approved_project, approved, _ = _costed_scenario(db_session, name="Approved")
    approval = client.post(
        approve_path(approved_project.id, approved.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert approval.status_code == 200, approval.text
    approved_path = personnel_cost_path(approved_project.id, approved.id)
    _assert_withheld(_read(client, approved_path, EVERYTHING))

    grant_personnel_cost_visibility(
        db_session, project_id=approved_project.id, user_id=IN_SCOPE_USER
    )
    shown = _read(client, approved_path, EVERYTHING)
    _assert_visible(shown)
    assert shown.json()["personnel_cost"]["assumptions_used"]["rate_source"] == "approved_snapshot"


def test_k_04_the_gate_refuses_to_shape_one_callers_view_with_another_callers_identity(
    db_session: Session,
) -> None:
    """K-04's identity guard — a view built for user A, shaped with user B's permission set, is an
    error rather than a silently widened gate (the `_without_personnel_costs` assertion, applied to
    the third shaping function). The contrast is the same view shaped for A.
    """
    project, scenario, _ = _costed_scenario(db_session, cost_visible=True)
    anna = CallerIdentity(user_id=IN_SCOPE_USER, permissions=EVERYTHING)
    view = scenario_cost_for_caller(db_session, anna, project.id, scenario.id)
    assert view is not None

    assert shape_scenario_personnel_cost(view, anna).personnel_cost.amount == Decimal("14400.00")
    with pytest.raises(AssertionError):
        shape_scenario_personnel_cost(
            view, CallerIdentity(user_id=OUT_OF_SCOPE_USER, permissions=EVERYTHING)
        )


def test_k_04_the_scenario_cost_fields_are_their_own_set_and_the_projects_stays_empty() -> None:
    """K-04 — the field set the gate removes, as decided (ADR-0005, aneks 2026-09-23 SC-5-01, point
    3): the amount and the rates, in a set of their own; `PERSONNEL_COST_FIELDS` of the project
    payload untouched and still empty (its own canary,
    `test_project_personnel_cost_visibility.py::test_k_06_…`, stays as it was)."""
    assert SCENARIO_COST_FIELDS == frozenset({"amount", "assumptions_used"})
    assert PERSONNEL_COST_FIELDS == frozenset()


# --- K-05: out of scope is indistinguishable from absent -----------------------------------------


def test_k_05_a_scenario_outside_the_callers_scope_is_the_same_404_as_no_scenario(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — four addresses, one answer, for a caller holding **every** permission and the cost
    flag on each of their own projects:

    1. a scenario of a project the caller has no access to — costed, so a leaking read would have a
       figure to show;
    2. a scenario id that does not exist;
    3. a scenario of a project in scope, addressed through **another** project in scope;
    4. a project id that does not exist.

    Status, body and length identical. The contrast is the caller's own scenario: `200` with the
    figure, so the `404`s are about scope and not about a broken path. Mutation: a
    `select(Scenario)` of the cost path's own instead of `scenario_view_in_scope`.
    """
    mine_project, mine, _ = _costed_scenario(db_session, cost_visible=True)
    theirs_project = make_project(
        db_session,
        name="Theirs",
        accessible_to=(OUT_OF_SCOPE_USER,),
        cost_visible_to=(OUT_OF_SCOPE_USER,),
    )
    theirs = make_scenario(db_session, theirs_project, name="Theirs")
    theirs_dimensions = make_dimension_tuple(db_session, suffix=" theirs")
    theirs_position = make_staffing_position(
        db_session, theirs, theirs_dimensions, start_date=MAR
    )
    make_allocation(db_session, theirs_position, period_month=MAR)
    make_rate(db_session, theirs_dimensions, effective_from=date(2026, 1, 1), currency="PLN")
    other_mine_project = make_project(
        db_session,
        name="Cassiopeia",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    other_mine = make_scenario(db_session, other_mine_project, name="Mine too")

    addresses = [
        personnel_cost_path(theirs_project.id, theirs.id),
        personnel_cost_path(mine_project.id, uuid.uuid4()),
        personnel_cost_path(mine_project.id, other_mine.id),
        personnel_cost_path(uuid.uuid4(), mine.id),
    ]
    responses = [_read(client, path, EVERYTHING) for path in addresses]

    for response in responses:
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": PERSONNEL_COST_NOT_FOUND_DETAIL}
    assert len({response.content for response in responses}) == 1

    _assert_visible(_read(client, personnel_cost_path(mine_project.id, mine.id), EVERYTHING))


# --- K-06: the revenue still carries no cost, with the cost gate open -----------------------------


def test_k_06_the_revenue_carries_no_cost_field_even_for_a_caller_the_cost_gate_admits(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — one caller, both halves of the conjunction true: every permission **and** the flag.

    The same caller reads the cost (`14400.00` at `120.0000` — the gate is really open for them,
    the contrast) and the revenue: the revenue's field set is, by equality, the one SC-4-01 decided
    (`test_commercial_terms_access._assert_field_sets`), and neither cost figure appears in its
    body. Mutation: the revenue shaping growing a cost/profit/margin field "for callers who may see
    it" — this is the one caller who may, and it would appear here.
    """
    project, scenario, _ = _costed_scenario(db_session, cost_visible=True)
    make_commercial_terms(db_session, scenario)

    cost = _read(client, personnel_cost_path(project.id, scenario.id), EVERYTHING)
    revenue = _read(client, commercial_terms_path(project.id, scenario.id), EVERYTHING)

    _assert_visible(cost)
    assert revenue.status_code == 200, revenue.text
    assert_revenue_field_sets(revenue.json())
    assert revenue.json()["revenue"]["state"] == "calculated"
    assert COST_RATE_TEXT not in revenue.text
    assert COST_AMOUNT_TEXT not in revenue.text
