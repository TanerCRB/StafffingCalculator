"""F-01's mandatory project fields are mandatory at the boundary — name, client, owner.

Why this file exists: the suite proved that a bad *currency*, an inverted *period* and an
*unknown* field are refused, but nothing refused a project with no name. Lowering
`NonEmptyName`'s `min_length` from 1 to 0 left every test green, and the database offers no
second line of defence here: `projects.name` is `NOT NULL VARCHAR(200)` with no non-empty check
constraint (see the create migration), so `''` is a perfectly valid row. The schema is the only
mechanism, which makes it the thing to test.

Whitespace is part of the same claim: `strip_whitespace=True` runs before the length check, so
`"   "` must be refused for the same reason `""` is — otherwise a project named three spaces is
indistinguishable from a nameless one on every screen that renders it.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import IN_SCOPE_USER, as_caller, count_projects, project_payload

BLANK_VALUES = ["", "   ", "\t", "\n"]


@pytest.mark.parametrize("field", ["name", "client", "owner"])
@pytest.mark.parametrize("blank", BLANK_VALUES)
def test_project_create_rejects_a_blank_mandatory_field(
    client: TestClient, db_session: Session, field: str, blank: str
) -> None:
    """One field blanked at a time, the rest of the body valid — so a 422 can only come from
    the field under test, never from a payload that was broken in several ways at once."""
    response = client.post(
        "/projects", json=project_payload(**{field: blank}), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 422, f"{field}={blank!r} was accepted"
    assert count_projects(db_session) == 0


@pytest.mark.parametrize("field", ["name", "client", "owner"])
def test_project_create_accepts_the_same_field_once_it_has_content(
    client: TestClient, field: str
) -> None:
    """The contrast, one changed character apart: the same request with a single non-blank
    character in the same field is created. Without this, "reject everything" would satisfy the
    test above."""
    response = client.post(
        "/projects", json=project_payload(**{field: "A"}), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 201, response.text
    assert response.json()[field] == "A"


def test_project_create_stores_names_without_their_surrounding_whitespace(
    client: TestClient,
) -> None:
    """`strip_whitespace=True` is a normalization the read path must agree with: the value that
    comes back is the trimmed one, not the one that was sent. Asserted on the read, so this is
    about the stored row and not about what the POST response happened to echo."""
    created = client.post(
        "/projects",
        json=project_payload(name="  Aurora migration  "),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert created.status_code == 201, created.text

    read = client.get(f"/projects/{created.json()['id']}", headers=as_caller(IN_SCOPE_USER))

    assert read.json()["name"] == "Aurora migration"


def test_project_create_accepts_a_body_with_no_description_at_all(client: TestClient) -> None:
    """`description` is the one optional field of F-01, and every other test sends it — so the
    default has never been exercised. Omitting it must create the project with an empty
    description, not a 422 and not a `null` the read path then has to cope with."""
    payload = project_payload()
    del payload["description"]

    created = client.post("/projects", json=payload, headers=as_caller(IN_SCOPE_USER))

    assert created.status_code == 201, created.text
    read = client.get(f"/projects/{created.json()['id']}", headers=as_caller(IN_SCOPE_USER))
    assert read.json()["description"] == ""


def test_project_create_rejects_a_name_longer_than_the_column_can_hold(
    client: TestClient, db_session: Session
) -> None:
    """201 characters is a 422, not a database error: `projects.name` is `VARCHAR(200)`, and a
    boundary that lets the value through turns a client mistake into a 500."""
    response = client.post(
        "/projects", json=project_payload(name="A" * 201), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 422
    assert count_projects(db_session) == 0
