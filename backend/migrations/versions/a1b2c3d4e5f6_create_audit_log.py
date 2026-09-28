"""create audit_log

Expand only (ADR-0001, expand -> deploy -> contract): one new table, one new enum type. Nothing
existing is dropped or altered, so code deployed before this migration keeps working unchanged
against the new schema.

**The first table of plan block 8** (ADR-0004, "Konsekwencje"; addendum 2026-09-18 deferred it
here, by name and date). SC-8-01 (Issue #14) is the first and only writer, at
`POST /projects/{project_id}/scenarios/{scenario_id}/approve`.

**Shape (ADR-0004, aneks 2026-09-27 SC-8-01):**

1. Dedicated to scenario lifecycle events, not a generic system-wide table: real foreign keys
   (`scenario_id`, `project_id`), and `action_type` a closed enum with exactly one member today
   (`scenario_approved`). No polymorphic `resource_type`/`resource_id` pair, and no speculative
   column for an action that does not write here yet.
2. No descriptive copy of scenario/project fields — the approval snapshot is already the record of
   *what* was approved; this table only ever carries references (the two foreign keys) plus who and
   when.
3. `ON DELETE RESTRICT` on both foreign keys, explicit — named even though nothing in this
   repository deletes a scenario or a project today, so that a future delete path cannot make a
   history row about it disappear silently.
4. Append-only: no `updated_at`, no ADR-0007 concurrency token. One writer, one insert, never an
   update — the same reasoning `approved_snapshot_*` tables already carry for omitting a token,
   spelled out here as the point of the table rather than a consequence of it being a snapshot.
5. Absent from `SNAPSHOT_TABLES` and from `SCENARIO_CHILD_COPIERS` by requirement, not by omission
   (module docstring of `app.models.audit_log`): a duplicated scenario is a new, unapproved draft,
   and copying a history row onto it would misstate its own approval history.

Revision ID: a1b2c3d4e5f6
Revises: f1a2c4b6d8e0
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f1a2c4b6d8e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTION_TYPE = postgresql.ENUM(
    "scenario_approved",
    name="audit_action_type",
    create_type=False,
)
"""The closed vocabulary of `action_type` (`app.models.audit_log.AuditActionType`). One member
today; a second one is a schema change (a new migration adding an enum value), never a free-text
column a caller could invent a value into."""


def upgrade() -> None:
    _ACTION_TYPE.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("action_type", _ACTION_TYPE, nullable=False),
        sa.Column("performed_by", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_audit_log_scenario_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_audit_log_project_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        op.f("ix_audit_log_scenario_id"), "audit_log", ["scenario_id"]
    )
    op.create_index(
        op.f("ix_audit_log_project_id"), "audit_log", ["project_id"]
    )


def downgrade() -> None:
    """Reverse of `upgrade`. A downgrade of an expand-only migration discards data by definition —
    every recorded history row goes with the table — consistent with the deployment order
    (expand -> deploy -> contract, never "deploy and downgrade if it goes wrong")."""
    op.drop_index(op.f("ix_audit_log_project_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_scenario_id"), table_name="audit_log")
    op.drop_table("audit_log")
    _ACTION_TYPE.drop(op.get_bind(), checkfirst=False)
