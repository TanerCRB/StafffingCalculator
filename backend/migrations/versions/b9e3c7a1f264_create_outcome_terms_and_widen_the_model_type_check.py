"""create outcome_terms and widen the commercial model discriminator

SC-4-03 (F-06.3, Issue #67; ADR-0003 addendum 2026-09-25 SC-4-03, ADR-0004 addendum 2026-09-25
SC-4-03). Expand only (ADR-0001, expand → deploy → contract): one new table and one widened
constraint. No column of an existing table is added, dropped or altered, no row is written — the
code deployed before this migration keeps working on the new schema. In the mixed-version window,
an instance of the older code that meets an `outcome_based` rule responds with the named state
`unsupported_model_type` (read) and `409` (copy), never half of the aggregate (SC-4-01,
R-02/R-03).

**The only statement touching an existing table**: `ck_commercial_terms_model_type_known` is
dropped and recreated with the **full `IN` list** — `time_and_material`, `story_points` and
`outcome_based` (ADR-0003, addendum SC-4-03, point 10c). A migration carrying only its own value
would silently invalidate the recorded T&M and Story Points rules when the constraint is
validated; `downgrade` recreates the list from before this migration (`time_and_material`,
`story_points` — exactly the expression from `d2f6a91c4b58`, unchanged by `b7e3f19a6c52`), not an
empty or single-value list with the new value. Recreating it validates the existing rows under an
`ACCESS EXCLUSIVE` lock on `commercial_terms` — the table is small (one whole-scenario rule and at
most one per segment, SC-4-05), and `lock_timeout` below bounds the wait.

**Linearisation (human decision 2026-09-25):** this migration was created in parallel with SC-4-04
on `a3d9e6f20c71`; when merging `main`, its `down_revision` was repointed to `d2f6a91c4b58` (Story
Points), so the history has one head, and the `IN` list covers both earlier models. On the second
merge from `main` (SC-4-05, `scope_ref`), it was repointed again, to `b7e3f19a6c52` — that
migration does not touch `ck_commercial_terms_model_type_known`, so the list from before this
migration remains the list from `d2f6a91c4b58`.

**What the database enforces in `outcome_terms`** (a fixture, a seed script or an import
never passes through Pydantic):

1. type agreement through the composite foreign key `(commercial_terms_id, model_type) →
   commercial_terms (id, model_type)` and `CHECK (model_type = 'outcome_based')` — the `tm_terms`
   pattern, unchanged (point 1);
2. the fixed fee `NOT NULL`, the optional components `NULL` — never `0` for absence (point 2); all
   amounts, the rate and the unit counts non-negative; `revenue_min <= revenue_max` when both
   are set;
3. four categories as columns, probabilities `NUMERIC(5,2)`, "all `NULL` or the sum exactly 100" as
   a single-row `CHECK` (points 3-4); unit counts `NULL` allowed only without a unit rate — with a
   rate all four `NOT NULL` (a single-row `CHECK`);
4. the rule's currency: the same two `CHECK`s as in the catalogue (point 7).

**What is deliberately not here:** an `ON DELETE` on the foreign key (a cascade would be a second,
unguarded way for an approved scenario's row to disappear), `updated_at` (the marker belongs to the
rule), a snapshot table (ADR-0004, addendum SC-4-03, point 4 — nothing outside the scenario).

Revision ID: b9e3c7a1f264
Revises: b7e3f19a6c52
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e3c7a1f264"
down_revision: str | None = "b7e3f19a6c52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06): recreating the CHECK on
`commercial_terms` and the foreign key to it wait for locks on a table read by other sessions."""

# Spelled here, not imported from the model: a migration must keep describing the schema it
# produced even when the model moves on (the rule of `f3a1d0c58b27`).
# `tests/test_outcome_terms_schema.py` keeps the copies honest.
_MODEL_TYPE_OUTCOME_BASED = "outcome_based"

_MODEL_TYPE_KNOWN_EXPRESSION = (
    "model_type IN ('time_and_material', 'story_points', 'outcome_based')"
)
"""The full `IN` list — the earlier models' values together with the new one (ADR-0003, addendum
SC-4-03, point 10c)."""

_PREVIOUS_MODEL_TYPE_KNOWN_EXPRESSION = "model_type IN ('time_and_material', 'story_points')"
"""The list from before this migration — exactly the expression from `d2f6a91c4b58` (SC-4-04),
unchanged by `b7e3f19a6c52` (SC-4-05); `downgrade` recreates it."""

_MODEL_TYPE_KNOWN = "ck_commercial_terms_model_type_known"

_OUTCOME_MODEL_TYPE_EXPRESSION = "model_type = 'outcome_based'"

_CATEGORIES = ("not_achieved", "partial", "achieved", "exceeded")

_PROBABILITIES_EXPRESSION = (
    "(not_achieved_probability IS NULL AND partial_probability IS NULL"
    " AND achieved_probability IS NULL AND exceeded_probability IS NULL)"
    " OR (not_achieved_probability IS NOT NULL AND partial_probability IS NOT NULL"
    " AND achieved_probability IS NOT NULL AND exceeded_probability IS NOT NULL"
    " AND not_achieved_probability + partial_probability + achieved_probability"
    " + exceeded_probability = 100)"
)

_UNITS_WITH_UNIT_RATE_EXPRESSION = (
    "unit_rate IS NULL OR (not_achieved_units IS NOT NULL AND partial_units IS NOT NULL"
    " AND achieved_units IS NOT NULL AND exceeded_units IS NOT NULL)"
)

_BOUNDS_ORDERED_EXPRESSION = (
    "revenue_min IS NULL OR revenue_max IS NULL OR revenue_min <= revenue_max"
)
_CURRENCY_ISO4217_EXPRESSION = "char_length(currency) = 3"
_CURRENCY_UPPER_EXPRESSION = "currency = upper(currency)"

_AMOUNT = sa.Numeric(precision=14, scale=4)
_UNITS = sa.Numeric(precision=14, scale=4)
_PROBABILITY = sa.Numeric(precision=5, scale=2)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    # --- discriminator: full `IN` list (ADR-0003, addendum SC-4-03, points 1 and 10c) -------------
    op.drop_constraint(op.f(_MODEL_TYPE_KNOWN), "commercial_terms", type_="check")
    op.create_check_constraint(
        op.f(_MODEL_TYPE_KNOWN), "commercial_terms", sa.text(_MODEL_TYPE_KNOWN_EXPRESSION)
    )

    # --- Outcome-based rule details, 1:1, type agreement in the database --------------------------
    category_columns: list[sa.Column] = []
    category_checks: list[sa.CheckConstraint] = []
    for category in _CATEGORIES:
        units, probability = f"{category}_units", f"{category}_probability"
        category_columns += [
            sa.Column(units, _UNITS, nullable=True),
            sa.Column(probability, _PROBABILITY, nullable=True),
        ]
        category_checks += [
            sa.CheckConstraint(
                f"{units} >= 0", name=op.f(f"ck_outcome_terms_{units}_not_negative")
            ),
            sa.CheckConstraint(
                f"{probability} >= 0", name=op.f(f"ck_outcome_terms_{probability}_not_negative")
            ),
        ]

    op.create_table(
        "outcome_terms",
        sa.Column("commercial_terms_id", sa.UUID(), nullable=False),
        sa.Column(
            "model_type",
            sa.String(length=40),
            server_default=sa.text(f"'{_MODEL_TYPE_OUTCOME_BASED}'"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("fixed_fee", _AMOUNT, nullable=False),
        sa.Column("success_bonus", _AMOUNT, nullable=True),
        sa.Column("unit_rate", _AMOUNT, nullable=True),
        sa.Column("revenue_min", _AMOUNT, nullable=True),
        sa.Column("revenue_max", _AMOUNT, nullable=True),
        *category_columns,
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _OUTCOME_MODEL_TYPE_EXPRESSION, name=op.f("ck_outcome_terms_model_type_is_outcome")
        ),
        sa.CheckConstraint(
            _CURRENCY_ISO4217_EXPRESSION, name=op.f("ck_outcome_terms_currency_iso4217")
        ),
        sa.CheckConstraint(
            _CURRENCY_UPPER_EXPRESSION, name=op.f("ck_outcome_terms_currency_is_upper")
        ),
        sa.CheckConstraint("fixed_fee >= 0", name=op.f("ck_outcome_terms_fixed_fee_not_negative")),
        sa.CheckConstraint(
            "success_bonus >= 0", name=op.f("ck_outcome_terms_success_bonus_not_negative")
        ),
        sa.CheckConstraint("unit_rate >= 0", name=op.f("ck_outcome_terms_unit_rate_not_negative")),
        sa.CheckConstraint(
            "revenue_min >= 0", name=op.f("ck_outcome_terms_revenue_min_not_negative")
        ),
        sa.CheckConstraint(
            "revenue_max >= 0", name=op.f("ck_outcome_terms_revenue_max_not_negative")
        ),
        sa.CheckConstraint(
            _BOUNDS_ORDERED_EXPRESSION, name=op.f("ck_outcome_terms_revenue_bounds_ordered")
        ),
        *category_checks,
        sa.CheckConstraint(
            _PROBABILITIES_EXPRESSION, name=op.f("ck_outcome_terms_probabilities_sum_to_100")
        ),
        sa.CheckConstraint(
            _UNITS_WITH_UNIT_RATE_EXPRESSION,
            name=op.f("ck_outcome_terms_units_given_with_unit_rate"),
        ),
        sa.ForeignKeyConstraint(
            ["commercial_terms_id", "model_type"],
            ["commercial_terms.id", "commercial_terms.model_type"],
            name=op.f("fk_outcome_terms_commercial_terms_model_type"),
        ),
        sa.PrimaryKeyConstraint("commercial_terms_id", name=op.f("pk_outcome_terms")),
    )

    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`: the details table, then the `IN` list from before this migration.

    Data loss by definition, like every downgrade of an expand migration — it exists so the
    migration can be tested both ways. The T&M and Story Points rules pass through unchanged. A
    remaining `outcome_based` rule (with no details left once the table is dropped) will make
    recreating the CHECK be refused by the database — deliberately: downgrade does not silently
    delete scenario rules.
    """
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.drop_table("outcome_terms")
    op.drop_constraint(op.f(_MODEL_TYPE_KNOWN), "commercial_terms", type_="check")
    op.create_check_constraint(
        op.f(_MODEL_TYPE_KNOWN), "commercial_terms", sa.text(_PREVIOUS_MODEL_TYPE_KNOWN_EXPRESSION)
    )
    op.execute("SET LOCAL lock_timeout = DEFAULT")
