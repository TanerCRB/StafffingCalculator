"""Request and response schemas for the project endpoints.

The schema is the contract: a project outside the caller's scope has no representation here at
all — there is no "unavailable" variant, no tombstone, no placeholder row. Absence is the only
way an inaccessible project can appear (F-13, ADR-0005).
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, PlainSerializer, StringConstraints, model_validator

DecimalString = Annotated[Decimal, PlainSerializer(lambda value: format(value, "f"), str)]
"""Decimals cross the API boundary as fixed-point strings, never as JSON floats — a float would
lose exactly the precision NF-01/ADR-0002 require the storage layer to keep."""

ProjectStatusLabel = Literal["Active", "Archived"]
ScenarioStatusLabel = Literal["Draft", "Approved"]


class DeliveryPeriod(BaseModel):
    """Also a *request* model, nested inside `ProjectCreateRequest` — hence `extra="forbid"`
    here too. Pydantic applies that setting per model, never down a tree: declared only on the
    outer model, `{"start": …, "end": …, "user_id": "someone"}` is accepted and the smuggled key
    is silently dropped — precisely what the outer `extra="forbid"` exists to prevent. Every
    nested request model has to carry it, or it reopens the hole one level down."""

    model_config = ConfigDict(extra="forbid")

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


class ProjectDetail(ProjectListItem):
    """One project, read by id (GET /projects/{id}, POST /projects).

    Everything the list row carries, plus `owner`. The owner is a person's name — personal data
    (NF-11) — and F-01 names it as a field of the project, which SC-1-01's first acceptance
    criterion requires to be retrievable. It is served here and nowhere else: the list keeps
    omitting it (no screen renders it there), and a caller reaches this schema only after the
    `project_access` filter in `app.data.project_reads` has already returned the row, so the
    field never leaves the scope ADR-0005 defines. Nothing logs it.
    """

    owner: str


class ProjectListResponse(BaseModel):
    """An object, not a bare array: the list gains filtering/pagination metadata in a later
    Story (explicitly out of scope here) without breaking the contract."""

    projects: list[ProjectListItem]


NonEmptyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Iso4217Code = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
"""ISO-4217 alphabetic code, uppercase, validated by shape and not against a closed list:
ADR-0006 keeps currencies an open list (a string, not a database enum) so that adding one needs
no migration. Rejected rather than silently upper-cased — a request that means `eur` is a client
bug worth surfacing, and normalizing input on the way in is how two spellings of one currency
end up in the same column."""


class ProjectCreateRequest(BaseModel):
    """The body of POST /projects — exactly F-01's project fields, and nothing else.

    `extra="forbid"`: an unknown key is a 422, not an ignored field. This is what stops a client
    from smuggling an identity or an access grant into the payload (`user_id`, `owner_user_id`,
    `project_access`) and having it quietly dropped — the creator is taken from the request's
    auth context, and a body that tries to say otherwise fails loudly.
    """

    model_config = ConfigDict(extra="forbid")

    name: NonEmptyName
    client: NonEmptyName
    owner: NonEmptyName
    delivery_period: DeliveryPeriod
    reporting_currency: Iso4217Code
    description: Annotated[str, StringConstraints(max_length=10_000)] = ""

    @model_validator(mode="after")
    def _delivery_period_is_ordered(self) -> Self:
        """Reject an inverted period at the boundary so the request gets a 422 instead of the
        500 a violated `delivery_period_ordered` check constraint would produce. The constraint
        in the database stays the actual guarantee — this is the error message, not the rule."""
        if self.delivery_period.end < self.delivery_period.start:
            raise ValueError("delivery_period.end must not be earlier than delivery_period.start")
        return self
