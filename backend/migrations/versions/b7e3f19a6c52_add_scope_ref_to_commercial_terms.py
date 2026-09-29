"""add scope_ref to commercial_terms

SC-4-05 (F-06, F-06.5; Issue #69; `ADR-0003-model-modeli-komercyjnych.md`, addendum 2026-09-25
"impact map for gate 1 of SC-4-05" and its closing "gate 1 SC-4-05 — human decision" of the
same date; `ADR-0016-segment-dostawy-scenariusza.md`). Expand only (ADR-0001, expand -> deploy ->
contract): one nullable column added, one foreign key added, one UNIQUE constraint replaced by two
partial unique indexes that together admit strictly more rows than it did — no existing column is
altered and no row is written, so the code deployed before this migration keeps working against the
new schema unchanged (it never sets `scope_ref`, so every row it writes still satisfies the narrower
of the two new indexes exactly as the old, non-partial constraint did), and there is no contract
phase to pair with it.

**What the database enforces here, and why in the schema:**

1. `commercial_terms.scope_ref` — nullable `UUID`, written by no code that predates this migration.
   `NULL` means "the whole scenario" (the only shape before this task); a segment id means "this one
   segment of this scenario" (D-3=A).
2. **Cross-scenario integrity is a composite foreign key, not an application check** (criterion
   K-04 — the same pattern `fk_tm_terms_commercial_terms_model_type` already proves for the model
   discriminator): `commercial_terms (scope_ref, scenario_id) -> scenario_delivery_segment (id,
   scenario_id)`, against `uq_scenario_delivery_segment_id_scenario_id` — the unique constraint
   ADR-0016 prepared for exactly this foreign key (that decision's point 4). A rule pointing at a
   segment of *another* scenario is unwritable; `NULL` never participates in a foreign key check
   (an ordinary SQL rule, not a case this constraint has to special-case), so a whole-scenario rule
   takes no part in it at all.
3. **`uq_commercial_terms_scenario_id` is narrowed, not dropped** (D-3=A; criterion K-02): from "one
   row per scenario" to "one row per scenario with `scope_ref IS NULL`" — a partial unique index of
   the *same name*, so the existing test and the existing error message that already name this
   constraint (`tests/test_commercial_terms_guards.py`, `tests/test_commercial_terms_schema.py`)
   keep meaning exactly what they said: two whole-scenario rules for one scenario are still refused
   by this name, unchanged. A second, new partial unique index,
   `uq_commercial_terms_scenario_id_scope_ref`, adds: one row per (scenario, segment) — no segment
   carries two rules. Together the two admit what the single, non-partial constraint could not: a
   whole-scenario rule and any number of distinct-segment rules coexisting under one scenario (the
   "combined rule" of D-3=A — a scenario-wide default plus segment overrides), while still refusing
   a second rule of the *same* scope, whichever scope that is.

**What is deliberately not here:** any predicate connecting two *different* scope values (whether
one segment rule's scope overlaps another's in some other sense, or "double-billing" at the level
of a revenue amount). ADR-0016 point 6 already allows segments themselves to overlap in time, and
nothing below or above this migration knows which `staffing_position` belongs to which segment
(F-04 is out of scope of SC-4-05, as it was of SC-1-11). This migration proves disjointness of
*rows of this table* — not of revenue; the revenue-level protection is named out of scope in
`docs/PLAN.md`'s entry for SC-4-05 and in the closing annex of ADR-0003's addendum of 2026-09-25.

Revision ID: b7e3f19a6c52
Revises: d2f6a91c4b58
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e3f19a6c52"
down_revision: str | None = "d2f6a91c4b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06): the new foreign key takes `SHARE
ROW EXCLUSIVE` on `scenario_delivery_segment`, and the dropped/recreated unique constraint takes
`ACCESS EXCLUSIVE` on `commercial_terms` for the statement's duration."""

_SCOPE_REF_FOREIGN_KEY = "fk_commercial_terms_scope_ref_scenario_id"
_WHOLE_SCENARIO_UNIQUE = "uq_commercial_terms_scenario_id"
"""Kept from `e7b41c9d2a58` on purpose — narrowed by this migration, not replaced (see point 3
above)."""
_SCOPE_UNIQUE = "uq_commercial_terms_scenario_id_scope_ref"

_SCOPE_REF_NULL_EXPRESSION = "scope_ref IS NULL"
_SCOPE_REF_NOT_NULL_EXPRESSION = "scope_ref IS NOT NULL"
"""Spelled here as well as in `app.models.commercial_terms.SCOPE_REF_NULL_EXPRESSION`/
`SCOPE_REF_NOT_NULL_EXPRESSION` rather than imported from them — the convention
`_MODEL_TYPE_KNOWN_EXPRESSION` already follows; `tests/test_commercial_terms_scope.py` keeps the
two copies honest."""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    op.add_column("commercial_terms", sa.Column("scope_ref", sa.UUID(), nullable=True))

    # --- narrow the old constraint instead of dropping it (point 3) ------------------------------
    op.drop_constraint(op.f(_WHOLE_SCENARIO_UNIQUE), "commercial_terms", type_="unique")
    op.create_index(
        op.f(_WHOLE_SCENARIO_UNIQUE),
        "commercial_terms",
        ["scenario_id"],
        unique=True,
        postgresql_where=sa.text(_SCOPE_REF_NULL_EXPRESSION),
    )
    op.create_index(
        op.f(_SCOPE_UNIQUE),
        "commercial_terms",
        ["scenario_id", "scope_ref"],
        unique=True,
        postgresql_where=sa.text(_SCOPE_REF_NOT_NULL_EXPRESSION),
    )

    # --- cross-scenario integrity (point 2; criterion K-04) ---------------------------------------
    op.create_foreign_key(
        op.f(_SCOPE_REF_FOREIGN_KEY),
        "commercial_terms",
        "scenario_delivery_segment",
        ["scope_ref", "scenario_id"],
        ["id", "scenario_id"],
    )

    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`. Not a safe rollback plan for production data — recreating the original,
    non-partial `UNIQUE (scenario_id)` fails outright if any scenario currently holds more than one
    rule (a whole-scenario rule together with one or more segment rules, or two distinct-segment
    rules), which this migration is what made legal — the same "data loss/incompatibility by
    definition" every downgrade of an expand migration in this repository already accepts. It exists
    so the migration is tested both ways, not as a deployment path.
    """
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    op.drop_constraint(op.f(_SCOPE_REF_FOREIGN_KEY), "commercial_terms", type_="foreignkey")
    op.drop_index(op.f(_SCOPE_UNIQUE), table_name="commercial_terms")
    op.drop_index(op.f(_WHOLE_SCENARIO_UNIQUE), table_name="commercial_terms")
    op.drop_column("commercial_terms", "scope_ref")
    op.create_unique_constraint(op.f(_WHOLE_SCENARIO_UNIQUE), "commercial_terms", ["scenario_id"])

    op.execute("SET LOCAL lock_timeout = DEFAULT")
