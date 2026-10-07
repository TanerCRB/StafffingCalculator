"""SC-1-26: create an independent named draft in a caller-visible project."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models import ProjectStatus, Scenario, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    caller_holding,
    make_project,
    make_scenario,
)

CREATE_PERMISSIONS = (Permission.SCENARIO_CREATE, Permission.PROJECT_READ)


def scenario_create_path(project_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios"


def count_scenarios(session: Session, project_id: uuid.UUID) -> int:
    return session.scalar(
        sa.select(sa.func.count()).select_from(Scenario).where(Scenario.project_id == project_id)
    )


def test_k_01_create_requires_dedicated_permission_and_project_scope(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="Create auth", accessible_to=(IN_SCOPE_USER,))
    path = scenario_create_path(project.id)
    before = count_scenarios(db_session, project.id)

    with caller_holding(Permission.PROJECT_READ, user_id=IN_SCOPE_USER):
        missing_create = client.post(
            path, json={"name": "Denied without create"}, headers=as_caller(IN_SCOPE_USER)
        )
    assert count_scenarios(db_session, project.id) == before
    assert missing_create.status_code == 403

    with caller_holding(Permission.SCENARIO_CREATE, user_id=IN_SCOPE_USER):
        missing_project_read = client.post(
            path,
            json={"name": "Denied without project read"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert count_scenarios(db_session, project.id) == before
    assert missing_project_read.status_code == 403

    with caller_holding(*CREATE_PERMISSIONS, user_id=IN_SCOPE_USER):
        created = client.post(path, json={"name": "Allowed"}, headers=as_caller(IN_SCOPE_USER))
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "Draft"
    assert count_scenarios(db_session, project.id) == before + 1

    outside_project = make_project(
        db_session, name="Outside create", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    with caller_holding(*CREATE_PERMISSIONS, user_id=IN_SCOPE_USER):
        outside = client.post(
            scenario_create_path(outside_project.id),
            json={"name": "Hidden"},
            headers=as_caller(IN_SCOPE_USER),
        )
        unknown = client.post(
            scenario_create_path(uuid.uuid4()),
            json={"name": "Unknown"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert (outside.status_code, outside.json()) == (unknown.status_code, unknown.json())
    assert count_scenarios(db_session, outside_project.id) == 0


def test_k_02_created_draft_is_committed_and_belongs_to_selected_project(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="Selected project", accessible_to=(IN_SCOPE_USER,))
    other = make_project(db_session, name="Other project", accessible_to=(IN_SCOPE_USER,))
    with caller_holding(*CREATE_PERMISSIONS, user_id=IN_SCOPE_USER):
        response = client.post(
            scenario_create_path(project.id),
            json={"name": "Planning draft"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert response.status_code == 201, response.text
    scenario_id = uuid.UUID(response.json()["id"])
    db_session.expire_all()
    row = db_session.get(Scenario, scenario_id)
    assert row is not None
    assert row.status == ScenarioStatus.DRAFT
    assert row.project_id == project.id
    assert row.project_id != other.id
    assert count_scenarios(db_session, project.id) == 1
    assert count_scenarios(db_session, other.id) == 0


def test_k_03_new_draft_readiness_is_its_own_missing_input_state(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="Independent readiness", accessible_to=(IN_SCOPE_USER,))
    sibling = make_scenario(
        db_session,
        project,
        name="Configured sibling",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        working_calendar="Standard",
        full_time_hours_per_week=Decimal("40"),
        currency="EUR",
        target_margin_percent=Decimal("20"),
        overload_threshold_percent=Decimal("10"),
    )
    with caller_holding(*CREATE_PERMISSIONS, user_id=IN_SCOPE_USER):
        response = client.post(
            scenario_create_path(project.id),
            json={"name": "Incomplete draft"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["missing_inputs"]
    assert payload["ready_for_approval"] is False
    assert sibling.start_date is not None
    assert payload["id"] != str(sibling.id)

    with caller_holding(Permission.PROJECT_READ, user_id=IN_SCOPE_USER):
        project_response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))
    assert project_response.status_code == 200
    scenarios = {
        item["id"]: item
        for listed_project in project_response.json()["projects"]
        if listed_project["id"] == str(project.id)
        for item in listed_project["scenarios"]
    }
    assert scenarios[str(sibling.id)]["missing_inputs"] == []
    assert scenarios[str(sibling.id)]["ready_for_approval"] is True


def test_k_04_exact_name_conflict_is_project_scoped_and_variants_are_distinct(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="Duplicate target", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, project, name="Baseline")
    before = count_scenarios(db_session, project.id)
    with caller_holding(*CREATE_PERMISSIONS, user_id=IN_SCOPE_USER):
        duplicate = client.post(
            scenario_create_path(project.id),
            json={"name": "Baseline"},
            headers=as_caller(IN_SCOPE_USER),
        )
        case_variant = client.post(
            scenario_create_path(project.id),
            json={"name": "baseline"},
            headers=as_caller(IN_SCOPE_USER),
        )
        whitespace_variant = client.post(
            scenario_create_path(project.id),
            json={"name": "Baseline "},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert duplicate.status_code == 409
    assert "exact name" in duplicate.json()["detail"]
    assert count_scenarios(db_session, project.id) == before + 2
    assert case_variant.status_code == 201, case_variant.text
    assert whitespace_variant.status_code == 201, whitespace_variant.text
    assert db_session.get(Scenario, uuid.UUID(case_variant.json()["id"])).name == "baseline"
    assert db_session.get(Scenario, uuid.UUID(whitespace_variant.json()["id"])).name == "Baseline "

    other = make_project(db_session, name="Other name scope", accessible_to=(IN_SCOPE_USER,))
    with caller_holding(*CREATE_PERMISSIONS, user_id=IN_SCOPE_USER):
        same_name_other_project = client.post(
            scenario_create_path(other.id),
            json={"name": "Baseline"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert same_name_other_project.status_code == 201, same_name_other_project.text
    assert count_scenarios(db_session, other.id) == 1


def test_k_05_archived_project_refusal_is_named_and_does_not_write(
    client: TestClient, db_session: Session
) -> None:
    active = make_project(
        db_session,
        name="Active target",
        status=ProjectStatus.ACTIVE,
        accessible_to=(IN_SCOPE_USER,),
    )
    archived = make_project(
        db_session,
        name="Archived target",
        status=ProjectStatus.ARCHIVED,
        accessible_to=(IN_SCOPE_USER,),
    )
    before = count_scenarios(db_session, archived.id)
    with caller_holding(*CREATE_PERMISSIONS, user_id=IN_SCOPE_USER):
        active_response = client.post(
            scenario_create_path(active.id),
            json={"name": "Baseline"},
            headers=as_caller(IN_SCOPE_USER),
        )
        response = client.post(
            scenario_create_path(archived.id),
            json={"name": "Baseline"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert active_response.status_code == 201, active_response.text
    assert count_scenarios(db_session, active.id) == 1
    assert response.status_code == 409
    assert "archived project" in response.json()["detail"]
    assert count_scenarios(db_session, archived.id) == before


def test_k_04_concurrent_exact_name_race_returns_named_conflict_and_one_row(
    committing_client: TestClient, engine: Engine
) -> None:
    """The database uniqueness constraint decides when both prechecks see an unused name."""
    from sqlalchemy import event

    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(
            setup, name="Concurrent duplicate", accessible_to=(IN_SCOPE_USER,)
        )
        project_id = project.id
        setup.commit()

    both_prechecks_finished = threading.Barrier(2)
    synchronized_queries: list[str] = []

    def synchronize_after_duplicate_precheck(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ) -> None:
        normalized = statement.lower()
        if (
            normalized.lstrip().startswith("select")
            and "from scenarios" in normalized
            and "where scenarios.project_id =" in normalized
            and "and scenarios.name =" in normalized
        ):
            synchronized_queries.append(statement)
            both_prechecks_finished.wait(timeout=30)

    event.listen(Engine, "after_cursor_execute", synchronize_after_duplicate_precheck)
    try:
        with caller_holding(*CREATE_PERMISSIONS, user_id=IN_SCOPE_USER):
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(
                    executor.map(
                        lambda _: committing_client.post(
                            scenario_create_path(project_id),
                            json={"name": "Same name"},
                            headers=as_caller(IN_SCOPE_USER),
                        ),
                        range(2),
                    )
                )
    finally:
        event.remove(Engine, "after_cursor_execute", synchronize_after_duplicate_precheck)

    assert len(synchronized_queries) == 2, "both requests must pass the empty-name precheck"
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"] == (
        "A scenario with this exact name already exists in this project."
    )
    with engine.connect() as connection:
        count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(Scenario)
            .where(Scenario.project_id == project_id, Scenario.name == "Same name")
        )
    assert count == 1
