"""The organisational catalogue: role dimensions, vendors, rates, calendars, absence types and
absence budgets (F-03, F-05; SC-2-01/SC-2-03/SC-3-02/SC-3-03).

Ten tables, and one thing they all have in common: **no column ties a row to a project, a user, a
business unit or a tenant.** That is what puts them outside the `project_access` scope filter
(ADR-0005, addendum 2026-09-19 "pierwszy zbiór danych bez zasięgu projektu", point 1) and outside
the single-guarded-read-path requirement (ADR-0001, addendum 2026-09-19 — a guard function exists so
a scope predicate cannot be forgotten, and there is no predicate here to forget). The moment one of
these tables grows such a column, both exceptions expire and need their own dated entry in those
decisions.

**`vendor_id` is not that column, and the distinction is decided rather than assumed** (ADR-0001,
addendum 2026-09-21, point 1; ADR-0005, addendum 2026-09-21, point 1). A subcontractor is the
*other side of a contract*, not a subject the caller acts on behalf of: no endpoint narrows rates by
the caller's relationship to a vendor, and `CATALOG_READ` shows every vendor's price list to
everybody who holds it. That consequence was taken deliberately at gate 1 (Issue #46), not
discovered afterwards. The exception expires the day any read is narrowed per vendor.

**SC-3-02 adds the sixth and seventh dictionaries and one child table** (ADR-0005, addendum
2026-09-22, points 1-3): `working_calendar`, `absence_type` and `working_calendar.days`. They are
dictionaries of the same family — same exemption from scope, same `CATALOG_READ`/`CATALOG_WRITE`
pair, no new permission — and `catalog_locations.calendar_id` does **not** end the exemption: it
points at another organisational row, not at a subject the caller acts for, exactly as `vendor_id`
does (ADR-0001, addendum 2026-09-22, point 2). What is *not* here is the absence **instance**: that
row belongs to a scenario and lives in `app.models.staffing`, behind the `project_access` filter.

**SC-3-03 adds the eighth table, `AbsenceBudget`, and one flag column on `absence_type`** (ADR-0005,
addendum 2026-09-22 SC-3-03, points 1-3): the same exemption from scope, the same
`CATALOG_READ`/`CATALOG_WRITE` pair, no new permission. A budget is a number of **days**, not an
amount — the decision that it is organisational data rather than cost data is point 3 of that
addendum, and the boundary that comes with it is point 4: the *computed cost* of a budget is a cost
field and goes back through the SC-1-08 conjunction. Nothing in SC-3-03 computes one.

**SC-5-05 adds one more dictionary, `catalog_cost_categories`** (ADR-0014, point 2; ADR-0005, aneks
2026-09-23 SC-5-05, point 3): the categories of an additional cost, built on the same base as the
five below and served by the same pair of endpoints — another dictionary, not another mechanism.

The five dictionaries are **data, not code** (NF-10): no `StrEnum` anywhere restricts which roles,
seniorities, locations, engagement types or vendors may exist, so adding "Site Reliability Engineer"
is an `INSERT`, not a migration. The five *kinds* are code, because they are five columns of the
rate table — that is a different statement from the values they hold.

`CatalogDefaultRate` is the first table in this repository built on ADR-0008's effective-range
pattern (`effective_from`/`effective_to` + a generated `valid_period daterange` + `EXCLUDE USING
gist`). It sets the precedent for `exchange_rates` (ADR-0006) and `commercial_terms` (ADR-0003), so
the shape here is the shape those two inherit.

Not a child of a scenario: `SCENARIO_CHILD_COPIERS` deliberately gains no entry for these tables
(ADR-0004, addendum 2026-09-19 — copying a project does not copy the company's catalogue).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import DATERANGE, ExcludeConstraint, Range
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

RATE_UNIT_HOUR = "hour"
"""The only unit a rate row may carry today (gate-1 decision 4).

A constant, not an enum with one member: F-07 adds daily and monthly units, and an enum with one
member invites reading "unit" as decoration. The refusal lives in the database (`unit_is_hour`
below), because a Pydantic `Literal` only ever sees requests (criterion K-07 requires the path that
does not go through the request schema to be refused as well)."""

RATE_PRECISION = 14
RATE_SCALE = 4
"""`NUMERIC(14,4)` — deliberately a larger scale than any currency's minor unit (ADR-0008, point 6).

A rate is *input*, not the result of a rounding step: storing it as `NUMERIC(12,2)` would silently
change 12.345 into 12.35 at write time. Rounding to the currency unit stays the consumer's rule
(`app.core.money.round_money`), applied where the rate enters a calculation."""


CONCURRENCY_MARKER_COLUMN = "updated_at"
"""The name of ADR-0007's concurrency marker, spelled once for the six catalogue tables.

Referenced by `app.data.catalog` (which builds the `WHERE … AND updated_at = :expected` clause) and
by the migration that adds the column, so a rename is one edit rather than a search. It is a
*timestamp*, and deliberately not accompanied by a "who changed this" column: such a column would
tie a catalogue row to a user and immediately expire the structural exception this module's
docstring rests on (ADR-0001/ADR-0005, addenda 2026-09-19 — Issue #49, gate-1 decision Q-1)."""


class _CatalogDimension(Base):
    """One row of one dimension dictionary: an id, a name and the two timestamps, nothing else.

    Abstract on purpose — the five dictionaries are five tables rather than one table with a `kind`
    discriminator, because the rate row references each of them separately as a foreign key, and a
    single table would make "seniority id in the location column" a valid row.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    """ADR-0007's concurrency marker, declared once here for all five dictionaries (SC-2-04).

    One column on the shared abstract base rather than five copies: SC-2-03 decided that a vendor is
    "the fifth dictionary, not a fifth mechanism", and a marker declared per table is exactly the
    shape in which the fifth one is the one that does not get it.

    `onupdate=func.now()` is a **SQL** expression, so the new value is the database's clock and not
    this process's — two application instances cannot disagree about which write came last, and
    nothing here reads the system clock. The comparison that makes it a guard is not in Python
    either: it is the `WHERE … AND updated_at = :expected` that
    `app.data.catalog.update_dimension_entry` builds, evaluated by the database in the same
    statement as the write."""


DIMENSION_NAME_KEY_EXPRESSION = r"lower(btrim(regexp_replace(name, '\s+', ' ', 'g')))"
"""What makes two dimension names "the same name", as SQL — the key of the unique index below.

Case-folded, trimmed, and with every run of whitespace collapsed to one space. Measured before this
existed (R-04, reviewer 2026-09-19): `"Senior"`, `"senior"`, `"SENIOR"`, `"Senior Dev"` and
`"Senior  Dev"` were five rows, while this module's own docstring claimed the opposite. Five
"Senior"s split the rate table into five halves that look like one, and no screen can tell them
apart — which is the same class of defect as accepting `"Hour"` for the rate unit, refused two
tables over by a CHECK constraint.

**Refusal, not normalisation.** The stored `name` keeps exactly the spelling that was sent (the
index key is computed, the column is not rewritten), for the reason `Iso4217Code` gives for
rejecting `"eur"`: silently normalising input is how two spellings of one thing end up
indistinguishable in a column that nobody can audit afterwards. What is refused is the *second* row
that means the same thing.

In the database rather than in a validator: a `SELECT` asking "does a similar name exist?" before an
`INSERT` is check-then-act, and two callers adding "Senior" at once would both pass it (the mutation
that already survived delivered tests twice in this repository — SC-1-02, SC-1-04). Every function
in this expression (`lower`, `btrim`, `regexp_replace`) is `IMMUTABLE`, which is what lets it index.

**The nesting order is load-bearing.** `regexp_replace` runs *first*, `btrim` second: PostgreSQL's
one-argument `btrim` strips **spaces only**, so trimming first leaves a leading tab or newline in
place, and a tab-padded "Senior" then normalises to `" senior "` — a sixth spelling,
indistinguishable from a fix. Collapsing every whitespace run to a single space first makes the
trim complete. Migration `4f0a9c1b7d62` names the same trap (`btrim` versus `~ '[^[:space:]]'`),
and the tab case of `test_r_04_a_dimension_name_differing_only_in_case_or_spacing_is_the_same_name`
is what caught it in the first version of this expression.

Spelled once, next to the model, and asserted identical to the migration's copy by
`test_the_model_and_the_migration_agree_on_every_sql_expression` (R-02)."""


def _dimension_table_args(table_name: str) -> tuple[object, ...]:
    """The non-blank CHECK the projects table carries, plus a unique index on the *normalised* name.

    `NOT NULL` alone still admits `''` and `'   '`; the pattern `~ '[^[:space:]]'` is the exact
    claim the API boundary makes with `strip_whitespace=True`, so the two cannot disagree about what
    a blank name is (migration `4f0a9c1b7d62` made the same argument for projects).

    Uniqueness is on the name because a dimension entry *is* its name — and on the *normalised*
    name, because `UNIQUE (name)` alone made "Senior" and "senior" two different entries (R-04; see
    `DIMENSION_NAME_KEY_EXPRESSION`). A plain `UNIQUE (name)` is deliberately **not** kept
    alongside it: it refuses a strict subset of what the index refuses, so two constraints would
    only make the error message depend on which one PostgreSQL happened to check first.

    The CHECK is named short so the metadata naming convention (`app.db.base.NAMING_CONVENTION`)
    expands it to `ck_<table>_name_not_blank`. The index carries its full name, because that
    convention has no template for an expression index and a derived name would be unreadable.
    """
    return (
        CheckConstraint("name ~ '[^[:space:]]'", name="name_not_blank"),
        Index(
            f"uq_{table_name}_name_normalized",
            text(DIMENSION_NAME_KEY_EXPRESSION),
            unique=True,
        ),
    )


class CatalogRole(_CatalogDimension):
    __tablename__ = "catalog_roles"
    __table_args__ = _dimension_table_args("catalog_roles")


class CatalogSeniority(_CatalogDimension):
    __tablename__ = "catalog_seniorities"
    __table_args__ = _dimension_table_args("catalog_seniorities")


class CatalogLocation(_CatalogDimension):
    __tablename__ = "catalog_locations"
    __table_args__ = _dimension_table_args("catalog_locations")

    calendar_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("working_calendar.id", name="fk_catalog_locations_calendar_id"),
        nullable=True,
    )
    """Which working calendar the people in this location follow (F-05, SC-3-02).

    **Nullable, and `NULL` is a named state rather than a gap to fill with a guess** (ADR-0008,
    addendum 2026-09-22, point 7, gate-1 decision G-2). A position in a location with no calendar
    gets `derived_capacity_state = "no_calendar"` and `derived_capacity_hours = "n/a"` — never a
    silent `0` hours per day, never an unhandled exception (criterion K-23). The same reading
    `vendor_id` carries one table over: an omission is a state, not a hole.

    **Not a scope column** (ADR-0005, addendum 2026-09-22, point 2; ADR-0001, same date, point 2).
    It points at another organisational row, not at a subject the caller acts on behalf of, so the
    exemption this module rests on survives it — and, with it, the absence of a guard function for
    the calendar tables. The expiry condition is unchanged: the first *per-caller* predicate on any
    of these tables ends the exemption.

    No `ondelete` — i.e. `NO ACTION`: the database refuses to delete a calendar a location still
    points at. Deleting a calendar is out of scope (there is no delete path in this task at all),
    and refusing pre-empts no later decision about referential history."""


class CatalogEngagementType(_CatalogDimension):
    """"Engagement type" is not defined in the requirements (named in Issue #5). In this task it is
    a label with no behaviour: it takes part in the rate key and in nothing else. Its cost
    consequences (overtime, stand-by, contractor vs. employee) are F-07/F-08."""

    __tablename__ = "catalog_engagement_types"
    __table_args__ = _dimension_table_args("catalog_engagement_types")


VENDOR_KEY_SENTINEL = "00000000-0000-0000-0000-000000000000"
"""The nil UUID, used by the `EXCLUDE` key to stand for "no vendor" — and refused as a vendor id.

Why a sentinel at all: an `EXCLUDE` constraint reports a violation only when *every* operator in the
key yields `TRUE`, and `NULL = NULL` yields `NULL`. Adding `vendor_id` to the key as a plain column
would therefore switch the overlap protection off for exactly the rows that have it today — the
internal rates, where the column is `NULL` (ADR-0008, addendum 2026-09-21, point 3; criterion K-02).

Why it cannot collide with a real vendor: `CatalogVendor` carries a CHECK refusing this id, so
"internal" and "some vendor" are distinguishable by construction rather than by the odds of
`uuid4()` producing the nil UUID."""

VENDOR_KEY_EXPRESSION = f"COALESCE(vendor_id, '{VENDOR_KEY_SENTINEL}'::uuid)"
"""The fifth element of the `EXCLUDE` key, as SQL. Spelled once here, once in migration
`c1a4f7b92e05`, and asserted identical by
`test_the_model_and_the_migration_agree_on_every_sql_expression`."""


class CatalogVendor(_CatalogDimension):
    """A subcontractor whose rates the catalogue may carry (SC-2-03, Issue #46).

    The fifth dictionary, not a fifth mechanism: same two columns, same normalised-name index, same
    pair of endpoints, same `CATALOG_READ`/`CATALOG_WRITE` gates. That was a gate-1 decision with an
    expiry condition attached (Issue #46, decision 2): **the first attribute beyond `name`** — a
    currency, a contract, a confidentiality flag — takes vendors out of the shared endpoints and
    needs its own dated decision.

    `name` is expected to identify a company. Nothing here enforces that: a sole-trader (JDG)
    subcontractor entered as a person's name makes this row personal data, and no mechanism on
    this table or its callers catches that case (security review, 2026-09-21). Do not treat this
    table as personal-data-free by construction in a future audit — treat it as unclassified.

    The extra CHECK is the other half of `VENDOR_KEY_SENTINEL`: an id equal to the nil UUID would be
    indistinguishable from "no vendor" inside the `EXCLUDE` key.
    """

    __tablename__ = "catalog_vendors"
    __table_args__ = (
        *_dimension_table_args("catalog_vendors"),
        CheckConstraint(
            f"id <> '{VENDOR_KEY_SENTINEL}'::uuid", name="id_is_not_the_exclude_sentinel"
        ),
    )


class CatalogCostCategory(_CatalogDimension):
    """A category of additional cost — recruitment, hardware, licences, cloud… (F-08, SC-5-05).

    **Another dictionary, not another mechanism** (ADR-0014, point 2; ADR-0005, aneks 2026-09-23
    SC-5-05, point 3): the same id/name/timestamps base, the same normalised-name index, the same
    shared pair of endpoints (`app.data.catalog.DIMENSION_MODELS`) and the same
    `CATALOG_READ`/`CATALOG_WRITE` gates as the other dictionaries. No project, user or tenant
    column, so the catalogue's scope exception holds for it unchanged.

    **A label and nothing else** (ADR-0014, point 2, Q-4 = A): no amount, no default price, no
    snapshot. Renaming a category after an approval renames it on the approved scenario too — the
    accepted limitation of ADR-0004's group 1 (aneks SC-5-05, point 2). A default price per category
    would be a consumer of ADR-0008 and needs its own decision (ADR-0008, aneks SC-5-05, point 3).

    **Not deletable while any cost points at it**: `additional_cost.category_id` is a foreign key
    with no `ON DELETE` action (`NO ACTION`), and no endpoint deletes a dictionary entry anyway.

    **Not seeded** by the migration that creates it (ADR-0014, point 2; the precedent of ADR-0012,
    point 3): the eight categories F-08 lists are data an organisation enters, not code.
    """

    __tablename__ = "catalog_cost_categories"
    __table_args__ = _dimension_table_args("catalog_cost_categories")


RATE_DIMENSION_COLUMNS: tuple[str, ...] = (
    "role_id",
    "seniority_id",
    "location_id",
    "engagement_type_id",
)
"""The **business dimensions** of a rate — the four `NOT NULL` columns that say *what* is priced.

Unchanged by SC-2-03, and deliberately no longer the whole key of the `EXCLUDE` constraint: see
`RATE_EXCLUDE_KEY` for that. All four are `NOT NULL`, because a nullable dimension would mean "any",
which is a second, unnamed resolution mechanism on top of the date window. `vendor_id` is nullable
precisely because it answers a different question — *whose* price this is — and `NULL` there means
one specific thing ("the organisation's own"), never "any" (criteria K-03/K-04).

As data, because it is both the tuple of the resolution lookup and the first four elements of the
constraint key, and those must never disagree."""

RATE_EXCLUDE_KEY: tuple[str, ...] = (*RATE_DIMENSION_COLUMNS, VENDOR_KEY_EXPRESSION)
"""What the database treats as "the same rate": the four business dimensions plus the vendor.

Five elements, four of them columns and the fifth an expression (`VENDOR_KEY_EXPRESSION`). The
vendor joined the key in SC-2-03 because the criterion of ADR-0008 point 4 — "one tuple has at most
one rate at a time" — is simply false once subcontractors exist: the same tuple legitimately carries
the internal rate and one rate per vendor at once (ADR-0008, addendum 2026-09-21, points 1-2)."""

VALID_PERIOD_EXPRESSION = "daterange(effective_from, (effective_to + 1), '[)')"
"""The **only** place `effective_to`'s inclusiveness is converted to PostgreSQL's canonical
half-open form (ADR-0008, point 3). `effective_to + 1` on a `NULL` yields `NULL`, which `daterange`
reads as "unbounded above" — so an open-ended window needs no sentinel date.

Spelled once and used twice: here, to build the generated column, and in the migration that creates
it. A second copy inside a `WHERE` clause is what ADR-0008 forbids — hence every lookup asks
`valid_period @> :on_date` instead of rebuilding the range."""

RATE_PAGE_INDEX = "ix_catalog_default_rates_effective_from_id"
"""The btree index that makes one page of `GET /catalog/rates` a bounded top-N (R-01, gate-2
review 2026-09-21).

Before it, this table carried two indexes and neither could order a page: the primary key (on `id`
alone) and the `EXCLUDE`'s gist index (leading on the four dimension columns and the vendor
expression). `ORDER BY effective_from DESC, id DESC LIMIT n` therefore had to sort *every* filtered
row before the `LIMIT` could cut, so the response shrank while the server-side cost stayed linear in
the size of the catalogue — the `LIMIT` bounded what was sent, not what was done.

**Ascending, although the page is read descending.** A btree is scannable in both directions
(`Index Scan Backward`), so one ascending index serves the descending page order as well as a
`DESC, DESC` index would, and it also serves any later ascending reader — two indexes for two
directions of the same key would be two objects to maintain for one ordering.

`(effective_from, id)` and not `(effective_from)` alone: the tie-breaker is part of the order
`list_rates` pages by (`app.data.catalog.list_rates`), and an index on the leading column only
leaves the rows sharing one `effective_from` to be sorted after the scan — which is the whole
top-N property, lost on exactly the rows that need it.

What it does **not** bound: the `on_date` variant. `valid_period @> :date` is a containment
predicate no btree can answer, so with a filter this index provides the *order* (the scan stops as
soon as the page is full) but the number of rows walked to fill the page depends on how selective
the filter is. And `total` is a count of the filtered set — unbounded by construction, whichever
statement produces it."""

NO_OVERLAP_CONSTRAINT = "ex_catalog_default_rates_no_overlapping_periods"
"""Name of the `EXCLUDE USING gist` constraint, spelled once.

Referenced by the tests that prove a refusal came from *this* mechanism rather than from something
else that happened to fail, so a rename is a single edit rather than a search."""


class CatalogDefaultRate(Base):
    """One default cost/selling rate for one dimension tuple over one effective period (F-03).

    Cost and selling rate share the row (gate-1 decision 6): two columns, one row, so denying the
    cost rate stays what SC-1-08 proved it is — a removed *field*, with the row and the selling rate
    still present (ADR-0005, addendum 2026-09-19, point 5). Splitting them into two tables would
    turn the same denial into a hidden row, and "this tuple has no rate" would become
    indistinguishable from "you may not see its cost".
    """

    __tablename__ = "catalog_default_rates"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # No `ondelete` — i.e. `NO ACTION`: the database refuses to delete a dimension entry a rate
    # still references. Deleting or deactivating a dimension entry that is in use is explicitly out
    # of scope for SC-2-01 (it needs a decision about referential history, ADR-0004), and refusing
    # is the option that pre-empts neither answer.
    #
    # The four foreign keys carry explicit names rather than the ones
    # `NAMING_CONVENTION["fk"]` derives: for `engagement_type_id` that derivation is 66 characters,
    # so PostgreSQL's 63-character identifier limit makes SQLAlchemy truncate it and append a hash
    # (`…_catalog_eng_0ab4`). A name with a hash in it has to be copied into the migration
    # character by character and cannot be read back to its meaning — these say what they are.
    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_roles.id", name="fk_catalog_default_rates_role_id"),
        nullable=False,
    )
    seniority_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_seniorities.id", name="fk_catalog_default_rates_seniority_id"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_locations.id", name="fk_catalog_default_rates_location_id"),
        nullable=False,
    )
    engagement_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "catalog_engagement_types.id", name="fk_catalog_default_rates_engagement_type_id"
        ),
        nullable=False,
    )

    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_vendors.id", name="fk_catalog_default_rates_vendor_id"),
        nullable=True,
    )
    """Whose price this is: a subcontractor, or the organisation itself when `NULL` (SC-2-03).

    **`NULL` means "internal", never "any".** It is a value of the vendor axis, not an absent one,
    which is why the `EXCLUDE` key reads it through `COALESCE` (`VENDOR_KEY_EXPRESSION`) and why the
    resolution lookup filters on `vendor_id IS NULL` when a caller names no vendor. Read as "any",
    it would be a second resolution mechanism — and the concrete failure is immediate, not
    theoretical: a tuple priced both internally and by one vendor would make `one_or_none()` raise
    on perfectly valid data (criteria K-03/K-04).

    Nullable, unlike the four dimensions above, and for the opposite reason: a nullable *dimension*
    would mean "any", while a nullable vendor names the one case that has no vendor. It is the only
    column of this table that points at a business entity rather than at a label, and ADR-0001's
    addendum of 2026-09-21 (point 1) decides explicitly that this does not make the row scoped: a
    subcontractor is a counterparty, not a subject the caller acts for.

    No `ondelete`, like the other four foreign keys: the database refuses to delete a vendor a rate
    still references (Issue #46, "Out of scope" point 9)."""

    # `NUMERIC` → `Decimal`, never float, on a money path or next to one (NF-01, ADR-0002).
    default_cost_rate: Mapped[Decimal] = mapped_column(
        Numeric(RATE_PRECISION, RATE_SCALE), nullable=False
    )
    """The personnel cost of an hour of this tuple — personal-ish data in the sense of NF-11/AC-06,
    and the field the catalogue's cost gate removes. `NOT NULL`: the column is the *default* the
    calculation falls back to, so a row without one is a row that cannot answer the question it
    exists for. Its visibility is a property of the response, not of the column (ADR-0005, addendum
    2026-09-19, point 5) — see `app.api.response_shaping.shape_catalog_rate`."""

    default_selling_rate: Mapped[Decimal] = mapped_column(
        Numeric(RATE_PRECISION, RATE_SCALE), nullable=False
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    """ISO-4217 alphabetic code as a string — not an enum and not a foreign key (ADR-0006), so
    adding a currency needs no migration. Deliberately **not** part of the `EXCLUDE` key (ADR-0008,
    point 4): one tuple has at most one rate at a time, in one currency, and conversion goes through
    `exchange_rates`, never through parallel rows in several currencies."""

    unit: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=RATE_UNIT_HOUR, default=RATE_UNIT_HOUR
    )

    # Calendar dates, not points in time (invariant-guardian rule 15): "this rate applies from
    # 1 March" has no timezone.
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    """`NULL` means open-ended, not a `9999-12-31` sentinel (ADR-0008, point 2). **Inclusive** in
    the API and in the data: "valid to 31 December" means the 31st is covered. The conversion to the
    half-open form PostgreSQL canonicalises to happens in exactly one place — the `valid_period`
    expression below — and must not be repeated in any query (ADR-0008, point 3)."""

    valid_period: Mapped[Range[date]] = mapped_column(
        DATERANGE,
        Computed(VALID_PERIOD_EXPRESSION, persisted=True),
        nullable=False,
    )
    """The one representation of the window, generated by the database and read by both the
    `EXCLUDE` constraint and the resolution lookup (`valid_period @> :on_date`).

    Read-only from the ORM's point of view — `Computed`, never assigned on insert (ADR-0008,
    "Konsekwencje"). The point of the generated column is that the constraint and the lookup cannot
    drift apart about where the boundary is: repeating `daterange(...)` in the query would be the
    same expression in two places, and a mismatch of one day is invisible to any test that asks
    about a date in the middle of a window."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    """ADR-0007's concurrency marker for a rate window (SC-2-04, Issue #49, decision Q-1).

    The `EXCLUDE` constraint above does **not** cover an edit: changing only the amount or the
    currency of an existing row touches no element of that key, so two people editing one rate from
    one read would both succeed and the last one would win silently. That is what this column is
    for, and it is the same mechanism as `Project.updated_at`/`StaffingPosition.updated_at` rather
    than a third one (ADR-0007, "Konsekwencje": reuse, do not invent per entity).

    Not an audit column and not to be grown into one: it says *when* the row last changed, never by
    whom. A "who" column would tie a catalogue row to a user and expire the structural exception
    that keeps these six tables outside the `project_access` scope filter — see
    `CONCURRENCY_MARKER_COLUMN`."""

    # Integrity in the database, not in application code (ADR-0001, invariant-guardian rule 13).
    #
    # The `EXCLUDE` is the whole guarantee that a lookup never has to choose between two rows: with
    # it, "latest row wins" is not a policy that was rejected — it is a situation that cannot arise.
    # A Python check-then-act equivalent has already survived delivered tests twice in this
    # repository (SC-1-02, SC-1-04), which is why ADR-0008 rejects it by name.
    #
    # `btree_gist` is what allows `=` on the five `uuid` key elements inside a gist index (`&&` on a
    # `daterange` is native). The extension is created by migration `7b3d5c81e40a`, and the database
    # privilege that needs is documented in `backend/README.md` (ADR-0008, points 5 and 7 — the
    # dependency grew with the fifth element and is still unproven on any target environment).
    __table_args__ = (
        CheckConstraint(f"unit = '{RATE_UNIT_HOUR}'", name="unit_is_hour"),
        CheckConstraint("char_length(currency) = 3", name="currency_iso4217"),
        # ISO 4217 codes are upper-case by definition; same class of defect as `unit` above —
        # `"eur"` written through a path that skips `Iso4217Code` must not create a second
        # spelling of one currency.
        CheckConstraint("currency = upper(currency)", name="currency_is_upper"),
        # Load-bearing for the EXCLUDE below it, not merely tidy: with `effective_to` one day
        # *before* `effective_from`, `daterange` yields an *empty* range, and `&&` against an empty
        # range is false for everything — the overlap constraint would silently stop applying to
        # that row. Ordering the pair is what keeps every window non-empty.
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_period_ordered",
        ),
        # Five elements, and the fifth is an expression rather than a column (`RATE_EXCLUDE_KEY`):
        # `COALESCE(vendor_id, '00000000-0000-0000-0000-000000000000'::uuid)` — the literal
        # `VENDOR_KEY_SENTINEL`, not `uuid_nil()`. `uuid_nil()` would return the identical value
        # but needs the `uuid-ossp` extension, which this database does not have (only
        # `btree_gist`); the literal is what `VENDOR_KEY_EXPRESSION` and the migration actually
        # write. Either way, `NULL = NULL` is not `TRUE`, and a plain nullable column in the key
        # would exempt every internal rate from the guarantee (K-02).
        ExcludeConstraint(
            *[
                (text(element) if element == VENDOR_KEY_EXPRESSION else element, "=")
                for element in RATE_EXCLUDE_KEY
            ],
            ("valid_period", "&&"),
            using="gist",
            name=NO_OVERLAP_CONSTRAINT,
        ),
        # Not an integrity constraint — the one index this table has for *reading* (R-01). Declared
        # here as well as created by migration `e2c7b04d9a31`, so the model keeps describing the
        # database that exists: an index absent from the model is one a reader of this file cannot
        # know a query may depend on. See `RATE_PAGE_INDEX`.
        Index(RATE_PAGE_INDEX, "effective_from", "id"),
    )


# --- working calendars and absence types (F-05, SC-3-02) -----------------------------------------


STANDARD_HOURS_PRECISION = 4
STANDARD_HOURS_SCALE = 2
"""`NUMERIC(4,2)` for the length of a standard working day — `Decimal`, never `float`.

The same rule as for the hours of an allocation row (`app.models.staffing.HOURS_PRECISION`), and
for the same reason: this figure is multiplied by a number of days and then by a rate, so a binary
rounding error here reaches every cost and revenue figure derived from a calendar (NF-01,
ADR-0002). Precision 4 because a working day is a one- or two-digit number of hours; scale 2
because 7.5 and 8.25 are what a real calendar is written in."""

WEEK_PATTERN_LENGTH = 7

WEEK_PATTERN_EXPRESSION = "week_pattern ~ '^[01]{7}$'"
"""What makes `week_pattern` a week, as SQL — seven characters, each `0` or `1`.

Monday first (index 0 is `date.weekday() == 0`), so the string reads the way a European calendar is
printed. A pattern is **data, not code** (NF-10): a calendar working Monday to Saturday is
`'1111110'`, an insert rather than a migration, and there is no `weekday() < 5` anywhere in this
codebase to disagree with it (criterion K-02, mutation b).

Spelled once here and once in the migration that creates the table, and asserted identical to that
copy by `tests/test_working_calendar_schema_constraints.py` — the drift guard R-02 introduced for
the catalogue's generated column."""


class WorkingCalendarDayKind(StrEnum):
    """What an exceptional day *does* to the week pattern — the two directions F-05 names.

    `NON_WORKING` removes a working day the pattern would have given (a public holiday);
    `WORKING` adds one the pattern would not have (a working Saturday). Two values rather than a
    boolean `is_holiday`, because the second direction is a requirement and not a negation: a
    calendar without it cannot express a working Saturday at all (criterion K-02).

    Deliberately **not** a third value for "partially working": a column of hours on this row would
    be a second mechanism overriding the calendar's own basis, and ADR-0008's addendum of 2026-09-22
    (point 6) puts it explicitly out of scope, needing its own dated entry.
    """

    NON_WORKING = "non_working"
    WORKING = "working"


class WorkingCalendar(_CatalogDimension):
    """One working calendar: a name, a standard working day and a week pattern (F-05, SC-3-02).

    The sixth dictionary of the catalogue, not a sixth mechanism (ADR-0005, addendum 2026-09-22,
    point 3): same id/name/timestamps base, same normalised-name uniqueness, same
    `CATALOG_READ`/`CATALOG_WRITE` pair, no new permission.

    **`standard_hours_per_day` carries no effective-date window, and that is a decision** (ADR-0008,
    addendum 2026-09-22). The unit of versioning is the calendar, not the column: an organisation
    that changes the length of its working day creates a new calendar and repoints locations at it.
    A window on this column would be a *second* mechanism resolving by date, next to the set of days
    the calendar already holds, and rule 13 of the Invariant Guardian exists to keep exactly one.
    The reproducibility a window would have served is served by the approval snapshot instead
    (`app.models.approved_snapshot`), structurally and more strongly. The expiry condition is dated:
    the first request for two different day lengths under **one** calendar name ends this and needs
    the `effective_from`/`effective_to` + `EXCLUDE` pattern on a child table.
    """

    __tablename__ = "working_calendar"

    standard_hours_per_day: Mapped[Decimal] = mapped_column(
        Numeric(STANDARD_HOURS_PRECISION, STANDARD_HOURS_SCALE), nullable=False
    )
    """How many hours one working day of this calendar is. `NOT NULL`: a calendar without it cannot
    answer the question it exists for, and defaulting it to 8 in the column would be the silent
    constant criterion K-01's mutation is about."""

    week_pattern: Mapped[str] = mapped_column(String(WEEK_PATTERN_LENGTH), nullable=False)
    """Which days of the week are working days, Monday first — see `WEEK_PATTERN_EXPRESSION`."""

    days: Mapped[list["WorkingCalendarDay"]] = relationship(
        back_populates="calendar",
        order_by="WorkingCalendarDay.day",
        cascade="all, delete-orphan",
        passive_deletes=False,
    )
    """The exceptional days, ordered — so a calendar read twice comes back the same way."""

    __table_args__ = (
        *_dimension_table_args("working_calendar"),
        # In the database, not only in a request schema: a zero-hour working day is a calendar that
        # silently makes every capacity zero, which is the failure K-23 refuses to let happen by
        # accident even when it is spelled as data.
        CheckConstraint("standard_hours_per_day > 0", name="standard_hours_per_day_positive"),
        CheckConstraint(WEEK_PATTERN_EXPRESSION, name="week_pattern_is_seven_flags"),
    )


class WorkingCalendarDay(Base):
    """One exceptional day of one calendar: a holiday removed or a working day added (F-05).

    **Data, not code** (NF-10): which days are holidays is a set of rows, not a Python list and not
    a library of national calendars. That is the whole of criterion K-02's first half — a holiday is
    an `INSERT`, and removing this table from the reading path changes an answer.
    """

    __tablename__ = "working_calendar_day"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("working_calendar.id", name="fk_working_calendar_day_calendar_id"),
        nullable=False,
    )
    """No `index=True`: `UNIQUE (calendar_id, day)` below already creates a btree whose leading
    column is this one, and "the days of one calendar" is the only lookup this table has (the R-05
    argument the allocation row carries)."""

    # A calendar date, not a point in time (invariant-guardian rule 15): "25 December is a holiday"
    # has no timezone and no clock.
    day: Mapped[date] = mapped_column(Date, nullable=False)

    kind: Mapped[WorkingCalendarDayKind] = mapped_column(
        Enum(
            WorkingCalendarDayKind,
            name="working_calendar_day_kind",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    """ADR-0007's marker, present from the table's creation although SC-3-02 ships no form for it
    (addendum 2026-09-22, point 3): the addendum of 2026-09-21 rejected "which tables have a marker"
    as a second rule to remember at every later form, so every organisational table gets one."""

    calendar: Mapped["WorkingCalendar"] = relationship(back_populates="days")

    __table_args__ = (
        # One row per (calendar, day) — in the database, because two rows naming one day are two
        # answers to "is this a working day?" and nothing downstream could choose between them. A
        # `SELECT` before the `INSERT` would be check-then-act and two connections would both pass
        # it (criterion K-03; the mutation that has survived delivered tests three times here).
        UniqueConstraint("calendar_id", "day"),
    )


STATUTORY_LEAVE_UNIQUE_INDEX = "uq_absence_type_statutory_leave"
"""Name of the partial unique index that keeps `is_statutory_leave` true on **at most one** row.

Spelled once, referenced by the tests that prove a refusal came from *this* mechanism and by the
migration that creates it (ADR-0008, addendum 2026-09-22 SC-3-03, point 8a)."""

STATUTORY_LEAVE_INDEX_EXPRESSION = "(true)"
STATUTORY_LEAVE_INDEX_PREDICATE = "is_statutory_leave"
"""`CREATE UNIQUE INDEX … ON absence_type ((true)) WHERE is_statutory_leave`, as SQL.

A unique index on a **constant** expression, restricted to the flagged rows: two flagged rows would
both index the value `true`, and the second one is refused **in the statement that inserts it**.
That is the whole reason this is an index and not a `SELECT count(*)` in Python — a Python check is
check-then-act, which has survived delivered tests three times in this repository (SC-1-02 ×2,
SC-2-01) and which ADR-0008's addendum (point 8a) rejects here by name.

What it cannot express is the other half — "at least one" — and that half is deliberately a *named
state* rather than a constraint (point 8b): a catalogue with no statutory type answers "not
named", never a guess and never a silent `0`.

Spelled here and in the migration, and compared by the drift guard in
`tests/test_absence_budget_schema_constraints.py`."""


class AbsenceType(_CatalogDimension):
    """A kind of absence — holiday, sick leave, training — with its two commercial flags (F-05).

    The seventh dictionary of the catalogue (ADR-0005, addendum 2026-09-22, point 3). Two booleans,
    and they are **independent of each other**: paid holiday generates cost and no revenue, billable
    training may generate both, unpaid leave neither. Deriving one from the other (or storing one
    flag and a sign) is the mutation criterion K-11 exists to kill.

    **Nothing in SC-3-02 reads these flags.** They are persisted and returned, and no cost or
    revenue calculation consults them, because there is no cost or revenue calculation yet (F-07 /
    F-08, plan block 5). K-11 therefore proves persistence and independence and *not* that an
    absence costs anything — the task that first prices an absence has to prove that itself.

    **No column about a person.** The type is a dictionary entry; who is absent is a question this
    table and `staffing_position_absence` both refuse to carry (ADR-0005, addendum 2026-09-22,
    point 6).
    """

    __tablename__ = "absence_type"

    generates_cost: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    """Whether time booked against this type still costs the organisation money (paid leave does).
    `NOT NULL` with a default of false: an unanswered flag must not read as "yes"."""

    generates_revenue: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    """Whether time booked against this type is still billable to the client. Independent of
    `generates_cost` above — see the class docstring."""

    is_statutory_leave: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    """Which type the **absence budget** is settled against (F-05, SC-3-03).

    A flag on the dictionary, not a column on the budget row (ADR-0008, addendum 2026-09-22 SC-3-03,
    point 8, a human's decision): the budget's key stays `(calendar_id, engagement_type_id)` and the
    question "what does this budget count against" is answered by the catalogue. The rejected
    alternative is named there too — a type *name* hard-coded in Python, which NF-10 and the
    `WEEK_PATTERN_EXPRESSION` precedent ("a pattern is data, not code") exclude in advance.

    **At most one row may carry it, and the database says so** — `STATUTORY_LEAVE_UNIQUE_INDEX`.
    **At least one is not enforceable that way and is a named state instead** (point 8b): with no
    flagged row the answer is "no statutory leave type named", never the first type alphabetically,
    never one whose name contains "leave", never a silent `0`.

    `NOT NULL` with a `false` default, like the two flags above: an unanswered flag must not read as
    "yes", and the column can be added to a populated table without a backfill (expand-only).

    **No effective-date window, and the consequence is accepted rather than unnoticed** (point 8c):
    moving the flag to another type changes what every budget row counts against, at once and
    without a trace. Draft scenarios pick that up silently, exactly as they pick up any catalogue
    edit; approved ones are protected only because the flag enters the approval snapshot
    (ADR-0004, addendum 2026-09-22 SC-3-03, point 8)."""

    __table_args__ = (
        *_dimension_table_args("absence_type"),
        # Declared here as well as created by the migration, so the model keeps describing the
        # database that exists. A partial unique index on a constant expression — see
        # `STATUTORY_LEAVE_INDEX_EXPRESSION` for why it is an index and not a Python check.
        Index(
            STATUTORY_LEAVE_UNIQUE_INDEX,
            text(STATUTORY_LEAVE_INDEX_EXPRESSION),
            unique=True,
            postgresql_where=text(STATUTORY_LEAVE_INDEX_PREDICATE),
        ),
    )


# --- the absence budget (F-05, SC-3-03) ----------------------------------------------------------


BUDGET_PRECISION = 6
BUDGET_SCALE = 2
"""`NUMERIC(6,2)` for a number of budgeted days — `Decimal`, never `float` (NF-01, ADR-0002).

The same reasoning as for `standard_hours_per_day`, and ADR-0008's addendum of 2026-09-22 (SC-3-03,
point 5) states it for this column: the figure is one multiplication away from money — days × the
calendar's standard day × a rate — so a binary rounding error here reaches every cost and revenue
figure derived from it. Scale 2 because half-days are real; precision 6 because a year has fewer
than 400 working days and a budget is not an amount.

Nothing rounds the stored value on the way in (point 5, quoting point 6 of the decision): rounding
is the consumer's rule, applied once, through `app.core.money.round_money`."""

BUDGET_UNIT_DAY = "day"
"""The only unit a budget row may carry, enforced by a CHECK in the database.

ADR-0008's addendum (SC-3-03, point 5) requires the unit to be **named and enforced**, not inferred
from the size of the number: "20" read as days and "20" read as FTE-days are two different answers
from one row, and no test on a value from the middle of the range would notice. A constant rather
than a one-member enum, exactly as `RATE_UNIT_HOUR` — F-07 may add FTE, and an enum with one member
invites reading the unit as decoration."""

ABSENCE_BUDGET_KEY_COLUMNS: tuple[str, ...] = ("calendar_id", "engagement_type_id")
"""What the database treats as "the same budget": the calendar and the engagement type, and those
two only (ADR-0008, addendum 2026-09-22 SC-3-03, point 3 — gate-1 decision Q-2).

Both `NOT NULL`. A nullable column in this key would mean "any", i.e. a second, unnamed resolution
mechanism laid on top of the date window — the argument `RATE_DIMENSION_COLUMNS` makes for its four
dimensions, and the mutation criterion K-08 kills.

**No `location_id`**, and that is a decision with an expiry condition (point 3b): locations pointing
at one calendar share its budget, and "this location has no calendar" is therefore also "this
location has no budget" — one named state to show, not two independent ones. The first request for
two different budgets under one calendar re-opens it, and re-opening it is a rebuild of the
`EXCLUDE` constraint, i.e. a migration with no expand/contract pair.

As data, because it is both the tuple of the resolution lookup and the first two elements of the
constraint key, and those must never disagree."""

ABSENCE_BUDGET_NO_OVERLAP_CONSTRAINT = "ex_absence_budget_no_overlapping_periods"
"""Name of this table's `EXCLUDE USING gist` constraint, spelled once — the second such constraint
in this repository (ADR-0008, addendum SC-3-03, points 1 and 11), and the second table depending on
`btree_gist`."""

BUDGET_WINDOW_MONTH_ALIGNED_EXPRESSION = (
    "effective_from = date_trunc('month', effective_from)"
    " AND (effective_to + 1) = date_trunc('month', (effective_to + 1))"
)
"""A budget window begins on the first of a month and ends on the last day of one, as SQL.

**The invariant of ADR-0008's addendum (SC-3-03, point 10a) made structural**: the monthly proration
is "budget days ÷ months of the window", and its required property is that the shares of all the
months of a window add back up to the budget. With a window that starts on 15 January, "how many
months is that" has several defensible answers and each produces a different monthly figure — so
instead of choosing one in the arithmetic, the shape that raises the question is refused. The
denominator is then a count of whole months and the sum is exact by construction, for every window
the table can hold.

`(effective_to + 1)` rather than a second `date_trunc` idiom: it is the same "day after the end"
the generated `valid_period` uses (`VALID_PERIOD_EXPRESSION`), so the two cannot disagree about
where a window ends. On a `NULL` `effective_to` the comparison is `NULL`, i.e. this CHECK passes —
the closed-window rule is a *separate*, named constraint (`effective_to_is_closed`), because they
are two independent claims and merging them would make one refusal answer for both.

Spelled once here and once in the migration, and compared by the drift guard in
`tests/test_absence_budget_schema_constraints.py` (the R-02 mechanism)."""


class AbsenceBudget(Base):
    """How many days of statutory leave one calendar regime plus one engagement type carries (F-05).

    The **eighth** table of the catalogue and the **fourth** consumer of ADR-0008's effective-range
    pattern — the second one actually built (addendum 2026-09-22, SC-3-03, points 1-2). Same
    columns, same generated `valid_period`, same `EXCLUDE USING gist`, same `btree_gist` dependency
    as `CatalogDefaultRate`, with exactly one narrowing: a window here may not be open-ended (point
    10b, and see `effective_to`).

    **Organisational data, not cost data** (ADR-0005, addendum 2026-09-22 SC-3-03, points 1-3): no
    column ties a row to a project, a user, a business unit or a tenant, so the exemption from the
    `project_access` filter and from a guard function survives it, and the gate is `CATALOG_READ` /
    `CATALOG_WRITE` with no new permission. A number of days is not an amount and reveals no
    person's cost — the multiplier, a cost rate, is gated separately. **The boundary that comes with
    that** (point 4): the *computed cost* of a budget is a cost field and goes back through the
    SC-1-08 conjunction; nothing in this task computes one.

    **Not a child of a scenario** (ADR-0004, addendum 2026-09-22 SC-3-03, point 1): the row belongs
    to the organisation and can be edited after an approval by somebody who never heard of the
    calculation, which is what puts it in group 1 — inherited, therefore snapshotted
    (`app.models.approved_snapshot.ApprovedSnapshotAbsenceBudget`) — and keeps it out of
    `SCENARIO_CHILD_COPIERS`.
    """

    __tablename__ = "absence_budget"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # No `ondelete` — i.e. `NO ACTION`, like every other foreign key in this module: the database
    # refuses to delete a calendar or an engagement type a budget still references. Deleting either
    # is out of scope, and refusing pre-empts no later decision about referential history.
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("working_calendar.id", name="fk_absence_budget_calendar_id"),
        nullable=False,
    )
    """Which calendar regime this budget belongs to — **not** which location (point 3a).

    The calendar is the unit of the working-time regime (addendum SC-3-02, point 1: "the unit of
    versioning is the calendar, not the column"), and a location points at a calendar rather than
    the other way round."""

    engagement_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "catalog_engagement_types.id", name="fk_absence_budget_engagement_type_id"
        ),
        nullable=False,
    )
    """Which engagement type. `NOT NULL` for the reason the whole key is (`ABSENCE_BUDGET_KEY_
    COLUMNS`): a `NULL` meaning "any type" would resolve a second way, next to the date window."""

    budget_days: Mapped[Decimal] = mapped_column(
        Numeric(BUDGET_PRECISION, BUDGET_SCALE), nullable=False
    )
    """The entitlement over the whole window, in days. `>= 0` and **zero is a legal, meaningful
    value** (point 7): an engagement type with no leave entitlement is a row saying so, and it has
    to stay distinguishable from *no row at all*, which is the named state "no budget"."""

    unit: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=BUDGET_UNIT_DAY, default=BUDGET_UNIT_DAY
    )
    """Always `'day'` today, refused otherwise by a CHECK — see `BUDGET_UNIT_DAY`."""

    source: Mapped[str] = mapped_column(String(500), nullable=False)
    """Where the number comes from: a staff regulation, a clause of a collective agreement, an
    organisational decision (ADR-0008, addendum SC-3-03, point 6).

    **Mandatory and non-blank, in the database** — `NOT NULL` plus `source ~ '[^[:space:]]'`, the
    same spelling of "blank" every name column in this module uses. A number with no named source is
    a number nobody can check, and the refusal has to hold for a fixture, a seed script or an import
    as much as for the API (criterion K-02).

    **It is never the author of the row, and there is no column for one.** A "who entered this"
    column would be the first column tying a catalogue row to a user, and it would expire both the
    scope exemption and the absence of a guard function on these tables (ADR-0005, addendum
    2026-09-21 SC-2-04, point 6) — introducing it needs its own dated entry there, not here.

    **Free text, therefore unclassified with respect to personal data** (point 6b), and the approval
    snapshot copies it verbatim, with no `UPDATE` or `DELETE` path to the copy. ADR-0008's addendum
    made resolving that a precondition of the first write path; ADR-0005's addendum (SC-3-03,
    point 6, a human's decision of 2026-09-22) resolves it as a **named risk without technical
    enforcement**, on the same precedent as `AbsenceType.name` and `CatalogVendor.name`. Do not read
    this column as personal-data-free by construction in a later audit — read it as unclassified,
    exactly as `CatalogVendor` says of its own name. The reopening condition is the erasure
    mechanism: the first task that designs one for `approved_snapshot_*` covers this column and
    `AbsenceType.name` together. See `app.api.catalog.create_absence_budget`."""

    # Calendar dates, not points in time (invariant-guardian rule 15).
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)

    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    """Inclusive, as everywhere in this pattern — and on **this** table it may not be `NULL`.

    The column keeps the shared shape (nullable, with the shared `effective_period_ordered` CHECK
    whose `IS NULL` branch is dead here) and the refusal is a separate, named CHECK:
    `ck_absence_budget_effective_to_is_closed`. That is the one deviation ADR-0008's addendum
    (SC-3-03, point 10b) allows this table, and it is narrow on purpose — the other three tables of
    the pattern keep open-ended windows and `VALID_PERIOD_EXPRESSION` does not change at all.

    Why the deviation exists: the monthly proration divides by the number of months of the window,
    and an open-ended window has no denominator. Rather than giving the *arithmetic* an exception,
    the table loses the shape that would force one.

    The price, accepted deliberately: a budget **expires**, and a scenario planned past the last
    window falls into the named "no budget" state (point 7) — never a silent `0`."""

    valid_period: Mapped[Range[date]] = mapped_column(
        DATERANGE,
        Computed(VALID_PERIOD_EXPRESSION, persisted=True),
        nullable=False,
    )
    """The one representation of the window — the same expression `CatalogDefaultRate` uses, not a
    second one (ADR-0008, point 3). Read by the `EXCLUDE` constraint and by every lookup
    (`valid_period @> :day`), so the constraint and the resolution cannot drift apart by a day."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    """ADR-0007's concurrency marker, present from this table's creation although SC-3-03 ships no
    edit form: the addendum of 2026-09-21 rejected "which tables have a marker" as a second rule to
    remember at every later form, so every organisational table gets one (the argument
    `WorkingCalendarDay.updated_at` already makes)."""

    __table_args__ = (
        CheckConstraint(f"unit = '{BUDGET_UNIT_DAY}'", name="unit_is_day"),
        # Zero is legal (point 7); negative is a sign error with no meaning in any calculation.
        CheckConstraint("budget_days >= 0", name="budget_days_not_negative"),
        # `NOT NULL` alone still admits `''` and `'   '` — the same argument every name column in
        # this module makes, and criterion K-02's first mutation ("drop the non-blank rule from the
        # database and keep the request validation") is exactly this constraint being removed.
        CheckConstraint("source ~ '[^[:space:]]'", name="source_not_blank"),
        # The shared shape, with a branch that is dead on this table (see `effective_to`). Removing
        # that branch would be a second divergence from the pattern where one is enough.
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_period_ordered",
        ),
        # The one named narrowing of the pattern (point 10b): no open-ended budget window.
        CheckConstraint("effective_to IS NOT NULL", name="effective_to_is_closed"),
        CheckConstraint(
            BUDGET_WINDOW_MONTH_ALIGNED_EXPRESSION, name="window_aligned_to_whole_months"
        ),
        # Integrity in the database, never in application code (ADR-0001, rule 13). The `EXCLUDE` is
        # the whole guarantee that a lookup never has to choose between two rows — with it, "the
        # latest row wins" is not a rejected policy but a situation that cannot arise. A Python
        # check-then-act equivalent survives a single-connection test by construction, which is why
        # criterion K-01 requires the two-connection race.
        ExcludeConstraint(
            *[(column, "=") for column in ABSENCE_BUDGET_KEY_COLUMNS],
            ("valid_period", "&&"),
            using="gist",
            name=ABSENCE_BUDGET_NO_OVERLAP_CONSTRAINT,
        ),
    )
