"""create commercial_terms, tm_terms and the catalogue rate snapshot

SC-4-01 (F-06.1, Issue #8; ADR-0003, ADR-0004 addendum 2026-09-23 SC-4-01). Expand only (ADR-0001,
expand → deploy → contract): three new tables. No existing table gains or loses a column, no
constraint is altered, no row is written — so the code deployed before this migration keeps working
against the new schema, and there is no contract phase to pair with it.

**What the database enforces here, and why in the schema** (a fixture, a seed script or a future
import never passes through a Pydantic model):

1. `commercial_terms.scenario_id` is `NOT NULL` and `UNIQUE` — one rule per scenario in the MVP
   (ADR-0003, point 1). A second rule for one scenario is refused by the unique index inside the
   `INSERT`, two connections included, not by a `SELECT` before it.
2. `commercial_terms.model_type` is closed by a CHECK to the models that have a details table —
   today only `time_and_material` (point 2). Every later model widens this CHECK in the migration
   that creates its own details table.
3. **Type agreement between a rule and its details row is a composite foreign key**, not an
   application check (point 3; criterion K-04): `tm_terms (commercial_terms_id, model_type) →
   commercial_terms (id, model_type)`, with `UNIQUE (id, model_type)` on the parent and
   `CHECK (model_type = 'time_and_material')` on the child. A T&M details row pointing at a rule of
   another model is unwritable.
4. `approved_snapshot_catalog_default_rate` follows the snapshot pattern of `f3a1d0c58b27` without
   a change (ADR-0004, addendum 2026-09-22 SC-3-02, point 3): keyed by `scenario_id`, the only
   foreign key points at `scenarios`, every source id is a plain `uuid` value, no `updated_at`, no
   CHECK repeated from the source. It carries a generated `valid_period` built from the **same
   expression** as `catalog_default_rates.valid_period`, so the snapshot reader asks the frozen rows
   exactly the question the live read asks the catalogue (ADR-0004, addendum SC-4-01, point 2e).

**What is deliberately not here:**

- **No `ON DELETE` action on any foreign key.** A cascade towards `scenarios` would be a second,
  unguarded way for the rows of an `approved` scenario to disappear (ADR-0003, point 1).
- **No `valid_period`, `effective_from`/`effective_to` or `EXCLUDE` on `commercial_terms`**
(ADR-0008,
  addendum 2026-09-23 SC-4-01): one rule per scenario, versioned by ADR-0004's copy-and-guard
  mechanism rather than by a date window.
- **No domain column on `tm_terms`.** It exists now because the type-agreement mechanism has to be
  proven the day the discriminator appears (ADR-0003, point 3); T&M fields arrive with the task that
  needs them.
- **No `updated_at` on `tm_terms`.** ADR-0007's marker for the rule is
`commercial_terms.updated_at`,
  covering its details row the way a position's marker covers its months (ADR-0003, "Konsekwencje").

Revision ID: e7b41c9d2a58
Revises: c8e2a4f61d93
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7b41c9d2a58"
down_revision: str | None = "c8e2a4f61d93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06). Here it covers the three foreign
keys to `scenarios`: creating one takes `SHARE ROW EXCLUSIVE` on the referenced table, so the
risk is the waiting behind a long reader of `scenarios`, exactly as for an `ALTER`."""

_MODEL_TYPE_TIME_AND_MATERIAL = "time_and_material"

_MODEL_TYPE_KNOWN_EXPRESSION = "model_type IN ('time_and_material')"
"""Spelled here as well as in `app.models.commercial_terms` rather than imported from it: a
migration must keep describing the schema it produced even after the model moves on (the rule of
`f3a1d0c58b27`). `tests/test_commercial_terms_schema.py` keeps the two copies honest."""

_TM_MODEL_TYPE_EXPRESSION = "model_type = 'time_and_material'"

_VALID_PERIOD_EXPRESSION = "daterange(effective_from, (effective_to + 1), '[)')"
"""Identical to `catalog_default_rates.valid_period` (migration `7b3d5c81e40a`). Identical on
purpose: the snapshot reader resolves a month with the same containment as the live read, and a
second spelling of the window's boundary is how the two would disagree by a day (ADR-0008, point
3). A drift guard compares this copy with `app.models.catalog.VALID_PERIOD_EXPRESSION`."""

_RATE = sa.Numeric(precision=14, scale=4)
"""`NUMERIC(14,4)`, the source's own type (`catalog_default_rates`), so nothing is narrowed on the
way into the snapshot."""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    # --- the rule of one scenario (ADR-0003, points 1-2; ADR-0004 group 2) ----------------------
    op.create_table(
        "commercial_terms",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("model_type", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _MODEL_TYPE_KNOWN_EXPRESSION, name=op.f("ck_commercial_terms_model_type_known")
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], name=op.f("fk_commercial_terms_scenario_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commercial_terms")),
        sa.UniqueConstraint("scenario_id", name=op.f("uq_commercial_terms_scenario_id")),
        # The parent half of the composite foreign key below. Redundant as a uniqueness claim (`id`
        # alone is the primary key) and required anyway: PostgreSQL accepts a foreign key only
        # against a unique constraint on exactly the referenced columns.
        sa.UniqueConstraint("id", "model_type", name=op.f("uq_commercial_terms_id_model_type")),
    )

    # --- the T&M details row, 1:1, type agreement in the database (ADR-0003, point 3) -----------
    op.create_table(
        "tm_terms",
        sa.Column("commercial_terms_id", sa.UUID(), nullable=False),
        sa.Column(
            "model_type",
            sa.String(length=40),
            server_default=sa.text(f"'{_MODEL_TYPE_TIME_AND_MATERIAL}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_TM_MODEL_TYPE_EXPRESSION, name=op.f("ck_tm_terms_model_type_is_tm")),
        sa.ForeignKeyConstraint(
            ["commercial_terms_id", "model_type"],
            ["commercial_terms.id", "commercial_terms.model_type"],
            name=op.f("fk_tm_terms_commercial_terms_model_type"),
        ),
        sa.PrimaryKeyConstraint("commercial_terms_id", name=op.f("pk_tm_terms")),
    )

    # --- the sixth snapshot table (ADR-0004, addendum 2026-09-23 SC-4-01, point 2) --------------
    op.create_table(
        "approved_snapshot_catalog_default_rate",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        # Values, never foreign keys (ADR-0004, addendum 2026-09-22 SC-3-02, point 3b).
        sa.Column("source_rate_id", sa.UUID(), nullable=False),
        sa.Column("source_role_id", sa.UUID(), nullable=False),
        sa.Column("source_seniority_id", sa.UUID(), nullable=False),
        sa.Column("source_location_id", sa.UUID(), nullable=False),
        sa.Column("source_engagement_type_id", sa.UUID(), nullable=False),
        sa.Column("source_vendor_id", sa.UUID(), nullable=True),
        # Both rates, although SC-4-01 reads only the selling one (addendum SC-4-01, point 2b): the
        # snapshot has no UPDATE path, so a cost not frozen now is a cost never recovered.
        sa.Column("default_cost_rate", _RATE, nullable=False),
        sa.Column("default_selling_rate", _RATE, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "valid_period",
            postgresql.DATERANGE(),
            sa.Computed(_VALID_PERIOD_EXPRESSION, persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_approved_snapshot_catalog_default_rate_scenario_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approved_snapshot_catalog_default_rate")),
    )
    op.create_index(
        op.f("ix_approved_snapshot_catalog_default_rate_scenario_id"),
        "approved_snapshot_catalog_default_rate",
        ["scenario_id"],
    )

    # `SET LOCAL` lasts to the end of the transaction; reset so the bound does not silently apply to
    # every migration after this one in the same batch (the convention of `a7c2e5f81b94`).
    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`, children before parents.

    Data loss by definition, like every downgrade of an expand migration: it exists so the migration
    can be tested both ways, not as a deployment path. Dropping these tables reinterprets no
    surviving row — every scenario goes back to having no commercial rule, which is the named
    `no_commercial_terms` state rather than a revenue of zero.
    """
    op.drop_index(
        op.f("ix_approved_snapshot_catalog_default_rate_scenario_id"),
        table_name="approved_snapshot_catalog_default_rate",
    )
    op.drop_table("approved_snapshot_catalog_default_rate")
    op.drop_table("tm_terms")
    op.drop_table("commercial_terms")
