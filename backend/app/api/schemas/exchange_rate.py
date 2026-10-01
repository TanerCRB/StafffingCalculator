"""Exchange-rate API boundary (F-02, ADR-0006)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.schemas.common import DecimalString, Iso4217Code

ExchangeRateValue = Annotated[DecimalString, Field(gt=0, max_digits=20, decimal_places=10)]
ExchangeRateSource = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class ExchangeRateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_currency: Iso4217Code
    target_currency: Iso4217Code
    effective_from: date
    effective_to: date | None = None
    rate: ExchangeRateValue
    source: ExchangeRateSource
    project_id: uuid.UUID | None = None
    scenario_id: uuid.UUID | None = None


class ExchangeRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    scenario_id: uuid.UUID | None
    source_currency: str
    target_currency: str
    effective_from: date
    effective_to: date | None
    rate: Decimal
    source: str
    updated_at: datetime
