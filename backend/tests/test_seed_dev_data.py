"""`backend/scripts/seed_dev_data.py` — Issues #145 and #147. One test per acceptance criterion
(K-01..K-06 from #145; the #147 tests below reuse the K-01..K-04 identifiers from that issue's own
criteria, which is a different set of behaviours than #145's K-01/K-02/K-03/K-04 above — the section
headers say which issue each block proves).

The script itself is never imported by `app.*` and is not part of any fixture other tests use —
these tests import it explicitly, the same way they would import any other module under test, and
run it against the same throwaway PostgreSQL container every other test in this suite uses
(`tests/conftest.py`). Nothing here makes the script part of the default `pytest` run of anything
*else*; it is proven exactly once, on purpose, in this file.
"""

from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data.catalog import resolve_rate
from app.domain.scenario_readiness import REQUIRED_SCENARIO_INPUTS
from app.models import (
    AbsenceBudget,
    AbsenceType,
    CatalogDefaultRate,
    CatalogEngagementType,
    CatalogLocation,
    CatalogRole,
    CatalogSeniority,
    CommercialTerms,
    Project,
    ProjectAccess,
    Scenario,
    ScenarioStatus,
    StaffingPosition,
    StoryPointsTerms,
    WorkingCalendar,
    WorkingCalendarDay,
)
from scripts.seed_dev_data import (
    CALENDAR_KRAKOW_NAME,
    CALENDAR_WARSAW_NAME,
    LOCATION_KRAKOW_NAME,
    LOCATION_WARSAW_NAME,
    SCENARIO_SP_DRAFT_NAME,
    SCENARIO_TM_APPROVED_NAME,
    SCENARIO_TM_DRAFT_NAME,
    MissingCallerUserIdError,
    resolve_caller_user_id,
    run_seed,
)
from tests.conftest import as_caller

SEED_CALLER = "synthetic-00000000-0000-4000-8000-000000000004"
"""A caller id chosen for this test suite only — distinct from any string the script might carry as
a hard-coded fallback. If `run_seed` (or a future edit of it) ever stopped granting access to the
`caller_user_id` argument and granted it to some other, invented identity instead, K-01's test below
would find the seeded project invisible under *this* id, exactly the empty-screen symptom the script
exists to fix, and would fail — it would not pass by accident because this id happens to match a
default somewhere."""


def _row_counts(session: Session) -> dict[str, int]:
    """One count per table `run_seed` writes, keyed by a readable label — used to prove a second
    run changes none of them (K-02)."""
    models = {
        "catalog_roles": CatalogRole,
        "catalog_seniorities": CatalogSeniority,
        "catalog_locations": CatalogLocation,
        "catalog_engagement_types": CatalogEngagementType,
        "working_calendar": WorkingCalendar,
        "working_calendar_day": WorkingCalendarDay,
        "catalog_default_rates": CatalogDefaultRate,
        "absence_type": AbsenceType,
        "absence_budget": AbsenceBudget,
        "projects": Project,
        "project_access": ProjectAccess,
        "scenarios": Scenario,
        "commercial_terms": CommercialTerms,
        "staffing_position": StaffingPosition,
    }
    return {
        label: session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()
        for label, model in models.items()
    }


# --- K-01 ------------------------------------------------------------------------------------


def test_k_01_seeded_project_is_visible_to_the_real_dev_identity(
    client: TestClient, db_session: Session
) -> None:
    """The seeded project is readable through `GET /projects` under the *configured* caller id —
    the same identity `run_seed` was given — and through no other.

    Observable: `project_access.user_id` for the seeded project equals `SEED_CALLER`, and
    `GET /projects` with `X-Caller-User-Id: SEED_CALLER` returns it.

    Contrast (the mutation this guards against): a hard-coded or random UUID standing in for the
    parametrised identity would make `project_access.user_id` something other than `SEED_CALLER`,
    and this same request would then return an *empty* list — indistinguishable from today's
    empty-database symptom. The second assertion below (a different, unrelated caller id sees
    nothing) is the direct proof that access is scoped to one specific identity and not open to
    everybody, which is what would also happen if the grant silently went to a wildcard/shared row.
    """
    summary = run_seed(db_session, caller_user_id=SEED_CALLER)

    grant_owner = db_session.execute(
        sa.select(ProjectAccess.user_id).where(ProjectAccess.project_id == summary.project_id)
    ).scalar_one()
    assert grant_owner == SEED_CALLER

    response = client.get("/projects", headers=as_caller(SEED_CALLER))
    assert response.status_code == 200
    returned_ids = [project["id"] for project in response.json()["projects"]]
    assert str(summary.project_id) in returned_ids

    # A caller identity the script was never told about must not see the seeded project — proves
    # the grant is scoped to the configured identity, not to "whoever asks".
    other_response = client.get("/projects", headers=as_caller("somebody-else-entirely"))
    assert str(summary.project_id) not in [
        project["id"] for project in other_response.json()["projects"]
    ]


# --- K-02 ------------------------------------------------------------------------------------


def test_k_02_a_second_run_does_not_duplicate_rows(db_session: Session) -> None:
    """Running the script twice against the same database leaves every row count unchanged —
    including `working_calendar`, which carries no database-level unique key covering its content
    (only a normalised-name index on `name`), so idempotency here rests entirely on this script's
    own lookup-before-insert, not on a constraint refusing the second insert.
    """
    run_seed(db_session, caller_user_id=SEED_CALLER)
    after_first_run = _row_counts(db_session)
    assert after_first_run["working_calendar"] == 2, "both seeded calendars must exist once"

    run_seed(db_session, caller_user_id=SEED_CALLER)
    after_second_run = _row_counts(db_session)

    assert after_second_run == after_first_run


# --- K-03 ------------------------------------------------------------------------------------


def test_k_03_a_gap_in_the_seeded_rate_windows_is_reachable(db_session: Session) -> None:
    """The two seeded rate windows for the Warsaw dimension tuple leave March 2026 uncovered —
    `resolve_rate` must answer `None` for a day inside the gap and a real row for a day inside
    each window either side of it, so the "no rate for this day" resolution state is exercisable
    against seeded data (not only against a purpose-built test fixture).
    """
    run_seed(db_session, caller_user_id=SEED_CALLER)

    role_id = db_session.execute(
        sa.select(CatalogRole.id).where(CatalogRole.name == "Seed Role Backend Developer")
    ).scalar_one()
    seniority_id = db_session.execute(
        sa.select(CatalogSeniority.id).where(CatalogSeniority.name == "Seed Seniority Senior")
    ).scalar_one()
    location_id = db_session.execute(
        sa.select(CatalogLocation.id).where(CatalogLocation.name == LOCATION_WARSAW_NAME)
    ).scalar_one()
    engagement_id = db_session.execute(
        sa.select(CatalogEngagementType.id).where(
            CatalogEngagementType.name == "Seed Engagement Type B2B"
        )
    ).scalar_one()

    dimensions = {
        "role_id": role_id,
        "seniority_id": seniority_id,
        "location_id": location_id,
        "engagement_type_id": engagement_id,
        "vendor_id": None,
    }

    in_gap = resolve_rate(db_session, on_date=date(2026, 3, 15), **dimensions)
    assert in_gap is None, "March 2026 must fall inside the seeded gap between the two windows"

    before_gap = resolve_rate(db_session, on_date=date(2026, 1, 15), **dimensions)
    after_gap = resolve_rate(db_session, on_date=date(2026, 4, 15), **dimensions)
    assert before_gap is not None
    assert after_gap is not None
    assert before_gap.id != after_gap.id


# --- K-04 and K-06 -----------------------------------------------------------------------------
#
# Both criteria are observable on the same response (`GET .../staffing-positions`): one seeded
# position sits in a location pointing at the Warsaw calendar with a covering budget, the other at
# the Krakow calendar with no budget for its (calendar, engagement type) pair. Two separate test
# functions, one per criterion, sharing the fetch so a reader can see exactly which field each
# criterion is about.


def _seeded_positions_response(client: TestClient, db_session: Session) -> dict:
    summary = run_seed(db_session, caller_user_id=SEED_CALLER)
    scenario_id = summary.scenario_ids["tm_draft"]
    response = client.get(
        f"/projects/{summary.project_id}/scenarios/{scenario_id}/staffing-positions",
        headers=as_caller(SEED_CALLER),
    )
    assert response.status_code == 200
    return response.json()


def _allocation_for_location(
    payload: dict, db_session: Session, *, location_name: str, period_month: str
) -> dict:
    location_id = str(
        db_session.execute(
            sa.select(CatalogLocation.id).where(CatalogLocation.name == location_name)
        ).scalar_one()
    )
    position = next(p for p in payload["positions"] if p["location_id"] == location_id)
    return next(a for a in position["allocations"] if a["period_month"] == period_month)


def test_k_04_both_absence_budget_states_are_reachable_simultaneously(
    client: TestClient, db_session: Session
) -> None:
    """Among the (calendar, engagement type) pairs the seeded positions actually use, the Warsaw
    pair has a covering `absence_budget` row (`"resolved"`) and the Krakow pair deliberately does
    not (`"no_budget"`) — both reachable from one scenario's response at once.
    """
    payload = _seeded_positions_response(client, db_session)

    warsaw_allocation = _allocation_for_location(
        payload, db_session, location_name=LOCATION_WARSAW_NAME, period_month="2026-04-01"
    )
    krakow_allocation = _allocation_for_location(
        payload, db_session, location_name=LOCATION_KRAKOW_NAME, period_month="2026-04-01"
    )

    assert warsaw_allocation["absence_budget_state"] == "resolved"
    assert krakow_allocation["absence_budget_state"] == "no_budget"


def test_k_06_two_calendars_with_different_week_patterns_produce_a_capacity_difference(
    client: TestClient, db_session: Session
) -> None:
    """The two seeded positions share the same headcount, the same period and the same allocation
    figures — the only thing that can make their `derived_capacity_hours` differ is the calendar
    each one's location points at (different week pattern, different standard day, a different
    exceptional day each).
    """
    payload = _seeded_positions_response(client, db_session)

    warsaw_allocation = _allocation_for_location(
        payload, db_session, location_name=LOCATION_WARSAW_NAME, period_month="2026-04-01"
    )
    krakow_allocation = _allocation_for_location(
        payload, db_session, location_name=LOCATION_KRAKOW_NAME, period_month="2026-04-01"
    )

    assert warsaw_allocation["derived_capacity_state"] == "resolved"
    assert krakow_allocation["derived_capacity_state"] == "resolved"
    assert (
        warsaw_allocation["derived_capacity_hours"] != krakow_allocation["derived_capacity_hours"]
    )

    # And the calendars named in the response really are the two distinct seeded ones.
    calendar_names = {
        warsaw_allocation["derived_capacity_source"]["calendar_name"],
        krakow_allocation["derived_capacity_source"]["calendar_name"],
    }
    assert calendar_names == {CALENDAR_WARSAW_NAME, CALENDAR_KRAKOW_NAME}


# --- K-05 ------------------------------------------------------------------------------------


def test_k_05_three_scenarios_span_two_commercial_models_and_both_statuses(
    db_session: Session,
) -> None:
    """One project holds three scenarios: two distinct commercial models (Time & Material, Story
    Points — never Fixed Price, which has no implementation in this backend) and both scenario
    statuses (draft and approved), the latter carrying a real approval snapshot rather than a
    fixture writing the status directly.
    """
    summary = run_seed(db_session, caller_user_id=SEED_CALLER)

    scenario_ids = list(summary.scenario_ids.values())
    assert len(scenario_ids) == 3

    statuses = db_session.execute(
        sa.select(Scenario.status).where(Scenario.id.in_(scenario_ids))
    ).scalars().all()
    assert sorted(status.value for status in statuses) == ["approved", "draft", "draft"]

    model_types = set(
        db_session.execute(
            sa.select(CommercialTerms.model_type).where(
                CommercialTerms.scenario_id.in_(scenario_ids)
            )
        ).scalars()
    )
    assert model_types == {"time_and_material", "story_points"}
    assert "fixed_price" not in model_types

    approved_scenario_id = summary.scenario_ids["tm_approved"]
    snapshot_rows = db_session.execute(
        sa.text(
            "SELECT count(*) FROM approved_snapshot_working_calendar WHERE scenario_id = :sid"
        ),
        {"sid": approved_scenario_id},
    ).scalar_one()
    assert snapshot_rows > 0, "the approval must have actually frozen a calendar, not an empty plan"


# --- Issue #147: the six `REQUIRED_SCENARIO_INPUTS` are set on every seeded scenario -----------
#
# `backend/scripts/seed_dev_data.py` (Issue #145) seeded three scenarios without ever setting
# `start_date`, `end_date`, `working_calendar`, `full_time_hours_per_week`, `currency` or
# `target_margin_percent` — the six columns `app.domain.scenario_readiness.REQUIRED_SCENARIO_
# INPUTS` names — so every seeded scenario, including the one deliberately approved, reported
# `ready_for_approval=False`. These four tests reuse the identifiers K-01..K-04 from Issue #147's
# own acceptance criteria (a different set of behaviours from #145's K-01..K-04 above).


def _scenario_items_by_id(client: TestClient, project_id: object, *, caller: str) -> dict:
    response = client.get(f"/projects/{project_id}", headers=as_caller(caller))
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()["scenarios"]}


def test_k147_01_both_draft_scenarios_are_ready_independently_of_each_other(
    client: TestClient, db_session: Session
) -> None:
    """Both drafts carry all six required inputs and are reported ready — and the readiness check
    runs per scenario row, not once for the whole project: making one draft incomplete directly on
    its own row must not affect the other draft's report.
    """
    summary = run_seed(db_session, caller_user_id=SEED_CALLER)
    tm_draft_id = str(summary.scenario_ids["tm_draft"])
    sp_draft_id = str(summary.scenario_ids["sp_draft"])

    scenarios = _scenario_items_by_id(client, summary.project_id, caller=SEED_CALLER)
    assert scenarios[tm_draft_id]["missing_inputs"] == []
    assert scenarios[tm_draft_id]["ready_for_approval"] is True
    assert scenarios[sp_draft_id]["missing_inputs"] == []
    assert scenarios[sp_draft_id]["ready_for_approval"] is True

    # Contrast: blank one field on the TM draft alone (simulating an incomplete row), on the
    # database directly — never through the seed script. Only that scenario, and only that one
    # field, must show up as missing; the untouched SP draft must stay fully ready.
    db_session.execute(
        sa.update(Scenario).where(Scenario.id == summary.scenario_ids["tm_draft"]).values(
            currency=None
        )
    )
    db_session.commit()

    scenarios_after = _scenario_items_by_id(client, summary.project_id, caller=SEED_CALLER)
    assert scenarios_after[tm_draft_id]["missing_inputs"] == ["currency"]
    assert scenarios_after[tm_draft_id]["ready_for_approval"] is False
    assert scenarios_after[sp_draft_id]["missing_inputs"] == []
    assert scenarios_after[sp_draft_id]["ready_for_approval"] is True


def test_k147_02_a_second_run_repairs_a_scenario_seeded_by_the_old_script(
    client: TestClient, db_session: Session
) -> None:
    """A `Scenario` row with the natural key of a scenario this script seeds, but with every one
    of the six assumption columns `NULL` — the exact shape a database seeded by the pre-fix
    version of this script leaves behind — is repaired (not left alone, and not duplicated) by a
    subsequent run of the fixed script.
    """
    summary = run_seed(db_session, caller_user_id=SEED_CALLER)

    db_session.execute(
        sa.update(Scenario)
        .where(Scenario.id == summary.scenario_ids["sp_draft"])
        .values(
            start_date=None,
            end_date=None,
            working_calendar=None,
            full_time_hours_per_week=None,
            currency=None,
            target_margin_percent=None,
        )
    )
    db_session.commit()

    bugged_row = db_session.execute(
        sa.select(Scenario).where(Scenario.id == summary.scenario_ids["sp_draft"])
    ).scalar_one()
    for field_name in REQUIRED_SCENARIO_INPUTS:
        assert getattr(bugged_row, field_name) is None, f"setup: {field_name} must start NULL"

    run_seed(db_session, caller_user_id=SEED_CALLER)

    repaired_row = db_session.execute(
        sa.select(Scenario).where(Scenario.id == summary.scenario_ids["sp_draft"])
    ).scalar_one()
    for field_name in REQUIRED_SCENARIO_INPUTS:
        assert getattr(repaired_row, field_name) is not None, f"not repaired: {field_name}"

    scenarios = _scenario_items_by_id(client, summary.project_id, caller=SEED_CALLER)
    repaired_item = scenarios[str(summary.scenario_ids["sp_draft"])]
    assert repaired_item["missing_inputs"] == []
    assert repaired_item["ready_for_approval"] is True

    scenario_count_after_repair = db_session.execute(
        sa.select(sa.func.count()).select_from(Scenario)
    ).scalar_one()

    # A further run — now against an already-repaired, fully-seeded database — must not duplicate
    # the row or raise (matches #145's own K-02 idempotency guarantee, extended to this column set).
    run_seed(db_session, caller_user_id=SEED_CALLER)
    scenario_count_after_third_run = db_session.execute(
        sa.select(sa.func.count()).select_from(Scenario)
    ).scalar_one()
    assert scenario_count_after_third_run == scenario_count_after_repair == 3


def test_k147_02c_never_backfills_an_already_approved_row(
    client: TestClient, db_session: Session
) -> None:
    """Rule 7 (ADR-0004): a write to an approved calculation is refused at the data-access layer.

    A database left behind by the pre-#147 script can have `Seed Scenario TM Approved` already
    `approved` with every assumption column still `NULL` (the old script approved it before this
    fix ever set them). A re-seed must not silently patch an approved row's own columns — it must
    leave the gap alone, not raise, and not duplicate the row.
    """
    summary = run_seed(db_session, caller_user_id=SEED_CALLER)

    approved_id = summary.scenario_ids["tm_approved"]
    approved_before = db_session.execute(
        sa.select(Scenario).where(Scenario.id == approved_id)
    ).scalar_one()
    assert approved_before.status == ScenarioStatus.APPROVED, "setup: must already be approved"

    db_session.execute(
        sa.update(Scenario)
        .where(Scenario.id == approved_id)
        .values(
            start_date=None,
            end_date=None,
            working_calendar=None,
            full_time_hours_per_week=None,
            currency=None,
            target_margin_percent=None,
        )
    )
    db_session.commit()

    run_seed(db_session, caller_user_id=SEED_CALLER)

    still_incomplete = db_session.execute(
        sa.select(Scenario).where(Scenario.id == approved_id)
    ).scalar_one()
    for field_name in REQUIRED_SCENARIO_INPUTS:
        assert getattr(still_incomplete, field_name) is None, (
            f"an approved row's {field_name} must never be silently backfilled"
        )
    assert still_incomplete.status == ScenarioStatus.APPROVED

    scenario_count = db_session.execute(
        sa.select(sa.func.count()).select_from(Scenario)
    ).scalar_one()
    assert scenario_count == 3, "must not duplicate the row while refusing to repair it"


def test_k147_02b_a_second_run_does_not_clobber_a_manually_edited_value(
    db_session: Session,
) -> None:
    """QA contrast (Issue #147): `_get_or_create_scenario`'s own docstring claims the found-row
    branch backfills only the `NULL` columns, "never the ones already set (a manual edit on a real
    dev database must survive a re-seed)" — but `test_k147_02` above only ever starts from a row
    with all six columns `NULL`, so it cannot tell a conditional (`if ... is None`) backfill apart
    from an unconditional overwrite: both would leave that row fully non-null after a second run.

    Mutation record: changing the backfill loop to `setattr(existing, field_name, value)`
    unconditionally (dropping the `is None` guard) survived the full suite green before this test
    was added — the flaw was in the test, not in the code, which already guards this correctly.

    This test sets one column to a value a developer could plausibly have hand-edited on a local
    dev database — deliberately different from every constant this script writes — and asserts a
    second run leaves exactly that value alone, while every other, still-`NULL` column gets
    repaired around it in the same run.
    """
    summary = run_seed(db_session, caller_user_id=SEED_CALLER)

    db_session.execute(
        sa.update(Scenario)
        .where(Scenario.id == summary.scenario_ids["sp_draft"])
        .values(
            currency="EUR",  # manual edit — the script only ever writes "PLN"
            start_date=None,
            end_date=None,
            working_calendar=None,
            full_time_hours_per_week=None,
            target_margin_percent=None,
        )
    )
    db_session.commit()

    run_seed(db_session, caller_user_id=SEED_CALLER)

    row_after_second_run = db_session.execute(
        sa.select(Scenario).where(Scenario.id == summary.scenario_ids["sp_draft"])
    ).scalar_one()
    assert row_after_second_run.currency == "EUR", (
        "a re-seed must not clobber a manually-edited, already-non-null column"
    )
    for field_name in REQUIRED_SCENARIO_INPUTS:
        if field_name == "currency":
            continue
        assert getattr(row_after_second_run, field_name) is not None, (
            f"not repaired: {field_name}"
        )


def test_k147_03_scenario_currency_and_story_points_currency_are_separate_columns(
    db_session: Session,
) -> None:
    """`Scenario.currency` (the assumption this task fixes) and `story_points_terms.currency`
    (the Story Points revenue calculation's own, unrelated column, already seeded correctly by
    Issue #145) are read from two separate columns and must not collide.
    """
    run_seed(db_session, caller_user_id=SEED_CALLER)

    scenario_currencies = dict(
        db_session.execute(
            sa.select(Scenario.name, Scenario.currency).where(
                Scenario.name.in_(
                    [SCENARIO_TM_DRAFT_NAME, SCENARIO_SP_DRAFT_NAME, SCENARIO_TM_APPROVED_NAME]
                )
            )
        ).all()
    )
    assert scenario_currencies == {
        SCENARIO_TM_DRAFT_NAME: "PLN",
        SCENARIO_SP_DRAFT_NAME: "PLN",
        SCENARIO_TM_APPROVED_NAME: "PLN",
    }, "K-01 requires Scenario.currency to be set (non-null) on every seeded scenario"

    sp_scenario_id = db_session.execute(
        sa.select(Scenario.id).where(Scenario.name == SCENARIO_SP_DRAFT_NAME)
    ).scalar_one()
    story_points_currency = db_session.execute(
        sa.select(StoryPointsTerms.currency)
        .join(CommercialTerms, CommercialTerms.id == StoryPointsTerms.commercial_terms_id)
        .where(CommercialTerms.scenario_id == sp_scenario_id)
    ).scalar_one()
    assert story_points_currency == "PLN", (
        "story_points_terms.currency must stay exactly what #145 already seeds for it, unchanged "
        "by this task's Scenario.currency fix"
    )


def test_k147_04_the_approved_scenario_is_reported_ready_and_approved_at_once(
    client: TestClient, db_session: Session
) -> None:
    """`Seed Scenario TM Approved` reports `status="Approved"` and `ready_for_approval=True` /
    `missing_inputs=[]` simultaneously — approval status never masks an incomplete scenario.

    Contrast: omitting one required field only on this scenario's row must flip
    `ready_for_approval` back to `False` with exactly that field listed, while `status` stays
    `"Approved"` — proving readiness is a function of completeness alone, never of status.
    """
    summary = run_seed(db_session, caller_user_id=SEED_CALLER)
    approved_id = str(summary.scenario_ids["tm_approved"])

    scenarios = _scenario_items_by_id(client, summary.project_id, caller=SEED_CALLER)
    approved_item = scenarios[approved_id]
    assert approved_item["status"] == "Approved"
    assert approved_item["missing_inputs"] == []
    assert approved_item["ready_for_approval"] is True

    db_session.execute(
        sa.update(Scenario)
        .where(Scenario.id == summary.scenario_ids["tm_approved"])
        .values(working_calendar=None)
    )
    db_session.commit()

    scenarios_after = _scenario_items_by_id(client, summary.project_id, caller=SEED_CALLER)
    approved_item_after = scenarios_after[approved_id]
    assert approved_item_after["status"] == "Approved"
    assert approved_item_after["missing_inputs"] == ["working_calendar"]
    assert approved_item_after["ready_for_approval"] is False


# --- resolve_caller_user_id (no database) -----------------------------------------------------


def test_resolve_caller_user_id_prefers_the_cli_argument(tmp_path: Path) -> None:
    value = resolve_caller_user_id("from-cli", env={"VITE_CALLER_USER_ID": "from-env"})
    assert value == "from-cli"


def test_resolve_caller_user_id_falls_back_to_the_environment_variable() -> None:
    value = resolve_caller_user_id(None, env={"VITE_CALLER_USER_ID": "from-env"})
    assert value == "from-env"


def test_resolve_caller_user_id_falls_back_to_frontend_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("VITE_API_BASE_URL=http://localhost:8000\nVITE_CALLER_USER_ID=from-file\n")

    value = resolve_caller_user_id(None, env={}, frontend_env_path=env_file)

    assert value == "from-file"


def test_resolve_caller_user_id_refuses_when_nothing_is_configured(tmp_path: Path) -> None:
    """The exact shape `frontend/.env.example` ships — `VITE_CALLER_USER_ID=` with nothing after
    the `=` — must refuse to seed, not silently seed under the empty string."""
    env_file = tmp_path / ".env"
    env_file.write_text("VITE_CALLER_USER_ID=\n")

    with pytest.raises(MissingCallerUserIdError):
        resolve_caller_user_id(None, env={}, frontend_env_path=env_file)


def test_resolve_caller_user_id_refuses_when_no_source_exists_at_all() -> None:
    with pytest.raises(MissingCallerUserIdError):
        resolve_caller_user_id(None, env={}, frontend_env_path=None)
