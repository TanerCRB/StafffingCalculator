"""add working calendar holiday provenance

Revision ID: 05821c519ddd
Revises: a91c4e7d2b60
Create Date: 2026-10-01 18:19:23.209455+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '05821c519ddd'
down_revision: str | None = 'a91c4e7d2b60'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "working_calendar_day",
        sa.Column("source", sa.String(20), server_default="manual", nullable=False),
    )
    op.add_column("working_calendar_day", sa.Column("name", sa.String(200), nullable=True))
    op.add_column("working_calendar_day", sa.Column("country_code", sa.String(2), nullable=True))
    op.add_column("working_calendar_day", sa.Column("year", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_working_calendar_day_source_valid",
        "working_calendar_day",
        "source IN ('manual', 'nager_date')",
    )
    op.create_check_constraint(
        "ck_working_calendar_day_provenance_complete",
        "working_calendar_day",
        "(source = 'manual' AND name IS NULL AND country_code IS NULL AND year IS NULL) OR "
        "(source = 'nager_date' AND name IS NOT NULL AND country_code IS NOT NULL "
        "AND year IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_working_calendar_day_country_code",
        "working_calendar_day",
        "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
    )
    op.create_check_constraint(
        "ck_working_calendar_day_provenance_year",
        "working_calendar_day",
        "year IS NULL OR (year BETWEEN 1 AND 9999 AND year = EXTRACT(YEAR FROM day)::integer)",
    )

    op.add_column(
        "approved_snapshot_working_calendar_day",
        sa.Column("source", sa.String(20), nullable=True),
    )
    op.add_column(
        "approved_snapshot_working_calendar_day",
        sa.Column("name", sa.String(200), nullable=True),
    )
    op.add_column(
        "approved_snapshot_working_calendar_day",
        sa.Column("country_code", sa.String(2), nullable=True),
    )
    op.add_column(
        "approved_snapshot_working_calendar_day",
        sa.Column("year", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE approved_snapshot_working_calendar_day "
        "SET source = 'manual', name = NULL, country_code = NULL, year = NULL"
    )
    op.alter_column("approved_snapshot_working_calendar_day", "source", nullable=False)


def downgrade() -> None:
    op.drop_column("approved_snapshot_working_calendar_day", "year")
    op.drop_column("approved_snapshot_working_calendar_day", "country_code")
    op.drop_column("approved_snapshot_working_calendar_day", "name")
    op.drop_column("approved_snapshot_working_calendar_day", "source")
    op.drop_constraint(
        "ck_working_calendar_day_provenance_year", "working_calendar_day", type_="check"
    )
    op.drop_constraint(
        "ck_working_calendar_day_country_code", "working_calendar_day", type_="check"
    )
    op.drop_constraint(
        "ck_working_calendar_day_provenance_complete", "working_calendar_day", type_="check"
    )
    op.drop_constraint(
        "ck_working_calendar_day_source_valid", "working_calendar_day", type_="check"
    )
    op.drop_column("working_calendar_day", "year")
    op.drop_column("working_calendar_day", "country_code")
    op.drop_column("working_calendar_day", "name")
    op.drop_column("working_calendar_day", "source")
