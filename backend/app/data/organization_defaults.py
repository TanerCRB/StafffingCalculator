"""Reading the organisation level of the assumption chain — live or frozen (SC-1-10, ADR-0012).

The one place that decides **which** organisation defaults a scenario resolves against:

- a **draft** reads the live `organization_defaults` row (gate 1, Q-1: "saved" in F-02 means
  *approved*; a draft follows the organisation's current standard, and that cost was accepted);
- an **approved** scenario reads the row its approval froze into
  `approved_snapshot_organization_defaults` — **and never the live table**, not even when no row was
  frozen (AC-04; criteria K-05 and K-06). A missing frozen row means "the organisation had no
  defaults when this was approved", and filling it in from the live table would move an approved
  calculation the day somebody configures a default.

This is the first code in the repository that reads an approval snapshot back (gate 1, P-B). It
reads only the one snapshot table SC-1-10 owns; the calendar, day, absence-type and budget snapshots
still have no reader (their first reader is the reproducible report of plan block 8).

**No scope decision here.** Both tables belong to no project; the scenarios whose level is loaded
were already narrowed to the caller by `app.data.project_reads`, which is the only caller of
`organization_level_for` that passes scenarios in. This module must not grow a `select(Scenario)`.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domain.assumptions import OrganizationDefaultValues
from app.models.approved_snapshot import ApprovedSnapshotOrganizationDefaults
from app.models.organization_defaults import OrganizationDefaults
from app.models.scenario import Scenario, ScenarioStatus


class OrganizationDefaultsConcurrentEditConflict(RuntimeError):
    """The singleton is no longer in the state named by the caller's marker."""


def update_organization_defaults(
    session: Session,
    *,
    expected_updated_at: datetime | None,
    changes: Mapping[str, Decimal | None],
) -> OrganizationDefaults:
    """Atomically write the singleton only if its current marker matches the request.

    `None` names the absent-row state. PostgreSQL `ON CONFLICT DO NOTHING` makes the first
    write create-if-absent under concurrent requests; a competing insert is a stale conflict.
    Existing-row writes compare `updated_at` in the `UPDATE` predicate, never in Python.
    """
    if expected_updated_at is None:
        values: dict[str, object] = {
            "id": 1,
            "target_margin_percent": None,
            "overload_threshold_percent": None,
            **changes,
        }
        statement = (
            pg_insert(OrganizationDefaults)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[OrganizationDefaults.id])
            .returning(OrganizationDefaults)
        )
    else:
        statement = (
            sa.update(OrganizationDefaults)
            .where(
                OrganizationDefaults.id == 1,
                OrganizationDefaults.updated_at == expected_updated_at,
            )
            .values(**changes, updated_at=sa.func.clock_timestamp())
            .returning(OrganizationDefaults)
        )

    row = session.execute(statement).scalars().one_or_none()
    if row is None:
        raise OrganizationDefaultsConcurrentEditConflict(
            "Writing the organization defaults failed: condition=updated_at_marker. "
            "The row changed since it was read (concurrency marker). Re-read it and edit again."
        )
    session.commit()
    return row


@dataclass(frozen=True)
class OrganizationLevel:
    """The organisation level for a set of scenarios: the live row, and the rows frozen for them.

    Loaded once per read (two statements, whatever the number of scenarios) and carried on
    `app.data.project_reads.CallerProjectView`, because the layer that shapes readiness never
    receives a `Session` and must never grow a query of its own.
    """

    live: OrganizationDefaultValues | None
    frozen: Mapping[uuid.UUID, OrganizationDefaultValues] = field(default_factory=dict)
    """Keyed by scenario id; an approved scenario with **no** key had no row frozen (K-06)."""

    def for_scenario(self, scenario: Scenario) -> OrganizationDefaultValues | None:
        """The organisation level `scenario` resolves against — frozen if approved, else live.

        For an approved scenario the answer comes from `frozen` **only**; a missing key is `None`
        ("nothing was frozen"), never a fall-back to `live`. The one situation in which that `None`
        is not the frozen truth is a scenario approved *after* this level was loaded in the same
        request — no path in this repository shapes a scenario it has just approved, and the
        consequence would be a "no value" in a readiness list, never a live value in an approved
        calculation.
        """
        if scenario.status == ScenarioStatus.APPROVED:
            return self.frozen.get(scenario.id)
        return self.live


def _values(row: OrganizationDefaults | ApprovedSnapshotOrganizationDefaults) -> (
    OrganizationDefaultValues
):
    return OrganizationDefaultValues(
        target_margin_percent=row.target_margin_percent,
        overload_threshold_percent=row.overload_threshold_percent,
    )


def live_organization_defaults(session: Session) -> OrganizationDefaultValues | None:
    """The live defaults row as values, or `None` when the organisation has none (gate 1, P-D)."""
    row = session.execute(sa.select(OrganizationDefaults)).scalars().one_or_none()
    return None if row is None else _values(row)


def frozen_organization_defaults(
    session: Session, scenario_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, OrganizationDefaultValues]:
    """The rows frozen at approval for these scenarios — only those that have one."""
    ids = list(scenario_ids)
    if not ids:
        return {}
    rows = session.execute(
        sa.select(ApprovedSnapshotOrganizationDefaults).where(
            ApprovedSnapshotOrganizationDefaults.scenario_id.in_(ids)
        )
    ).scalars()
    return {row.scenario_id: _values(row) for row in rows}


def organization_level_for(session: Session, scenarios: Iterable[Scenario]) -> OrganizationLevel:
    """Load the organisation level for scenarios the caller has already been granted.

    The frozen rows are fetched only for the scenarios that are approved *in the rows handed in*;
    a draft never consults the snapshot table, so a stray snapshot row under a draft (which no path
    writes) could not change a draft's answer either.
    """
    approved = [scenario.id for scenario in scenarios if scenario.status == ScenarioStatus.APPROVED]
    return OrganizationLevel(
        live=live_organization_defaults(session),
        frozen=frozen_organization_defaults(session, approved),
    )
