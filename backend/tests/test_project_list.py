"""SC-1-05 — the caller's project list. One test per acceptance criterion.

All setup is written straight to the database: this slice is read-only, so no test may use a
create/edit/archive endpoint (there is none).
"""

import uuid
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas.project import ScenarioListItem
from app.models import ProjectStatus, Scenario, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    UNKNOWN_USER,
    as_caller,
    make_project,
    make_scenario,
)


def test_project_list_omits_projects_outside_caller_access(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 1 — one response, in-scope project present, out-of-scope project absent.

    Absent means absent: no row, no placeholder, no "unavailable" marker, and no trace of the
    project's name or id anywhere in the payload.
    """
    in_scope = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    out_of_scope = make_project(
        db_session, name="Borealis rollout", accessible_to=(OUT_OF_SCOPE_USER,)
    )

    response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200
    payload = response.json()
    returned_ids = [project["id"] for project in payload["projects"]]

    assert str(in_scope.id) in returned_ids, "the caller's own project must be in the response"
    assert str(out_of_scope.id) not in returned_ids
    assert returned_ids == [str(in_scope.id)]
    # A tombstone/placeholder would still carry the name or the id somewhere in the body.
    body = response.text
    assert str(out_of_scope.id) not in body
    assert "Borealis rollout" not in body


def test_sc_1_05_02_project_supports_two_independent_scenarios(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 2 — two scenarios of one project are two independent rows.

    Proven at the data layer: the fixture writes both, a direct write mutates one, and the
    other is unaffected — in the database and in the response.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    baseline = make_scenario(
        db_session,
        project,
        name="Baseline",
        working_calendar="PL-standard",
        target_margin_percent=Decimal("12.500"),
    )
    stretched = make_scenario(
        db_session,
        project,
        name="Stretched",
        working_calendar="DE-standard",
        target_margin_percent=Decimal("18.250"),
    )

    assert baseline.id != stretched.id
    assert db_session.query(Scenario).filter(Scenario.project_id == project.id).count() == 2

    # Direct write to one scenario only — no edit endpoint exists in this slice.
    baseline.target_margin_percent = Decimal("7.000")
    baseline.working_calendar = "ES-standard"
    db_session.flush()
    db_session.expire_all()

    reloaded_other = db_session.get(Scenario, stretched.id)
    assert reloaded_other is not None
    assert reloaded_other.target_margin_percent == Decimal("18.250")
    assert reloaded_other.working_calendar == "DE-standard"

    response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200
    scenarios = {item["name"]: item for item in response.json()["projects"][0]["scenarios"]}
    assert set(scenarios) == {"Baseline", "Stretched"}
    assert scenarios["Baseline"]["id"] != scenarios["Stretched"]["id"]
    # Decimals cross the boundary as strings, not JSON floats (ADR-0002).
    assert scenarios["Baseline"]["target_margin_percent"] == "7.000"
    assert scenarios["Stretched"]["target_margin_percent"] == "18.250"


def test_sc_1_05_03_drafts_report_their_own_missing_inputs_and_are_not_ready(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 3 — each draft carries its own missing-input list and a not-ready flag.

    Two drafts with different gaps must produce different lists; a single hardcoded list would
    make these two assertions contradict each other.

    Since SC-1-10 (gate 1, Q-5) `target_margin_percent` is missing when the *resolved* margin is —
    i.e. when it is absent on **all three** levels of the chain (scenario, project, organisation),
    not merely when the scenario's column is `NULL`. The first draft's margin is absent on all
    three: its project carries no override and no organisation defaults row exists, because no
    migration seeds one (gate 1, P-D). The assertion is therefore unchanged and still literally
    true; the contrast (a margin inherited from the organisation counts as present) is
    `tests/test_assumption_readiness.py`.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(
        db_session,
        project,
        name="Missing calendar and margin",
        start_date=date(2026, 2, 1),
        end_date=date(2026, 8, 31),
        full_time_hours_per_week=Decimal("40.00"),
        currency="EUR",
    )
    make_scenario(
        db_session,
        project,
        name="Missing dates and hours",
        working_calendar="PL-standard",
        currency="PLN",
        target_margin_percent=Decimal("15.000"),
    )

    response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200
    scenarios = {item["name"]: item for item in response.json()["projects"][0]["scenarios"]}

    first = scenarios["Missing calendar and margin"]
    second = scenarios["Missing dates and hours"]

    assert first["status"] == "Draft"
    assert second["status"] == "Draft"
    assert first["ready_for_approval"] is False
    assert second["ready_for_approval"] is False

    assert set(first["missing_inputs"]) == {"working_calendar", "target_margin_percent"}
    assert set(second["missing_inputs"]) == {
        "start_date",
        "end_date",
        "full_time_hours_per_week",
    }
    assert first["missing_inputs"] != second["missing_inputs"]


def test_sc_1_05_04_archived_project_stays_on_the_default_list_marked_archived(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 4 — an archived project (state set by fixture) is listed and marked Archived,
    while out-of-scope projects stay absent whatever their archive state."""
    active = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    archived = make_project(
        db_session,
        name="Cirrus maintenance",
        status=ProjectStatus.ARCHIVED,
        accessible_to=(IN_SCOPE_USER,),
    )
    foreign_active = make_project(
        db_session, name="Borealis rollout", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    foreign_archived = make_project(
        db_session,
        name="Delta wind-down",
        status=ProjectStatus.ARCHIVED,
        accessible_to=(OUT_OF_SCOPE_USER,),
    )

    response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200
    statuses = {project["id"]: project["status"] for project in response.json()["projects"]}

    assert statuses[str(archived.id)] == "Archived"
    assert statuses[str(active.id)] == "Active"
    assert str(foreign_active.id) not in statuses
    assert str(foreign_archived.id) not in statuses


def test_sc_1_05_04_approved_scenario_is_listed_as_approved_and_ready(
    client: TestClient, db_session: Session
) -> None:
    """Supporting check for the status vocabulary: `Draft`/`Approved` on the scenario, not on
    the project row (gate-1 decision 7).

    The margin is set on the scenario itself, so its resolved value (SC-1-10) comes from the
    scenario level whatever the other two levels hold."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(
        db_session,
        project,
        name="Signed off",
        status=ScenarioStatus.APPROVED,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
        working_calendar="PL-standard",
        full_time_hours_per_week=Decimal("40.00"),
        currency="EUR",
        target_margin_percent=Decimal("21.000"),
    )

    response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))

    scenario = response.json()["projects"][0]["scenarios"][0]
    assert scenario["status"] == "Approved"
    assert scenario["missing_inputs"] == []
    assert scenario["ready_for_approval"] is True


def test_sc_1_05_03_approved_scenario_with_a_gap_is_still_reported_as_not_ready(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 3, the case that must not be papered over: readiness follows completeness, not
    status.

    An `approved` row missing an input should not exist — but if one does, the list says so
    (gap listed, `ready_for_approval` false) instead of letting the status vouch for it. F-01:
    incomplete results are never presented as ready.

    "Missing" means absent on all three levels of the assumption chain since SC-1-10 (gate 1, Q-5):
    the scenario sets no margin, its project no override, and — this scenario being approved — no
    organisation defaults row was frozen for it (none exists: gate 1, P-D). The assertion is
    unchanged and still literally true.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(
        db_session,
        project,
        name="Approved but incomplete",
        status=ScenarioStatus.APPROVED,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
        working_calendar="PL-standard",
        full_time_hours_per_week=Decimal("40.00"),
        currency="EUR",
        # target_margin_percent deliberately absent on all three levels of the chain (SC-1-10):
        # not set here, no project override, no organisation defaults row frozen at approval
    )

    response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))

    scenario = response.json()["projects"][0]["scenarios"][0]
    assert scenario["status"] == "Approved"
    assert scenario["missing_inputs"] == ["target_margin_percent"]
    assert scenario["ready_for_approval"] is False


def test_project_owner_is_not_serialized_into_the_list_payload(
    client: TestClient, db_session: Session
) -> None:
    """The owner's name is stored but not sent: no screen renders it, and it is personal data
    (NF-11). A field nobody displays should not be travelling over the wire."""
    make_project(
        db_session,
        name="Aurora migration",
        accessible_to=(IN_SCOPE_USER,),
        owner="Anna Kowalska",
    )

    response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))

    project_payload = response.json()["projects"][0]
    assert "owner" not in project_payload
    assert "Anna Kowalska" not in response.text


def test_caller_with_no_project_access_rows_gets_an_empty_list_not_an_error(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 1, the other side: "no projects" is a normal, successful answer.

    A caller who appears in no `project_access` row at all is not an error case and not a
    special case — the scope filter simply matches nothing. The contrast against
    `test_project_list_omits_projects_outside_caller_access` is one changed element: the same
    two projects exist, only the caller is someone with no grants, and the list is empty
    rather than one-of-two. An endpoint that answered 404/403 here would tell an outsider that
    projects exist and that they are simply not theirs.
    """
    make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_project(db_session, name="Borealis rollout", accessible_to=(OUT_OF_SCOPE_USER,))

    response = client.get("/projects", headers=as_caller(UNKNOWN_USER))

    assert response.status_code == 200
    assert response.json() == {"projects": [], "total": 0}
    assert "Aurora migration" not in response.text
    assert "Borealis rollout" not in response.text


def test_decimals_cross_the_api_boundary_as_fixed_point_strings() -> None:
    """ADR-0002/NF-01 at the level the mechanism actually lives.

    The end-to-end assertion on `"18.250"` does *not* prove this schema's serializer: a plain
    `Decimal` field would produce the same string, because Pydantic already renders Decimal as
    JSON text, and `NUMERIC(6,3)` can never hand back an exponent-form Decimal. What only
    `DecimalString` guarantees is *fixed-point* text for any Decimal reaching the boundary —
    including the exponent-form values that computed figures (margin, markup, F-07 costs) can
    carry once they are not read straight out of a scaled column. `1E+2` rendered as `"1E+2"`
    is a number no spreadsheet export and no frontend `Number()` parse reads back as 100.
    """
    item = ScenarioListItem(
        id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        name="Computed",
        status="Draft",
        missing_inputs=[],
        ready_for_approval=False,
        target_margin_percent=Decimal("1E+2"),
    )

    rendered = item.model_dump(mode="json")["target_margin_percent"]

    assert isinstance(rendered, str), "a Decimal must never leave as a JSON number"
    assert rendered == "100"
    assert "E" not in rendered and "e" not in rendered

    # Contrast: a value that is already fixed point keeps its trailing zeros — the scale is
    # information (ADR-0002), so the serializer must not normalise it away either.
    kept_scale = item.model_copy(
        update={"target_margin_percent": Decimal("18.250")}
    ).model_dump(mode="json")["target_margin_percent"]
    assert kept_scale == "18.250"
