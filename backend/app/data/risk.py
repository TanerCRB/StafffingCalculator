"""The only path by which a scenario's declared risks are read and written.

SC-6-08 (F-09 pt 4-5, Issue #89; ADR-0021).

Every mechanism here is an existing mechanism of this repository applied to a new table (ADR-0021,
points 6-10; ADR-0004, ADR-0005, ADR-0007 addenda SC-6-08) - the same four as
`app.data.additional_cost`:

1. **Scope** - `app.data.staffing.scenario_in_scope`, the only scope function. "No such project",
   "not yours", "that scenario belongs to another project" and "no such risk in this scenario" are
   one `None` / one `RiskNotFound`, for the read and for every write (criterion K-07). A risk id of
   another scenario is narrowed away **inside the statement** (`scenario_id = :scenario_id`).
2. **The refusal of a write to an `approved` scenario, in the statement that writes** -
   `app.data.scenario_guard.unapproved_scenario`, embedded as the source of the `INSERT ... SELECT`
   and as `scenario_id IN (...)` in the `UPDATE`/`DELETE`; it also takes the scenario row lock that
   serialises the write against a concurrent approval (criterion K-05).
3. **ADR-0007's marker per risk row** - `updated_at = :expected` in the `WHERE`, `updated_at =
   now()` in the `SET`.
4. **The composite foreign keys and the absent `ON DELETE`** (`app.models.risk`): deleting a risk
   that a cost event or a reserve still points at is refused by the database (criterion K-04), and
   this module turns that refusal into a named `409` (`RiskInUse`).

**What the read reports, and what it never does** (ADR-0021, points 1, 3, 5; gate 1 G-1). Per risk:
which representations exist (`none` / `cost_event` / `reserve` / `both`) and the two **counts** they
come from - and **no amount, ever**. A count is a count of rows that carry the declared link
(`additional_cost.risk_id = risk.id`, `risk_reserve.risk_id = risk.id`), position-level cost events
included, narrowed to the risk's own scenario. Nothing is matched by name, category, month or
amount, and nothing is aggregated across scenarios or projects. The read never writes and never
touches a total: detection is additive (control R-02).

**Not here: the reserve total** (`app.data.risk_reserve`) and **no import of the revenue path or the
personnel cost** (control R-08).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.scenario_guard import unapproved_scenario
from app.data.staffing import scenario_in_scope
from app.data.write_errors import WriteFailed, WriteRefused, failure_for, sqlstate_of
from app.domain.risk_reserve import risk_representation
from app.models.additional_cost import AdditionalCost
from app.models.risk import RiskReserve, ScenarioRisk
from app.models.scenario import Scenario, ScenarioStatus

_RISK_TABLE = ScenarioRisk.__table__

DEFAULT_RISK_LIST_LIMIT = 200
MAX_RISK_LIST_LIMIT = 1000
MAX_RISK_LIST_OFFSET = 1_000_000
"""ADR-0017, point 3: the constants of this resource, declared here, named consistently."""

EDITABLE_RISK_FIELDS: frozenset[str] = frozenset({"name"})
"""Everything the edit path will ever write - `id`, `scenario_id` and the timestamps are not user
input, and moving a risk to another scenario is not an edit."""


class RiskWriteFailed(WriteFailed):
    """A risk write failed for a reason nothing here established - a `500`."""


class RiskWriteRefused(RiskWriteFailed, WriteRefused):
    """The database refused the write for a reason its SQLSTATE names - a `409`: a blank name, a
    second risk of the same name in the scenario. Named by SQLSTATE and constraint, no value."""


class RiskWriteRejected(RuntimeError):
    """The write was understood and refused *by state* - a `409`, distinct from a broken write."""


class RiskFrozen(RiskWriteRejected):
    """The scenario is `approved`, so its risks are part of an approved calculation. Permanent: the
    way forward is a copy of the scenario, which is a `draft` (ADR-0004)."""


class ConcurrentRiskEditConflict(RiskWriteRejected):
    """The risk changed since the caller read it (ADR-0007), or - on an insert - the scenario
    changed between the scope read and the write. Resolved by re-reading."""


class RiskInUse(RiskWriteRejected):
    """A cost event or a reserve still points at this risk, so the database refused to delete it
    (ADR-0021, Q-8 = A). Resolved by unlinking first; deliberately not `SET NULL`/cascade, which
    would be a second, unguarded way to alter the rows of an `approved` scenario."""


class RiskNotFound(RuntimeError):
    """No such risk in this scenario. Answered as the same `404` as "no such scenario"."""


class RiskFieldNotEditable(RuntimeError):
    """A programming error, not a client error - deliberately not a `RiskWriteRejected`."""


def _failure(error: SQLAlchemyError) -> WriteFailed:
    return failure_for(error, subject="risk", refused=RiskWriteRefused, failed=RiskWriteFailed)


_FROZEN_MESSAGE = (
    "This scenario is approved, so its risks are part of an approved calculation and cannot be "
    "changed. Copy the scenario to open a new version and change the copy."
)
_IN_USE_MESSAGE = (
    "A cost event or a reserve still points at this risk. Unlink it from them first, then delete "
    "the risk."
)


# --- reading -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskRow:
    """One declared risk, the two counts of its declared links, and the state they give.

    **No amount anywhere on this type** (gate 1 G-1): it is what makes "the risk read carries no
    figure" a property of the value object rather than of a serialiser.
    """

    risk: ScenarioRisk
    cost_event_count: int
    reserve_count: int
    representation: str


@dataclass(frozen=True)
class RiskPage:
    scenario: Scenario
    risks: Sequence[RiskRow]
    total: int
    """Every risk of the scenario, before paging (ADR-0017, point 5)."""


def _cost_event_count() -> sa.ScalarSelect[int]:
    return (
        sa.select(sa.func.count())
        .select_from(AdditionalCost)
        .where(
            AdditionalCost.risk_id == ScenarioRisk.id,
            AdditionalCost.scenario_id == ScenarioRisk.scenario_id,
        )
        .scalar_subquery()
    )


def _reserve_count() -> sa.ScalarSelect[int]:
    return (
        sa.select(sa.func.count())
        .select_from(RiskReserve)
        .where(
            RiskReserve.risk_id == ScenarioRisk.id,
            RiskReserve.scenario_id == ScenarioRisk.scenario_id,
        )
        .scalar_subquery()
    )


def _row_of(risk: ScenarioRisk, cost_events: int, reserves: int) -> RiskRow:
    return RiskRow(
        risk=risk,
        cost_event_count=cost_events,
        reserve_count=reserves,
        representation=risk_representation(cost_event_count=cost_events, reserve_count=reserves),
    )


def _rows(
    session: Session, scenario_id: uuid.UUID, *, limit: int | None, offset: int
) -> list[tuple[ScenarioRisk, int, int, int]]:
    """`(risk, cost events, reserves, total)` - the page and the total in one statement (ADR-0017,
    point 5), ordered by name then id (a total order, point 2)."""
    statement = (
        sa.select(
            ScenarioRisk,
            _cost_event_count().label("cost_event_count"),
            _reserve_count().label("reserve_count"),
            sa.func.count().over().label("total"),
        )
        .where(ScenarioRisk.scenario_id == scenario_id)
        .order_by(ScenarioRisk.name, ScenarioRisk.id)
        .execution_options(populate_existing=True)
    )
    if limit is not None:
        statement = statement.limit(limit).offset(offset)
    return [
        (risk, cost_events, reserves, total)
        for risk, cost_events, reserves, total in session.execute(statement).all()
    ]


def risks_for_caller(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> RiskPage | None:
    """The declared risks of one scenario, each with its representation - or `None`, with no way to
    tell why (K-07). A scenario with no risk is an empty page, never `None`.

    `limit=None` is the whole list (ADR-0017, point 6: each task decides its default). An offset
    past the end is an empty page with the true total - never a `404`: paging is not a second
    channel that could answer for scope.
    """
    scenario = scenario_in_scope(session, caller, project_id, scenario_id)
    if scenario is None:
        return None
    session.refresh(scenario)
    rows = _rows(session, scenario.id, limit=limit, offset=offset)
    if rows:
        total = rows[0][3]
    else:
        total = session.execute(
            sa.select(sa.func.count())
            .select_from(ScenarioRisk)
            .where(ScenarioRisk.scenario_id == scenario.id)
        ).scalar_one()
    return RiskPage(
        scenario=scenario,
        risks=[_row_of(risk, cost_events, reserves) for risk, cost_events, reserves, _ in rows],
        total=total,
    )


def _row_by_id(session: Session, risk_id: uuid.UUID) -> RiskRow:
    """Re-read one risk after a write - the row the database holds (`now()` is its clock)."""
    risk, cost_events, reserves = session.execute(
        sa.select(ScenarioRisk, _cost_event_count(), _reserve_count())
        .where(ScenarioRisk.id == risk_id)
        .execution_options(populate_existing=True)
    ).one()
    return _row_of(risk, cost_events, reserves)


# --- writing -------------------------------------------------------------------------------------


def _risk_in_scenario(session: Session, scenario_id: uuid.UUID, risk_id: uuid.UUID) -> bool:
    return session.execute(
        sa.select(
            sa.exists().where(ScenarioRisk.id == risk_id, ScenarioRisk.scenario_id == scenario_id)
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


def create_risk(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    name: str,
) -> RiskRow | None:
    """Insert one risk - or refuse, or answer `None` ("no such scenario for this caller").

    ```
    INSERT INTO scenario_risk (id, scenario_id, name)
    SELECT :id, open_scenario.id, :name
      FROM (SELECT id FROM scenarios
             WHERE id = :scenario_id AND status <> 'approved' FOR UPDATE) AS open_scenario
    RETURNING id
    ```

    The `approved` refusal and the lock are inside the statement that writes (ADR-0004; K-05): no
    open parent, no row, zero rows returned - diagnosed only afterwards.
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    risk_id = uuid.uuid4()
    open_scenario = unapproved_scenario(scenario_id).subquery("open_scenario")
    columns = _RISK_TABLE.c
    source = sa.select(
        sa.literal(risk_id, type_=columns.id.type).label("id"),
        open_scenario.c.id.label("scenario_id"),
        sa.literal(name, type_=columns.name.type).label("name"),
    ).select_from(open_scenario)
    statement = (
        sa.insert(_RISK_TABLE)
        .from_select(["id", "scenario_id", "name"], source)
        .returning(columns.id)
    )
    try:
        inserted = session.execute(statement).one_or_none()
        if inserted is None:
            raise _diagnose_insert_refusal(session, scenario_id)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`: PostgreSQL's `DETAIL: Failing row contains (...)` must not ride along.
        raise _failure(error) from None
    return _row_by_id(session, risk_id)


def _diagnose_insert_refusal(session: Session, scenario_id: uuid.UUID) -> Exception:
    if _scenario_is_approved(session, scenario_id):
        return RiskFrozen(_FROZEN_MESSAGE)
    return ConcurrentRiskEditConflict(
        "The scenario changed since it was read. Re-read it and apply the change again."
    )


def update_risk(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    risk_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> RiskRow | None:
    """Edit one risk, or refuse - `None` when there is no such scenario for this caller.

    Everything that decides whether this write may happen is in **one statement**:

    ```
    UPDATE scenario_risk SET name = :name, updated_at = now()
     WHERE id = :risk_id AND scenario_id = :scenario_id AND updated_at = :expected
       AND scenario_id IN (SELECT id FROM scenarios
                            WHERE id = :scenario_id AND status <> 'approved' FOR UPDATE)
    RETURNING id
    ```
    """
    forbidden = sorted(set(changes) - EDITABLE_RISK_FIELDS)
    if forbidden or not changes:
        raise RiskFieldNotEditable(
            f"A risk edit must name at least one of {sorted(EDITABLE_RISK_FIELDS)} and nothing "
            f"else; got {sorted(changes)}."
        )
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None
    statement = (
        sa.update(_RISK_TABLE)
        .where(
            _RISK_TABLE.c.id == risk_id,
            _RISK_TABLE.c.scenario_id == scenario_id,
            _RISK_TABLE.c.updated_at == expected_updated_at,
            _RISK_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
        )
        .values(**dict(changes), updated_at=sa.func.now())
        .returning(_RISK_TABLE.c.id)
    )
    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            raise _diagnose_row_refusal(session, scenario_id, risk_id)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _row_by_id(session, risk_id)


def delete_risk(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    risk_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
) -> bool | None:
    """Remove one risk, or refuse - `None` when there is no such scenario for this caller.

    Guarded exactly like a write, because it is one: the scenario id, the marker and the lock
    against a concurrent approval are all in the `WHERE`. **A risk that a cost event or a reserve
    still points at is refused by the database** (`fk_additional_cost_risk_same_scenario`,
    `fk_risk_reserve_risk_same_scenario`, no `ON DELETE` action - SQLSTATE `23503`) and named
    `RiskInUse`.
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None
    statement = (
        sa.delete(_RISK_TABLE)
        .where(
            _RISK_TABLE.c.id == risk_id,
            _RISK_TABLE.c.scenario_id == scenario_id,
            _RISK_TABLE.c.updated_at == expected_updated_at,
            _RISK_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
        )
        .returning(_RISK_TABLE.c.id)
    )
    try:
        deleted = session.execute(statement).one_or_none()
        if deleted is None:
            raise _diagnose_row_refusal(session, scenario_id, risk_id)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        if sqlstate_of(error) == "23503":
            raise RiskInUse(_IN_USE_MESSAGE) from None
        raise _failure(error) from None
    return True


def _diagnose_row_refusal(
    session: Session, scenario_id: uuid.UUID, risk_id: uuid.UUID
) -> Exception:
    """Name the reason an edit or a delete matched no row - after the refusal, never as the guard.
    The R-01 order: the target first, then the permanent reason (`approved`), then the transient
    one (the marker)."""
    if not _risk_in_scenario(session, scenario_id, risk_id):
        return RiskNotFound("No such risk in this scenario.")
    if _scenario_is_approved(session, scenario_id):
        return RiskFrozen(_FROZEN_MESSAGE)
    return ConcurrentRiskEditConflict(
        "The risk changed since it was read (concurrency marker). Re-read it and apply the "
        "change again."
    )
