"""SC-8-01 (Issue #14, F-12 block 8) — the change history one approval writes (ADR-0004, addendum
2026-09-27).

Four criteria:

- **K-01** approving a scenario leaves **exactly one** `audit_log` row for it, timestamped inside
  the test's own window; a second approval of the same, now-`approved` scenario (already proven
  `409` elsewhere — `test_k_19_...` in `test_scenario_approval.py`) adds **zero** new rows.
- **K-02** the row names the scenario it is *about*, not another one: two independent approvals in
  one test, each naming its own scenario and project.
- **K-03** `performed_by` carries the identity the *request's own context* carried — today
  `CallerIdentity.user_id`, resolved per request, never a constant — proven by substituting two
  different identities for two separate approvals (the `dependency_overrides` pattern
  `test_staffing_approved_guards.py` already uses for the same purpose, SC-1-08 K-06).
- **The mandatory canary** (ADR-0004, addendum 2026-09-27 SC-8-01, point 4) — duplicating a scenario
  must never duplicate its history: a copy is a new, unapproved draft, and a history row pointing at
  it would say the copy itself had been approved.

**What none of these prove, named here so a reader does not take them for more** (module docstring
of `app.models.audit_log`): a real, authenticated, multi-subject identity — `performed_by` carries
today's placeholder string, and the closing condition is the authentication ADR; history for any
action besides approval (duplication, archiving, editing, copying stay unaudited); reading the
history back (no endpoint exists); access control on the history resource itself (F-13, out of
scope).
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models import AuditActionType, AuditLog, Project, Scenario
from tests.conftest import (
    approve_path,
    as_caller,
    caller_holding,
    count_audit_log_rows,
    make_project,
    make_scenario,
)

_TIME_WINDOW_SLACK = timedelta(seconds=30)
"""Generous on purpose: this asserts the row's timestamp is the database's own clock at
approval time, not that the clocks of the test process and the database container agree to the
second. A mutation writing a constant or a far-off timestamp still fails it; ordinary clock skew
between a test host and a container does not."""


def _draft_scenario(
    session: Session, *, name: str, accessible_to: tuple[str, ...]
) -> tuple[Project, Scenario]:
    """A project in scope for `accessible_to`, with one empty draft scenario — nothing this task's
    criteria need depends on staffing content, only on the scenario being approvable."""
    project = make_project(session, name=name, accessible_to=accessible_to)
    scenario = make_scenario(session, project, name="Baseline")
    return project, scenario


def _audit_row_for(session: Session, scenario_id: uuid.UUID) -> AuditLog:
    session.expire_all()
    return session.execute(
        select(AuditLog).where(AuditLog.scenario_id == scenario_id)
    ).scalar_one()


# --- K-01: exactly one history row, timestamped in the test's window, none new on a refusal ------


def test_k_01_approving_a_scenario_leaves_exactly_one_audit_log_row(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — one approval, one row, timestamped now; a second (refused) approval adds none."""
    project, scenario = _draft_scenario(
        db_session, name="Aurora migration", accessible_to=("pm-anna",)
    )
    before = datetime.now(UTC) - _TIME_WINDOW_SLACK

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller("pm-anna"))
    after = datetime.now(UTC) + _TIME_WINDOW_SLACK

    assert response.status_code == 200, response.text
    assert count_audit_log_rows(db_session, scenario.id) == 1, (
        "approving a scenario must leave exactly one audit_log row for it — the mutation this "
        "kills is the write dropped from the approval path entirely"
    )
    row = _audit_row_for(db_session, scenario.id)
    assert row.action_type == AuditActionType.SCENARIO_APPROVED
    assert row.project_id == project.id
    assert before <= row.created_at <= after, (
        f"audit_log.created_at ({row.created_at}) is outside the test's own window "
        f"[{before}, {after}]"
    )

    # The contrast: a second approval of the now-approved scenario is refused (proven `409` by
    # `test_k_19_...` in test_scenario_approval.py) and must add zero new history rows.
    second = client.post(approve_path(project.id, scenario.id), headers=as_caller("pm-anna"))
    assert second.status_code == 409, second.text
    assert count_audit_log_rows(db_session, scenario.id) == 1, (
        "a refused, already-approved second approval added a new audit_log row — the refusal must "
        "leave the history exactly where the first approval left it"
    )


# --- K-02: a row names the scenario it is about, never another one --------------------------------


def test_k_02_each_approvals_row_names_its_own_scenario_not_the_other(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — two independent approvals in one test; each history row names its own scenario and
    project. The mutation this kills is substituting a constant or the first-seen id: with two
    scenarios approved in the same test, that mutation makes both rows agree on one id, and this
    assertion tells the two apart."""
    project_a, scenario_a = _draft_scenario(
        db_session, name="Aurora migration", accessible_to=("pm-anna",)
    )
    project_b, scenario_b = _draft_scenario(
        db_session, name="Borealis rollout", accessible_to=("pm-anna",)
    )

    ok_a = client.post(approve_path(project_a.id, scenario_a.id), headers=as_caller("pm-anna"))
    ok_b = client.post(approve_path(project_b.id, scenario_b.id), headers=as_caller("pm-anna"))
    assert ok_a.status_code == 200, ok_a.text
    assert ok_b.status_code == 200, ok_b.text

    row_a = _audit_row_for(db_session, scenario_a.id)
    row_b = _audit_row_for(db_session, scenario_b.id)

    assert row_a.scenario_id == scenario_a.id
    assert row_a.project_id == project_a.id
    assert row_b.scenario_id == scenario_b.id
    assert row_b.project_id == project_b.id
    # The contrast that actually distinguishes "named correctly" from "named identically": neither
    # row may be reachable through the other scenario's id.
    assert row_a.scenario_id != row_b.scenario_id
    assert count_audit_log_rows(db_session, scenario_a.id) == 1
    assert count_audit_log_rows(db_session, scenario_b.id) == 1


# --- K-03: performed_by carries the request's own identity, not a constant -----------------------


def test_k_03_performed_by_carries_the_identity_the_request_context_carried(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — two separate approvals, two separate substituted identities
    (`dependency_overrides`, the same pattern `test_staffing_approved_guards.py` uses for SC-1-08
    K-06), and the audit row's `performed_by` must follow the substitution rather than stay fixed.

    **Named, permanent limit (ADR-0004, addendum 2026-09-27 SC-8-01):** this proves the field is
    wired to the request's own context, not that it carries a real, authenticated identity —
    that waits on the authentication ADR, which does not exist yet.
    """
    project_a, scenario_a = _draft_scenario(
        db_session, name="Aurora migration", accessible_to=("alice",)
    )
    project_b, scenario_b = _draft_scenario(
        db_session, name="Borealis rollout", accessible_to=("bob",)
    )

    with caller_holding(Permission.PROJECT_EDIT, user_id="alice"):
        ok_a = client.post(approve_path(project_a.id, scenario_a.id), headers=as_caller("alice"))
    with caller_holding(Permission.PROJECT_EDIT, user_id="bob"):
        ok_b = client.post(approve_path(project_b.id, scenario_b.id), headers=as_caller("bob"))

    assert ok_a.status_code == 200, ok_a.text
    assert ok_b.status_code == 200, ok_b.text

    row_a = _audit_row_for(db_session, scenario_a.id)
    row_b = _audit_row_for(db_session, scenario_b.id)

    assert row_a.performed_by == "alice", (
        f"expected 'alice', got {row_a.performed_by!r} — performed_by did not follow the "
        "substituted request identity"
    )
    assert row_b.performed_by == "bob", (
        f"expected 'bob', got {row_b.performed_by!r} — performed_by did not follow the "
        "substituted request identity"
    )
    # The contrast that kills "constant instead of context": the two rows must disagree, because
    # the two requests carried different identities.
    assert row_a.performed_by != row_b.performed_by


# --- The mandatory canary: a duplicate carries no history row of its own --------------------------


def test_a_duplicated_scenario_carries_zero_audit_log_rows_of_its_own(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0004, addendum 2026-09-27 SC-8-01, point 4 — `audit_log` is absent from
    `SCENARIO_CHILD_COPIERS` by requirement, and this is the canary that requirement needs: the
    registry is silent about omissions, so a future entry copying history rows onto a duplicate
    would pass every other test in this file and only fail here.

    The contrast is the source scenario's own row, checked untouched before and after the
    duplicate: this is not satisfied by a database that fails to write history at all.
    """
    project, scenario = _draft_scenario(
        db_session, name="Aurora migration", accessible_to=("pm-anna",)
    )
    approved = client.post(approve_path(project.id, scenario.id), headers=as_caller("pm-anna"))
    assert approved.status_code == 200, approved.text
    assert count_audit_log_rows(db_session, scenario.id) == 1

    duplicate = client.post(
        f"/projects/{project.id}/scenarios/{scenario.id}/duplicate",
        headers=as_caller("pm-anna"),
    )
    assert duplicate.status_code == 201, duplicate.text
    copy_id = uuid.UUID(duplicate.json()["id"])

    assert count_audit_log_rows(db_session, copy_id) == 0, (
        "a duplicated scenario carries an audit_log row naming itself — the copy has not been "
        "approved, and a history row saying so would misstate its own approval history"
    )
    assert count_audit_log_rows(db_session, scenario.id) == 1, (
        "duplicating the scenario changed the source's own history — it must be left exactly as "
        "the approval left it"
    )
