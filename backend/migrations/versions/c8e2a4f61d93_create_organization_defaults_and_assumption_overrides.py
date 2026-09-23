"""create organization defaults, the project and scenario overrides, and their snapshot

SC-1-10 (F-02, ADR-0012). Expand only (ADR-0001, expand → deploy → contract): two new tables and
three new **nullable** columns without a default on existing tables. Nothing is dropped, nothing is
altered, no existing column changes meaning, and no row is written — so the code deployed before
this migration keeps working against the new schema, and there is no contract phase to pair with it.

**No seed row** (gate 1, P-D). `organization_defaults` is created empty: "the organisation has no
default" is the named "no value" state of ADR-0012 (point 1), and a number chosen here would be a
business decision taken by a migration and applied, silently, to every draft that inherits it.

**What the database enforces, and why in the schema** (a fixture, a seed script or a future import
never sees a Pydantic model):

1. `organization_defaults` holds **at most one row**: its primary key is a `smallint` pinned to `1`
   by a CHECK. A second `INSERT` is refused by the primary key inside the statement that attempts it
   — two connections included — rather than by a `SELECT` before it.
2. `overload_threshold_percent > 0` on **all three levels** of the chain (criterion K-09; gate 1,
   G-2). Two of the three levels have no API path at all in this task, so a request-schema rule
   would guard one of three.
3. No CHECK on any `target_margin_percent`: `scenarios.target_margin_percent` has never had one, and
   `0` is a legal margin (ADR-0012, point 1).

**What the `ALTER`s on the two populated tables cost, stated rather than optimised away.** Each new
column is nullable with no default, so `ADD COLUMN` is catalogue-only. `ADD CONSTRAINT … CHECK` is
not: it scans the table to validate existing rows, under the `ACCESS EXCLUSIVE` lock the `ALTER`
already holds. A `NOT VALID` + `VALIDATE CONSTRAINT` split would *not* shorten that here —
`migrations/env.py` runs the whole batch in one transaction, so the `ACCESS EXCLUSIVE` taken by
`ADD COLUMN` is held to the commit anyway — and it was tried and taken out for that reason. The scan
reads `projects` and `scenarios`, which hold one row per project and per scenario, and the column it
checks was added a statement earlier and holds nothing but `NULL`. The `lock_timeout` convention of
`d5e94a1c6b73` (R-06) bounds the *waiting* for every lock here, which is where the risk is.

Revision ID: c8e2a4f61d93
Revises: a7c2e5f81b94
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2a4f61d93"
down_revision: str | None = "a7c2e5f81b94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06) — one answer to "how long does a
migration wait for a lock", not a second one."""

_PERCENT = sa.Numeric(precision=6, scale=3)
"""`NUMERIC(6, 3)`, the type `scenarios.target_margin_percent` has had since the first migration.
Spelled here rather than imported from `app.models.organization_defaults`: a migration describes the
schema it produced, and an import would rewrite that history the next time the model changes."""

_THRESHOLD_POSITIVE = "overload_threshold_percent > 0"

_OVERRIDE_TABLES = ("projects", "scenarios")
"""The two existing tables that gain an override column. `projects` gains both overrides;
`scenarios` gains only the threshold, because `scenarios.target_margin_percent` already exists."""


def upgrade() -> None:
    # --- the top of the chain: the organisation's defaults (ADR-0012, points 1, 3) -------------
    op.create_table(
        "organization_defaults",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("target_margin_percent", _PERCENT, nullable=True),
        sa.Column("overload_threshold_percent", _PERCENT, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_organization_defaults_singleton")),
        sa.CheckConstraint(
            _THRESHOLD_POSITIVE,
            name=op.f("ck_organization_defaults_overload_threshold_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_defaults")),
    )

    # --- the project and scenario levels (ADR-0012, point 5; ADR-0004 group 2 for the project) --
    # Nullable, no default: `ADD COLUMN` is catalogue-only. The CHECK scans each table once under
    # the lock already held (the module docstring says why that is not split into `NOT VALID`).
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.add_column("projects", sa.Column("target_margin_percent", _PERCENT, nullable=True))
    for table in _OVERRIDE_TABLES:
        op.add_column(table, sa.Column("overload_threshold_percent", _PERCENT, nullable=True))
        op.create_check_constraint(
            op.f(f"ck_{table}_overload_threshold_positive"), table, _THRESHOLD_POSITIVE
        )
    # `SET LOCAL` lasts to the end of the transaction; without this the bound would silently apply
    # to every migration after this one in the same batch (the convention of `a7c2e5f81b94`).
    op.execute("SET LOCAL lock_timeout = DEFAULT")

    # --- the fifth snapshot table (ADR-0012, point 6; ADR-0004 addendum 2026-09-22, point 3) ----
    # Raw values, never the result of the chain and never a `source` column: the level a value came
    # from is decided when the snapshot is read (gate 1, P-A). The only foreign key points at
    # `scenarios`, with no `ON DELETE` action, like every snapshot table before it. No CHECK is
    # repeated from the source: a snapshot records what was approved, it does not re-judge it.
    op.create_table(
        "approved_snapshot_organization_defaults",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("target_margin_percent", _PERCENT, nullable=True),
        sa.Column("overload_threshold_percent", _PERCENT, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_approved_snapshot_organization_defaults_scenario_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approved_snapshot_organization_defaults")),
    )
    op.create_index(
        op.f("ix_approved_snapshot_organization_defaults_scenario_id"),
        "approved_snapshot_organization_defaults",
        ["scenario_id"],
    )


def downgrade() -> None:
    """Reverse of `upgrade`, children before parents.

    Data loss by definition, like every downgrade of an expand migration (see `a7c2e5f81b94`): it
    exists so the migration can be tested both ways, not as a deployment path. No guard of the
    `c1a4f7b92e05` kind: dropping these columns reinterprets no surviving row — every scenario and
    project goes back to having no override, and the only reader of the overrides goes with them.
    """
    op.drop_index(
        op.f("ix_approved_snapshot_organization_defaults_scenario_id"),
        table_name="approved_snapshot_organization_defaults",
    )
    op.drop_table("approved_snapshot_organization_defaults")

    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    for table in _OVERRIDE_TABLES:
        # `op.f`: the name is final — without it the metadata's naming convention prefixes it again.
        op.drop_constraint(
            op.f(f"ck_{table}_overload_threshold_positive"), table, type_="check"
        )
        op.drop_column(table, "overload_threshold_percent")
    op.drop_column("projects", "target_margin_percent")
    op.execute("SET LOCAL lock_timeout = DEFAULT")

    op.drop_table("organization_defaults")
