"""create working calendars, absence types, position absences and the approval snapshot

Expand only (ADR-0001, expand → deploy → contract): six new tables, one new enum type and **one new
nullable column** on an existing table (`catalog_locations.calendar_id`). Nothing is dropped,
nothing is altered, no existing column changes meaning — so the code version deployed before this
migration keeps working against the new schema and there is no contract phase to pair with it. The
new column is nullable with no default and no backfill, which is what makes it backward compatible:
`NULL` is a *named state* ("this location has no calendar", ADR-0008 addendum 2026-09-22 point 7),
not a value waiting to be filled in, so there is nothing for a later migration to complete.

**What the database enforces here, and why each rule is in the schema rather than only in a request
schema** (a fixture, a seed script, a future import or a second endpoint never sees a Pydantic
model):

1. `UNIQUE (calendar_id, day)` on `working_calendar_day` — one day of one calendar cannot be named
   twice, including from two connections at once (criterion K-03). A `SELECT` before the `INSERT`
   would be check-then-act, and both connections would pass it.
2. `CHECK week_pattern ~ '^[01]{7}$'` — a week pattern is seven flags, Monday first. It is data
   (NF-10), so a calendar working Monday to Saturday is an `INSERT` and not a migration.
3. `CHECK standard_hours_per_day > 0` — a zero-hour working day would make every derived capacity
   zero without anything saying so.
4. `CHECK end_date >= start_date` on `staffing_position_absence` — an inverted range intersects no
   calendar day, so it would consume nothing instead of being refused.
5. Every foreign key is `NO ACTION` (no `ondelete` clause anywhere in this migration). Towards
   `scenarios` and `staffing_position` that matters twice over: `ON DELETE CASCADE` would be a way
   for the rows of an `approved` scenario — its absences and its snapshot — to disappear past the
   write guard, which covers `INSERT`/`DELETE` and not a cascade.

**What is deliberately not here:**

- **No `updated_at` on `staffing_position_absence`.** The concurrency token is
  `staffing_position.updated_at` for the whole aggregate (ADR-0007, addendum 2026-09-22, point 1).
- **No `updated_at` on any `approved_snapshot_*` table.** They are written once, inside the approval
  transaction, so there are no two editors for a marker to arbitrate between (same addendum,
  point 5).
- **No column for a person, a note, a justification or a comment on `staffing_position_absence`.**
  The six columns below are the whole row, and criterion K-22 asserts that by *equality* against
  `information_schema.columns` (ADR-0005, addendum 2026-09-22, point 6).
- **No foreign key from a snapshot table to the row it copied.** A snapshot is a separate set of
  rows, not a reference to the organisation's current values (ADR-0004, addendum 2026-09-22,
  point 3b); the source ids are plain `uuid` values.
- **No `EXCLUDE USING gist` on `staffing_position_absence`.** Two absences of one position over the
  same days are two people, not one counted twice (criterion K-06). The absence of the constraint is
  the decision, as it already is on `staffing_position` itself.

Revision ID: f3a1d0c58b27
Revises: d5e94a1c6b73
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a1d0c58b27"
down_revision: str | None = "d5e94a1c6b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled here as well as in `app.models.catalog` rather than imported from it: a migration must
# keep describing the schema it produced even after the model moves on, and an import would silently
# rewrite history the next time somebody edits the model. What keeps the two copies honest is
# `test_the_model_and_the_migration_agree_on_the_week_pattern_check` in
# `tests/test_working_calendar_schema_constraints.py` — the drift guard R-02.
_WEEK_PATTERN_EXPRESSION = "week_pattern ~ '^[01]{7}$'"

_DIMENSION_NAME_KEY_EXPRESSION = r"lower(btrim(regexp_replace(name, '\s+', ' ', 'g')))"
"""The same normalised-name key the other five dictionaries use (migration `7b3d5c81e40a`).

Copied rather than imported, for the reason that migration gives, and kept honest by the same drift
guard: `test_the_model_and_the_migration_agree_on_every_sql_expression` compares it with
`app.models.catalog.DIMENSION_NAME_KEY_EXPRESSION`. Two "Poland 2026" calendars would split the
capacity of one organisation the way five "Senior"s split its rate table."""

_LOCK_TIMEOUT = "3s"
"""How long the one `ALTER TABLE` here waits for its lock before giving up (R-06, `d5e94a1c6b73`).

The same figure as the migration that established this convention, and deliberately the same rather
than re-argued: a second number would make "how long does a migration wait" a question with two
answers. See the comment at the `ALTER` below for why only that block is bounded."""

_DAY_KIND = postgresql.ENUM(
    "non_working",
    "working",
    name="working_calendar_day_kind",
    create_type=False,
)
"""The two directions an exceptional day can take (`WorkingCalendarDayKind`).

`create_type=False` on the object plus one explicit `create()` below: without it the type would be
emitted once per table that uses it, and the second `CREATE TYPE` fails. The snapshot table reuses
the same type rather than declaring a parallel one — two vocabularies for one value is how a
snapshot ends up disagreeing with its source about what `'working'` means."""


def upgrade() -> None:
    _DAY_KIND.create(op.get_bind(), checkfirst=False)

    # --- the sixth and seventh catalogue dictionaries (ADR-0005, addendum 2026-09-22) ------------
    op.create_table(
        "working_calendar",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        # NUMERIC → Decimal, never float: this figure is multiplied by days and then by a rate
        # (NF-01, ADR-0002). No effective-date window on it — the unit of versioning is the
        # calendar, not the column (ADR-0008, addendum 2026-09-22).
        sa.Column("standard_hours_per_day", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("week_pattern", sa.String(length=7), nullable=False),
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
            "name ~ '[^[:space:]]'", name=op.f("ck_working_calendar_name_not_blank")
        ),
        sa.CheckConstraint(
            "standard_hours_per_day > 0",
            name=op.f("ck_working_calendar_standard_hours_per_day_positive"),
        ),
        sa.CheckConstraint(
            _WEEK_PATTERN_EXPRESSION, name=op.f("ck_working_calendar_week_pattern_is_seven_flags")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_working_calendar")),
    )
    # The same normalised-name uniqueness as the other dictionaries (`DIMENSION_NAME_KEY_EXPRESSION`
    # in `app.models.catalog`): case-folded, trimmed, whitespace collapsed. Two "Poland 2026"s would
    # split the calendar the same way five "Senior"s split the rate table.
    op.execute(
        "CREATE UNIQUE INDEX uq_working_calendar_name_normalized"
        f" ON working_calendar ({_DIMENSION_NAME_KEY_EXPRESSION})"
    )

    op.create_table(
        "working_calendar_day",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("calendar_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", _DAY_KIND, nullable=False),
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
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["working_calendar.id"],
            name=op.f("fk_working_calendar_day_calendar_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_working_calendar_day")),
        # Criterion K-03. No separate index on `calendar_id`: this constraint's btree leads on it.
        sa.UniqueConstraint(
            "calendar_id", "day", name=op.f("uq_working_calendar_day_calendar_id_day")
        ),
    )

    op.create_table(
        "absence_type",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        # Two independent flags, neither derived from the other (criterion K-11). `NOT NULL` with a
        # `false` default: an unanswered flag must not read as "yes".
        sa.Column(
            "generates_cost", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "generates_revenue", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
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
        sa.CheckConstraint("name ~ '[^[:space:]]'", name=op.f("ck_absence_type_name_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_absence_type")),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_absence_type_name_normalized"
        f" ON absence_type ({_DIMENSION_NAME_KEY_EXPRESSION})"
    )

    # --- a location may name the calendar its people follow (ADR-0008, addendum, point 7) --------
    # Nullable, no server default, no backfill: `NULL` means "this location has no calendar", a
    # named state the reading path answers with `derived_capacity_state = "no_calendar"` rather than
    # with a silent `0` (criterion K-23). Adding a nullable column takes only a brief ACCESS
    # EXCLUSIVE lock in PostgreSQL 11+ — no table rewrite, because there is no default to
    # materialise.
    #
    # The `lock_timeout` follows the convention `d5e94a1c6b73` established (R-06, design review
    # 2026-09-22) and applies for the same reason: the two statements below are microseconds of work
    # *once they hold their locks*, and the entire risk is the waiting. A lock request queues ahead
    # of everything that arrives after it, so an `ALTER` waiting on one long-running reader of
    # `catalog_locations` puts every later reader behind itself — an unbounded, catalogue-wide
    # stall.
    # With the bound it is a migration that fails with `55P03`, rolls the whole transaction back
    # (`migrations/env.py` runs one transaction for the batch) and leaves the database at
    # `d5e94a1c6b73`: a deployment to retry in a quieter minute rather than an incident.
    #
    # Three seconds, the same figure and the same reasoning as that migration — long enough for an
    # ordinary catalogue query to finish, short enough to read as "something is holding a lock"
    # rather than as a hang. Two lock acquisitions here, so the worst case this bounds is ~6 s.
    # Only this block is bounded: every other statement in this migration creates a *new* table or
    # type, which takes no lock on anything an application session could be holding.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.add_column("catalog_locations", sa.Column("calendar_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_catalog_locations_calendar_id"),
        "catalog_locations",
        "working_calendar",
        ["calendar_id"],
        ["id"],
    )
    # `SET LOCAL` lasts to the end of the *transaction*, and `env.py` wraps the whole
    # `alembic upgrade` run in one — so without this line the bound would silently apply to every
    # migration running after this one in the same batch. `DEFAULT` rather than a saved value:
    # reading the old one needs `SHOW`, i.e. a live connection, which would make this file
    # unrenderable by `alembic upgrade --sql` — the documented review practice.
    op.execute("SET LOCAL lock_timeout = DEFAULT")

    # --- the third table of the staffing aggregate (F-05) ----------------------------------------
    op.create_table(
        "staffing_position_absence",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("position_id", sa.UUID(), nullable=False),
        sa.Column("absence_type_id", sa.UUID(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # These six columns are the whole row. No person, no note, no justification, no comment —
        # criterion K-22 asserts the set by equality, not by "person_name is absent".
        # No `updated_at` either: the token is the position's (ADR-0007, addendum 2026-09-22).
        sa.CheckConstraint(
            "end_date >= start_date",
            name=op.f("ck_staffing_position_absence_absence_period_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["staffing_position.id"],
            name=op.f("fk_staffing_position_absence_position_id"),
        ),
        sa.ForeignKeyConstraint(
            ["absence_type_id"],
            ["absence_type.id"],
            name=op.f("fk_staffing_position_absence_absence_type_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staffing_position_absence")),
    )
    # Indexed here, unlike `staffing_position_allocation.position_id`: this table has no unique
    # constraint whose leading column it could ride on, because overlapping absences are legal.
    op.create_index(
        op.f("ix_staffing_position_absence_position_id"),
        "staffing_position_absence",
        ["position_id"],
    )

    # --- the approval snapshot (ADR-0004, addendum 2026-09-22) -----------------------------------
    # Values, never references: the only foreign key on each of these three tables points at
    # `scenarios`. `source_*_id` columns are plain `uuid`, so editing or deleting the source cannot
    # touch a frozen row (criterion K-16, asserted by schema introspection).
    op.create_table(
        "approved_snapshot_working_calendar",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("source_calendar_id", sa.UUID(), nullable=False),
        sa.Column("source_location_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("standard_hours_per_day", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("week_pattern", sa.String(length=7), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_approved_snapshot_working_calendar_scenario_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approved_snapshot_working_calendar")),
    )
    op.create_index(
        op.f("ix_approved_snapshot_working_calendar_scenario_id"),
        "approved_snapshot_working_calendar",
        ["scenario_id"],
    )

    op.create_table(
        "approved_snapshot_working_calendar_day",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("source_calendar_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", _DAY_KIND, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_approved_snapshot_working_calendar_day_scenario_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approved_snapshot_working_calendar_day")),
        # No uniqueness on (scenario, calendar, day): the snapshot records what was approved, and a
        # constraint here would make the approval of a defective calendar fail with a message about
        # the snapshot instead of about the calendar.
    )
    op.create_index(
        op.f("ix_approved_snapshot_working_calendar_day_scenario_id"),
        "approved_snapshot_working_calendar_day",
        ["scenario_id"],
    )

    op.create_table(
        "approved_snapshot_absence_type",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("source_absence_type_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        # No `server_default` on either flag, unlike the source table: a snapshot column that can be
        # written without a value read from the source is a snapshot that can be silently wrong.
        sa.Column("generates_cost", sa.Boolean(), nullable=False),
        sa.Column("generates_revenue", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_approved_snapshot_absence_type_scenario_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approved_snapshot_absence_type")),
    )
    op.create_index(
        op.f("ix_approved_snapshot_absence_type_scenario_id"),
        "approved_snapshot_absence_type",
        ["scenario_id"],
    )


def downgrade() -> None:
    """Reverse of `upgrade`, children before parents — the enum type last, after its two users.

    A downgrade of an expand-only migration is data loss by definition (the absences and the
    snapshot rows go with the tables), which is why the deployment order is expand → deploy →
    contract and never "deploy and downgrade if it goes wrong". This exists so a migration can be
    *tested* both ways, as `tests/test_catalog_migration_reversibility.py` does for SC-2-01.
    """
    for table in (
        "approved_snapshot_absence_type",
        "approved_snapshot_working_calendar_day",
        "approved_snapshot_working_calendar",
        "staffing_position_absence",
    ):
        op.drop_table(table)

    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.drop_constraint(
        op.f("fk_catalog_locations_calendar_id"), "catalog_locations", type_="foreignkey"
    )
    op.drop_column("catalog_locations", "calendar_id")
    op.execute("SET LOCAL lock_timeout = DEFAULT")

    op.drop_table("absence_type")
    op.drop_table("working_calendar_day")
    op.drop_table("working_calendar")
    _DAY_KIND.drop(op.get_bind(), checkfirst=False)
