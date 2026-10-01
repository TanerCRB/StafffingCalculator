"""Create separately approved Fixed Price adjustments (SC-4-09).

Revision ID: a91c4e7d2b60
Revises: d4b8a2e6c910
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a91c4e7d2b60"
down_revision = "ea13f02c9b71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fixed_price_adjustment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("commercial_terms_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("amount", sa.Numeric(14, 4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="pending", nullable=False),
        sa.ForeignKeyConstraint(
            ["commercial_terms_id"],
            ["fixed_price_terms.commercial_terms_id"],
            name=op.f("fk_fixed_price_adjustment_commercial_terms_id_fixed_price_terms"),
        ),
        sa.UniqueConstraint(
            "commercial_terms_id",
            "request_id",
            name=op.f("uq_fixed_price_adjustment_request"),
        ),
        sa.CheckConstraint(
            "kind IN ('increase', 'decrease')",
            name=op.f("ck_fixed_price_adjustment_kind_known"),
        ),
        sa.CheckConstraint(
            "amount >= 0", name=op.f("ck_fixed_price_adjustment_amount_non_negative")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_fixed_price_adjustment_status_known"),
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name=op.f("ck_fixed_price_adjustment_currency_valid"),
        ),
    )
    op.create_index(
        "ix_fixed_price_adjustment_terms_status",
        "fixed_price_adjustment",
        ["commercial_terms_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_fixed_price_adjustment_terms_status", table_name="fixed_price_adjustment")
    op.drop_table("fixed_price_adjustment")
