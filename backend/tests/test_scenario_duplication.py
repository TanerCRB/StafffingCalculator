"""SC-6-01, Issue #11 — duplicating a scenario into its own project (F-09 pt.1, AC-02).

`POST /projects/{project_id}/scenarios/{scenario_id}/duplicate`: the second entry point into
`app.data.project_writes.copy_scenario` (ADR-0004, addendum 2026-09-18, point 1), after
`POST /projects/{id}/copy` (SC-1-03). One test per criterion (K-01..K-06), named after it.

**The naming algorithm under test** (gate 1, decision 1, `app.data.scenario_duplication`):
`"<source.name> (copy)"`, then `"(copy 2)"`, `"(copy 3)"`, … — the first not already held by a
sibling scenario of the same project.

**Limit of the proof for K-03, commercial_terms/tm_terms**, named here and in the report: unlike
staffing (`PATCH .../allocations/{month}`, `POST`/`DELETE .../absences`) and additional costs
(`PATCH .../additional-costs/{id}`), the commercial-terms router ships **no edit and no delete
path** — its own module docstring says so ("the model is immutable after the write… the one write
path is the one guarded, raced and tested"). "Edit a row on the duplicate through its existing
write endpoint" is therefore impossible to demonstrate for this table specifically; what is proven
instead, exactly as `tests/test_commercial_terms_copy.py` proves it for the SC-1-03 entry point, is
that the duplicate's `commercial_terms`/`tm_terms` rows carry their own, disjoint ids rather than
pointing back at the source's.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_caller_identity
from app.core.identity import CallerIdentity, Permission
from app.data import scenario_duplication
from app.main import app
from app.models import (
    AdditionalCost,
    CommercialTerms,
    Project,
    Scenario,
    ScenarioStatus,
    StaffingPositionAbsence,
    TmTerms,
)
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    additional_cost_path,
    additional_costs_path,
    as_caller,
    make_absence,
    make_absence_type,
    make_additional_cost,
    make_allocation,
    make_commercial_terms,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    staffing_path,
)

MARCH = date(2026, 3, 1)


def duplicate_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    """The address of the duplication action (SC-6-01)."""
    return f"/projects/{project_id}/scenarios/{scenario_id}/duplicate"


def count_scenarios(session: Session, project_id: uuid.UUID | None = None) -> int:
    statement = sa.select(sa.func.count()).select_from(Scenario)
    if project_id is not None:
        statement = statement.where(Scenario.project_id == project_id)
    return session.execute(statement).scalar_one()


def scenario_row(session: Session, scenario_id: uuid.UUID) -> Scenario:
    session.expire_all()
    row = session.get(Scenario, scenario_id)
    assert row is not None
    return row


def _duplicate_id(response) -> uuid.UUID:
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _fully_equipped_source(session: Session, *, status: ScenarioStatus = ScenarioStatus.DRAFT):
    """One project, one scenario, one row in every table `SCENARIO_CHILD_COPIERS` reaches.

    Used by the K-03 test: a position with one month and one absence, a T&M commercial rule, and
    two additional costs — one attached to the position, one scenario-level (ADR-0014, point 10,
    the two halves of the additional-cost cascade).
    """
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", status=status)
    dimensions = make_dimension_tuple(session)
    position = make_staffing_position(
        session, scenario, dimensions, headcount=2, start_date=MARCH, end_date=None
    )
    make_allocation(
        session,
        position,
        period_month=MARCH,
        availability_hours=Decimal("160.00"),
        planned_allocation_hours=Decimal("120.00"),
        billable_hours=Decimal("100.00"),
    )
    absence_type = make_absence_type(session)
    make_absence(
        session, position, absence_type, start_date=date(2026, 3, 2), end_date=date(2026, 3, 4)
    )
    make_commercial_terms(session, scenario, with_details=True)
    category = make_cost_category(session)
    make_additional_cost(
        session,
        scenario,
        category,
        amount=Decimal("500.0000"),
        start_month=MARCH,
        position=position,
    )
    make_additional_cost(
        session,
        scenario,
        category,
        amount=Decimal("900.0000"),
        start_month=MARCH,
    )
    return project, scenario, position


# --- K-01 ------------------------------------------------------------------------------------


def test_k_01_the_duplicate_lands_in_the_source_s_own_project(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — the duplicate is a new scenario row of the *same* project, never a new project.

    Contrast: `POST /projects/{id}/copy` (SC-1-03) is the entry point that *does* create a new
    project, and running it on the same source grows the project count. The mutation this test
    kills is `into_project` silently becoming a fresh `Project()` instead of `source.project`.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    count_projects_stmt = sa.select(sa.func.count()).select_from(Project)
    projects_before = db_session.execute(count_projects_stmt).scalar_one()

    response = client.post(
        duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    duplicate_id = _duplicate_id(response)

    assert duplicate_id != scenario.id
    duplicate_row = scenario_row(db_session, duplicate_id)
    assert duplicate_row.project_id == project.id, "the duplicate must be in the source's project"
    assert count_scenarios(db_session, project.id) == 2
    projects_after = db_session.execute(count_projects_stmt).scalar_one()
    assert projects_after == projects_before, "duplicating a scenario must not create a project"

    # Contrast: the sibling entry point that *does* grow the project count.
    copy_response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert copy_response.status_code == 201, copy_response.text
    projects_after_copy = db_session.execute(count_projects_stmt).scalar_one()
    assert projects_after_copy == projects_before + 1, (
        "the contrast is void if POST /projects/{id}/copy does not grow the project count either"
    )


# --- K-02 ------------------------------------------------------------------------------------


def test_k_02_the_duplicate_is_always_a_draft_and_the_source_s_status_is_untouched(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — a fresh `draft` copy, whatever the source's status, and the source's own status
    column is never written by this endpoint.

    Two sources in the same test, one `draft` and one `approved`: a mutation that copied the
    source's status verbatim passes for the draft source and fails only for the approved one, so
    both are needed to kill it.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    draft_source = make_scenario(db_session, project, name="Draft baseline")
    approved_source = make_scenario(
        db_session, project, name="Approved baseline", status=ScenarioStatus.APPROVED
    )

    draft_dup = client.post(
        duplicate_path(project.id, draft_source.id), headers=as_caller(IN_SCOPE_USER)
    )
    approved_dup = client.post(
        duplicate_path(project.id, approved_source.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert draft_dup.status_code == 201, draft_dup.text
    assert approved_dup.status_code == 201, approved_dup.text
    assert draft_dup.json()["status"] == "Draft"
    assert approved_dup.json()["status"] == "Draft", "a copy of an approved scenario is a draft"

    assert scenario_row(db_session, draft_source.id).status is ScenarioStatus.DRAFT
    assert scenario_row(db_session, approved_source.id).status is ScenarioStatus.APPROVED, (
        "duplicating a scenario must not demote or promote the source's own status"
    )
    assert (
        scenario_row(db_session, uuid.UUID(draft_dup.json()["id"])).status
        is ScenarioStatus.DRAFT
    )
    assert (
        scenario_row(db_session, uuid.UUID(approved_dup.json()["id"])).status
        is ScenarioStatus.DRAFT
    )


# --- K-03 ------------------------------------------------------------------------------------


def test_k_03_ac_02_holds_through_this_entry_point_for_every_registered_child_table(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — AC-02, proven through *this* endpoint, for every table `SCENARIO_CHILD_COPIERS`
    reaches: staffing position + allocation + absence; commercial_terms + tm_terms; additional
    cost, both halves (attached to a position, and scenario-level).

    Independence is checked in **both directions** wherever a write endpoint exists (staffing
    allocation, additional costs), and by disjoint identifiers where none does (commercial terms —
    see the module docstring's named limit).
    """
    project, source, source_position = _fully_equipped_source(db_session)

    response = client.post(
        duplicate_path(project.id, source.id), headers=as_caller(IN_SCOPE_USER)
    )
    duplicate_scenario_id = _duplicate_id(response)

    # --- staffing: position + allocation -----------------------------------------------------
    source_grid = client.get(
        staffing_path(project.id, source.id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]
    duplicate_grid = client.get(
        staffing_path(project.id, duplicate_scenario_id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]
    assert duplicate_grid["id"] != source_grid["id"], "the duplicate's position is a new row"
    assert (
        duplicate_grid["allocations"][0]["billable_hours"]
        == source_grid["allocations"][0]["billable_hours"]
        == "100.00"
    )

    # Direction one: edit the *duplicate's* allocation through the PATCH endpoint.
    edit_on_duplicate = client.patch(
        f"{staffing_path(project.id, duplicate_scenario_id)}/{duplicate_grid['id']}"
        f"/allocations/{MARCH.isoformat()}",
        json={"updated_at": duplicate_grid["updated_at"], "billable_hours": "77.00"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert edit_on_duplicate.status_code == 200, edit_on_duplicate.text
    source_after_1 = client.get(
        staffing_path(project.id, source.id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]
    assert source_after_1["allocations"][0]["billable_hours"] == "100.00", (
        "editing the duplicate's allocation must not move the source's own row"
    )

    # Direction two: edit the *source's* allocation through the same endpoint.
    edit_on_source = client.patch(
        f"{staffing_path(project.id, source.id)}/{source_after_1['id']}"
        f"/allocations/{MARCH.isoformat()}",
        json={"updated_at": source_after_1["updated_at"], "billable_hours": "55.00"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert edit_on_source.status_code == 200, edit_on_source.text
    duplicate_after = client.get(
        staffing_path(project.id, duplicate_scenario_id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]
    assert duplicate_after["allocations"][0]["billable_hours"] == "77.00", (
        "editing the source's allocation must not move the duplicate's own row"
    )

    # --- staffing: absences (no PATCH; DELETE is the write endpoint under test) -------------
    assert len(duplicate_grid["absences"]) == 1
    assert len(source_grid["absences"]) == 1
    duplicate_absence_id = duplicate_grid["absences"][0]["id"]
    assert duplicate_absence_id != source_grid["absences"][0]["id"]

    delete_on_duplicate = client.request(
        "DELETE",
        f"{staffing_path(project.id, duplicate_scenario_id)}/{duplicate_grid['id']}"
        f"/absences/{duplicate_absence_id}",
        json={"updated_at": duplicate_after["updated_at"]},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert delete_on_duplicate.status_code == 200, delete_on_duplicate.text
    source_absences_after = db_session.execute(
        sa.select(sa.func.count())
        .select_from(StaffingPositionAbsence)
        .where(StaffingPositionAbsence.position_id == source_position.id)
    ).scalar_one()
    assert source_absences_after == 1, (
        "deleting the duplicate's absence must not remove the source's own absence"
    )

    # --- commercial_terms + tm_terms: no edit endpoint exists (module docstring) -------------
    # AC-02 is proven by disjoint identifiers instead, matching the precedent
    # `tests/test_commercial_terms_copy.py` sets for the project-copy entry point.
    source_terms = db_session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == source.id)
    ).scalar_one()
    duplicate_terms = db_session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == duplicate_scenario_id)
    ).scalar_one()
    assert duplicate_terms.id != source_terms.id
    assert duplicate_terms.model_type == source_terms.model_type == "time_and_material"
    source_tm = db_session.get(TmTerms, source_terms.id)
    duplicate_tm = db_session.get(TmTerms, duplicate_terms.id)
    assert source_tm is not None and duplicate_tm is not None
    assert duplicate_tm.commercial_terms_id != source_tm.commercial_terms_id

    # --- additional cost, both halves (ADR-0014, point 10) -----------------------------------
    source_costs = {
        cost["position_id"]: cost
        for cost in client.get(
            additional_costs_path(project.id, source.id), headers=as_caller(IN_SCOPE_USER)
        ).json()["costs"]
    }
    duplicate_costs = {
        cost["position_id"]: cost
        for cost in client.get(
            additional_costs_path(project.id, duplicate_scenario_id),
            headers=as_caller(IN_SCOPE_USER),
        ).json()["costs"]
    }
    # Each scenario has exactly two costs: one scenario-level (`position_id` null) and one
    # attached to that scenario's own copy of the position.
    assert set(source_costs) == {None, str(source_position.id)}
    assert set(duplicate_costs) == {None, duplicate_grid["id"]}

    cost_pairs = (
        ("scenario-level", source_costs[None], duplicate_costs[None]),
        (
            "position-attached",
            source_costs[str(source_position.id)],
            duplicate_costs[duplicate_grid["id"]],
        ),
    )
    for label, source_cost, duplicate_cost in cost_pairs:
        assert duplicate_cost["id"] != source_cost["id"], label

        # Direction one: edit the duplicate's cost through the PATCH endpoint.
        edited = client.patch(
            additional_cost_path(
                project.id, duplicate_scenario_id, uuid.UUID(duplicate_cost["id"])
            ),
            json={"updated_at": duplicate_cost["updated_at"], "amount": "1.2300"},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert edited.status_code == 200, (label, edited.text)
        source_reread = db_session.execute(
            sa.select(AdditionalCost).where(AdditionalCost.id == uuid.UUID(source_cost["id"]))
        ).scalar_one()
        assert source_reread.amount == Decimal(source_cost["amount"]), (
            label,
            "editing the duplicate's cost must not move the source's own row",
        )

        # Direction two: edit the source's cost through the same endpoint.
        edited_source = client.patch(
            additional_cost_path(project.id, source.id, uuid.UUID(source_cost["id"])),
            json={"updated_at": source_cost["updated_at"], "amount": "4.5600"},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert edited_source.status_code == 200, (label, edited_source.text)
        duplicate_reread = db_session.execute(
            sa.select(AdditionalCost).where(AdditionalCost.id == uuid.UUID(duplicate_cost["id"]))
        ).scalar_one()
        assert duplicate_reread.amount == Decimal("1.2300"), (
            label,
            "editing the source's cost must not move the duplicate's row, which must still carry "
            "its own earlier edit",
        )


# --- K-04 ------------------------------------------------------------------------------------


def test_k_04_duplicating_a_scenario_outside_the_callers_scope_is_not_found(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — 404, never 403, and zero rows written. Contrast: granted access, it succeeds."""
    project = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")

    denied = client.post(
        duplicate_path(project.id, scenario.id), headers=as_caller(OUT_OF_SCOPE_USER)
    )

    assert denied.status_code == 404
    assert "Borealis rollout" not in denied.text
    assert count_scenarios(db_session, project.id) == 1

    granted = client.post(
        duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert granted.status_code == 201, granted.text
    assert count_scenarios(db_session, project.id) == 2


# --- K-05 ------------------------------------------------------------------------------------


def test_k_05_duplicating_without_the_scenario_copy_permission_is_forbidden(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — a caller in scope but without `SCENARIO_COPY` gets 403 and writes nothing.
    Contrast: the same caller holding the permission succeeds.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    app.dependency_overrides[get_caller_identity] = lambda: CallerIdentity(
        user_id=IN_SCOPE_USER, permissions=frozenset({Permission.PROJECT_READ})
    )
    try:
        refused = client.post(
            duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
    finally:
        app.dependency_overrides.pop(get_caller_identity, None)

    assert refused.status_code == 403
    assert count_scenarios(db_session, project.id) == 1

    allowed = client.post(
        duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert allowed.status_code == 201, allowed.text
    assert count_scenarios(db_session, project.id) == 2


# --- K-06 ------------------------------------------------------------------------------------


def test_k_06_a_scenario_id_under_the_wrong_project_id_is_not_found(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — the right scenario id under the *wrong* project id is a 404, zero rows written.
    Contrast: the correct project id for that scenario succeeds.
    """
    home_project = make_project(
        db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,)
    )
    other_project = make_project(
        db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, home_project, name="Baseline")

    mismatched = client.post(
        duplicate_path(other_project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert mismatched.status_code == 404
    assert count_scenarios(db_session, home_project.id) == 1
    assert count_scenarios(db_session, other_project.id) == 0

    correct = client.post(
        duplicate_path(home_project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert correct.status_code == 201, correct.text
    assert count_scenarios(db_session, home_project.id) == 2


# --- the naming/retry algorithm itself (gate 1, decision 1), not a K-criterion -----------------


def test_the_duplicate_name_increments_the_suffix_over_repeated_duplicates(
    client: TestClient, db_session: Session
) -> None:
    """Gate 1, decision 1 — `"(copy)"`, then `"(copy 2)"`, `"(copy 3)"`, one per call, because each
    earlier duplicate is itself a sibling the next call must not collide with."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")

    first = client.post(duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    second = client.post(duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    third = client.post(duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert [response.json()["name"] for response in (first, second, third)] == [
        "Baseline (copy)",
        "Baseline (copy 2)",
        "Baseline (copy 3)",
    ]


def test_exhausting_every_candidate_name_is_a_clean_409_not_a_raw_500(
    client_serving_server_errors: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 1, decision 1's pathological case — every candidate up to the bound is already taken.

    The bound is monkeypatched down to 2 so the test does not have to create a thousand rows: with
    `"Baseline (copy)"` and `"Baseline (copy 2)"` both already present, the third call must find no
    candidate and answer a clean `409`, not let `copy_scenario`'s own `IntegrityError` surface as an
    unhandled `500` (gate 1's explicit requirement).
    """
    monkeypatch.setattr(scenario_duplication, "MAX_DUPLICATE_NAME_ATTEMPTS", 2)
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    make_scenario(db_session, project, name="Baseline (copy)")
    make_scenario(db_session, project, name="Baseline (copy 2)")
    before = count_scenarios(db_session, project.id)

    response = client_serving_server_errors.post(
        duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 409, response.text
    assert "Baseline" not in response.text, "NF-11: no scenario name quoted in the refusal"
    assert count_scenarios(db_session, project.id) == before


def test_a_name_collision_in_an_out_of_scope_project_still_answers_404_not_409(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404-before-409 ordering, forced rather than assumed (developer's own named gap).

    K-04/K-06 always exercise a source with no siblings, so a naming collision is never even
    possible in those scenarios — an accidental reorder of "check scope" and "compute the
    candidate name" would pass them anyway. Here the *out-of-scope* project's naming space is
    deliberately exhausted (the bound monkeypatched down to 2, exactly as the 409 test above does)
    before `OUT_OF_SCOPE_USER` ever asks to duplicate it. If scope were checked after (or
    interleaved with) the naming logic, this would leak a `409` — or the database's own duplicate
    name — to a caller who cannot see the project at all. It must still be `404`, indistinguishable
    from a project that does not exist.

    Contrast: the same collision, the same monkeypatched bound, but `IN_SCOPE_USER` — who does get
    the `409` this scenario is built to produce, proving the setup is capable of the conflict at
    all and this isn't vacuously "404 because nothing would have collided anyway."
    """
    monkeypatch.setattr(scenario_duplication, "MAX_DUPLICATE_NAME_ATTEMPTS", 2)
    project = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    make_scenario(db_session, project, name="Baseline (copy)")
    make_scenario(db_session, project, name="Baseline (copy 2)")
    before = count_scenarios(db_session, project.id)

    denied = client.post(
        duplicate_path(project.id, scenario.id), headers=as_caller(OUT_OF_SCOPE_USER)
    )

    assert denied.status_code == 404, denied.text
    assert "Borealis rollout" not in denied.text
    assert "Baseline" not in denied.text
    assert count_scenarios(db_session, project.id) == before

    # Contrast: the same exhausted naming space, but a caller who IS in scope — this must be the
    # 409 the setup is built to produce, or the test proves nothing about ordering.
    granted = client.post(
        duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert granted.status_code == 409, granted.text
    assert count_scenarios(db_session, project.id) == before


# --- R-01 (reviewer, fixed 2026-09-24) — a chained duplicate must never overflow the name column -


def test_r_01_bounded_base_name_leaves_room_for_the_longest_possible_suffix() -> None:
    """R-01, the unit-level claim — `_bounded_base_name` truncates so that the longest suffix this
    module can ever generate (`_longest_possible_suffix`, driven by `MAX_DUPLICATE_NAME_ATTEMPTS`)
    still fits `scenarios.name`'s `String(200)` bound, and leaves a short name untouched.

    Budget = 200 − len(" (copy 1000)") = 187 today; the exact number is derived here rather than
    hardcoded a third time, so a future change to either constant changes this assertion with it.
    """
    max_length = scenario_duplication._scenario_name_max_length()
    budget = max_length - len(scenario_duplication._longest_possible_suffix())

    short_name = "Baseline"
    assert scenario_duplication._bounded_base_name(short_name) == short_name, (
        "a name already inside the budget must be returned unchanged"
    )

    long_name = "A" * (budget + 50)
    bounded = scenario_duplication._bounded_base_name(long_name)
    assert bounded == "A" * budget
    assert len(bounded) == budget
    # Every candidate built from `bounded` — including the longest suffix — must fit the column.
    assert len(bounded) + len(scenario_duplication._longest_possible_suffix()) <= max_length


def test_r_01_a_near_limit_source_name_is_truncated_before_the_suffix_is_appended(
    client: TestClient, db_session: Session
) -> None:
    """R-01 (reviewer) — a source name close to the `String(200)` bound is truncated before any
    suffix is appended, so the write never reaches the database as an unmapped `DataError`
    (SQLSTATE `22001`, deliberately absent from `REFUSAL_BY_SQLSTATE` — see the module docstring
    of `app.data.scenario_duplication`).

    195 `"A"`s plus the shortest suffix (`" (copy)"`, 7 characters) is 202 characters — already over
    the 200-character column before the fix existed. The mutation this test kills is
    `_bounded_base_name` becoming a no-op (or being skipped in `_next_available_name`): without it,
    this call raises an unmapped `DataError` at `session.flush()` and the endpoint answers a raw,
    opaque `500` instead of a clean `201`.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    long_name = "A" * 195
    scenario = make_scenario(db_session, project, name=long_name)

    response = client.post(
        duplicate_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 201, response.text
    name = response.json()["name"]
    assert len(name) <= 200
    assert name.endswith(" (copy)")
    assert name != long_name + " (copy)", (
        "the untruncated candidate is 202 characters and would not have fit the column — this "
        "assertion is void if it were somehow accepted"
    )
    row = scenario_row(db_session, uuid.UUID(response.json()["id"]))
    assert row.name == name


def test_r_01_a_chain_of_duplicates_never_overflows_the_name_column(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — the reviewer's actual failure scenario: not one overlong name, but a *chain* of
    ordinary duplications, each one suffixing the name of the previous duplicate (F-09's own
    "duplicate and modify independently" as an iterative workflow).

    Starting from a 190-character name, the pre-fix behaviour already overflows the column on the
    **second** hop (190 + 7 + 7 = 204 characters) — so ten hops is well past the point a defect
    would surface, not a contrived, unreachable depth. Every hop must answer `201`; none may ever
    answer `500`, and no name may ever exceed the column, however many times it is chained.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="A" * 190)

    current_id = scenario.id
    lengths: list[int] = []
    for hop in range(10):
        response = client.post(
            duplicate_path(project.id, current_id), headers=as_caller(IN_SCOPE_USER)
        )
        assert response.status_code == 201, (hop, response.text)
        name = response.json()["name"]
        assert len(name) <= 200, (hop, name)
        lengths.append(len(name))
        current_id = uuid.UUID(response.json()["id"])

    # The contrast: without the fix, hop 2 alone already overflows (see the docstring above) — so
    # ten successful hops is not a vacuous pass.
    assert len(lengths) == 10
