"""The personnel-cost gate on the *detail* read (GET /projects/{id}, POST /projects).

Why this file exists: `PERSONNEL_COST_FIELDS` is empty today (no personnel-cost column exists
yet), so `_without_personnel_costs` is currently a no-op and deleting the call from
`shape_project_detail` changes no response — a mutation that removes the gate from the new
detail path survives the whole suite. That is a gap in the *proof*, not a live leak: there is
nothing to leak until F-13's own task adds the field, and on that day the gate must already be
wired into the detail path or the first personnel-cost column ships visible to everyone.

These tests close the gap by supplying the missing ingredient from the test side only: the field
set is monkeypatched to a field that does exist, and both directions are asserted against the
same request — with the permission the field survives, without it the field is blanked. Nothing
in `app/` is touched.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import response_shaping
from app.api.deps import get_caller_identity
from app.core.identity import CallerIdentity, Permission
from app.main import app
from tests.conftest import IN_SCOPE_USER, as_caller, project_payload

STAND_IN_COST_FIELD = "description"
"""A field that exists on `ProjectDetail` today, standing in for the personnel-cost columns that
do not exist yet. The gate is field-set driven, so which field it is carries no meaning — what
is being proven is that the detail path consults the set at all."""


@pytest.fixture
def personnel_costs_are_a_gated_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the otherwise-empty gate have something to remove."""
    monkeypatch.setattr(
        response_shaping, "PERSONNEL_COST_FIELDS", frozenset({STAND_IN_COST_FIELD})
    )


@pytest.fixture
def caller_with_personnel_cost_permission() -> Iterator[None]:
    """A caller holding `PERSONNEL_COSTS_READ` — the placeholder identity never grants it, and
    this is the only difference from the default caller used in the test above."""
    app.dependency_overrides[get_caller_identity] = lambda: CallerIdentity(
        user_id=IN_SCOPE_USER,
        permissions=frozenset(
            {
                Permission.PROJECT_READ,
                Permission.PROJECT_CREATE,
                Permission.PERSONNEL_COSTS_READ,
            }
        ),
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_caller_identity, None)


def _create_and_read(client: TestClient) -> tuple[dict, dict]:
    created = client.post("/projects", json=project_payload(), headers=as_caller(IN_SCOPE_USER))
    assert created.status_code == 201, created.text
    read = client.get(
        f"/projects/{created.json()['id']}", headers=as_caller(IN_SCOPE_USER)
    )
    assert read.status_code == 200, read.text
    return created.json(), read.json()


@pytest.mark.usefixtures("personnel_costs_are_a_gated_field")
def test_project_detail_blanks_cost_fields_for_a_caller_without_the_cost_permission(
    client: TestClient,
) -> None:
    """Seeing a project is not seeing its costs (F-13, AC-06) — on the detail read too.

    Both representations of the same project are checked: the one POST answers with and the one
    GET answers with. A gate applied on only one of them is a leak through the other.
    """
    created, read = _create_and_read(client)

    assert created[STAND_IN_COST_FIELD] is None
    assert read[STAND_IN_COST_FIELD] is None
    # The rest of the project is untouched — the gate removes cost fields, not the response.
    assert read["name"] == "Aurora migration"
    assert read["owner"] == "Anna Kowalska"


@pytest.mark.usefixtures("personnel_costs_are_a_gated_field")
def test_project_list_blanks_cost_fields_for_a_caller_without_the_cost_permission(
    client: TestClient,
) -> None:
    """The same gate on the list path, which has the same untested-because-inert problem: today
    removing `_without_personnel_costs` from the list row changes no response either."""
    created = client.post("/projects", json=project_payload(), headers=as_caller(IN_SCOPE_USER))
    assert created.status_code == 201, created.text

    listed = client.get("/projects", headers=as_caller(IN_SCOPE_USER))

    assert listed.status_code == 200, listed.text
    assert [row[STAND_IN_COST_FIELD] for row in listed.json()["projects"]] == [None]


@pytest.mark.usefixtures(
    "personnel_costs_are_a_gated_field", "caller_with_personnel_cost_permission"
)
def test_project_detail_serves_cost_fields_to_a_caller_holding_the_cost_permission(
    client: TestClient,
) -> None:
    """The contrast: same request, same project, one extra permission — the field comes back.

    Without this half, a `shape_project_detail` that blanked the field unconditionally (or
    returned nothing at all) would satisfy the test above forever.
    """
    created, read = _create_and_read(client)

    expected = project_payload()[STAND_IN_COST_FIELD]
    assert created[STAND_IN_COST_FIELD] == expected
    assert read[STAND_IN_COST_FIELD] == expected
