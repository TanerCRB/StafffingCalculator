"""The resolved assumptions of one scenario, as one caller may see them (SC-1-10, gate 1 P-B).

The minimal reader gate 1 asked for: value, state and source of every assumption in
`app.domain.assumptions.RESOLVABLE_ASSUMPTIONS`, for one scenario —

- of a **draft**, resolved against the organisation's **live** defaults (gate 1, Q-1);
- of an **approved** scenario, resolved against the defaults **frozen at its approval** and never
  against the live row (AC-04; criteria K-05, K-06). The scenario level is the scenario's own row
  (frozen by the write guard of ADR-0004) and the project level the live project row (frozen by
  `FROZEN_BY_APPROVED_SCENARIO` and the P-C lock — not by the snapshot, gate 1 P-A).

**Scope comes first and is not decided here.** The scenario is found through
`app.data.staffing.scenario_in_scope`, i.e. through `project_for_caller`: a scenario outside the
caller's scope, of another project, or non-existent is the same `None` (ADR-0005). Because the
project level is read off the project that call returned, a source of `"project"` can only ever name
a project the caller may already see (Story criterion 7).
"""

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.organization_defaults import organization_level_for
from app.data.staffing import scenario_in_scope
from app.domain.assumptions import ResolvedAssumption, resolve_all
from app.models.scenario import Scenario


@dataclass(frozen=True)
class ScenarioAssumptionsView:
    """One scenario and its resolved assumptions, keyed by assumption name."""

    scenario: Scenario
    assumptions: dict[str, ResolvedAssumption]


@dataclass(frozen=True)
class ScenarioResetPreviewView:
    """Values inherited after removing scenario overrides, for a scoped draft only."""

    scenario: Scenario
    assumptions: dict[str, ResolvedAssumption]


@dataclass(frozen=True)
class _ScenarioAssumptionsAfterReset:
    """The resolver's scenario-level shape when both draft overrides are removed."""

    target_margin_percent: None = None
    overload_threshold_percent: None = None


def scenario_assumptions_for_caller(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> ScenarioAssumptionsView | None:
    """Resolve one scenario's assumptions — or `None`, with no way to tell why.

    Reads only. The organisation level is loaded by `organization_level_for`, which picks the
    frozen row for an approved scenario and the live row for a draft; nothing here chooses between
    them a second time.
    """
    scenario = scenario_in_scope(session, caller, project_id, scenario_id)
    if scenario is None:
        return None
    level = organization_level_for(session, [scenario])
    return ScenarioAssumptionsView(
        scenario=scenario,
        assumptions=resolve_all(scenario, scenario.project, level.for_scenario(scenario)),
    )


def scenario_reset_preview_for_caller(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    lock_for_reset: bool = False,
) -> ScenarioResetPreviewView | None:
    """Resolve the two reset targets without consulting the scenario's current overrides.

    Scope is established through the existing ``scenario_in_scope`` boundary. Drafts use live
    organization defaults; the scenario-level input is deliberately ``None`` for both fields.
    """
    scenario = scenario_in_scope(session, caller, project_id, scenario_id)
    if scenario is None:
        return None
    if lock_for_reset:
        # Match approval's scenario -> project lock order (scenario_approval.py). This lets a
        # concurrent project-override edit finish before we authorize the inherited source while
        # avoiding the approval/reset deadlock caused by taking project first.
        session.refresh(scenario, with_for_update=True)
        session.refresh(scenario.project, with_for_update=True)
        # This table lock protects the singleton's absent-row state too: it conflicts with the
        # ROW EXCLUSIVE lock taken by inserts/updates and lasts through the guarded scenario write.
        session.execute(
            sa.text("LOCK TABLE organization_defaults IN SHARE ROW EXCLUSIVE MODE")
        )
        # `scenario_in_scope` also resolved the live organization level; expire those earlier
        # identity-map results so the post-lock query sees the values the locks now protect.
        session.expire_all()
    level = organization_level_for(session, [scenario]).for_scenario(scenario)
    return ScenarioResetPreviewView(
        scenario=scenario,
        assumptions=resolve_all(_ScenarioAssumptionsAfterReset(), scenario.project, level),
    )
