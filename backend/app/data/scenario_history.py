"""Scoped read of a scenario's own approval event and frozen input rows (SC-8-02)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, literal, select, true
from sqlalchemy.orm import Session, aliased

from app.core.identity import CallerIdentity
from app.data.project_reads import CallerProjectView, scenario_for_caller
from app.models.approved_snapshot import (
    ApprovedSnapshotAbsenceBudget,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotCatalogDefaultRate,
    ApprovedSnapshotExchangeRate,
    ApprovedSnapshotOrganizationDefaults,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
)
from app.models.audit_log import AuditActionType, AuditLog
from app.models.scenario import Scenario, ScenarioStatus

MAX_SCENARIO_HISTORY_PAGE_OFFSET = 1_000_000
SNAPSHOT_COLLECTIONS = (
    "working_calendars",
    "working_calendar_days",
    "absence_types",
    "absence_budgets",
    "catalog_rates",
    "exchange_rates",
)
DEFAULT_SNAPSHOT_PAGE_LIMITS = {
    "working_calendars": 100,
    "working_calendar_days": 100,
    "absence_types": 100,
    "absence_budgets": 100,
    "catalog_rates": 100,
    "exchange_rates": 100,
}
MAX_SNAPSHOT_PAGE_LIMITS = {
    "working_calendars": 500,
    "working_calendar_days": 500,
    "absence_types": 500,
    "absence_budgets": 500,
    "catalog_rates": 500,
    "exchange_rates": 500,
}


@dataclass(frozen=True)
class SnapshotPage[T]:
    items: tuple[T, ...]
    total: int
    limit: int
    offset: int


class ScenarioHistoryPageInvalid(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


@dataclass(frozen=True)
class ScenarioHistoryView:
    project_view: CallerProjectView
    scenario: Scenario
    approval_event: AuditLog | None
    organization_defaults: ApprovedSnapshotOrganizationDefaults | None
    working_calendars: SnapshotPage[ApprovedSnapshotWorkingCalendar]
    working_calendar_days: SnapshotPage[ApprovedSnapshotWorkingCalendarDay]
    absence_types: SnapshotPage[ApprovedSnapshotAbsenceType]
    absence_budgets: SnapshotPage[ApprovedSnapshotAbsenceBudget]
    catalog_rates: SnapshotPage[ApprovedSnapshotCatalogDefaultRate]
    exchange_rates: SnapshotPage[ApprovedSnapshotExchangeRate]


def _page_parameter(value: str, field: str, minimum: int, maximum: int) -> int:
    if (
        not value.isascii()
        or not value.isdecimal()
        or len(value) > len(str(maximum))
    ):
        raise ScenarioHistoryPageInvalid(field)
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ScenarioHistoryPageInvalid(field)
    return parsed


def _snapshot_page(session: Session, model, scenario_id: uuid.UUID, limit: int, offset: int):
    filters = model.scenario_id == scenario_id
    total = select(func.count()).select_from(model).where(filters).scalar_subquery()
    page = (
        select(model)
        .where(filters)
        .order_by(model.id)
        .limit(limit)
        .offset(offset)
        .subquery()
    )
    page_model = aliased(model, page)
    anchor = select(literal(1).label("unit")).subquery()
    statement = select(total.label("total"), page_model).select_from(anchor.outerjoin(page, true()))
    rows = session.execute(statement).all()
    count = rows[0].total
    items = tuple(row[1] for row in rows if row[1] is not None)
    return SnapshotPage(items=items, total=count, limit=limit, offset=offset)


def scenario_history_for_caller(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    limits: dict[str, str] | None = None,
    offsets: dict[str, str] | None = None,
) -> ScenarioHistoryView | None:
    """Read one scenario after the shared project-access query has established scope.

    Independent scenarios remain independent: a draft never inherits an approval event or
    snapshots from a sibling copy. Snapshot tables are queried only for the addressed approved
    scenario, and an out-of-scope, mismatched, or missing address returns the same None.
    """
    scoped = scenario_for_caller(session, caller, project_id, scenario_id)
    if scoped is None:
        return None
    raw_limits = limits or {}
    raw_offsets = offsets or {}
    parsed_limits = {
        name: _page_parameter(
            raw_limits.get(f"{name}_limit", str(DEFAULT_SNAPSHOT_PAGE_LIMITS[name])),
            f"{name}_limit",
            1,
            MAX_SNAPSHOT_PAGE_LIMITS[name],
        )
        for name in SNAPSHOT_COLLECTIONS
    }
    parsed_offsets = {
        name: _page_parameter(
            raw_offsets.get(f"{name}_offset", "0"),
            f"{name}_offset",
            0,
            MAX_SCENARIO_HISTORY_PAGE_OFFSET,
        )
        for name in SNAPSHOT_COLLECTIONS
    }
    project_view, scenario = scoped
    if scenario.status != ScenarioStatus.APPROVED:
        return ScenarioHistoryView(
            project_view=project_view,
            scenario=scenario,
            approval_event=None,
            organization_defaults=None,
            working_calendars=SnapshotPage(
                (), 0, parsed_limits["working_calendars"], parsed_offsets["working_calendars"]
            ),
            working_calendar_days=SnapshotPage(
                (),
                0,
                parsed_limits["working_calendar_days"],
                parsed_offsets["working_calendar_days"],
            ),
            absence_types=SnapshotPage(
                (), 0, parsed_limits["absence_types"], parsed_offsets["absence_types"]
            ),
            absence_budgets=SnapshotPage(
                (), 0, parsed_limits["absence_budgets"], parsed_offsets["absence_budgets"]
            ),
            catalog_rates=SnapshotPage(
                (), 0, parsed_limits["catalog_rates"], parsed_offsets["catalog_rates"]
            ),
            exchange_rates=SnapshotPage(
                (), 0, parsed_limits["exchange_rates"], parsed_offsets["exchange_rates"]
            ),
        )

    event = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.scenario_id == scenario.id,
            AuditLog.project_id == project_id,
            AuditLog.action_type == AuditActionType.SCENARIO_APPROVED,
        )
        .order_by(AuditLog.created_at, AuditLog.id)
    ).first()
    organization_defaults = session.scalars(
        select(ApprovedSnapshotOrganizationDefaults).where(
            ApprovedSnapshotOrganizationDefaults.scenario_id == scenario.id
        )
    ).first()

    return ScenarioHistoryView(
        project_view=project_view,
        scenario=scenario,
        approval_event=event,
        organization_defaults=organization_defaults,
        working_calendars=_snapshot_page(
            session, ApprovedSnapshotWorkingCalendar, scenario.id,
            parsed_limits["working_calendars"],
            parsed_offsets["working_calendars"]
        ),
        working_calendar_days=_snapshot_page(
            session, ApprovedSnapshotWorkingCalendarDay, scenario.id,
            parsed_limits["working_calendar_days"],
            parsed_offsets["working_calendar_days"]
        ),
        absence_types=_snapshot_page(
            session, ApprovedSnapshotAbsenceType, scenario.id,
            parsed_limits["absence_types"],
            parsed_offsets["absence_types"]
        ),
        absence_budgets=_snapshot_page(
            session, ApprovedSnapshotAbsenceBudget, scenario.id,
            parsed_limits["absence_budgets"],
            parsed_offsets["absence_budgets"]
        ),
        catalog_rates=_snapshot_page(
            session, ApprovedSnapshotCatalogDefaultRate, scenario.id,
            parsed_limits["catalog_rates"],
            parsed_offsets["catalog_rates"]
        ),
        exchange_rates=_snapshot_page(
            session, ApprovedSnapshotExchangeRate, scenario.id,
            parsed_limits["exchange_rates"],
            parsed_offsets["exchange_rates"]
        ),
    )
