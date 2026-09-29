"""Copying the declared risks of a scenario, and remapping links onto the copy's own risks.

SC-6-08 (F-09 pt 4-5; ADR-0021, point 8, Q-7 = A; ADR-0004, addendum SC-6-08).

A module of its own, importing only models, for a dependency reason: `app.data.staffing`
(`copy_staffing_positions`, which copies the position-level cost events) needs the remap, and
`app.data.risk` needs `app.data.staffing` for its scope - so the remap cannot live in the module
that serves the endpoints without a cycle (the reason `app.data.column_copy` is separate too).

**The mechanism: the risk entry runs first, the remap is by name.** `copy_scenario_risks` is an
entry of `app.data.project_writes.SCENARIO_CHILD_COPIERS` **placed before**
`copy_staffing_positions`: by the time a cost event or a reserve is copied, the copy's risks already
exist. A link is then remapped by the risk's `UNIQUE (scenario_id, name)` - the source risk's name
finds the copy's risk of that name (the ADR-0016 point 5 / ADR-0004 addendum SC-4-05 D-4 = A
pattern), never a shared id-mapping channel through every copier (rejected once, ADR-0016 D-4).

A link is **never copied as it is**: `risk_id` is in `ADDITIONAL_COST_COLUMNS_NOT_COPIED` and
`RESERVE_COLUMNS_NOT_COPIED`, so a copied row cannot keep pointing at the source's risk (which the
composite foreign key would refuse anyway, and which - were it not refused - would let the copy
change the source's representation state).
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.data.column_copy import values_to_copy
from app.models.risk import ScenarioRisk
from app.models.scenario import Scenario

RISK_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "scenario_id", "created_at", "updated_at"}
)
"""Risk attributes a copy does **not** inherit: a new row (AC-02), the copy's own scenario, and its
own ADR-0007 marker. `name` is copied - it is the remap key."""

RESERVE_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "scenario_id", "risk_id", "created_at", "updated_at"}
)
"""Reserve attributes a copy does not inherit; `risk_id` is remapped, never copied."""


def copy_scenario_risks(session: Session, source: Scenario, copy: Scenario) -> None:
    """Copy the source scenario's declared risks onto the copy, with new identifiers.

    The entry in `SCENARIO_CHILD_COPIERS`, **ordered before `copy_staffing_positions`** (Q-7 = A).
    Nothing here is guarded against `approved`: every copy is a `draft`, and the source is only
    read.
    """
    risks = list(
        session.execute(
            sa.select(ScenarioRisk)
            .where(ScenarioRisk.scenario_id == source.id)
            .order_by(ScenarioRisk.name, ScenarioRisk.id)
        )
        .scalars()
        .all()
    )
    for risk in risks:
        session.add(
            ScenarioRisk(
                id=uuid.uuid4(),
                scenario_id=copy.id,
                **values_to_copy(risk, excluded=RISK_COLUMNS_NOT_COPIED),
            )
        )
    session.flush()


def copied_risk_ids(
    session: Session, source_scenario_id: uuid.UUID, copy_scenario_id: uuid.UUID
) -> dict[uuid.UUID, uuid.UUID]:
    """`{source risk id: the copy's own risk id}`, joined on `UNIQUE (scenario_id, name)`.

    Empty when the source has no risk. A source risk with no copy of that name is simply absent
    from the mapping - which the callers treat as an error (`remapped`), never as "no link".
    """
    source_risk = aliased(ScenarioRisk)
    copied_risk = aliased(ScenarioRisk)
    rows = session.execute(
        sa.select(source_risk.id, copied_risk.id)
        .join(
            copied_risk,
            sa.and_(
                copied_risk.name == source_risk.name,
                copied_risk.scenario_id == copy_scenario_id,
            ),
        )
        .where(source_risk.scenario_id == source_scenario_id)
    ).all()
    return {source_id: copied_id for source_id, copied_id in rows}


def remapped(mapping: dict[uuid.UUID, uuid.UUID], risk_id: uuid.UUID | None) -> uuid.UUID | None:
    """The copy's risk id for a source link - `None` stays `None`; a link whose risk has no copy is
    a broken cascade (the risk copier did not run first) and fails loudly rather than dropping the
    link and silently changing what the copy says."""
    if risk_id is None:
        return None
    try:
        return mapping[risk_id]
    except KeyError:
        raise RuntimeError(
            "A copied row links to a risk that has no copy: the risk copier must run before "
            "every copier of a row that carries a risk link."
        ) from None
