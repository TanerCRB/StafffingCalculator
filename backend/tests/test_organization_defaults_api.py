"""SC-1-22: the independently permissioned organization-defaults API."""

from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import PLACEHOLDER_PERMISSIONS
from app.core.identity import Permission
from app.models import ApprovedSnapshotOrganizationDefaults, OrganizationDefaults, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    assumptions_path,
    caller_holding,
    make_project,
    make_scenario,
    set_organization_defaults,
)

PATH = "/organization-defaults"
READ = Permission.ORGANIZATION_DEFAULTS_READ
WRITE = Permission.ORGANIZATION_DEFAULTS_WRITE


def _read_resolved(client: TestClient, path: str) -> dict:
    with caller_holding(
        Permission.PROJECT_READ,
        Permission.SCENARIO_ASSUMPTIONS_READ,
        Permission.ORGANIZATION_DEFAULTS_READ,
    ):
        response = client.get(path, headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()


def _read(client: TestClient) -> dict:
    response = client.get(PATH, headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()


def _stored(session: Session) -> OrganizationDefaults | None:
    session.expire_all()
    return session.execute(sa.select(OrganizationDefaults)).scalars().one_or_none()


def test_k_01_only_dedicated_organization_permissions_authorize_the_resource(
    client: TestClient, db_session: Session
) -> None:
    """No default placeholder, catalog, or project grant authorizes org defaults."""
    assert READ not in PLACEHOLDER_PERMISSIONS
    assert WRITE not in PLACEHOLDER_PERMISSIONS
    assert _stored(db_session) is None

    assert client.get(PATH, headers=as_caller(IN_SCOPE_USER)).status_code == 403
    assert client.patch(
        PATH,
        json={"updated_at": None, "target_margin_percent": "15.000"},
        headers=as_caller(IN_SCOPE_USER),
    ).status_code == 403

    # Explicit contrasting grants: project/catalog authority remains unrelated.
    with caller_holding(Permission.PROJECT_READ, Permission.CATALOG_READ):
        assert client.get(PATH, headers=as_caller(IN_SCOPE_USER)).status_code == 403
    with caller_holding(Permission.CATALOG_WRITE, READ):
        assert client.patch(
            PATH,
            json={"updated_at": None, "target_margin_percent": "15.000"},
            headers=as_caller(IN_SCOPE_USER),
        ).status_code == 403
    with caller_holding(READ):
        assert client.patch(
            PATH,
            json={"updated_at": None, "target_margin_percent": "15.000"},
            headers=as_caller(IN_SCOPE_USER),
        ).status_code == 403
    with caller_holding(WRITE):
        assert client.get(PATH, headers=as_caller(IN_SCOPE_USER)).status_code == 403
        assert client.patch(
            PATH,
            json={"updated_at": None, "target_margin_percent": "15.000"},
            headers=as_caller(IN_SCOPE_USER),
        ).status_code == 403
    assert _stored(db_session) is None


def test_k_02_org_admin_can_create_read_and_update_each_default_independently(
    client: TestClient, db_session: Session
) -> None:
    """Absent marker inserts once; each partial write persists, reloads, and returns new marker."""
    with caller_holding(READ, WRITE):
        first = client.patch(
            PATH,
            json={"updated_at": None, "target_margin_percent": "18.250"},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["target_margin_percent"] == "18.250"
        assert first_body["overload_threshold_percent"] is None
        assert first_body["updated_at"]
    with caller_holding(READ):
        assert _read(client) == first_body

    with caller_holding(READ, WRITE):
        second = client.patch(
            PATH,
            json={
                "updated_at": first_body["updated_at"],
                "overload_threshold_percent": "125.000",
            },
            headers=as_caller(IN_SCOPE_USER),
        )
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["target_margin_percent"] == "18.250"
        assert second_body["overload_threshold_percent"] == "125.000"
        assert second_body["updated_at"] != first_body["updated_at"]
    with caller_holding(READ):
        assert _read(client) == second_body

    row = _stored(db_session)
    assert row is not None
    assert row.target_margin_percent == Decimal("18.250")
    assert row.overload_threshold_percent == Decimal("125.000")


def test_k_03_changing_org_defaults_updates_draft_resolution_only(
    client: TestClient, db_session: Session
) -> None:
    """Drafts inherit the live value; project overrides remain more specific."""
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("10.000"),
        overload_threshold_percent=Decimal("110.000"),
    )
    inherited_project = make_project(
        db_session, name="Defaults API inherited", accessible_to=(IN_SCOPE_USER,)
    )
    inherited_draft = make_scenario(db_session, inherited_project, name="Draft inherits")
    override_project = make_project(
        db_session, name="Defaults API override", accessible_to=(IN_SCOPE_USER,)
    )
    from tests.conftest import set_project_overrides

    set_project_overrides(db_session, override_project.id, target_margin_percent=Decimal("12.500"))
    override_draft = make_scenario(db_session, override_project, name="Draft override")
    make_scenario(
        db_session,
        inherited_project,
        name="Approved without frozen value",
        status=ScenarioStatus.APPROVED,
    )
    defaults = _stored(db_session)
    assert defaults is not None
    with caller_holding(READ, WRITE):
        changed = client.patch(
            PATH,
            json={
                "updated_at": defaults.updated_at.isoformat(),
                "target_margin_percent": "22.125",
            },
            headers=as_caller(IN_SCOPE_USER),
        )
        assert changed.status_code == 200, changed.text
    inherited = _read_resolved(client, assumptions_path(inherited_project.id, inherited_draft.id))
    assert inherited["target_margin_percent"] == {
        "value": "22.125",
        "state": "resolved",
        "source": "organization",
    }
    override = _read_resolved(client, assumptions_path(override_project.id, override_draft.id))
    assert override["target_margin_percent"] == {
        "value": "12.500",
        "state": "resolved",
        "source": "project",
    }


def test_k_04_approved_snapshot_is_unchanged_by_live_default_edit(
    client: TestClient, db_session: Session
) -> None:
    """An approved scenario reads only its frozen organization row after live changes."""
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("17.500"),
        overload_threshold_percent=Decimal("120.000"),
    )
    project = make_project(db_session, name="Frozen defaults API", accessible_to=(IN_SCOPE_USER,))
    draft = make_scenario(db_session, project, name="Draft sees live default")
    approved = make_scenario(db_session, project, name="Approved", status=ScenarioStatus.APPROVED)
    snapshot = ApprovedSnapshotOrganizationDefaults(
        scenario_id=approved.id,
        target_margin_percent=Decimal("17.500"),
        overload_threshold_percent=Decimal("120.000"),
    )
    db_session.add(snapshot)
    db_session.flush()
    before = _read_resolved(client, assumptions_path(project.id, approved.id))
    old = before
    assert old["target_margin_percent"]["value"] == "17.500"
    assert old["overload_threshold_percent"]["value"] == "120.000"

    current = _stored(db_session)
    assert current is not None
    with caller_holding(READ, WRITE):
        changed = client.patch(
            PATH,
            json={"updated_at": current.updated_at.isoformat(), "target_margin_percent": "21.000"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert changed.status_code == 200, changed.text
    with caller_holding(READ, WRITE):
        changed = client.patch(
            PATH,
            json={
                "updated_at": changed.json()["updated_at"],
                "overload_threshold_percent": "95.000",
            },
            headers=as_caller(IN_SCOPE_USER),
        )
    assert changed.status_code == 200, changed.text
    after = _read_resolved(client, assumptions_path(project.id, approved.id))
    assert after == old
    draft_after = _read_resolved(client, assumptions_path(project.id, draft.id))
    assert draft_after["target_margin_percent"] == {
        "value": "21.000",
        "state": "resolved",
        "source": "organization",
    }
    assert draft_after["overload_threshold_percent"] == {
        "value": "95.000",
        "state": "resolved",
        "source": "organization",
    }
    db_session.refresh(snapshot)
    assert snapshot.target_margin_percent == Decimal("17.500")
    assert snapshot.overload_threshold_percent == Decimal("120.000")


def test_k_05_invalid_input_is_rejected_without_changing_stored_values(
    client: TestClient, db_session: Session
) -> None:
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("19.000"),
        overload_threshold_percent=Decimal("120.000"),
    )
    stored = _stored(db_session)
    assert stored is not None
    marker = stored.updated_at.isoformat()
    for field, value in (
        ("target_margin_percent", "1.0001"),
        ("target_margin_percent", "1000.000"),
        ("overload_threshold_percent", "0"),
        ("overload_threshold_percent", "1.0001"),
    ):
        with caller_holding(READ, WRITE):
            response = client.patch(
                PATH,
                json={"updated_at": marker, field: value},
                headers=as_caller(IN_SCOPE_USER),
            )
        assert response.status_code == 422, response.text
        row = _stored(db_session)
        assert row is not None
        assert (row.target_margin_percent, row.overload_threshold_percent) == (
            Decimal("19.000"),
            Decimal("120.000"),
        )


def test_k_06_stale_marker_and_competing_first_write_are_named_409_without_overwrite(
    client: TestClient, db_session: Session
) -> None:
    """The database compares present markers atomically and absent tokens insert only once."""
    with caller_holding(READ, WRITE):
        first = client.patch(
            PATH,
            json={"updated_at": None, "target_margin_percent": "10.000"},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert first.status_code == 200, first.text
        token = first.json()["updated_at"]

        winner = client.patch(
            PATH,
            json={"updated_at": token, "target_margin_percent": "20.000"},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert winner.status_code == 200, winner.text
        stale = client.patch(
            PATH,
            json={"updated_at": token, "target_margin_percent": "30.000"},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert stale.status_code == 409
        assert "condition=updated_at_marker" in stale.json()["detail"]
        assert "30.000" not in stale.text

        absent_retry = client.patch(
            PATH,
            json={"updated_at": None, "target_margin_percent": "40.000"},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert absent_retry.status_code == 409
        assert "condition=updated_at_marker" in absent_retry.json()["detail"]
    with caller_holding(READ):
        assert _read(client)["target_margin_percent"] == "20.000"
