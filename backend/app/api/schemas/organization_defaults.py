"""API schemas for the singleton organization-default assumptions."""

from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.common import DecimalString
from app.models.organization_defaults import PERCENT_PRECISION, PERCENT_SCALE

TargetMargin = Annotated[
    DecimalString,
    Field(max_digits=PERCENT_PRECISION, decimal_places=PERCENT_SCALE),
]
OverloadThreshold = Annotated[
    DecimalString,
    Field(gt=0, max_digits=PERCENT_PRECISION, decimal_places=PERCENT_SCALE),
]


class OrganizationDefaultsRead(BaseModel):
    target_margin_percent: DecimalString | None
    overload_threshold_percent: DecimalString | None
    """Null means no organization default is configured for that assumption."""

    updated_at: AwareDatetime | None
    """Null denotes that the singleton row does not exist yet (create-if-absent token)."""


class OrganizationDefaultsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime | None
    target_margin_percent: TargetMargin | None = None
    overload_threshold_percent: OverloadThreshold | None = None

    @model_validator(mode="after")
    def _at_least_one_value(self) -> "OrganizationDefaultsPatch":
        if not ({"target_margin_percent", "overload_threshold_percent"} & self.model_fields_set):
            raise ValueError("At least one organization default must be supplied")
        return self

    def changes(self) -> dict[str, Decimal | None]:
        return {
            name: getattr(self, name)
            for name in ("target_margin_percent", "overload_threshold_percent")
            if name in self.model_fields_set
        }
