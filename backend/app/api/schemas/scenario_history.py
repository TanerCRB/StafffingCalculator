"""Read model for one scenario's approval history (SC-8-02, ADR-0004/0005/0019/0022)."""

import uuid
from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from app.api.schemas.common import DecimalString
from app.api.schemas.project import ScenarioStatusLabel


class SnapshotPageRead[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ScenarioHistoryInputs(BaseModel):
    """The selected scenario's persisted fields; for Draft these are its current saved inputs."""

    start_date: date | None
    end_date: date | None
    working_calendar: str | None
    full_time_hours_per_week: DecimalString | None
    currency: str | None
    target_margin_percent: DecimalString | None
    overload_threshold_percent: DecimalString | None


class ScenarioApprovalHistoryEvent(BaseModel):
    action_type: Literal["scenario_approved"]
    performed_by: str
    performed_by_verified: Literal[False] = False
    created_at: AwareDatetime


class ApprovedOrganizationDefaultsInput(BaseModel):
    target_margin_percent: DecimalString | None
    overload_threshold_percent: DecimalString | None


class ApprovedWorkingCalendarInput(BaseModel):
    source_calendar_id: uuid.UUID
    source_location_id: uuid.UUID
    name: str
    standard_hours_per_day: DecimalString
    week_pattern: str


class ApprovedWorkingCalendarDayInput(BaseModel):
    source_calendar_id: uuid.UUID
    day: date
    source: str
    name: str | None
    country_code: str | None
    year: int | None
    kind: str


class ApprovedAbsenceTypeInput(BaseModel):
    source_absence_type_id: uuid.UUID
    name: str
    generates_cost: bool
    generates_revenue: bool
    is_statutory_leave: bool


class ApprovedAbsenceBudgetInput(BaseModel):
    source_calendar_id: uuid.UUID
    source_engagement_type_id: uuid.UUID
    budget_days: DecimalString
    unit: str
    effective_from: date
    effective_to: date


class ApprovedCatalogRateRestrictedInput(BaseModel):
    source_rate_id: uuid.UUID
    source_role_id: uuid.UUID
    source_seniority_id: uuid.UUID
    source_location_id: uuid.UUID
    source_engagement_type_id: uuid.UUID
    source_vendor_id: uuid.UUID | None
    default_selling_rate: DecimalString
    currency: str
    unit: str
    effective_from: date
    effective_to: date | None
    surcharge_percent: DecimalString
    includes_surcharge: bool


class ApprovedCatalogRateWithCostInput(ApprovedCatalogRateRestrictedInput):
    default_cost_rate: DecimalString
    cost_rate_unit: str


class ApprovedExchangeRateInput(BaseModel):
    source_rate_id: uuid.UUID
    source_scope: str
    source_project_id: uuid.UUID | None
    source_scenario_id: uuid.UUID | None
    source_currency: str
    target_currency: str
    effective_from: date
    effective_to: date | None
    rate: DecimalString
    source: str


class ApprovedScenarioSnapshotInputs(BaseModel):
    """Key frozen inputs only; source attribution is carried as IDs, never re-read live."""

    organization_defaults: ApprovedOrganizationDefaultsInput | None
    working_calendars: SnapshotPageRead[ApprovedWorkingCalendarInput]
    working_calendar_days: SnapshotPageRead[ApprovedWorkingCalendarDayInput]
    absence_types: SnapshotPageRead[ApprovedAbsenceTypeInput]
    absence_budgets: SnapshotPageRead[ApprovedAbsenceBudgetInput]
    catalog_rates: SnapshotPageRead[
        ApprovedCatalogRateRestrictedInput | ApprovedCatalogRateWithCostInput
    ]
    exchange_rates: SnapshotPageRead[ApprovedExchangeRateInput]


class ScenarioHistoryRead(BaseModel):
    scenario_id: uuid.UUID
    name: str
    status: ScenarioStatusLabel
    updated_at: AwareDatetime
    inputs: ScenarioHistoryInputs
    approval_event: ScenarioApprovalHistoryEvent | None
    approved_snapshot: ApprovedScenarioSnapshotInputs | None
