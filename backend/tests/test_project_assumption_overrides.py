"""SC-1-10, K-07 and K-09 — the project-level override through `PATCH /projects/{id}`, and the
threshold's domain on every level. Plus the Story's copy criterion.

- **K-07** (gate 1, Q-3 = A2) the two overrides are edited through the existing `PATCH`, belong to
  group 2 of ADR-0004 (`FROZEN_BY_APPROVED_SCENARIO`) and are refused with a `409` once a scenario
  of the project is approved. `null` removes an override (gate 1, G-1) — and only for these two
  fields.
- **K-09** (gate 1, Q-2/G-2) the threshold is a percentage of derived capacity: `> 100` is legal,
  `<= 0` is refused **by the database on all three levels** (two of which have no API path), and by
  the request schema with a `422` on the one level that has.
- **Copy** (the Story's criterion 6; ADR-0004, addendum 2026-09-18, point 4) a copied project and
  its copied scenarios carry the overrides with the same source; the organisation's default is not
  copied.
"""

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.domain.assumptions import ORGANIZATION, PROJECT, RESOLVED, SCENARIO
from app.models import OrganizationDefaults, Project, Scenario
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


def _token(client: TestClient, project_id: uuid.UUID) -> str:
    response = client.get(f"/projects/{project_id}", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()["updated_at"]


def _patch(http: TestClient, project_id: uuid.UUID, fields: dict[str, object]):
    return http.patch(
        f"/projects/{project_id}",
        json={"updated_at": _token(http, project_id), **fields},
        headers=as_caller(IN_SCOPE_USER),
    )


def _stored(session: Session, project_id: uuid.UUID) -> tuple[Decimal | None, Decimal | None]:
    session.expire_all()
    row = session.execute(
        sa.select(Project.target_margin_percent, Project.overload_threshold_percent).where(
            Project.id == project_id
        )
    ).one()
    return (row.target_margin_percent, row.overload_threshold_percent)


def _resolved(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
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


# --- K-07 -----------------------------------------------------------------------------------------


def test_k_07_a_project_override_is_set_and_cleared_through_patch_and_named_as_the_project_source(
    client: TestClient, db_session: Session
) -> None:
    """K-07 — set both overrides, see them win with source `project`; `null` gives the organisation
    back. Both edits are real `PATCH` requests carrying the ADR-0007 token."""
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("18.250"),
        overload_threshold_percent=Decimal("120.000"),
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")

    set_both = _patch(client, project.id, {MARGIN: "12.500", THRESHOLD: "105.000"})
    assert set_both.status_code == 200, set_both.text
    assert _stored(db_session, project.id) == (Decimal("12.500"), Decimal("105.000"))
    body = _resolved(client, project.id, scenario.id)
    assert body[MARGIN] == {"value": "12.500", "state": RESOLVED, "source": PROJECT}
    assert body[THRESHOLD] == {"value": "105.000", "state": RESOLVED, "source": PROJECT}

    cleared = _patch(client, project.id, {MARGIN: None})
    assert cleared.status_code == 200, cleared.text
    assert _stored(db_session, project.id) == (None, Decimal("105.000"))
    body = _resolved(client, project.id, scenario.id)
    assert body[MARGIN] == {"value": "18.250", "state": RESOLVED, "source": ORGANIZATION}
    assert body[THRESHOLD]["source"] == PROJECT, "clearing one override cleared the other"


def _detail(client: TestClient, project_id: uuid.UUID) -> dict:
    response = client.get(f"/projects/{project_id}", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()


def test_r_01_the_project_detail_serves_the_stored_override_not_the_resolved_value(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — what `PATCH` writes on the project level, `GET /projects/{id}` reads back, raw.

    (c) a new project answers `None` for both at once; (a) a set override is returned as stored;
    (b) `null` through `PATCH` makes it `None` again, without touching the other one.

    The scenario with its **own** margin override and the organisation default are the contrast
    that separates "stored" from "resolved": `/assumptions` answers the scenario's `7.000` (source
    `scenario`) before and after the clear — the detail answers the project's `12.500` and then
    `None`, never `7.000` and never the organisation's `18.250`. A shaping that served the resolved
    value fails both halves. Also checks the list row does not carry the fields (detail-only, like
    `updated_at` and `owner`).
    """
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("18.250"),
        overload_threshold_percent=Decimal("120.000"),
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(
        db_session, project, name="Baseline", target_margin_percent=Decimal("7.000")
    )

    fresh = _detail(client, project.id)
    assert (fresh[MARGIN], fresh[THRESHOLD]) == (None, None)

    set_both = _patch(client, project.id, {MARGIN: "12.500", THRESHOLD: "105.000"})
    assert set_both.status_code == 200, set_both.text
    assert (set_both.json()[MARGIN], set_both.json()[THRESHOLD]) == ("12.500", "105.000")
    db_session.expire_all()
    stored = _detail(client, project.id)
    assert (stored[MARGIN], stored[THRESHOLD]) == ("12.500", "105.000")
    assert _resolved(client, project.id, scenario.id)[MARGIN]["source"] == SCENARIO

    cleared = _patch(client, project.id, {MARGIN: None})
    assert cleared.status_code == 200, cleared.text
    db_session.expire_all()
    after_clear = _detail(client, project.id)
    assert (after_clear[MARGIN], after_clear[THRESHOLD]) == (None, "105.000")
    assert _resolved(client, project.id, scenario.id)[MARGIN]["value"] == "7.000"

    listed = client.get("/projects", headers=as_caller(IN_SCOPE_USER)).json()["projects"][0]
    assert MARGIN not in listed and THRESHOLD not in listed


@pytest.mark.parametrize(
    "change",
    [{MARGIN: "9.000"}, {THRESHOLD: "140.000"}, {MARGIN: None}, {THRESHOLD: None}],
    ids=["margin", "threshold", "margin-cleared", "threshold-cleared"],
)
def test_k_07_an_override_is_frozen_once_a_scenario_of_the_project_is_approved(
    client: TestClient, db_session: Session, change: dict
) -> None:
    """K-07 — group 2: after an approval (through the endpoint) every override edit is a `409`
    naming the approval, and the stored values do not move. Clearing is an edit too.

    Contrast: a group-1 field (`name`) is still editable on the same project, so the refusal is
    about which field, not about the project being locked.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    before_approval = _patch(client, project.id, {MARGIN: "11.000", THRESHOLD: "130.000"})
    assert before_approval.status_code == 200, before_approval.text
    approval = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    assert approval.status_code == 200, approval.text

    refused = _patch(client, project.id, change)

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"]
    assert _stored(db_session, project.id) == (Decimal("11.000"), Decimal("130.000"))
    renamed = _patch(client, project.id, {"name": "Aurora migration — phase 2"})
    assert renamed.status_code == 200, renamed.text


@pytest.mark.parametrize(
    "field", ["name", "client", "owner", "description", "reporting_currency", "delivery_period"]
)
def test_k_07_null_is_still_refused_for_every_other_field(
    client: TestClient, db_session: Session, field: str
) -> None:
    """Gate 1, G-1: `null` is legal for the two overrides **only**. Every field that was `NOT NULL`
    before SC-1-10 still answers `null` with a `422` naming it, and nothing is written."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    response = _patch(client, project.id, {field: None})

    assert response.status_code == 422, response.text
    assert field in response.text
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "Aurora migration"


# --- K-09 -----------------------------------------------------------------------------------------


def _write_threshold(session: Session, level: str, value: Decimal) -> None:
    """Write `value` as the threshold of one level, straight to the table — no Pydantic in the
    way."""
    if level == "organization_defaults":
        session.execute(
            sa.insert(OrganizationDefaults).values(id=1, overload_threshold_percent=value)
        )
        return
    project = make_project(session, name=f"K-09 {uuid.uuid4()}", accessible_to=(IN_SCOPE_USER,))
    if level == "projects":
        session.execute(
            sa.update(Project).where(Project.id == project.id).values(
                overload_threshold_percent=value
            )
        )
        return
    session.execute(
        sa.insert(Scenario).values(
            id=uuid.uuid4(), project_id=project.id, name="K-09", overload_threshold_percent=value
        )
    )


@pytest.mark.parametrize("level", ["organization_defaults", "projects", "scenarios"])
@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-5.000")], ids=["zero", "negative"])
def test_k_09_a_non_positive_threshold_is_refused_by_the_database_on_every_level(
    db_session: Session, level: str, value: Decimal
) -> None:
    """K-09 — the CHECK, on each of the three tables, named by the constraint that refused it."""
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError) as refused:
        _write_threshold(db_session, level, value)
        db_session.flush()
    savepoint.rollback()
    assert f"ck_{level}_overload_threshold_positive" in str(refused.value.orig)


@pytest.mark.parametrize("level", ["organization_defaults", "projects", "scenarios"])
@pytest.mark.parametrize(
    "value", [Decimal("0.001"), Decimal("250.000")], ids=["smallest", "overload-above-100"]
)
def test_k_09_contrast_a_positive_threshold_including_above_100_is_accepted_on_every_level(
    db_session: Session, level: str, value: Decimal
) -> None:
    """K-09's contrast — the smallest positive value the column holds, and a value above 100 %
    (over-allocation is legal, SC-3-01), are both stored on every level."""
    _write_threshold(db_session, level, value)
    db_session.flush()
    stored = db_session.execute(
        sa.text(f"SELECT count(*) FROM {level} WHERE overload_threshold_percent = :value"),
        {"value": value},
    ).scalar_one()
    assert stored == 1


@pytest.mark.parametrize("value", ["0", "0.000", "-1"])
def test_k_09_the_api_refuses_a_non_positive_threshold_with_422_and_writes_nothing(
    client: TestClient, db_session: Session, value: str
) -> None:
    """K-09 at the one level with an API path: a `422` naming the field, not the `500` the CHECK
    alone would produce, and the stored value unchanged. Contrast: `150.000` is accepted."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    refused = _patch(client, project.id, {THRESHOLD: value})
    assert refused.status_code == 422, refused.text
    assert THRESHOLD in refused.text
    assert _stored(db_session, project.id) == (None, None)

    accepted = _patch(client, project.id, {THRESHOLD: "150.000"})
    assert accepted.status_code == 200, accepted.text
    assert _stored(db_session, project.id) == (None, Decimal("150.000"))


def test_an_override_more_precise_than_the_column_is_refused_rather_than_rounded(
    client: TestClient, db_session: Session
) -> None:
    """`NUMERIC(6, 3)`: a fourth decimal is a `422`, never a silent rounding at write time; an
    oversized figure is a `422`, never SQLSTATE `22003` answered as a `500`."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    for value in ("12.3456", "12345.000"):
        response = _patch(client, project.id, {MARGIN: value})
        assert response.status_code == 422, (value, response.text)
    assert _stored(db_session, project.id) == (None, None)


# --- the Story's criterion 6: a copy carries the overrides and their source ----------------------


def test_copying_a_project_carries_its_overrides_and_their_sources(
    client: TestClient, db_session: Session
) -> None:
    """Overrides on the project and on a scenario arrive on the copy, on new rows, with the same
    source; the organisation's row is not duplicated (there is still exactly one). The source is
    derived from which level holds the value (ADR-0012, point 2), so it is checked through the
    reader on the copy, not assumed from the columns."""
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("18.250"),
        overload_threshold_percent=Decimal("120.000"),
    )
    source = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, source, name="Baseline", overload_threshold_percent=Decimal("90.000"))
    edit = _patch(client, source.id, {MARGIN: "12.500"})
    assert edit.status_code == 200, edit.text

    copied = client.post(f"/projects/{source.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert copied.status_code == 201, copied.text
    copy_id = uuid.UUID(copied.json()["id"])
    copy_scenario_id = uuid.UUID(copied.json()["scenarios"][0]["id"])

    body = _resolved(client, copy_id, copy_scenario_id)
    assert body[MARGIN] == {"value": "12.500", "state": RESOLVED, "source": PROJECT}
    assert body[THRESHOLD] == {"value": "90.000", "state": RESOLVED, "source": SCENARIO}
    assert _stored(db_session, copy_id) == (Decimal("12.500"), None)
    organisation_rows = db_session.execute(
        sa.select(sa.func.count()).select_from(OrganizationDefaults)
    ).scalar_one()
    assert organisation_rows == 1
