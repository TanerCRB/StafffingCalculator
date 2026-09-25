"""The write and copy path for `scenario_delivery_segment` (F-02, F-06; SC-1-11, ADR-0016).

ADR-0016 builds the entity and this module builds exactly the two obligations ADR-0004 places on
every child table of `scenarios` (its addendum of 2026-09-25, SC-1-11) — nothing else, and in
particular **no scope function, no permission check and no HTTP surface** (ADR-0016, point 8): there
is no caller yet, and the first task that adds an endpoint over this table resolves `ADR-0005` and
decides what a caller may see, through the same `project_for_caller`/`scenario_in_scope` mechanism
every other scenario-scoped write in this repository uses — never a second one written here ahead of
that decision.

1. **The refusal of a write to an `approved` scenario, in the statement that writes**
   (ADR-0004, addendum 2026-09-25 SC-1-11, point 2) — `app.data.scenario_guard.unapproved_scenario`,
   embedded as the source of the `INSERT … SELECT`. It also takes the scenario row lock that
   serialises the write against a concurrent approval (criterion K-07), the same already-closed
   window every other child table relies on (addendum 2026-09-19 SC-3-01, point 2; addendum
   2026-09-22 SC-3-02, point 5) — no new mechanism is opened here.
2. **One copier for the aggregate** (`copy_scenario_delivery_segments`, one entry in
   `app.data.project_writes.SCENARIO_CHILD_COPIERS` — ADR-0004, addendum 2026-09-25 SC-1-11, point
   4; criterion K-06). The simplest shape this registry has: a segment has no child or grandchild of
   its own today (F-04 out of scope, ADR-0016 point 9), so the copier carries no old-to-new id
   mapping, unlike the staffing or additional-cost aggregates.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.data.column_copy import values_to_copy
from app.data.scenario_guard import unapproved_scenario
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.models.scenario import Scenario, ScenarioStatus
from app.models.scenario_delivery_segment import ScenarioDeliverySegment

_SEGMENT_TABLE = ScenarioDeliverySegment.__table__
_SCENARIOS_TABLE = Scenario.__table__

SEGMENT_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "scenario_id", "created_at", "updated_at"}
)
"""Segment attributes a copy does **not** inherit, and why each one is here:

- `id` — a copy is a new row, not a second name for the source one (ADR-0016, point 4/criterion
  K-06: "new identifiers, the same `name`").
- `scenario_id` — set to the copy's own scenario, which is the whole point of copying.
- `created_at`/`updated_at` — the copy's rows are created now; inheriting the source's timestamps
  would backdate a row that did not exist and hand the copy a stale ADR-0007 marker.

`name` is copied verbatim (ADR-0016, criterion K-06). Everything else is copied by reflection over
the mapper rather than a hand-written field list, so a column added to this table later is copied by
default instead of silently dropped — the drift guard in
`tests/test_scenario_delivery_segment_copy.py` asserts every mapped attribute is either copied or
named here, so a new column forces the decision instead of inheriting one."""


# --- exceptions: the division every write path here makes -----------------------------------------


class ScenarioDeliverySegmentWriteFailed(WriteFailed):
    """A segment write failed for a reason nothing here established (R-01, SC-2-01)."""


class ScenarioDeliverySegmentWriteRefused(ScenarioDeliverySegmentWriteFailed, WriteRefused):
    """The database refused the write for a reason its SQLSTATE names — the two uniqueness
    constraints (K-03, K-09) or the non-blank name CHECK. Named by SQLSTATE and constraint,
    quoting no value (NF-11)."""


class ScenarioDeliverySegmentWriteRejected(RuntimeError):
    """The write was understood and refused *by state* — distinct from a broken write (ADR-0007:
    one place, two reasons for refusal)."""


class ScenarioDeliverySegmentFrozen(ScenarioDeliverySegmentWriteRejected):
    """The scenario is `approved`, so its segments are part of an approved calculation. Permanent:
    the way forward is a copy of the scenario, which is a `draft` (ADR-0004)."""


class ScenarioDeliverySegmentScenarioChanged(ScenarioDeliverySegmentWriteRejected):
    """The insert matched no open scenario for a reason that is not "approved" — the scenario was
    removed, or changed state, between whatever decided to call this function and the statement
    itself. Carries nothing about what changed (NF-11)."""


class ScenarioDeliverySegmentScenarioNotFound(RuntimeError):
    """No such scenario at all. Raised rather than returned as `None`, on the same reasoning as
    `app.data.additional_cost.AdditionalCostNotFound`: there is no scope layer here yet to collapse
    this into a single "not for you" answer — the first endpoint over this table adds that."""


def _failure(error: SQLAlchemyError) -> WriteFailed:
    return failure_for(
        error,
        subject="scenario delivery segment",
        refused=ScenarioDeliverySegmentWriteRefused,
        failed=ScenarioDeliverySegmentWriteFailed,
    )


_FROZEN_MESSAGE = (
    "This scenario is approved, so its delivery segments are part of an approved calculation and "
    "cannot be changed. Copy the scenario to open a new version and change the copy."
)


def _scenario_exists(session: Session, scenario_id: uuid.UUID) -> bool:
    return session.execute(
        sa.select(sa.exists().where(Scenario.id == scenario_id))
    ).scalar_one()


def _scenario_is_approved(session: Session, scenario_id: uuid.UUID) -> bool:
    return session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()


def _row_by_id(session: Session, segment_id: uuid.UUID) -> ScenarioDeliverySegment:
    """Re-read the row after a write — the one the database holds, not the object assembled in
    Python (`updated_at` is the database's clock)."""
    return session.execute(
        sa.select(ScenarioDeliverySegment)
        .where(ScenarioDeliverySegment.id == segment_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _diagnose_insert_refusal(session: Session, scenario_id: uuid.UUID) -> Exception:
    """Name the reason the guarded insert wrote nothing — after the refusal, never as the guard
    (the R-01 order: does the target exist, before may it be written)."""
    if not _scenario_exists(session, scenario_id):
        return ScenarioDeliverySegmentScenarioNotFound("No such scenario.")
    if _scenario_is_approved(session, scenario_id):
        return ScenarioDeliverySegmentFrozen(_FROZEN_MESSAGE)
    return ScenarioDeliverySegmentScenarioChanged(
        "The scenario changed since it was checked. Re-read it and try again."
    )


def create_scenario_delivery_segment(
    session: Session, scenario_id: uuid.UUID, name: str
) -> ScenarioDeliverySegment:
    """Insert one segment under `scenario_id`, or refuse.

    ```
    INSERT INTO scenario_delivery_segment (id, scenario_id, name)
    SELECT :id, open_scenario.id, :name
      FROM (SELECT id FROM scenarios
             WHERE id = :scenario_id AND status <> 'approved' FOR UPDATE) AS open_scenario
    RETURNING id
    ```

    **The `approved` refusal and the lock are inside the statement that writes** (ADR-0004,
    addendum 2026-09-25 SC-1-11, point 2): no open parent → no row → zero rows returned, diagnosed
    only afterwards. `UNIQUE (scenario_id, name)` and the non-blank CHECK are the database's own,
    reported by SQLAlchemy as an `IntegrityError` and classified by `_failure` — never checked here
    first, so the shape of the row is asserted exactly once, by the database (K-09).
    """
    segment_id = uuid.uuid4()
    open_scenario = unapproved_scenario(scenario_id).subquery("open_scenario")
    columns = _SEGMENT_TABLE.c

    def typed(value: object, column: sa.Column) -> sa.ColumnElement:
        # An untyped parameter in the `SELECT` of an `INSERT … SELECT` reaches PostgreSQL as
        # `unknown` (the reason `app.data.staffing.create_position` gives for the same pattern).
        return sa.literal(value, type_=column.type)

    source = sa.select(
        typed(segment_id, columns.id).label("id"),
        open_scenario.c.id.label("scenario_id"),
        typed(name, columns.name).label("name"),
    ).select_from(open_scenario)
    statement = (
        sa.insert(_SEGMENT_TABLE)
        .from_select(["id", "scenario_id", "name"], source)
        .returning(columns.id)
    )

    try:
        inserted = session.execute(statement).one_or_none()
        if inserted is None:
            # Not a `SQLAlchemyError`, so it passes the `except` below untouched (the reasoning of
            # `app.data.additional_cost.create_additional_cost`).
            raise _diagnose_insert_refusal(session, scenario_id)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`: PostgreSQL's `DETAIL: Failing row contains (…)` must not ride along (NF-11).
        raise _failure(error) from None
    return _row_by_id(session, segment_id)


# --- the copying cascade (ADR-0004, addendum 2026-09-25 SC-1-11, point 4) -------------------------


def copy_scenario_delivery_segments(session: Session, source: Scenario, copy: Scenario) -> None:
    """Copy every segment of `source` onto `copy`, with new identifiers and the same `name`.

    The single entry in `SCENARIO_CHILD_COPIERS` for this table (criterion K-06). Segment has no
    child or grandchild of its own (ADR-0016, point 9), so — unlike the staffing or additional-cost
    aggregates — there is no old-to-new id mapping to hold: every row is copied independently.

    Nothing here is guarded against `approved`: every copy is a `draft` (`copy_scenario` takes no
    status), and the source is only read.
    """
    segments = list(
        session.execute(
            sa.select(ScenarioDeliverySegment)
            .where(ScenarioDeliverySegment.scenario_id == source.id)
            .order_by(ScenarioDeliverySegment.name, ScenarioDeliverySegment.id)
        )
        .scalars()
        .all()
    )
    for segment in segments:
        session.add(
            ScenarioDeliverySegment(
                id=uuid.uuid4(),
                scenario_id=copy.id,
                **values_to_copy(segment, excluded=SEGMENT_COLUMNS_NOT_COPIED),
            )
        )
    session.flush()
