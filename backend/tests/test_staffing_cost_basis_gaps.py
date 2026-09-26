"""SC-5-03 (Issue #78) — QA closes the three gaps the developer named explicitly at hand-off, not
covered by any of K-01..K-06's own test files:

(a) the `what-if` endpoint (`GET .../what-if`, SC-6-04) had never been exercised against a
    scenario carrying a `fixed_amount` position — `app.data.scenario_what_if` passes
    `cost_view.fixed_amount` through unraised (by design, per its own docstring), and nothing
    proved that on a real response body;
(b) the new `PATCH .../staffing-positions/{position_id}` endpoint had never been proven to refuse a
    caller without `STAFFING_WRITE` — every sibling write on this router has its own such test
    (`test_staffing_positions.py::test_k_03_the_allocation_edit_is_denied_to_a_caller_without_
    staffing_write`), and a route wired without the dependency (or with it silently dropped in a
    copy from that sibling) would pass every other test in this task;
(c) the two Pydantic validators added by this task
    (`StaffingPositionCreateRequest._fixed_amount_matches_its_basis`,
    `StaffingPositionCostBasisEditRequest._at_least_one_field_and_consistent_with_its_basis`) had
    no `422` test at all — K-06's own proof is at the database (`test_staffing_positions_cost_
    basis_hidden.py`), so these validators were, until this file, pure vocabulary nobody had run.

None of these are K-01..K-06 by name (K-06 is proved at the database regardless of what the
schema does), which is why QA judged them closeable-but-not-blocking and closed the cheap,
high-value ones here rather than leaving all three as named limitations. What is *not* closed here
is recorded under "residual" at the bottom of each section, deliberately, rather than implied by
omission.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models.staffing import StaffingPosition
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    caller_holding,
    count_positions,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    staffing_path,
    staffing_position_payload,
)
from tests.test_scenario_results import EVERYTHING
from tests.test_scenario_what_if import what_if_path
from tests.test_staffing_positions import _dimensions_and_draft_scenario

MAR = date(2026, 3, 1)


# --- gap (a): the what-if endpoint and a fixed_amount position ------------------------------------


def test_sc_5_03_the_what_if_endpoint_carries_a_fixed_amount_position_through_unraised(
    client: TestClient, db_session: Session
) -> None:
    """Gap (a), closed — a scenario with one `worked_time` and one `fixed_amount` position, read
    through `GET .../what-if` at `0%` and at a real raise.

    The fixed-amount component must be **identical** at both percentages: a `fixed_amount` position
    reads no catalogue rate for any multiplier to touch (`app.domain.fixed_amount_cost` takes no
    rate at all — K-01). The worked-time component is the contrast: it must visibly change, so a
    shortcut that raised *everything* the scenario carries (e.g. an aggregate `included_cost *
    multiplier`, or a dispatcher that fed `fixed_amount`'s lines through `_raised_months` by
    mistake) disagrees with this test on the one figure it should leave alone.
    """
    project = make_project(
        db_session,
        name="WhatIfFixedAmount",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = make_scenario(db_session, project, name="Baseline", currency="PLN")
    dimensions = make_dimension_tuple(db_session, suffix=" WhatIfFixedAmount")
    worked = make_staffing_position(db_session, scenario, dimensions, start_date=MAR)
    make_allocation(db_session, worked, period_month=MAR)
    make_rate(
        db_session,
        dimensions,
        effective_from=date(2026, 1, 1),
        default_cost_rate=Decimal("120.0000"),
        default_selling_rate=Decimal("200.0000"),
        currency="PLN",
    )
    make_staffing_position(
        db_session,
        scenario,
        dimensions,
        start_date=MAR,
        cost_basis="fixed_amount",
        fixed_amount=Decimal("500.0000"),
        fixed_amount_currency="PLN",
    )
    make_commercial_terms(db_session, scenario)

    with caller_holding(*EVERYTHING):
        zero = client.get(what_if_path(project.id, scenario.id, "0"))
        raised = client.get(what_if_path(project.id, scenario.id, "100"))

    assert zero.status_code == 200, zero.text
    assert raised.status_code == 200, raised.text
    zero_cost = zero.json()["personnel_cost"]
    raised_cost = raised.json()["personnel_cost"]

    # The fixed-amount component: identical at both percentages, never `None`/withheld.
    assert zero_cost["fixed_amount_state"] == raised_cost["fixed_amount_state"] == "calculated"
    assert zero_cost["fixed_amount_amount"] == raised_cost["fixed_amount_amount"] == "500.00"
    assert (
        zero_cost["fixed_amount_assumptions_used"]
        == raised_cost["fixed_amount_assumptions_used"]
    )

    # The contrast: the worked-time component, which a 100% raise must double.
    assert zero_cost["state"] == raised_cost["state"] == "calculated"
    assert Decimal(raised_cost["amount"]) == Decimal(zero_cost["amount"]) * 2


# Residual (gap a): only the salary-raise what-if is exercised here. A second `fixed_amount`
# position in a *mismatched* currency, read through what-if, is not — the claim "the named state
# is carried through unchanged too" is not proven, only "the calculated amount is". See the QA
# report's "what this suite does not prove".


# --- gap (b): STAFFING_WRITE on the new PATCH endpoint --------------------------------------------


def test_the_cost_basis_edit_is_denied_to_a_caller_without_staffing_write(
    client: TestClient, db_session: Session
) -> None:
    """Gap (b), closed — the same shape as `test_staffing_positions.py`'s own
    `test_k_03_the_allocation_edit_is_denied_to_a_caller_without_staffing_write`, one route over.

    A caller with `STAFFING_READ` can see the position's `updated_at`; carrying it into this
    `PATCH` must still not let them write. The row is read back afterwards, unchanged — a `403`
    that had already applied the change would satisfy a status-only assertion.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MAR)
    db_session.commit()

    with caller_holding(Permission.STAFFING_READ):
        token = client.get(
            staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        ).json()["positions"][0]["updated_at"]
        refused = client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position.id}",
            json={
                "updated_at": token,
                "cost_basis": "fixed_amount",
                "fixed_amount": "50.0000",
                "fixed_amount_currency": "PLN",
            },
            headers=as_caller(IN_SCOPE_USER),
        )

    assert refused.status_code == 403, refused.text
    db_session.expire_all()
    fresh = db_session.get(StaffingPosition, position.id)
    assert fresh is not None
    assert fresh.cost_basis == "worked_time"
    assert fresh.fixed_amount is None


# --- gap (c): the two Pydantic validators, at least their main branches ---------------------------


def test_create_request_refuses_fixed_amount_basis_with_neither_field_named(
    client: TestClient, db_session: Session
) -> None:
    """Gap (c), `StaffingPositionCreateRequest._fixed_amount_matches_its_basis` — the branch that
    matters most: `cost_basis="fixed_amount"` with nothing else, a `422` naming both missing
    fields, and nothing written.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)

    response = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions, cost_basis="fixed_amount"),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "fixed_amount" in response.text
    assert "fixed_amount_currency" in response.text
    assert count_positions(db_session) == 0


def test_create_request_refuses_a_stray_fixed_amount_on_a_worked_time_basis(
    client: TestClient, db_session: Session
) -> None:
    """Gap (c), the other branch of the same validator — `cost_basis` left at the default
    (`worked_time`) but `fixed_amount`/`fixed_amount_currency` given anyway: refused before the
    request reaches the database, exactly as the missing-field branch is.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)

    response = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(
            dimensions, fixed_amount="10.0000", fixed_amount_currency="PLN"
        ),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert count_positions(db_session) == 0


def test_cost_basis_edit_refuses_switching_to_fixed_amount_with_only_the_amount_named(
    client: TestClient, db_session: Session
) -> None:
    """Gap (c), `StaffingPositionCostBasisEditRequest._at_least_one_field_and_consistent_with_its_
    basis` — switching to `fixed_amount` with `fixed_amount` given but `fixed_amount_currency`
    omitted is a `422` naming the missing field, and the row is unchanged.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MAR)
    db_session.commit()

    token = client.get(
        staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]["updated_at"]

    response = client.patch(
        f"{staffing_path(project.id, scenario.id)}/{position.id}",
        json={"updated_at": token, "cost_basis": "fixed_amount", "fixed_amount": "10.0000"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "fixed_amount_currency" in response.text
    db_session.expire_all()
    fresh = db_session.get(StaffingPosition, position.id)
    assert fresh.cost_basis == "worked_time"


def test_cost_basis_edit_refuses_a_body_naming_no_field_to_change(
    client: TestClient, db_session: Session
) -> None:
    """Gap (c) — the other validator on the same schema (`_at_least_one_field_and_consistent_with_
    its_basis`'s first line): a body with only `updated_at` is a `422`, not a silent no-op `200`
    that would rotate the concurrency token for nothing.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MAR)
    db_session.commit()

    token = client.get(
        staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]["updated_at"]

    response = client.patch(
        f"{staffing_path(project.id, scenario.id)}/{position.id}",
        json={"updated_at": token},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text


# Residual (gap c): the schema's third branch — naming `fixed_amount` or `fixed_amount_currency`
# alone while `cost_basis` is *not* also being changed on an already-`fixed_amount` position (the
# `elif amount_given != currency_given` line) — is not exercised by any test here or elsewhere.
# See the QA report's "what this suite does not prove".
