"""create projects, scenarios, project_access

First migration of the schema (ADR-0001). Expand only: it adds three tables and two enum types
and removes nothing, so the previous code version keeps working against the new schema. A
contracting step never travels with the code that needs it.

Revision ID: 09191236aba0
Revises:
Create Date: 2026-09-18 14:48:06.274072+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "09191236aba0"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("client", sa.String(length=200), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("delivery_period_start", sa.Date(), nullable=False),
        sa.Column("delivery_period_end", sa.Date(), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Enum("active", "archived", name="project_status"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(reporting_currency) = 3",
            name=op.f("ck_projects_reporting_currency_iso4217"),
        ),
        sa.CheckConstraint(
            "delivery_period_end >= delivery_period_start",
            name=op.f("ck_projects_delivery_period_ordered"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    # The isolation boundary (ADR-0005): a user sees a project only through a row here.
    op.create_table(
        "project_access",
        sa.Column("user_id", sa.String(length=200), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column(
            "can_view_personnel_costs", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_access_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "project_id", name=op.f("pk_project_access")),
    )
    op.create_table(
        "scenarios",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.Enum("draft", "approved", name="scenario_status"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("working_calendar", sa.String(length=100), nullable=True),
        # NUMERIC, not FLOAT — decimal arithmetic all the way to storage (NF-01, ADR-0002).
        sa.Column("full_time_hours_per_week", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("target_margin_percent", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name=op.f("ck_scenarios_scenario_period_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_scenarios_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenarios")),
        # Two scenarios of one project are two distinct rows — enforced by the database, not by
        # a convention in application code.
        sa.UniqueConstraint("project_id", "name", name=op.f("uq_scenarios_project_id_name")),
    )
    op.create_index(
        op.f("ix_scenarios_project_id"), "scenarios", ["project_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_scenarios_project_id"), table_name="scenarios")
    op.drop_table("scenarios")
    op.drop_table("project_access")
    op.drop_table("projects")
    # PostgreSQL keeps enum types after their last table is dropped; remove them explicitly so
    # a re-run of the upgrade does not hit a leftover type.
    sa.Enum(name="scenario_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="project_status").drop(op.get_bind(), checkfirst=True)
