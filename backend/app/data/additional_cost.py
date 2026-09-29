"""The only path by which a scenario's additional costs are read, written, copied and summed.

SC-5-05 (F-08, Issue #10; ADR-0014).

Four mechanisms, none of them new — each is an existing mechanism of this repository applied to the
first table of F-08 (ADR-0014; ADR-0004, ADR-0005, ADR-0007 addenda 2026-09-23 SC-5-05):

1. **Scope** — `app.data.staffing.scenario_in_scope`, i.e. `project_for_caller` plus membership of
   `Project.scenarios`. No scope function of its own and no `select(Scenario)` here: "no such
   project", "not yours" and "that scenario belongs to another project" are one `None`, for the
   read and for every write (ADR-0005, addendum SC-5-05, point 4; criterion K-08). A cost id or a
   position id of another scenario is narrowed away **inside the statement** (`scenario_id =
   :scenario_id` in every `WHERE`, the position joined only within the scenario), so a
   path-confusion request matches nothing and answers the same `404`.
2. **The refusal of a write to an `approved` scenario, in the statement that writes** (ADR-0004,
   addendum SC-5-05, point 3) — `app.data.scenario_guard.unapproved_scenario`, embedded as the
   source of the `INSERT … SELECT` and as `scenario_id IN (…)` in the `UPDATE`/`DELETE`. It also
   takes the scenario row lock that serialises the write against a concurrent approval
   (criterion K-06).
3. **ADR-0007's marker per cost row** (addendum SC-5-05): `updated_at = :expected` in the same
   `WHERE`, and `updated_at = now()` in the same `SET`. The unit of editing is one cost, not the
   position and not the scenario — editing cost A leaves cost B's marker alone.
4. **Two copiers for two halves** (ADR-0014, point 10, Q-6 = A): the costs attached to a position
   are copied inside `app.data.staffing.copy_staffing_positions` (it holds the old-to-new position
   ids); the costs with no position are copied by `copy_scenario_additional_costs` below, its own
   entry in `app.data.project_writes.SCENARIO_CHILD_COPIERS`.

**What this module never reads: a revenue, a rate, a personnel cost.** It imports nothing of
`app.data.commercial_terms`, `app.domain.revenue*`, `app.data.personnel_cost` or
`app.domain.personnel_cost`, and none of them imports it (ADR-0014, "Konsekwencje"; control D-8).

**No cost gate.** Additional costs are read and written under `STAFFING_READ`/`STAFFING_WRITE`,
with no `PERSONNEL_COSTS_READ` conjunction (ADR-0014, point 11, Q-7 = B; ADR-0005, addendum
SC-5-05, point 1) — which is why the view below carries no per-caller flag: a flag field would
advertise a gate that does not exist. The named risk of that decision (a recruitment cost on a
`headcount = 1` position points at one person) is ADR-0005's, addendum SC-5-05, point 2.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.column_copy import values_to_copy
from app.data.risk_copy import copied_risk_ids, remapped
from app.data.scenario_guard import unapproved_scenario
from app.data.staffing import ADDITIONAL_COST_COLUMNS_NOT_COPIED, scenario_in_scope
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.domain.additional_cost import AdditionalCostAnswer, CostLine, additional_cost_total
from app.models.additional_cost import AdditionalCost
from app.models.catalog import CatalogCostCategory
from app.models.risk import ScenarioRisk
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import StaffingPosition

_COST_TABLE = AdditionalCost.__table__
_POSITION_TABLE = StaffingPosition.__table__
_RISK_TABLE = ScenarioRisk.__table__

EDITABLE_ADDITIONAL_COST_FIELDS: frozenset[str] = frozenset(
    {
        "category_id",
        "position_id",
        "risk_id",
        "amount",
        "currency",
        "cost_type",
        "start_month",
        "end_month",
        "funding_source",
    }
)
"""Everything the edit path will ever write. An allow-list rather than "whatever the request
carried": `id`, `scenario_id` and the timestamps are not user input. Moving a cost to another
scenario is not an edit — it would be a write to two calculations, one of which may be approved."""


# --- exceptions: the division every write path here makes -----------------------------------------


class AdditionalCostWriteFailed(WriteFailed):
    """A cost write failed for a reason nothing here established — a `500` (R-01, SC-2-01)."""


class AdditionalCostWriteRefused(AdditionalCostWriteFailed, WriteRefused):
    """The database refused the write for a reason its SQLSTATE names — a `409`: a CHECK on the
    row's shape, a category that does not exist. Named by SQLSTATE and constraint, quoting no value
    (NF-11)."""


class AdditionalCostWriteRejected(RuntimeError):
    """The write was understood and refused *by state* — a `409`, distinct from a broken write.
    Two subclasses, two independent reasons (ADR-0007: one place, two reasons for refusal)."""


class AdditionalCostFrozen(AdditionalCostWriteRejected):
    """The scenario is `approved`, so its additional costs are part of an approved calculation.
    Permanent: the way forward is a copy of the scenario, which is a `draft` (ADR-0004)."""


class ConcurrentAdditionalCostEditConflict(AdditionalCostWriteRejected):
    """The cost changed since the caller read it (ADR-0007, addendum SC-5-05) — or, on an insert,
    the scenario changed between the scope read and the write. Resolved by re-reading. Carries
    nothing about the competing change."""


class AdditionalCostNotFound(RuntimeError):
    """No such cost in this scenario, or no such position or risk in this scenario.

    Answered as the same `404` as "no such scenario" (ADR-0005, addendum SC-5-05, point 4). Raised
    rather than returned as `None` so that "outside your scope" and "no such row here" stay two
    facts *inside* this layer while the API answers both with one body — the division
    `app.data.staffing.AbsenceNotFound` makes.
    """


class AdditionalCostFieldNotEditable(RuntimeError):
    """A caller asked to write a column outside `EDITABLE_ADDITIONAL_COST_FIELDS`, or nothing.

    A programming error, not a client error (the request schema cannot express either case) —
    deliberately not a `AdditionalCostWriteRejected`, so it cannot be answered with a `409`.
    """


def _failure(error: SQLAlchemyError) -> WriteFailed:
    return failure_for(
        error,
        subject="additional cost",
        refused=AdditionalCostWriteRefused,
        failed=AdditionalCostWriteFailed,
    )


_FROZEN_MESSAGE = (
    "This scenario is approved, so its additional costs are part of an approved calculation and "
    "cannot be changed. Copy the scenario to open a new version and change the copy."
)


# --- reading -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AdditionalCostRow:
    """One cost row and the current name of its category — a label read live (ADR-0004, addendum
    SC-5-05, point 2: group 1, no snapshot)."""

    cost: AdditionalCost
    category_name: str


@dataclass(frozen=True)
class ScenarioAdditionalCostView:
    """One scenario's additional costs and their sum, resolved in one read.

    A value object for the reason `StaffingPositionView` is one: the shaping layer never receives a
    `Session`. **No per-caller flag, and that absence is the statement** (module docstring, "No cost
    gate").
    """

    scenario: Scenario
    costs: Sequence[AdditionalCostRow]
    total: AdditionalCostAnswer
    status_at_read: ScenarioStatus
    """The scenario's status **as this read saw it**, copied into an immutable value right after
    this read's own `session.refresh` (SC-7-03, reviewer R-01; ADR-0015, addendum SC-7-03, point 8).
    This read never branches on it — draft and approved read the same live rows — but its refresh
    moves the shared, identity-mapped `Scenario` that the composed reads
    (`app.data.scenario_results`, `app.data.scenario_what_if`) go on to branch on and serialise, so
    the race guard there compares this value too. Never serialised by this module's own endpoint."""


def _rows(session: Session, scenario_id: uuid.UUID) -> list[AdditionalCostRow]:
    """Every cost **of this scenario** — its own rows and nothing joined to them but the category.

    No join to positions or allocation months: a cost belongs to the scenario (and optionally to
    one position) by its own columns, and a join to anything with more than one row per cost would
    fan it out (criterion K-01). `populate_existing`, because a row loaded earlier in the same
    session may predate a write made through Core since.
    """
    statement = (
        sa.select(AdditionalCost, CatalogCostCategory.name)
        .join(CatalogCostCategory, CatalogCostCategory.id == AdditionalCost.category_id)
        .where(AdditionalCost.scenario_id == scenario_id)
        .order_by(AdditionalCost.start_month, AdditionalCost.id)
        .execution_options(populate_existing=True)
    )
    return [
        AdditionalCostRow(cost=cost, category_name=name)
        for cost, name in session.execute(statement).all()
    ]


def _line_of(row: AdditionalCostRow) -> CostLine:
    cost = row.cost
    return CostLine(
        cost_id=cost.id,
        position_id=cost.position_id,
        category_id=cost.category_id,
        category_name=row.category_name,
        funding_source=cost.funding_source,
        cost_type=cost.cost_type,
        amount=cost.amount,
        currency=cost.currency,
        start_month=cost.start_month,
        end_month=cost.end_month,
    )


def additional_costs_for_caller(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> ScenarioAdditionalCostView | None:
    """The costs and their sum for one scenario — or `None`, with no way to tell why (K-08).

    `None` is "no such scenario *for this caller*"; a scenario with no cost is a view with an empty
    list and a sum of `0.00` (or the named `no_cost_currency`), never `None`.

    Draft and approved scenarios read the same live rows: nothing outside the scenario can change
    them, and after an approval the write guard keeps them as they were (ADR-0004, addendum SC-5-05,
    point 1 — group 2, no snapshot).
    """
    scenario = scenario_in_scope(session, caller, project_id, scenario_id)
    if scenario is None:
        return None
    # Refreshed, not trusted from the identity map: the currency and status may have changed since
    # the object was loaded earlier in the same session.
    session.refresh(scenario)
    status_at_read = scenario.status
    rows = _rows(session, scenario.id)
    return ScenarioAdditionalCostView(
        scenario=scenario,
        costs=rows,
        total=additional_cost_total(
            [_line_of(row) for row in rows], scenario_currency=scenario.currency
        ),
        status_at_read=status_at_read,
    )


def _row_by_id(session: Session, cost_id: uuid.UUID) -> AdditionalCostRow:
    """Re-read one cost after a write — the row the database holds, not an object assembled in
    Python (`NUMERIC(14,4)` and `now()` are the database's)."""
    cost, name = session.execute(
        sa.select(AdditionalCost, CatalogCostCategory.name)
        .join(CatalogCostCategory, CatalogCostCategory.id == AdditionalCost.category_id)
        .where(AdditionalCost.id == cost_id)
        .execution_options(populate_existing=True)
    ).one()
    return AdditionalCostRow(cost=cost, category_name=name)


# --- writing -------------------------------------------------------------------------------------


def _position_in_scenario(
    session: Session, scenario_id: uuid.UUID, position_id: uuid.UUID
) -> bool:
    """Is there such a position *inside this scenario*? Reached only after `scenario_in_scope`, so
    it can never answer about a project the caller cannot see."""
    return session.execute(
        sa.select(
            sa.exists().where(
                StaffingPosition.id == position_id, StaffingPosition.scenario_id == scenario_id
            )
        )
    ).scalar_one()


def _risk_in_scenario(session: Session, scenario_id: uuid.UUID, risk_id: uuid.UUID) -> bool:
    """Is there such a declared risk *inside this scenario*? (SC-6-08; the mirror of
    `_position_in_scenario`, for the link a cost event may carry.)"""
    return session.execute(
        sa.select(
            sa.exists().where(ScenarioRisk.id == risk_id, ScenarioRisk.scenario_id == scenario_id)
        )
    ).scalar_one()


def _cost_in_scenario(session: Session, scenario_id: uuid.UUID, cost_id: uuid.UUID) -> bool:
    return session.execute(
        sa.select(
            sa.exists().where(
                AdditionalCost.id == cost_id, AdditionalCost.scenario_id == scenario_id
            )
        )
    ).scalar_one()


def _scenario_is_approved(session: Session, scenario_id: uuid.UUID) -> bool:
    return session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()


def create_additional_cost(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    category_id: uuid.UUID,
    position_id: uuid.UUID | None,
    risk_id: uuid.UUID | None = None,
    amount: Decimal,
    currency: str,
    cost_type: str,
    start_month: date,
    end_month: date | None,
    funding_source: str,
) -> AdditionalCostRow | None:
    """Insert one cost — or refuse, or answer `None` ("no such scenario for this caller").

    ```
    INSERT INTO additional_cost (id, scenario_id, position_id, …)
    SELECT :id, open_scenario.id, same_scenario_position.id, …
      FROM (SELECT id FROM scenarios
             WHERE id = :scenario_id AND status <> 'approved' FOR UPDATE) AS open_scenario
      [ CROSS JOIN (SELECT id FROM staffing_position
                     WHERE id = :position_id AND scenario_id = :scenario_id)
                 AS same_scenario_position ]
    RETURNING id
    ```

    - **The `approved` refusal and the lock are inside the statement that writes** (ADR-0004,
      addendum SC-5-05, point 3): no open parent → no row → zero rows returned, diagnosed only
      afterwards.
    - **The position is taken from the scenario, not from the request**: a position id of another
      scenario — possibly of a project the caller cannot see — joins nothing, the insert writes
      nothing, and the answer is the same `404` as for a position that does not exist (K-08). The
      composite foreign key stays the guarantee underneath (K-03) for every path that is not this
      one.
    - **The declared risk is taken from the scenario as well** (SC-6-08; ADR-0021, points 1 and 7):
      a risk id of another scenario joins nothing and is the same `404`; the composite foreign key
      `fk_additional_cost_risk_same_scenario` stays the guarantee for every other path. The link
      is written by this same guarded statement, so it is refused under `approved` like the rest.
    - `amount` is written as given — `NUMERIC(14,4)`, never rounded here (ADR-0014, point 6).
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    cost_id = uuid.uuid4()
    open_scenario = unapproved_scenario(scenario_id).subquery("open_scenario")
    columns = _COST_TABLE.c

    def typed(value: object, column: sa.Column) -> sa.ColumnElement[Any]:
        # Typed literals, for the reason `app.data.staffing.create_position` gives: an untyped
        # parameter in the `SELECT` of an `INSERT … SELECT` reaches PostgreSQL as `unknown`.
        return sa.literal(value, type_=column.type)

    position_column: sa.ColumnElement[Any] = typed(None, columns.position_id)
    risk_column: sa.ColumnElement[Any] = typed(None, columns.risk_id)
    source_from: sa.FromClause = open_scenario
    if position_id is not None:
        same_scenario_position = (
            sa.select(_POSITION_TABLE.c.id)
            .where(
                _POSITION_TABLE.c.id == position_id,
                _POSITION_TABLE.c.scenario_id == scenario_id,
            )
            .subquery("same_scenario_position")
        )
        position_column = same_scenario_position.c.id
        source_from = source_from.join(same_scenario_position, sa.true())
    if risk_id is not None:
        same_scenario_risk = (
            sa.select(_RISK_TABLE.c.id)
            .where(_RISK_TABLE.c.id == risk_id, _RISK_TABLE.c.scenario_id == scenario_id)
            .subquery("same_scenario_risk")
        )
        risk_column = same_scenario_risk.c.id
        source_from = source_from.join(same_scenario_risk, sa.true())

    source = sa.select(
        typed(cost_id, columns.id).label("id"),
        open_scenario.c.id.label("scenario_id"),
        position_column.label("position_id"),
        risk_column.label("risk_id"),
        typed(category_id, columns.category_id).label("category_id"),
        typed(amount, columns.amount).label("amount"),
        typed(currency, columns.currency).label("currency"),
        typed(cost_type, columns.cost_type).label("cost_type"),
        typed(start_month, columns.start_month).label("start_month"),
        typed(end_month, columns.end_month).label("end_month"),
        typed(funding_source, columns.funding_source).label("funding_source"),
    ).select_from(source_from)
    statement = (
        sa.insert(_COST_TABLE)
        .from_select(
            [
                "id",
                "scenario_id",
                "position_id",
                "risk_id",
                "category_id",
                "amount",
                "currency",
                "cost_type",
                "start_month",
                "end_month",
                "funding_source",
            ],
            source,
        )
        .returning(columns.id)
    )

    try:
        inserted = session.execute(statement).one_or_none()
        if inserted is None:
            # Nothing was written, so nothing is rolled back — the reasoning of
            # `app.data.staffing.create_position`. Not a `SQLAlchemyError`, so it passes the
            # `except` below untouched.
            raise _diagnose_insert_refusal(session, scenario_id, position_id, risk_id)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`: PostgreSQL's `DETAIL: Failing row contains (…)` must not ride along (NF-11).
        raise _failure(error) from None
    return _row_by_id(session, cost_id)


def _diagnose_insert_refusal(
    session: Session,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID | None,
    risk_id: uuid.UUID | None,
) -> Exception:
    """Name the reason the guarded insert wrote nothing — after the refusal, never as the guard.

    **"Does the target exist?" before "may it be written?"** (the R-01 order of
    `app.data.staffing._diagnose_allocation_refusal`): a position that is not in this scenario is a
    `404` even under an `approved` scenario, because "copy the scenario and add it there" would be
    advice that cannot work.
    """
    if position_id is not None and not _position_in_scenario(session, scenario_id, position_id):
        return AdditionalCostNotFound("No such staffing position in this scenario.")
    if risk_id is not None and not _risk_in_scenario(session, scenario_id, risk_id):
        return AdditionalCostNotFound("No such risk in this scenario.")
    if _scenario_is_approved(session, scenario_id):
        return AdditionalCostFrozen(_FROZEN_MESSAGE)
    return ConcurrentAdditionalCostEditConflict(
        "The scenario changed since it was read. Re-read it and apply the change again."
    )


def update_additional_cost(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    cost_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> AdditionalCostRow | None:
    """Edit one cost, or refuse — `None` when there is no such scenario for this caller.

    Everything that decides whether this write may happen is in **one statement**:

    ```
    UPDATE additional_cost SET …, updated_at = now()
     WHERE id = :cost_id
       AND scenario_id = :scenario_id                    -- a cost of another scenario: nothing
       AND updated_at = :expected                        -- ADR-0007, this row's own marker
       AND scenario_id IN (SELECT id FROM scenarios
                            WHERE id = :scenario_id AND status <> 'approved'
                              FOR UPDATE)                -- ADR-0004, and the lock (K-06)
       [ AND EXISTS (SELECT 1 FROM staffing_position
                      WHERE id = :new_position_id AND scenario_id = :scenario_id) ]
    RETURNING id
    ```

    The marker is compared **by the database, inside the `UPDATE`**, and rotated by the same
    statement: a Python comparison against a value read a moment earlier is the window the marker
    exists to close. A row's shape (type, period, amount) that a CHECK refuses is a `409` naming the
    constraint (`AdditionalCostWriteRefused`).
    """
    forbidden = sorted(set(changes) - EDITABLE_ADDITIONAL_COST_FIELDS)
    if forbidden:
        raise AdditionalCostFieldNotEditable(
            "These additional-cost fields cannot be edited through this function: "
            + ", ".join(forbidden)
            + f". Editable: {', '.join(sorted(EDITABLE_ADDITIONAL_COST_FIELDS))}."
        )
    if not changes:
        raise AdditionalCostFieldNotEditable(
            "An additional-cost edit must name at least one field to change."
        )

    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    new_position_id = changes.get("position_id")
    new_risk_id = changes.get("risk_id")
    conditions: list[sa.ColumnElement[bool]] = [
        _COST_TABLE.c.id == cost_id,
        _COST_TABLE.c.scenario_id == scenario_id,
        _COST_TABLE.c.updated_at == expected_updated_at,
        _COST_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
    ]
    if new_position_id is not None:
        conditions.append(
            sa.exists().where(
                _POSITION_TABLE.c.id == new_position_id,
                _POSITION_TABLE.c.scenario_id == scenario_id,
            )
        )
    if new_risk_id is not None:
        # SC-6-08: the new link must be a risk of this scenario - inside the same statement, like
        # the position above (`None` unlinks, and needs no target).
        conditions.append(
            sa.exists().where(
                _RISK_TABLE.c.id == new_risk_id, _RISK_TABLE.c.scenario_id == scenario_id
            )
        )
    statement = (
        sa.update(_COST_TABLE)
        .where(*conditions)
        # Explicit rather than left to the column's `onupdate`: the rotation of the marker is part
        # of what this statement claims, and `now()` is the database's clock.
        .values(**dict(changes), updated_at=sa.func.now())
        .returning(_COST_TABLE.c.id)
    )

    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            raise _diagnose_row_refusal(
                session,
                scenario_id,
                cost_id,
                new_position_id=new_position_id,
                new_risk_id=new_risk_id,
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _row_by_id(session, cost_id)


def delete_additional_cost(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    cost_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
) -> bool | None:
    """Remove one cost, or refuse — `None` when there is no such scenario for this caller.

    **Guarded exactly like a write, because it is one** (ADR-0004, addendum SC-5-05, point 3 —
    INSERT, UPDATE *and* DELETE): the scenario's id, the row's marker and the lock against a
    concurrent approval are all in the `WHERE` of the statement that deletes. A delete of an id
    that is not in this scenario matches nothing and is a `404`, even under an `approved` scenario
    (K-06: "a non-existent id under `approved` → `404`, not `409`").
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None
    statement = (
        sa.delete(_COST_TABLE)
        .where(
            _COST_TABLE.c.id == cost_id,
            _COST_TABLE.c.scenario_id == scenario_id,
            _COST_TABLE.c.updated_at == expected_updated_at,
            _COST_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
        )
        .returning(_COST_TABLE.c.id)
    )
    try:
        deleted = session.execute(statement).one_or_none()
        if deleted is None:
            raise _diagnose_row_refusal(
                session, scenario_id, cost_id, new_position_id=None, new_risk_id=None
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return True


def _diagnose_row_refusal(
    session: Session,
    scenario_id: uuid.UUID,
    cost_id: uuid.UUID,
    *,
    new_position_id: uuid.UUID | None,
    new_risk_id: uuid.UUID | None,
) -> Exception:
    """Name the reason an edit or a delete matched no row — after the refusal, never as the guard.

    The order is the R-01 order: the target first (the cost in this scenario, then — on an edit
    that re-attaches it — the position in this scenario), and only then the permanent reason
    (`approved`) before the transient one (the marker). Every branch is reached only for a scenario
    `scenario_in_scope` has already returned, and every lookup is narrowed to it.
    """
    if not _cost_in_scenario(session, scenario_id, cost_id):
        return AdditionalCostNotFound("No such additional cost in this scenario.")
    if new_position_id is not None and not _position_in_scenario(
        session, scenario_id, new_position_id
    ):
        return AdditionalCostNotFound("No such staffing position in this scenario.")
    if new_risk_id is not None and not _risk_in_scenario(session, scenario_id, new_risk_id):
        return AdditionalCostNotFound("No such risk in this scenario.")
    if _scenario_is_approved(session, scenario_id):
        return AdditionalCostFrozen(_FROZEN_MESSAGE)
    return ConcurrentAdditionalCostEditConflict(
        "The additional cost changed since it was read (concurrency marker). Re-read it and "
        "apply the change again."
    )


# --- the copying cascade: the scenario-level half (ADR-0014, point 10, Q-6 = A) ------------------


def copy_scenario_additional_costs(session: Session, source: Scenario, copy: Scenario) -> None:
    """Copy the source scenario's costs **with no position** onto the copy, with new identifiers.

    The entry in `SCENARIO_CHILD_COPIERS` for the scenario-level half (ADR-0004, addendum SC-5-05,
    point 4). **Only `position_id IS NULL`**: the position-attached costs are copied by
    `app.data.staffing.copy_staffing_positions`, which holds the old-to-new position ids, and
    copying them here as well would count every one of them twice on the copy (or, with the
    source's `position_id`, be refused by `fk_additional_cost_position_same_scenario`).

    Nothing here is guarded against `approved`: every copy is a `draft` (`copy_scenario` takes no
    status), and the source is only read.
    """
    costs = list(
        session.execute(
            sa.select(AdditionalCost)
            .where(AdditionalCost.scenario_id == source.id, AdditionalCost.position_id.is_(None))
            .order_by(AdditionalCost.start_month, AdditionalCost.id)
        )
        .scalars()
        .all()
    )
    # SC-6-08: the link to a declared risk is remapped to the copy's own risk by name (ADR-0021,
    # point 8); the risk entry of the registry runs before this one.
    risk_mapping = copied_risk_ids(session, source.id, copy.id)
    for cost in costs:
        session.add(
            AdditionalCost(
                id=uuid.uuid4(),
                scenario_id=copy.id,
                position_id=None,
                risk_id=remapped(risk_mapping, cost.risk_id),
                **values_to_copy(cost, excluded=ADDITIONAL_COST_COLUMNS_NOT_COPIED),
            )
        )
    session.flush()
