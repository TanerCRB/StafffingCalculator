"""Add caller-scoped idempotency records for additional-cost creation (SC-5-13).

Revision ID: c3d5e7f9a1b2
Revises: b2d4f6a8c0e1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d5e7f9a1b2"
down_revision: str | None = "b2d4f6a8c0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "additional_cost_create_idempotency",
        sa.Column("caller_user_id", sa.String(length=200), nullable=False),
        sa.Column("key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("cost_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["cost_id"], ["additional_cost.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("caller_user_id", "key"),
        sa.UniqueConstraint("cost_id"),
    )


def downgrade() -> None:
    op.drop_table("additional_cost_create_idempotency")
