"""create catalog dimensions and default rates

Expand only (ADR-0001): five new tables and one extension, nothing dropped and nothing altered, so
the code version deployed before this migration keeps working against the new schema. There is no
contract phase to pair with it — no column of an existing table changes meaning.

**First use of ADR-0008's effective-range pattern in this repository.** Three things here exist
because that decision says so, and every later table that needs an effective range (`exchange_rates`
— ADR-0006, `commercial_terms` — ADR-0003) inherits them:

1. `CREATE EXTENSION IF NOT EXISTS btree_gist`, before the `EXCLUDE`. `btree_gist` is not needed for
   `&&` on a `daterange` (gist handles that natively) but for the `=` operator on the four `uuid`
   dimension columns *inside the same gist index*. `IF NOT EXISTS` because the extension is an
   object of the database, not of this table.
2. The generated column `valid_period`, which is the single place `effective_to`'s inclusiveness is
   converted to PostgreSQL's canonical half-open `[)` form. `effective_to + 1` over `NULL` is
   `NULL`, which `daterange` reads as unbounded — an open-ended window therefore needs no sentinel
   date.
3. `EXCLUDE USING gist` over the full dimension tuple plus `valid_period WITH &&`. The currency is
   deliberately **not** in the key (ADR-0008, point 4).

The downgrade drops the five tables and **does not drop the extension** (ADR-0008, point 5): it is a
database-wide object that the two tables above will share, so removing it here would break them
instead of cleaning up after this migration.

The privilege `CREATE EXTENSION` needs is documented in `backend/README.md`. It is verified in CI
against a testcontainers PostgreSQL whose role is a superuser — which proves the mechanism and
proves nothing about the target environment's application role (ADR-0008, point 7).

Revision ID: 7b3d5c81e40a
Revises: 4f0a9c1b7d62
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7b3d5c81e40a"
down_revision: str | None = "4f0a9c1b7d62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIMENSION_TABLES = (
    "catalog_roles",
    "catalog_seniorities",
    "catalog_locations",
    "catalog_engagement_types",
)

# The two SQL expressions below are spelled here as well as in `app.models.catalog` rather than
# imported from it: a migration must keep describing the schema it produced even after the model
# moves on, and an import would silently re-write history the next time somebody edits the model.
# What keeps the copies honest (R-02, reviewer + invariant-guardian 2026-09-19): `alembic upgrade`
# ignores the model's `Computed(...)`, so editing one copy alone used to break nothing. Two tests
# in `tests/test_catalog_schema_constraints.py` close that: one compares these strings with the
# model's constants (`test_the_model_and_the_migration_agree_on_every_sql_expression`), the other
# reads back what the migrated database really generates
# (`test_k_05_valid_period_is_generated_by_the_…`).
_VALID_PERIOD_EXPRESSION = "daterange(effective_from, (effective_to + 1), '[)')"

# `regexp_replace` before `btrim`, not after: one-argument `btrim` strips spaces only, so trimming
# first would leave a leading tab in place and make `'\tSenior\n'` a different name from `'Senior'`.
_DIMENSION_NAME_KEY_EXPRESSION = r"lower(btrim(regexp_replace(name, '\s+', ' ', 'g')))"

_DIMENSION_KEY = ("role_id", "seniority_id", "location_id", "engagement_type_id")


def upgrade() -> None:
    # Before the EXCLUDE that needs it, and separate from it, so a failure here names the privilege
    # that is missing instead of a constraint that could not be built.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    for table in _DIMENSION_TABLES:
        op.create_table(
            table,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            # `NOT NULL` admits `''` and `'   '`; this is the same claim the API boundary makes with
            # `strip_whitespace=True` (cf. migration `4f0a9c1b7d62` for `projects`).
            sa.CheckConstraint("name ~ '[^[:space:]]'", name=op.f(f"ck_{table}_name_not_blank")),
            sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        )
        # Uniqueness on the *normalised* name, and no plain `UNIQUE (name)` beside it: a plain one
        # refuses a strict subset of this (it let "Senior", "senior" and "Senior  Dev" coexist —
        # R-04), so keeping both would only make the reported constraint depend on which PostgreSQL
        # checked first. Written as DDL because an expression index has no `op.create_table`
        # equivalent, and because this is the statement `alembic upgrade head --sql` is reviewed on.
        op.execute(
            f"CREATE UNIQUE INDEX uq_{table}_name_normalized"
            f" ON {table} ({_DIMENSION_NAME_KEY_EXPRESSION})"
        )

    op.create_table(
        "catalog_default_rates",
        sa.Column("id", sa.UUID(), nullable=False),
        # All four dimensions NOT NULL: this is the key of the EXCLUDE constraint, and a nullable
        # dimension would mean "any", i.e. a second resolution mechanism nobody named.
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("seniority_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("engagement_type_id", sa.UUID(), nullable=False),
        # NUMERIC(14,4) — scale larger than any currency's minor unit (ADR-0008, point 6). A rate is
        # input, not the result of a rounding step: NUMERIC(12,2) would change 12.345 on write.
        sa.Column("default_cost_rate", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("default_selling_rate", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "unit", sa.String(length=20), server_default="hour", nullable=False
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        # NULL = open-ended, and inclusive when set (ADR-0008, points 2-3).
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "valid_period",
            postgresql.DATERANGE(),
            sa.Computed(_VALID_PERIOD_EXPRESSION, persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # The unit refusal has to live here and not only in the request schema: criterion K-07
        # requires the path that does not go through Pydantic (a fixture, a seed script, an import)
        # to be refused as well.
        sa.CheckConstraint(
            "unit = 'hour'", name=op.f("ck_catalog_default_rates_unit_is_hour")
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3",
            name=op.f("ck_catalog_default_rates_currency_iso4217"),
        ),
        sa.CheckConstraint(
            "currency = upper(currency)",
            name=op.f("ck_catalog_default_rates_currency_is_upper"),
        ),
        # Load-bearing for the EXCLUDE, not cosmetic: `effective_to` one day before
        # `effective_from` produces an *empty* daterange, and `&&` is false against an empty range —
        # the overlap constraint would silently stop covering that row.
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_catalog_default_rates_effective_period_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["catalog_roles.id"], name=op.f("fk_catalog_default_rates_role_id")
        ),
        sa.ForeignKeyConstraint(
            ["seniority_id"],
            ["catalog_seniorities.id"],
            name=op.f("fk_catalog_default_rates_seniority_id"),
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["catalog_locations.id"],
            name=op.f("fk_catalog_default_rates_location_id"),
        ),
        sa.ForeignKeyConstraint(
            ["engagement_type_id"],
            ["catalog_engagement_types.id"],
            name=op.f("fk_catalog_default_rates_engagement_type_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_default_rates")),
    )
    # Written as DDL rather than through `sa.ExcludeConstraint` inside `create_table`: this is the
    # statement `alembic upgrade head --sql` has to be reviewed on, and the reviewer of the first
    # use of this pattern in the repository should see the operators, not a Python expression that
    # renders into them. The gist index this constraint creates also serves the resolution lookup
    # (`valid_period @> :on_date`), so no separate index is added.
    op.execute(
        "ALTER TABLE catalog_default_rates"
        " ADD CONSTRAINT ex_catalog_default_rates_no_overlapping_periods"
        " EXCLUDE USING gist ("
        + ", ".join(f"{column} WITH =" for column in _DIMENSION_KEY)
        + ", valid_period WITH &&)"
    )


def downgrade() -> None:
    op.drop_table("catalog_default_rates")
    for table in reversed(_DIMENSION_TABLES):
        op.drop_table(table)
    # `btree_gist` stays. It is an object of the database, not of these tables, and ADR-0006 /
    # ADR-0003 tables inherit it — dropping it here would break them rather than undo this
    # migration. There are no enum types to clean up (the dimensions are data, not enums — NF-10).
