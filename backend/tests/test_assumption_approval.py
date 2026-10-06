"""SC-1-10, K-05 and K-06 — an approved scenario keeps the organisation defaults it was approved
with (AC-04; gate 1, P-A and P-B).

- **K-05** after the organisation's default changes, an approved scenario that inherited it still
  reads the **old** value with source `"organization"`; a draft inheriting the same default reads
  the **new** one (gate 1, Q-1: "saved" = approved). The approval goes through the **real endpoint**
  — with a scenario merely *set* to `approved` by a fixture there is no snapshot row, and the test
  would be proving K-06 under K-05's name (the analyst's warning, named in the Issue).
- **K-06** an approved scenario that was approved while the organisation had **no** defaults row
  stays at `"no_value"` after a default appears — the frozen absence is a fact, not a gap the live
  table may fill (the pattern of the statutory absence type, stage D of SC-3-03).

Committed, on separate connections: the approval commits, the organisation's default is changed by
another committed transaction, and the reader runs as a new request — the sequence AC-04 is about.
"""

import uuid
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.domain.assumptions import NO_VALUE, ORGANIZATION, PROJECT, RESOLVED
from app.models import ApprovedSnapshotOrganizationDefaults
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    assumptions_path,
    caller_holding,
    make_project,
    make_scenario,
    set_organization_defaults,
)

MARGIN = "target_margin_percent"
THRESHOLD = "overload_threshold_percent"


def _committed_project_with_two_drafts(engine: Engine) -> dict[str, uuid.UUID]:
    """A committed project with two drafts and no overrides anywhere: both inherit everything."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        to_approve = make_scenario(setup, project, name="To approve")
        stays_draft = make_scenario(setup, project, name="Stays draft")
        state = {
            "project_id": project.id,
            "approved_id": to_approve.id,
            "draft_id": stays_draft.id,
        }
        setup.commit()
    return state


def _read(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    with caller_holding(
        Permission.PROJECT_READ,
        Permission.SCENARIO_ASSUMPTIONS_READ,
        Permission.ORGANIZATION_DEFAULTS_READ,
    ):
        response = client.get(
            assumptions_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
        )
    assert response.status_code == 200, response.text
    return response.json()


def _set_defaults_committed(engine: Engine, **values: Decimal | None) -> None:
    with engine.begin() as connection:
        set_organization_defaults(connection, **values)


def test_k_05_changing_the_organization_default_does_not_change_an_approved_scenario(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05 — frozen value and frozen source for the approved; the new value for the draft.

    Both fields, one rule. The approval's own answer is checked too (`organization_defaults == 1`),
    so the frozen row is known to exist before the default moves — and the snapshot row is read
    back to show it holds the raw values and no source column.
    """
    state = _committed_project_with_two_drafts(engine)
    _set_defaults_committed(
        engine,
        target_margin_percent=Decimal("18.250"),
        overload_threshold_percent=Decimal("120.000"),
    )

    approval = committing_client.post(
        approve_path(state["project_id"], state["approved_id"]), headers=as_caller(IN_SCOPE_USER)
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["snapshot"]["organization_defaults"] == 1

    _set_defaults_committed(
        engine,
        target_margin_percent=Decimal("22.000"),
        overload_threshold_percent=Decimal("95.000"),
    )

    approved = _read(committing_client, state["project_id"], state["approved_id"])
    draft = _read(committing_client, state["project_id"], state["draft_id"])

    assert approved["status"] == "Approved"
    assert approved[MARGIN] == {"value": "18.250", "state": RESOLVED, "source": ORGANIZATION}
    assert approved[THRESHOLD] == {"value": "120.000", "state": RESOLVED, "source": ORGANIZATION}
    # The contrast (gate 1, Q-1): the draft follows the organisation's current standard.
    assert draft["status"] == "Draft"
    assert draft[MARGIN] == {"value": "22.000", "state": RESOLVED, "source": ORGANIZATION}
    assert draft[THRESHOLD] == {"value": "95.000", "state": RESOLVED, "source": ORGANIZATION}

    with engine.connect() as connection:
        frozen = connection.execute(
            sa.select(
                ApprovedSnapshotOrganizationDefaults.target_margin_percent,
                ApprovedSnapshotOrganizationDefaults.overload_threshold_percent,
            ).where(ApprovedSnapshotOrganizationDefaults.scenario_id == state["approved_id"])
        ).one()
        columns = set(
            connection.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'approved_snapshot_organization_defaults'"
                )
            ).scalars()
        )
    assert tuple(frozen) == (Decimal("18.250"), Decimal("120.000"))
    # Raw values and nothing else (gate 1, P-A): no resolved figure, no stored source.
    assert columns == {
        "id",
        "scenario_id",
        "target_margin_percent",
        "overload_threshold_percent",
        "created_at",
    }


def test_k_05_the_frozen_default_is_resolved_against_the_live_scenario_and_project_levels(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05 — the snapshot freezes the organisation level only; nearer levels still win on read.

    A project override set **before** the approval wins over the frozen default and names the
    project (P-A: the chain is resolved on read). It cannot be changed afterwards — that is K-07
    and K-08 — so this is the whole of what "resolved from frozen inputs" means for it.
    """
    state = _committed_project_with_two_drafts(engine)
    _set_defaults_committed(engine, target_margin_percent=Decimal("18.250"))
    token = committing_client.get(
        f"/projects/{state['project_id']}", headers=as_caller(IN_SCOPE_USER)
    ).json()["updated_at"]
    edit = committing_client.patch(
        f"/projects/{state['project_id']}",
        json={"updated_at": token, "overload_threshold_percent": "105.000"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert edit.status_code == 200, edit.text

    approval = committing_client.post(
        approve_path(state["project_id"], state["approved_id"]), headers=as_caller(IN_SCOPE_USER)
    )
    assert approval.status_code == 200, approval.text
    _set_defaults_committed(
        engine,
        target_margin_percent=Decimal("30.000"),
        overload_threshold_percent=Decimal("99.000"),
    )

    approved = _read(committing_client, state["project_id"], state["approved_id"])
    assert approved[MARGIN] == {"value": "18.250", "state": RESOLVED, "source": ORGANIZATION}
    assert approved[THRESHOLD] == {"value": "105.000", "state": RESOLVED, "source": PROJECT}


def test_k_06_an_approved_scenario_without_an_organization_default_stays_without_one(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06 — approved while the organisation had no defaults row: `no_value`, for ever.

    The approval reports `organization_defaults == 0` (nothing to freeze), a default is configured
    afterwards, and the approved scenario must *not* pick it up. The contrast is the draft of the
    same project, which does.
    """
    state = _committed_project_with_two_drafts(engine)

    approval = committing_client.post(
        approve_path(state["project_id"], state["approved_id"]), headers=as_caller(IN_SCOPE_USER)
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["snapshot"]["organization_defaults"] == 0

    _set_defaults_committed(
        engine,
        target_margin_percent=Decimal("18.250"),
        overload_threshold_percent=Decimal("120.000"),
    )

    approved = _read(committing_client, state["project_id"], state["approved_id"])
    draft = _read(committing_client, state["project_id"], state["draft_id"])

    for field in (MARGIN, THRESHOLD):
        assert approved[field] == {"value": "n/a", "state": NO_VALUE, "source": None}, (
            f"{field}: the approved scenario picked up a default configured after its approval"
        )
        assert draft[field]["state"] == RESOLVED
        assert draft[field]["source"] == ORGANIZATION
