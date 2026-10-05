"""SC-1-15: the project-copy API classifies database refusals without leaking row data."""

import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data import project_writes
from app.data.project_reads import project_for_caller
from app.data.project_writes import ProjectCopyRefused
from app.main import app
from app.models import Project, ProjectAccess, Scenario, ScenarioDeliverySegment
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    make_project,
    make_scenario,
    make_scenario_delivery_segment,
)


@pytest.fixture
def source_id(engine: Engine, committing_client: TestClient) -> uuid.UUID:
    with Session(engine) as session:
        source = make_project(
            session, name="Sensitive owner marker", accessible_to=(IN_SCOPE_USER,)
        )
        scenario = make_scenario(session, source, name="Baseline")
        make_scenario_delivery_segment(session, scenario, name="Phase 1")
        source_id = source.id
        session.commit()
    return source_id


def _refuse_after_copying_scenario(
    monkeypatch: pytest.MonkeyPatch,
    *,
    invalid_assignment: str = "delivery_period_end = delivery_period_start - 1",
) -> None:
    original = project_writes.copy_scenario

    def invalid_copy(session: Session, source: Scenario, *, into_project: Project) -> Scenario:
        copied = original(session, source, into_project=into_project)
        session.execute(
            sa.text(
                f"UPDATE projects SET {invalid_assignment} WHERE id = :project_id"
            ),
            {"project_id": into_project.id},
        )
        return copied

    monkeypatch.setattr(project_writes, "copy_scenario", invalid_copy)


def test_k_01_classified_check_refusal_is_409_and_unclassified_failure_is_500(
    engine: Engine,
    committing_client: TestClient,
    source_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refuse_after_copying_scenario(monkeypatch)
    refused = committing_client.post(
        f"/projects/{source_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )
    assert refused.status_code == 409
    assert "sqlstate=23514" in refused.json()["detail"]

    def broken_copy(session: Session, source: Scenario, *, into_project: Project) -> Scenario:
        session.execute(sa.text("SELECT 1 / 0"))
        raise AssertionError("the database statement should fail")

    monkeypatch.setattr(project_writes, "copy_scenario", broken_copy)
    with TestClient(app, raise_server_exceptions=False) as server_client:
        failed = server_client.post(f"/projects/{source_id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert failed.status_code == 500


@pytest.mark.parametrize(
    ("invalid_assignment", "constraint"),
    [
        ("delivery_period_end = delivery_period_start - 1", "ck_projects_delivery_period_ordered"),
        ("owner = '   '", "ck_projects_owner_not_blank"),
    ],
)
def test_k_02_refusal_names_the_actual_database_constraint(
    committing_client: TestClient,
    source_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    invalid_assignment: str,
    constraint: str,
) -> None:
    _refuse_after_copying_scenario(monkeypatch, invalid_assignment=invalid_assignment)
    response = committing_client.post(
        f"/projects/{source_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 409
    assert f"constraint={constraint}" in response.json()["detail"]


def test_k_03_refusal_excludes_failing_row_values_and_driver_detail(
    committing_client: TestClient, source_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_after_copying_scenario(monkeypatch)
    response = committing_client.post(
        f"/projects/{source_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 409
    body = response.text
    assert "Sensitive owner marker" not in body
    assert "Anna Kowalska" not in body
    assert "Failing row contains" not in body
    assert "UPDATE projects" not in body


def test_k_04_refusal_rolls_back_project_grant_and_scenario_for_a_separate_connection(
    engine: Engine,
    committing_client: TestClient,
    source_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refuse_after_copying_scenario(monkeypatch)
    response = committing_client.post(
        f"/projects/{source_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 409
    with engine.connect() as connection:
        for model in (Project, ProjectAccess, Scenario, ScenarioDeliverySegment):
            count = connection.scalar(sa.select(sa.func.count()).select_from(model))
            assert count == 1, model.__tablename__

    # The same session must recover too. Removing copy_project's explicit rollback leaves a
    # PostgreSQL transaction aborted; closing a request session alone would hide that mutation.
    caller = CallerIdentity(user_id=IN_SCOPE_USER, permissions=frozenset())
    with Session(engine) as session:
        source = project_for_caller(session, caller, source_id)
        assert source is not None
        with pytest.raises(ProjectCopyRefused):
            project_writes.copy_project(session, caller, source)
        assert session.scalar(sa.select(sa.func.count()).select_from(Project)) == 1


def test_k_05_accepted_copy_keeps_response_and_complete_aggregate(
    engine: Engine, committing_client: TestClient, source_id: uuid.UUID
) -> None:
    response = committing_client.post(
        f"/projects/{source_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 201
    copy_id = uuid.UUID(response.json()["id"])
    assert copy_id != source_id
    assert response.json()["name"] == "Sensitive owner marker"
    with engine.connect() as connection:
        copied_scenarios = connection.scalars(
            sa.select(Scenario.name).where(Scenario.project_id == copy_id)
        ).all()
        copied_segments = connection.scalars(
            sa.select(ScenarioDeliverySegment.name)
            .join(Scenario, Scenario.id == ScenarioDeliverySegment.scenario_id)
            .where(Scenario.project_id == copy_id)
        ).all()
        grant = connection.scalar(
            sa.select(ProjectAccess.user_id).where(ProjectAccess.project_id == copy_id)
        )
    assert copied_scenarios == ["Baseline"]
    assert copied_segments == ["Phase 1"]
    assert grant == IN_SCOPE_USER
