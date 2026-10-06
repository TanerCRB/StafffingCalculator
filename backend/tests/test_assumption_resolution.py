"""SC-1-10, K-01 and K-02 — the resolution rule scenario → project → organisation (F-02, ADR-0012).

Proven on **two** assumptions with the same rule (gate 1, Q-2): the target margin and the overload
threshold. A mechanism proven on one field cannot be told apart from an implementation shaped around
that field, so every criterion here runs on both.

- **K-01** no override anywhere → the organisation's default, source `"organization"`; nothing on
  any level → the named state `"no_value"`, value `"n/a"`, no source — never `0`, never an
  exception.
- **K-02** precedence scenario → project → organisation, with the level named; `0` is a value, not
  an absence (the "truthiness coalescing" mutation `scenario or project or organisation` loses it).

Also here: the two Story criteria that are properties of the reader rather than of the rule — an
override reaches only the project/scenario it was set on, and a scenario outside the caller's scope
is `404` (never `403`) with no source leaking a foreign project's value.

**Fixture-only levels, named rather than hidden** (gate 1, Q-3): the organisation's defaults and the
scenario-level override have no write path in this task, so they are written by fixtures. The
project level has one (`PATCH /projects/{id}`), and its criterion (K-07) uses it; here it is written
by a fixture so the rule's proof does not depend on the edit endpoint.
"""

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.scenarios import SCENARIO_NOT_FOUND_DETAIL
from app.core.identity import Permission
from app.core.money import NOT_APPLICABLE
from app.domain.assumptions import (
    NO_VALUE,
    ORGANIZATION,
    PROJECT,
    RESOLVABLE_ASSUMPTIONS,
    RESOLVED,
    SCENARIO,
    resolve,
)
from app.models import OrganizationDefaults
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    assumptions_path,
    caller_holding,
    make_project,
    make_scenario,
    set_organization_defaults,
    set_project_overrides,
)

MARGIN = "target_margin_percent"
THRESHOLD = "overload_threshold_percent"


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


# --- K-01 -----------------------------------------------------------------------------------------


def test_k_01_a_scenario_without_overrides_inherits_the_organization_default_and_names_its_source(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — no override on the scenario or the project: the organisation's value, source named.

    Contrast in the same test: the *same* scenario read before the organisation has any defaults row
    is `no_value` on both fields — so the values below come from the organisation row and from
    nowhere else (not from a constant, not from another level).
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")

    before = _read(client, project.id, scenario.id)
    for field in (MARGIN, THRESHOLD):
        assert before[field] == {"value": NOT_APPLICABLE, "state": NO_VALUE, "source": None}

    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("18.250"),
        overload_threshold_percent=Decimal("120.000"),
    )

    after = _read(client, project.id, scenario.id)
    assert after[MARGIN] == {"value": "18.250", "state": RESOLVED, "source": ORGANIZATION}
    assert after[THRESHOLD] == {"value": "120.000", "state": RESOLVED, "source": ORGANIZATION}


def test_k_01_a_field_with_no_value_on_any_level_is_the_named_state_not_zero_and_not_an_error(
    client: TestClient, db_session: Session
) -> None:
    """K-01, the other half — "no value" is a state, never `0`, never a `500`.

    Two shapes of "nothing", both of which must resolve the same way: no organisation row at all,
    and an organisation row whose column is `NULL`. The second is reached by giving the row a
    margin and no threshold, so the same response also shows one field resolved and the other not —
    which an implementation answering "the organisation has a row, so everything is resolved" fails.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    set_organization_defaults(db_session, target_margin_percent=Decimal("18.250"))

    body = _read(client, project.id, scenario.id)

    assert body[MARGIN]["state"] == RESOLVED
    assert body[THRESHOLD] == {"value": NOT_APPLICABLE, "state": NO_VALUE, "source": None}
    assert body[THRESHOLD]["value"] not in ("0", "0.000", 0, None)


# --- K-02 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", RESOLVABLE_ASSUMPTIONS)
def test_k_02_the_nearest_override_wins_and_names_the_level_it_came_from(
    client: TestClient, db_session: Session, field: str
) -> None:
    """K-02 — scenario beats project beats organisation, and the source says which one won.

    Three different values, one per level, so every answer is attributable to exactly one level.
    The override is then removed level by level, nearest first: each removal must uncover the next
    level's value **and** its name (the Story's contrast: "after removing the scenario override the
    project's value returns with source `project`").
    """
    values = {
        ORGANIZATION: Decimal("110.000"),
        PROJECT: Decimal("125.500"),
        SCENARIO: Decimal("140.250"),
    }
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", **{field: values[SCENARIO]})
    set_organization_defaults(db_session, **{field: values[ORGANIZATION]})
    set_project_overrides(db_session, project.id, **{field: values[PROJECT]})

    assert _read(client, project.id, scenario.id)[field] == {
        "value": "140.250",
        "state": RESOLVED,
        "source": SCENARIO,
    }

    db_session.execute(
        sa.text(f"UPDATE scenarios SET {field} = NULL WHERE id = :id"), {"id": scenario.id}
    )
    db_session.expire_all()
    assert _read(client, project.id, scenario.id)[field] == {
        "value": "125.500",
        "state": RESOLVED,
        "source": PROJECT,
    }

    set_project_overrides(db_session, project.id)
    assert _read(client, project.id, scenario.id)[field] == {
        "value": "110.000",
        "state": RESOLVED,
        "source": ORGANIZATION,
    }


def test_k_02_zero_is_a_value_that_stops_the_search_not_an_absence(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — `0` on a nearer level wins over a non-zero value further out.

    On the margin only: `0` is not a legal threshold (K-09, gate 1 G-2), so the threshold has no
    zero to lose. Two placements — `0` on the scenario over a project value, and `0` on the project
    over an organisation value — because truthiness coalescing can be written per pair and a single
    placement would let one of the pairs keep it.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    on_scenario = make_scenario(
        db_session, project, name="Zero on the scenario", target_margin_percent=Decimal("0")
    )
    on_project = make_scenario(db_session, project, name="Zero on the project")
    set_organization_defaults(db_session, target_margin_percent=Decimal("18.250"))
    set_project_overrides(db_session, project.id, target_margin_percent=Decimal("0.000"))

    assert _read(client, project.id, on_scenario.id)[MARGIN] == {
        "value": "0.000",
        "state": RESOLVED,
        "source": SCENARIO,
    }
    assert _read(client, project.id, on_project.id)[MARGIN] == {
        "value": "0.000",
        "state": RESOLVED,
        "source": PROJECT,
    }


@pytest.mark.parametrize(
    ("levels", "expected"),
    [
        ((Decimal("0"), Decimal("5"), Decimal("9")), (Decimal("0"), SCENARIO)),
        ((None, Decimal("0"), Decimal("9")), (Decimal("0"), PROJECT)),
        ((None, None, Decimal("0")), (Decimal("0"), ORGANIZATION)),
        ((Decimal("1"), None, None), (Decimal("1"), SCENARIO)),
        ((None, None, None), (NOT_APPLICABLE, None)),
    ],
)
def test_k_02_the_rule_itself_treats_only_none_as_absent(
    levels: tuple[Decimal | None, Decimal | None, Decimal | None],
    expected: tuple[Decimal | str, str | None],
) -> None:
    """K-02 at the level of the pure rule — every placement of `0`, and the empty chain."""
    resolved = resolve(*levels)
    assert (resolved.value, resolved.source) == expected
    assert resolved.state == (NO_VALUE if expected[1] is None else RESOLVED)


# --- the Story's criterion 3: an override reaches only where it was set ---------------------------


def test_an_override_reaches_only_the_project_or_scenario_it_was_set_on(
    client: TestClient, db_session: Session
) -> None:
    """A project override does not reach another project's scenario; a scenario override does not
    reach its sibling. Both siblings and the foreign scenario still see the organisation's value
    where nothing nearer is set."""
    set_organization_defaults(
        db_session,
        target_margin_percent=Decimal("10.000"),
        overload_threshold_percent=Decimal("100.000"),
    )
    project_a = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    project_b = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    x = make_scenario(
        db_session, project_a, name="X", overload_threshold_percent=Decimal("90.000")
    )
    y = make_scenario(db_session, project_a, name="Y")
    in_b = make_scenario(db_session, project_b, name="B")
    set_project_overrides(db_session, project_a.id, target_margin_percent=Decimal("12.500"))

    x_body = _read(client, project_a.id, x.id)
    y_body = _read(client, project_a.id, y.id)
    b_body = _read(client, project_b.id, in_b.id)

    assert (x_body[MARGIN]["source"], x_body[THRESHOLD]["source"]) == (PROJECT, SCENARIO)
    assert (y_body[MARGIN]["source"], y_body[THRESHOLD]["source"]) == (PROJECT, ORGANIZATION)
    assert y_body[THRESHOLD]["value"] == "100.000", "X's scenario override reached its sibling Y"
    assert (b_body[MARGIN]["value"], b_body[MARGIN]["source"]) == ("10.000", ORGANIZATION), (
        "project A's override reached a scenario of project B"
    )


# --- the Story's criterion 7: the access boundary -------------------------------------------------


def test_resolved_assumptions_of_a_scenario_outside_the_callers_scope_are_not_found(
    client: TestClient, db_session: Session
) -> None:
    """`404`, never `403`, and the same body for three different reasons (ADR-0005).

    The foreign project carries an override, so a leak would have a value to leak. Contrast: its
    owner reads it with a `200` naming the project as the source — so the `404`s are about scope
    and not about a broken endpoint.
    """
    foreign = make_project(db_session, name="Borealis rollout", accessible_to=(OUT_OF_SCOPE_USER,))
    foreign_scenario = make_scenario(db_session, foreign, name="Theirs")
    set_project_overrides(db_session, foreign.id, target_margin_percent=Decimal("33.000"))
    mine = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    with caller_holding(
        Permission.PROJECT_READ,
        Permission.SCENARIO_ASSUMPTIONS_READ,
        Permission.ORGANIZATION_DEFAULTS_READ,
    ):
        answers = [
            # the foreign scenario through its own project
            client.get(
                assumptions_path(foreign.id, foreign_scenario.id), headers=as_caller(IN_SCOPE_USER)
            ),
            # the foreign scenario addressed through a project the caller *can* see
            client.get(
                assumptions_path(mine.id, foreign_scenario.id), headers=as_caller(IN_SCOPE_USER)
            ),
            # a scenario that does not exist
            client.get(assumptions_path(mine.id, uuid.uuid4()), headers=as_caller(IN_SCOPE_USER)),
        ]
    for response in answers:
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": SCENARIO_NOT_FOUND_DETAIL}
        assert "33" not in response.text

    with caller_holding(
        Permission.PROJECT_READ,
        Permission.SCENARIO_ASSUMPTIONS_READ,
        user_id=OUT_OF_SCOPE_USER,
    ):
        owner = client.get(
            assumptions_path(foreign.id, foreign_scenario.id), headers=as_caller(OUT_OF_SCOPE_USER)
        )
    assert owner.status_code == 200, owner.text
    assert owner.json()[MARGIN] == {"value": "33.000", "state": RESOLVED, "source": PROJECT}


def test_the_assumptions_endpoint_declares_a_permission_and_refuses_a_caller_without_it(
    client: TestClient, db_session: Session
) -> None:
    """Deny by default: without `PROJECT_READ` the answer is `403`, before the database is asked."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")

    with caller_holding(Permission.PROJECT_EDIT):
        denied = client.get(assumptions_path(project.id, scenario.id))
    with caller_holding(Permission.PROJECT_READ):
        project_read_only = client.get(assumptions_path(project.id, scenario.id))
    with caller_holding(Permission.SCENARIO_ASSUMPTIONS_READ):
        assumptions_read_only = client.get(assumptions_path(project.id, scenario.id))
    with caller_holding(Permission.PROJECT_READ, Permission.SCENARIO_ASSUMPTIONS_READ):
        allowed = client.get(assumptions_path(project.id, scenario.id))

    assert denied.status_code == 403, denied.text
    assert project_read_only.status_code == 403, project_read_only.text
    assert assumptions_read_only.status_code == 403, assumptions_read_only.text
    assert allowed.status_code == 200, allowed.text


# --- schema: one name on three levels, and one organisation row ----------------------------------


def test_every_resolvable_assumption_is_a_column_on_all_three_levels_and_in_the_snapshot(
    db_session: Session,
) -> None:
    """The drift guard for `RESOLVABLE_ASSUMPTIONS`: the rule reads one column name per level, so a
    name missing on any level (or in the frozen copy) would resolve that level to "absent"
    silently. Asked of the migrated database, not of the models."""
    tables = (
        "scenarios",
        "projects",
        "organization_defaults",
        "approved_snapshot_organization_defaults",
    )
    present = set(
        db_session.execute(
            sa.text(
                "SELECT table_name || '.' || column_name FROM information_schema.columns"
                " WHERE table_name = ANY(:tables) AND column_name = ANY(:columns)"
            ),
            {"tables": list(tables), "columns": list(RESOLVABLE_ASSUMPTIONS)},
        ).scalars()
    )
    assert present == {f"{table}.{name}" for table in tables for name in RESOLVABLE_ASSUMPTIONS}


def test_the_organization_defaults_table_admits_exactly_one_row(db_session: Session) -> None:
    """"One row, enforced by the database" (gate 1): a second row is refused by the primary key,
    a row under any other key by the CHECK — neither by application code."""
    set_organization_defaults(db_session, target_margin_percent=Decimal("18.250"))

    refusals = ((1, "pk_organization_defaults"), (2, "ck_organization_defaults_singleton"))
    for key, constraint in refusals:
        savepoint = db_session.begin_nested()
        with pytest.raises(IntegrityError) as refused:
            db_session.execute(sa.insert(OrganizationDefaults).values(id=key))
        savepoint.rollback()
        assert constraint in str(refused.value.orig), refused.value.orig

    count = db_session.execute(
        sa.select(sa.func.count()).select_from(OrganizationDefaults)
    ).scalar_one()
    assert count == 1
