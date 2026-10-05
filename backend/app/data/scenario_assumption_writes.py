"""Persist scenario-level assumption overrides (SC-1-23; ADR-0004/0007/0012/0022).

Scope is resolved only through ``scenario_in_scope``. The update itself compares the opaque
``updated_at`` marker and embeds the shared ``unapproved_scenario`` locking predicate, so a write
and an approval serialize on the scenario row instead of relying on a Python state check.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.project_reads import accessible_projects
from app.data.scenario_guard import unapproved_scenario
from app.data.staffing import scenario_in_scope
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.models.project import Project
from app.models.scenario import Scenario, ScenarioStatus

ASSUMPTION_OVERRIDE_FIELDS = frozenset(
    {"target_margin_percent", "overload_threshold_percent"}
)


class ScenarioAssumptionWriteFailed(WriteFailed):
    """An unclassified database failure during a scenario-assumption write."""


class ScenarioAssumptionWriteRefused(ScenarioAssumptionWriteFailed, WriteRefused):
    """A classified database refusal, such as the positive-threshold constraint."""


class ScenarioAssumptionWriteRejected(RuntimeError):
    """A state refusal diagnosed after the guarded update matched no row."""


class ApprovedScenarioAssumptionsFrozen(ScenarioAssumptionWriteRejected):
    """Approved scenario overrides are immutable (ADR-0004)."""


class ConcurrentScenarioAssumptionEditConflict(ScenarioAssumptionWriteRejected):
    """The supplied scenario marker is stale (ADR-0007)."""


class ScenarioAssumptionFieldNotEditable(RuntimeError):
    """The caller named a non-override field or supplied an empty edit."""


@dataclass(frozen=True)
class ScenarioAssumptionOverrideView:
    id: uuid.UUID
    status: ScenarioStatus
    target_margin_percent: Decimal | None
    overload_threshold_percent: Decimal | None
    updated_at: datetime


def update_scenario_assumption_overrides(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> ScenarioAssumptionOverrideView | None:
    """Apply only named override fields and return the values accepted by PostgreSQL.

    ``None`` means the scenario is absent from the caller's assigned-project scope, preserving the
    existing 404 behavior. The approved-state guard and marker check are evaluated by the same
    statement as the update; refusal diagnosis runs afterward and gives approved immutability
    precedence over stale-token conflict.
    """
    forbidden = sorted(set(changes) - ASSUMPTION_OVERRIDE_FIELDS)
    if forbidden or not changes:
        raise ScenarioAssumptionFieldNotEditable(
            "An edit must name only scenario assumption overrides."
        )
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    table = Scenario.__table__
    # Scope can be revoked after the aggregate read above. Reuse the shared scope predicate in
    # the write statement so a revocation committed before this UPDATE cannot authorize a write.
    caller_still_has_scope = sa.exists(
        accessible_projects(caller).where(Project.id == project_id)
    )

    statement = (
        sa.update(table)
        .where(
            table.c.id == scenario_id,
            table.c.project_id == project_id,
            caller_still_has_scope,
            table.c.updated_at == expected_updated_at,
            table.c.id.in_(unapproved_scenario(scenario_id)),
        )
        .values(**dict(changes), updated_at=sa.func.now())
        .returning(
            table.c.id,
            table.c.status,
            table.c.target_margin_percent,
            table.c.overload_threshold_percent,
            table.c.updated_at,
        )
    )
    try:
        row = session.execute(statement).one_or_none()
        if row is None:
            if not session.scalar(
                sa.select(
                    sa.exists(
                        accessible_projects(caller).where(Project.id == project_id)
                    )
                )
            ):
                return None
            current = session.execute(
                sa.select(table.c.status, table.c.updated_at).where(
                    table.c.id == scenario_id, table.c.project_id == project_id
                )
            ).one_or_none()
            if current is None:
                return None
            if current.status == ScenarioStatus.APPROVED:
                raise ApprovedScenarioAssumptionsFrozen(
                    "Approved scenario assumptions cannot be changed; duplicate the scenario."
                )
            raise ConcurrentScenarioAssumptionEditConflict(
                "The scenario changed since it was read. Re-read it and apply the edit again."
            )
        session.commit()
        # Scope resolution may have loaded this Scenario into the identity map before the Core
        # UPDATE. Expire that copy so a subsequent read using the same Session observes the saved
        # values, just as the next request's fresh Session does.
        session.expire_all()
    except SQLAlchemyError as error:
        session.rollback()
        raise failure_for(
            error,
            subject="scenario assumptions",
            refused=ScenarioAssumptionWriteRefused,
            failed=ScenarioAssumptionWriteFailed,
        ) from None

    return ScenarioAssumptionOverrideView(
        id=row.id,
        status=row.status,
        target_margin_percent=row.target_margin_percent,
        overload_threshold_percent=row.overload_threshold_percent,
        updated_at=row.updated_at,
    )
