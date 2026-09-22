"""The approval snapshot: the organisational values an approved scenario keeps for ever (ADR-0004).

**The first `approved_snapshot_*` tables in this repository**, and the shape established here is the
one exchange rates (ADR-0006), resolved rates and commercial-rule versions (ADR-0003) inherit — not
a detail of SC-3-02 (ADR-0004, addendum 2026-09-22, point 3).

Four properties, each of them a decision with a mutation attached:

1. **One snapshot table per source table**, named `approved_snapshot_<table>` — not one generic
   table with a `jsonb` column. A number in JSON is a floating-point type, and these rows carry
   hours, i.e. values one multiplication away from money (ADR-0002, NF-01). Spelled out because the
   generic blob is the shortcut the next implementer reaches for.
2. **Keyed by `scenario_id`; the id of the source row is stored as a plain `uuid` value, never as a
   foreign key.** "A snapshot is a separate set of rows, not a reference to the organisation's
   current values" (ADR-0004, "Decyzja"). A foreign key *is* a reference: it would let the source
   block a delete or cascade a change into a frozen copy. `tests/test_scenario_approval_snapshot.py`
   asserts that by introspecting `pg_constraint`, because the mutation "store the id as an FK and
   read through a join" is invisible to any behavioural test that never changes the source.
3. **Values, not names to resolve later**: the calendar's name, its standard working day, its week
   pattern, every one of its exceptional days, and the name and both flags of every absence type.
   Editing the source calendar after approval must not move a single figure here (AC-04, AC-10 —
   criterion K-16).
4. **Write-once, and therefore no concurrency marker** (ADR-0007, addendum 2026-09-22, point 5).
   These rows are written inside the one transaction that approves a scenario and are never edited,
   so there are no two editors for a marker to arbitrate between.

**A third group of scenario children, which the two-group taxonomy did not have** (ADR-0004,
addendum 2026-09-22, point 2). These tables are children of `scenarios` and belong neither to group
1 (inherited → snapshot) nor to group 2 (own → write guard): they are written once, at approval, and
**never copied**. Their absence from `app.data.project_writes.SCENARIO_CHILD_COPIERS` is *required*
rather than merely permitted — a copy of an approved scenario is a `draft` that has not been through
an approval, so it must hold zero snapshot rows (criterion K-17's canary).

**What is deliberately not here: the scenario's own absence instances.** Nothing outside the
scenario can change them, so there is nothing to freeze; they are protected by the refusal of a
write instead (ADR-0004, addendum 2026-09-22, point 1). Adding a snapshot table for them is the
mutation criterion K-17 kills.

**Scope.** These rows inherit the scenario's scope and have no read path of their own (ADR-0005,
addendum 2026-09-22, point 8): the content comes from tables that carry no scope, and the origin of
the content does not carry the exemption with it.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.catalog import (
    STANDARD_HOURS_PRECISION,
    STANDARD_HOURS_SCALE,
    WEEK_PATTERN_LENGTH,
    WorkingCalendarDayKind,
)

SNAPSHOT_TABLES: tuple[str, ...] = (
    "approved_snapshot_working_calendar",
    "approved_snapshot_working_calendar_day",
    "approved_snapshot_absence_type",
)
"""Every snapshot table SC-3-02 creates, as data.

Read by the schema tests (which ask the migrated database about the foreign keys of each) and by
the canary that counts the rows a copied scenario holds. A table added here without an entry in the
approval transaction would be a snapshot that is always empty — which is the visible failure mode
ADR-0004 prefers to a guard nobody notices is missing."""


class _ApprovedSnapshotRow(Base):
    """What every snapshot row carries: its own id and the scenario whose approval wrote it.

    Abstract, so the three tables below cannot disagree about the two columns that make them
    snapshots. `created_at` is here and `updated_at` deliberately is not — see the module docstring.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    """When the approval happened — the database's clock, never this process's. Not a concurrency
    marker and not to be grown into one: there is no second writer of this row."""


class ApprovedSnapshotWorkingCalendar(_ApprovedSnapshotRow):
    """The working calendar of one location, frozen as values at the moment of approval."""

    __tablename__ = "approved_snapshot_working_calendar"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_approved_snapshot_working_calendar_scenario_id"),
        nullable=False,
        index=True,
    )
    """The one foreign key a snapshot row is allowed to have: to the scenario that owns it.

    It is what makes the row part of the scenario — and therefore of a project, and therefore inside
    the caller's scope (ADR-0005, addendum 2026-09-22, point 8). No `ondelete`, like every other
    foreign key here: nothing may make an approved scenario's snapshot disappear quietly."""

    source_calendar_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    """Which calendar this was copied from — **a value, not a foreign key** (ADR-0004, addendum
    2026-09-22, point 3b). It exists so a reader can say *which* calendar the frozen figures came
    from; it is never joined to resolve a value, and it must survive the source row being renamed,
    repointed or (one day) deleted."""

    source_location_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    """Which location pointed at that calendar at approval time — also a value, also never joined.
    Without it, a scenario whose positions sit in two locations with two calendars would hold two
    snapshot rows and no way to say which position reads which."""

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    standard_hours_per_day: Mapped[Decimal] = mapped_column(
        Numeric(STANDARD_HOURS_PRECISION, STANDARD_HOURS_SCALE), nullable=False
    )
    week_pattern: Mapped[str] = mapped_column(String(WEEK_PATTERN_LENGTH), nullable=False)
    """The three values the capacity calculation actually reads, copied rather than referenced.

    The column types are the source's own, so no value is narrowed on the way in: a snapshot stored
    at a smaller scale than the column it copies is a snapshot that already disagrees with what was
    approved. **No CHECK constraints are repeated here** — the source table refuses a zero-hour day
    and a malformed pattern, and a snapshot's job is to record what was approved, not to re-judge
    it."""


class ApprovedSnapshotWorkingCalendarDay(_ApprovedSnapshotRow):
    """One exceptional day of a snapshotted calendar — the complete set, as values."""

    __tablename__ = "approved_snapshot_working_calendar_day"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_approved_snapshot_working_calendar_day_scenario_id"),
        nullable=False,
        index=True,
    )
    source_calendar_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    """Which calendar's day this is — a value, not a foreign key, and deliberately not a foreign key
    to `approved_snapshot_working_calendar` either: the two tables are joined by
    `(scenario_id, source_calendar_id)`, which is a pair of values a later reader can reconstruct
    without either table having authority over the other's lifetime."""

    day: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[WorkingCalendarDayKind] = mapped_column(
        Enum(
            WorkingCalendarDayKind,
            name="working_calendar_day_kind",
            values_callable=lambda enum: [member.value for member in enum],
            create_type=False,
        ),
        nullable=False,
    )
    """The same enum type as the source column, not a second one: `create_type=False` reuses the
    type the source table's migration created. A second type with the same values would be two
    vocabularies that can drift a value apart.

    `values_callable` is the same as on the source column, and it is load-bearing rather than
    stylistic: without it SQLAlchemy persists and reads the member *names* (`NON_WORKING`) while the
    database type holds the member *values* (`non_working`). The snapshot is written by an
    `INSERT ... SELECT` that copies the source column verbatim, so the rows would be correct in the
    database and unreadable through the ORM — a drift between two spellings of one value, which is
    exactly what sharing the type is supposed to prevent.

    **No `UNIQUE (scenario_id, source_calendar_id, day)` here**, although the source has one. The
    snapshot records what was approved; if the source ever held two rows for one day, the honest
    snapshot holds two as well. A constraint here would make the approval of a defective calendar
    fail with a message about the snapshot rather than about the calendar."""


class ApprovedSnapshotAbsenceType(_ApprovedSnapshotRow):
    """One absence type — name and both flags — frozen at approval (ADR-0004, addendum, point 1).

    The **dictionary entry** is snapshotted; the scenario's absence *instances* are not (see the
    module docstring). The flags are organisational configuration that can be edited after the
    approval, which is exactly the class of value AC-04/AC-10 require to be frozen.
    """

    __tablename__ = "approved_snapshot_absence_type"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_approved_snapshot_absence_type_scenario_id"),
        nullable=False,
        index=True,
    )
    source_absence_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    """A value, not a foreign key — as everywhere in this module."""

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    generates_cost: Mapped[bool] = mapped_column(Boolean, nullable=False)
    generates_revenue: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """Both flags, independently, exactly as the source carries them (criterion K-11's shape one
    table over). No default on either: a snapshot column with a default is a column that can be
    written without a value having been read from the source."""
