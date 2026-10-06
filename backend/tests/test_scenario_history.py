"""Project-scoped scenario approval history (SC-8-02). All fixture identities are synthetic."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models import (
    ApprovedSnapshotCatalogDefaultRate,
    ApprovedSnapshotOrganizationDefaults,
    AuditActionType,
    AuditLog,
    OrganizationDefaults,
    ScenarioStatus,
)
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    caller_holding,
    make_project,
    make_scenario,
)

_SYNTHETIC_ACTOR = "synthetic-00000000-0000-4000-8000-000000000002"


def _history_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/history"


def test_k_01_scenario_history_requires_project_read_and_dedicated_history_permission(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="Synthetic history project", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Synthetic draft")
    path = _history_path(project.id, scenario.id)

    with caller_holding(Permission.PROJECT_READ):
        project_read_only = client.get(path)
    with caller_holding(Permission.SCENARIO_HISTORY_READ):
        history_only = client.get(path)
    with caller_holding(Permission.PROJECT_READ, Permission.SCENARIO_HISTORY_READ):
        without_catalog = client.get(path)
    with caller_holding(
        Permission.PROJECT_READ, Permission.SCENARIO_HISTORY_READ, Permission.CATALOG_READ
    ):
        both = client.get(path)

    assert project_read_only.status_code == 403
    assert history_only.status_code == 403
    assert without_catalog.status_code == 403
    assert both.status_code == 200, both.text


def test_k_02_unassigned_and_missing_projects_are_indistinguishable(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="Synthetic scoped project", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Synthetic scoped draft")
    path = _history_path(project.id, scenario.id)

    with caller_holding(
        Permission.PROJECT_READ,
        Permission.SCENARIO_HISTORY_READ,
        Permission.CATALOG_READ,
        user_id=OUT_OF_SCOPE_USER,
    ):
        out_of_scope = client.get(path)
        missing = client.get(_history_path(uuid.uuid4(), uuid.uuid4()))
        invalid_query_out_of_scope = client.get(f"{path}?working_calendars_limit=invalid")
    # Change only project assignment: an assigned caller can read the same scenario.
    with caller_holding(
        Permission.PROJECT_READ,
        Permission.SCENARIO_HISTORY_READ,
        Permission.CATALOG_READ,
        user_id=IN_SCOPE_USER,
    ):
        in_scope = client.get(path)

    assert in_scope.status_code == 200, in_scope.text
    assert in_scope.json()["name"] == "Synthetic scoped draft"
    assert out_of_scope.status_code == missing.status_code == 404
    assert out_of_scope.content == missing.content
    assert invalid_query_out_of_scope.status_code == 404
    assert invalid_query_out_of_scope.content == out_of_scope.content
    assert "Synthetic scoped project" not in out_of_scope.text


def test_k_04_approved_event_timestamp_actor_and_unverified_label(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="Synthetic event project", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(
        db_session, project, name="Synthetic approved", status=ScenarioStatus.APPROVED
    )
    db_session.add(
        AuditLog(
            id=uuid.uuid4(),
            scenario_id=scenario.id,
            project_id=project.id,
            action_type=AuditActionType.SCENARIO_APPROVED,
            performed_by="not-a-synthetic-actor",
        )
    )
    db_session.flush()

    with caller_holding(
        Permission.PROJECT_READ, Permission.SCENARIO_HISTORY_READ, Permission.CATALOG_READ
    ):
        response = client.get(_history_path(project.id, scenario.id))

    assert response.status_code == 200, response.text
    payload = response.json()
    event = payload["approval_event"]
    assert event["action_type"] == "scenario_approved"
    assert event["performed_by"] == "unverified-placeholder"
    assert event["performed_by_verified"] is False
    assert event["created_at"]


def test_k_05_selected_scenario_uses_own_inputs_and_only_its_approved_snapshot(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="Synthetic versions project", accessible_to=(IN_SCOPE_USER,)
    )
    approved = make_scenario(
        db_session,
        project,
        name="Synthetic approved version",
        status=ScenarioStatus.APPROVED,
        start_date=date(2026, 1, 1),
        target_margin_percent=Decimal("44.000"),
    )
    draft = make_scenario(
        db_session,
        project,
        name="Synthetic independent draft",
        target_margin_percent=Decimal("7.000"),
    )
    db_session.add_all(
        [
            AuditLog(
                id=uuid.uuid4(),
                scenario_id=approved.id,
                project_id=project.id,
                action_type=AuditActionType.SCENARIO_APPROVED,
                performed_by=_SYNTHETIC_ACTOR,
            ),
            ApprovedSnapshotOrganizationDefaults(
                id=uuid.uuid4(),
                scenario_id=approved.id,
                target_margin_percent=Decimal("22.000"),
                overload_threshold_percent=Decimal("125.000"),
            ),
            OrganizationDefaults(
                id=1,
                target_margin_percent=Decimal("99.000"),
                overload_threshold_percent=Decimal("180.000"),
            ),
        ]
    )
    db_session.flush()

    with caller_holding(
        Permission.PROJECT_READ, Permission.SCENARIO_HISTORY_READ, Permission.CATALOG_READ
    ):
        approved_response = client.get(_history_path(project.id, approved.id))
        draft_response = client.get(_history_path(project.id, draft.id))

    approved_payload = approved_response.json()
    draft_payload = draft_response.json()
    assert approved_payload["inputs"]["target_margin_percent"] == "44.000"
    assert approved_payload["approved_snapshot"]["organization_defaults"] == {
        "target_margin_percent": "22.000",
        "overload_threshold_percent": "125.000",
    }
    assert (
        approved_payload["approved_snapshot"]["organization_defaults"]["target_margin_percent"]
        != "99.000"
    )
    assert approved_payload["approval_event"]["performed_by"] == _SYNTHETIC_ACTOR
    assert draft_payload["inputs"]["target_margin_percent"] == "7.000"
    assert draft_payload["approval_event"] is None
    assert draft_payload["approved_snapshot"] is None


@pytest.mark.parametrize(
    ("global_cost_permission", "project_cost_flag", "expected_cost_field"),
    [(False, False, False), (True, False, False), (False, True, False), (True, True, True)],
)
def test_k_03_cost_rate_is_present_only_when_both_personnel_cost_gates_pass(
    client: TestClient,
    db_session: Session,
    global_cost_permission: bool,
    project_cost_flag: bool,
    expected_cost_field: bool,
) -> None:
    project = make_project(
        db_session,
        name=f"Synthetic cost gate {global_cost_permission}-{project_cost_flag}",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,) if project_cost_flag else (),
    )
    scenario = make_scenario(
        db_session, project, name="Synthetic approved cost snapshot", status=ScenarioStatus.APPROVED
    )
    db_session.add(
        ApprovedSnapshotCatalogDefaultRate(
            id=uuid.uuid4(),
            scenario_id=scenario.id,
            source_rate_id=uuid.uuid4(),
            source_role_id=uuid.uuid4(),
            source_seniority_id=uuid.uuid4(),
            source_location_id=uuid.uuid4(),
            source_engagement_type_id=uuid.uuid4(),
            source_vendor_id=None,
            default_cost_rate=Decimal("73.2500"),
            default_selling_rate=Decimal("150.0000"),
            currency="PLN",
            unit="hour",
            effective_from=date(2026, 1, 1),
            effective_to=None,
            surcharge_percent=Decimal("0.000"),
            includes_surcharge=False,
            cost_rate_unit="hour",
        )
    )
    db_session.flush()

    permissions = [
        Permission.PROJECT_READ,
        Permission.SCENARIO_HISTORY_READ,
        Permission.CATALOG_READ,
    ]
    if global_cost_permission:
        permissions.append(Permission.PERSONNEL_COSTS_READ)
    with caller_holding(*permissions):
        response = client.get(_history_path(project.id, scenario.id))

    assert response.status_code == 200, response.text
    rate_page = response.json()["approved_snapshot"]["catalog_rates"]
    assert rate_page["total"] == 1
    assert rate_page["limit"] == 100
    rate = rate_page["items"][0]
    gated_fields = {"default_cost_rate", "cost_rate_unit"}
    assert set(rate) & gated_fields == (gated_fields if expected_cost_field else set())
    if expected_cost_field:
        assert rate["default_cost_rate"] == "73.2500"


def test_snapshot_collections_are_bounded_and_page_totals_match_rows(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="Synthetic paged history project", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(
        db_session, project, name="Synthetic paged approved", status=ScenarioStatus.APPROVED
    )
    db_session.add_all(
        [
            ApprovedSnapshotCatalogDefaultRate(
                id=uuid.uuid4(),
                scenario_id=scenario.id,
                source_rate_id=uuid.uuid4(),
                source_role_id=uuid.uuid4(),
                source_seniority_id=uuid.uuid4(),
                source_location_id=uuid.uuid4(),
                source_engagement_type_id=uuid.uuid4(),
                source_vendor_id=None,
                default_cost_rate=Decimal("50.0000"),
                default_selling_rate=Decimal("100.0000"),
                currency="PLN",
                unit="hour",
                effective_from=date(2026, 1, 1),
                effective_to=None,
                surcharge_percent=Decimal("0.000"),
                includes_surcharge=False,
                cost_rate_unit="hour",
            )
            for _ in range(2)
        ]
    )
    db_session.flush()

    with caller_holding(
        Permission.PROJECT_READ, Permission.SCENARIO_HISTORY_READ, Permission.CATALOG_READ
    ):
        first = client.get(f"{_history_path(project.id, scenario.id)}?catalog_rates_limit=1")
        second = client.get(
            f"{_history_path(project.id, scenario.id)}?catalog_rates_limit=1&catalog_rates_offset=1"
        )
        invalid_limit = client.get(
            f"{_history_path(project.id, scenario.id)}?catalog_rates_limit=501"
        )
        oversized_limit = client.get(
            f"{_history_path(project.id, scenario.id)}?catalog_rates_limit={5000 * '9'}"
        )

    first_page = first.json()["approved_snapshot"]["catalog_rates"]
    second_page = second.json()["approved_snapshot"]["catalog_rates"]
    assert first.status_code == second.status_code == 200
    assert len(first_page["items"]) == len(second_page["items"]) == 1
    assert first_page["total"] == second_page["total"] == 2
    assert first_page["items"][0]["source_rate_id"] != second_page["items"][0]["source_rate_id"]
    assert invalid_limit.status_code == 422
    assert oversized_limit.status_code == 422
