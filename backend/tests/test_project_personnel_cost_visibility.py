"""SC-1-08 — the personnel-cost gate as a conjunction of two mechanisms (K-01..K-06).

The gate (`app.api.response_shaping._without_personnel_costs`) opens only when *both*
`caller.has(PERSONNEL_COSTS_READ)` and the caller's own
`project_access.can_view_personnel_costs` for *that project* are true (ADR-0005, addendum
2026-09-19, points 1–2). Before this task only the first half existed, which made cost visibility
a per-caller property — the shape the capabilities registry recorded as known gap R-03.

Two things every test here has to work around, both named by the gate-1 decision:

- **`PERSONNEL_COST_FIELDS` is empty in production** — no personnel-cost column exists yet
  (F-07/F-08, plan block 5), so the gate removes nothing and a denial is indistinguishable from
  no gate at all. The field set is monkeypatched to a field that does exist (`description`), from
  the test side only; `test_k_06_...` is the proof that this substitution is load-bearing rather
  than decorative.
- **Nothing in production grants the flag** (ADR-0005, addendum 2026-09-19, point 4). The
  positive branch is therefore reachable only from a test: the flag by a direct database write
  (`grant_personnel_cost_visibility` / `make_project(cost_visible_to=…)`), the permission by
  `app.dependency_overrides[get_caller_identity]`. `PLACEHOLDER_PERMISSIONS` is untouched, so the
  canary in `test_access_control.py` keeps holding.

Nothing in `app/` is touched by these tests.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import response_shaping
from app.api.deps import get_caller_identity
from app.core.identity import CallerIdentity, Permission
from app.main import app
from app.models import ProjectAccess
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    grant_personnel_cost_visibility,
    make_project,
    project_payload,
)

STAND_IN_COST_FIELD = "description"
"""A field that exists on `ProjectListItem`/`ProjectDetail` today, standing in for the
personnel-cost columns that do not exist yet. Deliberately a field present on *both*
representations, so the list path and the detail path can be asserted against the same name. Which
field it is carries no meaning — the gate is field-set driven."""

EVERY_PROJECT_PATH = ("create", "list", "detail", "patch", "archive", "copy")
"""Every endpoint in `app/api/projects.py` that answers with a project representation. Named as
data so K-04's assertion is "all of them", not "the ones somebody remembered".

`create` and `copy` are here even though both always answer with the field blanked: their grant is
brand new, so its flag is `false` by the column default whatever the caller holds (ADR-0005,
addendum 2026-09-19, point 4). They are still gated paths, and a path missing from this tuple is a
path nothing forces into the K-04 comparison — which is why the tuple is the full set of endpoints
rather than only the ones whose answer can differ."""


def _expected_description(name: str) -> str:
    """What `make_project` puts in the stand-in cost field."""
    return f"{name} description"


@pytest.fixture
def personnel_costs_are_a_gated_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the otherwise-empty gate have something to remove."""
    monkeypatch.setattr(
        response_shaping, "PERSONNEL_COST_FIELDS", frozenset({STAND_IN_COST_FIELD})
    )


@contextmanager
def caller_holding(*permissions: Permission) -> Iterator[None]:
    """Run the enclosed requests as `IN_SCOPE_USER` holding exactly `permissions`.

    The override is the only way to reach a caller with `PERSONNEL_COSTS_READ`: adding it to
    `PLACEHOLDER_PERMISSIONS` would widen the dated deviation of ADR-0005 and trip the set-equality
    canary (ADR-0005, addendum 2026-09-19, point 5). `user_id` stays `IN_SCOPE_USER` so the scope
    filter and the `project_access` grants below are the real ones.
    """
    app.dependency_overrides[get_caller_identity] = lambda: CallerIdentity(
        user_id=IN_SCOPE_USER, permissions=frozenset(permissions)
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_caller_identity, None)


def _cost_flag_in_database(session: Session, project_id: uuid.UUID, user_id: str) -> bool:
    """The flag as the database holds it — not as a response payload reports it."""
    return session.execute(
        sa.select(ProjectAccess.can_view_personnel_costs).where(
            ProjectAccess.user_id == user_id, ProjectAccess.project_id == project_id
        )
    ).scalar_one()


@pytest.mark.usefixtures("personnel_costs_are_a_gated_field")
def test_k_01_one_list_response_resolves_the_cost_flag_per_project_not_per_caller(
    client: TestClient, db_session: Session
) -> None:
    """K-01. One caller, one `GET /projects`, two projects differing *only* in the flag.

    This is the criterion that pins down the shape of the fix rather than its effect: a gate
    resolved once per caller — from "the caller's `project_access` row", singular, or from the
    first row of the join — cannot produce two different answers inside one response. Both
    directions are present, so aliasing to either end of the list is caught: Aurora (flag `true`)
    would lose its field, Borealis (flag `false`) would gain one.

    The caller holds `PERSONNEL_COSTS_READ`, so the first half of the conjunction is satisfied for
    the whole response and cannot be what makes the two rows differ.
    """
    granted = make_project(
        db_session,
        name="Aurora migration",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    denied = make_project(
        db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,)
    )

    with caller_holding(Permission.PROJECT_READ, Permission.PERSONNEL_COSTS_READ):
        listed = client.get("/projects", headers=as_caller(IN_SCOPE_USER))

    assert listed.status_code == 200, listed.text
    by_id = {row["id"]: row for row in listed.json()["projects"]}
    assert by_id[str(granted.id)][STAND_IN_COST_FIELD] == _expected_description(
        "Aurora migration"
    )
    assert by_id[str(denied.id)][STAND_IN_COST_FIELD] is None
    # Same response, same caller, same permission set — only the per-assignment flag differs.
    assert by_id[str(denied.id)]["name"] == "Borealis rollout", (
        "the gate removes cost fields, not the project"
    )


@pytest.mark.usefixtures("personnel_costs_are_a_gated_field")
def test_k_02_the_assignment_flag_alone_does_not_open_the_gate(
    client: TestClient, db_session: Session
) -> None:
    """K-02. Flag `true`, no `PERSONNEL_COSTS_READ` → field `None`; with the permission → present.

    The conjunct being tested is `caller.has(PERSONNEL_COSTS_READ)`. Removing it from the gate
    would let the per-assignment flag alone decide, which is the opposite error from R-03 and the
    reason the addendum insists the change is narrowing only (point 2).

    The denial half runs as the placeholder identity, which never holds `PERSONNEL_COSTS_READ`;
    the contrast is the same request with nothing else changed.
    """
    project = make_project(
        db_session,
        name="Aurora migration",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    url = f"/projects/{project.id}"

    without_permission = client.get(url, headers=as_caller(IN_SCOPE_USER))
    with caller_holding(Permission.PROJECT_READ, Permission.PERSONNEL_COSTS_READ):
        with_permission = client.get(url, headers=as_caller(IN_SCOPE_USER))

    assert without_permission.status_code == 200, without_permission.text
    assert without_permission.json()[STAND_IN_COST_FIELD] is None
    assert with_permission.status_code == 200, with_permission.text
    assert with_permission.json()[STAND_IN_COST_FIELD] == _expected_description(
        "Aurora migration"
    )


@pytest.mark.usefixtures("personnel_costs_are_a_gated_field")
def test_k_03_the_global_permission_alone_does_not_open_the_gate(
    client: TestClient, db_session: Session
) -> None:
    """K-03. `PERSONNEL_COSTS_READ` held, flag `false` → `200` with the field blanked.

    The conjunct being tested is `view.can_view_personnel_costs` — the one that did not exist
    before this task, i.e. the state of the code on the day the criterion was written.

    The status code is asserted explicitly, and it is half the criterion: this is a refusal of a
    *field*, not of the project. A `403` would say "you may not see this project after all" and a
    `404` would deny a project the caller demonstrably has a grant for — both would also make the
    F-13 distinction (seeing a project ≠ seeing individual costs) unexpressible.

    The contrast is the same project and the same caller after the flag is set, so nothing but the
    flag can account for the difference.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    url = f"/projects/{project.id}"

    with caller_holding(Permission.PROJECT_READ, Permission.PERSONNEL_COSTS_READ):
        while_flag_is_false = client.get(url, headers=as_caller(IN_SCOPE_USER))

        assert while_flag_is_false.status_code == 200, while_flag_is_false.text
        assert while_flag_is_false.json()[STAND_IN_COST_FIELD] is None
        assert while_flag_is_false.json()["name"] == "Aurora migration"

        grant_personnel_cost_visibility(
            db_session, project_id=project.id, user_id=IN_SCOPE_USER
        )
        after_the_flag_is_set = client.get(url, headers=as_caller(IN_SCOPE_USER))

    assert after_the_flag_is_set.status_code == 200, after_the_flag_is_set.text
    assert after_the_flag_is_set.json()[STAND_IN_COST_FIELD] == _expected_description(
        "Aurora migration"
    )


def _cost_field_from_every_path(
    client: TestClient, db_session: Session, *, name: str, cost_visible: bool
) -> dict[str, object]:
    """Run every project-returning endpoint against one project; collect the cost field from each.

    One helper rather than six tests so the flag is the only difference between the two runs
    below, and so a path added to `app/api/projects.py` without a gate has one obvious place to be
    missing from.

    `POST /projects` is the one path that cannot run against the pre-made project — it makes its
    own — so it is run here on a separate project of its own rather than left out of the mapping.
    Its payload carries a non-empty `description`, so "blanked by the gate" is distinguishable from
    "never sent".
    """
    project = make_project(
        db_session,
        name=name,
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,) if cost_visible else (),
    )
    headers = as_caller(IN_SCOPE_USER)

    created = client.post(
        "/projects", json=project_payload(name=f"{name} (created)"), headers=headers
    )
    assert created.status_code == 201, created.text
    assert project_payload()[STAND_IN_COST_FIELD], "the create payload must carry the gated field"

    listed = client.get("/projects", headers=headers)
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["projects"] if item["id"] == str(project.id))

    detail = client.get(f"/projects/{project.id}", headers=headers)
    assert detail.status_code == 200, detail.text

    patched = client.patch(
        f"/projects/{project.id}",
        json={"updated_at": detail.json()["updated_at"], "client": "Westwind"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text

    # After the PATCH, so the token above is still the current one; archiving moves `updated_at`.
    archived = client.post(f"/projects/{project.id}/archive", headers=headers)
    assert archived.status_code == 200, archived.text

    copied = client.post(f"/projects/{project.id}/copy", headers=headers)
    assert copied.status_code == 201, copied.text

    return {
        "create": created.json()[STAND_IN_COST_FIELD],
        "list": row[STAND_IN_COST_FIELD],
        "detail": detail.json()[STAND_IN_COST_FIELD],
        "patch": patched.json()[STAND_IN_COST_FIELD],
        "archive": archived.json()[STAND_IN_COST_FIELD],
        "copy": copied.json()[STAND_IN_COST_FIELD],
    }


@pytest.mark.usefixtures("personnel_costs_are_a_gated_field")
def test_k_04_every_path_returning_a_project_applies_the_cost_gate(
    client: TestClient, db_session: Session
) -> None:
    """K-04. The gate is on all six paths — create, list, detail, `PATCH`, archive, copy.

    Not list and detail only: `POST /projects`, `PATCH`, `POST …/archive` and `POST …/copy` all
    answer with a full `ProjectDetail`, so a gate wired into the read paths alone would be bypassed
    by editing a project and reading the response (ADR-0005, addendum 2026-09-19, point 7 names this
    shape of leak for the `owner` field; the conjunction narrows it for cost fields).

    The two omissions K-04 names kill this test independently, and through disjoint entries:
    dropping the gate from the list row (`_shape_project`) flips `list` alone, dropping it from
    `shape_project_detail` flips the other five, since create, `PATCH`, archive and copy all answer
    through that one function. The assertion compares the whole mapping, so neither omission can
    hide behind the other.

    Create and copy are the deliberate asymmetry: with the flag `true` on the source, the copy's
    field is still blanked, and so is the newly created project's, because visibility there is
    decided by the *new* grant those write paths insert for the caller (`false` by the column
    default), not inherited from the source and not granted by creating. A copy that inherited it
    would be a path that grants cost visibility as a side effect.
    """
    denied = _cost_field_from_every_path(
        client, db_session, name="Borealis rollout", cost_visible=False
    )
    with caller_holding(
        Permission.PROJECT_READ,
        Permission.PROJECT_CREATE,
        Permission.PROJECT_EDIT,
        Permission.PROJECT_ARCHIVE,
        Permission.PROJECT_COPY,
        Permission.PERSONNEL_COSTS_READ,
    ):
        granted = _cost_field_from_every_path(
            client, db_session, name="Aurora migration", cost_visible=True
        )

    assert denied == dict.fromkeys(EVERY_PROJECT_PATH, None)

    expected = _expected_description("Aurora migration")
    assert granted == {
        "create": None,
        "list": expected,
        "detail": expected,
        "patch": expected,
        "archive": expected,
        "copy": None,
    }


@pytest.mark.usefixtures("personnel_costs_are_a_gated_field")
def test_k_04_the_assignment_flag_alone_denies_on_every_path(
    client: TestClient, db_session: Session
) -> None:
    """K-04, second half — added by QA because the two runs above differ in *two* things.

    `test_k_04_...applies_the_cost_gate` compares a denied run made as the placeholder identity
    (no `PERSONNEL_COSTS_READ`, flag `false`) against a granted run (permission *and* flag). Both
    factors move at once, so on the three write paths — `PATCH`, archive, copy — nothing isolates
    the second conjunct: a `update_project`/`archive_project` that returned
    `can_view_personnel_costs=True` regardless of the grant still passed the whole suite. Two
    mutations proved it (see the mutation log for SC-1-08): forcing the flag to `True` in the view
    returned by `update_project`, and the same in `archive_project`, both SURVIVED.

    This run changes exactly one element against the granted run: the flag is `false` while the
    caller keeps `PERSONNEL_COSTS_READ`. Every path must blank the field, which is the per-request,
    per-assignment half of the conjunction being forced on all six paths rather than on the two
    read ones.
    """
    with caller_holding(
        Permission.PROJECT_READ,
        Permission.PROJECT_CREATE,
        Permission.PROJECT_EDIT,
        Permission.PROJECT_ARCHIVE,
        Permission.PROJECT_COPY,
        Permission.PERSONNEL_COSTS_READ,
    ):
        denied_by_the_flag_alone = _cost_field_from_every_path(
            client, db_session, name="Draco consolidation", cost_visible=False
        )

    assert denied_by_the_flag_alone == dict.fromkeys(EVERY_PROJECT_PATH, None), (
        "with the permission held, only the per-assignment flag can close the gate — and it must "
        "close it on every path, not only on list and detail"
    )


@pytest.mark.usefixtures("personnel_costs_are_a_gated_field")
def test_k_01_the_flag_is_the_callers_own_grant_not_anyones_on_the_project(
    client: TestClient, db_session: Session
) -> None:
    """K-01, second half — added by QA: "per (caller, project)" also constrains the *caller* axis.

    The tests above vary the flag across projects for one caller, which pins the project axis. They
    do not vary it across callers for one project, so nothing distinguished "this caller's grant"
    from "some grant on this project". A mutation that read the flag as
    `bool_or(can_view_personnel_costs)` over *every* `project_access` row of the project — the
    un-narrowed read ADR-0005's addendum (2026-09-19, point 6) forbids precisely because it would
    also reveal who else has access — SURVIVED the whole suite.

    Here the project has two grants: `OUT_OF_SCOPE_USER` may see its costs, `IN_SCOPE_USER` may
    not. The field must be blank for `IN_SCOPE_USER`, on the list path and the detail path alike.
    The contrast is the same project after `IN_SCOPE_USER`'s own grant is set, so the difference is
    one flag on one row.
    """
    project = make_project(
        db_session,
        name="Eridanus rollout",
        accessible_to=(IN_SCOPE_USER, OUT_OF_SCOPE_USER),
        cost_visible_to=(OUT_OF_SCOPE_USER,),
    )
    expected = _expected_description("Eridanus rollout")
    headers = as_caller(IN_SCOPE_USER)

    with caller_holding(Permission.PROJECT_READ, Permission.PERSONNEL_COSTS_READ):
        detail = client.get(f"/projects/{project.id}", headers=headers)
        listed = client.get("/projects", headers=headers)

        assert detail.status_code == 200, detail.text
        assert listed.status_code == 200, listed.text
        row = next(
            item for item in listed.json()["projects"] if item["id"] == str(project.id)
        )
        assert detail.json()[STAND_IN_COST_FIELD] is None, (
            "another user's cost-visibility grant is not this caller's"
        )
        assert row[STAND_IN_COST_FIELD] is None

        grant_personnel_cost_visibility(
            db_session, project_id=project.id, user_id=IN_SCOPE_USER
        )
        after_own_grant = client.get(f"/projects/{project.id}", headers=headers)
        listed_after = client.get("/projects", headers=headers)

    assert after_own_grant.status_code == 200, after_own_grant.text
    assert after_own_grant.json()[STAND_IN_COST_FIELD] == expected
    row_after = next(
        item for item in listed_after.json()["projects"] if item["id"] == str(project.id)
    )
    assert row_after[STAND_IN_COST_FIELD] == expected


@pytest.mark.usefixtures("personnel_costs_are_a_gated_field")
def test_k_05_a_new_grant_defaults_to_no_personnel_cost_visibility(
    client: TestClient, db_session: Session
) -> None:
    """K-05. `POST /projects` grants its creator access with `can_view_personnel_costs=false`.

    Checked in the database, not only in the payload: a response that happens to blank the field
    for some other reason would otherwise hide a grant written with the flag `true`. The row is
    read back through a `SELECT` of its own, so the assertion does not depend on the in-memory
    object the write path returned.

    The caller here *holds* `PERSONNEL_COSTS_READ`, which is what makes the criterion bite — the
    creation response is blanked by the second conjunct alone. This is the named consequence of
    ADR-0005, addendum 2026-09-19, point 4: after SC-1-08 the creator of a project does not see
    its personnel costs, and nothing in the running system grants the flag.

    The contrast rules out a response that blanks the field unconditionally.
    """
    with caller_holding(
        Permission.PROJECT_CREATE, Permission.PROJECT_READ, Permission.PERSONNEL_COSTS_READ
    ):
        created = client.post(
            "/projects", json=project_payload(), headers=as_caller(IN_SCOPE_USER)
        )
        assert created.status_code == 201, created.text
        project_id = uuid.UUID(created.json()["id"])

        assert _cost_flag_in_database(db_session, project_id, IN_SCOPE_USER) is False
        assert created.json()[STAND_IN_COST_FIELD] is None

        grant_personnel_cost_visibility(
            db_session, project_id=project_id, user_id=IN_SCOPE_USER
        )
        read_again = client.get(f"/projects/{project_id}", headers=as_caller(IN_SCOPE_USER))

    assert read_again.status_code == 200, read_again.text
    assert read_again.json()[STAND_IN_COST_FIELD] == project_payload()[STAND_IN_COST_FIELD]


def test_k_06_the_gate_is_inert_while_the_production_cost_field_set_is_empty(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K-06. Every denial above depends on the monkeypatched field set, and this says so out loud.

    Deliberately *not* using the `personnel_costs_are_a_gated_field` fixture for its first half:
    with the production value of `PERSONNEL_COST_FIELDS` (empty), `_without_personnel_costs`
    returns the item untouched and a doubly-denied caller still receives the field. So every
    `is None` assertion in this file would fail if the substitution were dropped — the proofs are
    not vacuous, and they are also not proof that AC-06 is satisfied today: there is nothing to
    leak until plan block 5 adds a real cost column.

    The second half re-runs the identical request with the field set substituted, so the two halves
    differ in exactly the ingredient this criterion is about.

    When plan block 5 lands and `PERSONNEL_COST_FIELDS` stops being empty, the first assertion
    fails and points at this docstring — which is the intended moment to delete the substitution
    from this file rather than to adjust the expectation.
    """
    project = make_project(db_session, name="Cassiopeia audit", accessible_to=(IN_SCOPE_USER,))
    url = f"/projects/{project.id}"
    expected = _expected_description("Cassiopeia audit")

    assert response_shaping.PERSONNEL_COST_FIELDS == frozenset(), (
        "a real personnel-cost field exists now — the stand-in substitution in this file has "
        "become a weaker test than the real thing"
    )
    inert = client.get(url, headers=as_caller(IN_SCOPE_USER))
    assert inert.status_code == 200, inert.text
    assert inert.json()[STAND_IN_COST_FIELD] == expected, (
        "with an empty field set the gate must remove nothing at all"
    )

    monkeypatch.setattr(
        response_shaping, "PERSONNEL_COST_FIELDS", frozenset({STAND_IN_COST_FIELD})
    )
    gated = client.get(url, headers=as_caller(IN_SCOPE_USER))

    assert gated.status_code == 200, gated.text
    assert gated.json()[STAND_IN_COST_FIELD] is None
