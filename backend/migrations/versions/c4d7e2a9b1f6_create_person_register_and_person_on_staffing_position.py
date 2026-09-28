"""create the person register and the optional person on staffing_position

SC-2-06 (F-03, Issue #31; ADR-0019 — personal data; ADR-0004, ADR-0005 and ADR-0007 aneksy
2026-09-27 and 2026-09-28; gate 1 decisions 2 and 5, 2026-09-27; human decisions D-3 = A and
D-4 = B, 2026-09-28). Expand only (ADR-0001, expand -> deploy -> contract): one new table, two new
columns on an existing table (`person_id`, nullable with no default; `person_assignment_updated_at`,
`NOT NULL` with a server default), one foreign key and two CHECKs. No column is dropped or altered
and no existing value is rewritten (every position gets `person_id = NULL`, i.e. stays anonymous) —
so the code deployed before this migration keeps reading and writing `staffing_position` exactly as
it did, and there is no contract phase to pair with it.

**Edited in place on 2026-09-28, not superseded by a new revision** (ADR-0019 aneks 2026-09-28,
D-3): this migration has never left the task branch, and ADR-0001's expand/contract governs deployed
migrations.

**What the database enforces here, and why in the schema** (a fixture, a seed script or a future
import never passes through a Pydantic model — ADR-0001):

1. `person` has **exactly** the columns ADR-0019 (point 3) approves — `id`, `full_name
   VARCHAR(200) NOT NULL`, `created_at`, `updated_at` — and no uniqueness on the name (two people
   may share one). `ck_person_full_name_canonical` — no `[[:space:]]` character at either end, and
   not empty (D-3 = A; the earlier `btrim` form caught the space only) — is a data rule and, at the
   same time, the one server-side refusal that quotes a real name in `DETAIL: Failing row contains
   (…)`, the contrast NF-11 criterion K-09 stands on. Named limit: which non-ASCII characters
   `[[:space:]]` covers depends on the target database's ctype; the ASCII whitespace (space, tab,
   LF, CR, VT, FF) is the guarantee, and it is what the tests pin.
2. `staffing_position.person_id` -> `person.id`, no `ON DELETE` (`NO ACTION`), like every other
   foreign key of that table: a person assigned to a position cannot be physically deleted
   (ADR-0019, point 7 — the deletion Story anonymises in place instead).
3. `staffing_position.person_assignment_updated_at` — the assignment's **own** concurrency marker
   (ADR-0007 aneks 2026-09-28, D-4 = B), so an assignment never moves the position's `updated_at`,
   which is visible without `PEOPLE_READ`. No `ON UPDATE` behaviour anywhere: only the assignment
   path writes it.
4. `ck_staffing_position_person_requires_single_headcount` (`person_id IS NULL OR headcount = 1`,
   decision Q-7 = a): holds for every existing row by construction (`person_id` is `NULL` on all of
   them), so adding it can never fail on data.

**What is deliberately not here:** a snapshot table for the register (ADR-0004, aneks 2026-09-27,
point 1 — the name enters no calculation and must stay correctable); an index on
`staffing_position.person_id` (no query reads positions by person — the register returns persons
only, ADR-0019 "Decyzja" pt 3 — and nothing deletes a person; the first reverse query or the
deletion Story adds it together with its own reason); any row in `person` (fictitious data only, and
never through a migration — ADR-0019, point 8).

**`NOT VALID` + `VALIDATE CONSTRAINT` not used**, for the reason `a8f18e00172b` records (`env.py`
runs one transaction per migration, so the split buys nothing) — the same named exception R-01, not
a new one.

Revision ID: c4d7e2a9b1f6
Revises: a1b2c3d4e5f6
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d7e2a9b1f6"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06): `ALTER TABLE staffing_position
...` takes `ACCESS EXCLUSIVE` for its statement's duration on a table other sessions read."""

# Spelled here as well as in the models rather than imported from them: a migration must keep
# describing the schema it produced even after the models move on (the rule of `f3a1d0c58b27`).
# `tests/test_people_register.py` keeps the copies honest.
_PERSON = "person"
_POSITION = "staffing_position"
_FULL_NAME_CANONICAL_EXPRESSION = (
    "full_name !~ '^[[:space:]]' AND full_name !~ '[[:space:]]$' AND char_length(full_name) > 0"
)
_PERSON_REQUIRES_SINGLE_HEADCOUNT_EXPRESSION = "person_id IS NULL OR headcount = 1"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    op.create_table(
        _PERSON,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _FULL_NAME_CANONICAL_EXPRESSION, name=op.f(f"ck_{_PERSON}_full_name_canonical")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_PERSON}")),
    )

    op.add_column(_POSITION, sa.Column("person_id", sa.UUID(), nullable=True))
    # ADR-0007 aneks 2026-09-28 (D-4 = B): the assignment's own concurrency marker. `NOT NULL` with
    # a server default, so every existing row gets `now()` in the same statement (a constant-ish
    # default on PostgreSQL 11+: no table rewrite); no `ON UPDATE` anywhere — only the assignment
    # path moves it.
    op.add_column(
        _POSITION,
        sa.Column(
            "person_assignment_updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_foreign_key(
        op.f(f"fk_{_POSITION}_person_id"), _POSITION, _PERSON, ["person_id"], ["id"]
    )
    op.create_check_constraint(
        op.f(f"ck_{_POSITION}_person_requires_single_headcount"),
        _POSITION,
        _PERSON_REQUIRES_SINGLE_HEADCOUNT_EXPRESSION,
    )

    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`. Data loss by definition, like every downgrade of an expand migration:
    every assignment and every person is dropped. It exists so the migration can be tested both
    ways, not as a deployment path."""
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    op.drop_constraint(
        op.f(f"ck_{_POSITION}_person_requires_single_headcount"), _POSITION, type_="check"
    )
    op.drop_constraint(op.f(f"fk_{_POSITION}_person_id"), _POSITION, type_="foreignkey")
    op.drop_column(_POSITION, "person_assignment_updated_at")
    op.drop_column(_POSITION, "person_id")
    op.drop_table(_PERSON)

    op.execute("SET LOCAL lock_timeout = DEFAULT")
