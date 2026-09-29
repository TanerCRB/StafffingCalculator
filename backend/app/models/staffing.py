"""Scenario staffing: one anonymous position per role tuple, a monthly grid of hours, and the
absences planned against it (F-04, F-05).

Three tables, one aggregate. `staffing_position` is a child of `scenarios`; its monthly allocation
rows and its absences (SC-3-02) are grandchildren, reachable only through a position. That shape
decides four things, each of them written down in an accepted decision rather than chosen here:

1. **Own data of the scenario, not an inherited value** (ADR-0004, addendum 2026-09-19 "the
   staffing position and allocation do not enter the snapshot"). Nothing outside the scenario can
   change these rows, so there is nothing for an approval snapshot to freeze: what protects them
   after approval is the refusal of a write (`app.data.staffing`), not a second copy of the rows.
2. **Scope is inherited through the scenario** (ADR-0001, addendum 2026-09-19; ADR-0005, addendum
   2026-09-19 "staffing positions"). `scenario_id → scenarios.project_id` ties every row to a
   project, so the `project_access` filter applies — and this is the first table in the repository
   for which it applies *indirectly*. The catalogue's exemption ("a row belonging to no project")
   explicitly does not stretch here.
3. **No effective-range pattern.** ADR-0008 is deliberately *not* applied (ADR-0004, addendum
   2026-09-19): two positions for the same role over the same months are legal — a PM plans
   different people on one role at different times, and an overlap is a state to see, not a write to
   refuse. The only uniqueness in this module is `UNIQUE (position_id, period_month)` on the
   allocation row. The absence of an `EXCLUDE` constraint here is correctness, not an omission.
4. **The concurrency token lives on the position** (ADR-0007, addendum 2026-09-19): one
   `updated_at` for the whole month grid of one position, because the grid is edited as a unit. The
   allocation row therefore has no `updated_at` of its own — and must not grow one without
   revisiting that decision, because two tokens on one edit path is a question ADR-0007 does not
   answer.

**Singular table names**, unlike `projects`/`scenarios`: `staffing_position` and
`staffing_position_allocation` are the names the binding decisions use (ADR-0005's addendum quotes
`staffing_position.scenario_id`, ADR-0007's quotes `staffing_position.updated_at`), and a table
whose name differs from the decision that created it cannot be found by reading the decision.

**Until SC-5-03, no cost, rate or amount of money.** A position carried a dimension tuple, a
headcount, its own period and hours — nothing a currency could be attached to (ADR-0005, addendum
2026-09-19, point 5).

**Since SC-5-03: `cost_basis`, `fixed_amount` and `fixed_amount_currency` (F-07; ADR-0013, addendum
2026-09-25 SC-5-03).** Own data of the scenario, group 2 like every other column of this table
(ADR-0004, addendum 2026-09-25 SC-5-03) — protected after approval by the same write guard as the
rest of the row, not by a snapshot: nothing outside the scenario ever changes them, so there is
nothing for an approval to freeze (point 2 of that addendum; contrast with `catalog_default_rates`,
whose value *is* inherited and therefore *is* snapshotted). `fixed_amount`/`fixed_amount_currency`
are `NULL` for the default basis (`worked_time`) and are the position's own stated amount for the
other. They
are **never** part of `GET .../staffing-positions`'s response schema (ADR-0005, addendum 2026-09-25
SC-5-03, Q4) — visible only through the cost endpoint the SC-1-08 conjunction already gates
(`app.api.personnel_cost`), the same treatment `default_cost_rate` gets on the catalogue.

**Since SC-2-06: `person_id` (F-03; ADR-0019; ADR-0004 and ADR-0005, addenda 2026-09-27).** A
position is still anonymous by default; it may optionally point at one row of the person register
(`app.models.person`). A reference, not a name — see `StaffingPosition`. The absence row keeps its
own boundary unchanged: an absence still hangs on the position, never on a person (ADR-0005,
addendum 2026-09-27, point 10)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.scenario import Scenario

HOURS_PRECISION = 10
HOURS_SCALE = 2
"""`NUMERIC(10,2)` for every hour figure — `Decimal`, never `float` (NF-01, ADR-0002).

Hours are not money, but they are one multiplication away from it: a `float` here would put a
binary rounding error into every cost and revenue figure derived from this row, which is the exact
failure ADR-0002 forbids on a money path *or next to one*. Scale 2 because half-hours and quarters
are what a plan is written in; precision 10 leaves room for the sum of a whole position (the hours
of a `headcount` > 1 position are its total, not per head — gate-1 decision 1)."""

FIRST_DAY_OF_MONTH_EXPRESSION = "period_month = date_trunc('month', period_month)"
"""What makes `period_month` a month rather than a day, as SQL — spelled once.

Load-bearing for `UNIQUE (position_id, period_month)` and not merely tidy: without it,
`2026-03-01` and `2026-03-15` are two different `DATE` values, the unique constraint accepts both,
and "one allocation per calendar month" quietly becomes "one allocation per day somebody happened
to type". The truncation is a *refusal*, not a normalisation — a request naming the 15th is a
client bug worth surfacing, for the same reason `Iso4217Code` refuses `"eur"` instead of
upper-casing it.

Copied into the migration that creates the table (a migration must keep describing the schema it
produced), and asserted identical to that copy by
`tests/test_staffing_schema_constraints.py::test_the_model_and_the_migration_agree_on_the_month_check`
— the drift guard R-02 added for the catalogue's generated column."""

HOURS_COLUMNS: tuple[str, ...] = (
    "availability_hours",
    "planned_allocation_hours",
    "billable_hours",
)
"""The three hour figures of one month, as data — because they are three independent inputs and
every mechanism that treats them as a group (the non-negativity constraints, the request schema, the
copier) has to cover all three or be visibly incomplete (criterion K-05).

None of the three is derived from another: availability is how much time the position has,
planned allocation is how much of it the plan uses, billable is how much of that the client pays
for. Deriving any one of them (billable := planned, availability := planned) is the mutation K-05
exists to kill — and the reason they are three columns rather than one column plus two ratios."""

HOURS_NON_NEGATIVE_CONSTRAINTS: dict[str, str] = {
    column: f"{column.removesuffix('_hours')}_non_negative" for column in HOURS_COLUMNS
}
"""Column → the name of the CHECK constraint that keeps it non-negative, spelled once.

The names drop the `_hours` suffix for a reason that is not style: PostgreSQL truncates an
identifier at 63 characters, and the naming convention already spends 32 of them on
`ck_staffing_position_allocation_`. `…_planned_allocation_hours_non_negative` would be cut to a name
nobody can read back to its meaning — and a *test* asserting on the name it expected would fail for
a reason that looks like the constraint being missing (measured while writing K-04c).

Referenced by the tests that prove a refusal came from *this* mechanism rather than from something
else that happened to fail, so a rename is a single edit rather than a search. The migration spells
the same three names out, and a test asks the migrated database whether all three are really there —
which is what would catch a truncation returning by the back door."""


POSITION_ID_SCENARIO_UNIQUE = "uq_staffing_position_id_scenario_id"
"""`UNIQUE (id, scenario_id)` on the position — added by SC-5-05 so that an additional cost can
point at "this position **of this scenario**" with a composite foreign key (ADR-0014, point 1)."""


COST_BASIS_WORKED_TIME = "worked_time"
COST_BASIS_FIXED_AMOUNT = "fixed_amount"
COST_BASIS_ASSIGNED_FTE = "assigned_fte"
COST_BASIS_VALUES: tuple[str, ...] = (
    COST_BASIS_WORKED_TIME,
    COST_BASIS_FIXED_AMOUNT,
    COST_BASIS_ASSIGNED_FTE,
)
"""The three personnel-cost bases a position may choose (F-07; ADR-0013, addendum 2026-09-25 SC-5-03
and addendum 2026-09-29 SC-5-04). `worked_time` is the default (K-02: every position that existed
before SC-5-03 keeps costing exactly as it did); `assigned_fte` joined in SC-5-04, once the
FTE→hours conversion it needs existed (`app.domain.fte_hours`, SC-3-07)."""

ASSIGNED_FTE_PRECISION = 10
ASSIGNED_FTE_SCALE = 4
"""`NUMERIC(10,4)` — a fraction of a full-time position (`1` = one FTE of the **position**), stored
exactly to four places and never rounded on write (ADR-0008, point 6). Six integer digits is far
above any plausible position total and keeps `fte x basis hours x rate` inside the 28-digit decimal
context and under `app.domain.fte_hours.MAX_INPUT` by construction."""

FIXED_AMOUNT_PRECISION = 14
FIXED_AMOUNT_SCALE = 4
"""`NUMERIC(14,4)` — the same precision as every other stored amount in this schema
(`additional_cost.amount`, `catalog_default_rates.default_cost_rate`): more digits than any
currency's minor unit, so an input is stored exactly and not silently rounded (ADR-0008,
point 6)."""

COST_BASIS_KNOWN_EXPRESSION = "cost_basis IN ('worked_time', 'fixed_amount', 'assigned_fte')"
FIXED_AMOUNT_REQUIRES_ITS_OWN_BASIS_EXPRESSION = (
    "cost_basis <> 'fixed_amount' OR "
    "(fixed_amount IS NOT NULL AND fixed_amount_currency IS NOT NULL)"
)
"""ADR-0013, addendum 2026-09-25 SC-5-03, point 2 (Q2 = A): a CHECK, not an application validation —
`cost_basis = 'fixed_amount' → fixed_amount IS NOT NULL`, widened here to require the currency too
(the open question the addendum names for this task to settle, point 2, second half). Reasoning: a
`fixed_amount` with no currency is exactly the shape ADR-0013's "two shapes, never a third" forbids
for the worked-time basis (an amount nobody can state a currency for), so the same rule applies to
its own basis rather than being left to a `NULL` currency nobody decided the meaning of. Named here
so the choice is not a silent one (the addendum requires it named in "Done when", not left
implicit). Mirrors `ck_additional_cost_amount_positive`/G-1 (ADR-0014, D-10) in spelling the
guarantee once, in the database, so a fixture, a seed script or a future import can never write the
unnamed state."""
FIXED_AMOUNT_POSITIVE_EXPRESSION = "fixed_amount IS NULL OR fixed_amount > 0"
FIXED_AMOUNT_CURRENCY_ISO4217_EXPRESSION = (
    "fixed_amount_currency IS NULL OR char_length(fixed_amount_currency) = 3"
)
FIXED_AMOUNT_CURRENCY_IS_UPPER_EXPRESSION = (
    "fixed_amount_currency IS NULL OR fixed_amount_currency = upper(fixed_amount_currency)"
)
"""The CHECK expressions as SQL, each spelled once here and once in migration `a8f18e00172b`, and
asserted identical to that copy by the accompanying schema-drift test (the pattern `HOURS_COLUMNS`'s
module docstring and `additional_cost`'s `_EXPRESSION` constants both already use). Since SC-5-04
`COST_BASIS_KNOWN_EXPRESSION` is the one of them that changed: it is spelled in migration
`d4a7e19c2b60` as well (which widens the constraint), and the drift test compares the model with the
*head* of the migration chain, not with the migration that first created it."""

ASSIGNED_FTE_POSITIVE_EXPRESSION = "assigned_fte IS NULL OR assigned_fte > 0"
ASSIGNED_FTE_REQUIRED_FOR_ITS_BASIS_EXPRESSION = (
    "cost_basis <> 'assigned_fte' OR assigned_fte IS NOT NULL"
)
ASSIGNED_FTE_ONLY_ON_ITS_BASIS_EXPRESSION = (
    "assigned_fte IS NULL OR cost_basis = 'assigned_fte'"
)
ASSIGNED_FTE_NOT_WITH_FIXED_AMOUNT_EXPRESSION = (
    "assigned_fte IS NULL OR (fixed_amount IS NULL AND fixed_amount_currency IS NULL)"
)
"""ADR-0013, addendum 2026-09-29 SC-5-04, point 2 (FA-1), in the database rather than in a request
schema, for the reason `FIXED_AMOUNT_REQUIRES_ITS_OWN_BASIS_EXPRESSION` gives: a fixture, a seed
script or a future import never passes through Pydantic. Four claims, four constraints, so a refusal
names which one broke: positive when set; not `NULL` on its own basis; `NULL` on every other
basis (a stray FTE on a `worked_time` row would be an input nothing reads and somebody may believe);
never together with a stated amount (two ways to say the same cost on one row). There is **no**
upper bound tied to `headcount`: a value above it is accepted without bound and is not surfaced
anywhere (point 2, named limitation: a percent typo, 50 for 0.5, prices 100 times too high with
state `calculated`)."""


STAFFING_POSITION_PAGE_INDEX = "ix_staffing_position_scenario_start_date_id"
"""The btree index that makes one page of `GET .../staffing-positions` a bounded top-N (SC-3-05,
gate-2 review R-02 — mirrored from `app.models.catalog.RATE_PAGE_INDEX`, the identical gap on the
identical shape of query).

Before it, this table carried the primary key (on `id` alone) and the single-column index on
`scenario_id` alone (`ForeignKey(..., index=True)` above) — neither can produce
`app.data.staffing._staffing_position_page_statement`'s order, `ORDER BY start_date, id` filtered by
one `scenario_id`. Without a composite index leading on all three, the planner has to fetch every
row of the scenario before it can sort and cut, so the docstring `_staffing_position_page_statement`
carried before this fix ("an index scan that stops after limit + offset rows") was true of no index
this table actually had — the same false promise `RATE_PAGE_INDEX`'s own module docstring records
having been the reason for its addition.

Created by migration `f1a2c4b6d8e0` (`op.create_index`, expand-only — the same shape as
`e2c7b04d9a31`: nothing dropped, nothing rewritten, a query plan improves and no result changes).
Declared here as well, so the model keeps describing the database that exists; a schema-drift test
compares the two copies of the name and the columns, the same pattern `RATE_PAGE_INDEX` uses."""


PERSON_REQUIRES_SINGLE_HEADCOUNT_EXPRESSION = "person_id IS NULL OR headcount = 1"
"""SC-2-06, decision Q-7 = a (gate 1, 2026-09-27): a named person may be assigned only to a position
that plans for exactly one person. In the database, not in the request schema, so it also refuses
the write no HTTP path exists for yet — raising `headcount` above 1 on a position that already has a
person (there is no headcount edit endpoint; a fixture, a seed script or a future import is exactly
the writer that would do it). Spelled here and once more in migration `c4d7e2a9b1f6`; a schema-drift
test compares the two copies."""

PERSON_REQUIRES_SINGLE_HEADCOUNT_CONSTRAINT = (
    "ck_staffing_position_person_requires_single_headcount"
)
"""The constraint's name in the database, spelled once for the tests that assert a refusal came
from *this* mechanism."""


class StaffingPosition(Base):
    """One staffing position of one scenario: a dimension tuple, a headcount, a period — and,
    optionally, the named person who is to fill it (SC-2-06).

    **Anonymous by default** (F-03/F-04): `person_id` is nullable, every position created before
    SC-2-06 has none, and creating a position never sets one. **Optionally named** (F-03: "Assigning
    a named person shall be optional"), under ADR-0019 (the personal-data decision) and ADR-0005's
    addendum 2026-09-27 — no longer "blocked on the authentication ADR" (decision P-2 = a: a named
    person is a separate record, not a user account). What the assignment is, and is not:

    - a reference to `person.id`, never a copy of the name — the name lives in the register only,
      so a correction (RODO art. 16) is one row, visible on every scenario, approved ones included;
    - own data of the scenario, group 2 (ADR-0004, addendum 2026-09-27, point 2): protected after
      approval by the write guard in the same statement as the write, never by a snapshot;
    - allowed only at `headcount = 1` (`PERSON_REQUIRES_SINGLE_HEADCOUNT_EXPRESSION`);
    - written through **one** path (`app.data.staffing.assign_person`), never as a side effect of
      another write — every other write of this row leaves it as it is (ADR-0005, addendum
      2026-09-27, point 6);
    - visible in a response only to a caller holding `STAFFING_READ` ∧ `PEOPLE_READ`, and for
      everyone else not even as a key (`app.api.response_shaping.shape_staffing_position`).
    """

    __tablename__ = "staffing_position"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # No `ondelete` — i.e. `NO ACTION` — on any foreign key in this module. Nothing in this task
    # deletes a scenario, a position or a dimension entry (deletion is out of scope, and named as
    # such in the plan entry), so the database refusing to orphan a row is the option that pre-empts
    # no later decision. `ON DELETE CASCADE` towards the scenario would additionally be a second,
    # unguarded way for the rows of an `approved` scenario to disappear: the write guard in
    # `app.data.staffing` is on `INSERT`/`UPDATE`, and a cascade is neither.
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_staffing_position_scenario_id"),
        nullable=False,
        index=True,
    )

    # The four business dimensions, all `NOT NULL`, exactly as `app.models.catalog.
    # RATE_DIMENSION_COLUMNS` names them — but **not**, since SC-2-03, the full key of a rate's
    # `EXCLUDE` constraint. That key (`app.models.catalog.RATE_EXCLUDE_KEY`) has a fifth element,
    # `vendor_id`, nullable in the column and a required keyword argument (no default, R-03) on
    # `app.data.catalog.resolve_rate`. A future consumer of this position's tuple (Issue #9, a
    # rate on a staffing position) that calls `resolve_rate(**tuple, on_date=...)` is therefore
    # refused by Python before it reaches the database — the signature forces a conscious choice
    # of whose price is wanted (`None` for the organisation's own, an id for one vendor's;
    # K-03/K-04) rather than silently defaulting to "internal" for a tuple that may only be
    # priced by a subcontractor. A nullable dimension, in contrast, would mean "any", which is a
    # rate-resolution rule nobody decided (rule 13 of the Invariant Guardian). The foreign keys
    # carry explicit names for the reason `catalog_default_rates` documents — the derived name for
    # `engagement_type_id` exceeds PostgreSQL's 63-character identifier limit and gets truncated
    # with a hash appended.
    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_roles.id", name="fk_staffing_position_role_id"),
        nullable=False,
    )
    seniority_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_seniorities.id", name="fk_staffing_position_seniority_id"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_locations.id", name="fk_staffing_position_location_id"),
        nullable=False,
    )
    engagement_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "catalog_engagement_types.id", name="fk_staffing_position_engagement_type_id"
        ),
        nullable=False,
    )

    headcount: Mapped[int] = mapped_column(Integer, nullable=False)
    """How many people this position plans for. An integer, not a `Decimal`: a fraction of a person
    is an allocation figure (hours), not a headcount, and FTE as a unit is out of scope until a
    working calendar exists to convert it (F-05, Issue #7)."""

    # Calendar dates, not points in time (invariant-guardian rule 15): "this position starts on
    # 1 March" has no timezone and no clock.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    """`NULL` means open-ended — the position runs for as long as the scenario does. Not a
    `9999-12-31` sentinel, for the same reason ADR-0008 gives for `effective_to`: a sentinel is a
    second spelling of "unbounded" that no query treats as one."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    """ADR-0007's concurrency token for the whole position, its month grid included (addendum
    2026-09-19). The value is always the *database's* clock — `func.now()` and, on the allocation
    edit path, an explicit `now()` in the statement — never this process's, so two application
    instances cannot disagree about which write came last."""

    allocations: Mapped[list["StaffingPositionAllocation"]] = relationship(
        back_populates="position",
        order_by="StaffingPositionAllocation.period_month",
        cascade="all, delete-orphan",
        passive_deletes=False,
    )
    """Ordered by month, because a grid read back in arbitrary order is a grid nobody can compare
    with the one they sent. `delete-orphan` is an ORM-level statement about the aggregate (an
    allocation with no position is not a row that means anything), not a database cascade — the
    foreign key stays `NO ACTION`, so nothing deletes a position that still has months."""

    absences: Mapped[list["StaffingPositionAbsence"]] = relationship(
        back_populates="position",
        order_by="StaffingPositionAbsence.start_date, StaffingPositionAbsence.id",
        cascade="all, delete-orphan",
        passive_deletes=False,
    )
    """The planned absences of this position (F-05, SC-3-02), ordered by start date and then by id.

    Ordered by *both*, unlike the month grid: two absences may legitimately start on the same day
    (criterion K-06), so the start date alone is not a total order and a list read twice could come
    back differently. `delete-orphan` is an ORM statement about the aggregate, not a database
    cascade — the foreign key stays `NO ACTION`."""

    cost_basis: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=COST_BASIS_WORKED_TIME
    )
    """Which formula prices this position's personnel cost (F-07; ADR-0013, addendum 2026-09-25
    SC-5-03) — `worked_time` (the default, K-02) or `fixed_amount`. A stored, persistent choice, not
    a parameter of a read: two callers reading the same position on the same day must see the same
    basis. `server_default` rather than an application default alone, so a row written outside the
    request schema (a fixture, a future import) still lands on the backward-compatible value."""

    fixed_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(FIXED_AMOUNT_PRECISION, FIXED_AMOUNT_SCALE), nullable=True
    )
    """The position's personnel cost, stated directly, when `cost_basis = 'fixed_amount'` — `NULL`
    otherwise. Read by nothing but its own formula (`app.domain.fixed_amount_cost`): no catalogue
    lookup, no dependency on `planned_allocation_hours` (K-01)."""

    fixed_amount_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    """`fixed_amount`'s own currency (ADR-0013, addendum 2026-09-25 SC-5-03, Q1 = A) — not inherited
    from `scenarios.currency`: the two are compared by the formula, and disagreement is the named
    `currency_mismatch` state, never a silent conversion (ADR-0006)."""

    assigned_fte: Mapped[Decimal | None] = mapped_column(
        Numeric(ASSIGNED_FTE_PRECISION, ASSIGNED_FTE_SCALE), nullable=True
    )
    """The stored FTE of the position when `cost_basis = 'assigned_fte'` — `NULL` otherwise (F-07;
    ADR-0013, addendum 2026-09-29 SC-5-04, point 2). A **fraction and a position total**: `1` is one
    FTE of the position, no `headcount` factor and no comparison with it. Read by nothing but its
    own formula (`app.domain.assigned_fte_cost`); never a column of `GET .../staffing-positions`
    (ADR-0005 addendum 2026-09-29 SC-5-04, the same treatment `fixed_amount` gets). Own data of the
    scenario, like every column of this row: protected after approval by the write guard, copied by
    the existing reflective copier, never snapshotted (ADR-0004, same date)."""

    person_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("person.id", name="fk_staffing_position_person_id"),
        nullable=True,
    )
    """The named person assigned to this position, or `NULL` for an anonymous one (SC-2-06).

    No `ondelete` — `NO ACTION`, like every foreign key in this module, and here it carries a second
    meaning (ADR-0019, point 7): a person assigned to a position cannot be physically deleted, which
    is the direction the future deletion Story is decided to take anyway (anonymisation in place,
    `id` and assignments untouched). Copied as-is by `copy_staffing_positions` — the copy points at
    the **same** person, never at a copy of one (ADR-0004, addendum 2026-09-27, point 4)."""

    person_assignment_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    """The assignment's **own** concurrency marker (ADR-0007 addendum 2026-09-28, D-4 = B) —
    protecting `person_id` and nothing else, and moved by the assignment path and nothing else.

    **No `onupdate`, on purpose.** The position's `updated_at` is visible to every caller with
    `STAFFING_READ`, `PEOPLE_READ` or not; if an assignment moved it, the move itself would be the
    "a person was assigned here" flag ADR-0019 (point 4) forbids. So the two markers guard disjoint
    columns: this one `person_id`, `updated_at` every other column and the aggregate — and every
    write path compares exactly one of them. The assignment statement also spells `updated_at =
    updated_at` explicitly, because an ORM-level `onupdate` fires on any `UPDATE` of the table that
    does not name the column (`app.data.staffing.assign_person`).

    Gated in responses exactly like `person_id` (ADR-0005 addendum 2026-09-28, point 3). **Not**
    copied: a copy gets its own (ADR-0007 addendum 2026-09-28, point 5)."""

    scenario: Mapped["Scenario"] = relationship()

    __table_args__ = (
        # SC-2-06 (Q-7 = a): a person only on a position that plans for exactly one person.
        CheckConstraint(
            PERSON_REQUIRES_SINGLE_HEADCOUNT_EXPRESSION, name="person_requires_single_headcount"
        ),
        # In the database, not only in the request schema: a headcount of 0 or -1 written by a
        # fixture, a seed script or a future import is the same defect as one written by a client.
        CheckConstraint("headcount > 0", name="headcount_positive"),
        # The same ordering rule as `scenario_period_ordered` and the catalogue's
        # `effective_period_ordered`, for the same reason — an inverted period is not a period.
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="position_period_ordered"
        ),
        # The parent half of `app.models.additional_cost.POSITION_SAME_SCENARIO_FOREIGN_KEY`
        # (SC-5-05, ADR-0014 point 1). Redundant as a uniqueness claim — `id` alone is the primary
        # key — and required anyway: PostgreSQL accepts a foreign key only against a unique
        # constraint on exactly the referenced columns. The same construction as
        # `uq_commercial_terms_id_model_type` (SC-4-01).
        UniqueConstraint("id", "scenario_id", name=POSITION_ID_SCENARIO_UNIQUE),
        # SC-5-03 (F-07; ADR-0013, addendum 2026-09-25 SC-5-03, points 2 and 6; K-06). The
        # database, not the request schema, is what makes "fixed_amount basis with no amount" a
        # state nobody can write — a fixture, a seed script or a future import included.
        CheckConstraint(COST_BASIS_KNOWN_EXPRESSION, name="cost_basis_known"),
        CheckConstraint(
            FIXED_AMOUNT_REQUIRES_ITS_OWN_BASIS_EXPRESSION,
            name="fixed_amount_required_for_its_basis",
        ),
        CheckConstraint(FIXED_AMOUNT_POSITIVE_EXPRESSION, name="fixed_amount_positive"),
        CheckConstraint(
            FIXED_AMOUNT_CURRENCY_ISO4217_EXPRESSION, name="fixed_amount_currency_iso4217"
        ),
        CheckConstraint(
            FIXED_AMOUNT_CURRENCY_IS_UPPER_EXPRESSION, name="fixed_amount_currency_is_upper"
        ),
        # SC-5-04 (F-07; ADR-0013, addendum 2026-09-29 SC-5-04, point 2; FA-1): the stored FTE.
        CheckConstraint(ASSIGNED_FTE_POSITIVE_EXPRESSION, name="assigned_fte_positive"),
        CheckConstraint(
            ASSIGNED_FTE_REQUIRED_FOR_ITS_BASIS_EXPRESSION,
            name="assigned_fte_required_for_its_basis",
        ),
        CheckConstraint(
            ASSIGNED_FTE_ONLY_ON_ITS_BASIS_EXPRESSION, name="assigned_fte_only_on_its_basis"
        ),
        CheckConstraint(
            ASSIGNED_FTE_NOT_WITH_FIXED_AMOUNT_EXPRESSION,
            name="assigned_fte_not_with_fixed_amount",
        ),
        # Not an integrity constraint — the one index this table has for *paging* a scenario's
        # grid (SC-3-05, R-02). Declared here as well as created by migration `f1a2c4b6d8e0`, so the
        # model keeps describing the database that exists. See `STAFFING_POSITION_PAGE_INDEX`.
        Index(STAFFING_POSITION_PAGE_INDEX, "scenario_id", "start_date", "id"),
    )


class StaffingPositionAllocation(Base):
    """One calendar month of one position: available, planned and billable hours (F-04).

    Three independent figures, no derivation between them (see `HOURS_COLUMNS`), and a month with
    zero hours is data rather than an error: a gap in the middle of a position's period is how a
    plan says "nobody works here that month", so `0` is accepted and only a *negative* figure is
    refused.
    """

    __tablename__ = "staffing_position_allocation"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("staffing_position.id", name="fk_staffing_position_allocation_position_id"),
        nullable=False,
    )
    """No `index=True` (R-05, reviewer 2026-09-19): `UNIQUE (position_id, period_month)` below
    already creates a btree whose leading column is this one, so `WHERE position_id = ?` — the only
    lookup this table has — rides on it. A second index would be a write on every insert and edit of
    a month row for no query of its own. `StaffingPosition.scenario_id` does keep its index: that
    column has no unique constraint to ride on."""

    period_month: Mapped[date] = mapped_column(Date, nullable=False)
    """The first day of the month this row is about — a `DATE` truncated to the month by the CHECK
    constraint below, not a free-form day (see `FIRST_DAY_OF_MONTH_EXPRESSION`). Monthly
    granularity is the gate-1 decision: delivery phases as a dimension need an entity that does not
    exist yet (F-02, Issue #4)."""

    availability_hours: Mapped[Decimal] = mapped_column(
        Numeric(HOURS_PRECISION, HOURS_SCALE), nullable=False
    )
    """How many hours this position has available that month — entered by hand. **Named risk:**
    until F-05 (calendars, absences — Issue #7) nothing in the system knows about a single day off,
    so this figure is an assertion by the planner and not a derived capacity."""

    planned_allocation_hours: Mapped[Decimal] = mapped_column(
        Numeric(HOURS_PRECISION, HOURS_SCALE), nullable=False
    )
    """How many hours the plan uses. May exceed `availability_hours`: over-allocation is a legal
    state to be shown, never a write to refuse (gate-1 out of scope, point 5 — the warning needs a
    threshold from F-02 and must not be implemented as a CHECK constraint)."""

    billable_hours: Mapped[Decimal] = mapped_column(
        Numeric(HOURS_PRECISION, HOURS_SCALE), nullable=False
    )
    """How many of the planned hours the client pays for. Not derived from the planned figure and
    not a percentage of it: the cost of non-billable effort is F-07 (Issue #9), and it needs this
    number to be an input rather than a rounding of another one."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # No `updated_at` here: the concurrency token is the position's (ADR-0007, addendum
    # 2026-09-19). A second token on this row would make one request carry N tokens and would need
    # a rule for partial refusal that ADR-0007 does not have.

    position: Mapped["StaffingPosition"] = relationship(back_populates="allocations")

    __table_args__ = (
        # One allocation per (position, calendar month). Together with the CHECK below — never
        # without it, see `FIRST_DAY_OF_MONTH_EXPRESSION`.
        UniqueConstraint("position_id", "period_month"),
        CheckConstraint(FIRST_DAY_OF_MONTH_EXPRESSION, name="period_month_is_first_of_month"),
        # One constraint per column rather than one conjunction over the three: the refusal then
        # names *which* figure was negative (`describe_without_values` keeps the constraint name and
        # drops the values — NF-11), and dropping one of the three is a mutation a test can kill.
        *(
            CheckConstraint(f"{column} >= 0", name=constraint)
            for column, constraint in HOURS_NON_NEGATIVE_CONSTRAINTS.items()
        ),
    )


class StaffingPositionAbsence(Base):
    """One planned absence of one staffing position: a type from the dictionary and a date range.

    The third table of the position aggregate (F-05, SC-3-02) and, like the month row, a
    *grandchild* of the scenario. Four decisions, each of them already taken in an accepted
    decision rather than here:

    1. **Own data of the scenario → write guard, not snapshot** (ADR-0004, addendum 2026-09-22,
       point 1). Nothing outside the scenario can change these rows, so there is nothing for the
       approval snapshot to freeze; what protects them after approval is the refusal of a write
       (`app.data.staffing`). The absence *type* is the other way round — organisational, therefore
       snapshotted (`app.models.approved_snapshot`). Reconsider the day an absence moves to a person
       or an organisational register (Issue #31): it changes group and the snapshot has to grow.
    2. **Scope inherited through the position** (ADR-0005, addendum 2026-09-22, point 4):
       `position_id → staffing_position.scenario_id → scenarios.project_id`, the second degree of
       indirection after the month row, through the same `project_for_caller` and with no scope
       function of its own.
    3. **No concurrency token of its own** (ADR-0007, addendum 2026-09-22, point 1). The token is
       `staffing_position.updated_at`, for the whole aggregate, and adding one here would make one
       request carry N tokens with no rule for partial refusal.
    4. **A personal-data boundary drawn at creation, not after an incident** (ADR-0005, addendum
       2026-09-22, point 6). The columns below are the whole row: there is **no column for a person,
       a note, a justification or a comment**, and a type that in some cases implies health data may
       only be named through the dictionary. An "optional" free-text field added later turns this
       table into a health register without the decision that governs one — criterion K-22 asserts
       the column set by *equality* for that reason, so `employee`, `comment` or `justification`
       fail it just as `person_name` does.

    **Whole days only.** A half-day absence is the same open question as a partially working day
    (ADR-0008, addendum 2026-09-22, point 6) and is out of scope with it.
    """

    __tablename__ = "staffing_position_absence"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("staffing_position.id", name="fk_staffing_position_absence_position_id"),
        nullable=False,
        index=True,
    )
    """Indexed, unlike the allocation row's `position_id`: this table has no unique constraint whose
    leading column it could ride on (two absences of one position over the same days are legal and
    are counted twice — criterion K-06), so "the absences of one position" has nothing else to use.

    No `ondelete`, like every other foreign key in this module: a cascade towards the scenario or
    the position would be a second, unguarded way for the rows of an `approved` scenario to
    disappear, since the write guard covers `INSERT`/`DELETE` and a cascade is neither."""

    absence_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("absence_type.id", name="fk_staffing_position_absence_absence_type_id"),
        nullable=False,
    )
    """Which kind of absence — **only** from the dictionary. `NOT NULL` and a foreign key, so there
    is no free-text way to say what this absence is (ADR-0005, addendum 2026-09-22, point 6)."""

    # Calendar dates, not points in time (invariant-guardian rule 15). Both `NOT NULL`: an absence
    # is a closed range of days, and an open-ended one would be a person absent for ever.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    """Inclusive at both ends, and the inclusiveness is never converted anywhere: the consumer
    intersects the range with the calendar's working days (`app.domain.capacity`), so there is no
    second spelling of the boundary to disagree with this one."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # No `updated_at`: the token is the position's (ADR-0007, addendum 2026-09-22, point 1), and
    # `tests/test_staffing_schema_constraints.py` asserts its absence against the migrated database.

    position: Mapped["StaffingPosition"] = relationship(back_populates="absences")

    __table_args__ = (
        # The same ordering rule as every other period in this schema, and load-bearing rather than
        # tidy: an inverted range intersects no calendar day at all, so it would be an absence that
        # silently consumes nothing instead of being refused.
        CheckConstraint("end_date >= start_date", name="absence_period_ordered"),
        # **No `EXCLUDE USING gist`, and the absence is the decision.** Two absences of one position
        # over the same days are two people, not one counted twice (criterion K-06) — a position
        # with `headcount = 3` legitimately has three overlapping absences. An overlap constraint
        # copied from `catalog_default_rates` "for symmetry" would refuse the plan it exists to
        # express.
    )
