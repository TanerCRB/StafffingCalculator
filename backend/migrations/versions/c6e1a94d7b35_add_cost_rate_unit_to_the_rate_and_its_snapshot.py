"""add cost_rate_unit to catalog_default_rates and its approval snapshot

SC-5-08 (Issue #80, F-07): daily and monthly cost rates. One new column, `cost_rate_unit`
(`hour` / `day` / `month`), on the same row as `default_cost_rate`, on both the live rate table and
its approval snapshot (ADR-0013, addendum 2026-09-29 SC-5-08; ADR-0004, same date; ADR-0005, same
date). The selling-rate column `unit` is untouched and stays pinned to `hour` by its own CHECK
(`unit_is_hour`, ADR-0002 addendum 2026-09-29).

**Expand only, backward compatible.** Both columns are `NOT NULL DEFAULT 'hour'`, added in one
`ALTER TABLE` each:

- every existing row gets `hour` from the default at the moment of the `ALTER` — a fact, not a
  guess: until this revision the catalogue refused every unit but `hour` (`unit_is_hour`), so every
  cost rate ever entered or frozen was hourly (ADR-0004, addendum 2026-09-29, point 3);
- the code version deployed before this migration keeps working: its `INSERT`s do not name the
  column and the default fills it in, and nothing it reads goes missing. There is no contract step:
  nothing is dropped and no existing statement stops being valid.

**Like the SC-5-02 pair, the snapshot column loses its default after the backfill** (reviewer R-01,
ADR-0004 addendum 2026-09-29, point 2). The default is present only long enough to give every row
frozen before this revision the honest value (`hour`, a fact), then `ALTER COLUMN … DROP DEFAULT`
in this same revision: from then on an `INSERT … SELECT` that omits the column fails on `NOT NULL`
instead of silently freezing a monthly rate as hourly. The live catalogue column keeps `NOT NULL
DEFAULT 'hour'` (the API's create default). The copier names the column explicitly, and a test
proves
a `month` window is frozen as `month`. Rolling-deploy consequence, named: a not-yet-updated copier
running against the new schema fails its approval loudly (a rolled-back transaction) rather than
freezing wrong.

**A CHECK on the live table only** (`cost_rate_unit_is_known`): a rule about *input*. The snapshot
never re-judges what was approved (`app.models.approved_snapshot`, "no CHECK constraints are
repeated here").

**Downgrade refuses while any row carries a non-`hour` unit** (ADR-0004 addendum 2026-09-29, point
6). Dropping the column would silently reprice those rows hourly — a factor of about the hours of a
month or a day, with no error — and for a snapshot row there is no path to repair it afterwards. The
guard reads both tables under `ACCESS EXCLUSIVE` locks taken before the count, so no such row can be
written between the check and the drop (the shape `c1a4f7b92e05` uses). The refusal names no row
value (NF-11): neither a rate, an id nor a count. It also refuses offline (`--sql`), where there is
no connection to read the tables through. Note, as for `9b3f6a1d0c47`: once the application that
reads `cost_rate_unit` is deployed, downgrading this revision without also redeploying the previous
code turns every personnel-cost read into a `column … does not exist` error.

Revision ID: c6e1a94d7b35
Revises: b8f2d6a41c93
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "c6e1a94d7b35"
down_revision: str | None = "b8f2d6a41c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled here rather than imported from `app.models.catalog`: a migration keeps describing the
# schema it produced even after the model moves on (same reason as `d5e94a1c6b73`).
_COLUMN = "cost_rate_unit"
_DEFAULT_UNIT = "hour"
_KNOWN_UNITS = ("hour", "day", "month")
_LIVE_TABLE = "catalog_default_rates"
_SNAPSHOT_TABLE = "approved_snapshot_catalog_default_rate"
_CHECK_NAME = "cost_rate_unit_is_known"

_LOCK_TIMEOUT = "3s"
"""The bound `d5e94a1c6b73` established for an `ALTER TABLE` on the catalogue (R-06). Both `ALTER`s
are metadata-only with a constant default (PostgreSQL >= 11 rewrites no rows), so each finishes in
microseconds once it holds the lock — the risk this bounds is the wait, not the work."""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.add_column(
        _LIVE_TABLE,
        sa.Column(
            _COLUMN,
            sa.String(length=20),
            server_default=_DEFAULT_UNIT,
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f(f"ck_{_LIVE_TABLE}_{_CHECK_NAME}"),
        _LIVE_TABLE,
        f"{_COLUMN} IN (" + ", ".join(f"'{unit}'" for unit in _KNOWN_UNITS) + ")",
    )
    op.execute("SET LOCAL lock_timeout = DEFAULT")

    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.add_column(
        _SNAPSHOT_TABLE,
        sa.Column(
            _COLUMN,
            sa.String(length=20),
            server_default=_DEFAULT_UNIT,
            nullable=False,
        ),
    )
    # The default existed only to backfill the rows above; every future insert must name the column.
    op.alter_column(_SNAPSHOT_TABLE, _COLUMN, server_default=None)
    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            f"downgrade of {revision} cannot run in --sql (offline) mode: the guard reads "
            f"{_LIVE_TABLE} and {_SNAPSHOT_TABLE} live before deciding whether to proceed, and "
            "offline mode has no connection to read them from. Run this downgrade online."
        )

    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    connection = op.get_bind()
    # Both tables locked before either is counted: every statement of a migration runs in one
    # transaction (`migrations/env.py`), so the locks are held through the drops below and no
    # non-`hour` row can be committed between the count and the schema change.
    connection.execute(sa.text(f"LOCK TABLE {_LIVE_TABLE} IN ACCESS EXCLUSIVE MODE"))
    connection.execute(sa.text(f"LOCK TABLE {_SNAPSHOT_TABLE} IN ACCESS EXCLUSIVE MODE"))
    non_hourly = connection.execute(
        sa.text(
            f"SELECT (SELECT count(*) FROM {_LIVE_TABLE} WHERE {_COLUMN} <> :unit)"
            f" + (SELECT count(*) FROM {_SNAPSHOT_TABLE} WHERE {_COLUMN} <> :unit)"
        ),
        {"unit": _DEFAULT_UNIT},
    ).scalar_one()
    if non_hourly:
        # No count, no rate, no id, no unit of any row (NF-11): the fact that such a row exists is
        # all a person needs to decide what to do.
        raise RuntimeError(
            f"Refusing to downgrade {revision}: at least one cost rate in the catalogue or in an "
            "approval snapshot is stated in a unit other than 'hour'. Dropping the column would "
            "silently reprice it as an hourly rate, and a snapshot row cannot be repaired "
            "afterwards. Restate the catalogue rates in hours, and do not downgrade past a "
            "snapshot that froze a daily or monthly rate."
        )

    # The CHECK on the live table is dropped with its column (PostgreSQL drops a CHECK that
    # references only that column; see `9b3f6a1d0c47`).
    op.drop_column(_SNAPSHOT_TABLE, _COLUMN)
    op.drop_column(_LIVE_TABLE, _COLUMN)
    op.execute("SET LOCAL lock_timeout = DEFAULT")
