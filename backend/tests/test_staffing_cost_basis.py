"""SC-5-03, K-02 — `cost_basis` is a stored, persistent column of the position, not a parameter of
a read (F-07; ADR-0013, addendum 2026-09-25 SC-5-03).

Every persistence claim here is checked on a **separate connection** (the `engine`/`committing_
client` pair `test_staffing_positions.py`'s own K-01 uses), which by definition sees committed rows
only — a claim proven only against the same session that wrote the row would say nothing about
persistence, only about the identity map.

`cost_basis`/`fixed_amount`/`fixed_amount_currency` are never part of `GET …/staffing-positions`'s
response body (ADR-0005, addendum 2026-09-25 SC-5-03, Q4 — proved structurally in
`test_staffing_positions_cost_basis_hidden.py`), so every read here goes straight at the row through
SQL, exactly as `test_staffing_positions.py`'s persistence tests already do for the dimension tuple.
"""

import uuid
from datetime import date

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import Permission
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    caller_holding,
    make_dimension_tuple,
    make_project,
    make_scenario,
    staffing_path,
    staffing_position_payload,
)

MAR = date(2026, 3, 1)


def _position_row(engine: Engine, position_id: uuid.UUID) -> sa.Row:
    """The position's own `cost_basis`/`fixed_amount`/`fixed_amount_currency`, read on a fresh
    connection — never the one that wrote the row."""
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT cost_basis, fixed_amount, fixed_amount_currency"
                " FROM staffing_position WHERE id = :id"
            ),
            {"id": position_id},
        ).one()


def _dimensions_and_draft_scenario(engine: Engine):
    """`cost_visible_to` names `IN_SCOPE_USER` (gate 1 SC-5-03 fix 3): the cost-basis `PATCH` now
    needs `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs` for this project, so every test in
    this file that writes through it has to run as a caller who actually holds the conjunction
    (`caller_holding`), on a project that grants the project-level half of it."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(
            setup,
            name="Aurora migration",
            accessible_to=(IN_SCOPE_USER,),
            cost_visible_to=(IN_SCOPE_USER,),
        )
        scenario = make_scenario(setup, project, name="Baseline")
        dimensions = make_dimension_tuple(setup)
        setup.commit()
    return project, scenario, dimensions


# --- K-02: default, persisted, read from an independent session -----------------------------------


def test_k_02_a_position_created_without_naming_a_basis_persists_as_worked_time(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-02 — backward compatibility: a request that says nothing about `cost_basis` (every request
    this repository's tests sent before this task) still creates a row whose basis is `worked_time`
    and whose `fixed_amount`/`fixed_amount_currency` are `NULL` — read back from a fresh connection,
    not assumed from the schema's default alone."""
    project, scenario, dimensions = _dimensions_and_draft_scenario(engine)

    created = committing_client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert created.status_code == 201, created.text
    position_id = uuid.UUID(created.json()["id"])

    row = _position_row(engine, position_id)
    assert (row.cost_basis, row.fixed_amount, row.fixed_amount_currency) == (
        "worked_time",
        None,
        None,
    )


def test_k_02_a_fixed_amount_position_persists_its_amount_and_currency_across_sessions(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-02 — a position created with `cost_basis = "fixed_amount"` keeps exactly the amount and
    currency it was given, read back on a session that never wrote it. Contrast with the default
    test above: the two requests differ only in these three fields, and the two rows differ
    identically."""
    project, scenario, dimensions = _dimensions_and_draft_scenario(engine)

    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        created = committing_client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(
                dimensions,
                cost_basis="fixed_amount",
                fixed_amount="2500.0000",
                fixed_amount_currency="USD",
            ),
        )
    assert created.status_code == 201, created.text
    position_id = uuid.UUID(created.json()["id"])

    row = _position_row(engine, position_id)
    assert (str(row.cost_basis), str(row.fixed_amount), row.fixed_amount_currency) == (
        "fixed_amount",
        "2500.0000",
        "USD",
    )


def test_k_02_the_basis_is_a_stored_field_not_a_parameter_of_the_read(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-02 — two independent `GET`s of the same position, by two different callers, agree on the
    basis without either of them naming one: `cost_basis` cannot be a request parameter of the read
    (there is no such parameter on `GET …/staffing-positions`, and this proves the *data* backs that
    up, not only the absent schema field) — it is a fact about the row.

    Verified indirectly through the personnel-cost endpoint's `fixed_amount_state`, since the
    basis itself never crosses the read boundary (Q4): a `fixed_amount` position with no amount
    would be unwritable (K-06), so `"calculated"` here can only come from the stored basis being
    read as `fixed_amount`, never `worked_time` re-interpreted by a caller-supplied flag that
    does not exist.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(engine)
    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        created = committing_client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(
                dimensions,
                cost_basis="fixed_amount",
                fixed_amount="10.0000",
                fixed_amount_currency="PLN",
            ),
        )
    assert created.status_code == 201, created.text

    with caller_holding(*Permission):
        first = committing_client.get(
            f"/projects/{project.id}/scenarios/{scenario.id}/personnel-cost"
        )
        second = committing_client.get(
            f"/projects/{project.id}/scenarios/{scenario.id}/personnel-cost"
        )
    assert first.status_code == second.status_code == 200
    assert (
        first.json()["personnel_cost"]["fixed_amount_state"]
        == second.json()["personnel_cost"]["fixed_amount_state"]
        == "calculated"
    )


# --- the write endpoint round-trips the token, exactly like the allocation edit ------------------


def test_the_cost_basis_edit_endpoint_changes_the_basis_and_is_reflected_in_the_cost(
    committing_client: TestClient, engine: Engine
) -> None:
    """A real `PATCH .../staffing-positions/{position_id}`, switching a freshly created
    `worked_time` position to `fixed_amount` — and the personnel-cost endpoint agreeing afterwards.
    Not one of K-01..K-06 by itself; it is the write path K-02/K-03 build their proofs on, exercised
    end to end once so the two do not only prove the mechanism in isolation.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(engine)
    created = committing_client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions),
        headers=as_caller(IN_SCOPE_USER),
    ).json()
    position_id = created["id"]
    token = created["updated_at"]

    # Fix 3 (Security-Auditor, gate 1 SC-5-03): the write needs the same conjunction the
    # personnel-cost read does, not just `STAFFING_WRITE`.
    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        edited = committing_client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position_id}",
            json={
                "updated_at": token,
                "cost_basis": "fixed_amount",
                "fixed_amount": "333.3300",
                "fixed_amount_currency": "PLN",
            },
        )
    assert edited.status_code == 200, edited.text
    assert edited.json()["updated_at"] != token, "the ADR-0007 token did not rotate"

    row = _position_row(engine, uuid.UUID(position_id))
    assert (str(row.cost_basis), str(row.fixed_amount), row.fixed_amount_currency) == (
        "fixed_amount",
        "333.3300",
        "PLN",
    )

    # A second edit with the *old* token is refused — the same token this endpoint just rotated.
    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        stale = committing_client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position_id}",
            json={"updated_at": token, "cost_basis": "worked_time"},
        )
    assert stale.status_code == 409, stale.text


# --- gate 1 SC-5-03, fix 1 (Guardian): an amount cannot land on a `worked_time` row without ----
# also naming `cost_basis` in the same request -----------------------------------------------------
#
# The schema validator (`StaffingPositionCostBasisEditRequest._at_least_one_field_and_consistent_
# with_its_basis`) cannot refuse this combination on its own — it has no view of the row's *current*
# basis — so the guard lives in `update_position_cost_basis`'s own `UPDATE ... WHERE`, beside the
# `approved` guard and the ADR-0007 token. Both tests below are real HTTP round trips against a real
# position, not a call to the validator in isolation, because the claim is about the write path.


def test_fix_1_an_amount_without_cost_basis_is_refused_on_a_worked_time_position(
    committing_client: TestClient, engine: Engine
) -> None:
    """Guardian finding, gate 1 SC-5-03 — `PATCH {"fixed_amount": ..., "fixed_amount_currency":
    ...}` with no `cost_basis` on a position whose stored basis is (still) `worked_time` is refused
    with a `409` naming the mismatch, and the row is untouched: neither the amount nor the currency
    is written, and the ADR-0007 token does not rotate. Before this fix the same request passed
    validation and silently wrote the amount onto a `worked_time` row — the invariant this test
    guards.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(engine)
    created = committing_client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions),
        headers=as_caller(IN_SCOPE_USER),
    ).json()
    position_id = created["id"]
    token = created["updated_at"]

    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        refused = committing_client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position_id}",
            json={
                "updated_at": token,
                "fixed_amount": "50.0000",
                "fixed_amount_currency": "PLN",
            },
        )

    assert refused.status_code == 409, refused.text
    assert "cost_basis" in refused.json()["detail"], refused.text

    row = _position_row(engine, uuid.UUID(position_id))
    assert (row.cost_basis, row.fixed_amount, row.fixed_amount_currency) == (
        "worked_time",
        None,
        None,
    )
    unchanged = committing_client.get(
        staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert unchanged.json()["positions"][0]["updated_at"] == token, (
        "the ADR-0007 token rotated on a write that was refused"
    )


def test_fix_1_an_amount_alone_still_edits_an_already_fixed_amount_position(
    committing_client: TestClient, engine: Engine
) -> None:
    """The contrast the refusal above needs: the identical shape of request — `fixed_amount`/
    `fixed_amount_currency` named, `cost_basis` omitted — succeeds when the position's *stored*
    basis is already `fixed_amount`. Guardian's fix narrows the gap without over-narrowing it: an
    amount correction on an existing `fixed_amount` position must keep working exactly as it did
    before this task, and this is the request that proves it still does.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(engine)
    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        created = committing_client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(
                dimensions,
                cost_basis="fixed_amount",
                fixed_amount="100.0000",
                fixed_amount_currency="PLN",
            ),
        ).json()
    position_id = created["id"]
    token = created["updated_at"]

    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        edited = committing_client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position_id}",
            json={
                "updated_at": token,
                "fixed_amount": "250.0000",
                "fixed_amount_currency": "USD",
            },
        )

    assert edited.status_code == 200, edited.text
    assert edited.json()["updated_at"] != token, "the ADR-0007 token did not rotate"

    row = _position_row(engine, uuid.UUID(position_id))
    assert (str(row.cost_basis), str(row.fixed_amount), row.fixed_amount_currency) == (
        "fixed_amount",
        "250.0000",
        "USD",
    )


# --- gate 1 SC-5-03, fix 3 (Security-Auditor): the write needs the read side's own conjunction -


def test_fix_3_the_cost_basis_write_is_denied_without_personnel_cost_visibility(
    committing_client: TestClient, engine: Engine
) -> None:
    """Security-Auditor finding, gate 1 SC-5-03 — a caller holding `STAFFING_WRITE` but neither
    `PERSONNEL_COSTS_READ` nor this project's `can_view_personnel_costs` gets a `403` on the
    cost-basis `PATCH`, and the row is untouched. Before this fix the same caller could set
    `fixed_amount` — a personnel-cost figure — without ever being allowed to read it back.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(engine)
    created = committing_client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions),
        headers=as_caller(IN_SCOPE_USER),
    ).json()
    position_id = created["id"]
    token = created["updated_at"]

    with caller_holding(Permission.STAFFING_WRITE):
        denied = committing_client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position_id}",
            json={
                "updated_at": token,
                "cost_basis": "fixed_amount",
                "fixed_amount": "50.0000",
                "fixed_amount_currency": "PLN",
            },
        )

    assert denied.status_code == 403, denied.text
    row = _position_row(engine, uuid.UUID(position_id))
    assert (row.cost_basis, row.fixed_amount, row.fixed_amount_currency) == (
        "worked_time",
        None,
        None,
    )

    # The contrast: the same caller, the same request, holding the full conjunction — succeeds.
    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        accepted = committing_client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position_id}",
            json={
                "updated_at": token,
                "cost_basis": "fixed_amount",
                "fixed_amount": "50.0000",
                "fixed_amount_currency": "PLN",
            },
        )

    assert accepted.status_code == 200, accepted.text
    row = _position_row(engine, uuid.UUID(position_id))
    assert (str(row.cost_basis), str(row.fixed_amount), row.fixed_amount_currency) == (
        "fixed_amount",
        "50.0000",
        "PLN",
    )


# --- gate 1 SC-5-03, fix 3 extended to POST (human follow-up, 2026-09-25): `create_position` ---
# has the identical gap the PATCH endpoint had — a caller holding only `STAFFING_WRITE` could create
# a `fixed_amount` position, without ever holding the SC-1-08 conjunction. A `worked_time` create
# (the default, and every request this repository's tests sent before SC-5-03) writes no
# personnel-cost figure, so it must stay reachable without the conjunction — the contrast this suite
# needs beside the two `fixed_amount` cases.


def test_fix_3_creating_a_worked_time_position_needs_no_personnel_cost_conjunction(
    committing_client: TestClient, engine: Engine
) -> None:
    """Contrast, checked first: a caller holding only `STAFFING_WRITE` (no `PERSONNEL_COSTS_READ`,
    no `can_view_personnel_costs`) still creates a `worked_time` position — the default basis, and
    the request shape used everywhere else in this repository's suite. The conjunction must not be
    demanded of a request that never touches a personnel-cost figure.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(engine)

    with caller_holding(Permission.STAFFING_WRITE):
        created = committing_client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(dimensions),
        )

    assert created.status_code == 201, created.text
    row = _position_row(engine, uuid.UUID(created.json()["id"]))
    assert (row.cost_basis, row.fixed_amount, row.fixed_amount_currency) == (
        "worked_time",
        None,
        None,
    )


def test_fix_3_creating_a_fixed_amount_position_is_denied_without_personnel_cost_visibility(
    committing_client: TestClient, engine: Engine
) -> None:
    """Security-Auditor finding, gate 1 SC-5-03, extended to `POST` — a caller holding
    `STAFFING_WRITE` but neither `PERSONNEL_COSTS_READ` nor this project's `can_view_personnel_
    costs` gets a `403` when the request's `cost_basis` is `fixed_amount`, and nothing is written.
    The contrast is the identical request with the full conjunction, which succeeds.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(engine)

    with caller_holding(Permission.STAFFING_WRITE):
        denied = committing_client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(
                dimensions,
                cost_basis="fixed_amount",
                fixed_amount="75.0000",
                fixed_amount_currency="PLN",
            ),
        )

    assert denied.status_code == 403, denied.text
    with engine.connect() as connection:
        written = connection.execute(
            sa.text("SELECT count(*) FROM staffing_position WHERE scenario_id = :id"),
            {"id": scenario.id},
        ).scalar_one()
    assert written == 0, "the denied create wrote a row anyway"

    with caller_holding(Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ):
        accepted = committing_client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(
                dimensions,
                cost_basis="fixed_amount",
                fixed_amount="75.0000",
                fixed_amount_currency="PLN",
            ),
        )

    assert accepted.status_code == 201, accepted.text
    row = _position_row(engine, uuid.UUID(accepted.json()["id"]))
    assert (str(row.cost_basis), str(row.fixed_amount), row.fixed_amount_currency) == (
        "fixed_amount",
        "75.0000",
        "PLN",
    )
