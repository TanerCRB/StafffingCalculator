"""Create effective-dated directed exchange rates.

Revision ID: ea13f02c9b71
Revises: d4b8a2e6c910
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ea13f02c9b71"
down_revision: str | None = "d4b8a2e6c910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERIOD = "daterange(effective_from, (effective_to + 1), '[)')"


def upgrade() -> None:
    op.create_table(
        "exchange_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_currency", sa.String(3), nullable=False),
        sa.Column("target_currency", sa.String(3), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "valid_period",
            postgresql.DATERANGE(),
            sa.Computed(_PERIOD, persisted=True),
            nullable=False,
        ),
        sa.Column("rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_currency ~ '^[A-Z]{3}$'", name=op.f("ck_exchange_rates_source_currency_iso4217")
        ),
        sa.CheckConstraint(
            "target_currency ~ '^[A-Z]{3}$'", name=op.f("ck_exchange_rates_target_currency_iso4217")
        ),
        sa.CheckConstraint(
            "source_currency <> target_currency",
            name=op.f("ck_exchange_rates_currency_pair_distinct"),
        ),
        sa.CheckConstraint("rate > 0", name=op.f("ck_exchange_rates_rate_positive")),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_exchange_rates_period_ordered"),
        ),
        sa.CheckConstraint(
            "source ~ '[^[:space:]]'", name=op.f("ck_exchange_rates_source_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_exchange_rates_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], name=op.f("fk_exchange_rates_scenario_id_scenarios")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exchange_rates")),
    )
    op.create_index(op.f("ix_exchange_rates_project_id"), "exchange_rates", ["project_id"])
    op.create_index(op.f("ix_exchange_rates_scenario_id"), "exchange_rates", ["scenario_id"])
    op.execute(
        "ALTER TABLE exchange_rates ADD CONSTRAINT ex_exchange_rates_no_overlapping_periods "
        "EXCLUDE USING gist ("
        "(coalesce(project_id, '00000000-0000-0000-0000-000000000000'::uuid)) WITH =, "
        "(coalesce(scenario_id, '00000000-0000-0000-0000-000000000000'::uuid)) WITH =, "
        "source_currency WITH =, target_currency WITH =, valid_period WITH &&)"
    )
    op.create_table(
        "approved_snapshot_exchange_rate",
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_rate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_scope", sa.String(20), nullable=False),
        sa.Column("source_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_currency", sa.String(3), nullable=False),
        sa.Column("target_currency", sa.String(3), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"],
            name=op.f("fk_approved_snapshot_exchange_rate_scenario_id_scenarios"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approved_snapshot_exchange_rate")),
    )
    op.create_index(
        op.f("ix_approved_snapshot_exchange_rate_scenario_id"),
        "approved_snapshot_exchange_rate",
        ["scenario_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_approved_snapshot_exchange_rate_scenario_id"),
        table_name="approved_snapshot_exchange_rate",
    )
    op.drop_table("approved_snapshot_exchange_rate")
    op.drop_index(op.f("ix_exchange_rates_scenario_id"), table_name="exchange_rates")
    op.drop_index(op.f("ix_exchange_rates_project_id"), table_name="exchange_rates")
    op.drop_table("exchange_rates")
