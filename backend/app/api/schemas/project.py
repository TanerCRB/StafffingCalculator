"""Request and response schemas for the project endpoints.

The schema is the contract: a project outside the caller's scope has no representation here at
all — there is no "unavailable" variant, no tombstone, no placeholder row. Absence is the only
way an inaccessible project can appear (F-13, ADR-0005).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
    model_validator,
)

from app.api.schemas.common import DecimalString, Iso4217Code, NonEmptyName

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

    updated_at: datetime
    """The concurrency token of ADR-0007, and deliberately the row's own `updated_at` rather than
    a new `version` column: the client holds it between the read and the next write and sends it
    back in `PATCH /projects/{id}`, where a mismatch is a 409 instead of a silent overwrite
    (NF-05). Timezone-aware — a point in time, not a calendar date.

    On the detail read only. The list row does not carry it, because a token is useful exactly to
    whoever is about to edit *this* project, and the edit screen reads the project by id first.
    """


class ProjectListResponse(BaseModel):
    """An object, not a bare array: the list gains filtering/pagination metadata in a later
    Story (explicitly out of scope here) without breaking the contract."""

    projects: list[ProjectListItem]


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


DELIVERY_PERIOD_COLUMNS = ("delivery_period_start", "delivery_period_end")
"""The one request field that is two columns. Named here so the edit request and the data layer
agree on the spelling; every other editable field maps to a column of its own name."""


class ProjectEditRequest(BaseModel):
    """The body of PATCH /projects/{id} — the fields to change, plus the concurrency token.

    Partial by design: "which fields did the caller send" is answered by `model_fields_set`, not
    by "which fields are not null". A field absent from the body is left alone; a field present
    with `null` is a 422 (see the validator below), never a way to blank out a `NOT NULL` column.

    `extra="forbid"`, for the same reason as on `ProjectCreateRequest` and one more: `status` and
    `id` are not editable fields, and an ignored unknown key would make
    `{"status": "archived"}` look like an archive action that quietly did nothing (SC-1-04 is a
    separate task with its own permission).
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    """The value returned by the read this edit is based on (ADR-0007). Required — an edit without
    a token is not "an edit that skips the check", it is a malformed request. Must carry an
    offset: a naive timestamp compared against a `timestamptz` column is a guess about which
    clock the client meant."""

    name: NonEmptyName | None = None
    client: NonEmptyName | None = None
    owner: NonEmptyName | None = None
    description: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    reporting_currency: Iso4217Code | None = None
    delivery_period: DeliveryPeriod | None = None

    @model_validator(mode="after")
    def _at_least_one_field_and_none_of_them_null(self) -> Self:
        """Two refusals that would otherwise both end as a 500 or a silent no-op.

        An explicit `null` for any editable field would reach the `UPDATE` as `NULL` and violate
        the column's `NOT NULL` — a 500 for what is a client mistake. A body carrying only the
        token asks for no change at all; accepted, it would bump `updated_at` and thereby
        invalidate every other client's token for nothing.
        """
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        if not changed:
            raise ValueError("An edit must name at least one field to change.")
        nulled = sorted(field for field in changed if getattr(self, field) is None)
        if nulled:
            raise ValueError(f"These fields cannot be set to null: {', '.join(nulled)}")
        return self

    @model_validator(mode="after")
    def _delivery_period_is_ordered(self) -> Self:
        """Same boundary check as on create — the database's `delivery_period_ordered` constraint
        stays the guarantee, this is only the error message."""
        if self.delivery_period is not None and (
            self.delivery_period.end < self.delivery_period.start
        ):
            raise ValueError("delivery_period.end must not be earlier than delivery_period.start")
        return self

    def changes(self) -> dict[str, Any]:
        """The requested changes as column names → values, in declaration order.

        Declaration order rather than set iteration order so that the generated `UPDATE` (and any
        message naming the fields) is the same for the same request every time.
        """
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        changes: dict[str, Any] = {}
        for field in type(self).model_fields:
            if field not in changed:
                continue
            value = getattr(self, field)
            if field == "delivery_period":
                start_column, end_column = DELIVERY_PERIOD_COLUMNS
                changes[start_column] = value.start
                changes[end_column] = value.end
            else:
                changes[field] = value
        return changes
