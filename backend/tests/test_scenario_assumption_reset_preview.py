"""SC-1-25 K1-K7: scoped, permission-aware preview of draft assumption resets."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.api import scenarios as scenarios_api
from app.core.identity import Permission
from app.data.organization_defaults import update_organization_defaults
from app.models import OrganizationDefaults, Project, ProjectAccess, Scenario
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    caller_holding,
    make_project,
    make_scenario,
    set_organization_defaults,
    set_project_overrides,
    wait_until_a_lock_request_is_pending,
)

READ = Permission.SCENARIO_ASSUMPTIONS_READ
WRITE = Permission.SCENARIO_ASSUMPTIONS_WRITE
ORG_READ = Permission.ORGANIZATION_DEFAULTS_READ


def _preview_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/assumptions/reset-preview"


def _token(session: Session, scenario_id: uuid.UUID):
    session.expire_all()
    return session.scalar(sa.select(Scenario.updated_at).where(Scenario.id == scenario_id))


def _overrides(session: Session, scenario_id: uuid.UUID):
    session.expire_all()
    return session.execute(
        sa.select(Scenario.target_margin_percent, Scenario.overload_threshold_percent).where(
            Scenario.id == scenario_id
        )
    ).one()


def test_k_01_reset_preview_skips_scenario_values_and_uses_project_overrides(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K1 reset project", accessible_to=(IN_SCOPE_USER,))
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("8.000"),
        overload_threshold_percent=Decimal("88.000"),
    )
    set_project_overrides(
        db_session,
        project.id,
        target_margin_percent=Decimal("21.000"),
        overload_threshold_percent=Decimal("111.000"),
    )
    scenario = make_scenario(
        db_session,
        project,
        name="Draft",
        target_margin_percent=Decimal("31.000"),
        overload_threshold_percent=Decimal("131.000"),
    )
    with caller_holding(READ, ORG_READ):
        project_result = client.get(
            _preview_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
    assert project_result.status_code == 200, project_result.text
    assert project_result.json()["target_margin_percent"] == {
        "value": "21.000", "state": "resolved", "source": "project"
    }
    assert project_result.json()["overload_threshold_percent"] == {
        "value": "111.000", "state": "resolved", "source": "project"
    }
    # Clear one project field: target margin falls through; overload stays project-sourced.
    db_session.execute(
        sa.update(Project).where(Project.id == project.id).values(target_margin_percent=None)
    )
    db_session.flush()
    db_session.expire_all()
    with caller_holding(READ, ORG_READ):
        organization_result = client.get(
            _preview_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
    assert organization_result.status_code == 200, organization_result.text
    assert organization_result.json()["target_margin_percent"] == {
        "value": "8.000", "state": "resolved", "source": "organization"
    }
    assert organization_result.json()["overload_threshold_percent"] == {
        "value": "111.000", "state": "resolved", "source": "project"
    }


def test_k_02_reset_preview_uses_live_organization_fallback(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K2 live org", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Draft")
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("18.250"),
        overload_threshold_percent=Decimal("95.000"),
    )
    with caller_holding(READ, ORG_READ):
        first = client.get(_preview_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
        set_organization_defaults(
            db_session,
            target_margin_percent=Decimal("19.250"),
            overload_threshold_percent=Decimal("95.000"),
        )
        db_session.expire_all()
        second = client.get(
            _preview_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
    assert first.status_code == second.status_code == 200
    assert first.json()["target_margin_percent"] == {
        "value": "18.250", "state": "resolved", "source": "organization"
    }
    assert second.json()["target_margin_percent"] == {
        "value": "19.250", "state": "resolved", "source": "organization"
    }
    assert first.json()["overload_threshold_percent"] == second.json()[
        "overload_threshold_percent"
    ] == {"value": "95.000", "state": "resolved", "source": "organization"}


def test_k_03_reset_preview_resolves_each_assumption_independently(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K3 independent", accessible_to=(IN_SCOPE_USER,))
    set_project_overrides(db_session, project.id, target_margin_percent=Decimal("22.000"))
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("9.000"),
        overload_threshold_percent=Decimal("104.000"),
    )
    scenario = make_scenario(
        db_session,
        project,
        name="Draft",
        target_margin_percent=Decimal("32.000"),
        overload_threshold_percent=Decimal("134.000"),
    )
    with caller_holding(READ, ORG_READ):
        response = client.get(
            _preview_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        set_organization_defaults(
            db_session,
            target_margin_percent=Decimal("10.000"),
            overload_threshold_percent=Decimal("104.000"),
        )
        db_session.expire_all()
        changed_one_field = client.get(
            _preview_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
    assert response.status_code == 200, response.text
    assert response.json()["target_margin_percent"]["value"] == "22.000"
    assert response.json()["target_margin_percent"]["source"] == "project"
    assert response.json()["overload_threshold_percent"]["value"] == "104.000"
    assert response.json()["overload_threshold_percent"]["source"] == "organization"
    assert changed_one_field.status_code == 200
    assert changed_one_field.json()["target_margin_percent"]["value"] == "22.000"
    assert changed_one_field.json()["overload_threshold_percent"]["value"] == "104.000"


def test_k_04_out_of_scope_and_unknown_scenarios_have_identical_refusal(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K4 owned", accessible_to=(IN_SCOPE_USER,))
    other_project = make_project(db_session, name="K4 other", accessible_to=(OUT_OF_SCOPE_USER,))
    other_scenario = make_scenario(db_session, other_project, name="Hidden")
    unknown_scenario_id = uuid.uuid4()
    with caller_holding(READ, user_id=IN_SCOPE_USER):
        hidden = client.get(
            _preview_path(other_project.id, other_scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        db_session.add(ProjectAccess(user_id=IN_SCOPE_USER, project_id=other_project.id))
        db_session.flush()
        granted = client.get(
            _preview_path(other_project.id, other_scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        missing = client.get(
            _preview_path(project.id, unknown_scenario_id), headers=as_caller(IN_SCOPE_USER)
        )
    assert hidden.status_code == missing.status_code == 404
    assert hidden.content == missing.content
    assert granted.status_code == 200, granted.text


def test_k_05_approved_scenarios_are_refused_while_drafts_are_previewable(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K5 draft boundary", accessible_to=(IN_SCOPE_USER,))
    draft = make_scenario(db_session, project, name="Draft")
    approved = make_scenario(db_session, project, name="Approved")
    db_session.execute(
        sa.update(Scenario).where(Scenario.id == approved.id).values(status="approved")
    )
    db_session.flush()
    with caller_holding(READ):
        draft_response = client.get(
            _preview_path(project.id, draft.id), headers=as_caller(IN_SCOPE_USER)
        )
        approved_response = client.get(
            _preview_path(project.id, approved.id), headers=as_caller(IN_SCOPE_USER)
        )
    assert draft_response.status_code == 200
    assert approved_response.status_code == 409
    assert "target_margin_percent" not in approved_response.text


def test_k_06_organization_default_permission_gates_preview_and_reset(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K6 hidden org", accessible_to=(IN_SCOPE_USER,))
    set_organization_defaults(db_session, target_margin_percent=Decimal("17.250"))
    scenario = make_scenario(
        db_session, project, name="Draft", target_margin_percent=Decimal("33.000")
    )
    path = _preview_path(project.id, scenario.id)
    with caller_holding(READ, WRITE):
        denied_preview = client.get(path, headers=as_caller(IN_SCOPE_USER))
        denied_reset = client.patch(
            f"/projects/{project.id}/scenarios/{scenario.id}/assumptions",
            json={
                "updated_at": _token(db_session, scenario.id).isoformat(),
                "target_margin_percent": None,
            },
            headers=as_caller(IN_SCOPE_USER),
        )
    assert denied_preview.status_code == denied_reset.status_code == 403
    assert "17.250" not in denied_preview.text
    assert "organization" not in denied_preview.text
    assert _overrides(db_session, scenario.id)[0] == Decimal("33.000")
    with caller_holding(READ, WRITE, ORG_READ):
        allowed = client.get(path, headers=as_caller(IN_SCOPE_USER))
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["target_margin_percent"] == {
        "value": "17.250", "state": "resolved", "source": "organization"
    }


def test_k_07_preview_does_not_persist_reset_and_explicit_reset_does(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K7 non-mutating", accessible_to=(IN_SCOPE_USER,))
    set_organization_defaults(db_session, target_margin_percent=Decimal("16.000"))
    scenario = make_scenario(
        db_session,
        project,
        name="Draft",
        target_margin_percent=Decimal("36.000"),
        overload_threshold_percent=Decimal("136.000"),
    )
    before = _overrides(db_session, scenario.id)
    with caller_holding(READ, ORG_READ):
        response = client.get(
            _preview_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
    assert response.status_code == 200, response.text
    assert _overrides(db_session, scenario.id) == before
    with caller_holding(READ, WRITE, ORG_READ):
        reset = client.patch(
            f"/projects/{project.id}/scenarios/{scenario.id}/assumptions",
            json={
                "updated_at": _token(db_session, scenario.id).isoformat(),
                "target_margin_percent": None,
            },
            headers=as_caller(IN_SCOPE_USER),
        )
    assert reset.status_code == 200, reset.text
    assert _overrides(db_session, scenario.id) == (None, Decimal("136.000"))


def test_k_08_partial_reset_is_denied_when_another_preview_value_needs_org_read(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="K8 mixed source", accessible_to=(IN_SCOPE_USER,))
    set_project_overrides(db_session, project.id, target_margin_percent=Decimal("22.000"))
    set_organization_defaults(db_session, overload_threshold_percent=Decimal("105.000"))
    scenario = make_scenario(
        db_session,
        project,
        name="Draft",
        target_margin_percent=Decimal("32.000"),
        overload_threshold_percent=Decimal("135.000"),
    )
    before = _overrides(db_session, scenario.id)

    with caller_holding(READ, WRITE):
        preview = client.get(
            _preview_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        reset = client.patch(
            f"/projects/{project.id}/scenarios/{scenario.id}/assumptions",
            json={
                "updated_at": _token(db_session, scenario.id).isoformat(),
                "target_margin_percent": None,
            },
            headers=as_caller(IN_SCOPE_USER),
        )

    assert preview.status_code == reset.status_code == 403
    assert _overrides(db_session, scenario.id) == before

    # Grant only the missing permission: the same partial reset is now authorized.
    with caller_holding(READ, WRITE, ORG_READ):
        allowed_reset = client.patch(
            f"/projects/{project.id}/scenarios/{scenario.id}/assumptions",
            json={
                "updated_at": _token(db_session, scenario.id).isoformat(),
                "target_margin_percent": None,
            },
            headers=as_caller(IN_SCOPE_USER),
        )
    assert allowed_reset.status_code == 200, allowed_reset.text
    assert _overrides(db_session, scenario.id) == (None, Decimal("135.000"))


def test_k_09_reset_and_concurrent_org_default_insert_are_serialized(
    committing_client: TestClient,
    engine: Engine,
    monkeypatch,
    request: pytest.FixtureRequest,
) -> None:
    """A default inserted while reset holds its source lock commits only after the reset."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        assert setup.get(OrganizationDefaults, 1) is None
        project = make_project(setup, name="K9 reset lock", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(
            setup, project, name="Draft", target_margin_percent=Decimal("36.000")
        )
        state = (project.id, scenario.id)
        setup.commit()

    def cleanup_defaults() -> None:
        with engine.begin() as cleanup:
            cleanup.execute(sa.delete(OrganizationDefaults))

    request.addfinalizer(cleanup_defaults)

    lock_held = Event()
    release_reset = Event()
    original_preview = scenarios_api.scenario_reset_preview_for_caller

    def pause_after_locked_preview(*args, **kwargs):
        preview = original_preview(*args, **kwargs)
        if kwargs.get("lock_for_reset"):
            lock_held.set()
            if not release_reset.wait(timeout=10):
                raise TimeoutError("test did not release the reset transaction")
        return preview

    monkeypatch.setattr(
        scenarios_api, "scenario_reset_preview_for_caller", pause_after_locked_preview
    )

    def insert_default():
        with Session(bind=engine, expire_on_commit=False, future=True) as writer:
            return update_organization_defaults(
                writer,
                expected_updated_at=None,
                changes={"target_margin_percent": Decimal("17.000")},
            )

    path = f"/projects/{state[0]}/scenarios/{state[1]}/assumptions"
    with ThreadPoolExecutor(max_workers=2) as pool, caller_holding(READ, WRITE):
        reset_future = pool.submit(
            committing_client.patch,
            path,
            json={
                "updated_at": _token_from_engine(engine, state[1]),
                "target_margin_percent": None,
            },
            headers=as_caller(IN_SCOPE_USER),
        )
        assert lock_held.wait(timeout=10), "reset never reached its protected source read"
        writer_future = pool.submit(insert_default)
        try:
            blocked = wait_until_a_lock_request_is_pending(engine, timeout=5.0)
            assert blocked, "organization-default insert did not wait for the reset lock"
            assert not writer_future.done(), "organization default changed before reset committed"
        finally:
            release_reset.set()
        reset = reset_future.result(timeout=20)
        writer_future.result(timeout=20)

    assert reset.status_code == 200, reset.text
    with engine.connect() as observer:
        saved_override = observer.execute(
            sa.select(Scenario.target_margin_percent).where(Scenario.id == state[1])
        ).scalar_one()
        current_default = observer.execute(
            sa.select(OrganizationDefaults.target_margin_percent)
        ).scalar_one()
    assert saved_override is None
    assert current_default == Decimal("17.000")


def _token_from_engine(engine: Engine, scenario_id: uuid.UUID) -> str:
    with Session(bind=engine, expire_on_commit=False, future=True) as reader:
        token = reader.scalar(sa.select(Scenario.updated_at).where(Scenario.id == scenario_id))
        assert token is not None
        return token.isoformat()
