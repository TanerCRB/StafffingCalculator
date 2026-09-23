"""SC-1-01 — persist a Project: create it, read it back, and stay silent about the ones the
caller may not see. One test per acceptance criterion.

Criterion 1: a created Project is retrievable with name, client, owner, delivery period,
reporting currency and description.
Criterion 2: a caller without access gets a response indistinguishable from "does not exist" —
never a 403, which would confirm the Project exists.

The database is a real PostgreSQL (see `conftest`), and the create path really commits, so
"persisted" here means a row, not an object in a session.
"""

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    count_projects,
    make_project,
    project_payload,
)


def test_sc_1_01_01_created_project_is_retrievable_with_all_its_fields(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 1 — POST, then GET by id, and every F-01 field comes back unchanged.

    `expunge_all()` between the two requests empties the ORM identity map, so the read is
    answered from rows in PostgreSQL rather than from objects the write left behind in the
    session. The assertion is on the whole body, not on individual keys: an extra field that
    appeared without being asked for fails this test too.
    """
    payload = project_payload()

    created = client.post("/projects", json=payload, headers=as_caller(IN_SCOPE_USER))

    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    uuid.UUID(project_id)  # a real identifier, not an echo of something the client sent

    db_session.expunge_all()
    read = client.get(f"/projects/{project_id}", headers=as_caller(IN_SCOPE_USER))

    assert read.status_code == 200, read.text
    concurrency_token = read.json()["updated_at"]
    assert datetime.fromisoformat(concurrency_token).tzinfo is not None
    assert read.json() == {
        **payload,
        "id": project_id,
        "status": "Active",
        "scenarios": [],
        # Added to the detail contract by SC-1-02 (ADR-0007, accepted 2026-09-18): the
        # concurrency token the edit request has to send back. Its value comes from the
        # database's clock, not from anything the request carried, so it is named as a key here
        # and taken from the response — with the type assertion above standing in for the value.
        # Every other key stays pinned, so a field that appears without being asked for still
        # fails this test.
        "updated_at": concurrency_token,
        # Added to the detail contract by SC-1-10 (R-01): the project level's own assumption
        # overrides, as stored. A freshly created project sets none, so both are pinned to `None`
        # ("no override on this level, inherits") — a value here would be an override nobody set.
        "target_margin_percent": None,
        "overload_threshold_percent": None,
    }
    # The POST answered with the same representation it persisted — no "write shape" that
    # quietly differs from the "read shape".
    assert created.json() == read.json()


def test_sc_1_01_01_created_project_is_committed_and_readable_from_another_connection(
    committing_client: TestClient, engine: Engine
) -> None:
    """Criterion 1, the persistence half — the row outlives the request that created it.

    The test above proves the round trip; it cannot prove a commit, because every read in it
    shares one transaction with the write. Here the request runs in its own transaction and the
    check is made on a *different* connection, which by definition sees committed data only: a
    `create_project` that flushed without committing leaves nothing behind for this query.
    """
    created = committing_client.post(
        "/projects", json=project_payload(), headers=as_caller(IN_SCOPE_USER)
    )
    assert created.status_code == 201, created.text
    project_id = uuid.UUID(created.json()["id"])

    with engine.connect() as connection:
        project = connection.execute(
            sa.text(
                "SELECT name, client, owner, delivery_period_start, delivery_period_end,"
                " reporting_currency, description, status FROM projects WHERE id = :id"
            ),
            {"id": project_id},
        ).one_or_none()
        access_holders = connection.execute(
            sa.text("SELECT user_id FROM project_access WHERE project_id = :id"),
            {"id": project_id},
        ).scalars().all()

    assert project is not None, "the project was not committed — it did not survive the request"
    assert project.name == "Aurora migration"
    assert project.client == "Northwind"
    assert project.owner == "Anna Kowalska"
    assert project.delivery_period_start == date(2026, 3, 1)
    assert project.delivery_period_end == date(2026, 11, 30)
    assert project.reporting_currency == "EUR"
    assert project.description == "Migration of the billing platform to the cloud."
    assert project.status == "active"
    # The access grant is committed in the same unit of work, for the creator alone.
    assert list(access_holders) == [IN_SCOPE_USER]


def test_sc_1_01_02_unknown_project_id_is_not_found(client: TestClient) -> None:
    """Criterion 2, half one — an id nobody ever created is a 404."""
    response = client.get(f"/projects/{uuid.uuid4()}", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found."}


def test_sc_1_01_02_out_of_scope_project_is_indistinguishable_from_one_that_does_not_exist(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 2, and the contrast that keeps it from passing trivially.

    The *same* project id is requested three times: by a caller without `project_access`, by the
    same caller for an id that was never created, and by the caller who does have access. The
    first two responses must be identical down to the body text; the third must be a 200 with the
    project — otherwise "always 404" would satisfy the first two assertions on its own.
    """
    project = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))

    denied = client.get(f"/projects/{project.id}", headers=as_caller(OUT_OF_SCOPE_USER))
    never_existed = client.get(f"/projects/{uuid.uuid4()}", headers=as_caller(OUT_OF_SCOPE_USER))
    granted = client.get(f"/projects/{project.id}", headers=as_caller(IN_SCOPE_USER))

    # Contrast: the row is there, and a caller inside its scope reads it in full.
    assert granted.status_code == 200, granted.text
    assert granted.json()["name"] == "Borealis rollout"
    assert granted.json()["owner"] == "Anna Kowalska"

    # A 403 would answer "it exists, but not for you". This must not be distinguishable from
    # "there is no such project".
    assert denied.status_code == 404
    assert denied.status_code == never_existed.status_code
    assert denied.text == never_existed.text
    assert denied.headers.get("content-type") == never_existed.headers.get("content-type")
    assert denied.headers.get("content-length") == never_existed.headers.get("content-length")
    # Nothing about the project leaks through the refusal itself.
    assert "Borealis rollout" not in denied.text
    assert str(project.id) not in denied.text


def test_sc_1_01_creating_a_project_grants_access_to_its_creator_and_to_nobody_else(
    client: TestClient,
) -> None:
    """The access grant follows the authenticated creator, not anything in the request body.

    Without the `project_access` row written by `create_project`, criterion 1 could not hold at
    all — the creator's own project would answer 404. With it granted too widely, criterion 2
    would fail for every newly created project. Both directions are asserted here.
    """
    created = client.post("/projects", json=project_payload(), headers=as_caller(IN_SCOPE_USER))
    project_id = created.json()["id"]

    creators_list = client.get("/projects", headers=as_caller(IN_SCOPE_USER))
    outsiders_list = client.get("/projects", headers=as_caller(OUT_OF_SCOPE_USER))
    outsiders_read = client.get(f"/projects/{project_id}", headers=as_caller(OUT_OF_SCOPE_USER))

    assert [project["id"] for project in creators_list.json()["projects"]] == [project_id]
    assert outsiders_list.json()["projects"] == []
    assert project_id not in outsiders_list.text
    assert outsiders_read.status_code == 404
    # The list contract is unchanged by SC-1-01: `owner` is served on the detail read only.
    assert "owner" not in creators_list.json()["projects"][0]


def test_project_create_rejects_a_delivery_period_that_ends_before_it_starts(
    client: TestClient, db_session: Session
) -> None:
    """An inverted delivery period is refused at the boundary and writes nothing.

    The database's `delivery_period_ordered` check constraint is the actual guarantee; this test
    is about the request never reaching it, so the client gets a 422 instead of a 500.
    """
    payload = project_payload(delivery_period={"start": "2026-11-30", "end": "2026-03-01"})

    response = client.post("/projects", json=payload, headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 422
    assert count_projects(db_session) == 0


def test_project_create_refuses_a_body_that_tries_to_name_the_access_holder(
    client: TestClient, db_session: Session
) -> None:
    """A client-supplied user id is rejected outright, not silently ignored.

    "Ignored unknown field" and "rejected unknown field" look identical in the happy path and
    differ entirely the day a field of that name starts being read.
    """
    payload = project_payload(user_id=OUT_OF_SCOPE_USER)

    response = client.post("/projects", json=payload, headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 422
    assert count_projects(db_session) == 0


def test_project_create_refuses_an_unknown_key_smuggled_into_the_nested_delivery_period(
    client: TestClient, db_session: Session
) -> None:
    """`extra="forbid"` has to be declared on the nested model as well.

    Pydantic applies it per model, not down the tree: with it only on `ProjectCreateRequest`, the
    outer body is strict while `delivery_period` quietly accepts and drops anything extra — so
    the refusal proven by the test above would have a hole exactly one level down.
    """
    payload = project_payload(
        delivery_period={"start": "2026-03-01", "end": "2026-11-30", "user_id": OUT_OF_SCOPE_USER}
    )

    response = client.post("/projects", json=payload, headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 422
    assert count_projects(db_session) == 0


def test_project_create_rejects_a_reporting_currency_that_is_not_an_iso_4217_code(
    client: TestClient, db_session: Session
) -> None:
    """Three uppercase letters, per ADR-0006's open list — `eur` and `EURO` are both refused,
    rather than normalized into a second spelling of the same currency."""
    for bad_currency in ("eur", "EURO", "E", ""):
        response = client.post(
            "/projects",
            json=project_payload(reporting_currency=bad_currency),
            headers=as_caller(IN_SCOPE_USER),
        )
        assert response.status_code == 422, bad_currency

    assert count_projects(db_session) == 0
