"""require non-blank name, client and owner on projects

Expand only: it adds three CHECK constraints and drops nothing, so the previous code version
keeps working against the new schema (ADR-0001). Every row written so far comes from
`POST /projects`, which already refuses blank values at the boundary, so the constraints validate
against existing data without a backfill step.

Why a new revision instead of editing `09191236aba0`: that migration is merged to `main`
(commit 6e40937, PR #18) and has been applied by anything that ran `alembic upgrade head` since.
Editing an applied migration leaves those databases permanently without the constraint while
`alembic_version` claims they are up to date — the silent divergence ADR-0001's "schema change
lives only in a migration file" rule exists to prevent.

The pattern `~ '[^[:space:]]'` means "contains at least one non-whitespace character". Deliberate
choice over `btrim(name) <> ''`: PostgreSQL's one-argument `btrim` strips spaces only, so a name
consisting of a single tab would pass that check while the API's `strip_whitespace=True` rejects
it — two different definitions of "blank" in one system.

Revision ID: 4f0a9c1b7d62
Revises: 09191236aba0
Create Date: 2026-09-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4f0a9c1b7d62"
down_revision: str | None = "09191236aba0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOT_BLANK_COLUMNS = ("name", "client", "owner")


def upgrade() -> None:
    for column in _NOT_BLANK_COLUMNS:
        # `op.f(...)` = the final name, spelled out: the same name the model's naming convention
        # produces from `name="name_not_blank"`, and not subject to a convention this migration
        # would have to re-derive years from now.
        op.create_check_constraint(
            op.f(f"ck_projects_{column}_not_blank"),
            "projects",
            f"{column} ~ '[^[:space:]]'",
        )


def downgrade() -> None:
    for column in _NOT_BLANK_COLUMNS:
        # `op.f(...)` here too: without it the naming convention is applied to a name that
        # already follows it, and the statement asks to drop
        # `ck_projects_ck_projects_name_not_blank` — a constraint that does not exist. Caught by
        # reading `alembic downgrade --sql`, which is the only place a broken downgrade shows up
        # before someone needs it.
        op.drop_constraint(op.f(f"ck_projects_{column}_not_blank"), "projects", type_="check")
