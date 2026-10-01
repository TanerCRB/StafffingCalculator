"""Scoped reads and writes for manual exchange-rate windows."""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.project_reads import project_for_caller
from app.data.scenario_guard import unapproved_scenario
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.domain.exchange_rates import EffectiveExchangeRate
from app.models.approved_snapshot import ApprovedSnapshotExchangeRate
from app.models.exchange_rate import ExchangeRate
from app.models.scenario import Scenario, ScenarioStatus


class ExchangeRateWriteFailed(WriteFailed):
    """The exchange-rate statement failed unexpectedly."""


class ExchangeRateWriteRefused(ExchangeRateWriteFailed, WriteRefused):
    """The database rejected a rate window or overlapping effective period."""


def _failure(error: SQLAlchemyError) -> WriteFailed:
    return failure_for(
        error,
        subject="exchange rate",
        refused=ExchangeRateWriteRefused,
        failed=ExchangeRateWriteFailed,
    )


def list_exchange_rates(
    session: Session,
    caller: CallerIdentity,
    *,
    project_id: uuid.UUID | None = None,
    scenario_id: uuid.UUID | None = None,
) -> list[ExchangeRate] | None:
    """Read organization rates, or rates in a caller-visible project and scenario."""
    if scenario_id is not None and project_id is None:
        return None
    if project_id is not None:
        if project_for_caller(session, caller, project_id) is None:
            return None
        if (
            scenario_id is not None
            and session.scalar(
                sa.select(Scenario.id).where(
                    Scenario.id == scenario_id, Scenario.project_id == project_id
                )
            )
            is None
        ):
            return None
    statement = sa.select(ExchangeRate).order_by(
        ExchangeRate.source_currency,
        ExchangeRate.target_currency,
        ExchangeRate.effective_from,
    )
    if project_id is None:
        statement = statement.where(
            ExchangeRate.project_id.is_(None), ExchangeRate.scenario_id.is_(None)
        )
    else:
        statement = statement.where(
            sa.or_(ExchangeRate.project_id.is_(None), ExchangeRate.project_id == project_id),
            sa.or_(ExchangeRate.scenario_id.is_(None), ExchangeRate.scenario_id == scenario_id),
        )
        if scenario_id is None:
            statement = statement.where(ExchangeRate.scenario_id.is_(None))
    return list(session.scalars(statement).all())


def create_exchange_rate(
    session: Session,
    caller: CallerIdentity,
    *,
    source_currency: str,
    target_currency: str,
    effective_from: date,
    effective_to: date | None,
    rate: Decimal,
    source: str,
    project_id: uuid.UUID | None = None,
    scenario_id: uuid.UUID | None = None,
) -> ExchangeRate | None:
    """Create a rate after applying the shared project-scope check to any override."""
    if scenario_id is not None and project_id is None:
        return None
    if project_id is not None and project_for_caller(session, caller, project_id) is None:
        return None

    table = ExchangeRate.__table__
    row_id = uuid.uuid4()
    values = sa.select(
        sa.literal(row_id),
        sa.literal(project_id),
        sa.literal(scenario_id),
        sa.literal(source_currency),
        sa.literal(target_currency),
        sa.literal(effective_from),
        sa.literal(effective_to),
        sa.literal(rate),
        sa.literal(source),
    )
    if scenario_id is not None:
        values = values.where(
            Scenario.id.in_(unapproved_scenario(scenario_id)),
            Scenario.project_id == project_id,
            Scenario.status == ScenarioStatus.DRAFT,
        )
    statement = (
        sa.insert(table)
        .from_select(
            [
                "id",
                "project_id",
                "scenario_id",
                "source_currency",
                "target_currency",
                "effective_from",
                "effective_to",
                "rate",
                "source",
            ],
            values,
        )
        .returning(table.c.id)
    )
    try:
        inserted_id = session.scalar(statement)
        if inserted_id is None:
            if (
                scenario_id is not None
                and session.scalar(
                    sa.select(Scenario.id).where(
                        Scenario.id == scenario_id, Scenario.project_id == project_id
                    )
                )
                is not None
            ):
                raise ExchangeRateWriteRefused("The scenario is no longer editable.")
            return None
        session.commit()
        return session.get(ExchangeRate, inserted_id)
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None


def rates_for_scenario(session: Session, scenario: Scenario) -> tuple[EffectiveExchangeRate, ...]:
    """Resolve the candidate source rows from live defaults or the scenario's frozen snapshot."""
    if scenario.status == ScenarioStatus.APPROVED:
        rows = session.scalars(
            sa.select(ApprovedSnapshotExchangeRate).where(
                ApprovedSnapshotExchangeRate.scenario_id == scenario.id
            )
        ).all()
        return tuple(
            EffectiveExchangeRate(
                source_currency=row.source_currency,
                target_currency=row.target_currency,
                effective_from=row.effective_from,
                effective_to=row.effective_to,
                value=row.rate,
                source=row.source,
                scope=row.source_scope,
            )
            for row in rows
        )
    rows = session.scalars(
        sa.select(ExchangeRate).where(
            sa.or_(
                ExchangeRate.project_id.is_(None) & ExchangeRate.scenario_id.is_(None),
                (ExchangeRate.project_id == scenario.project_id)
                & ExchangeRate.scenario_id.is_(None),
                ExchangeRate.scenario_id == scenario.id,
            )
        )
    ).all()
    return tuple(
        EffectiveExchangeRate(
            source_currency=row.source_currency,
            target_currency=row.target_currency,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            value=row.rate,
            source=row.source,
            scope=(
                "scenario" if row.scenario_id else "project" if row.project_id else "organization"
            ),
        )
        for row in rows
    )
