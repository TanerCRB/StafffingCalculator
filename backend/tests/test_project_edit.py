"""SC-1-02 — edit a Project. One test per acceptance criterion, plus the contrasts each
criterion names.

Criterion 1: the descriptive fields (`name`, `client`, `owner`, `description`) are editable
whatever the status of the project's scenarios (ADR-0004, addendum, group 1).
Criterion 2: `reporting_currency` / `delivery_period` are refused **in the data-access layer**,
not only in the API, once the project has an `approved` scenario — accepted before it
(ADR-0004, addendum, group 2).
Criterion 3: an edit carrying a stale concurrency token is a 409, never a silent overwrite
(ADR-0007, NF-05) — with the contrast that a fresh token succeeds.
Criterion 4: editing a project outside the caller's scope is a 404 indistinguishable from an id
that never existed, including when the token is stale as well (ADR-0007: 404 before 409).
Criterion 5: a caller holding only `PROJECT_READ` gets a 403 and writes nothing — the refusal
test ADR-0005 makes mandatory for every new permission.

Approval is set by a direct database write, exactly as SC-1-05 did for `archived`: no approval
endpoint exists yet, and these tests must not pretend otherwise.

The database is a real PostgreSQL (see `conftest`). Criterion 3 runs on `committing_client`
because the token only moves when a write actually commits: inside one shared transaction
PostgreSQL's `now()` is the transaction's own start time, so two edits would be stamped
identically and a stale token would look fresh for reasons that have nothing to do with the
guard under test.
"""

import uuid
from datetime import date, datetime

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.deps import get_caller_identity
from app.core.identity import CallerIdentity, Permission
from app.data.project_writes import (
    ApprovedScenarioFieldsFrozen,
    ConcurrentEditConflict,
    ProjectFieldNotEditable,
    update_project,
)
from app.main import app
from app.models.scenario import ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    make_project,
    make_scenario,
)

EDITING_CALLER = CallerIdentity(
    user_id=IN_SCOPE_USER, permissions=frozenset({Permission.PROJECT_EDIT})
)
"""The data-layer tests build the identity themselves instead of borrowing the request
placeholder: `update_project` must work from the caller it is handed, and a test that took the
placeholder's permission set would stop proving that the day the set changes."""

DESCRIPTIVE_EDIT = {
    "name": "Aurora migration (phase 2)",
    "client": "Northwind Group",
    "owner": "Bartek Nowak",
    "description": "Phase 2: decommissioning the legacy billing stack.",
}

FROZEN_FIELD_EDIT = {
    "reporting_currency": "USD",
    "delivery_period": {"start": "2027-01-01", "end": "2027-09-30"},
}


def read_row(session: Session, project_id: uuid.UUID) -> sa.Row:  # type: ignore[type-arg]
    """The project as the database holds it — not as the ORM remembers it.

    Deliberately raw SQL on the columns: a "nothing was written" assertion made against an ORM
    object that is still in the identity map can pass while the row underneath says otherwise.
    """
    return session.execute(
        sa.text(
            "SELECT name, client, owner, description, reporting_currency,"
            " delivery_period_start, delivery_period_end, status FROM projects WHERE id = :id"
        ),
        {"id": project_id},
    ).one()


@pytest.mark.parametrize(
    "scenario_statuses",
    [
        (),
        (ScenarioStatus.DRAFT,),
        (ScenarioStatus.APPROVED,),
        (ScenarioStatus.DRAFT, ScenarioStatus.APPROVED),
    ],
    ids=["no-scenarios", "draft-only", "approved-only", "draft-and-approved"],
)
def test_sc_1_02_01_descriptive_fields_are_editable_whatever_the_scenario_status(
    client: TestClient,
    db_session: Session,
    scenario_statuses: tuple[ScenarioStatus, ...],
) -> None:
    """Criterion 1 — `name`, `client`, `owner`, `description` change in all four states.

    The parametrisation *is* the contrast: the approved cases and the non-approved cases run the
    same request against the same starting row, so a rule that froze the whole project after
    approval would fail here while still satisfying criterion 2. The fields ADR-0004 puts in
    group 2 are asserted unchanged as well — an edit of group 1 must not drag them along.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    for index, status in enumerate(scenario_statuses):
        make_scenario(db_session, project, name=f"Variant {index}", status=status)
    before = read_row(db_session, project.id)
    token = client.get(f"/projects/{project.id}", headers=as_caller(IN_SCOPE_USER)).json()[
        "updated_at"
    ]

    response = client.patch(
        f"/projects/{project.id}",
        json={"updated_at": token, **DESCRIPTIVE_EDIT},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == DESCRIPTIVE_EDIT["name"]
    assert body["client"] == DESCRIPTIVE_EDIT["client"]
    assert body["owner"] == DESCRIPTIVE_EDIT["owner"]
    assert body["description"] == DESCRIPTIVE_EDIT["description"]

    db_session.expire_all()
    row = read_row(db_session, project.id)
    assert (row.name, row.client, row.owner, row.description) == (
        DESCRIPTIVE_EDIT["name"],
        DESCRIPTIVE_EDIT["client"],
        DESCRIPTIVE_EDIT["owner"],
        DESCRIPTIVE_EDIT["description"],
    )
    # Group 2 is untouched by a group-1 edit, and so is the project's visibility status
    # (archiving is SC-1-04 and has its own permission).
    assert row.reporting_currency == before.reporting_currency
    assert row.delivery_period_start == before.delivery_period_start
    assert row.delivery_period_end == before.delivery_period_end
    assert row.status == before.status


def test_sc_1_02_02_frozen_fields_are_refused_by_the_data_layer_once_a_scenario_is_approved(
    db_session: Session,
) -> None:
    """Criterion 2, in the layer the criterion names — no API, no HTTP, no endpoint at all.

    One project, edited twice with the same change: accepted while its only scenario is a draft,
    refused after that scenario is approved by a direct database write. The contrast is on the
    *same* row, so a rule that simply never allowed `reporting_currency` to change would fail the
    first half.

    Calling `update_project` directly is the point: an API-level test could pass with the check
    living in a FastAPI handler, which is exactly the arrangement ADR-0004 rejects ("odrzucany na
    poziomie warstwy dostępu do danych, nie tylko w UI") — every future non-HTTP writer (import,
    script, export tooling) would then bypass it.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", status=ScenarioStatus.DRAFT)

    accepted = update_project(
        db_session,
        EDITING_CALLER,
        project.id,
        expected_updated_at=project.updated_at,
        changes={
            "reporting_currency": "USD",
            "delivery_period_start": date(2027, 1, 1),
            "delivery_period_end": date(2027, 9, 30),
        },
    )

    assert accepted is not None
    row_before_approval = read_row(db_session, project.id)
    assert row_before_approval.reporting_currency == "USD"
    assert row_before_approval.delivery_period_start == date(2027, 1, 1)

    # Approval by fixture, not by an action: no approval endpoint exists yet (SC-1-05 did the
    # same for `archived`).
    scenario.status = ScenarioStatus.APPROVED
    db_session.flush()

    with pytest.raises(ApprovedScenarioFieldsFrozen) as refusal:
        update_project(
            db_session,
            EDITING_CALLER,
            project.id,
            expected_updated_at=accepted.project.updated_at,
            changes={
                "reporting_currency": "PLN",
                "delivery_period_start": date(2028, 1, 1),
                "delivery_period_end": date(2028, 9, 30),
            },
        )

    # The refusal names the frozen fields and quotes no value (NF-11).
    assert "reporting_currency" in str(refusal.value)
    assert "PLN" not in str(refusal.value)

    db_session.expire_all()
    row = read_row(db_session, project.id)
    assert row.reporting_currency == "USD", "the refused edit still changed the row"
    assert row.delivery_period_start == date(2027, 1, 1)
    assert row.delivery_period_end == date(2027, 9, 30)

    # Same row, same moment, same token: group 1 stays editable after approval.
    still_editable = update_project(
        db_session,
        EDITING_CALLER,
        project.id,
        expected_updated_at=accepted.project.updated_at,
        changes={"name": "Aurora migration (renamed after approval)"},
    )
    assert still_editable is not None
    assert read_row(db_session, project.id).name == "Aurora migration (renamed after approval)"


def test_sc_1_02_02_the_api_reports_the_frozen_field_refusal_as_409_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 2 at the boundary: the data layer's refusal surfaces as a 409, not a 500.

    This is the mapping only. The rule itself is proven by the test above, without an endpoint.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", status=ScenarioStatus.DRAFT)
    token = client.get(f"/projects/{project.id}", headers=as_caller(IN_SCOPE_USER)).json()[
        "updated_at"
    ]

    accepted = client.patch(
        f"/projects/{project.id}",
        json={"updated_at": token, **FROZEN_FIELD_EDIT},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["reporting_currency"] == "USD"

    scenario.status = ScenarioStatus.APPROVED
    db_session.flush()
    token_after_approval = client.get(
        f"/projects/{project.id}", headers=as_caller(IN_SCOPE_USER)
    ).json()["updated_at"]

    refused = client.patch(
        f"/projects/{project.id}",
        json={
            "updated_at": token_after_approval,
            "reporting_currency": "PLN",
            "delivery_period": {"start": "2028-01-01", "end": "2028-09-30"},
        },
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"]
    assert "PLN" not in refused.text
    db_session.expire_all()
    row = read_row(db_session, project.id)
    assert row.reporting_currency == "USD"
    assert row.delivery_period_start == date(2027, 1, 1)


def test_sc_1_02_03_an_edit_carrying_a_stale_concurrency_token_is_refused_with_409(
    committing_client: TestClient, engine: Engine
) -> None:
    """Criterion 3 — the second writer is told, and the first writer's value survives.

    Three requests, one project: an edit with the token from the read (200, and the token moves),
    the same edit repeated with the *old* token (409), then the same edit with the new token
    (200). Without the last two contrasts "always 409" and "always 200" would each pass half of
    this test.

    `committing_client` rather than the shared-transaction client: `updated_at` is
    `func.now()`, i.e. the transaction's start time, so two edits inside one transaction carry
    the same stamp and no token could ever go stale. The `token_after_first != token` assertion
    is there to fail loudly if that ever becomes true again, instead of letting the 409 assertion
    below pass for the wrong reason.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        project_id = project.id
        setup.commit()

    token = committing_client.get(
        f"/projects/{project_id}", headers=as_caller(IN_SCOPE_USER)
    ).json()["updated_at"]

    first = committing_client.patch(
        f"/projects/{project_id}",
        json={"updated_at": token, "client": "Northwind Group"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert first.status_code == 200, first.text
    token_after_first = first.json()["updated_at"]
    assert token_after_first != token, (
        "the write did not move the concurrency token — nothing below could detect a stale one"
    )

    second = committing_client.patch(
        f"/projects/{project_id}",
        json={"updated_at": token, "client": "Contoso"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert second.status_code == 409, second.text
    # A refusal, not a merge: nothing about the competing change is disclosed.
    assert "Northwind Group" not in second.text
    assert "Contoso" not in second.text

    with engine.connect() as connection:
        survivor = connection.execute(
            sa.text("SELECT client FROM projects WHERE id = :id"), {"id": project_id}
        ).scalar_one()
    assert survivor == "Northwind Group", "the stale edit overwrote the other writer silently"

    third = committing_client.patch(
        f"/projects/{project_id}",
        json={"updated_at": token_after_first, "client": "Contoso"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert third.status_code == 200, third.text
    assert third.json()["client"] == "Contoso"


def test_sc_1_02_03_the_detail_read_carries_the_concurrency_token_and_the_list_does_not(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 3's precondition: the token the edit requires is obtainable from the read.

    ADR-0007 puts it on the read of one project ("odczyt zwraca go, zapis go wymaga"). The list
    stays as SC-1-05/06 defined it — asserted, so the token is not added to the list contract by
    accident.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    detail = client.get(f"/projects/{project.id}", headers=as_caller(IN_SCOPE_USER)).json()
    listed = client.get("/projects", headers=as_caller(IN_SCOPE_USER)).json()["projects"][0]

    token = datetime.fromisoformat(detail["updated_at"])
    assert token.tzinfo is not None, "a concurrency token without an offset is ambiguous"
    assert "updated_at" not in listed


def test_sc_1_02_04_editing_a_project_outside_the_callers_scope_is_not_found(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 4 — 404, byte-for-byte the same as an id that never existed, and never a 403.

    The outsider here *holds* `PROJECT_EDIT` (the placeholder grants it to everyone), so this
    tests the scope boundary and not the permission boundary — a 403 would confirm the project
    exists, which ADR-0005 forbids.

    The third request is ADR-0007's precedence rule: an out-of-scope project *and* a stale token
    must still answer 404. A 409 there would be a side channel — "this id is real, and somebody
    is editing it".
    """
    project = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    token = client.get(f"/projects/{project.id}", headers=as_caller(IN_SCOPE_USER)).json()[
        "updated_at"
    ]
    intruding_edit = {"updated_at": token, "name": "Renamed by an outsider"}

    denied = client.patch(
        f"/projects/{project.id}", json=intruding_edit, headers=as_caller(OUT_OF_SCOPE_USER)
    )
    never_existed = client.patch(
        f"/projects/{uuid.uuid4()}", json=intruding_edit, headers=as_caller(OUT_OF_SCOPE_USER)
    )
    denied_with_stale_token = client.patch(
        f"/projects/{project.id}",
        json={"updated_at": "2020-01-01T00:00:00+00:00", "name": "Renamed by an outsider"},
        headers=as_caller(OUT_OF_SCOPE_USER),
    )
    granted = client.patch(
        f"/projects/{project.id}",
        json={"updated_at": token, "name": "Borealis rollout (phase 2)"},
        headers=as_caller(IN_SCOPE_USER),
    )

    # Contrast: the row is editable, by the caller inside its scope.
    assert granted.status_code == 200, granted.text

    assert denied.status_code == 404
    assert denied.status_code == never_existed.status_code
    assert denied.text == never_existed.text
    assert denied.headers.get("content-length") == never_existed.headers.get("content-length")
    assert denied_with_stale_token.status_code == 404, "404 must take precedence over 409"
    assert denied_with_stale_token.text == never_existed.text
    assert "Borealis rollout" not in denied.text
    assert str(project.id) not in denied.text

    db_session.expire_all()
    assert read_row(db_session, project.id).name == "Borealis rollout (phase 2)"


def test_sc_1_02_05_caller_holding_only_project_read_is_refused_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 5 — the mandatory refusal test for `PROJECT_EDIT` (ADR-0005).

    `PROJECT_EDIT` has to be a permission of its own for this test to be possible: a caller with
    `PROJECT_READ` and nothing else is F-13's read-only viewer, and a viewer who can rename a
    project is the failure the separation exists to prevent. Holding `PROJECT_CREATE` would not
    help either — creating and editing are different rights over different rows.

    The 403 is asserted together with the row, because a refusal that answered 403 *after*
    writing would satisfy the status-code assertion on its own.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    token = client.get(f"/projects/{project.id}", headers=as_caller(IN_SCOPE_USER)).json()[
        "updated_at"
    ]
    app.dependency_overrides[get_caller_identity] = lambda: CallerIdentity(
        user_id=IN_SCOPE_USER,
        permissions=frozenset({Permission.PROJECT_READ, Permission.PROJECT_CREATE}),
    )
    try:
        response = client.patch(
            f"/projects/{project.id}",
            json={"updated_at": token, "name": "Renamed without permission"},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        app.dependency_overrides.pop(get_caller_identity, None)

    assert response.status_code == 403
    db_session.expire_all()
    assert read_row(db_session, project.id).name == "Aurora migration"


def test_project_edit_denies_a_caller_with_no_identity_at_all(
    client: TestClient, db_session: Session
) -> None:
    """No identity, no edit — and the refusal says nothing about the project."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    response = client.patch(f"/projects/{project.id}", json={"name": "Renamed"})

    assert response.status_code == 401
    assert "Aurora migration" not in response.text
    db_session.expire_all()
    assert read_row(db_session, project.id).name == "Aurora migration"


def test_the_data_layer_refuses_to_write_a_column_outside_the_editable_allow_list(
    db_session: Session,
) -> None:
    """`status` is not an editable field here: archiving is SC-1-04, with its own permission.

    `update_project` takes a mapping, which is convenient and exactly how an "edit" grows into a
    way to write any column. The allow-list is asserted through the two columns that matter most
    — the visibility flag and the identifier — and the refusal happens before any statement runs.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    before = read_row(db_session, project.id)

    for forbidden in ({"status": "archived"}, {"id": uuid.uuid4()}, {}):
        with pytest.raises(ProjectFieldNotEditable):
            update_project(
                db_session,
                EDITING_CALLER,
                project.id,
                expected_updated_at=project.updated_at,
                changes=forbidden,
            )

    db_session.expire_all()
    assert read_row(db_session, project.id) == before


def test_the_data_layer_returns_none_for_a_project_outside_the_callers_scope(
    db_session: Session,
) -> None:
    """The 404 of criterion 4 is not the endpoint's own idea, and not a `ConcurrentEditConflict`
    in disguise: with a stale token *and* no access, the data layer still returns `None` rather
    than raising — there is no path from here to an answer that distinguishes the two cases."""
    project = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    outsider = CallerIdentity(
        user_id=OUT_OF_SCOPE_USER, permissions=frozenset({Permission.PROJECT_EDIT})
    )

    outcome = update_project(
        db_session,
        outsider,
        project.id,
        expected_updated_at=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
        changes={"name": "Renamed by an outsider"},
    )

    assert outcome is None
    assert read_row(db_session, project.id).name == "Borealis rollout"


def test_the_concurrency_guard_is_the_update_statement_not_a_python_comparison(
    db_session: Session,
) -> None:
    """A token that never matched any state of the row is refused, and the refusal is the one
    ADR-0007 names.

    The row here is loaded and current, so a guard implemented as "compare the value I just read"
    would still pass a token nobody ever held — this asserts that the comparison happens in the
    `UPDATE ... WHERE`, against the row, and that the refusal is a `ConcurrentEditConflict` and
    not the frozen-field one (the project has no approved scenario at all).
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    with pytest.raises(ConcurrentEditConflict):
        update_project(
            db_session,
            EDITING_CALLER,
            project.id,
            expected_updated_at=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
            changes={"name": "Renamed with a token nobody issued"},
        )

    db_session.expire_all()
    assert read_row(db_session, project.id).name == "Aurora migration"
