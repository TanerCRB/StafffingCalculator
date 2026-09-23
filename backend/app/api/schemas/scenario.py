"""Response schema for the scenario approval endpoint (ADR-0004, SC-3-02).

One shape, and what it deliberately does not contain says more than what it does.

**No snapshot rows, only counts.** Nothing in this repository *reads* the approval snapshot yet
(`app.data.scenario_approval` says so in its own docstring), and a payload carrying the frozen rows
would advertise a read path that does not exist — and would be the first place a snapshot column
could reach a client without anyone deciding it should. The counts are what a caller can act on:
"this calculation was frozen against two calendars, eleven exceptional days and three absence
types" is a sentence a person can check against what they expected.

**No author and no timestamp of the approval.** `audit_log` is deferred to plan block 8 (ADR-0004,
addendum 2026-09-18), so there is nothing truthful to put in such a field, and a field filled from
`created_at` of a snapshot row would look like an audit trail while being a coincidence.
"""

import uuid

from pydantic import BaseModel

from app.api.schemas.project import ScenarioStatusLabel


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


class ScenarioApproval(BaseModel):
    """The result of approving one scenario: its new status and what was frozen with it."""

    id: uuid.UUID
    status: ScenarioStatusLabel
    """`"Approved"` — the same label vocabulary the project payloads use
    (`app.api.response_shaping._SCENARIO_STATUS_LABELS`), not the raw enum value, so a client has
    one spelling of this status and not two."""

    snapshot: ApprovedSnapshotCounts
