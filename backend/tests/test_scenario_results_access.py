"""SC-7-01, K-03/K-06 — who reaches a scenario's whole-life result, and who sees which of its
fields (F-10, Issue #12; ADR-0005, addendum 2026-09-24).

- **K-03** a scenario outside the caller's scope is a `404` indistinguishable from one that does not
  exist — proven with a caller holding every permission, on a scenario that *would* answer with real
  figures if it were in scope.
- **K-06** the personnel-cost conjunction (`PERSONNEL_COSTS_READ` ∧ `project_access.
  can_view_personnel_costs`) withholds `profit`/`margin`/`markup`/`included_cost` as `null` — never
  the whole resource — while `revenue` and `additional_cost` stay visible; a caller without
  `RESULTS_READ` at all is refused the whole endpoint (`403`), a different level entirely. This
  doubles as the mandatory refusal test every new permission needs (Invariant Guardian rule 8).

The positive branch of the personnel-cost gate is reachable only through `caller_holding`: the
placeholder does not grant `PERSONNEL_COSTS_READ` (ADR-0005), so every caller of the running system
gets `profit`/`margin`/`markup`/`included_cost` withheld — the same shape SC-5-01 already proved.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.response_shaping import shape_scenario_results
from app.api.scenario_results import SCENARIO_RESULTS_NOT_FOUND_DETAIL
from app.core.identity import CallerIdentity, Permission
from app.data.scenario_results import scenario_results_for_caller
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    caller_holding,
    make_project,
    make_scenario,
)
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)

WITHOUT_PERSONNEL_COSTS_READ = EVERYTHING - {Permission.PERSONNEL_COSTS_READ}
WITHOUT_RESULTS_READ = EVERYTHING - {Permission.RESULTS_READ}


# --- K-03: out of scope is indistinguishable from absent -----------------------------------------


def test_k_03_a_scenario_outside_the_callers_scope_is_the_same_404_as_no_scenario(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — three addresses, one answer, for a caller holding **every** permission:

    1. a scenario of a project the caller has no access to — a real, priced and costed scenario, so
       a leaking read would have figures to show;
    2. a scenario id that does not exist, under a project the caller *does* access;
    3. a project id that does not exist.

    Status, body and length identical. The contrast is the caller's own scenario: `200` with real
    figures, so the `404`s are about scope and not about a broken path.
    """
    _ensure_statutory_bypass(db_session)
    mine_project, mine_scenario, _ = _full_scenario(db_session, name="Mine")
    theirs_project = make_project(db_session, name="Theirs", accessible_to=(OUT_OF_SCOPE_USER,))
    theirs_scenario = make_scenario(db_session, theirs_project, name="Theirs")

    addresses = [
        results_path(theirs_project.id, theirs_scenario.id),
        results_path(mine_project.id, uuid.uuid4()),
        results_path(uuid.uuid4(), mine_scenario.id),
    ]
    with caller_holding(*EVERYTHING):
        responses = [client.get(path) for path in addresses]

    for response in responses:
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": SCENARIO_RESULTS_NOT_FOUND_DETAIL}
    assert len({response.content for response in responses}) == 1

    with caller_holding(*EVERYTHING):
        mine_response = client.get(results_path(mine_project.id, mine_scenario.id))
    assert mine_response.status_code == 200, mine_response.text
    assert mine_response.json()["profit"] == "6000.00"


# --- K-06: the personnel-cost conjunction on four fields, and RESULTS_READ on the endpoint --------


def test_k_06_the_four_aggregate_fields_are_withheld_but_revenue_and_additional_cost_stay(
    client: TestClient, db_session: Session
) -> None:
    """K-06, the 2×2 on one caller with full project access:

    | `PERSONNEL_COSTS_READ` | flag  | `profit`/`margin`/`markup`/`included_cost` |
    |------------------------|-------|---------------------------------------------|
    | no                     | true  | `null`                                       |
    | yes                    | true  | real numbers                                 |

    In both rows `revenue` and `additional_cost` are visible, and `personnel_cost.amount` follows
    the existing SC-5-01 gate (also `null` in the first row) — proof this endpoint reuses that gate
    rather than inventing a second one that could disagree with it.

    Mutation killed: adding a `PERSONNEL_COSTS_READ` conjunct to the *endpoint's* permission
    dependency (row 1 would then be `403`, not `200` with `null` fields).
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="Gate", cost_visible=True)
    path = results_path(project.id, scenario.id)

    with caller_holding(*WITHOUT_PERSONNEL_COSTS_READ):
        withheld = client.get(path)
    assert withheld.status_code == 200, withheld.text
    body = withheld.json()
    assert body["profit"] is None
    assert body["margin"] is None
    assert body["markup"] is None
    assert body["included_cost"] is None
    assert body["personnel_cost"]["amount"] is None
    assert body["revenue"]["amount"] == "20000.00"
    assert body["additional_cost"]["amount"] == "2000.00"
    assert "6000.00" not in withheld.text
    assert "12000.00" not in withheld.text

    with caller_holding(*EVERYTHING):
        shown = client.get(path)
    assert shown.status_code == 200, shown.text
    shown_body = shown.json()
    assert shown_body["profit"] == "6000.00"
    assert shown_body["margin"] == "30.00"
    assert shown_body["markup"] == "42.86"
    assert shown_body["included_cost"] == "14000.00"
    assert shown_body["personnel_cost"]["amount"] == "12000.00"


def test_k_06_the_flag_is_the_one_on_the_scenarios_own_project_not_on_another_assignment(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — the caller has the flag on project B and **not** on project A (the scenario's). The
    result of A withholds the four fields; the contrast, B, shows them. Mutation killed: the flag
    resolved per caller (any/first `project_access` row) instead of per (caller, project)."""
    _ensure_statutory_bypass(db_session)
    project_b, scenario_b, _ = _full_scenario(db_session, name="Borealis", cost_visible=True)
    project_a, scenario_a, _ = _full_scenario(db_session, name="Aurora", cost_visible=False)

    with caller_holding(*EVERYTHING):
        withheld = client.get(results_path(project_a.id, scenario_a.id))
        shown = client.get(results_path(project_b.id, scenario_b.id))

    assert withheld.json()["profit"] is None
    assert shown.json()["profit"] == "6000.00"


def test_k_06_a_caller_without_results_read_is_refused_the_whole_endpoint(
    client: TestClient, db_session: Session
) -> None:
    """K-06's other level — `RESULTS_READ` absent, everything else (including
    `PERSONNEL_COSTS_READ`) present: `403`, the whole resource, no figure anywhere in the body. Also
    the mandatory refusal test for the new permission (Invariant Guardian rule 8).

    Mutation killed: the `RESULTS_READ` check on the endpoint removed altogether (this caller holds
    every other permission and would get through).
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="Refused", cost_visible=True)

    with caller_holding(*WITHOUT_RESULTS_READ):
        refused = client.get(results_path(project.id, scenario.id))

    assert refused.status_code == 403, refused.text
    assert "6000.00" not in refused.text
    assert "20000.00" not in refused.text


def test_k_06_the_gate_refuses_to_shape_one_callers_view_with_another_callers_identity(
    db_session: Session,
) -> None:
    """K-06's identity guard — a view built for user A, shaped with user B's permission set, is an
    error rather than a silently widened gate (`_without_scenario_profitability`'s own assertion,
    the sibling of `_without_scenario_personnel_costs`'s — same `ScenarioCostView`, same check,
    applied a second time to the four aggregate fields). The contrast is the same view shaped for
    A."""
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="Identity", cost_visible=True)
    anna = CallerIdentity(user_id=IN_SCOPE_USER, permissions=EVERYTHING)
    view = scenario_results_for_caller(db_session, anna, project.id, scenario.id)
    assert view is not None

    assert shape_scenario_results(view, anna).profit == Decimal("6000.00")
    with pytest.raises(AssertionError):
        shape_scenario_results(
            view, CallerIdentity(user_id=OUT_OF_SCOPE_USER, permissions=EVERYTHING)
        )


def test_k_06_the_running_placeholder_sees_revenue_and_additional_cost_but_not_the_four_fields(
    client: TestClient, db_session: Session
) -> None:
    """The production identity path: the header placeholder holds `RESULTS_READ` (it is in
    `PLACEHOLDER_PERMISSIONS`, like `STAFFING_READ`/`COMMERCIAL_READ`) but never
    `PERSONNEL_COSTS_READ` — so the endpoint is reachable and the four gated fields are always
    `null` for every caller the running system has, whatever the project's flag says.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="Placeholder", cost_visible=True)

    response = client.get(
        results_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profit"] is None
    assert body["revenue"]["amount"] == "20000.00"
    assert body["additional_cost"]["amount"] == "2000.00"


# --- K-06 x K-05: the gate and an unresolvable source at once -------------------------------------


def test_k_06_the_gate_and_an_unresolvable_source_together_answer_null_not_n_a(
    client: TestClient, db_session: Session
) -> None:
    """The interaction neither K-05 nor K-06 alone exercises: the personnel-cost gate **closed**
    (`cost_visible=False`) *and*, independently, the base personnel cost itself unresolvable
    (`mid_month_cost_change=True`, K-05's own `no_cost_rate` scenario) — both at once, on the same
    request.

    `app.api.response_shaping.shape_scenario_results` always computes `scenario_profitability`
    first (which would answer `"n/a"` here, exactly as K-05 proves alone) and *then* applies
    `_without_scenario_profitability`, whose `dict.fromkeys` overwrite runs unconditionally when the
    gate is closed — it does not ask what value it is replacing. So the gate is asserted here to
    **win over** the named `"n/a"`: the caller sees `null`, never `"n/a"`, on all four fields. This
    is the fact the developer named as believed-but-unverified ("the gate wins"); this test is what
    verifies it, live, through the real endpoint.

    `personnel_cost.state` is unaffected by either mechanism and must still name the real reason
    (`no_cost_rate`): `state` is not a member of `SCENARIO_COST_FIELDS`, so the profitability gate
    (which never touches `personnel_cost` at all) and the personnel-cost gate (which nulls `amount`
    and the rate assumptions but not `state`) agree in leaving it alone — a caller who may not see
    the cost still learns which named reason would apply once they could.

    Contrast: the same broken scenario with the gate **open** (`cost_visible=True`, K-05's own test)
    answers `"n/a"`, not `null` — so this test's `null` is provably the gate's doing and not a
    second, accidental way of spelling the same unresolvable state.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session, name="GateAndUnresolvable", cost_visible=False, mid_month_cost_change=True
    )

    with caller_holding(*EVERYTHING):
        response = client.get(results_path(project.id, scenario.id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profit"] is None
    assert body["margin"] is None
    assert body["markup"] is None
    assert body["included_cost"] is None
    assert body["personnel_cost"]["amount"] is None
    assert body["personnel_cost"]["state"] == "no_cost_rate"
    assert body["revenue"]["amount"] == "20000.00"
    assert body["additional_cost"]["amount"] == "2000.00"
    assert "n/a" not in (body["profit"], body["margin"], body["markup"], body["included_cost"])
