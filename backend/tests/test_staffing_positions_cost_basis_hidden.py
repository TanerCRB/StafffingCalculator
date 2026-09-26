"""SC-5-03, K-05 (Q4) — `cost_basis`/`fixed_amount`/`fixed_amount_currency` are never part of
`GET …/staffing-positions`'s response schema (ADR-0005, aneks 2026-09-25 SC-5-03).

**Structural proof: the response's field set, by equality** (the wording the aneks requires —
"zbiór pól odpowiedzi… identyczny przed/po tym zadaniu", the same construction as
`test_commercial_terms_access.py::_assert_field_sets` and `test_personnel_cost.py::
_assert_field_sets`), not a `"cost_basis" not in body` assertion that a field renamed to
`personnel_cost_basis` would quietly slip past.

The position under test is a `fixed_amount` one with a stated amount — the case with the most to
leak — so a field added carelessly to `StaffingPositionRead`/`StaffingAllocation` to "round-trip
what was just written" is exactly what this equality catches, on the position that makes such a
field tempting to add.
"""

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    caller_holding,
    make_dimension_tuple,
    make_project,
    make_scenario,
    staffing_path,
    staffing_position_payload,
)

MAR = date(2026, 3, 1)

POSITION_FIELDS = {
    "id",
    "role_id",
    "seniority_id",
    "location_id",
    "engagement_type_id",
    "headcount",
    "start_date",
    "end_date",
    "updated_at",
    "allocations",
    "absences",
}
"""Every field `StaffingPositionRead` carries — asserted by equality (K-04 and K-05 of SC-3-01/
SC-3-02 already prove this set closed against a rate; this file adds "and against `cost_basis`/
`fixed_amount`/`fixed_amount_currency`" without opening a second, competing definition of it)."""

ALLOCATION_FIELDS = {
    "id",
    "period_month",
    "availability_hours",
    "planned_allocation_hours",
    "billable_hours",
    "derived_capacity_hours",
    "derived_capacity_state",
    "derived_capacity_source",
    "absence_budget_hours",
    "absence_budget_state",
    "absence_budget_source",
}


def test_k_05_the_staffing_positions_response_field_set_excludes_the_cost_basis_columns(
    client: TestClient, db_session: Session
) -> None:
    """K-05 (Q4) — a `fixed_amount` position, read back through the real endpoint: the position's
    field set and its one allocation's field set are exactly the sets above, on both the `POST`
    response and the `GET` list — no `cost_basis`, no `fixed_amount`, no `fixed_amount_currency`,
    and (the contrast direction) nothing *missing* either, which a schema quietly narrowed to hide
    the new columns would produce just as wrongly.
    """
    # `cost_visible_to` (bramka 1 SC-5-03, fix 3 extended to POST): creating a `fixed_amount`
    # position now needs the SC-1-08 conjunction.
    project = make_project(
        db_session,
        name="Aurora migration",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session)

    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        created = client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(
                dimensions,
                cost_basis="fixed_amount",
                fixed_amount="777.7700",
                fixed_amount_currency="PLN",
            ),
        )
    assert created.status_code == 201, created.text
    created_body = created.json()
    assert set(created_body) == POSITION_FIELDS
    assert set(created_body["allocations"][0]) == ALLOCATION_FIELDS
    assert "cost_basis" not in created.text
    assert "fixed_amount" not in created.text
    assert "777.7700" not in created.text

    listed = client.get(
        staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert listed.status_code == 200, listed.text
    listed_body = listed.json()["positions"][0]
    assert set(listed_body) == POSITION_FIELDS
    assert set(listed_body["allocations"][0]) == ALLOCATION_FIELDS
    assert "cost_basis" not in listed.text
    assert "fixed_amount" not in listed.text
    assert "777.7700" not in listed.text
