"""create story_points_terms and widen the model_type discriminator

SC-4-04 (F-06.4, Issue #68; ADR-0003 addendum 2026-09-25). Expand only (ADR-0001, expand → deploy →
contract): one new table, and the widening of one existing CHECK to admit a second discriminator
value. No column is added to, or dropped from, an existing table; no row is written — so a code
version deployed before this migration keeps working against the new schema (it never sees a
`story_points` row until its own deploy step lands), and there is no contract phase to pair with it.

**What the database enforces here, and why in the schema:**

1. `commercial_terms.model_type` is widened from `CHECK (model_type IN ('time_and_material'))` to
   `CHECK (model_type IN ('time_and_material', 'story_points'))` (ADR-0003, point 2; addendum
   2026-09-25, "Rozszerza się wprost", point 1). Every later model widens this same CHECK in the
   migration that creates its own details table — this is the first migration to do it, proving the
   pattern generalises rather than being a property of the one CHECK T&M shipped with.
2. **Type agreement between the rule and `story_points_terms` is a composite foreign key**, not an
   application check (point 3; criterion K-04) — the same mechanism `fk_tm_terms_commercial_terms_
   model_type` already proves for `tm_terms`: `story_points_terms (commercial_terms_id, model_type)
   → commercial_terms (id, model_type)`, against the `UNIQUE (id, model_type)` the first migration
   already created on the parent. A Story Points details row pointing at a rule of another model is
   unwritable, and a `tm_terms` row cannot claim to be a Story Points rule's details either.
3. **`price_per_point > 0` and `accepted_points >= 0` are CHECKs**, the same reasoning
   `amount_positive` (`a3d9e6f20c71`) and `budget_days_not_negative` (`7b3d5c81e40a`) already apply
   to a money and a count column on this repository's tables.

**What is deliberately not here** (ADR-0003 addendum 2026-09-25, D-4/A and D-5/A):

- No budget cap column and no "sprint fee" variant — out of scope of SC-4-04, opcja A.
- No edit path and no `updated_at` on `story_points_terms` — `accepted_points` is written once, at
  creation, in the one guarded statement (`app.data.commercial_terms.create_commercial_terms`); a
  changed figure needs a copy of the scenario, the mechanism every commercial rule already uses.
  ADR-0007's marker for the aggregate stays `commercial_terms.updated_at`, exactly as for
  `tm_terms`.
- No foreign key to any staffing table: `accepted_points` is not derived from the plan (criterion
  K-02 — no Story Points ↔ hours conversion).

Revision ID: d2f6a91c4b58
Revises: b1f4e8a3c95d
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2f6a91c4b58"
down_revision: str | None = "b1f4e8a3c95d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06): the risk here is the `ALTER TABLE`
widening `commercial_terms`'s CHECK, which takes `ACCESS EXCLUSIVE` for the statement's duration."""

_MODEL_TYPE_TIME_AND_MATERIAL = "time_and_material"
_MODEL_TYPE_STORY_POINTS = "story_points"

_MODEL_TYPE_KNOWN_EXPRESSION = "model_type IN ('time_and_material', 'story_points')"
"""Spelled here as well as in `app.models.commercial_terms` rather than imported from it — the
same convention `e7b41c9d2a58` and `test_commercial_terms_schema.py`'s drift guard already keep
honest."""

_SP_MODEL_TYPE_EXPRESSION = "model_type = 'story_points'"

_RATE = sa.Numeric(precision=14, scale=4)
"""The same precision as `catalog_default_rates.default_selling_rate` (migration `7b3d5c81e40a`) —
not a new scale invented for this table."""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    # --- widen the discriminator (ADR-0003, point 2; addendum 2026-09-25, point 1) ---------------
    op.drop_constraint(
        op.f("ck_commercial_terms_model_type_known"), "commercial_terms", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_commercial_terms_model_type_known"),
        "commercial_terms",
        _MODEL_TYPE_KNOWN_EXPRESSION,
    )

    # --- the Story Points details row, 1:1, type agreement in the database (point 3) --------------
    op.create_table(
        "story_points_terms",
        sa.Column("commercial_terms_id", sa.UUID(), nullable=False),
        sa.Column(
            "model_type",
            sa.String(length=40),
            server_default=sa.text(f"'{_MODEL_TYPE_STORY_POINTS}'"),
            nullable=False,
        ),
        sa.Column("price_per_point", _RATE, nullable=False),
        sa.Column("accepted_points", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _SP_MODEL_TYPE_EXPRESSION, name=op.f("ck_story_points_terms_model_type_is_sp")
        ),
        sa.CheckConstraint(
            "price_per_point > 0", name=op.f("ck_story_points_terms_price_per_point_positive")
        ),
        sa.CheckConstraint(
            "accepted_points >= 0",
            name=op.f("ck_story_points_terms_accepted_points_not_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["commercial_terms_id", "model_type"],
            ["commercial_terms.id", "commercial_terms.model_type"],
            name=op.f("fk_story_points_terms_commercial_terms_model_type"),
        ),
        sa.PrimaryKeyConstraint("commercial_terms_id", name=op.f("pk_story_points_terms")),
    )

    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`. Data loss by definition (every downgrade of an expand migration is): a
    scenario with a Story Points rule goes back to a rule the narrower CHECK refuses to have
    existed, which is why this drops the table before it narrows the CHECK — the narrower
    constraint must find no `story_points` row left to violate it.
    """
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    op.drop_table("story_points_terms")

    op.drop_constraint(
        op.f("ck_commercial_terms_model_type_known"), "commercial_terms", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_commercial_terms_model_type_known"),
        "commercial_terms",
        f"model_type IN ('{_MODEL_TYPE_TIME_AND_MATERIAL}')",
    )

    op.execute("SET LOCAL lock_timeout = DEFAULT")
