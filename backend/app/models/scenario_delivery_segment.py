"""The delivery-phase/workstream entity of a scenario (F-02, F-06; SC-1-11, Issue #65; ADR-0016).

One table, and every property below is a decision of ADR-0016 (accepted at gate 1 of SC-1-11) or of
its dated addendum to `ADR-0004-wersjonowanie-kalkulacji.md`, not a choice made here:

1. **No discriminator, one table for "phase" and "workstream" alike** (ADR-0016, point 1). Neither
   word carries a distinct behaviour anywhere in `Requirements_EN.md` today, and a column nothing
   consumes would repeat the speculative-construction risk ADR-0003 already named for `model_type`.
2. **A child of the scenario, one level down, `scenario_id` `NOT NULL`** (point 2). No `ON DELETE`
   action — the same reason as `staffing_position` and `commercial_terms`: a cascade would be a
   second, unguarded way for the rows of an `approved` scenario to disappear (ADR-0003, point 1).
   Belonging is a column on the segment's own row, never an association table: one scenario has
   many segments (1:N), one segment has exactly one parent (criteria K-01/K-02).
3. **Columns: `id`, `scenario_id`, `name`, `created_at`, `updated_at` — nothing else** (point 3).
   `name` is `VARCHAR(200) NOT NULL` with a non-blank CHECK, the same pattern as `projects.name`/
   `client`/`owner` (ADR-0001, migration `4f0a9c1b7d62`): `NOT NULL` alone does not refuse `''` or
   whitespace-only. `created_at`/`updated_at` carry the segment's **own** ADR-0007 concurrency
   token, not the scenario's — a segment is edited (created, renamed, removed) as its own unit, the
   same reasoning `additional_cost` and `commercial_terms` already apply to their own markers.
   Deliberately absent: an ordering/sequence column (a user-chosen name such as "Phase 1" already
   carries order if the user wants one), any effective-date interval (ADR-0008 is not activated for
   this table, point 6), any amount/currency/hours/allocation column (K-05), and `position_id` or
   any other link to a staffing position (F-04 is out of scope, point 9).
4. **`UNIQUE (id, scenario_id)`** (point 4; K-03) — redundant as a uniqueness claim (`id` alone is
   the primary key) and required anyway: PostgreSQL accepts a composite foreign key only against a
   uniqueness constraint on exactly the referenced columns. The same construction as
   `uq_commercial_terms_id_model_type` (ADR-0003, point 3) and `uq_staffing_position_id_scenario_id`
   (ADR-0014, point 1) — it prepares the ground for a future composite foreign key
   `(segment_id, scenario_id) → scenario_delivery_segment (id, scenario_id)` from whichever table
   grows a `scope_ref` (SC-4-05), and proves nothing today about cross-scenario reference strength
   on its own — exactly as `staffing_position`'s docstring already says about its own copy of this
   pattern.
5. **`UNIQUE (scenario_id, name)`** (point 5) — two segments of one scenario sharing a name are the
   same class of error `UniqueConstraint("project_id", "name")` on `scenarios` already refuses one
   level up. Scoped to `scenario_id`, so copying a scenario (which gives every copy a new
   `scenario_id`) never collides with the source.
6. **No `EXCLUDE`, no effective-date interval** (point 6). Overlapping segments (two parallel
   phases, a workstream spanning the whole delivery period) are a legal state a future commercial
   rule may want to express, not an error — the same conclusion ADR-0004's addendum of 2026-09-19
   (SC-3-01) already reached for `staffing_position`'s own period.
7. **Group 2 of ADR-0004 (own data, write guard, no snapshot)** — see the addendum of 2026-09-25
   (SC-1-11) to `ADR-0004-wersjonowanie-kalkulacji.md`. Nothing outside the scenario changes a
   segment, so there is nothing to freeze at approval; a write under an `approved` scenario is
   refused instead, in `app.data.scenario_delivery_segment`.
8. **No HTTP surface in this task** (point 8, Q2 = A). This model and the write function that
   guards it exist so the entity is ready for a commercial rule to point at (SC-4-05) — no endpoint,
   no router, no public Pydantic schema is built here. Permissions (ADR-0005) are undecided until
   the first task that adds one.
9. **F-04 (staffing allocation per phase) is out of scope** (point 9). `staffing_position` and
   `staffing_position_allocation` are untouched by this table.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

NAME_NOT_BLANK_EXPRESSION = "name ~ '[^[:space:]]'"
"""The same expression `projects.name`/`client`/`owner` use (ADR-0001, migration `4f0a9c1b7d62`),
spelled here and once more in the migration; `tests/test_scenario_delivery_segment_schema.py`
asserts the two copies stay identical (the drift guard R-02)."""

ID_SCENARIO_UNIQUE = "uq_scenario_delivery_segment_id_scenario_id"
"""The parent half of a composite foreign key no table references yet (ADR-0016, point 4) — the
construction `uq_commercial_terms_id_model_type` and `uq_staffing_position_id_scenario_id` already
use, prepared here for the first future `scope_ref` (SC-4-05)."""

SCENARIO_NAME_UNIQUE = "uq_scenario_delivery_segment_scenario_id_name"
"""Two segments of one scenario cannot share a name (ADR-0016, point 5)."""


class ScenarioDeliverySegment(Base):
    """One delivery phase or workstream of one scenario — a name and a place in the hierarchy,
    nothing more (ADR-0016)."""

    __tablename__ = "scenario_delivery_segment"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_scenario_delivery_segment_scenario_id"),
        nullable=False,
        index=True,
    )
    """The owner (ADR-0016, point 2). Indexed: "the segments of this scenario" is the lookup of
    every future read and of the copying cascade below."""

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    """User-chosen; carries order if the user wants one ("Phase 1", "Phase 2") without the schema
    enforcing it (ADR-0016, point 3)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    """ADR-0007's concurrency marker for **this segment row**, not the scenario's (ADR-0016,
    point 3) — always the database's clock (`now()` in the statement that writes), never this
    process's."""

    __table_args__ = (
        CheckConstraint(NAME_NOT_BLANK_EXPRESSION, name="name_not_blank"),
        UniqueConstraint("id", "scenario_id", name=ID_SCENARIO_UNIQUE),
        UniqueConstraint("scenario_id", "name", name=SCENARIO_NAME_UNIQUE),
    )
