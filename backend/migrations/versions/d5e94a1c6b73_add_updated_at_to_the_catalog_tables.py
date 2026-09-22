"""add the ADR-0007 concurrency marker (updated_at) to the six catalogue tables

SC-2-04 (Issue #49, gate-1 decision Q-1). Until now the catalogue could only be *added* to, and an
`INSERT` cannot lose somebody else's change. Editing an existing row can, and the `EXCLUDE`
constraint does not cover it: changing only the amount or the currency of a rate touches no element
of `ex_catalog_default_rates_no_overlapping_periods`' key, so two editors working from one read
would both succeed and the later write would silently win. `updated_at` is the marker that makes the
loser of that race be told instead (ADR-0007) — the same mechanism as on `projects` and
`staffing_position`, not a third one.

**All six tables, not only the rates.** SC-2-03 established that a vendor is "the fifth dictionary,
not a fifth mechanism"; a marker on the rate table alone would restore exactly the asymmetry that
decision removed (Issue #49, Q-1, rejected variant C).

**Backward compatible in one migration, and this is the choice worth explaining.** The column is
`NOT NULL` with `server_default now()`, added in a single `ALTER TABLE`:

- every existing row gets a valid value from the default at the moment of the `ALTER`, so there is
  no nullable-then-backfill-then-`SET NOT NULL` sequence and no second migration to remember;
- the code version deployed *before* this migration keeps working unchanged — its `INSERT`s do not
  name `updated_at`, and the server default fills it in. This is the expand step of ADR-0001's
  expand → deploy → contract, and there is no contract step to follow: nothing is dropped, nothing
  is rewritten, no existing statement stops being valid.

The `server_default` stays on the column afterwards (it is not a migration-only scaffold), for that
same reason: it is what makes an `INSERT` that does not mention the marker a valid one, exactly as
`created_at` already works on these tables. The *new value on an update* comes from
`onupdate=func.now()` in `app.models.catalog` — a SQL expression, so the clock is the database's,
not the application's.

**No "who changed it" column, deliberately** (Issue #49, gate-1). A timestamp does not tie a row to
a project, a user, a business unit or a tenant, so the structural exception that keeps these six
tables outside the `project_access` scope filter survives (ADR-0001/ADR-0005, addenda 2026-09-19).
An author column would expire it on the spot and needs its own dated decision.

Revision ID: d5e94a1c6b73
Revises: e2c7b04d9a31
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e94a1c6b73"
down_revision: str | None = "e2c7b04d9a31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled here as well as in `app.models.catalog` rather than imported from it — the same reason as
# in every other migration of this table (`7b3d5c81e40a`, `c1a4f7b92e05`): a migration has to keep
# describing the schema it produced even after the model moves on.
_MARKER_COLUMN = "updated_at"

_LOCK_TIMEOUT = "3s"
"""How long an `ALTER TABLE` here waits for its `ACCESS EXCLUSIVE` lock before giving up (R-06).

The `ALTER`s below are metadata-only — `now()` is `STABLE`, so PostgreSQL ≥ 11 writes no rows and
each statement finishes in microseconds *once it holds the lock*. The entire risk is the waiting:
if any other session holds a conflicting lock on one of the six tables (a long
`GET /catalog/rates`, a session left idle in a transaction), the `ALTER` queues — and, because a
lock request queues ahead of everything that arrives after it, **every subsequent reader of that
table queues behind the `ALTER`**. Without a timeout that is an organisation-wide catalogue outage
lasting as long as whatever the `ALTER` is waiting for, i.e. unbounded. With one it is a migration
that fails with `55P03 lock_timeout`, rolls the whole transaction back (`migrations/env.py` runs
every migration in one) and leaves the database at `e2c7b04d9a31` — a deployment to retry in a
quieter minute instead of an incident to diagnose.

**Three seconds**, chosen as the smaller side of two costs. Too long and the queue behind a blocked
`ALTER` becomes the outage the timeout exists to prevent; too short and a perfectly ordinary
half-second catalogue query turns a deployment into a spurious failure. A catalogue read is
milliseconds, so three seconds is ample room for one to finish, and it is short enough that a human
watching the deploy reads the failure as "something is holding a lock" rather than "it hung".
The number is per lock acquisition, not per migration: six `ALTER`s, so the worst case this bounds
is 6 × 3 s ≈ 18 s of blocking before the transaction rolls back — named here because it is the
figure that matters operationally, not the 3 s.

No deployed environment exists yet (open decision #5, the same factual basis `e2c7b04d9a31` and
`c1a4f7b92e05` record for their own lock choices), so today this cannot fire: the only databases
are ephemeral test containers with no concurrent writer. It is set now because the first persistent
environment is exactly where the absence would be discovered, and the guard costs one statement."""

_CATALOG_TABLES: tuple[str, ...] = (
    "catalog_roles",
    "catalog_seniorities",
    "catalog_locations",
    "catalog_engagement_types",
    "catalog_vendors",
    "catalog_default_rates",
)
"""The six tables of the catalogue, listed rather than derived.

A list, because the migration must describe the schema as it was at this revision: deriving it from
`DIMENSION_MODELS` plus the rate model would make this file change meaning the day a seventh table
is added, and a migration whose meaning depends on today's code is not a migration."""


def upgrade() -> None:
    # R-06 (design review, 2026-09-22). `SET LOCAL`, so the setting belongs to this migration and
    # not to the application: `migrations/env.py` and `app/db/session.py` stay untouched, and a
    # migration that needs a different bound sets its own. There is no repository convention to
    # follow here — this is the first migration in `migrations/versions/` to set a `lock_timeout`
    # at all.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    for table in _CATALOG_TABLES:
        op.add_column(
            table,
            sa.Column(
                _MARKER_COLUMN,
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )

    # `SET LOCAL` lasts until the end of the *transaction*, and `migrations/env.py` wraps the whole
    # `alembic upgrade` run in one — so without this line the bound would silently apply to every
    # migration that happens to run after this one in the same batch. Restoring it keeps "for the
    # duration of this migration" literally true. `DEFAULT` rather than a saved value: reading the
    # old one needs a live connection (`SHOW`), which would make this file unrenderable by
    # `alembic upgrade --sql`, the documented review practice (`4f0a9c1b7d62`).
    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    # Dropping the marker loses no data anybody entered — it is bookkeeping the database wrote for
    # itself — so there is no guard here of the kind `c1a4f7b92e05`'s downgrade needs. What it does
    # lose is the protection itself: the code version that sends `updated_at` in a `PATCH` body
    # stops having a column to compare against and every catalogue edit fails, which is the loud
    # failure to prefer over silently reinstated "last write wins".
    #
    # R-06: `DROP COLUMN` takes the same `ACCESS EXCLUSIVE` lock as `ADD COLUMN`, so the downgrade
    # can queue the whole catalogue behind itself exactly as the upgrade can, and carries the same
    # bound for the same reason.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    for table in reversed(_CATALOG_TABLES):
        op.drop_column(table, _MARKER_COLUMN)

    op.execute("SET LOCAL lock_timeout = DEFAULT")
