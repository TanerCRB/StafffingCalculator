"""Response schema for the scenario approval endpoint (ADR-0004, SC-3-02).

One shape, and what it deliberately does not contain says more than what it does.

**No snapshot rows, only counts.** Nothing in this repository *reads* the approval snapshot yet
(`app.data.scenario_approval` says so in its own docstring), and a payload carrying the frozen rows
would advertise a read path that does not exist — and would be the first place a snapshot column
could reach a client without anyone deciding it should. The counts are what a caller can act on:
"this calculation was frozen against two calendars, eleven exceptional days and three absence
types" is a sentence a person can check against what they expected.

**No author and no timestamp of the approval, still.** Since SC-8-01 (Issue #14, ADR-0004 addendum
2026-09-27) an `audit_log` row exists for every approval — but no endpoint reads it back (out of
scope for that task, F-13 access control to the resource being a separate, future concern), so this
payload has nothing truthful to put in such a field yet. A field filled from `created_at` of a
snapshot row would still look like an audit trail while being a coincidence.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.common import DecimalString
from app.api.schemas.project import ScenarioStatusLabel
from app.core.money import NOT_APPLICABLE
from app.models.organization_defaults import PERCENT_PRECISION, PERCENT_SCALE

NotApplicableValue = Literal[NOT_APPLICABLE]


class ApprovedSnapshotCounts(BaseModel):
    """How many rows the approval froze, per snapshot table.

    One field per table rather than a single total: the tables are frozen by four separate
    statements, and a single number would report "something was written" where the interesting
    failure is "one of the four wrote nothing".
    """

    working_calendars: int
    working_calendar_days: int
    absence_types: int
    absence_budgets: int
    """The fourth table, from SC-3-03 (ADR-0004, addendum 2026-09-22 SC-3-03).

    Adding a field to this payload is a **deliberate** change to a canary: every test asserting
    these counts by equality fails on the day a snapshot table joins, which is the only moment at
    which noticing is cheap. A zero here is two different facts — "no budget covers the pairs this
    scenario reads", which is legal and named, and "budgets existed and were not copied", which is
    the regression point 2 of that addendum is about — so it is read against a contrast and never on
    its own (point 6)."""

    organization_defaults: int
    """The fifth table, from SC-1-10 (ADR-0012, point 6): `1` when the organisation had a defaults
    row at the moment of approval and it is now frozen, `0` when it had none — in which case the
    approved scenario keeps "no organisation default" for ever, whatever is configured later
    (criterion K-06). The same deliberate canary growth as `absence_budgets`."""

    catalog_default_rates: int
    """The sixth table, from SC-4-01 (ADR-0004, addendum 2026-09-23 SC-4-01, point 2): how many
    catalogue rate windows the scenario's T&M revenue reads and the approval froze — only those,
    never the catalogue (criterion K-08). A count, never the rows: the frozen rows carry
    `default_cost_rate` (point 2b), and no path in SC-4-01 returns them (ADR-0005, addendum SC-4-01,
    point 7). The same deliberate canary growth as the fourth and fifth."""

    exchange_rates: int
    """Rate windows copied with the approved scenario so later defaults cannot move its result."""


class ScenarioApproval(BaseModel):
    """The result of approving one scenario: its new status and what was frozen with it."""

    id: uuid.UUID
    status: ScenarioStatusLabel
    """`"Approved"` — the same label vocabulary the project payloads use
    (`app.api.response_shaping._SCENARIO_STATUS_LABELS`), not the raw enum value, so a client has
    one spelling of this status and not two."""

    snapshot: ApprovedSnapshotCounts


class ResolvedAssumptionRead(BaseModel):
    """One assumption of one scenario: value, state, and the level the value came from.

    `value` is a fixed-point string when `state` is `"resolved"` and `"n/a"` when it is
    `"no_value"` — never `0`, never `null` (ADR-0012, point 1; the `no_calendar` pattern of
    SC-3-02). `source` names the level that supplied the value and is `null` exactly when there is
    none.
    """

    value: DecimalString | NotApplicableValue
    state: Literal["resolved", "no_value"]
    source: Literal["scenario", "project", "organization"] | None


class ScenarioAssumptions(BaseModel):
    """`GET …/scenarios/{id}/assumptions` — the first reader of an approval snapshot (SC-1-10).

    One field per assumption rather than a list or a map keyed by name: the set is fixed by
    `app.domain.assumptions.RESOLVABLE_ASSUMPTIONS`, and a closed shape makes an assumption that
    silently dropped out of the payload a validation error instead of a missing key.
    """

    id: uuid.UUID
    status: ScenarioStatusLabel
    updated_at: AwareDatetime
    target_margin_percent: ResolvedAssumptionRead
    overload_threshold_percent: ResolvedAssumptionRead


class ScenarioAssumptionResetPreview(BaseModel):
    """The values and sources inherited after clearing both draft scenario overrides."""

    id: uuid.UUID
    status: Literal["Draft"]
    target_margin_percent: ResolvedAssumptionRead
    overload_threshold_percent: ResolvedAssumptionRead


ScenarioTargetMargin = Annotated[
    DecimalString, Field(max_digits=PERCENT_PRECISION, decimal_places=PERCENT_SCALE)
]
ScenarioOverloadThreshold = Annotated[
    DecimalString,
    Field(gt=0, max_digits=PERCENT_PRECISION, decimal_places=PERCENT_SCALE),
]


class ScenarioAssumptionsPatch(BaseModel):
    """Partial edit of the two scenario-level assumption overrides.

    Omitted fields remain unchanged; explicit null removes only that scenario override so the
    existing resolver can inherit from project then organization. The opaque scenario timestamp
    is required for every edit (ADR-0007/ADR-0009).
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    target_margin_percent: ScenarioTargetMargin | None = None
    overload_threshold_percent: ScenarioOverloadThreshold | None = None

    @model_validator(mode="after")
    def _at_least_one_override(self) -> "ScenarioAssumptionsPatch":
        if not ({"target_margin_percent", "overload_threshold_percent"} & self.model_fields_set):
            raise ValueError("At least one scenario assumption must be supplied")
        return self

    def changes(self) -> dict[str, Decimal | None]:
        return {
            name: getattr(self, name)
            for name in ("target_margin_percent", "overload_threshold_percent")
            if name in self.model_fields_set
        }


class ScenarioAssumptionOverridesRead(BaseModel):
    """Persisted scenario overrides and the authoritative concurrency marker after PATCH."""

    id: uuid.UUID
    status: ScenarioStatusLabel
    target_margin_percent: DecimalString | None
    overload_threshold_percent: DecimalString | None
    updated_at: AwareDatetime
