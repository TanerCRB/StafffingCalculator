"""create fixed_price_terms and widen the commercial model discriminator

SC-4-02 (F-06.2, Issue #66; ADR-0003, addendum 2026-09-25 SC-4-02; ADR-0004, addendum 2026-09-25
SC-4-02). Expand only (ADR-0001, expand → deploy → contract): one new table and one widened CHECK.
No column is added to or dropped from an existing table, no row is written, and every value the
old CHECK admitted the new one admits — so the code deployed before this migration keeps working
against the new schema (it writes `time_and_material`, `story_points` and `outcome_based` only), and
there is no
contract phase to pair with it. A `fixed_price` rule written after this migration and read by an
instance still running the previous code is the named `unsupported_model_type` state there (R-02
of SC-4-01), never a `500`.

**What the database enforces here, and why in the schema** (a fixture, a seed script or a future
import never passes through a Pydantic model):

1. **The discriminator admits the Fixed Price model** — `ck_commercial_terms_model_type_known` is
   widened from `('time_and_material', 'story_points', 'outcome_based')` (`b9e3c7a1f264`, SC-4-03,
   the latest migration to recreate it) to `('time_and_material', 'story_points', 'outcome_based',
   'fixed_price')` in the same migration that creates the model's details table (ADR-0003, point 2),
   the way `d2f6a91c4b58` and `b9e3c7a1f264` widened it before — the full `IN` list, not only the
   new value (ADR-0003, addendum 2026-09-25 SC-4-03, point 10c). The constraint is dropped and
   re-added **under the same name**, inside this migration's one transaction (and under the
   `ACCESS EXCLUSIVE` lock of the first `ALTER TABLE`), so no other transaction ever sees the
   column unconstrained and every name a test or a refusal message relies on survives (criterion
   K-06).
2. **Type agreement is a composite foreign key again** — `fixed_price_terms (commercial_terms_id,
   model_type) → commercial_terms (id, model_type)`, against the existing
   `uq_commercial_terms_id_model_type`, with `CHECK (model_type = 'fixed_price')` on the child
   (point 1 of the addendum). A Fixed Price details row of a T&M rule is unwritable, and so —
   through the untouched `fk_tm_terms_commercial_terms_model_type` — is a T&M details row of a
   Fixed Price rule.
3. **The agreed price** — `NUMERIC(14,4) NOT NULL` with `CHECK (agreed_price >= 0)` (D-5), and its
   ISO 4217 currency on the same row with the two currency rules `catalog_default_rates` carries.

**What is deliberately not here:**

- **No `ON DELETE` on either foreign key** — a cascade would be a second, unguarded way for the
  rows of an `approved` scenario to disappear (ADR-0003, point 1).
- **No `updated_at` on `fixed_price_terms`** — ADR-0007's marker for the aggregate stays
  `commercial_terms.updated_at` (ADR-0003, "Konsekwencje"; point 7 of the addendum).
- **No snapshot table** — the price is own data of the scenario, protected by the write guard and
  never frozen (ADR-0004, addendum 2026-09-25 SC-4-02, points 1 and 3).
- **No milestone and no adjustment table** — D-1 = A and D-3 = C of gate 1.

This migration was written on top of `d2f6a91c4b58` (SC-4-04) and re-parented onto the single head
of `main` at the sync of 2026-09-28 (`c4d7e2a9b1f6`, SC-2-06), after SC-4-03 (`b9e3c7a1f264`) had
widened the CHECK with `outcome_based`; no migration between `b9e3c7a1f264` and `c4d7e2a9b1f6`
touches the CHECK, so the list restored by `downgrade` is `b9e3c7a1f264`'s.

Revision ID: b8f2d6a41c93
Revises: c4d7e2a9b1f6
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8f2d6a41c93"
down_revision: str | None = "c4d7e2a9b1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06). It covers the `ALTER TABLE` on
`commercial_terms` (`ACCESS EXCLUSIVE`) and the foreign key to it (`SHARE ROW EXCLUSIVE`): the risk
is waiting behind a long reader of the rule table, not the work itself."""

_MODEL_TYPE_FIXED_PRICE = "fixed_price"

_MODEL_TYPE_KNOWN_EXPRESSION = (
    "model_type IN ('time_and_material', 'story_points', 'outcome_based', 'fixed_price')"
)
"""Spelled here as well as in `app.models.commercial_terms` rather than imported from it: a
migration must keep describing the schema it produced even after the model moves on (the rule of
`f3a1d0c58b27`). `tests/test_commercial_terms_schema.py` (`LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH`)
keeps the two copies honest while this is the newest migration to recreate the CHECK."""

_PREVIOUS_MODEL_TYPE_KNOWN_EXPRESSION = (
    "model_type IN ('time_and_material', 'story_points', 'outcome_based')"
)
"""What `b9e3c7a1f264` (SC-4-03) left — the latest migration before this one to recreate the CHECK —
restored by `downgrade`."""

_FIXED_PRICE_MODEL_TYPE_EXPRESSION = "model_type = 'fixed_price'"

_AGREED_PRICE_NON_NEGATIVE_EXPRESSION = "agreed_price >= 0"

_TYPE_AGREEMENT_FOREIGN_KEY = "fk_fixed_price_terms_commercial_terms_model_type"

_AGREED_PRICE = sa.Numeric(precision=14, scale=4)
"""`NUMERIC(14,4)` — the precision of every amount in this schema (`catalog_default_rates`,
`additional_cost`)."""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    # --- widen the discriminator (ADR-0003, point 2) — as `d2f6a91c4b58` and `b9e3c7a1f264` did ---
    op.drop_constraint(
        op.f("ck_commercial_terms_model_type_known"), "commercial_terms", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_commercial_terms_model_type_known"),
        "commercial_terms",
        _MODEL_TYPE_KNOWN_EXPRESSION,
    )

    # --- the Fixed Price details row, 1:1, type agreement in the database -------------------------
    op.create_table(
        "fixed_price_terms",
        sa.Column("commercial_terms_id", sa.UUID(), nullable=False),
        sa.Column(
            "model_type",
            sa.String(length=40),
            server_default=sa.text(f"'{_MODEL_TYPE_FIXED_PRICE}'"),
            nullable=False,
        ),
        sa.Column("agreed_price", _AGREED_PRICE, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _FIXED_PRICE_MODEL_TYPE_EXPRESSION,
            name=op.f("ck_fixed_price_terms_model_type_is_fixed_price"),
        ),
        sa.CheckConstraint(
            _AGREED_PRICE_NON_NEGATIVE_EXPRESSION,
            name=op.f("ck_fixed_price_terms_agreed_price_non_negative"),
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3", name=op.f("ck_fixed_price_terms_currency_iso4217")
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_fixed_price_terms_currency_is_upper")
        ),
        sa.ForeignKeyConstraint(
            ["commercial_terms_id", "model_type"],
            ["commercial_terms.id", "commercial_terms.model_type"],
            name=op.f(_TYPE_AGREEMENT_FOREIGN_KEY),
        ),
        sa.PrimaryKeyConstraint("commercial_terms_id", name=op.f("pk_fixed_price_terms")),
    )

    # `SET LOCAL` lasts to the end of the transaction; reset so the bound does not silently apply to
    # every migration after this one in the same batch (the convention of `a7c2e5f81b94`).
    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`: the details table, then the narrower CHECK.

    It exists so the migration can be tested both ways, not as a deployment path. **It never
    deletes a scenario's rule** (R-02 of the SC-4-02 review, 2026-09-28 — the behaviour of
    `b9e3c7a1f264`, SC-4-03): with a `fixed_price` rule still in `commercial_terms`, re-adding the
    narrower CHECK is refused by the database, the downgrade fails loudly, and — the migration
    being one transaction — the dropped `fixed_price_terms` comes back with it: no price and no
    rule is lost. With no Fixed Price rule it runs through. No T&M, Story Points or Outcome-based
    row is touched; the CHECK goes back to exactly what `b9e3c7a1f264` left.
    """
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.drop_table("fixed_price_terms")
    op.drop_constraint(
        op.f("ck_commercial_terms_model_type_known"), "commercial_terms", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_commercial_terms_model_type_known"),
        "commercial_terms",
        _PREVIOUS_MODEL_TYPE_KNOWN_EXPRESSION,
    )
    op.execute("SET LOCAL lock_timeout = DEFAULT")
