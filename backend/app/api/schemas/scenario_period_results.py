"""Period results and anonymous staffing timeline (SC-7-11)."""

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.api.schemas.common import DecimalString
from app.api.schemas.project import ScenarioStatusLabel
from app.core.money import NOT_APPLICABLE

PeriodAmount = DecimalString | Literal[NOT_APPLICABLE]
GatedPeriodAmount = PeriodAmount | None
ProfitabilityState = Literal["calculated", "not_applicable", "currency_mismatch"]


class ScenarioPeriodRow(BaseModel):
    period_month: date
    revenue: PeriodAmount
    revenue_currency: str | None
    personnel_cost: GatedPeriodAmount
    personnel_cost_currency: str | None
    additional_cost: PeriodAmount
    additional_cost_currency: str | None
    period_cost: GatedPeriodAmount
    period_cost_currency: str | None
    profit: GatedPeriodAmount
    margin: GatedPeriodAmount
    profitability_state: ProfitabilityState | None
    below_target_margin: bool | None
    negative_profit: bool | None
    planned_fte: DecimalString | Literal[NOT_APPLICABLE]


class ScenarioPeriodUnallocated(BaseModel):
    revenue: PeriodAmount
    revenue_currency: str | None
    fixed_amount_cost: GatedPeriodAmount
    fixed_amount_cost_currency: str | None


class ScenarioPeriodResults(BaseModel):
    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel
    reporting_currency: str | None
    target_margin_percent: DecimalString | None
    periods: list[ScenarioPeriodRow]
    unallocated: ScenarioPeriodUnallocated
