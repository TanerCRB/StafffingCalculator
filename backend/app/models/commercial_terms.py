"""The commercial rule of one scenario and its Time & Material details (F-06, F-06.1; SC-4-01).

Two tables, one aggregate, and every property below is a decision of ADR-0003 (rewritten and
accepted at gate 1 of SC-4-01) rather than a choice made here:

1. **The rule belongs to the scenario** (point 1). `commercial_terms.scenario_id` is `NOT NULL` and
   `UNIQUE` — one rule per scenario in the MVP — and its scope is inherited through
   `scenario_id → scenarios.project_id`, i.e. through `app.data.project_reads.project_for_caller`,
   with no scope function of its own (ADR-0005, addendum 2026-09-23 SC-4-01, point 1).
2. **A discriminator closed to the models that have a details table** (point 2). The CHECK below
   admits `time_and_material` and nothing else; each later model widens it in the same migration
   that creates its details table. `model_type` is immutable after the write — there is no edit path
   for it, and changing a rule's model is out of scope of the MVP.
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
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.scenario import Scenario

MODEL_TYPE_TIME_AND_MATERIAL = "time_and_material"
"""The one commercial model that exists today (ADR-0003, point 2)."""

MODEL_TYPES: tuple[str, ...] = (MODEL_TYPE_TIME_AND_MATERIAL,)
"""Every model with a details table, as data — the set the discriminator CHECK admits.

Growing this tuple is a migration, not an edit: the CHECK in the database is the rule, and the task
adding a model adds its details table, its value here and its branch of the revenue dispatcher
(`app.data.commercial_terms.REVENUE_BY_MODEL`) together."""

MODEL_TYPE_KNOWN_EXPRESSION = "model_type IN ('time_and_material')"
"""The discriminator CHECK as SQL — spelled once here and once in migration `e7b41c9d2a58`, and
asserted identical to that copy by `tests/test_commercial_terms_schema.py` (the drift guard R-02
introduced for the catalogue)."""

TM_MODEL_TYPE_EXPRESSION = f"model_type = '{MODEL_TYPE_TIME_AND_MATERIAL}'"

MODEL_TYPE_LENGTH = 40

TYPE_AGREEMENT_FOREIGN_KEY = "fk_tm_terms_commercial_terms_model_type"
"""The composite foreign key that makes type agreement a property of the database (criterion K-04).

Named explicitly and spelled once, so the test proving a refusal came from *this* mechanism —
and not from the CHECK on the child, which refuses a different row — can assert on the name."""


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
        UniqueConstraint("scenario_id", name="uq_commercial_terms_scenario_id"),
        # The parent half of `TYPE_AGREEMENT_FOREIGN_KEY`: PostgreSQL accepts a foreign key only
        # against a unique constraint on exactly the referenced columns.
        UniqueConstraint("id", "model_type", name="uq_commercial_terms_id_model_type"),
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
