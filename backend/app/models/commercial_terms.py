"""The commercial rule of one scenario, its Time & Material, Story Points, Outcome-based and Fixed
Price details (F-06, F-06.1, F-06.2, F-06.3, F-06.4, F-06.5; SC-4-01, SC-4-02, SC-4-03, SC-4-04,
SC-4-05).

SC-4-05 (Issue #69) adds `scope_ref`, a nullable pointer from a rule to one `scenario_delivery_
segment` of *its own* scenario (ADR-0003 addendum 2026-09-25, D-3=A). It does not add a new entity
("a combined rule" is not a table) and it does not change how a rule's own model prices anything —
it only widens *how many* rows one scenario may have and *which one* each points at. See `scope_ref`
below and `SCENARIO_ID_WHOLE_SCENARIO_UNIQUE`/`SCENARIO_ID_SCOPE_UNIQUE`/`SCOPE_REF_FOREIGN_KEY`.
`scope_ref` lives on `commercial_terms`, not on a details table, so it applies to every model —
`outcome_based` (SC-4-03) included — with no per-model column.

Two details tables, one rule table, and every property below is a decision of ADR-0003 (rewritten
and accepted at gate 1 of SC-4-01, extended by its 2026-09-25 addendum for SC-4-04) rather than a
choice made here. `story_points_terms` (SC-4-04, Issue #68) is the second model to join the
registry, and it is the first real proof that the pattern below generalises: nothing in this module
names `time_and_material` outside the one constant each model owns, and `MODEL_TYPES` /
`DETAIL_TABLE_BY_MODEL` / `REVENUE_BY_MODEL` (`app.data.commercial_terms`) are what a caller reads
to find either model — never a name compared by hand (D-1..D-6 of the addendum, option A
everywhere).

1. **The rule belongs to the scenario** (point 1). `commercial_terms.scenario_id` is `NOT NULL` and
   `UNIQUE` — one rule per scenario in the MVP — and its scope is inherited through
   `scenario_id → scenarios.project_id`, i.e. through `app.data.project_reads.project_for_caller`,
   with no scope function of its own (ADR-0005, addendum 2026-09-23 SC-4-01, point 1).
2. **A discriminator closed to the models that have a details table** (point 2). The CHECK below
   admits `time_and_material`, `story_points` (SC-4-04), `outcome_based` (SC-4-03) and `fixed_price`
   (SC-4-02); each model widens it in the same migration that creates its details table, recreating
   the full `IN` list (ADR-0003, addendum 2026-09-25 SC-4-03, point 10c). `model_type` is immutable
   after the write — there is no edit path for it, and changing a rule's model is out of scope of
   the MVP.
3. **Type agreement is a composite foreign key, not an application check** (point 3; criterion
   K-04). `tm_terms (commercial_terms_id, model_type) → commercial_terms (id, model_type)`, the
   parent side carrying `UNIQUE (id, model_type)` and the child side `CHECK (model_type =
   'time_and_material')`. A T&M details row for a rule of another model is unwritable — the class of
   defect the catalogue already refused once (`app.models.catalog._CatalogDimension`: "a single
   table would make »seniority id in the location column« a valid row"). The **existence** of the
   details row is not enforced by the database (that would need a deferred trigger): a T&M rule
   without it is the named state `incomplete_commercial_terms`, never a revenue of `0`, and the
   write path creates both rows in one statement
   (`app.data.commercial_terms.create_commercial_terms`).
4. **Own data of the scenario → write guard, not snapshot** (ADR-0004, addendum 2026-09-23 SC-4-01,
   point 1). Nothing outside the scenario changes these rows, so the approval freezes nothing of
   them; what protects them after approval is the refusal of a write in the statement that writes
   (`app.data.scenario_guard`), and a copy of the scenario copies them as one aggregate
   (`app.data.commercial_terms.copy_commercial_terms`, one entry in `SCENARIO_CHILD_COPIERS`).

**No `ON DELETE` on any foreign key here**, for the reason `app.models.staffing` gives: a cascade
would be a second, unguarded way for the rows of an `approved` scenario to disappear.

**What is deliberately absent:** a date window (ADR-0008, addendum 2026-09-23 SC-4-01 — one rule per
scenario, versioned by the copy mechanism), any T&M domain column (hour caps, overtime rates, a
billable-day length — ADR-0003 points 7 and "Deferred"), and any rate: the selling rate comes only
from the catalogue (point 4), so nothing on these rows is priced and nothing is a personnel cost.

**SC-4-02 (Fixed Price, F-06.2, Issue #66) adds its model** exactly the way point 2 and
"Konsekwencje" of ADR-0003 foresaw — a details table, a value of the discriminator CHECK and a
branch of the dispatcher, with no other existing table changed (ADR-0003, addendum 2026-09-25
SC-4-02):

5. **`fixed_price_terms` mirrors `tm_terms`**: primary key `commercial_terms_id`, its own `CHECK
   (model_type = 'fixed_price')` and its own composite foreign key to `commercial_terms (id,
   model_type)` (point 1 of the addendum).
6. **A details table with domain columns** (as `outcome_terms`, SC-4-03, is): the agreed price of
   the whole project, `NUMERIC(14,4)` and `NOT NULL`, bounded below by `CHECK (agreed_price >= 0)`
   (D-5 — a revenue of zero, AC-05, is reachable), and its ISO 4217 currency **on the same row** —
   never inferred from `scenarios.currency`, which may be `NULL` (point 2 of the addendum;
   ADR-0002).
7. **Still no `updated_at` on the details row.** The ADR-0007 marker of the aggregate stays
   `commercial_terms.updated_at`; the price edit rotates it in the statement that writes the price
   (`app.data.commercial_terms.update_fixed_price`, point 7 of the addendum).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.scenario import Scenario

MODEL_TYPE_TIME_AND_MATERIAL = "time_and_material"
"""The first commercial model (ADR-0003, point 2; SC-4-01)."""

MODEL_TYPE_STORY_POINTS = "story_points"
"""The second commercial model (SC-4-04, Issue #68; ADR-0003 addendum 2026-09-25, D-1/A): a single
shape — price per point × accepted points, no "sprint fee" variant (out of scope)."""

MODEL_TYPE_OUTCOME_BASED = "outcome_based"
"""Model Outcome-based (F-06.3; ADR-0003, addendum 2026-09-25 SC-4-03)."""

MODEL_TYPE_FIXED_PRICE = "fixed_price"
"""The Fixed Price commercial model (F-06.2; SC-4-02, Issue #66; ADR-0003, addendum 2026-09-25
SC-4-02)."""

MODEL_TYPES: tuple[str, ...] = (
    MODEL_TYPE_TIME_AND_MATERIAL,
    MODEL_TYPE_STORY_POINTS,
    MODEL_TYPE_OUTCOME_BASED,
    MODEL_TYPE_FIXED_PRICE,
)
"""Every model with a details table, as data — the set the discriminator CHECK admits.

Growing this tuple is a migration, not an edit: the CHECK in the database is the rule, and the task
adding a model adds its details table, its value here and its branch of the revenue dispatcher
(`app.data.commercial_terms.REVENUE_BY_MODEL`) together."""

MODEL_TYPE_KNOWN_EXPRESSION = (
    "model_type IN ('time_and_material', 'story_points', 'outcome_based', 'fixed_price')"
)
"""The discriminator CHECK as SQL — spelled here and in the migration that last recreated it
(`b8f2d6a41c93`, SC-4-02, re-parented on 2026-09-28 onto `c4d7e2a9b1f6`; before it `b9e3c7a1f264`,
SC-4-03, `d2f6a91c4b58`, SC-4-04, and `e7b41c9d2a58`, SC-4-01), and asserted identical to that copy
by `tests/test_commercial_terms_schema.py` (`LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH`, the drift
guard R-02 introduced for the catalogue). The constraint keeps its name,
`ck_commercial_terms_model_type_known`, across every widening.

**The full `IN` list, not only the latest model's value** (ADR-0003, addendum 2026-09-25 SC-4-03,
point 10c): a migration carrying only its own value would silently invalidate the saved rules of
earlier models at the next validation of the constraint."""

TM_MODEL_TYPE_EXPRESSION = f"model_type = '{MODEL_TYPE_TIME_AND_MATERIAL}'"
SP_MODEL_TYPE_EXPRESSION = f"model_type = '{MODEL_TYPE_STORY_POINTS}'"

OUTCOME_MODEL_TYPE_EXPRESSION = f"model_type = '{MODEL_TYPE_OUTCOME_BASED}'"

MODEL_TYPE_LENGTH = 40

OUTCOME_CATEGORIES: tuple[str, ...] = ("not_achieved", "partial", "achieved", "exceeded")
"""Four fixed outcome categories (ADR-0003, addendum 2026-09-25 SC-4-03, point 3), from the worst.
Each is a pair of `outcome_terms` columns: `<category>_units` and `<category>_probability`."""


def units_column(category: str) -> str:
    """The name of a category's units-count column — one spelling for the model, the write and the
    read."""
    return f"{category}_units"


def probability_column(category: str) -> str:
    """The name of a category's probability column — one spelling for the model, the write and the
    read."""
    return f"{category}_probability"


OUTCOME_AMOUNT_PRECISION = 14
OUTCOME_AMOUNT_SCALE = 4
"""`NUMERIC(14,4)` for the rule's amounts and unit rate — the same scale as the catalogue's rates
and additional costs (ADR-0008, point 6). Rounding to the currency unit belongs to the calculation
(`app.core.money.round_money`), never to the write."""

OUTCOME_UNITS_PRECISION = 14
OUTCOME_UNITS_SCALE = 4

PROBABILITY_PRECISION = 5
PROBABILITY_SCALE = 2
"""`NUMERIC(5,2)` — percentages with two decimal places (ADR-0003, addendum SC-4-03, point 4)."""

OUTCOME_PROBABILITIES_EXPRESSION = (
    "("
    + " AND ".join(f"{probability_column(c)} IS NULL" for c in OUTCOME_CATEGORIES)
    + ") OR ("
    + " AND ".join(f"{probability_column(c)} IS NOT NULL" for c in OUTCOME_CATEGORIES)
    + " AND "
    + " + ".join(probability_column(c) for c in OUTCOME_CATEGORIES)
    + " = 100)"
)
""""All four `NULL` or all set and the sum exactly 100" — a single-row `CHECK` (ADR-0003, addendum
SC-4-03, points 3-4). No tolerance: `NUMERIC` compares exactly."""

OUTCOME_UNITS_WITH_UNIT_RATE_EXPRESSION = (
    "unit_rate IS NULL OR ("
    + " AND ".join(f"{units_column(c)} IS NOT NULL" for c in OUTCOME_CATEGORIES)
    + ")"
)
"""The unit counts are inputs to the unit rate (SC-4-03 verification round 2, point 5): with no rate
(`unit_rate IS NULL`) each of the four may be `NULL` — there is nothing to multiply them by, and a
`0` written in for the absence would be a false value; with a rate, all four are `NOT NULL`. A
single-row `CHECK`, so a fixture and an import cannot write a rate with no units either."""

OUTCOME_BOUNDS_ORDERED_EXPRESSION = (
    "revenue_min IS NULL OR revenue_max IS NULL OR revenue_min <= revenue_max"
)
"""`min <= max`, when both are set (point 2). No bound is `NULL`, never `0`."""

OUTCOME_CURRENCY_ISO4217_EXPRESSION = "char_length(currency) = 3"
OUTCOME_CURRENCY_UPPER_EXPRESSION = "currency = upper(currency)"
"""The same two currency rules as in the catalogue
(`ck_catalog_default_rates_currency_*`; point 7)."""

OUTCOME_TYPE_AGREEMENT_FOREIGN_KEY = "fk_outcome_terms_commercial_terms_model_type"
"""The composite foreign key for type agreement of `outcome_terms` — the pattern of ADR-0003
point 3, unchanged."""

TYPE_AGREEMENT_FOREIGN_KEY = "fk_tm_terms_commercial_terms_model_type"
"""The composite foreign key that makes type agreement a property of the database (criterion K-04).

Named explicitly and spelled once, so the test proving a refusal came from *this* mechanism —
and not from the CHECK on the child, which refuses a different row — can assert on the name."""

STORY_POINTS_TYPE_AGREEMENT_FOREIGN_KEY = "fk_story_points_terms_commercial_terms_model_type"
"""The same mechanism as `TYPE_AGREEMENT_FOREIGN_KEY`, for `story_points_terms` (SC-4-04, K-04): a
Story Points details row pointing at a rule of another model is unwritable, by this foreign key."""

SCOPE_REF_FOREIGN_KEY = "fk_commercial_terms_scope_ref_scenario_id"
"""The composite foreign key that makes cross-scenario integrity of `scope_ref` a property of the
database (SC-4-05, Issue #69; criterion K-04) — `(scope_ref, scenario_id) -> scenario_delivery_
segment (id, scenario_id)`, the same construction `TYPE_AGREEMENT_FOREIGN_KEY` already proves for
the model discriminator. `NULL` (the whole-scenario rule) never participates in a foreign key check
— an ordinary SQL rule, not a case this constraint special-cases."""

SCOPE_REF_NULL_EXPRESSION = "scope_ref IS NULL"
SCOPE_REF_NOT_NULL_EXPRESSION = "scope_ref IS NOT NULL"
"""Spelled here as well as in migration `b7e3f19a6c52` rather than imported from it — the same
convention `MODEL_TYPE_KNOWN_EXPRESSION` already follows for the discriminator CHECK;
`tests/test_commercial_terms_schema.py` keeps the two copies honest."""

SCENARIO_ID_WHOLE_SCENARIO_UNIQUE = "uq_commercial_terms_scenario_id"
"""At most one rule with `scope_ref IS NULL` per scenario — the *same name* `e7b41c9d2a58` (SC-4-01)
gave the non-partial `UNIQUE (scenario_id)` it shipped with. SC-4-05 narrows what this name means
(one *whole-scenario* rule, not "one rule") rather than replacing it, so the SC-4-01 test and error
message that already name it (`tests/test_commercial_terms_guards.py`,
`tests/test_commercial_terms_schema.py`) keep meaning exactly what they said."""

SCENARIO_ID_SCOPE_UNIQUE = "uq_commercial_terms_scenario_id_scope_ref"
"""At most one rule per (scenario, segment) — no segment carries two rules (D-3=A, criterion K-02).
New in SC-4-05: paired with `SCENARIO_ID_WHOLE_SCENARIO_UNIQUE` above, the two admit a
whole-scenario rule and any number of distinct-segment rules coexisting under one scenario, while
still refusing a second rule of the *same* scope, whichever scope that is."""

FIXED_PRICE_MODEL_TYPE_EXPRESSION = f"model_type = '{MODEL_TYPE_FIXED_PRICE}'"

AGREED_PRICE_NON_NEGATIVE_EXPRESSION = "agreed_price >= 0"
"""D-5 of SC-4-02's gate 1: the lower bound of the agreed price, enforced by the database. `>= 0`,
not `> 0`, so the zero revenue of AC-05 is reachable for Fixed Price as well."""

AGREED_PRICE_PRECISION = 14
AGREED_PRICE_SCALE = 4
"""`NUMERIC(14,4)` — the explicit precision of every amount in this schema (ADR-0001; the scale of
`catalog_default_rates` and `additional_cost.amount`). The price is *input*: `150000.1234` is stored
as typed, a fifth decimal place is a `422` at the API (ADR-0002, addendum 2026-09-21, point 2), and
the one rounding happens on the revenue (`app.core.money.round_money`)."""

FIXED_PRICE_TYPE_AGREEMENT_FOREIGN_KEY = "fk_fixed_price_terms_commercial_terms_model_type"
"""The Fixed Price twin of `TYPE_AGREEMENT_FOREIGN_KEY` (ADR-0003, addendum 2026-09-25 SC-4-02,
point 1; criterion K-06). Named explicitly for the same reason."""


class CommercialTerms(Base):
    """The commercial rule of one scenario: which model prices it (ADR-0003, points 1-2)."""

    __tablename__ = "commercial_terms"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_commercial_terms_scenario_id"),
        nullable=False,
    )
    """`UNIQUE` (constraint below), which also gives the index every read of this table uses — "the
    rule of this scenario" is the only lookup there is."""

    model_type: Mapped[str] = mapped_column(String(MODEL_TYPE_LENGTH), nullable=False)
    """A string closed by a CHECK, not a PostgreSQL enum: widening a CHECK in the migration that
    adds a model is one statement, while `ALTER TYPE … ADD VALUE` cannot run inside the one
    transaction `migrations/env.py` wraps a batch in."""

    scope_ref: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """`NULL` — the rule prices the whole scenario (the only shape before SC-4-05). A segment id —
    the rule prices one `scenario_delivery_segment` of *this* scenario, enforced by the composite
    foreign key `SCOPE_REF_FOREIGN_KEY` below, never by a plain `ForeignKey("scenario_delivery_
    segment.id")`, which would accept a segment belonging to another scenario (SC-4-05, criterion
    K-04). At most one row per scenario carries `NULL` and at most one row per (scenario, segment)
    carries a given non-null value (`SCENARIO_ID_WHOLE_SCENARIO_UNIQUE`, `SCENARIO_ID_SCOPE_UNIQUE`
    below) — a scenario is either governed by one whole-scenario rule or by any number of
    distinct-segment rules, never two rules of the identical scope (D-3=A, criterion K-02). Nothing
    on this row, and nothing in `app.data.commercial_terms`, knows which `staffing_position` belongs
    to which segment (F-04 is out of scope of SC-4-05): this column proves disjointness of *rows*;
    the per-segment allocation of revenue is not proven here."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    """ADR-0007's concurrency marker for the rule **and its details row** (ADR-0003,
    "Konsekwencje"), the way a position's marker covers its months. SC-4-01 ships no edit path — the
    model is immutable and `tm_terms` has no column to edit — so the marker is carried now for the
    first task that adds one, and returned on every read so that task's client already has it."""

    scenario: Mapped["Scenario"] = relationship()

    __table_args__ = (
        CheckConstraint(MODEL_TYPE_KNOWN_EXPRESSION, name="model_type_known"),
        # At most one whole-scenario rule per scenario (`scope_ref IS NULL`) — narrowed from the
        # non-partial `UNIQUE (scenario_id)` SC-4-01 shipped, kept at the same name (migration
        # `b7e3f19a6c52`, SC-4-05, D-3=A).
        Index(
            SCENARIO_ID_WHOLE_SCENARIO_UNIQUE,
            "scenario_id",
            unique=True,
            postgresql_where=text(SCOPE_REF_NULL_EXPRESSION),
        ),
        # At most one rule per (scenario, segment) — no segment carries two rules.
        Index(
            SCENARIO_ID_SCOPE_UNIQUE,
            "scenario_id",
            "scope_ref",
            unique=True,
            postgresql_where=text(SCOPE_REF_NOT_NULL_EXPRESSION),
        ),
        # The parent half of `TYPE_AGREEMENT_FOREIGN_KEY`: PostgreSQL accepts a foreign key only
        # against a unique constraint on exactly the referenced columns.
        UniqueConstraint("id", "model_type", name="uq_commercial_terms_id_model_type"),
        # Cross-scenario integrity for `scope_ref` (SC-4-05, criterion K-04) — against
        # `scenario_delivery_segment`'s own `UNIQUE (id, scenario_id)` (ADR-0016, point 4), the
        # composite foreign key that decision prepared the ground for without naming this table.
        ForeignKeyConstraint(
            ["scope_ref", "scenario_id"],
            ["scenario_delivery_segment.id", "scenario_delivery_segment.scenario_id"],
            name=SCOPE_REF_FOREIGN_KEY,
        ),
    )


class TmTerms(Base):
    """The Time & Material details of one rule — 1:1, and type-agreed in the database (point 3).

    No domain column in the MVP, deliberately: the row exists so that the type-agreement mechanism
    is proven the day the discriminator appears, not the day the first T&M field lands on a table
    that already holds data.
    """

    __tablename__ = "tm_terms"

    commercial_terms_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True
    )
    model_type: Mapped[str] = mapped_column(
        String(MODEL_TYPE_LENGTH),
        nullable=False,
        server_default=MODEL_TYPE_TIME_AND_MATERIAL,
        default=MODEL_TYPE_TIME_AND_MATERIAL,
    )
    """Always `time_and_material` (CHECK below), and a column anyway: it is the second half of the
    composite foreign key, which is what makes "a T&M details row of a Fixed Price rule" a row the
    database refuses rather than a row the application has to remember not to write."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # No `updated_at`: the marker is the rule's (ADR-0003, "Konsekwencje").

    __table_args__ = (
        CheckConstraint(TM_MODEL_TYPE_EXPRESSION, name="model_type_is_tm"),
        ForeignKeyConstraint(
            ["commercial_terms_id", "model_type"],
            ["commercial_terms.id", "commercial_terms.model_type"],
            name=TYPE_AGREEMENT_FOREIGN_KEY,
        ),
    )


class StoryPointsTerms(Base):
    """The Story Points details of one rule — 1:1, type-agreed in the database (point 3; SC-4-04).

    Two domain columns, both decided at gate 1 of SC-4-04 (ADR-0003 addendum 2026-09-25):

    - **`price_per_point` × `accepted_points`, nothing else** (D-1/A) — the "sprint fee" variant is
      out of scope; a second shape under this same discriminator would be the wide-table defect
      ADR-0003 already refused once ("Considered alternatives"), so it would need its own
      discriminator value, not a column here.
    - **`accepted_points` is written once, at creation, with no edit path** (D-5/A) — like this
      whole table, like `tm_terms`: nothing outside the scenario changes it, so approval freezes
      nothing of it and a copy of the scenario is the only way to change the figure
      (`app.data.scenario_guard`, `app.data.commercial_terms.copy_commercial_terms`).
      Accumulating points sprint over sprint is a named, accepted MVP limitation — not modelled.

    `price_per_point > 0` and `accepted_points >= 0` are database CHECKs, not application validation
    (NF-01), the same reasoning `amount_positive` (`app.models.additional_cost`) and
    `budget_days_not_negative` (`app.models.catalog`) already apply to a money and a count column.
    """

    __tablename__ = "story_points_terms"

    commercial_terms_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True
    )
    model_type: Mapped[str] = mapped_column(
        String(MODEL_TYPE_LENGTH),
        nullable=False,
        server_default=MODEL_TYPE_STORY_POINTS,
        default=MODEL_TYPE_STORY_POINTS,
    )
    """Always `story_points` (CHECK below) — the second half of the composite foreign key, the same
    role `TmTerms.model_type` plays for Time & Material."""

    price_per_point: Mapped[Decimal] = mapped_column(Numeric(precision=14, scale=4), nullable=False)
    """The selling price of one accepted point, in `currency` below. `NUMERIC(14,4)`, the precision
    every rate in this repository uses (`catalog_default_rates.default_selling_rate`) — not a new
    scale invented for this table."""

    accepted_points: Mapped[int] = mapped_column(Integer, nullable=False)
    """How many points are billed, decided once at creation (D-5/A above). Not a count of anything
    the staffing plan derives — no column here reads `staffing_position_allocation` (criterion K-02:
    no Story Points ↔ hours conversion, in either direction)."""

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    """The revenue's currency. No cross-check against `scenarios.currency` and no conversion — the
    same "no `exchange_rates`, no invented rate" reasoning as ADR-0003 point 8, not extended to this
    model by this task (open decision, named in the SC-4-04 developer report)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # No `updated_at`: the marker is the rule's (ADR-0003, "Konsekwencje"; confirmed for every
    # `DETAIL_TABLE_BY_MODEL` entry by the SC-4-04 addendum, not only for `tm_terms`).

    __table_args__ = (
        CheckConstraint(SP_MODEL_TYPE_EXPRESSION, name="model_type_is_sp"),
        CheckConstraint("price_per_point > 0", name="price_per_point_positive"),
        CheckConstraint("accepted_points >= 0", name="accepted_points_not_negative"),
        ForeignKeyConstraint(
            ["commercial_terms_id", "model_type"],
            ["commercial_terms.id", "commercial_terms.model_type"],
            name=STORY_POINTS_TYPE_AGREEMENT_FOREIGN_KEY,
        ),
    )


def _amount_column(*, nullable: bool) -> Mapped[Decimal | None]:
    return mapped_column(Numeric(OUTCOME_AMOUNT_PRECISION, OUTCOME_AMOUNT_SCALE), nullable=nullable)


def _units_column() -> Mapped[Decimal | None]:
    return mapped_column(Numeric(OUTCOME_UNITS_PRECISION, OUTCOME_UNITS_SCALE), nullable=True)


def _probability_column() -> Mapped[Decimal | None]:
    return mapped_column(Numeric(PROBABILITY_PRECISION, PROBABILITY_SCALE), nullable=True)


class OutcomeTerms(Base):
    """The Outcome-based rule's details — 1:1, type agreement in the database (ADR-0003, addendum
    2026-09-25 SC-4-03).

    The first details table with domain columns. Every rule below is a constraint of the database,
    not only of the API schema — a fixture, a script or an import do not go through Pydantic:

    - **the fixed fee is mandatory; the bonus, the unit rate, the minimum and the maximum are
      optional**, and an absent component is `NULL`, never `0` (point 2) — `0` is a value entered
      by the user;
    - the amounts, the rate and the unit counts are non-negative; `revenue_min <= revenue_max`, when
      both are set;
    - **four fixed categories as columns** (point 3), each with a unit count (entered by hand;
      `NULL` allowed only with no unit rate —
      `ck_outcome_terms_units_given_with_unit_rate`) and an optional `NUMERIC(5,2)`
      probability; "all `NULL` or the sum exactly 100" as a single-row `CHECK`
      (point 4);
    - **the rule's own currency** (point 7), the same two `CHECK`s as in the catalogue.

    Own data of the scenario (ADR-0004, addendum 2026-09-25 SC-4-03, group 2): the write guard
    protects it, one entry of the aggregate copies it, the snapshot freezes nothing of it.
    """

    __tablename__ = "outcome_terms"

    commercial_terms_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True
    )
    model_type: Mapped[str] = mapped_column(
        String(MODEL_TYPE_LENGTH),
        nullable=False,
        server_default=MODEL_TYPE_OUTCOME_BASED,
        default=MODEL_TYPE_OUTCOME_BASED,
    )
    """Always `outcome_based` (CHECK below) — the second half of the composite foreign key, as in
    `tm_terms`."""

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fixed_fee: Mapped[Decimal] = _amount_column(nullable=False)
    success_bonus: Mapped[Decimal | None] = _amount_column(nullable=True)
    """A binary bonus — paid for the categories "achieved" and "exceeded" (point 2)."""
    unit_rate: Mapped[Decimal | None] = _amount_column(nullable=True)
    revenue_min: Mapped[Decimal | None] = _amount_column(nullable=True)
    revenue_max: Mapped[Decimal | None] = _amount_column(nullable=True)
    """The minimum and maximum bound the **whole** revenue — the category's portion together with
    the guaranteed one (point 6)."""

    not_achieved_units: Mapped[Decimal | None] = _units_column()
    not_achieved_probability: Mapped[Decimal | None] = _probability_column()
    partial_units: Mapped[Decimal | None] = _units_column()
    partial_probability: Mapped[Decimal | None] = _probability_column()
    achieved_units: Mapped[Decimal | None] = _units_column()
    achieved_probability: Mapped[Decimal | None] = _probability_column()
    exceeded_units: Mapped[Decimal | None] = _units_column()
    exceeded_probability: Mapped[Decimal | None] = _probability_column()

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # No `updated_at`: the concurrency marker belongs to the rule (ADR-0003, "Konsekwencje";
    # addendum SC-4-03, point 9).

    __table_args__ = (
        CheckConstraint(OUTCOME_MODEL_TYPE_EXPRESSION, name="model_type_is_outcome"),
        CheckConstraint(OUTCOME_CURRENCY_ISO4217_EXPRESSION, name="currency_iso4217"),
        CheckConstraint(OUTCOME_CURRENCY_UPPER_EXPRESSION, name="currency_is_upper"),
        CheckConstraint("fixed_fee >= 0", name="fixed_fee_not_negative"),
        CheckConstraint("success_bonus >= 0", name="success_bonus_not_negative"),
        CheckConstraint("unit_rate >= 0", name="unit_rate_not_negative"),
        CheckConstraint("revenue_min >= 0", name="revenue_min_not_negative"),
        CheckConstraint("revenue_max >= 0", name="revenue_max_not_negative"),
        CheckConstraint(OUTCOME_BOUNDS_ORDERED_EXPRESSION, name="revenue_bounds_ordered"),
        *(
            CheckConstraint(
                f"{units_column(category)} >= 0", name=f"{units_column(category)}_not_negative"
            )
            for category in OUTCOME_CATEGORIES
        ),
        *(
            CheckConstraint(
                f"{probability_column(category)} >= 0",
                name=f"{probability_column(category)}_not_negative",
            )
            for category in OUTCOME_CATEGORIES
        ),
        CheckConstraint(OUTCOME_PROBABILITIES_EXPRESSION, name="probabilities_sum_to_100"),
        CheckConstraint(
            OUTCOME_UNITS_WITH_UNIT_RATE_EXPRESSION, name="units_given_with_unit_rate"
        ),
        ForeignKeyConstraint(
            ["commercial_terms_id", "model_type"],
            ["commercial_terms.id", "commercial_terms.model_type"],
            name=OUTCOME_TYPE_AGREEMENT_FOREIGN_KEY,
        ),
    )


class FixedPriceTerms(Base):
    """The Fixed Price details of one rule: the agreed price of the whole project (F-06.2; D-1).

    1:1 with its rule and type-agreed in the database, like `TmTerms` (ADR-0003, addendum 2026-09-25
    SC-4-02, point 1). Own data of the scenario (group 2 of ADR-0004): nothing outside the scenario
    changes it, the approval freezes nothing of it, and a write to it under an `approved` scenario
    is refused in the statement that writes (ADR-0004, addendum 2026-09-25 SC-4-02, point 1).

    **What is deliberately absent** (gate 1 of SC-4-02): milestones (D-1 = A), price adjustments —
    bonuses, penalties, scope changes (D-3 = C, Issue #112) — and any hours, rate or cost. The
    revenue this row yields depends on nothing but the price (F-06.2: "Increasing effort or staffing
    shall not automatically increase revenue").
    """

    __tablename__ = "fixed_price_terms"

    commercial_terms_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True
    )
    model_type: Mapped[str] = mapped_column(
        String(MODEL_TYPE_LENGTH),
        nullable=False,
        server_default=MODEL_TYPE_FIXED_PRICE,
        default=MODEL_TYPE_FIXED_PRICE,
    )
    """Always `fixed_price` (CHECK below) — the second half of the composite foreign key, as on
    `tm_terms`."""

    agreed_price: Mapped[Decimal] = mapped_column(
        Numeric(AGREED_PRICE_PRECISION, AGREED_PRICE_SCALE), nullable=False
    )
    """The price agreed for the whole project — the revenue of the scenario, with no adjustment
    (adjustments are out of scope, D-3 = C). `NOT NULL`: a Fixed Price rule without a price is not
    writable through the API (`422`), and a details row without one is refused here."""

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    """ISO 4217, next to the amount (ADR-0002: every money field is an (amount, code) pair). No
    conversion anywhere (ADR-0006): a price in a currency other than `scenarios.currency` is the
    named `currency_mismatch` state, never a converted figure."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # No `updated_at`: the marker is the rule's (ADR-0003, "Konsekwencje"; point 7 of the addendum).

    __table_args__ = (
        CheckConstraint(FIXED_PRICE_MODEL_TYPE_EXPRESSION, name="model_type_is_fixed_price"),
        CheckConstraint(AGREED_PRICE_NON_NEGATIVE_EXPRESSION, name="agreed_price_non_negative"),
        # The same two rules `catalog_default_rates` and `additional_cost` carry for a currency.
        CheckConstraint("char_length(currency) = 3", name="currency_iso4217"),
        CheckConstraint("currency = upper(currency)", name="currency_is_upper"),
        ForeignKeyConstraint(
            ["commercial_terms_id", "model_type"],
            ["commercial_terms.id", "commercial_terms.model_type"],
            name=FIXED_PRICE_TYPE_AGREEMENT_FOREIGN_KEY,
        ),
    )
