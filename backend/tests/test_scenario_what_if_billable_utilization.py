"""SC-6-05: draft T&M utilization sensitivity without persisted scenario changes."""

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.data.scenario_what_if as scenario_what_if_module
from app.core.identity import CallerIdentity
from app.data.commercial_terms import priced_month_windows
from app.data.personnel_cost import scenario_cost_for_caller as real_scenario_cost_for_caller
from app.models import ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    caller_holding,
    make_allocation,
    make_project,
    make_scenario,
    make_story_points_terms,
)
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)

APR = date(2026, 4, 1)
NOT_FOUND = {"detail": "Scenario not found."}


def utilization_path(project_id: uuid.UUID, scenario_id: uuid.UUID, decrease: str) -> str:
    return (
        f"/projects/{project_id}/scenarios/{scenario_id}/what-if/billable-utilization"
        f"?billable_utilization_decrease_percentage_points={decrease}"
    )


def test_ordinary_tm_revenue_read_does_not_select_planned_allocation_hours() -> None:
    ordinary_columns = set(
        priced_month_windows(from_snapshot=False, scenario_id=uuid.uuid4()).selected_columns.keys()
    )
    sensitivity_columns = set(
        priced_month_windows(
            from_snapshot=False,
            scenario_id=uuid.uuid4(),
            include_planned_allocation_hours=True,
        ).selected_columns.keys()
    )

    assert "planned_allocation_hours" not in ordinary_columns
    assert "planned_allocation_hours" in sensitivity_columns


def test_k_01_reduces_per_position_month_and_leaves_zero_plan_hours_unchanged(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, position = _full_scenario(
        db_session,
        name="UtilizationPerMonth",
        planned_hours=Decimal("100.00"),
        billable_hours=Decimal("80.00"),
    )
    make_allocation(
        db_session,
        position,
        period_month=APR,
        planned_allocation_hours=Decimal("0.00"),
        billable_hours=Decimal("7.00"),
    )

    with caller_holding(*EVERYTHING):
        response = client.get(utilization_path(project.id, scenario.id, "10"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["amount"] == "15400.00"
    assert Decimal(body["billable_utilization_decrease_percentage_points"]) == Decimal("10")


def test_k_02_zero_is_baseline_and_invalid_decreases_return_one_generic_422(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session,
        name="UtilizationBoundaries",
        planned_hours=Decimal("100.00"),
        billable_hours=Decimal("100.00"),
    )
    def path(value: str) -> str:
        return utilization_path(project.id, scenario.id, value)

    with caller_holding(*EVERYTHING):
        baseline = client.get(results_path(project.id, scenario.id))
        zero = client.get(path("0"))
        negative = client.get(path("-1"))
        below_zero_hours = client.get(path("100.001"))
        exact_zero_hours = client.get(path("100"))

    assert baseline.status_code == zero.status_code == exact_zero_hours.status_code == 200
    assert zero.json()["revenue"] == baseline.json()["revenue"]
    assert exact_zero_hours.json()["revenue"]["amount"] == "0.00"
    for response in (negative, below_zero_hours):
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid utilization decrease."}


def test_k_03_existing_tm_revenue_formula_prices_the_temporary_hours(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session,
        name="UtilizationRevenue",
        planned_hours=Decimal("100.00"),
        billable_hours=Decimal("80.00"),
    )

    with caller_holding(*EVERYTHING):
        result = client.get(utilization_path(project.id, scenario.id, "10"))

    assert result.status_code == 200, result.text
    revenue = result.json()["revenue"]
    assert revenue["amount"] == "14000.00"
    assert revenue["assumptions_used"]["rate_source"] == "live_catalog"


def test_k_04_profitability_changes_with_revenue_while_cost_inputs_stay_at_baseline(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session,
        name="UtilizationProfit",
        planned_hours=Decimal("100.00"),
        billable_hours=Decimal("100.00"),
    )

    with caller_holding(*EVERYTHING):
        baseline = client.get(results_path(project.id, scenario.id))
        result = client.get(utilization_path(project.id, scenario.id, "10"))

    assert baseline.status_code == result.status_code == 200
    before, after = baseline.json(), result.json()
    assert after["revenue"]["amount"] == "18000.00"
    assert after["personnel_cost"] == before["personnel_cost"]
    assert after["additional_cost"] == before["additional_cost"]
    assert after["included_cost"] == before["included_cost"] == "14000.00"
    assert after["profit"] == "4000.00"


def test_k_05_results_and_database_rows_are_unchanged_by_the_what_if(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, position = _full_scenario(db_session, name="UtilizationReadOnly")
    db_session.flush()
    before_rows = db_session.execute(
        sa.text(
            "SELECT period_month, planned_allocation_hours, billable_hours "
            "FROM staffing_position_allocation WHERE position_id = :position_id "
            "ORDER BY period_month"
        ),
        {"position_id": str(position.id)},
    ).all()
    before_scenario = db_session.execute(
        sa.text("SELECT name, status, currency, updated_at FROM scenarios WHERE id = :id"),
        {"id": str(scenario.id)},
    ).one()

    with caller_holding(*EVERYTHING):
        before = client.get(results_path(project.id, scenario.id))
        what_if = client.get(utilization_path(project.id, scenario.id, "25"))
        after = client.get(results_path(project.id, scenario.id))

    after_rows = db_session.execute(
        sa.text(
            "SELECT period_month, planned_allocation_hours, billable_hours "
            "FROM staffing_position_allocation WHERE position_id = :position_id "
            "ORDER BY period_month"
        ),
        {"position_id": str(position.id)},
    ).all()
    after_scenario = db_session.execute(
        sa.text("SELECT name, status, currency, updated_at FROM scenarios WHERE id = :id"),
        {"id": str(scenario.id)},
    ).one()
    assert what_if.status_code == 200, what_if.text
    assert before.status_code == after.status_code == 200
    assert before.json() == after.json()
    assert before_rows == after_rows
    assert before_scenario == after_scenario
    assert not db_session.new and not db_session.dirty and not db_session.deleted


def test_k_06_out_of_scope_and_approved_scenarios_are_the_same_404(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="UtilizationScope")
    other_project = make_project(
        db_session, name="OtherUtilizationScope", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    other_scenario = make_scenario(db_session, other_project, name="Other")
    approved_project, approved_scenario, _ = _full_scenario(
        db_session, name="UtilizationApproved"
    )
    approved_scenario.status = ScenarioStatus.APPROVED
    db_session.flush()
    non_tm_project = make_project(
        db_session, name="UtilizationNonTm", accessible_to=(IN_SCOPE_USER,)
    )
    non_tm_scenario = make_scenario(db_session, non_tm_project, name="Story Points")
    make_story_points_terms(db_session, non_tm_scenario)

    paths = (
        utilization_path(other_project.id, other_scenario.id, "0"),
        utilization_path(project.id, uuid.uuid4(), "0"),
        utilization_path(uuid.uuid4(), scenario.id, "0"),
        utilization_path(approved_project.id, approved_scenario.id, "999.999"),
        utilization_path(non_tm_project.id, non_tm_scenario.id, "0"),
    )
    with caller_holding(*EVERYTHING):
        responses = [client.get(path) for path in paths]

    assert all(response.status_code == 404 for response in responses), [
        (response.status_code, response.text) for response in responses
    ]
    assert all(response.json() == NOT_FOUND for response in responses)
    assert len({response.content for response in responses}) == 1


def test_k_07_a_status_race_is_refused_by_the_shared_guard(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="UtilizationRace")

    def disagreeing_cost_view(
        session: Session,
        caller: CallerIdentity,
        project_id: uuid.UUID,
        scenario_id: uuid.UUID,
    ) -> object:
        view = real_scenario_cost_for_caller(session, caller, project_id, scenario_id)
        assert view is not None
        return replace(view, status_at_read=ScenarioStatus.APPROVED)

    monkeypatch.setattr(
        scenario_what_if_module, "scenario_cost_for_caller", disagreeing_cost_view
    )
    with caller_holding(*EVERYTHING):
        response = client.get(utilization_path(project.id, scenario.id, "10"))

    assert response.status_code == 409, response.text
    assert "live_catalog" not in response.text
    assert "approved_snapshot" not in response.text
