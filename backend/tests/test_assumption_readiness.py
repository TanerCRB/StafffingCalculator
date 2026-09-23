"""SC-1-10, K-04 — readiness counts the *resolved* margin, not the scenario's column (gate 1, Q-5).

F-01's `missing_inputs` / `ready_for_approval` (SC-1-05) asked "is `scenarios.target_margin_percent`
filled". Since SC-1-10 it asks "does the chain scenario → project → organisation produce a value": a
draft inheriting the organisation's margin is not missing one.

**The existing SC-1-05 assertions in `tests/test_project_list.py` are unchanged** (their docstrings
say why they stay literally true): no migration seeds an organisation row (gate 1, P-D), so a
scenario without a margin in those fixtures still has none on any level. What is new is the contrast
below — the same scenario, the same response, the margin supplied by another level.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    make_project,
    make_scenario,
    set_organization_defaults,
    set_project_overrides,
)

COMPLETE_EXCEPT_MARGIN = {
    "start_date": date(2026, 1, 1),
    "end_date": date(2026, 6, 30),
    "working_calendar": "PL-standard",
    "full_time_hours_per_week": Decimal("40.00"),
    "currency": "EUR",
}


def _scenarios(client: TestClient) -> dict[str, dict]:
    response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return {
        scenario["name"]: scenario
        for project in response.json()["projects"]
        for scenario in project["scenarios"]
    }


def test_k_04_an_inherited_margin_counts_as_present_for_readiness(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — missing on every level → listed; supplied by the organisation or the project → not.

    One scenario complete except for its own margin, read three times: with nothing anywhere (the
    SC-1-05 state), with an organisation default, and with the organisation default removed and a
    project override instead. The scenario's own column is `NULL` in all three reads, so an
    implementation still reading the column fails the second and the third.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, project, name="Inherits its margin", **COMPLETE_EXCEPT_MARGIN)

    nothing = _scenarios(client)["Inherits its margin"]
    assert nothing["missing_inputs"] == ["target_margin_percent"]
    assert nothing["ready_for_approval"] is False

    set_organization_defaults(db_session, target_margin_percent=Decimal("18.250"))
    from_organization = _scenarios(client)["Inherits its margin"]
    assert from_organization["missing_inputs"] == []
    assert from_organization["ready_for_approval"] is True

    set_organization_defaults(db_session)  # the row stays, the margin goes
    set_project_overrides(db_session, project.id, target_margin_percent=Decimal("12.500"))
    from_project = _scenarios(client)["Inherits its margin"]
    assert from_project["missing_inputs"] == []
    assert from_project["ready_for_approval"] is True

    # The payload's own `target_margin_percent` keeps its SC-1-05 meaning — the scenario's column —
    # in all three reads (the list contract is not widened by SC-1-10; the resolved value is served
    # by `GET …/assumptions`).
    assert from_project["target_margin_percent"] is None


def test_k_04_an_inherited_zero_margin_counts_as_present(
    client: TestClient, db_session: Session
) -> None:
    """K-04 with `0`: a 0 % margin inherited from the organisation is a value — nothing is
    missing."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, project, name="Zero", **COMPLETE_EXCEPT_MARGIN)
    set_organization_defaults(db_session, target_margin_percent=Decimal("0"))

    assert _scenarios(client)["Zero"]["missing_inputs"] == []


def test_k_04_an_approved_scenario_is_assessed_against_what_was_frozen_not_the_live_default(
    client: TestClient, db_session: Session
) -> None:
    """Readiness and the reader answer one question the same way (ADR-0012, point 7).

    An approved scenario with no frozen organisation row (here: approved by fixture, so no snapshot
    was ever written) keeps its margin listed as missing when a live default appears — while a draft
    of the same project, with the same columns, does not.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(
        db_session,
        project,
        name="Approved",
        status=ScenarioStatus.APPROVED,
        **COMPLETE_EXCEPT_MARGIN,
    )
    make_scenario(db_session, project, name="Draft", **COMPLETE_EXCEPT_MARGIN)
    set_organization_defaults(db_session, target_margin_percent=Decimal("18.250"))

    scenarios = _scenarios(client)
    assert scenarios["Approved"]["missing_inputs"] == ["target_margin_percent"]
    assert scenarios["Draft"]["missing_inputs"] == []
