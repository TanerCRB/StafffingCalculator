"""Inputs for the period results contract (SC-7-11).

This composes the already-scoped scenario result read with the existing resolved-assumption
reader and an anonymous monthly planned-FTE aggregation. It introduces no second project-scope
predicate and reads calendar bases through the same live/frozen split as existing calculations.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.assumptions import scenario_assumptions_for_caller
from app.data.scenario_results import (
    ScenarioResultsRaceDetected,
    ScenarioResultsView,
    scenario_results_for_caller,
)
from app.data.working_calendar import basis_by_location, frozen_basis_by_location
from app.domain.fte_hours import hours_to_fte_percent
from app.models.scenario import ScenarioStatus
from app.models.staffing import StaffingPosition, StaffingPositionAllocation


@dataclass(frozen=True)
class ScenarioPeriodResultsView:
    results: ScenarioResultsView
    target_margin_percent: Decimal | None
    planned_fte_by_month: dict[date, Decimal | str]


def _planned_fte_by_month(session: Session, view: ScenarioResultsView) -> dict[date, Decimal | str]:
    rows = session.execute(
        sa.select(
            StaffingPosition.location_id,
            StaffingPositionAllocation.period_month,
            StaffingPositionAllocation.planned_allocation_hours,
        )
        .join(
            StaffingPositionAllocation,
            StaffingPositionAllocation.position_id == StaffingPosition.id,
        )
        .where(StaffingPosition.scenario_id == view.scenario.id)
        .order_by(StaffingPositionAllocation.period_month, StaffingPosition.id)
    ).all()
    locations = sorted({row.location_id for row in rows})
    if view.scenario.status is ScenarioStatus.APPROVED:
        bases = frozen_basis_by_location(session, view.scenario.id)
    else:
        bases = basis_by_location(session, locations)

    totals: dict[date, Decimal] = {}
    unavailable: set[date] = set()
    for location_id, period_month, hours in rows:
        conversion = hours_to_fte_percent(
            bases.get(location_id), period_month=period_month, hours=hours
        )
        if conversion.fte_exact is None:
            unavailable.add(period_month)
            continue
        totals[period_month] = totals.get(period_month, Decimal("0")) + conversion.fte_exact
    return {
        month: "n/a" if month in unavailable else totals.get(month, Decimal("0"))
        for month in set(totals) | unavailable
    }


def scenario_period_results_for_caller(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> ScenarioPeriodResultsView | None:
    results = scenario_results_for_caller(session, caller, project_id, scenario_id)
    if results is None:
        return None

    assumptions = scenario_assumptions_for_caller(session, caller, project_id, scenario_id)
    if assumptions is None:
        return None
    assumptions_status = assumptions.scenario.status
    if assumptions_status != results.scenario.status:
        raise ScenarioResultsRaceDetected(
            revenue_status=results.scenario.status,
            cost_status=results.cost_view.status_at_read,
            additional_cost_status=assumptions_status,
        )

    target = assumptions.assumptions["target_margin_percent"]
    return ScenarioPeriodResultsView(
        results=results,
        target_margin_percent=target.value if isinstance(target.value, Decimal) else None,
        planned_fte_by_month=_planned_fte_by_month(session, results),
    )
