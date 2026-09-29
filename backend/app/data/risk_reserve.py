"""The only path by which a scenario's risk reserves are read, written, copied and summed.

SC-6-08 (F-09 pt 4, Issue #89; ADR-0021, points 2-4, 6-10).

The same four mechanisms as `app.data.risk` and `app.data.additional_cost`, applied to the
`risk_reserve` table - see the first for the scope, guard and marker, the second for the
`INSERT ... SELECT` construction this module copies:

1. scope through `app.data.staffing.scenario_in_scope`, one `404` body for every "nothing here for
   you" (criterion K-07);
2. the `approved` refusal and the lock **in the statement that writes** (K-05);
3. ADR-0007's marker per reserve row;
4. the optional link to a declared risk is taken **from the scenario inside the statement** (a risk
   of another scenario joins nothing and is the same `404`), with the composite foreign key
   `fk_risk_reserve_risk_same_scenario` as the guarantee underneath (K-04).

**The reserve total is reported beside the additional cost, never inside it** (ADR-0021, point 3,
Q-3 = A). `reserves_for_caller` computes it from the reserves of the scenario only, through
`app.domain.risk_reserve.reserve_total`; nothing here calls `app.data.additional_cost`,
`app.data.scenario_results` or any revenue/personnel-cost module (control R-08), and no module of
those imports this one - so `included_cost`, `profit`, `margin` and `markup` cannot move because a
reserve exists (control R-02).

**No cost gate** (ADR-0021, point 9): `STAFFING_READ`/`STAFFING_WRITE`, no `PERSONNEL_COSTS_READ`
conjunction, and a reserve carries no `position_id`.
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
from app.data.risk_copy import RESERVE_COLUMNS_NOT_COPIED, copied_risk_ids, remapped
from app.data.scenario_guard import unapproved_scenario
from app.data.staffing import scenario_in_scope
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.domain.risk_reserve import ReserveLine, ReserveTotalAnswer, reserve_total
from app.models.risk import RiskReserve, ScenarioRisk
from app.models.scenario import Scenario, ScenarioStatus

_RESERVE_TABLE = RiskReserve.__table__
_RISK_TABLE = ScenarioRisk.__table__

DEFAULT_RESERVE_LIST_LIMIT = 200
MAX_RESERVE_LIST_LIMIT = 1000
MAX_RESERVE_LIST_OFFSET = 1_000_000
"""ADR-0017, point 3: the constants of this resource."""

MAX_RESERVE_MONTHS = 60
"""The most months one recurring reserve may span, both ends included (ADR-0014, point 4). The data
layer does not import from `app.api`, so this is spelled here and a test pins it equal to the
request schema's `MAX_RESERVE_MONTHS`; the schema checks a create (and an edit naming both ends)
early, this bound is the one an edit naming a single end cannot escape."""

EDITABLE_RESERVE_FIELDS: frozenset[str] = frozenset(
    {"risk_id", "amount", "currency", "reserve_type", "start_month", "end_month"}
)
"""Everything the edit path will ever write; `id`, `scenario_id` and the timestamps are not user
input, and moving a reserve to another scenario is not an edit."""


class ReserveWriteFailed(WriteFailed):
    """A reserve write failed for a reason nothing here established - a `500`."""


class ReserveWriteRefused(ReserveWriteFailed, WriteRefused):
    """The database refused the write for a reason its SQLSTATE names - a `409`."""


class ReserveWriteRejected(RuntimeError):
    """The write was understood and refused *by state* - a `409`."""


class ReserveFrozen(ReserveWriteRejected):
    """The scenario is `approved`. Permanent: copy the scenario (ADR-0004)."""


class ReserveSpanTooLong(ReserveWriteRejected):
    """The edit would leave a recurring reserve spanning more than `MAX_RESERVE_MONTHS` months. A
    `409` like every refusal by row state: a request naming one end cannot show it."""


class ConcurrentReserveEditConflict(ReserveWriteRejected):
    """The reserve changed since it was read (ADR-0007), or the scenario changed between the scope
    read and an insert. Resolved by re-reading."""


class ReserveNotFound(RuntimeError):
    """No such reserve, or no such risk, in this scenario - the same `404` as no such scenario."""


class ReserveFieldNotEditable(RuntimeError):
    """A programming error, not a client error - deliberately not a `ReserveWriteRejected`."""


def _failure(error: SQLAlchemyError) -> WriteFailed:
    return failure_for(
        error, subject="reserve", refused=ReserveWriteRefused, failed=ReserveWriteFailed
    )


_FROZEN_MESSAGE = (
    "This scenario is approved, so its reserves are part of an approved calculation and cannot "
    "be changed. Copy the scenario to open a new version and change the copy."
)


# --- reading -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReservePage:
    """One page of a scenario's reserves, and the sum of **all** of them.

    `reserve_total` is over every reserve of the scenario, not over the page: a page is a window on
    a list, and a sum that changed with the window would be a different number each time it was
    asked for.
    """

    scenario: Scenario
    reserves: Sequence[RiskReserve]
    total: int
    """Every reserve of the scenario, before paging (ADR-0017, point 5)."""
    reserve_total: ReserveTotalAnswer
    status_at_read: ScenarioStatus


def _line_of(reserve: RiskReserve) -> ReserveLine:
    return ReserveLine(
        reserve_id=reserve.id,
        risk_id=reserve.risk_id,
        reserve_type=reserve.reserve_type,
        amount=reserve.amount,
        currency=reserve.currency,
        start_month=reserve.start_month,
        end_month=reserve.end_month,
    )


def reserves_for_caller(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> ReservePage | None:
    """The reserves of one scenario and their total - or `None`, with no way to tell why (K-07).

    A scenario with no reserve is an empty list and a total of `0.00` (or the named
    `no_cost_currency`), never `None`. Draft and approved scenarios read the same live rows (group
    2 of ADR-0004: no snapshot). `limit=None` is the whole list (ADR-0017, point 6).
    """
    scenario = scenario_in_scope(session, caller, project_id, scenario_id)
    if scenario is None:
        return None
    session.refresh(scenario)
    status_at_read = scenario.status
    every = list(
        session.execute(
            sa.select(RiskReserve)
            .where(RiskReserve.scenario_id == scenario.id)
            .order_by(RiskReserve.start_month, RiskReserve.id)
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    page = every if limit is None else every[offset : offset + limit]
    return ReservePage(
        scenario=scenario,
        reserves=page,
        total=len(every),
        reserve_total=reserve_total(
            [_line_of(reserve) for reserve in every], scenario_currency=scenario.currency
        ),
        status_at_read=status_at_read,
    )


def _reserve_by_id(session: Session, reserve_id: uuid.UUID) -> RiskReserve:
    return session.execute(
        sa.select(RiskReserve)
        .where(RiskReserve.id == reserve_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


# --- writing -------------------------------------------------------------------------------------


def _risk_in_scenario(session: Session, scenario_id: uuid.UUID, risk_id: uuid.UUID) -> bool:
    return session.execute(
        sa.select(
            sa.exists().where(ScenarioRisk.id == risk_id, ScenarioRisk.scenario_id == scenario_id)
        )
    ).scalar_one()


def _reserve_in_scenario(session: Session, scenario_id: uuid.UUID, reserve_id: uuid.UUID) -> bool:
    return session.execute(
        sa.select(
            sa.exists().where(RiskReserve.id == reserve_id, RiskReserve.scenario_id == scenario_id)
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


def create_reserve(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    risk_id: uuid.UUID | None,
    amount: Decimal,
    currency: str,
    reserve_type: str,
    start_month: date,
    end_month: date | None,
) -> RiskReserve | None:
    """Insert one reserve - or refuse, or answer `None` ("no such scenario for this caller").

    ```
    INSERT INTO risk_reserve (id, scenario_id, risk_id, ...)
    SELECT :id, open_scenario.id, same_scenario_risk.id, ...
      FROM (SELECT id FROM scenarios
             WHERE id = :scenario_id AND status <> 'approved' FOR UPDATE) AS open_scenario
      [ CROSS JOIN (SELECT id FROM scenario_risk
                     WHERE id = :risk_id AND scenario_id = :scenario_id) AS same_scenario_risk ]
    RETURNING id
    ```

    `amount` is written as given - `NUMERIC(14,4)`, never rounded here (ADR-0021, point 4).
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    reserve_id = uuid.uuid4()
    open_scenario = unapproved_scenario(scenario_id).subquery("open_scenario")
    columns = _RESERVE_TABLE.c

    def typed(value: object, column: sa.Column) -> sa.ColumnElement[Any]:
        return sa.literal(value, type_=column.type)

    risk_column: sa.ColumnElement[Any] = typed(None, columns.risk_id)
    source_from: sa.FromClause = open_scenario
    if risk_id is not None:
        same_scenario_risk = (
            sa.select(_RISK_TABLE.c.id)
            .where(_RISK_TABLE.c.id == risk_id, _RISK_TABLE.c.scenario_id == scenario_id)
            .subquery("same_scenario_risk")
        )
        risk_column = same_scenario_risk.c.id
        source_from = open_scenario.join(same_scenario_risk, sa.true())

    source = sa.select(
        typed(reserve_id, columns.id).label("id"),
        open_scenario.c.id.label("scenario_id"),
        risk_column.label("risk_id"),
        typed(amount, columns.amount).label("amount"),
        typed(currency, columns.currency).label("currency"),
        typed(reserve_type, columns.reserve_type).label("reserve_type"),
        typed(start_month, columns.start_month).label("start_month"),
        typed(end_month, columns.end_month).label("end_month"),
    ).select_from(source_from)
    statement = (
        sa.insert(_RESERVE_TABLE)
        .from_select(
            [
                "id",
                "scenario_id",
                "risk_id",
                "amount",
                "currency",
                "reserve_type",
                "start_month",
                "end_month",
            ],
            source,
        )
        .returning(columns.id)
    )
    try:
        inserted = session.execute(statement).one_or_none()
        if inserted is None:
            raise _diagnose_insert_refusal(session, scenario_id, risk_id)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _reserve_by_id(session, reserve_id)


def _diagnose_insert_refusal(
    session: Session, scenario_id: uuid.UUID, risk_id: uuid.UUID | None
) -> Exception:
    """ "Does the target exist?" before "may it be written?" (the R-01 order): a risk that is not in
    this scenario is a `404` even under an `approved` scenario."""
    if risk_id is not None and not _risk_in_scenario(session, scenario_id, risk_id):
        return ReserveNotFound("No such risk in this scenario.")
    if _scenario_is_approved(session, scenario_id):
        return ReserveFrozen(_FROZEN_MESSAGE)
    return ConcurrentReserveEditConflict(
        "The scenario changed since it was read. Re-read it and apply the change again."
    )


def update_reserve(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    reserve_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> RiskReserve | None:
    """Edit one reserve, or refuse - `None` when there is no such scenario for this caller.

    Everything that decides whether this write may happen is in **one statement**: the reserve id
    and scenario id, the row's marker, the `approved` guard with its lock, and - when the edit
    links a risk - that the risk is one of this scenario. A row's shape (type, period, amount) that
    a CHECK refuses is a `409` naming the constraint.
    """
    forbidden = sorted(set(changes) - EDITABLE_RESERVE_FIELDS)
    if forbidden or not changes:
        raise ReserveFieldNotEditable(
            f"A reserve edit must name at least one of {sorted(EDITABLE_RESERVE_FIELDS)} and "
            f"nothing else; got {sorted(changes)}."
        )
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    new_risk_id = changes.get("risk_id")
    conditions: list[sa.ColumnElement[bool]] = [
        _RESERVE_TABLE.c.id == reserve_id,
        _RESERVE_TABLE.c.scenario_id == scenario_id,
        _RESERVE_TABLE.c.updated_at == expected_updated_at,
        _RESERVE_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
    ]
    if new_risk_id is not None:
        conditions.append(
            sa.exists().where(
                _RISK_TABLE.c.id == new_risk_id, _RISK_TABLE.c.scenario_id == scenario_id
            )
        )
    span_condition = _resulting_span_within_bound(changes)
    statement = (
        sa.update(_RESERVE_TABLE)
        .where(*conditions, *([span_condition] if span_condition is not None else []))
        .values(**dict(changes), updated_at=sa.func.now())
        .returning(_RESERVE_TABLE.c.id)
    )
    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            refusal = _diagnose_row_refusal(
                session, scenario_id, reserve_id, new_risk_id=new_risk_id
            )
            if isinstance(refusal, ConcurrentReserveEditConflict) and span_condition is not None:
                # Nothing above explains the miss (target, risk, approved); if every other
                # condition still holds, the span bound did it - and a stale marker stays a marker.
                still_matches = session.execute(
                    sa.select(sa.exists().where(*conditions))
                ).scalar_one()
                if still_matches:
                    refusal = ReserveSpanTooLong(
                        f"A recurring reserve may span at most {MAX_RESERVE_MONTHS} months, both "
                        "ends included; this edit would leave the reserve longer than that."
                    )
            raise refusal
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _reserve_by_id(session, reserve_id)


def _month_number(value: Any) -> sa.ColumnElement[Any]:
    return sa.extract("year", value) * 12 + sa.extract("month", value)


def _resulting_span_within_bound(changes: Mapping[str, Any]) -> sa.ColumnElement[bool] | None:
    """The span bound on the reserve **as it will be after the edit**, evaluated inside the `UPDATE`
    (no read of the other half in Python - check-then-act). A field the edit does not name is the
    stored column; an edit that names neither period field, or ends the range (`end_month` null, a
    one-off), has nothing to bound. Without it a body carrying only `end_month` could stretch a
    stored reserve over tens of thousands of months, and every read of the scenario would spread it
    month by month."""
    if "start_month" not in changes and "end_month" not in changes:
        return None
    if "end_month" in changes and changes["end_month"] is None:
        return None
    columns = _RESERVE_TABLE.c
    start = (
        sa.literal(changes["start_month"], type_=columns.start_month.type)
        if "start_month" in changes
        else columns.start_month
    )
    end = (
        sa.literal(changes["end_month"], type_=columns.end_month.type)
        if "end_month" in changes
        else columns.end_month
    )
    return sa.or_(
        end.is_(None), _month_number(end) - _month_number(start) + 1 <= MAX_RESERVE_MONTHS
    )


def delete_reserve(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    reserve_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
) -> bool | None:
    """Remove one reserve, guarded exactly like a write, because it is one. A delete of an id that
    is not in this scenario matches nothing and is a `404`, even under an `approved` scenario."""
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None
    statement = (
        sa.delete(_RESERVE_TABLE)
        .where(
            _RESERVE_TABLE.c.id == reserve_id,
            _RESERVE_TABLE.c.scenario_id == scenario_id,
            _RESERVE_TABLE.c.updated_at == expected_updated_at,
            _RESERVE_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
        )
        .returning(_RESERVE_TABLE.c.id)
    )
    try:
        deleted = session.execute(statement).one_or_none()
        if deleted is None:
            raise _diagnose_row_refusal(session, scenario_id, reserve_id, new_risk_id=None)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return True


def _diagnose_row_refusal(
    session: Session,
    scenario_id: uuid.UUID,
    reserve_id: uuid.UUID,
    *,
    new_risk_id: uuid.UUID | None,
) -> Exception:
    """Name the reason an edit or a delete matched no row - after the refusal, never as the guard.
    The R-01 order: the target, then the new risk, then `approved`, then the marker."""
    if not _reserve_in_scenario(session, scenario_id, reserve_id):
        return ReserveNotFound("No such reserve in this scenario.")
    if new_risk_id is not None and not _risk_in_scenario(session, scenario_id, new_risk_id):
        return ReserveNotFound("No such risk in this scenario.")
    if _scenario_is_approved(session, scenario_id):
        return ReserveFrozen(_FROZEN_MESSAGE)
    return ConcurrentReserveEditConflict(
        "The reserve changed since it was read (concurrency marker). Re-read it and apply the "
        "change again."
    )


# --- the copying cascade (ADR-0021, point 8) -----------------------------------------------------


def copy_scenario_reserves(session: Session, source: Scenario, copy: Scenario) -> None:
    """Copy the source scenario's reserves onto the copy, with new identifiers and each link
    remapped to the copy's **own** risk (by the risk's name).

    The entry in `SCENARIO_CHILD_COPIERS`, placed after `copy_scenario_risks` (the copy's risks must
    exist). Nothing here is guarded against `approved`: every copy is a `draft`, and the source is
    only read.
    """
    reserves = list(
        session.execute(
            sa.select(RiskReserve)
            .where(RiskReserve.scenario_id == source.id)
            .order_by(RiskReserve.start_month, RiskReserve.id)
        )
        .scalars()
        .all()
    )
    risk_mapping = copied_risk_ids(session, source.id, copy.id)
    for reserve in reserves:
        session.add(
            RiskReserve(
                id=uuid.uuid4(),
                scenario_id=copy.id,
                risk_id=remapped(risk_mapping, reserve.risk_id),
                **values_to_copy(reserve, excluded=RESERVE_COLUMNS_NOT_COPIED),
            )
        )
    session.flush()
