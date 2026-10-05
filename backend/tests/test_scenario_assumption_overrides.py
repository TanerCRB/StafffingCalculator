"""SC-1-23 K1-K6: scoped, independent, draft-only scenario override persistence."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity, Permission
from app.data import scenario_assumption_writes
from app.data.scenario_approval import approve_scenario
from app.models import (
    ApprovedSnapshotOrganizationDefaults,
    Scenario,
    ScenarioStatus,
)
from app.models.project_access import ProjectAccess
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    assumptions_path,
    caller_holding,
    make_project,
    make_scenario,
    set_organization_defaults,
    set_project_overrides,
)

READ = Permission.SCENARIO_ASSUMPTIONS_READ
WRITE = Permission.SCENARIO_ASSUMPTIONS_WRITE
OVERRIDE_PERMISSIONS = (READ, WRITE)


def _path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return assumptions_path(project_id, scenario_id)


def _token(session: Session, scenario_id: uuid.UUID):
    session.expire_all()
    return session.scalar(sa.select(Scenario.updated_at).where(Scenario.id == scenario_id))


def _stored(session: Session, scenario_id: uuid.UUID) -> tuple[Decimal | None, Decimal | None]:
    session.expire_all()
    row = session.execute(
        sa.select(Scenario.target_margin_percent, Scenario.overload_threshold_percent).where(
            Scenario.id == scenario_id
        )
    ).one()
    return row.target_margin_percent, row.overload_threshold_percent


def _patch(
    client: TestClient,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    token,
    **changes: object,
):
    return client.patch(
        _path(project_id, scenario_id),
        json={"updated_at": token.isoformat(), **changes},
        headers=as_caller(IN_SCOPE_USER),
    )


def test_k_01_scenario_override_write_requires_both_grants_and_project_scope(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K1 scope", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(
        db_session,
        project,
        name="Draft",
        target_margin_percent=Decimal("12.000"),
        overload_threshold_percent=Decimal("80.000"),
    )
    token = _token(db_session, scenario.id)

    for permissions in ((READ,), (WRITE,)):
        with caller_holding(*permissions):
            response = _patch(
                client, project.id, scenario.id, token, target_margin_percent="25.000"
            )
        assert response.status_code == 403

    with caller_holding(*OVERRIDE_PERMISSIONS, user_id=OUT_OF_SCOPE_USER):
        response = client.patch(
            _path(project.id, scenario.id),
            json={"updated_at": token.isoformat(), "target_margin_percent": "25.000"},
            headers=as_caller(OUT_OF_SCOPE_USER),
        )
    assert response.status_code == 404
    assert _stored(db_session, scenario.id) == (Decimal("12.000"), Decimal("80.000"))

    with caller_holding(*OVERRIDE_PERMISSIONS):
        response = _patch(
            client, project.id, scenario.id, token, target_margin_percent="25.000"
        )
    assert response.status_code == 200, response.text
    assert _stored(db_session, scenario.id) == (Decimal("25.000"), Decimal("80.000"))


def test_k_01_project_scope_revoked_after_initial_check_does_not_authorize_write(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(db_session, name="K1 revocation race", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(
        db_session,
        project,
        name="Draft",
        target_margin_percent=Decimal("12.000"),
        overload_threshold_percent=Decimal("80.000"),
    )
    token = _token(db_session, scenario.id)
    original_scope_check = scenario_assumption_writes.scenario_in_scope

    def revoke_after_scope_check(session, caller, project_id, scenario_id):
        scoped_scenario = original_scope_check(session, caller, project_id, scenario_id)
        session.execute(
            sa.delete(ProjectAccess).where(
                ProjectAccess.user_id == caller.user_id,
                ProjectAccess.project_id == project_id,
            )
        )
        session.commit()
        return scoped_scenario

    monkeypatch.setattr(scenario_assumption_writes, "scenario_in_scope", revoke_after_scope_check)
    with caller_holding(*OVERRIDE_PERMISSIONS):
        response = _patch(
            client, project.id, scenario.id, token, target_margin_percent="25.000"
        )

    assert response.status_code == 404
    assert _stored(db_session, scenario.id) == (Decimal("12.000"), Decimal("80.000"))


def test_k_02_each_override_persists_independently_without_changing_siblings(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K2 independent", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Primary")
    sibling = make_scenario(
        db_session,
        project,
        name="Sibling",
        target_margin_percent=Decimal("7.000"),
        overload_threshold_percent=Decimal("90.000"),
    )

    with caller_holding(*OVERRIDE_PERMISSIONS):
        first = _patch(
            client,
            project.id,
            scenario.id,
            _token(db_session, scenario.id),
            target_margin_percent="18.375",
        )
        assert first.status_code == 200, first.text
        assert first.json()["target_margin_percent"] == "18.375"
        second = _patch(
            client,
            project.id,
            scenario.id,
            _token(db_session, scenario.id),
            overload_threshold_percent="115.000",
        )
    assert second.status_code == 200, second.text
    assert second.json()["target_margin_percent"] == "18.375"
    assert second.json()["overload_threshold_percent"] == "115.000"
    assert second.json()["updated_at"]
    assert _stored(db_session, scenario.id) == (Decimal("18.375"), Decimal("115.000"))
    assert _stored(db_session, sibling.id) == (Decimal("7.000"), Decimal("90.000"))


def test_k_03_clearing_one_override_inherits_each_level_and_zero_remains_a_value(
    client: TestClient, db_session: Session
) -> None:
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("21.000"),
        overload_threshold_percent=Decimal("125.000"),
    )
    project = make_project(db_session, name="K3 inheritance", accessible_to=(IN_SCOPE_USER,))
    set_project_overrides(
        db_session,
        project.id,
        target_margin_percent=Decimal("15.500"),
        overload_threshold_percent=Decimal("105.000"),
    )
    scenario = make_scenario(
        db_session,
        project,
        name="Draft",
        target_margin_percent=Decimal("0.000"),
        overload_threshold_percent=Decimal("140.000"),
    )

    with caller_holding(*OVERRIDE_PERMISSIONS, Permission.PROJECT_READ):
        zero_read = client.get(
            assumptions_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        assert zero_read.json()["target_margin_percent"]["value"] == "0.000"
        cleared_margin = _patch(
            client,
            project.id,
            scenario.id,
            _token(db_session, scenario.id),
            target_margin_percent=None,
        )
        assert cleared_margin.status_code == 200, cleared_margin.text
        assert cleared_margin.json()["overload_threshold_percent"] == "140.000"
        margin_read = client.get(
            assumptions_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        assert margin_read.json()["target_margin_percent"] == {
            "value": "15.500",
            "state": "resolved",
            "source": "project",
        }
        set_project_overrides(
            db_session,
            project.id,
            target_margin_percent=None,
            overload_threshold_percent=Decimal("105.000"),
        )
        organization_margin = client.get(
            assumptions_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        ).json()
        assert organization_margin["target_margin_percent"] == {
            "value": "21.000",
            "state": "resolved",
            "source": "organization",
        }
        cleared_threshold = _patch(
            client,
            project.id,
            scenario.id,
            _token(db_session, scenario.id),
            overload_threshold_percent=None,
        )
        assert cleared_threshold.status_code == 200, cleared_threshold.text
        project_threshold = client.get(
            assumptions_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        ).json()
        assert project_threshold["overload_threshold_percent"] == {
            "value": "105.000",
            "state": "resolved",
            "source": "project",
        }
        set_project_overrides(
            db_session,
            project.id,
            target_margin_percent=None,
            overload_threshold_percent=None,
        )
        inherited = client.get(
            assumptions_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        ).json()
    assert inherited["overload_threshold_percent"] == {
        "value": "125.000",
        "state": "resolved",
        "source": "organization",
    }
    assert _stored(db_session, scenario.id) == (None, None)


def test_k_04_approved_scenario_refuses_override_write_and_draft_contrast_succeeds(
    client: TestClient, db_session: Session
) -> None:
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("20.000"),
        overload_threshold_percent=Decimal("100.000"),
    )
    project = make_project(db_session, name="K4 approved", accessible_to=(IN_SCOPE_USER,))
    approved = make_scenario(
        db_session,
        project,
        name="Approved",
        target_margin_percent=Decimal("11.000"),
        overload_threshold_percent=Decimal("85.000"),
    )
    approver = CallerIdentity(
        user_id=IN_SCOPE_USER, permissions=frozenset({Permission.PROJECT_EDIT})
    )
    approve_scenario(db_session, approver, project.id, approved.id)
    snapshot_before = db_session.execute(
        sa.select(
            ApprovedSnapshotOrganizationDefaults.target_margin_percent,
            ApprovedSnapshotOrganizationDefaults.overload_threshold_percent,
        ).where(ApprovedSnapshotOrganizationDefaults.scenario_id == approved.id)
    ).one()
    approved_values = _stored(db_session, approved.id)

    draft = make_scenario(db_session, project, name="Draft")
    with caller_holding(*OVERRIDE_PERMISSIONS):
        refused = _patch(
            client,
            project.id,
            approved.id,
            _token(db_session, approved.id),
            target_margin_percent="30.000",
        )
        refused_clear = _patch(
            client,
            project.id,
            approved.id,
            _token(db_session, approved.id),
            overload_threshold_percent=None,
        )
        accepted = _patch(
            client,
            project.id,
            draft.id,
            _token(db_session, draft.id),
            target_margin_percent="30.000",
        )
    assert refused.status_code == 409
    assert "Approved scenario" in refused.json()["detail"]
    assert refused_clear.status_code == 409
    assert "Approved scenario" in refused_clear.json()["detail"]
    assert accepted.status_code == 200, accepted.text
    assert _stored(db_session, approved.id) == approved_values
    snapshot_after = db_session.execute(
        sa.select(
            ApprovedSnapshotOrganizationDefaults.target_margin_percent,
            ApprovedSnapshotOrganizationDefaults.overload_threshold_percent,
        ).where(ApprovedSnapshotOrganizationDefaults.scenario_id == approved.id)
    ).one()
    assert snapshot_after == snapshot_before
    assert _stored(db_session, draft.id) == (Decimal("30.000"), None)


def test_k_05_invalid_override_requests_and_database_threshold_guard_leave_state_unchanged(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K5 validation", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(
        db_session,
        project,
        name="Draft",
        target_margin_percent=Decimal("5.000"),
        overload_threshold_percent=Decimal("75.000"),
    )
    token = _token(db_session, scenario.id)
    with caller_holding(*OVERRIDE_PERMISSIONS):
        unbounded_margin = _patch(
            client, project.id, scenario.id, token, target_margin_percent="150.000"
        )
        assert unbounded_margin.status_code == 200, unbounded_margin.text
        assert unbounded_margin.json()["target_margin_percent"] == "150.000"
        token = _token(db_session, scenario.id)
        before = _stored(db_session, scenario.id)
        for invalid in (
            {"target_margin_percent": "1.0001"},
            {"target_margin_percent": "1000.000"},
            {"overload_threshold_percent": "0"},
        ):
            response = _patch(client, project.id, scenario.id, token, **invalid)
            assert response.status_code == 422
    assert _stored(db_session, scenario.id) == before

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            sa.update(Scenario)
            .where(Scenario.id == scenario.id)
            .values(overload_threshold_percent=Decimal("0.000"))
        )
    assert _stored(db_session, scenario.id) == before


def test_k_06_current_marker_succeeds_and_stale_or_approved_refusals_do_not_write(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K6 concurrency", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(
        db_session,
        project,
        name="Draft",
        target_margin_percent=Decimal("10.000"),
        overload_threshold_percent=Decimal("90.000"),
    )
    current = _token(db_session, scenario.id)
    stale = current - timedelta(seconds=1)

    with caller_holding(*OVERRIDE_PERMISSIONS):
        accepted = _patch(
            client, project.id, scenario.id, current, target_margin_percent="12.000"
        )
        assert accepted.status_code == 200, accepted.text
        stale_refusal = _patch(
            client, project.id, scenario.id, stale, target_margin_percent="30.000"
        )
    assert stale_refusal.status_code == 409
    assert "changed since it was read" in stale_refusal.json()["detail"]
    after_stale = _stored(db_session, scenario.id)
    assert after_stale == (Decimal("12.000"), Decimal("90.000"))

    db_session.execute(
        sa.update(Scenario)
        .where(Scenario.id == scenario.id)
        .values(status=ScenarioStatus.APPROVED)
    )
    approved_marker = _token(db_session, scenario.id)
    with caller_holding(*OVERRIDE_PERMISSIONS):
        approved_refusal = _patch(
            client,
            project.id,
            scenario.id,
            approved_marker,
            overload_threshold_percent="95.000",
        )
    assert approved_refusal.status_code == 409
    assert "Approved scenario" in approved_refusal.json()["detail"]
    assert _stored(db_session, scenario.id) == after_stale
