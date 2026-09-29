"""SC-1-03 — copy a Project: a new project row, every scenario copied as a `draft`, access for
the copying caller alone. One test per acceptance criterion.

Criterion 1: the copy is a new, independent Project row.
Criterion 2: every source scenario is copied into the copy as `draft` — including a source that
was `approved` — with no shared mutable reference to the source's data, proven at the level of the
scenario row.
Criterion 3: `project_access` for the copy exists for the copying caller only; the source's grants
are not replicated.
Criterion 4: copying a project outside the caller's scope is a 404.
Criterion 5: a caller without `PROJECT_COPY` gets a 403 and nothing is written.

**Limit of the proof for criterion 2, stated here and not only in the report:** a scenario's child
tables (staffing, costs, rates, commercial-model rules — ADR-0003) do not exist in the schema yet,
so "no shared mutable reference" is proven for the scenario *row* and for nothing below it. This
is therefore **not** a full proof of AC-02. What is proven about the part that does not exist yet
is narrower and explicit: the cascade registry
(`app.data.project_writes.SCENARIO_CHILD_COPIERS`) is really invoked, once per copied scenario,
after the copy row exists — see
`test_sc_1_03_02_registered_child_table_copiers_run_once_per_copied_scenario`. Each later task
creating such a table must append its copier there, in that same task (ADR-0004, addendum
2026-09-18, point 4).

The database is a real PostgreSQL (see `conftest`) and the copy path really commits.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.deps import get_caller_identity
from app.core.identity import CallerIdentity, Permission
from app.data import project_writes
from app.data.project_writes import PROJECT_COLUMNS_NOT_COPIED, SCENARIO_COLUMNS_NOT_COPIED
from app.main import app
from app.models import (
    Project,
    ProjectAccess,
    ProjectStatus,
    Scenario,
    ScenarioStatus,
    WorkingCalendarDayKind,
)
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    count_projects,
    make_absence,
    make_absence_type,
    make_allocation,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
    project_payload,
)

TEAMMATE_WITH_SOURCE_ACCESS = "pm-dawid"
"""A second user holding `project_access` to the *source* project and nothing else. The contrast
criterion 3 needs: someone who can see what was copied but not the copy."""

COPIED_SCENARIO_FIELDS: tuple[str, ...] = (
    "name",
    "start_date",
    "end_date",
    "working_calendar",
    "full_time_hours_per_week",
    "currency",
    "target_margin_percent",
    # SC-1-10: the scenario level of the assumption chain. A deliberate canary growth — the drift
    # guard below fired on the day the column joined, and the claim ("copied, value by value") is
    # unchanged. The override travels with the copy; its *source* is derived from which level holds
    # it, so carrying the value is carrying the source (ADR-0012, point 2).
    "overload_threshold_percent",
)
"""Scenario attributes the copy must carry over, written out by hand on purpose.

The production code copies by reflection over the mapper; repeating that reflection here would
compare the mechanism with itself and pass whatever it did. This list is the independent
statement of what "copied" means, and the drift guard below ties it to the model so a new column
cannot join `Scenario` without someone deciding which side it belongs on.
"""

COPIED_PROJECT_FIELDS: tuple[str, ...] = (
    "name",
    "client",
    "owner",
    "delivery_period_start",
    "delivery_period_end",
    "reporting_currency",
    "description",
    # SC-1-10: the project level of the assumption chain — the same deliberate canary growth.
    "target_margin_percent",
    "overload_threshold_percent",
)


def _fully_populated_scenario_inputs() -> dict[str, object]:
    """Every configurable input set, so a copied value can be told apart from a default."""
    return {
        "start_date": date(2026, 4, 1),
        "end_date": date(2026, 10, 31),
        "working_calendar": "PL-2026",
        # Decimal, never float — a rate/percentage crossing this test must not lose precision
        # on the way through (NF-01, ADR-0002).
        "full_time_hours_per_week": Decimal("37.50"),
        "currency": "EUR",
        "target_margin_percent": Decimal("18.250"),
        # SC-1-10 — set, so the copied value is compared against a value and not `None == None`.
        "overload_threshold_percent": Decimal("90.000"),
    }


def _scenario_rows(session: Session, project_id: uuid.UUID) -> dict[str, Scenario]:
    """Scenario rows of one project, keyed by name, read back from the database."""
    session.expire_all()
    rows = session.execute(sa.select(Scenario).where(Scenario.project_id == project_id)).scalars()
    return {row.name: row for row in rows}


def count_scenarios(session: Session) -> int:
    return session.execute(sa.select(sa.func.count()).select_from(Scenario)).scalar_one()


def access_holders(session: Session, project_id: uuid.UUID) -> list[str]:
    statement = (
        sa.select(ProjectAccess.user_id)
        .where(ProjectAccess.project_id == project_id)
        .order_by(ProjectAccess.user_id)
    )
    return list(session.execute(statement).scalars().all())


def test_sc_1_03_01_copy_is_a_new_independent_project_row(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 1 — a second row, not a second name for the first one.

    Three claims, because "new row" fails in three different ways: the response could echo the
    source id, the copy could be the only row left (a move, not a copy), and the two rows could
    share state. The third is checked by writing to the copy's row and reading the source's back:
    if the copy were the same row, or a view over it, the source's name would change too.
    """
    source = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    source_id = source.id

    response = client.post(f"/projects/{source_id}/copy", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 201, response.text
    copy_id = uuid.UUID(response.json()["id"])
    assert copy_id != source_id
    assert count_projects(db_session) == 2, "a copy adds a row; it does not move or replace one"

    db_session.expire_all()
    copy = db_session.get(Project, copy_id)
    assert copy is not None
    for field in COPIED_PROJECT_FIELDS:
        assert getattr(copy, field) == getattr(source, field), field

    # Independence: a write to the copy leaves the source untouched.
    db_session.execute(
        sa.update(Project)
        .where(Project.id == copy_id)
        .values(name="Aurora migration — variant B", description="reworked for the copy")
    )
    db_session.flush()
    db_session.expire_all()
    source_after = db_session.get(Project, source_id)
    assert source_after is not None
    assert source_after.name == "Aurora migration"
    assert source_after.description == "Aurora migration description"


def test_sc_1_03_01_copy_survives_the_request_as_its_own_committed_row(
    committing_client: TestClient, engine: Engine
) -> None:
    """Criterion 1, the persistence half — and criteria 2 and 3 at the committed level.

    The test above runs inside one transaction, where a flush and a commit are indistinguishable.
    Here each request commits for real and the checks are made on a *separate* connection, which
    by definition sees committed data only: a `copy_project` that flushed without committing
    leaves nothing for these queries, and a copy whose scenarios were never flushed leaves a
    project row with no children.
    """
    created = committing_client.post(
        "/projects", json=project_payload(), headers=as_caller(IN_SCOPE_USER)
    )
    assert created.status_code == 201, created.text
    source_id = uuid.UUID(created.json()["id"])
    with engine.begin() as connection:
        connection.execute(
            sa.insert(Scenario).values(
                id=uuid.uuid4(),
                project_id=source_id,
                name="Baseline",
                status=ScenarioStatus.APPROVED,
                **_fully_populated_scenario_inputs(),
            )
        )

    copied = committing_client.post(
        f"/projects/{source_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )

    assert copied.status_code == 201, copied.text
    copy_id = uuid.UUID(copied.json()["id"])
    with engine.connect() as connection:
        project_ids = connection.execute(sa.text("SELECT id FROM projects")).scalars().all()
        scenarios = connection.execute(
            sa.text(
                "SELECT project_id, name, status, working_calendar, full_time_hours_per_week"
                " FROM scenarios ORDER BY project_id = :copy_id"
            ),
            {"copy_id": copy_id},
        ).all()
        copy_access = connection.execute(
            sa.text("SELECT user_id FROM project_access WHERE project_id = :id"),
            {"id": copy_id},
        ).scalars().all()

    assert sorted(project_ids) == sorted([source_id, copy_id])
    source_scenario, copied_scenario = scenarios
    assert source_scenario.project_id == source_id
    assert source_scenario.status == "approved", "the source's approval is not spent by the copy"
    assert copied_scenario.project_id == copy_id
    assert copied_scenario.status == "draft"
    assert copied_scenario.name == "Baseline"
    assert copied_scenario.working_calendar == "PL-2026"
    assert copied_scenario.full_time_hours_per_week == Decimal("37.50")
    assert list(copy_access) == [IN_SCOPE_USER]


def test_sc_1_03_02_every_source_scenario_is_copied_as_a_draft_row_of_its_own(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 2 — one new `draft` row per source scenario, with no shared state.

    The source deliberately holds two scenarios in different states: an `approved` one with every
    input filled, and a `draft` with gaps. Both must arrive as `draft` copies carrying their own
    values — a copy that reset the inputs, or that turned two scenarios into one, fails here.

    Independence is checked in **both** directions, because a shared row shows up asymmetrically
    depending on which side is written: the copy's scenario is updated and the source's row read
    back, then the source's scenario is updated and the copy's row read back.
    """
    source = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    approved = make_scenario(
        db_session,
        source,
        name="Baseline",
        status=ScenarioStatus.APPROVED,
        **_fully_populated_scenario_inputs(),
    )
    incomplete = make_scenario(
        db_session, source, name="Aggressive", start_date=date(2026, 5, 1)
    )
    source_scenario_ids = {approved.id, incomplete.id}

    response = client.post(f"/projects/{source.id}/copy", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 201, response.text
    copy_id = uuid.UUID(response.json()["id"])
    # The response already says it: every scenario of the copy is a Draft.
    assert [scenario["status"] for scenario in response.json()["scenarios"]] == ["Draft", "Draft"]

    copies = _scenario_rows(db_session, copy_id)
    originals = {approved.name: approved, incomplete.name: incomplete}
    assert sorted(copies) == ["Aggressive", "Baseline"], "one copy per source scenario, no merging"
    for name, copied in copies.items():
        assert copied.id not in source_scenario_ids, f"{name} is a new row, not the source one"
        assert copied.project_id == copy_id
        assert copied.status is ScenarioStatus.DRAFT, (
            f"{name} must be a draft on the copy even when the source was "
            f"{originals[name].status.value}"
        )
        for field in COPIED_SCENARIO_FIELDS:
            assert getattr(copied, field) == getattr(originals[name], field), f"{name}.{field}"
    # The source is not demoted by having been copied.
    assert _scenario_rows(db_session, source.id)["Baseline"].status is ScenarioStatus.APPROVED

    # Independence at row level, direction one: write to the copy's scenario.
    db_session.execute(
        sa.update(Scenario)
        .where(Scenario.id == copies["Baseline"].id)
        .values(working_calendar="DE-2026", target_margin_percent=Decimal("9.000"))
    )
    db_session.flush()
    source_baseline = _scenario_rows(db_session, source.id)["Baseline"]
    assert source_baseline.working_calendar == "PL-2026"
    assert source_baseline.target_margin_percent == Decimal("18.250")

    # Direction two: write to the source's scenario.
    db_session.execute(
        sa.update(Scenario)
        .where(Scenario.id == source_baseline.id)
        .values(currency="USD", full_time_hours_per_week=Decimal("40.00"))
    )
    db_session.flush()
    copied_baseline = _scenario_rows(db_session, copy_id)["Baseline"]
    assert copied_baseline.currency == "EUR"
    assert copied_baseline.full_time_hours_per_week == Decimal("37.50")


def test_sc_1_03_02_registered_child_table_copiers_run_once_per_copied_scenario(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 2, the part the schema cannot prove yet — the cascade seam is live, not planned.

    No child table of `scenarios` exists (staffing, costs, rates: later plan blocks), so there is
    nothing below the scenario row for this task to deep-copy. What can be proven today is that a
    copier registered in `SCENARIO_CHILD_COPIERS` is actually called, once per copied scenario,
    with the source and its copy — and *after* the copy row is flushed, because a child row needs
    its parent to exist before it can point at it. Without this, ADR-0004's forward commitment
    ("every new child table joins the cascade in the task that creates it") would rest on a
    registry nothing reads.

    This does not turn criterion 2 into a proof of AC-02; it narrows what is left unproven to the
    rows themselves.
    """
    source = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    first = make_scenario(db_session, source, name="Baseline")
    second = make_scenario(db_session, source, name="Aggressive")
    calls: list[tuple[uuid.UUID, uuid.UUID, bool]] = []

    def recording_copier(session: Session, original: Scenario, copied: Scenario) -> None:
        parent_exists = session.execute(
            sa.select(sa.func.count())
            .select_from(Scenario)
            .where(Scenario.id == copied.id)
        ).scalar_one() == 1
        calls.append((original.id, copied.id, parent_exists))

    monkeypatch.setattr(project_writes, "SCENARIO_CHILD_COPIERS", (recording_copier,))

    response = client.post(f"/projects/{source.id}/copy", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 201, response.text
    assert len(calls) == 2, "the cascade runs per scenario, not once for the whole project"
    assert {call[0] for call in calls} == {first.id, second.id}
    assert {call[1] for call in calls}.isdisjoint({first.id, second.id})
    assert all(call[2] for call in calls), "the copy row must exist before its children are copied"


def test_sc_1_03_03_project_access_for_the_copy_exists_only_for_the_caller(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 3 — the source's grants are not replicated (ADR-0005, addendum, point 4).

    The contrast is the teammate: someone who holds `project_access` to the source, did not run
    the copy, and must therefore not see the copy at all — while still seeing the source, so the
    assertion cannot be satisfied by a caller who simply sees nothing.
    """
    source = make_project(
        db_session,
        name="Aurora migration",
        accessible_to=(IN_SCOPE_USER, TEAMMATE_WITH_SOURCE_ACCESS),
    )

    response = client.post(f"/projects/{source.id}/copy", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 201, response.text
    copy_id = response.json()["id"]
    assert access_holders(db_session, uuid.UUID(copy_id)) == [IN_SCOPE_USER]
    # Copying moves nothing: the source keeps both of its grants.
    assert access_holders(db_session, source.id) == sorted(
        [IN_SCOPE_USER, TEAMMATE_WITH_SOURCE_ACCESS]
    )

    teammate_list = client.get("/projects", headers=as_caller(TEAMMATE_WITH_SOURCE_ACCESS))
    teammate_read = client.get(
        f"/projects/{copy_id}", headers=as_caller(TEAMMATE_WITH_SOURCE_ACCESS)
    )
    copier_read = client.get(f"/projects/{copy_id}", headers=as_caller(IN_SCOPE_USER))

    # Contrast: the teammate is not a caller who sees nothing — the source is right there.
    assert [project["id"] for project in teammate_list.json()["projects"]] == [str(source.id)]
    assert copy_id not in teammate_list.text
    assert teammate_read.status_code == 404
    assert copier_read.status_code == 200, copier_read.text


def test_sc_1_03_04_copying_a_project_outside_the_callers_scope_is_not_found(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 4 — 404, and indistinguishable from an id that never existed.

    ADR-0005's addendum (point 3) applies the SC-1-01 rule to write actions too: the refusal must
    not become a side-channel confirming the project exists. Both refusals are compared body for
    body, and nothing is written. The contrast at the end keeps "always 404" from passing: the
    same id, copied by the caller who does have access, is a 201.
    """
    source = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, source, name="Baseline")

    denied = client.post(f"/projects/{source.id}/copy", headers=as_caller(OUT_OF_SCOPE_USER))
    never_existed = client.post(
        f"/projects/{uuid.uuid4()}/copy", headers=as_caller(OUT_OF_SCOPE_USER)
    )

    assert denied.status_code == 404
    assert denied.status_code == never_existed.status_code
    assert denied.text == never_existed.text
    assert denied.headers.get("content-type") == never_existed.headers.get("content-type")
    assert denied.headers.get("content-length") == never_existed.headers.get("content-length")
    assert "Borealis rollout" not in denied.text
    assert str(source.id) not in denied.text
    # A refused copy writes nothing: no project row, no scenario row, no access grant.
    assert count_projects(db_session) == 1
    assert count_scenarios(db_session) == 1
    assert access_holders(db_session, source.id) == [IN_SCOPE_USER]

    granted = client.post(f"/projects/{source.id}/copy", headers=as_caller(IN_SCOPE_USER))

    assert granted.status_code == 201, granted.text
    assert count_projects(db_session) == 2


def test_sc_1_03_05_copy_denied_for_a_caller_without_the_project_copy_permission(
    client: TestClient, db_session: Session
) -> None:
    """Criterion 5 — the refusal test ADR-0005 makes mandatory for every new permission.

    The caller holds `PROJECT_READ` and nothing else: exactly F-13's read-only viewer, and the
    reason `PROJECT_COPY` has to be a permission of its own. Read access to the source is in
    place, so the 403 is about the *action*, not about scope — and the tables are unchanged
    afterwards, which is the part a status code alone does not prove. The contrast at the end
    runs the same request with the full placeholder identity and gets a 201.
    """
    source = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, source, name="Baseline", **_fully_populated_scenario_inputs())
    app.dependency_overrides[get_caller_identity] = lambda: CallerIdentity(
        user_id=IN_SCOPE_USER, permissions=frozenset({Permission.PROJECT_READ})
    )
    try:
        refused = client.post(f"/projects/{source.id}/copy", headers=as_caller(IN_SCOPE_USER))
    finally:
        app.dependency_overrides.pop(get_caller_identity, None)

    assert refused.status_code == 403
    assert count_projects(db_session) == 1
    assert count_scenarios(db_session) == 1
    assert access_holders(db_session, source.id) == [IN_SCOPE_USER]
    assert _scenario_rows(db_session, source.id)["Baseline"].currency == "EUR"

    allowed = client.post(f"/projects/{source.id}/copy", headers=as_caller(IN_SCOPE_USER))

    assert allowed.status_code == 201, allowed.text


def test_project_copy_denies_a_caller_without_any_identity(
    client: TestClient, db_session: Session
) -> None:
    """No identity, no copy — and the refusal leaves both tables as they were."""
    source = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, source, name="Baseline")

    response = client.post(f"/projects/{source.id}/copy")

    assert response.status_code == 401
    assert "Aurora migration" not in response.text
    assert count_projects(db_session) == 1
    assert count_scenarios(db_session) == 1


def test_copy_of_an_archived_project_is_active_and_leaves_the_source_archived(
    client: TestClient, db_session: Session
) -> None:
    """Archiving is a visibility state of the *source* (ADR-0004, addendum "archiving").

    Not an acceptance criterion — a decision written down in `PROJECT_COLUMNS_NOT_COPIED` and
    pinned here so it cannot drift silently. Copying an archived project is how a closed piece of
    work becomes the starting point of a new one, which an archived copy would not be.
    """
    source = make_project(
        db_session,
        name="Aurora migration",
        status=ProjectStatus.ARCHIVED,
        accessible_to=(IN_SCOPE_USER,),
    )

    response = client.post(f"/projects/{source.id}/copy", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "Active"
    db_session.expire_all()
    reread_source = db_session.get(Project, source.id)
    assert reread_source is not None
    assert reread_source.status is ProjectStatus.ARCHIVED


def test_every_scenario_column_is_either_copied_or_explicitly_excluded() -> None:
    """A drift guard, aimed at the failure ADR-0004 (addendum, point 4) names: something new that
    quietly stays behind when a scenario is copied.

    The production code copies by reflection, so a *column* added to `Scenario` later is carried
    over automatically — but the reflection is silent either way, and this test is what makes the
    decision visible: a new column fails here until it is added to `COPIED_SCENARIO_FIELDS` (and
    therefore compared value by value in the criterion-2 test) or named in
    `SCENARIO_COLUMNS_NOT_COPIED` with a reason. A *table* added later is not covered by this
    test at all; that is `SCENARIO_CHILD_COPIERS`.
    """
    mapped = {attribute.key for attribute in sa.inspect(Scenario).column_attrs}

    assert mapped == set(COPIED_SCENARIO_FIELDS) | SCENARIO_COLUMNS_NOT_COPIED


def test_every_project_column_is_either_copied_or_explicitly_excluded() -> None:
    """The same guard for the project row itself."""
    mapped = {attribute.key for attribute in sa.inspect(Project).column_attrs}

    assert mapped == set(COPIED_PROJECT_FIELDS) | PROJECT_COLUMNS_NOT_COPIED


# --- SC-3-02, K-15: what the cascade must NOT copy -----------------------------------------------


ORGANISATIONAL_TABLES = ("working_calendar", "working_calendar_day", "absence_type")
"""The three SC-3-02 tables whose rows belong to the organisation, not to a scenario.

Their absence from `SCENARIO_CHILD_COPIERS` is **correctness, not an omission** (ADR-0004, addendum
2026-09-22, point 1; the precedent is the catalogue, addendum 2026-09-19). A registry is silent
about what it does not contain, which is why this has to be asserted from the outside."""


def _row_counts(session: Session) -> dict[str, int]:
    """How many rows each of the three organisational tables holds, plus the three copied ones."""
    tables = (
        *ORGANISATIONAL_TABLES,
        "staffing_position",
        "staffing_position_allocation",
        "staffing_position_absence",
    )
    session.expire_all()
    return {
        table: session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in tables
    }


def test_k_15_copying_a_project_copies_no_organisational_calendar_row(
    client: TestClient, db_session: Session
) -> None:
    """K-15 — copying a project duplicates the plan, never the company's calendars.

    A calendar, its exceptional days and an absence type belong to the organisation; a copy of a
    project that duplicated them would give the copy a private calendar that drifts from the
    company's the first time a holiday is added, and would make "how many calendars do we have?"
    a function of how often somebody clicked *copy*.

    Three assertions, and the contrast is the middle one:

    1. **the three organisational tables hold exactly the same number of rows** before and after
       the copy. The mutation the criterion names — a calendar copier appended to the registry —
       makes these counts grow and fails here;
    2. **the three scenario-owned tables grow**, by the same numbers as the source held, so this
       test cannot be satisfied by a copy that copied nothing at all. Without it, deleting
       `copy_staffing_positions` from the registry would leave K-15 green;
    3. **`len(SCENARIO_CHILD_COPIERS)` is unchanged** — one entry, for one aggregate. The registry
       says nothing about what it omits, so the count is asserted directly, together with the
       identity check `test_the_staffing_copier_is_the_registered_entry_of_the_scenario_cascade`
       already makes one file over.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    make_calendar_day(
        db_session,
        calendar,
        day=date(2026, 12, 25),
        kind=WorkingCalendarDayKind.NON_WORKING,
    )
    absence_type = make_absence_type(db_session)
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    position = make_staffing_position(
        db_session,
        scenario,
        make_dimension_tuple(db_session, calendar=calendar),
        start_date=date(2026, 3, 1),
    )
    make_allocation(db_session, position, period_month=date(2026, 3, 1))
    make_absence(
        db_session,
        position,
        absence_type,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 6),
    )

    before = _row_counts(db_session)
    copiers_before = len(project_writes.SCENARIO_CHILD_COPIERS)

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text

    after = _row_counts(db_session)

    # (1) nothing organisational was duplicated.
    for table in ORGANISATIONAL_TABLES:
        assert after[table] == before[table], (
            f"copying a project duplicated rows of {table}, which belongs to the organisation and "
            "not to a scenario (ADR-0004, addendum 2026-09-22, point 1)"
        )

    # (2) the contrast: the scenario's own tables did grow, by exactly what the source held.
    for table in (
        "staffing_position",
        "staffing_position_allocation",
        "staffing_position_absence",
    ):
        assert after[table] == 2 * before[table] > 0, (
            f"{table} was not copied — the contrast of this test is void and K-15 would be "
            "satisfied by a copy that copies nothing"
        )

    # (3) the registry is unchanged by the copy. Two entries since SC-4-01 — re-armed, not loosened:
    # the second is the commercial-rule aggregate (ADR-0004, addendum 2026-09-23 SC-4-01, point 1b),
    # a scenario-owned table, and still no calendar copier. Three since SC-5-05 — re-armed, not
    # loosened: the third is `copy_scenario_additional_costs` (ADR-0004, addendum SC-5-05, point
    # 4), the scenario-level additional costs, again scenario-owned. Four since SC-1-11 —
    # re-armed, not loosened: the fourth is `copy_scenario_delivery_segments` (ADR-0004, addendum
    # 2026-09-25 SC-1-11, point 4), the delivery segments/workstreams, again scenario-owned.
    # Still no calendar
    # copier.
    assert len(project_writes.SCENARIO_CHILD_COPIERS) == copiers_before == 4


def test_k_15_the_copied_positions_still_point_at_the_one_shared_calendar(
    client: TestClient, db_session: Session
) -> None:
    """K-15's other half — the copy uses the *same* calendar, through the same location.

    Not the same claim as "no calendar row was copied": a copier could leave the calendar tables
    alone and still re-point the copy at nothing (a `NULL` `calendar_id` on a copied location, if
    locations were ever copied), which would make every capacity of the copy read `"n/a"`. Here the
    copied position carries the *source's* `location_id`, so it resolves the same calendar and
    produces the same figures.

    That shared reference is correct and is the point: a calendar is organisational, so two
    scenarios reading one calendar is the intended state, unlike the absence rows of criterion K-14
    which must not be shared.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=date(2026, 3, 1)
    )
    make_allocation(db_session, position, period_month=date(2026, 3, 1))

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    copy_id = uuid.UUID(response.json()["id"])
    db_session.expire_all()
    copy_scenario_id = db_session.execute(
        sa.select(Scenario.id).where(Scenario.project_id == copy_id)
    ).scalar_one()

    source_grid = client.get(
        f"/projects/{project.id}/scenarios/{scenario.id}/staffing-positions",
        headers=as_caller(IN_SCOPE_USER),
    ).json()["positions"][0]["allocations"][0]
    copy_grid = client.get(
        f"/projects/{copy_id}/scenarios/{copy_scenario_id}/staffing-positions",
        headers=as_caller(IN_SCOPE_USER),
    ).json()["positions"][0]["allocations"][0]

    assert copy_grid["derived_capacity_hours"] == source_grid["derived_capacity_hours"] == "165.00"
    assert (
        copy_grid["derived_capacity_source"]["calendar_id"]
        == source_grid["derived_capacity_source"]["calendar_id"]
        == str(calendar.id)
    )
