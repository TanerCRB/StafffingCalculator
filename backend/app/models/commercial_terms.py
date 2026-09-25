"""The commercial rule of one scenario, its Time & Material, Story Points and Outcome-based details
(F-06, F-06.1, F-06.3, F-06.4, F-06.5; SC-4-01, SC-4-03, SC-4-04, SC-4-05).

SC-4-05 (Issue #69) adds `scope_ref`, a nullable pointer from a rule to one `scenario_delivery_
segment` of *its own* scenario (ADR-0003 addendum 2026-09-25, D-3=A). It does not add a new entity
("reguła łączona" is not a table) and it does not change how a rule's own model prices anything — it
only widens *how many* rows one scenario may have and *which one* each points at. See `scope_ref`
below and `SCENARIO_ID_WHOLE_SCENARIO_UNIQUE`/`SCENARIO_ID_SCOPE_UNIQUE`/`SCOPE_REF_FOREIGN_KEY`.
`scope_ref` lives on `commercial_terms`, not on a details table, so it applies to every model —
`outcome_based` (SC-4-03) included — with no per-model column.

Two details tables, one rule table, and every property below is a decision of ADR-0003 (rewritten
and accepted at gate 1 of SC-4-01, extended by its 2026-09-25 addendum for SC-4-04) rather than a
choice made here. `story_points_terms` (SC-4-04, Issue #68) is the second model to join the
registry, and it is the first real proof that the pattern below generalises: nothing in this module
names `time_and_material` outside the one constant each model owns, and `MODEL_TYPES` /
`DETAIL_TABLE_BY_MODEL` / `REVENUE_BY_MODEL` (`app.data.commercial_terms`) are what a caller reads
to find either model — never a name compared by hand (D-1..D-6 of the addendum, opcja A wszędzie).

1. **The rule belongs to the scenario** (point 1). `commercial_terms.scenario_id` is `NOT NULL` and
   `UNIQUE` — one rule per scenario in the MVP — and its scope is inherited through
   `scenario_id → scenarios.project_id`, i.e. through `app.data.project_reads.project_for_caller`,
   with no scope function of its own (ADR-0005, addendum 2026-09-23 SC-4-01, point 1).
2. **A discriminator closed to the models that have a details table** (point 2). The CHECK below
   admits `time_and_material`, `story_points` (SC-4-04) and `outcome_based` (SC-4-03); each model
   widens it in the same migration that creates its details table, recreating the full `IN` list
   (ADR-0003, aneks 2026-09-25 SC-4-03, pkt 10c). `model_type` is immutable after the write — there
   is no edit path for it, and changing a rule's model is out of scope of the MVP.
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
billable-day length — ADR-0003 points 7 and "Odłożone"), and any rate: the selling rate comes only
from the catalogue (point 4), so nothing on these rows is priced and nothing is a personnel cost.
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
"""The first commercial model (ADR-0003, point 2)."""

MODEL_TYPE_STORY_POINTS = "story_points"
"""The second commercial model (SC-4-04, Issue #68; ADR-0003 addendum 2026-09-25, D-1/A): a single
shape — price per point × accepted points, no "sprint fee" variant (out of scope)."""

MODEL_TYPE_OUTCOME_BASED = "outcome_based"
"""Model Outcome-based (F-06.3; ADR-0003, aneks 2026-09-25 SC-4-03)."""

MODEL_TYPES: tuple[str, ...] = (
    MODEL_TYPE_TIME_AND_MATERIAL,
    MODEL_TYPE_STORY_POINTS,
    MODEL_TYPE_OUTCOME_BASED,
)
"""Every model with a details table, as data — the set the discriminator CHECK admits.

Growing this tuple is a migration, not an edit: the CHECK in the database is the rule, and the task
adding a model adds its details table, its value here and its branch of the revenue dispatcher
(`app.data.commercial_terms.REVENUE_BY_MODEL`) together."""

MODEL_TYPE_KNOWN_EXPRESSION = (
    "model_type IN ('time_and_material', 'story_points', 'outcome_based')"
)
"""The discriminator CHECK as SQL — spelled here and in the migration that last recreated it
(`b9e3c7a1f264`, SC-4-03, linearised on top of `b7e3f19a6c52`, SC-4-05, which leaves this CHECK
as `d2f6a91c4b58`, SC-4-04, recreated it), and asserted identical to
that copy by `tests/test_commercial_terms_schema.py` (`LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH`, the
drift guard R-02 introduced for the catalogue) and `tests/test_outcome_terms_schema.py`.

**Pełna lista `IN`, nie tylko wartość ostatniego modelu** (ADR-0003, aneks 2026-09-25 SC-4-03, pkt
10c): migracja niosąca wyłącznie własną wartość po cichu unieważniłaby zapisane reguły
wcześniejszych modeli przy następnej walidacji ograniczenia."""

TM_MODEL_TYPE_EXPRESSION = f"model_type = '{MODEL_TYPE_TIME_AND_MATERIAL}'"
SP_MODEL_TYPE_EXPRESSION = f"model_type = '{MODEL_TYPE_STORY_POINTS}'"

OUTCOME_MODEL_TYPE_EXPRESSION = f"model_type = '{MODEL_TYPE_OUTCOME_BASED}'"

MODEL_TYPE_LENGTH = 40

OUTCOME_CATEGORIES: tuple[str, ...] = ("not_achieved", "partial", "achieved", "exceeded")
"""Cztery stałe kategorie wyniku (ADR-0003, aneks 2026-09-25 SC-4-03, pkt 3), od najgorszej. Każda
jest parą kolumn `outcome_terms`: `<kategoria>_units` i `<kategoria>_probability`."""


def units_column(category: str) -> str:
    """Nazwa kolumny liczby jednostek kategorii — jedna pisownia dla modelu, zapisu i odczytu."""
    return f"{category}_units"


def probability_column(category: str) -> str:
    """Nazwa kolumny prawdopodobieństwa kategorii — jedna pisownia dla modelu, zapisu i odczytu."""
    return f"{category}_probability"


OUTCOME_AMOUNT_PRECISION = 14
OUTCOME_AMOUNT_SCALE = 4
"""`NUMERIC(14,4)` dla kwot i stawki za jednostkę reguły — ta sama skala co stawki katalogu i koszty
dodatkowe (ADR-0008, pkt 6). Zaokrąglenie do jednostki waluty należy do wyliczenia
(`app.core.money.round_money`), nigdy do zapisu."""

OUTCOME_UNITS_PRECISION = 14
OUTCOME_UNITS_SCALE = 4

PROBABILITY_PRECISION = 5
PROBABILITY_SCALE = 2
"""`NUMERIC(5,2)` — procenty z dwoma miejscami po przecinku (ADR-0003, aneks SC-4-03, pkt 4)."""

OUTCOME_PROBABILITIES_EXPRESSION = (
    "("
    + " AND ".join(f"{probability_column(c)} IS NULL" for c in OUTCOME_CATEGORIES)
    + ") OR ("
    + " AND ".join(f"{probability_column(c)} IS NOT NULL" for c in OUTCOME_CATEGORIES)
    + " AND "
    + " + ".join(probability_column(c) for c in OUTCOME_CATEGORIES)
    + " = 100)"
)
""""Wszystkie cztery `NULL` albo wszystkie ustawione i suma dokładnie 100" — `CHECK` jednego wiersza
(ADR-0003, aneks SC-4-03, pkt 3-4). Bez tolerancji: `NUMERIC` porównuje dokładnie."""

OUTCOME_UNITS_WITH_UNIT_RATE_EXPRESSION = (
    "unit_rate IS NULL OR ("
    + " AND ".join(f"{units_column(c)} IS NOT NULL" for c in OUTCOME_CATEGORIES)
    + ")"
)
"""Liczby jednostek są danymi wejściowymi stawki za jednostkę (runda 2 weryfikacji SC-4-03, pkt 5):
bez stawki (`unit_rate IS NULL`) każda z czterech może być `NULL` — nie ma czego nimi mnożyć, a `0`
wpisane za brak byłoby fałszywą wartością; ze stawką wszystkie cztery `NOT NULL`. `CHECK` jednego
wiersza, więc także fixture i import nie zapiszą stawki bez jednostek."""

OUTCOME_BOUNDS_ORDERED_EXPRESSION = (
    "revenue_min IS NULL OR revenue_max IS NULL OR revenue_min <= revenue_max"
)
"""`min <= max`, gdy oba ustawione (pkt 2). Brak ograniczenia to `NULL`, nigdy `0`."""

OUTCOME_CURRENCY_ISO4217_EXPRESSION = "char_length(currency) = 3"
OUTCOME_CURRENCY_UPPER_EXPRESSION = "currency = upper(currency)"
"""Te same dwie reguły waluty co w katalogu (`ck_catalog_default_rates_currency_*`; pkt 7)."""

OUTCOME_TYPE_AGREEMENT_FOREIGN_KEY = "fk_outcome_terms_commercial_terms_model_type"
"""Złożony klucz obcy zgodności typu dla `outcome_terms` — wzorzec pkt 3 ADR-0003 bez zmian."""

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
      ADR-0003 already refused once ("Rozważane alternatywy"), so it would need its own
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
    """Szczegóły reguły Outcome-based — 1:1, zgodność typu w bazie (ADR-0003, aneks 2026-09-25
    SC-4-03).

    Pierwsza tabela szczegółów z kolumnami dziedzinowymi. Każda reguła poniżej jest ograniczeniem
    bazy, nie tylko schematu API — fixture, skrypt ani import nie przechodzą przez Pydantic:

    - **opłata stała obowiązkowa; premia, stawka za jednostkę, minimum i maksimum opcjonalne**, a
      składnik nieobecny to `NULL`, nigdy `0` (pkt 2) — `0` to wartość wpisana przez
      użytkownika;
    - kwoty, stawka i liczby jednostek nieujemne; `revenue_min <= revenue_max`, gdy oba ustawione;
    - **cztery stałe kategorie jako kolumny** (pkt 3), każda z liczbą jednostek (wpisaną ręcznie;
      `NULL` dozwolone tylko bez stawki za jednostkę —
      `ck_outcome_terms_units_given_with_unit_rate`) i opcjonalnym prawdopodobieństwem
      `NUMERIC(5,2)`; "wszystkie `NULL` albo suma dokładnie 100" jako `CHECK` jednego wiersza
      (pkt 4);
    - **własna waluta reguły** (pkt 7), te same dwa `CHECK` co w katalogu.

    Dana własna scenariusza (ADR-0004, aneks 2026-09-25 SC-4-03, grupa 2): chroni ją strażnik
    zapisu, kopiuje ją jeden wpis agregatu, migawka nie zamraża niczego.
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
    """Zawsze `outcome_based` (CHECK niżej) — druga połowa złożonego klucza obcego, jak w
    `tm_terms`."""

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fixed_fee: Mapped[Decimal] = _amount_column(nullable=False)
    success_bonus: Mapped[Decimal | None] = _amount_column(nullable=True)
    """Premia binarna — wypłacana dla kategorii "osiągnięty" i "przekroczony" (pkt 2)."""
    unit_rate: Mapped[Decimal | None] = _amount_column(nullable=True)
    revenue_min: Mapped[Decimal | None] = _amount_column(nullable=True)
    revenue_max: Mapped[Decimal | None] = _amount_column(nullable=True)
    """Minimum i maksimum ograniczają **cały** przychód kategorii i gwarantowany (pkt 6)."""

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
    # Bez `updated_at`: znacznik współbieżności należy do reguły (ADR-0003, "Konsekwencje"; aneks
    # SC-4-03, pkt 9).

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
