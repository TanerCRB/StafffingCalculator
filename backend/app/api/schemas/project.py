"""Response schemas for the project list (GET /projects).

The schema is the contract: a project outside the caller's scope has no representation here at
all — there is no "unavailable" variant, no tombstone, no placeholder row. Absence is the only
way an inaccessible project can appear (F-13, ADR-0005).
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, PlainSerializer

DecimalString = Annotated[Decimal, PlainSerializer(lambda value: format(value, "f"), str)]
"""Decimals cross the API boundary as fixed-point strings, never as JSON floats — a float would
lose exactly the precision NF-01/ADR-0002 require the storage layer to keep."""

ProjectStatusLabel = Literal["Active", "Archived"]
ScenarioStatusLabel = Literal["Draft", "Approved"]


class DeliveryPeriod(BaseModel):
    start: date
    end: date


class ScenarioListItem(BaseModel):
    id: uuid.UUID
    name: str
    status: ScenarioStatusLabel
    missing_inputs: list[str]
    """The inputs *this* scenario has not got yet — computed per row, empty when nothing is
    missing."""
    ready_for_approval: bool
    """False whenever an input is missing: an incomplete calculation is never presented as ready
    (F-01)."""
    target_margin_percent: DecimalString | None = None


class ProjectListItem(BaseModel):
    id: uuid.UUID
    name: str
    client: str
    # `Project.owner` is stored but deliberately NOT serialized: no screen renders it (SC-1-06
    # shows name, client, delivery period, status), and an owner's name is personal data
    # (NF-11). It enters the payload the day a screen actually needs it.
    delivery_period: DeliveryPeriod
    reporting_currency: str
    description: str
    status: ProjectStatusLabel
    scenarios: list[ScenarioListItem]


class ProjectListResponse(BaseModel):
    """An object, not a bare array: the list gains filtering/pagination metadata in a later
    Story (explicitly out of scope here) without breaking the contract."""

    projects: list[ProjectListItem]
