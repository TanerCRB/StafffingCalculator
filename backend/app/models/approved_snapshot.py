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
   pattern, every one of its exceptional days, the name and **three** flags of every absence type
   (SC-3-03 replaces "both flags" here — ADR-0004, addendum 2026-09-22 SC-3-03, point 8), and the
   raw leave budget of every (calendar, engagement type) pair the scenario reads, (SC-1-10) the
   organisation's raw default assumptions, and (SC-4-01) every catalogue rate window the scenario's
   months read. Editing the source calendar, type, budget, default or rate after approval must not
   move a single figure here (AC-04, AC-10 — criteria K-16 and K-07 of SC-3-02/03, K-05 and K-06 of
   SC-1-10, K-09 of SC-4-01).
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
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import DATERANGE, Range
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.catalog import (
    BUDGET_PRECISION,
    BUDGET_SCALE,
    RATE_PRECISION,
    RATE_SCALE,
    STANDARD_HOURS_PRECISION,
    STANDARD_HOURS_SCALE,
    VALID_PERIOD_EXPRESSION,
    WEEK_PATTERN_LENGTH,
    WorkingCalendarDayKind,
)
from app.models.organization_defaults import PERCENT_PRECISION, PERCENT_SCALE

SNAPSHOT_TABLES: tuple[str, ...] = (
    "approved_snapshot_working_calendar",
    "approved_snapshot_working_calendar_day",
    "approved_snapshot_absence_type",
    "approved_snapshot_absence_budget",
    "approved_snapshot_organization_defaults",
    "approved_snapshot_catalog_default_rate",
)
"""Every snapshot table, as data — three from SC-3-02, the fourth from SC-3-03, the fifth from
SC-1-10 (the organisation's default assumptions, ADR-0012, point 6) and the sixth from SC-4-01 (the
catalogue rate windows a T&M revenue reads — ADR-0004, addendum 2026-09-23 SC-4-01, point 2).

Growing this tuple is a **deliberate** act and the canaries that compare against it are meant to
fail on the day it changes (ADR-0004, addendum 2026-09-22 SC-3-03, point 4): a table added to the
registry without an entry in the approval transaction is a snapshot that is always empty, and a
table added to the approval without a canary looks covered while it is not.

Read by the schema tests (which ask the migrated database about the foreign keys of each) and by
the canary that counts the rows a copied scenario holds."""


class _ApprovedSnapshotRow(Base):
    """What every snapshot row carries: its own id and the scenario whose approval wrote it.

    Abstract, so the four tables below cannot disagree about the two columns that make them
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

    **The contract a reader of this table relies on** (S-02, invariant-guardian, SC-3-03; amended by
    ADR-0004, aneks 2026-09-23 SC-5-06, point 5): for one scenario, *a row with
    `is_statutory_leave = true` is present* **if and only if** *a type was named when the scenario
    was approved and the scenario has an allocation row in a location with a calendar* —
    independently of whether any budget was frozen and of whether the scenario booked the type.
    Present → a type was named: any frozen budget (`approved_snapshot_absence_budget`) is deducted
    against it, the scenario's own bookings of that type count against it, and the paid-absence
    cost reads its frozen `generates_cost` in every month with a calendar. Absent while such an
    allocation exists → nobody had named one, and the frozen budget applies to nothing (the frozen
    counterpart of the live `NO_STATUTORY_LEAVE_TYPE` state). The absence of the row is therefore
    data, not a gap — without the unconditional copy, "deduct the whole entitlement" and "deduct
    none of it" would freeze identical rows, and so would "the named type costs nothing" and "no
    type was named".

    The SC-3-03 wording ("present ⇔ the frozen budgets apply") was right while the capacity was the
    only reader; it is superseded, not extended. Scenarios approved before the aneks keep the rows
    they got — a named, non-costing type without a frozen budget is absent from their snapshot for
    ever (no `UPDATE` path), named in the aneks rather than repaired. The writer that keeps this
    contract is `app.data.scenario_approval._copy_absence_types`, which carries the full reasoning.
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
    is_statutory_leave: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """The name and **three** flags, independently, exactly as the source carries them.

    The third flag joins in SC-3-03 (ADR-0004, addendum 2026-09-22 SC-3-03, point 8), and that
    addendum replaces — rather than deletes — the phrase "the name and both flags" in this module's
    docstring and in the SC-3-02 addendum. Without it the approval would be formally complete and
    still wrong: moving `is_statutory_leave` to another absence type after an approval would change
    what the `max` rule of ADR-0008 (addendum SC-3-03, point 9) compares the frozen budget against,
    i.e. it would move the capacity of an approved calculation. The mutation the criterion names is
    exactly "copy the name and two flags, and leave the third".

    No default on any of them: a snapshot column with a default is a column that can be written
    without a value having been read from the source."""


class ApprovedSnapshotAbsenceBudget(_ApprovedSnapshotRow):
    """The leave budget of one (calendar, engagement type) pair, frozen **raw** at approval (F-05).

    The fourth snapshot table (ADR-0004, addendum 2026-09-22 SC-3-03). Three properties of it are
    decisions with mutations attached, and the third is the one that is easy to get wrong:

    1. **It is written by the same approval that freezes the calendars, not by a separate step**
       (point 2). A snapshot holding a calendar and not the budget the calendar's positions read
       would reproduce a *different* billable capacity than the one approved: the working days would
       come from the frozen calendar and the leave days subtracted from them from the live table. No
       code mutation is needed for that failure — it is what happens if this table is simply not
       added.
    2. **One row per (pair, window the plan touches)**, resolved by `valid_period @> <month of the
       plan>` — the same containment the live read uses (ADR-0004, addendum 2026-09-22 SC-3-03,
       point 7, and its history). The earlier wording of that point — "one row per pair, resolved on
       the day of the approval" — froze the *wrong* window for any scenario not planned for the
       current one: a 2027 plan approved in 2026 kept the 2026 entitlement for ever, since nothing
       updates a snapshot row. What is frozen now is still narrower than the "every window in the
       snapshot" variant the addendum rejects: only the windows the plan actually reads. The price
       it names (point 7c) is that the reader resolves per month, and the first reader has to prove
       that resolution with a criterion of its own.
    3. **What is frozen is the raw budget — days, window, source, unit — and never the monthly
       share.** The proration (ADR-0008, addendum SC-3-03, point 10) is the reader's formula,
       not a state: a column holding "hours removed per month" would be a computed figure inside a
       record of what was approved, and it would silently be wrong the moment the reader's month
       grid differed from the one that produced it. Criterion K-07 asserts that over the *set of
       columns*, not over behaviour, because a computed column is invisible to any test that only
       reads values back.
    """

    __tablename__ = "approved_snapshot_absence_budget"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_approved_snapshot_absence_budget_scenario_id"),
        nullable=False,
        index=True,
    )
    """The only foreign key a snapshot row is allowed to have — see
    `ApprovedSnapshotWorkingCalendar.scenario_id`."""

    source_budget_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_calendar_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_engagement_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    """Values, never foreign keys (ADR-0004, addendum 2026-09-22 SC-3-02, point 3b).

    The key a later reader looks a row up by is `(scenario_id, source_calendar_id,
    source_engagement_type_id, effective_from)` — the pair **plus the window**, because a scenario
    whose months span two budget windows freezes both of them (reviewer R-03). Each of those occurs
    exactly once per approval, and the `EXCLUDE` on the source table is what guarantees the windows
    of one pair cannot overlap, so resolving a month against them has exactly one answer here
    too."""

    budget_days: Mapped[Decimal] = mapped_column(
        Numeric(BUDGET_PRECISION, BUDGET_SCALE), nullable=False
    )
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(500), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date] = mapped_column(Date, nullable=False)
    """The raw budget: how many days, in what unit, over which window, on whose authority.

    The column types are the source's own, so nothing is narrowed on the way in. `effective_to` is
    `NOT NULL` here without needing a CHECK, because the source table refuses an open-ended window
    in the first place (ADR-0008, addendum SC-3-03, point 10b) — a snapshot records what was
    approved and does not re-judge it.

    **`source` is free text copied verbatim, and there is no `UPDATE` or `DELETE` path to it.** That
    is what made the erasure/rectification question a precondition of the write path rather than a
    later discovery; ADR-0005's addendum (2026-09-22 SC-3-03, point 6) answers it as a **named risk
    carried**, not as a mechanism built — the same answer `ApprovedSnapshotAbsenceType.name` already
    has, and for the same reason. Whatever mechanism eventually erases or rectifies a snapshot row
    has to cover both of those columns at once rather than whichever one a later task happens to
    trip over. See `app.api.catalog.create_absence_budget`."""

    # **There is no `resolved_on`, and its absence is a decision** (reviewer R-03/R-06, 2026-09-22).
    # ADR-0004's addendum (SC-3-03, point 7a) required the day the window was resolved on to be
    # stored, and given point 7 it was right to: one window per pair, chosen by the approval's own
    # date, is a frozen number that cannot be attributed to a window unless the row says which day
    # picked it. The correction to point 7 removes the premise — the windows are resolved by the
    # months the scenario plans, every window the plan touches is frozen, and each row carries its
    # own `effective_from`/`effective_to`. The attribution is in the data; a date column would be a
    # fact about a clock that no reader of this table needs, and dropping it takes the last clock
    # read out of the approval path.


class ApprovedSnapshotOrganizationDefaults(_ApprovedSnapshotRow):
    """The organisation's default assumptions, frozen **raw** at approval (SC-1-10, ADR-0012).

    The fifth snapshot table, and the first whose source is not reached through the scenario's
    positions: a target margin applies to the whole calculation, so the approval freezes the one
    `organization_defaults` row whatever the scenario contains.

    **Raw values, not the result of the chain** (gate 1, P-A; ADR-0012, point 6). What is frozen is
    what the organisation said at the moment of approval — not "the margin this scenario resolved
    to" and not a `source` column naming the level that won. The chain is resolved when the snapshot
    is *read* (`app.data.assumptions`), from this row, the scenario's own columns (frozen by the
    write guard) and the project's (frozen by `FROZEN_BY_APPROVED_SCENARIO` and the lock of
    `app.data.scenario_guard.project_group_two_lock`). A stored result would be a computed figure
    inside a record of what was approved — the class of column SC-3-03's K-07 keeps out of
    `approved_snapshot_absence_budget`.

    **The presence of the row is the fact, the way it is for the statutory absence type**
    (`ApprovedSnapshotAbsenceType`, stage D of SC-3-03): a row present → the organisation had
    defaults at approval time, and each column says what they were (`NULL` → no default for that
    one); **no row** → the organisation had no defaults row at all when this was approved, and that
    stays the answer for ever, whatever is configured later (criterion K-06). A reader must never
    "fill in" a missing row from the live table — that is the one mutation this shape exists to make
    visible.

    **No `source_*_id` column**, and that is not an omission: the source is a singleton with a
    pinned key (`app.models.organization_defaults.SINGLETON_KEY`), so the id would carry no
    information. Uniqueness per scenario is not a constraint either — for the reason
    `ApprovedSnapshotWorkingCalendarDay` gives for not repeating its source's key: the source's own
    singleton key is what makes a second row impossible to copy.
    """

    __tablename__ = "approved_snapshot_organization_defaults"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "scenarios.id", name="fk_approved_snapshot_organization_defaults_scenario_id"
        ),
        nullable=False,
        index=True,
    )
    """The only foreign key a snapshot row is allowed to have — see
    `ApprovedSnapshotWorkingCalendar.scenario_id`."""

    target_margin_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(PERCENT_PRECISION, PERCENT_SCALE), nullable=True
    )
    overload_threshold_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(PERCENT_PRECISION, PERCENT_SCALE), nullable=True
    )
    """The two values as the organisation held them, in the source's own type. Nullable because the
    source is — `NULL` here is "the organisation had no default for this one", frozen as such. **No
    CHECK repeated** (the source refuses a non-positive threshold; a snapshot records what was
    approved and does not re-judge it — the rule every other snapshot table follows)."""


class ApprovedSnapshotCatalogDefaultRate(_ApprovedSnapshotRow):
    """One catalogue rate window a scenario's T&M revenue reads, frozen at approval (SC-4-01).

    The sixth snapshot table and **the first that freezes a rate** (ADR-0004, addendum 2026-09-23
    SC-4-01, point 2): until SC-4-01 no result of an approved calculation depended on a rate, so the
    absence of this table was not a regression; from SC-4-01 it would be (AC-04, AC-10). The shape
    is the SC-3-02 pattern without a change — one table per source table, keyed by `scenario_id`,
    the source ids stored as values, values copied rather than names resolved later.

    Four properties, each of them a decision with a mutation attached:

    1. **Only the windows the calculation reads** (point 2c), the way the budget snapshot freezes
       only the windows its months touch (addendum SC-3-03, points 3 and 7): for each position of
       the scenario, with `vendor_id IS NULL` (ADR-0003, point 4), and each month of its allocation
       that is **priced** — `app.data.commercial_terms.month_is_priced`: the windows overlapping
       the month cover every day of it and share one selling rate and currency (SC-4-01 gate 2,
       R-01) — **every** window of that month. Usually one; two or more when a boundary that
       changes only the cost rate falls inside the month, and then all of them are frozen, because
       the reader re-asks the same question of the frozen rows and needs every piece to answer yes.
       Not the catalogue. Two positions of one tuple freeze one row per window (the deduplication
       canary of SC-3-02 S-01/R-01). A month that is not priced yields no row and stays "no rate"
       for ever.
    2. **`default_cost_rate` is frozen too, although SC-4-01 never reads it** (point 2b). There is
       no UPDATE path to a snapshot, so a scenario approved before plan block 5 without a frozen
       cost could never recover one. The consequence named with it: this table is a **carrier of
       personnel cost**, every future reader of its rows is subject to the SC-1-08 conjunction
       (ADR-0005, addendum 2026-09-23 SC-4-01, point 7), and SC-4-01 exposes no path that returns
       them — the revenue reader selects the selling-rate columns only. **SC-5-01 is its first
       reader** (`app.data.personnel_cost`), behind the cost gate (ADR-0005, aneks 2026-09-23
       SC-5-01), and widens point 1: the windows of a month the *cost* predicate resolves are
       frozen too, whether or not the selling predicate prices it (ADR-0004, aneks 2026-09-23
       SC-5-01, point 1).
    3. **`valid_period` is generated from the same expression as the source's**
       (`app.models.catalog.VALID_PERIOD_EXPRESSION`), so the reader asks the frozen rows the exact
       question the live read asks the catalogue — `month_is_priced` over the windows overlapping
       the month — with no second spelling of the window's boundary (point 2e; the rule 13 of the
       Invariant Guardian that a mechanism moved into the reader must be proven there, which
       criteria K-09 and R-01 do).
    4. **No foreign key but the scenario's, no CHECK repeated, no `updated_at`** — the rules every
       snapshot table follows (module docstring).
    """

    __tablename__ = "approved_snapshot_catalog_default_rate"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "scenarios.id", name="fk_approved_snapshot_catalog_default_rate_scenario_id"
        ),
        nullable=False,
        index=True,
    )
    """The only foreign key a snapshot row is allowed to have — see
    `ApprovedSnapshotWorkingCalendar.scenario_id`."""

    source_rate_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_role_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_seniority_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_location_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_engagement_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    source_vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    """Values, never foreign keys (ADR-0004, addendum 2026-09-22 SC-3-02, point 3b): which window
    was frozen (`source_rate_id`), and the four dimensions plus the vendor axis it was priced for —
    the key a reader matches a position's tuple against. `source_vendor_id` is `NULL` on every row
    SC-4-01 writes (only internal rates are read) and is copied anyway, because the snapshot records
    the source row as it was rather than re-deciding which of its columns matter."""

    default_cost_rate: Mapped[Decimal] = mapped_column(
        Numeric(RATE_PRECISION, RATE_SCALE), nullable=False
    )
    """Frozen; never read by SC-4-01, read by SC-5-01's cost reader — see point 2 of the class
    docstring."""

    default_selling_rate: Mapped[Decimal] = mapped_column(
        Numeric(RATE_PRECISION, RATE_SCALE), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    """The source's own column types, so nothing is narrowed on the way in. `effective_to` stays
    nullable — an open-ended source window is frozen as open-ended — and the reader never compares
    it: it asks `valid_period` below."""

    surcharge_percent: Mapped[Decimal] = mapped_column(
        Numeric(PERCENT_PRECISION, PERCENT_SCALE), nullable=False
    )
    includes_surcharge: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """The two new columns of SC-5-02 (ADR-0004, aneks 2026-09-25 SC-5-02; ADR-0013/ADR-0005, same
    date): frozen on the **same row**, in the **same transaction**, as `default_cost_rate` — one
    decision to freeze, not a second bramka for a second column of one source row (point 2 of that
    aneks). `NOT NULL` with **no default here** on the model, unlike the source column
    (`app.models.catalog.CatalogDefaultRate`): the migration backfills a scenario approved before
    this column existed with the honest value for it (`0`/`false` — no surcharge concept existed at
    the moment of that approval, the same reasoning `a7c2e5f81b94` used for
    `approved_snapshot_absence_type.is_statutory_leave`), and then drops the server default, so no
    later `INSERT … SELECT` can silently omit the column and have the database invent a value nobody
    read from the source (module docstring, "no default on any of them").

    **Read back by the existing reader, symmetrically with `default_cost_rate`** — corrected during
    SC-5-02's own QA review, 2026-09-25: an earlier version of this task named a literal `0`/`false`
    in `app.data.personnel_cost.costed_month_windows`'s approval-snapshot branch instead of this
    column, reading the ADR-0013 aneks's "SC-5-02 sam nie musi wystawiać żadnej ścieżki, która
    zwraca tę kolumnę" (SC-5-02 does not *have to* expose a path returning it) as "must not". That
    left an approved scenario's fully loaded cost frozen at "no surcharge" regardless of what was
    configured at the moment of approval — a silent regression on approval with no catalogue edit
    involved, not a deferred feature. `costed_month_windows` is the *existing* reader of this table
    for the base cost (SC-5-01, unchanged); reading two more columns off the same row through the
    same branch completes the freeze this class exists for rather than building a new reader, and
    applies the identical SC-1-08 conjunction `default_cost_rate` already carries."""

    valid_period: Mapped[Range[date]] = mapped_column(
        DATERANGE,
        Computed(VALID_PERIOD_EXPRESSION, persisted=True),
        nullable=False,
    )
    """Generated by the database from the two dates above, with the source's expression — read-only
    from the ORM's point of view and absent from the approval's `INSERT … SELECT` column list."""
