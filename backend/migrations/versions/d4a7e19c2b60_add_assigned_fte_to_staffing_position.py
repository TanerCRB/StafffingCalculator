"""add assigned_fte to staffing_position and widen cost_basis_known

SC-5-04 (F-07, Issue #79; ADR-0013 addendum 2026-09-29 SC-5-04, ADR-0004/0005 addenda of the same
date). Expand only (ADR-0001, expand -> deploy -> contract): one new nullable column and four new
CHECK constraints on an existing table, plus one constraint (`cost_basis_known`) replaced by a
strictly wider one. No column is dropped or altered, no row is rewritten, and no existing row can
violate any new constraint (every existing row has `assigned_fte IS NULL` and a basis of
`worked_time` or `fixed_amount`) — so the code deployed before this migration runs unchanged **until
the first `assigned_fte` row exists**; from then on an old instance misbehaves in the two ways named
under "Mixed-version window" below. There is no contract phase to pair with the migration.

**What the database enforces here, and why in the schema** (a fixture, a seed script or a future
import never passes through a Pydantic model — ADR-0001):

1. `assigned_fte` is `NUMERIC(10,4)`, nullable: the stored FTE of the position as a **fraction**
   (`1` = one FTE of the position; ADR-0013 addendum 2026-09-29 SC-5-04, point 2). Four decimal
   places, stored exactly and never rounded on write (ADR-0008, point 6).
2. `cost_basis_known` is widened from `('worked_time', 'fixed_amount')` to also admit
   `'assigned_fte'` — dropped and recreated **inside this migration's one transaction** (see
   `migrations/env.py`), so no reader ever sees the table without it. A widening never refuses a
   row the old constraint accepted.
3. `assigned_fte_positive` — `assigned_fte IS NULL OR assigned_fte > 0`.
4. `assigned_fte_required_for_its_basis` — `cost_basis <> 'assigned_fte' OR assigned_fte IS NOT
   NULL`: an FTE basis with no FTE is the unnamed state "two shapes, never a third" forbids, so it
   cannot exist (FA-1).
5. `assigned_fte_only_on_its_basis` — `assigned_fte IS NULL OR cost_basis = 'assigned_fte'`: a stray
   FTE on another basis would be an input nothing reads that somebody may believe.
6. `assigned_fte_not_with_fixed_amount` — `assigned_fte IS NULL OR (fixed_amount IS NULL AND
   fixed_amount_currency IS NULL)`: two stated costs on one row are two answers.

No upper bound tied to `headcount`: a value above it is accepted without bound and is not surfaced
anywhere (point 2; named limitation: a percent typo, 50 for 0.5, prices 100 times too high).
**The CHECK expressions are spelled here, not imported** from `app.models.staffing`, the
convention every migration since `f3a1d0c58b27` keeps; `tests/test_assigned_fte_schema.py` compares
the two copies. `a8f18e00172b` is not edited: it keeps describing the schema it produced.

**What is deliberately not here:** a snapshot table or a new `SCENARIO_CHILD_COPIERS` entry
(ADR-0004, addendum 2026-09-29 SC-5-04 — own data of the scenario, protected by the write guard and
copied with the row by the existing reflective copier).

**Plain `ADD CONSTRAINT ... CHECK`, not `NOT VALID` + `VALIDATE`** (reviewer R-01 of SC-5-03, the
same named exception): `migrations/env.py` runs `upgrade()` in one transaction, so the split buys
nothing here. The lock bound below limits the wait, not the scan of the four new constraints.

**Downgrade refuses while any position uses the basis** (FA-13): dropping the column would silently
turn an FTE position into a position with no stated cost at all, and the narrower
`cost_basis_known` would refuse the rows regardless. The guard locks the table before counting, so
no such row can be committed between the check and the drop; it names no row value, id or count
(NF-11) and refuses offline (`--sql`), where there is no connection to read the table through.
Once the application that reads `assigned_fte` is deployed, downgrading this revision without also
redeploying the previous code turns every personnel-cost read into a `column ... does not exist`
error. **Mixed-version window, named (human decision H-7):** while old and new application versions
run side by side, an old instance (1) ignores `cost_basis = 'assigned_fte'`: it prices such a
position's allocation rows by the worked-time formula, a wrong figure with state `calculated`; and
(2) its reflective copier — which does not know the column — copies the row without `assigned_fte`,
which `assigned_fte_required_for_its_basis` refuses, so the copy fails and nothing wrong is stored.
How the refusal is answered, verified in `app.data.write_errors.failure_for` (it classifies by
SQLSTATE, never by constraint name: `23514` check_violation is a refusal, so none of the four new
names is "unmapped"): `POST .../scenarios/{id}/duplicate` answers `409`
(`ScenarioDuplicationRefused`), but `POST /projects/{id}/copy` answers `500` (`copy_project` raises
`ProjectWriteFailed`, which the endpoint does not catch). Accepted, not repaired: no shared
environment exists yet.

Revision ID: d4a7e19c2b60
Revises: c6e1a94d7b35
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "d4a7e19c2b60"
down_revision: str | None = "c6e1a94d7b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The bound every migration since `d5e94a1c6b73` keeps (R-06): `ADD COLUMN` and `ADD CONSTRAINT ...
CHECK` take `ACCESS EXCLUSIVE` on a table other sessions read."""

_TABLE = "staffing_position"
_COLUMN = "assigned_fte"
_BASIS = "assigned_fte"

_COST_BASIS_KNOWN_EXPRESSION = "cost_basis IN ('worked_time', 'fixed_amount', 'assigned_fte')"
_COST_BASIS_KNOWN_EXPRESSION_BEFORE = "cost_basis IN ('worked_time', 'fixed_amount')"
_ASSIGNED_FTE_POSITIVE_EXPRESSION = "assigned_fte IS NULL OR assigned_fte > 0"
_ASSIGNED_FTE_REQUIRED_FOR_ITS_BASIS_EXPRESSION = (
    "cost_basis <> 'assigned_fte' OR assigned_fte IS NOT NULL"
)
_ASSIGNED_FTE_ONLY_ON_ITS_BASIS_EXPRESSION = "assigned_fte IS NULL OR cost_basis = 'assigned_fte'"
_ASSIGNED_FTE_NOT_WITH_FIXED_AMOUNT_EXPRESSION = (
    "assigned_fte IS NULL OR (fixed_amount IS NULL AND fixed_amount_currency IS NULL)"
)

_NEW_CHECKS = (
    ("assigned_fte_positive", _ASSIGNED_FTE_POSITIVE_EXPRESSION),
    ("assigned_fte_required_for_its_basis", _ASSIGNED_FTE_REQUIRED_FOR_ITS_BASIS_EXPRESSION),
    ("assigned_fte_only_on_its_basis", _ASSIGNED_FTE_ONLY_ON_ITS_BASIS_EXPRESSION),
    ("assigned_fte_not_with_fixed_amount", _ASSIGNED_FTE_NOT_WITH_FIXED_AMOUNT_EXPRESSION),
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    op.add_column(
        _TABLE, sa.Column(_COLUMN, sa.Numeric(precision=10, scale=4), nullable=True)
    )

    # Widened, not replaced by something else: same name, a superset of the old values. Dropped and
    # recreated in this one transaction, so no statement of another session runs between the two.
    op.drop_constraint(op.f(f"ck_{_TABLE}_cost_basis_known"), _TABLE, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_cost_basis_known"), _TABLE, _COST_BASIS_KNOWN_EXPRESSION
    )
    for name, expression in _NEW_CHECKS:
        op.create_check_constraint(op.f(f"ck_{_TABLE}_{name}"), _TABLE, expression)

    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            f"downgrade of {revision} cannot run in --sql (offline) mode: the guard reads "
            f"{_TABLE} live before deciding whether to proceed, and offline mode has no "
            "connection to read it through. Run this downgrade online."
        )

    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    connection = op.get_bind()
    # Locked before counted: every statement of a migration runs in one transaction
    # (`migrations/env.py`), so the lock is held through the drops below and no `assigned_fte` row
    # can be committed between the count and the schema change.
    connection.execute(sa.text(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE"))
    in_use = connection.execute(
        sa.text(
            f"SELECT count(*) FROM {_TABLE} "
            f"WHERE cost_basis = :basis OR {_COLUMN} IS NOT NULL"
        ),
        {"basis": _BASIS},
    ).scalar_one()
    if in_use:
        # No count, no id, no value of any row (NF-11): that such a position exists is all a person
        # needs to decide what to do.
        raise RuntimeError(
            f"Refusing to downgrade {revision}: at least one staffing position is costed on the "
            f"'{_BASIS}' basis. Dropping the column would erase its stored FTE and leave the "
            "position with no stated cost. Switch those positions to another cost basis first."
        )

    for name, _expression in reversed(_NEW_CHECKS):
        op.drop_constraint(op.f(f"ck_{_TABLE}_{name}"), _TABLE, type_="check")
    op.drop_constraint(op.f(f"ck_{_TABLE}_cost_basis_known"), _TABLE, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_cost_basis_known"), _TABLE, _COST_BASIS_KNOWN_EXPRESSION_BEFORE
    )
    op.drop_column(_TABLE, _COLUMN)

    op.execute("SET LOCAL lock_timeout = DEFAULT")
