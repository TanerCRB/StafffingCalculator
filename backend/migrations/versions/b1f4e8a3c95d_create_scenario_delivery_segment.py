"""create scenario_delivery_segment

SC-1-11 (F-02, F-06; Issue #65; ADR-0016, and its addendum of 2026-09-25 to
`ADR-0004-wersjonowanie-kalkulacji.md`). Expand only (ADR-0001, expand → deploy → contract): one new
table, no column added to, dropped from or altered on an existing table, no existing constraint
changed, no row written — so the code deployed before this migration keeps working against the new
schema and there is no contract phase to pair with it.

**What the database enforces here, and why in the schema** (a fixture, a seed script or a future
import never passes through a Pydantic model — there is no Pydantic model for this table in this
task at all, ADR-0016 point 8):

1. `scenario_delivery_segment.scenario_id` `NOT NULL`, foreign key to `scenarios.id`, no `ON DELETE`
   action (ADR-0016, point 2; ADR-0003, point 1) — a segment naming a non-existent or another
   scenario is refused by the database (criterion K-01).
2. `name` `VARCHAR(200) NOT NULL` plus `CHECK (name ~ '[^[:space:]]')` — the same pattern as
   `projects.name`/`client`/`owner` (`4f0a9c1b7d62`): `NOT NULL` alone admits `''` and whitespace.
3. `UNIQUE (id, scenario_id)` (`uq_scenario_delivery_segment_id_scenario_id`) — redundant as a
   uniqueness claim (`id` is the primary key) and required anyway: PostgreSQL accepts a composite
   foreign key only against a uniqueness constraint on exactly the referenced columns. Prepares the
   ground for a future `(segment_id, scenario_id) → scenario_delivery_segment (id, scenario_id)`
   foreign key (SC-4-05) without needing a second migration to add it (criterion K-03).
4. `UNIQUE (scenario_id, name)` (`uq_scenario_delivery_segment_scenario_id_name`) — two segments of
   one scenario cannot share a name (criterion K-09); scoped to `scenario_id`, so a copy of the
   scenario (a fresh `scenario_id`) never collides with its source.

**What is deliberately not here:** an `EXCLUDE`/effective-date interval (ADR-0016, point 6 — no
`EXCLUDE`, overlapping segments are legal), a snapshot table (ADR-0004, addendum 2026-09-25 SC-1-11:
group 2, protected by the write guard, not copied at approval), any amount/currency/hours/allocation
column or `position_id` (criterion K-05; F-04 out of scope), and any `ON DELETE` action.

Revision ID: b1f4e8a3c95d
Revises: a3d9e6f20c71
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1f4e8a3c95d"
down_revision: str | None = "a3d9e6f20c71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06) — the foreign key to `scenarios`
waits for a lock on a table other sessions read."""

# Spelled here as well as in the model rather than imported from it: a migration must keep
# describing the schema it produced even after the model moves on (the rule of `f3a1d0c58b27`).
# `tests/test_scenario_delivery_segment_schema.py` keeps the copies honest.
_TABLE = "scenario_delivery_segment"
_NAME_NOT_BLANK_EXPRESSION = "name ~ '[^[:space:]]'"
_ID_SCENARIO_UNIQUE = "uq_scenario_delivery_segment_id_scenario_id"
_SCENARIO_NAME_UNIQUE = "uq_scenario_delivery_segment_scenario_id_name"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _NAME_NOT_BLANK_EXPRESSION, name=op.f(f"ck_{_TABLE}_name_not_blank")
        ),
        # No `ondelete`: a cascade would be a second, unguarded way for the rows of an `approved`
        # scenario to disappear (ADR-0003, point 1).
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], name=op.f(f"fk_{_TABLE}_scenario_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_TABLE}")),
        sa.UniqueConstraint("id", "scenario_id", name=op.f(_ID_SCENARIO_UNIQUE)),
        sa.UniqueConstraint("scenario_id", "name", name=op.f(_SCENARIO_NAME_UNIQUE)),
    )
    op.create_index(op.f(f"ix_{_TABLE}_scenario_id"), _TABLE, ["scenario_id"])

    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`. Data loss by definition, like every downgrade of an expand migration:
    it exists so the migration can be tested both ways, not as a deployment path. Dropping this
    table reinterprets no surviving row — no other table points at it yet."""
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.drop_index(op.f(f"ix_{_TABLE}_scenario_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
    op.execute("SET LOCAL lock_timeout = DEFAULT")
