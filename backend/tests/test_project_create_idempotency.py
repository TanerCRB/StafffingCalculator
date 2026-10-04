"""SC-1-18 K-06/K-07: PostgreSQL-backed project-create retry semantics."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity, Permission
from app.data.project_writes import create_project
from app.models.project import Project
from app.models.project_access import ProjectAccess
from tests.conftest import IN_SCOPE_USER, as_caller, caller_holding, count_projects, project_payload


def test_k_06_retry_after_lost_response_replays_the_created_project(
    client: TestClient, db_session: Session
) -> None:
    key = str(uuid.uuid4())
    headers = {**as_caller(IN_SCOPE_USER), "Idempotency-Key": key}

    first = client.post("/projects", json=project_payload(), headers=headers)
    retry = client.post("/projects", json=project_payload(), headers=headers)

    assert first.status_code == 201, first.text
    assert retry.status_code == 201, retry.text
    assert retry.json() == first.json()
    assert count_projects(db_session) == 1


def test_k_07_changed_payload_is_refused_and_another_caller_cannot_replay(
    client: TestClient, db_session: Session
) -> None:
    key = str(uuid.uuid4())
    headers = {**as_caller(IN_SCOPE_USER), "Idempotency-Key": key}
    first = client.post("/projects", json=project_payload(), headers=headers)
    changed = client.post(
        "/projects", json=project_payload(name="Different project"), headers=headers
    )
    other_caller = client.post(
        "/projects",
        json=project_payload(),
        headers={**as_caller("pm-bartek"), "Idempotency-Key": key},
    )

    assert first.status_code == 201, first.text
    assert changed.status_code == 409
    assert "Aurora migration" not in changed.text
    assert other_caller.status_code == 201, other_caller.text
    assert other_caller.json()["id"] != first.json()["id"]
    assert count_projects(db_session) == 2



def test_k_07_same_key_is_scoped_to_each_caller_even_when_both_have_records(
    client: TestClient, db_session: Session
) -> None:
    key = str(uuid.uuid4())
    other_headers = {**as_caller("pm-bartek"), "Idempotency-Key": key}
    caller_headers = {**as_caller(IN_SCOPE_USER), "Idempotency-Key": key}

    other_callers_project = client.post("/projects", json=project_payload(), headers=other_headers)
    callers_project = client.post("/projects", json=project_payload(), headers=caller_headers)
    callers_retry = client.post("/projects", json=project_payload(), headers=caller_headers)

    assert other_callers_project.status_code == 201, other_callers_project.text
    assert callers_project.status_code == 201, callers_project.text
    assert callers_retry.status_code == 201, callers_retry.text
    assert callers_project.json()["id"] != other_callers_project.json()["id"]
    assert callers_retry.json() == callers_project.json()
    assert other_callers_project.json()["id"] not in callers_retry.text
    assert count_projects(db_session) == 2


def test_k_07_retry_after_access_revocation_does_not_replay_project_details(
    client: TestClient, db_session: Session
) -> None:
    key = str(uuid.uuid4())
    headers = {**as_caller(IN_SCOPE_USER), "Idempotency-Key": key}
    first = client.post("/projects", json=project_payload(), headers=headers)
    assert first.status_code == 201, first.text
    project_id = first.json()["id"]

    db_session.execute(
        sa.delete(ProjectAccess).where(
            ProjectAccess.user_id == IN_SCOPE_USER,
            ProjectAccess.project_id == uuid.UUID(project_id),
        )
    )
    db_session.commit()

    retry = client.post("/projects", json=project_payload(), headers=headers)

    assert retry.status_code == 409
    assert project_id not in retry.text
    assert "Aurora migration" not in retry.text
    assert count_projects(db_session) == 1
    assert db_session.execute(
        sa.select(sa.func.count()).select_from(ProjectAccess).where(
            ProjectAccess.project_id == uuid.UUID(project_id)
        )
    ).scalar_one() == 0
def test_k_07_permission_is_rechecked_before_replay(
    client: TestClient, db_session: Session
) -> None:
    key = str(uuid.uuid4())
    headers = {**as_caller(IN_SCOPE_USER), "Idempotency-Key": key}
    first = client.post("/projects", json=project_payload(), headers=headers)
    assert first.status_code == 201, first.text

    with caller_holding(Permission.PROJECT_READ, user_id=IN_SCOPE_USER):
        denied = client.post("/projects", json=project_payload(), headers=headers)

    assert denied.status_code == 403
    assert first.json()["id"] not in denied.text
    assert count_projects(db_session) == 1


def test_k_06_concurrent_identical_retries_commit_one_project(
    engine: Engine,
) -> None:
    key = uuid.uuid4()
    caller = CallerIdentity(
        user_id="sc-1-18-concurrent",
        permissions=frozenset({Permission.PROJECT_CREATE}),
    )
    barrier = threading.Barrier(2)
    results: list[uuid.UUID] = []

    def submit() -> uuid.UUID:
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            view = create_project(
                session,
                caller,
                name="Concurrent retry",
                client="Northwind",
                owner="Owner",
                delivery_period_start=date(2026, 3, 1),
                delivery_period_end=date(2026, 11, 30),
                reporting_currency="EUR",
                description="same request",
                idempotency_key=key,
            )
            return view.project.id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))
        assert results[0] == results[1]
        with engine.connect() as connection:
            row_count = connection.execute(
                sa.select(sa.func.count()).select_from(Project).where(
                    Project.id == results[0]
                )
            ).scalar_one()
        assert row_count == 1
    finally:
        with engine.begin() as connection:
            connection.execute(sa.delete(Project).where(Project.id.in_(set(results))))
