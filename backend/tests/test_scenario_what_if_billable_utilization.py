"""SC-6-05: draft T&M utilization sensitivity without persisted scenario changes."""

import threading
import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import app.data.scenario_what_if as scenario_what_if_module
from app.core.identity import CallerIdentity
from app.data.commercial_terms import (
    commercial_terms_for_caller as real_commercial_terms_for_caller,
)
from app.data.commercial_terms import (
    priced_month_windows,
)
from app.data.personnel_cost import scenario_cost_for_caller as real_scenario_cost_for_caller
from app.data.scenario_guard import unapproved_scenario
from app.data.staffing import update_allocation
from app.domain.revenue_time_and_material import BillableMonth
from app.models import ScenarioStatus, StaffingPosition
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    caller_holding,
    make_allocation,
    make_project,
    make_scenario,
    make_story_points_terms,
    wait_until_a_lock_request_is_pending,
)
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)

APR = date(2026, 4, 1)
MAR = date(2026, 3, 1)
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


def test_non_tm_model_guard_rejects_even_when_billable_months_are_present(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(db_session, name="UtilizationModelGuard", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Story Points")
    make_story_points_terms(db_session, scenario)
    caller = CallerIdentity(user_id=IN_SCOPE_USER)
    commercial = real_commercial_terms_for_caller(
        db_session, caller, project.id, scenario.id, include_billable_months=True
    )
    assert commercial is not None and commercial.terms is not None
    assert commercial.terms.model_type == "story_points"

    # Keep the subsequent `billable_months is None` guard from masking removal of the model check.
    forced_billable_months = replace(
        commercial,
        billable_months=(
            BillableMonth(
                position_id=uuid.uuid4(),
                period_month=APR,
                billable_hours=Decimal("20"),
                price=None,
                planned_allocation_hours=Decimal("100"),
            ),
        ),
    )
    monkeypatch.setattr(
        scenario_what_if_module,
        "commercial_terms_for_caller",
        lambda *args, **kwargs: forced_billable_months,
    )

    def unexpected_cost_read(*args: object, **kwargs: object) -> None:
        pytest.fail("non-T&M scenarios must be refused before reading costs")

    monkeypatch.setattr(
        scenario_what_if_module, "scenario_cost_for_caller", unexpected_cost_read
    )

    result = scenario_what_if_module.scenario_what_if_billable_utilization_for_caller(
        db_session,
        caller,
        project.id,
        scenario.id,
        decrease_percentage_points=Decimal("10"),
    )

    assert result is None


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


def test_k_07_a_guarded_staffing_edit_waits_for_what_if(
    committing_client: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read holding `FOR SHARE` keeps a guarded staffing edit out until all components finish."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        _ensure_statutory_bypass(setup)
        project, scenario, position = _full_scenario(
            setup,
            name="UtilizationReaderWins",
            planned_hours=Decimal("100.00"),
            billable_hours=Decimal("80.00"),
        )
        project_id, scenario_id, position_id = project.id, scenario.id, position.id
        setup.commit()

    cost_read_reached = threading.Event()
    finish_calculation = threading.Event()
    result: dict[str, Any] = {}

    def paused_cost_read(
        session: Session,
        caller: CallerIdentity,
        project_id: uuid.UUID,
        scenario_id: uuid.UUID,
    ) -> object:
        cost_read_reached.set()
        if not finish_calculation.wait(timeout=15):
            raise TimeoutError("test did not release the paused what-if calculation")
        return real_scenario_cost_for_caller(session, caller, project_id, scenario_id)

    monkeypatch.setattr(
        scenario_what_if_module, "scenario_cost_for_caller", paused_cost_read
    )

    def read_what_if() -> None:
        try:
            result["response"] = committing_client.get(
                utilization_path(project_id, scenario_id, "10"),
                headers=as_caller(IN_SCOPE_USER),
            )
        except BaseException as error:  # noqa: BLE001 - captured for the parent test
            result["error"] = error

    request = threading.Thread(target=read_what_if, daemon=True)
    request.start()
    assert cost_read_reached.wait(timeout=10), "the what-if did not reach the paused cost read"

    def edit_allocation() -> None:
        try:
            with Session(bind=engine, expire_on_commit=False, future=True) as writer:
                position_row = writer.get(StaffingPosition, position_id)
                assert position_row is not None
                result["edit"] = update_allocation(
                    writer,
                    CallerIdentity(IN_SCOPE_USER),
                    project_id,
                    scenario_id,
                    position_id,
                    MAR,
                    expected_updated_at=position_row.updated_at,
                    changes={
                        "planned_allocation_hours": Decimal("200.00"),
                        "billable_hours": Decimal("160.00"),
                    },
                )
        except BaseException as error:  # noqa: BLE001 - captured for the parent test
            result["edit_error"] = error

    writer = threading.Thread(target=edit_allocation, daemon=True)
    writer.start()
    try:
        blocked = wait_until_a_lock_request_is_pending(engine)
    finally:
        finish_calculation.set()
    request.join(timeout=15)
    writer.join(timeout=15)

    assert not request.is_alive(), "the what-if never completed"
    assert not writer.is_alive(), "the guarded staffing edit never completed"
    assert "error" not in result, result.get("error")
    assert "edit_error" not in result, result.get("edit_error")
    assert blocked, "the guarded staffing edit did not wait for the what-if's scenario lock"
    assert result["response"].status_code == 200, result["response"].text
    assert result["response"].json()["revenue"]["amount"] == "14000.00"
    assert result["edit"] is not None

    after_edit = committing_client.get(
        utilization_path(project_id, scenario_id, "10"), headers=as_caller(IN_SCOPE_USER)
    )
    assert after_edit.status_code == 200, after_edit.text
    assert after_edit.json()["revenue"]["amount"] == "28000.00"


def test_k_07_a_waiting_what_if_reads_the_committed_staffing_edit(
    committing_client: TestClient,
    engine: Engine,
) -> None:
    """If a guarded edit gets `FOR UPDATE` first, the what-if reads its committed values."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        _ensure_statutory_bypass(setup)
        project, scenario, position = _full_scenario(
            setup,
            name="UtilizationWriterWins",
            planned_hours=Decimal("100.00"),
            billable_hours=Decimal("80.00"),
        )
        project_id, scenario_id, position_id = project.id, scenario.id, position.id
        setup.commit()

    result: dict[str, Any] = {}

    def read_what_if() -> None:
        try:
            result["response"] = committing_client.get(
                utilization_path(project_id, scenario_id, "10"),
                headers=as_caller(IN_SCOPE_USER),
            )
        except BaseException as error:  # noqa: BLE001 - captured for the parent test
            result["error"] = error

    with Session(bind=engine, expire_on_commit=False, future=True) as writer:
        assert writer.execute(unapproved_scenario(scenario_id)).one_or_none()
        request = threading.Thread(target=read_what_if, daemon=True)
        request.start()
        blocked = wait_until_a_lock_request_is_pending(engine)
        position_row = writer.get(StaffingPosition, position_id)
        assert position_row is not None
        edited = update_allocation(
            writer,
            CallerIdentity(IN_SCOPE_USER),
            project_id,
            scenario_id,
            position_id,
            MAR,
            expected_updated_at=position_row.updated_at,
            changes={
                "planned_allocation_hours": Decimal("200.00"),
                "billable_hours": Decimal("160.00"),
            },
        )
        assert edited is not None

    request.join(timeout=15)
    assert not request.is_alive(), "the what-if never completed after the staffing edit committed"
    assert "error" not in result, result.get("error")
    assert blocked, "the what-if did not wait for the writer's scenario lock"
    assert result["response"].status_code == 200, result["response"].text
    assert result["response"].json()["revenue"]["amount"] == "28000.00"
