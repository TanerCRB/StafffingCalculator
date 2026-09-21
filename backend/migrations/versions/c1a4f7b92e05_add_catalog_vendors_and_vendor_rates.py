"""add catalog vendors and the vendor column of a default rate

One rate row per dimension tuple per window was the whole point of `7b3d5c81e40a`. SC-2-03 (Issue
#46) adds the case that model cannot express: the same tuple priced at the same time by the
organisation itself *and* by one or more subcontractors. The vendor becomes the fifth column of the
`EXCLUDE` key.

**This migration rebuilds an existing constraint instead of expanding and later contracting, and
that is a named deviation from ADR-0001, not an oversight** (ADR-0001, addendum 2026-09-21, point 3;
ADR-0008, addendum 2026-09-21, point 4). The literal expand → deploy → contract shape was checked
and is not merely more expensive but unusable here: the old, four-column constraint refuses exactly
the rows this task exists to allow, so keeping it alive next to the new one would keep the feature
switched off. The factual basis for accepting that is that **no deployed environment exists** (open
decision #5) — the only databases are ephemeral test containers and local development ones. The
deviation expires the moment the first persistent environment is chosen.

What *is* backward compatible, and deliberately so: `vendor_id` is `NULL`-able with no
`server_default`, so an `INSERT` written by the code version deployed before this migration keeps
working and keeps meaning "internal rate".

Three things deserve reading twice:

1. **The key is an expression, not a column.**
   `COALESCE(vendor_id, '00000000-0000-0000-0000-000000000000'::uuid)` — the literal value
   `uuid_nil()` would return, spelled as a literal because `uuid-ossp` (the extension that defines
   `uuid_nil()`) is not installed on this database, only `btree_gist`. An `EXCLUDE` reports a
   violation only when *every* operator yields `TRUE`, and `NULL = NULL` yields `NULL`. A naive
   fifth column would therefore switch the overlap protection off for exactly the internal rates
   that have it today (criterion K-02).
2. **The sentinel cannot collide with a real vendor**, because `catalog_vendors` carries a CHECK
   refusing the nil UUID as an id. Constructive exclusion, not the improbability of `uuid4()`.
3. **`catalog_vendors` is the fifth dictionary**, built from the same two rules as the other four:
   a non-blank CHECK and a unique index on the *normalised* name (R-04).

Revision ID: c1a4f7b92e05
Revises: b6d2f74c3e18
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "c1a4f7b92e05"
down_revision: str | None = "b6d2f74c3e18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled here as well as in `app.models.catalog` rather than imported from it — same reason as in
# `7b3d5c81e40a`: a migration has to keep describing the schema it produced even after the model
# moves on. `test_the_model_and_the_migration_agree_on_every_sql_expression` compares the copies.
_VENDOR_TABLE = "catalog_vendors"

# `regexp_replace` before `btrim` (see `7b3d5c81e40a`): one-argument `btrim` strips spaces only.
_DIMENSION_NAME_KEY_EXPRESSION = r"lower(btrim(regexp_replace(name, '\s+', ' ', 'g')))"

_VENDOR_KEY_SENTINEL = "00000000-0000-0000-0000-000000000000"
_VENDOR_KEY_EXPRESSION = f"COALESCE(vendor_id, '{_VENDOR_KEY_SENTINEL}'::uuid)"

_NO_OVERLAP_CONSTRAINT = "ex_catalog_default_rates_no_overlapping_periods"
"""The same name as before. The constraint is *rebuilt*, not renamed: a second name would leave the
repository with two spellings of one guarantee, and `pg_constraint` is asserted to hold exactly one
exclusion constraint on this table (K-08)."""

# The four business dimensions, unchanged, plus the vendor expression. Order matters only for
# readability — PostgreSQL requires every operator to match for a violation.
_DIMENSION_KEY = ("role_id", "seniority_id", "location_id", "engagement_type_id")
_EXCLUDE_KEY = (*_DIMENSION_KEY, _VENDOR_KEY_EXPRESSION)

_PREVIOUS_EXCLUDE_KEY = _DIMENSION_KEY
"""What `7b3d5c81e40a` created, kept here so `downgrade()` restores that exact shape rather than an
approximation of it."""


def _exclude_statement(key: Sequence[str]) -> str:
    return (
        f"ALTER TABLE catalog_default_rates ADD CONSTRAINT {_NO_OVERLAP_CONSTRAINT}"
        " EXCLUDE USING gist ("
        + ", ".join(f"{element} WITH =" for element in key)
        + ", valid_period WITH &&)"
    )


def upgrade() -> None:
    op.create_table(
        _VENDOR_TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'", name=op.f(f"ck_{_VENDOR_TABLE}_name_not_blank")
        ),
        # The half of the sentinel decision that makes it safe: no vendor may *be* the value the
        # `EXCLUDE` key uses to mean "no vendor". Without it, "internal" and "the vendor whose id
        # happens to be the nil UUID" would be the same row as far as the constraint is concerned,
        # and the protection would be a statement about `uuid4()`'s odds rather than about the
        # schema (ADR-0008, addendum 2026-09-21, point 3).
        sa.CheckConstraint(
            f"id <> '{_VENDOR_KEY_SENTINEL}'::uuid",
            name=op.f(f"ck_{_VENDOR_TABLE}_id_is_not_the_exclude_sentinel"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_VENDOR_TABLE}")),
    )
    op.execute(
        f"CREATE UNIQUE INDEX uq_{_VENDOR_TABLE}_name_normalized"
        f" ON {_VENDOR_TABLE} ({_DIMENSION_NAME_KEY_EXPRESSION})"
    )

    # Nullable, no server default: `NULL` *is* the representation of an internal rate (ADR-0008,
    # addendum 2026-09-21, point 3), and an `INSERT` from the previous code version stays valid.
    op.add_column("catalog_default_rates", sa.Column("vendor_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_catalog_default_rates_vendor_id",
        "catalog_default_rates",
        _VENDOR_TABLE,
        ["vendor_id"],
        ["id"],
    )

    # Drop then create, in one migration and in this order: the two shapes cannot coexist, because
    # the old one refuses precisely the rows the new one exists to admit. See the module docstring
    # and ADR-0001's addendum of 2026-09-21 for why that is an accepted deviation here and what
    # ends it.
    op.execute(f"ALTER TABLE catalog_default_rates DROP CONSTRAINT {_NO_OVERLAP_CONSTRAINT}")
    op.execute(_exclude_statement(_EXCLUDE_KEY))


def downgrade() -> None:
    # R-03 (reviewer, 2026-09-21): the guard below is a live `SELECT` against the actual table, and
    # `alembic downgrade --sql` (documented review practice, `4f0a9c1b7d62`) never executes
    # anything — `op.get_bind()` returns `None` in offline mode, and `.scalar_one()` on that is an
    # opaque `AttributeError` instead of a diagnosis. Refuse in words instead of crashing.
    if context.is_offline_mode():
        raise RuntimeError(
            f"downgrade of {revision} cannot run in --sql (offline) mode: the vendor-row guard "
            "reads catalog_default_rates live before deciding whether to proceed, and offline mode "
            "has no connection to read it from. Run this downgrade online."
        )

    # The result this migration must not produce, empirically confirmed (reviewer, 2026-09-21): a
    # tuple priced *only* by a subcontractor collides with nothing under the four-column
    # constraint, because there is no internal row and no other vendor's row at that tuple/window
    # to collide with. Restoring that constraint therefore succeeds on it — the row survives — and
    # the column drop that follows removes the one fact (`vendor_id`) that distinguished it from
    # an internal rate. The same price, the same tuple, the same window, now read as "the
    # organisation's own": exactly the reinterpretation K-04 forbids, reached through `alembic
    # downgrade` instead of through the API. It is a strictly larger set of rows than the ones
    # that fail the constraint restore below — a colliding vendor row is caught there; a
    # non-colliding one is not, and is the one this guard exists for. The guard refuses on *any*
    # `vendor_id IS NOT NULL` row, not only a colliding one, before any schema change runs.
    #
    # R-01 (reviewer, 2026-09-21): the guard is check-then-act unless nothing can write between the
    # check and the act. `LOCK TABLE ... IN ACCESS EXCLUSIVE MODE` before the `SELECT` closes that
    # window — every statement in this migration runs inside one transaction (`migrations/env.py`),
    # so the lock is held from here through the `drop_column` below, and any concurrent writer
    # blocks on the lock (or is already committed and counted) rather than slipping a fresh
    # `vendor_id` row in between the count and the schema change. This is *not* what the four-column
    # `EXCLUDE` restore protects, and it is not what it was ever going to protect: that constraint
    # only ever catches a *colliding* row, and the case this guard exists for is a non-colliding one
    # (see above) — the lock's job is solely to stop a new row of either kind from being written
    # after the count has already been taken as final.
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE catalog_default_rates IN ACCESS EXCLUSIVE MODE"))
    vendor_rate_count = connection.execute(
        sa.text("SELECT count(*) FROM catalog_default_rates WHERE vendor_id IS NOT NULL")
    ).scalar_one()
    if vendor_rate_count:
        raise RuntimeError(
            f"Refusing to downgrade {revision}: {vendor_rate_count} row(s) of "
            "catalog_default_rates have vendor_id IS NOT NULL. Downgrading drops the vendor_id "
            "column, which would silently rename every one of those rows into an internal rate "
            "indistinguishable from one priced by the organisation itself — the outcome forbidden "
            "by K-04, reached here through a migration rather than through the API. Reassign or "
            "remove these vendor rates before downgrading."
        )

    # With the guard above satisfied under the table lock taken before it, no row of
    # catalog_default_rates has vendor_id set and none can appear before this transaction commits,
    # so restoring the four-column constraint cannot fail on a genuine collision either.
    op.execute(f"ALTER TABLE catalog_default_rates DROP CONSTRAINT {_NO_OVERLAP_CONSTRAINT}")
    op.execute(_exclude_statement(_PREVIOUS_EXCLUDE_KEY))
    op.drop_constraint(
        "fk_catalog_default_rates_vendor_id", "catalog_default_rates", type_="foreignkey"
    )
    op.drop_column("catalog_default_rates", "vendor_id")
    op.drop_table(_VENDOR_TABLE)
    # `btree_gist` stays (ADR-0008, point 5) — it is an object of the database, not of this table.
