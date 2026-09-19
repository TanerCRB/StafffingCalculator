"""SC-1-04 — archive a Project. One test per acceptance criterion.

Criterion 1: the archive *action* moves `status` Active→Archived and the project stays on the
list, marked. SC-1-05 already proved the read half with a fixture-set status; what is new here is
that a write path does it.
Criterion 2: archiving changes no scenario row — it is a visibility flag, not a freeze
(ADR-0004, addendum 2026-09-18).
Criterion 3: archiving a project outside the caller's scope answers `404`, indistinguishable from
a project that never existed.
Criterion 4: a caller without `PROJECT_ARCHIVE` is refused and nothing changes (the refusal test
ADR-0005 makes mandatory for every new permission).

The database is a real PostgreSQL (see `conftest`) and `archive_project` really commits, so
"archived" here means the row, not an attribute on an object in a session.
"""

import uuid
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.deps import get_caller_identity
from app.core.identity import CallerIdentity, Permission
from app.main import app
from app.models import ProjectStatus, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    make_project,
    make_scenario,
    project_payload,
)

SCENARIO_COLUMNS = (
    "id, project_id, name, status, start_date, end_date, working_calendar,"
    " full_time_hours_per_week, currency, target_margin_percent, created_at, updated_at"
)
"""Every column of `scenarios`, listed explicitly rather than as `*`.

`SELECT *` would compare whatever the table happens to have today, and a column added tomorrow
would silently join the comparison — which sounds strictly better until the added column is one
the archive action legitimately does not touch and the list is what documents that claim. Named
columns mean a new column is a deliberate edit here, with `updated_at` included on purpose: if
archiving ever issued an UPDATE against a scenario row, `onupdate` would move it even when no
value differed."""


def scenario_rows(session: Session, project_id: uuid.UUID) -> list[tuple[object, ...]]:
    """Every scenario row of one project, read straight from the table, in a stable order."""
    return [
        tuple(row)
        for row in session.execute(
            sa.text(
                f"SELECT {SCENARIO_COLUMNS} FROM scenarios WHERE project_id = :id ORDER BY id"
            ),
            {"id": project_id},
        ).all()
    ]


def stored_status(session: Session, project_id: uuid.UUID) -> str | None:
    """The project's `status` as the table holds it — not as a response body reports it."""
    return session.execute(
        sa.text("SELECT status FROM projects WHERE id = :id"), {"id": project_id}
    ).scalar_one_or_none()


def test_sc_1_04_01_archiving_moves_the_project_to_archived_and_keeps_it_on_the_list(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 1 — the action, then the list.

    Two projects, both in the caller's scope, both Active to begin with. One is archived through
    the new endpoint; afterwards the list must still contain *both* — the archived one marked
    Archived, the untouched one still Active. That second project is what keeps the test from
    passing on an implementation that archives everything it can reach, and the "before" list
    assertion is what keeps it from passing on a fixture that was already archived.

    `expunge_all()` empties the ORM identity map between the action and the read, so the list is
    answered from rows in PostgreSQL rather than from the object the write left in the session.
    """
    target = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    untouched = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    target_id, untouched_id = target.id, untouched.id

    before = client.get("/projects", headers=as_caller(IN_SCOPE_USER))
    assert before.status_code == 200, before.text
    assert {project["id"]: project["status"] for project in before.json()["projects"]} == {
        str(target_id): "Active",
        str(untouched_id): "Active",
    }

    archived = client.post(f"/projects/{target_id}/archive", headers=as_caller(IN_SCOPE_USER))

    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "Archived"
    assert archived.json()["id"] == str(target_id)

    db_session.expunge_all()
    after = client.get("/projects", headers=as_caller(IN_SCOPE_USER))

    assert after.status_code == 200, after.text
    # Still on the default list, marked — archiving hides no row (ADR-0004, addendum, point 2).
    assert {project["id"]: project["status"] for project in after.json()["projects"]} == {
        str(target_id): "Archived",
        str(untouched_id): "Active",
    }
    assert stored_status(db_session, target_id) == ProjectStatus.ARCHIVED.value
    assert stored_status(db_session, untouched_id) == ProjectStatus.ACTIVE.value
    # The archived project is still readable by id, and its fields are untouched apart from status.
    detail = client.get(f"/projects/{target_id}", headers=as_caller(IN_SCOPE_USER))
    assert detail.status_code == 200, detail.text
    assert detail.json() == archived.json()


def test_sc_1_04_01_archived_status_is_committed_and_survives_the_request(
    committing_client: TestClient, engine: Engine
) -> None:
    """Criterion 1, the persistence half — the status change outlives the request.

    The test above cannot prove a commit: its action and its reads share one transaction, so a
    `status` merely flushed would look identical to one committed. Here the request runs in its
    own transaction and the check is made on a *different* connection, which sees committed data
    only.
    """
    created = committing_client.post(
        "/projects", json=project_payload(), headers=as_caller(IN_SCOPE_USER)
    )
    assert created.status_code == 201, created.text
    project_id = uuid.UUID(created.json()["id"])

    archived = committing_client.post(
        f"/projects/{project_id}/archive", headers=as_caller(IN_SCOPE_USER)
    )
    assert archived.status_code == 200, archived.text

    with engine.connect() as connection:
        status_in_database = connection.execute(
            sa.text("SELECT status FROM projects WHERE id = :id"), {"id": project_id}
        ).scalar_one_or_none()

    assert status_in_database == ProjectStatus.ARCHIVED.value, (
        "the archive action did not commit — the status did not survive the request"
    )


def test_sc_1_04_02_archiving_changes_no_scenario_row(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 2 — archiving is a visibility flag, not a freeze and not a cascade.

    Two scenarios, deliberately different: an approved one with every input filled and a draft
    with gaps. Every column of both rows is compared before and after the archive action,
    `updated_at` included — an UPDATE touching a scenario row would move it even if no value
    changed, and a cascade would change `status`.

    The contrast that stops this from being satisfied by an endpoint that does nothing at all:
    the project's own `status` is asserted to have changed in the same test.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    project_id = project.id
    make_scenario(
        db_session,
        project,
        name="Approved baseline",
        status=ScenarioStatus.APPROVED,
        start_date=project.delivery_period_start,
        end_date=project.delivery_period_end,
        working_calendar="PL-standard",
        full_time_hours_per_week=Decimal("40.00"),
        currency="EUR",
        target_margin_percent=Decimal("18.500"),
    )
    make_scenario(db_session, project, name="Draft with gaps", status=ScenarioStatus.DRAFT)

    scenarios_before = scenario_rows(db_session, project_id)
    assert len(scenarios_before) == 2, "the fixture must give this project two scenarios"
    detail_before = client.get(f"/projects/{project_id}", headers=as_caller(IN_SCOPE_USER))
    assert detail_before.status_code == 200, detail_before.text

    archived = client.post(f"/projects/{project_id}/archive", headers=as_caller(IN_SCOPE_USER))

    assert archived.status_code == 200, archived.text
    db_session.expunge_all()
    scenarios_after = scenario_rows(db_session, project_id)

    assert scenarios_after == scenarios_before, (
        "archiving must not touch a single scenario column, `updated_at` included"
    )
    # Contrast: something *did* change — the project's status, and only that.
    assert stored_status(db_session, project_id) == ProjectStatus.ARCHIVED.value
    detail_after = client.get(f"/projects/{project_id}", headers=as_caller(IN_SCOPE_USER))
    assert detail_after.json()["scenarios"] == detail_before.json()["scenarios"]
    assert detail_before.json()["status"] == "Active"
    assert detail_after.json()["status"] == "Archived"


def test_sc_1_04_03_archiving_a_project_outside_the_callers_scope_is_not_found(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 3 — `404`, and indistinguishable from "there is no such project".

    The same project id is archived three times over: by a caller with no `project_access` row for
    it, by that same caller for an id nobody ever created, and finally by the caller who does have
    access. The first two responses must match down to the body text; the third must succeed, or
    "always 404" would satisfy the first two on its own. A `403` here would confirm the existence
    of a project the caller may not see (ADR-0005, addendum, point 3).
    """
    project = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    project_id = project.id

    denied = client.post(f"/projects/{project_id}/archive", headers=as_caller(OUT_OF_SCOPE_USER))
    never_existed = client.post(
        f"/projects/{uuid.uuid4()}/archive", headers=as_caller(OUT_OF_SCOPE_USER)
    )

    assert denied.status_code == 404
    assert denied.status_code == never_existed.status_code
    assert denied.text == never_existed.text
    assert denied.headers.get("content-type") == never_existed.headers.get("content-type")
    assert denied.headers.get("content-length") == never_existed.headers.get("content-length")
    assert "Borealis rollout" not in denied.text
    assert str(project_id) not in denied.text
    # The refused action wrote nothing: the project is still Active.
    db_session.expunge_all()
    assert stored_status(db_session, project_id) == ProjectStatus.ACTIVE.value

    # Contrast: the row is there, and the caller inside its scope archives it.
    granted = client.post(f"/projects/{project_id}/archive", headers=as_caller(IN_SCOPE_USER))
    assert granted.status_code == 200, granted.text
    assert granted.json()["status"] == "Archived"


def test_sc_1_04_04_caller_without_project_archive_permission_is_refused_and_changes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 4 — the mandatory refusal test for `PROJECT_ARCHIVE` (ADR-0005).

    The refused caller is inside the project's scope and holds `PROJECT_READ`, so the only reason
    left for the refusal is the missing action permission — which is the point of `PROJECT_ARCHIVE`
    being its own permission rather than part of a general write right. `403`, not `404`: here the
    project *is* in the caller's scope, so there is nothing to keep secret about its existence,
    and the caller already sees it on the list.

    "Zero changes" is asserted against the table, not against the response body — a response
    saying no while the row moved anyway is exactly what this assertion exists to catch.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    project_id = project.id
    app.dependency_overrides[get_caller_identity] = lambda: CallerIdentity(
        user_id=IN_SCOPE_USER, permissions=frozenset({Permission.PROJECT_READ})
    )
    try:
        refused = client.post(
            f"/projects/{project_id}/archive", headers=as_caller(IN_SCOPE_USER)
        )
    finally:
        app.dependency_overrides.pop(get_caller_identity, None)

    assert refused.status_code == 403
    db_session.expunge_all()
    assert stored_status(db_session, project_id) == ProjectStatus.ACTIVE.value
    assert scenario_rows(db_session, project_id) == []
    # Contrast: the same caller, now holding the permission, is let through — so the 403 above is
    # about the permission and not about a broken route or an unreachable project.
    granted = client.post(f"/projects/{project_id}/archive", headers=as_caller(IN_SCOPE_USER))
    assert granted.status_code == 200, granted.text
    assert stored_status(db_session, project_id) == ProjectStatus.ARCHIVED.value


def test_project_archive_denies_a_caller_without_any_identity(
    client: TestClient, db_session: Session
) -> None:
    """No identity header, no write — and the refusal leaks nothing about the project.

    Kept next to the criterion tests because the archive endpoint is the subject; the identity
    placeholder itself is covered in `test_access_control.py`.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    project_id = project.id

    response = client.post(f"/projects/{project_id}/archive")

    assert response.status_code == 401
    assert "Aurora migration" not in response.text
    db_session.expunge_all()
    assert stored_status(db_session, project_id) == ProjectStatus.ACTIVE.value


def test_project_archive_is_one_way_and_idempotent(
    client: TestClient, db_session: Session
) -> None:
    """Archiving twice is a no-op, and there is no way to ask for the opposite.

    Un-archiving is out of scope (F-01 names only "archive"; ADR-0004, addendum, point 4). This
    test pins the two consequences of that: a repeat call reports Archived instead of failing, and
    the endpoint takes no body, so a client cannot pass `status: "active"` — a body sent anyway is
    ignored rather than honoured, and the state stays Archived.
    """
    project = make_project(
        db_session,
        name="Aurora migration",
        status=ProjectStatus.ARCHIVED,
        accessible_to=(IN_SCOPE_USER,),
    )
    project_id = project.id

    again = client.post(f"/projects/{project_id}/archive", headers=as_caller(IN_SCOPE_USER))
    with_a_body = client.post(
        f"/projects/{project_id}/archive",
        json={"status": "Active"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert again.status_code == 200, again.text
    assert again.json()["status"] == "Archived"
    assert with_a_body.status_code == 200, with_a_body.text
    assert with_a_body.json()["status"] == "Archived"
    db_session.expunge_all()
    assert stored_status(db_session, project_id) == ProjectStatus.ARCHIVED.value
