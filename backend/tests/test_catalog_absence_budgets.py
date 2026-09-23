"""SC-3-03, K-02 and K-04 — the budget endpoints: a named source, and a regime that is read (F-05).

Two criteria, and they pull on different mechanisms:

- **K-02: a number without a named source is not a row.** The request schema refuses it with a
  `422`, the database refuses it again for every path that never sees the schema
  (`tests/test_absence_budget_schema_constraints.py`), and naming the *author* is not a substitute
  for naming the *source* — authorship is not a column this catalogue has (ADR-0008, addendum
  2026-09-22 SC-3-03, point 6).
- **K-04: the commercial regime of a budget is read from the one absence type flagged
  `is_statutory_leave`, never copied onto the budget row.** Four round-trip combinations rather than
  two, because two would be satisfied by an implementation that derived one flag from the other
  (the SC-3-02 lesson, criterion K-11's `FOUR_COMBINATIONS`).

**Every fixture here flags a type that is not first alphabetically.** `Statutory annual leave`
sorts after `Paid holiday` and after `Compassionate leave`, both of which appear unflagged in these
tests — so the mutation "pick the type by name instead of by the flag" answers with the wrong row
and fails, instead of passing by coincidence.
"""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.money import NOT_APPLICABLE
from app.domain.absence_budget import NO_STATUTORY_LEAVE_TYPE, RESOLVED
from tests.conftest import (
    BUDGET_SOURCE,
    IN_SCOPE_USER,
    STATUTORY_LEAVE_TYPE_NAME,
    as_caller,
    budget_payload,
    count_absence_budgets,
    make_absence_budget,
    make_absence_type,
    make_dimension_tuple,
    make_working_calendar,
)

BUDGETS_PATH = "/catalog/absence-budgets"

FOUR_COMBINATIONS = [(True, True), (True, False), (False, True), (False, False)]
"""Every pair the two commercial flags can take (SC-3-02's K-11, reused by K-04).

Four and not two: with only `(True, False)` and `(False, True)` an implementation that returned
`generates_revenue = not generates_cost` would round-trip both perfectly. `(True, True)` is
billable training; `(False, False)` is unpaid leave — both real, and both invisible to a two-case
test."""


def _profile(session: Session, *, suffix: str = ""):
    """One calendar and one dimension tuple — i.e. one budget key."""
    calendar = make_working_calendar(
        session, name=f"Poland 7.5h{suffix}", standard_hours_per_day=Decimal("7.50")
    )
    return calendar, make_dimension_tuple(session, suffix=suffix, calendar=calendar)


def _budgets(client: TestClient) -> list[dict]:
    response = client.get(BUDGETS_PATH, headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()["budgets"]


# --- K-02: the source of the number is mandatory, and an author is not one ------------------------


def test_k_02_a_budget_without_a_named_source_is_refused_with_422_and_writes_no_row(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — three requests, one accepted, and the two refusals are refused for the right reason.

    1. **No `source` at all** → `422`, and **no row**: asserted with a `SELECT`, not inferred from
       the status code, because "the endpoint said no" and "nothing was written" are two different
       claims and only the second one is what the criterion is about.
    2. **A source that is blank** (`"   "`) → `422` as well. `NOT NULL` alone would accept it, which
       is why the request schema strips and the database carries a non-blank CHECK.
    3. **The contrast: a real source** → `201`, and **exactly one** row, with the text stored
       verbatim. Without it, an endpoint that refused everything would satisfy the two refusals.

    The mutation the criterion names — making the field optional with a default of `""` — passes 3
    and fails 1, because a body with no source would then be accepted.
    """
    calendar, dimensions = _profile(db_session)
    body = budget_payload(calendar.id, dimensions.engagement_type_id)

    without_source = {key: value for key, value in body.items() if key != "source"}
    refused = client.post(
        BUDGETS_PATH, json=without_source, headers=as_caller(IN_SCOPE_USER)
    )
    assert refused.status_code == 422, refused.text
    assert "source" in refused.text
    assert count_absence_budgets(db_session) == 0, "the refused write was saved"

    blank = client.post(
        BUDGETS_PATH, json=body | {"source": "   "}, headers=as_caller(IN_SCOPE_USER)
    )
    assert blank.status_code == 422, blank.text
    assert count_absence_budgets(db_session) == 0, "a blank source was saved"

    created = client.post(BUDGETS_PATH, json=body, headers=as_caller(IN_SCOPE_USER))

    assert created.status_code == 201, created.text
    assert created.json()["source"] == BUDGET_SOURCE
    assert created.json()["budget_days"] == "26.00"
    assert count_absence_budgets(db_session) == 1
    stored = db_session.execute(
        sa.text("SELECT source, budget_days FROM absence_budget")
    ).one()
    assert stored.source == BUDGET_SOURCE
    assert stored.budget_days == Decimal("26.00")


def test_k_02_a_request_naming_the_author_instead_of_the_source_is_still_refused(
    client: TestClient, db_session: Session
) -> None:
    """K-02's second contrast: authorship does not substitute for a source, and never will.

    A body that says *who* entered the number, but not *where the number comes from*, is a `422` —
    and so is one that says both, because there is no column for an author and there must not be
    one: it would be the first column tying a catalogue row to a user and would expire the
    exemption that keeps this table outside the `project_access` filter (ADR-0005, addendum
    2026-09-21 SC-2-04, point 6; addendum 2026-09-22 SC-3-03, point 6).

    Four spellings, because `extra="forbid"` is the mechanism and a schema that quietly dropped
    unknown keys would accept every one of them while *looking* like it had refused the first.
    """
    calendar, dimensions = _profile(db_session)
    body = budget_payload(calendar.id, dimensions.engagement_type_id)
    without_source = {key: value for key, value in body.items() if key != "source"}

    for field in ("author", "entered_by", "approved_by", "user_id"):
        named_author = client.post(
            BUDGETS_PATH,
            json=without_source | {field: "Anna Kowalska"},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert named_author.status_code == 422, f"{field}: {named_author.text}"
        assert count_absence_budgets(db_session) == 0

        with_both = client.post(
            BUDGETS_PATH,
            json=body | {field: "Anna Kowalska"},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert with_both.status_code == 422, f"{field}: {with_both.text}"
        assert count_absence_budgets(db_session) == 0, (
            f"a budget carrying {field} was written — this catalogue has no column for an author"
        )


def test_an_overlapping_budget_window_is_refused_with_409_naming_the_constraint(
    client: TestClient, db_session: Session
) -> None:
    """The `EXCLUDE` reaching the API as a `409` that names the mechanism, not a `500`.

    Not a criterion of its own — K-01 owns the constraint — and it is here because the *translation*
    is a separate mechanism from the constraint: the data layer classifies by SQLSTATE and only a
    classified refusal becomes a `409` (R-01). The message names the constraint and quotes no row
    values (NF-11).
    """
    calendar, dimensions = _profile(db_session)
    body = budget_payload(calendar.id, dimensions.engagement_type_id)
    assert (
        client.post(BUDGETS_PATH, json=body, headers=as_caller(IN_SCOPE_USER)).status_code == 201
    )

    overlapping = client.post(
        BUDGETS_PATH,
        json=body | {"effective_from": "2026-07-01", "effective_to": "2027-06-30"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert overlapping.status_code == 409, overlapping.text
    assert "ex_absence_budget_no_overlapping_periods" in overlapping.json()["detail"]
    assert count_absence_budgets(db_session) == 1


def test_a_window_that_is_not_whole_months_is_refused_at_the_boundary_with_422(
    client: TestClient, db_session: Session
) -> None:
    """The boundary states the month-alignment rule as a `422` naming the field.

    The database's CHECK stays the guarantee (`tests/test_absence_budget_schema_constraints.py`);
    this is the status code. Both ends are tried, because a validator checking one of them passes
    half the malformed windows — and a window ending mid-month is the more plausible mistake, since
    it looks like "valid until the day we agreed".
    """
    calendar, dimensions = _profile(db_session)
    body = budget_payload(calendar.id, dimensions.engagement_type_id)

    for field, value in (("effective_from", "2026-01-15"), ("effective_to", "2026-12-15")):
        refused = client.post(
            BUDGETS_PATH, json=body | {field: value}, headers=as_caller(IN_SCOPE_USER)
        )
        assert refused.status_code == 422, f"{field}: {refused.text}"
        assert count_absence_budgets(db_session) == 0


# --- K-04: the regime comes from the flagged type, and from nothing else --------------------------


def test_k_04_a_budget_reports_the_regime_of_the_single_absence_type_flagged_as_statutory_leave_not_a_copy_of_its_own(  # noqa: E501 — the criterion names this test; the name is the contract, not a style choice
    client: TestClient, db_session: Session
) -> None:
    """K-04 — the budget answers with the flagged type's flags, whatever they are.

    Four combinations in one test, over one budget row whose own columns never change: the flags of
    the **type** are flipped between reads and the budget's answer follows. Two mutations die here:

    - **a literal on the budget** (`generates_cost = True` written into the payload, or a column on
      `absence_budget` holding it) — the answer would stop following the type after the first flip;
    - **choosing the type by name** — the flagged type is `Statutory annual leave`, and the
      dictionary also holds `Compassionate leave` and `Paid holiday`, both of which sort before it
      and both of which carry the *opposite* flags. "The first type alphabetically" therefore
      answers with a different pair and fails.

    The nested `statutory_leave` object is asserted as well as the flat pair: a client renders the
    flat pair and the object says which row it came from, and an implementation that filled one and
    not the other would be half-right in a way no single assertion would catch.
    """
    calendar, dimensions = _profile(db_session)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
    )
    # Two unflagged decoys, both sorting before the flagged type and carrying the opposite flags.
    make_absence_type(
        db_session, name="Compassionate leave", generates_cost=False, generates_revenue=True
    )
    make_absence_type(
        db_session, name="Paid holiday", generates_cost=False, generates_revenue=True
    )
    statutory = make_absence_type(
        db_session,
        name=STATUTORY_LEAVE_TYPE_NAME,
        generates_cost=True,
        generates_revenue=False,
        is_statutory_leave=True,
    )

    for generates_cost, generates_revenue in FOUR_COMBINATIONS:
        db_session.execute(
            sa.text(
                "UPDATE absence_type SET generates_cost = :cost, generates_revenue = :revenue"
                " WHERE id = :id"
            ),
            {"cost": generates_cost, "revenue": generates_revenue, "id": statutory.id},
        )
        db_session.flush()
        # The UPDATE is raw SQL, so the identity map still holds the old attribute values and the
        # read below would be served from memory — the read has to go back to the database for this
        # to be a round trip at all.
        db_session.expire_all()

        [budget] = _budgets(client)

        assert budget["statutory_leave_state"] == RESOLVED
        assert (budget["generates_cost"], budget["generates_revenue"]) == (
            generates_cost,
            generates_revenue,
        ), (
            "the budget did not report the flags of the type flagged is_statutory_leave: "
            f"{budget}"
        )
        assert budget["statutory_leave"] == {
            "absence_type_id": str(statutory.id),
            "name": STATUTORY_LEAVE_TYPE_NAME,
            "generates_cost": generates_cost,
            "generates_revenue": generates_revenue,
        }


def test_k_04_with_no_flagged_absence_type_the_budget_answers_a_named_state_and_n_a(
    client: TestClient, db_session: Session
) -> None:
    """K-04's third state — no type carries the flag (ADR-0008, addendum SC-3-03, point 8b).

    The half of "exactly one flagged type" that no index can enforce, and the answer is a **named
    state** plus `"n/a"`:

    - never a silent `false`, which would read as a decided answer nobody decided (and would make
      "this leave costs nothing" the default reading of an unconfigured catalogue);
    - never a `500`, which would make an incomplete catalogue a server error;
    - never a guess: the dictionary here holds two unflagged types, one of which is the obvious
      candidate by name, and the answer still names no type.

    The contrast is in the same test: flagging one of them resolves the state and produces the
    figures, so this is not satisfied by an endpoint that always answers `"n/a"`.
    """
    calendar, dimensions = _profile(db_session)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
    )
    make_absence_type(db_session, name="Paid holiday")
    candidate = make_absence_type(db_session, name=STATUTORY_LEAVE_TYPE_NAME)

    [unresolved] = _budgets(client)

    assert unresolved["statutory_leave_state"] == NO_STATUTORY_LEAVE_TYPE
    assert unresolved["statutory_leave"] is None
    assert unresolved["generates_cost"] == NOT_APPLICABLE
    assert unresolved["generates_revenue"] == NOT_APPLICABLE
    assert unresolved["budget_days"] == "26.00", (
        "the budget's own figures disappeared with the unresolved regime — the state is about the "
        "type, not about the row"
    )

    db_session.execute(
        sa.text("UPDATE absence_type SET is_statutory_leave = true WHERE id = :id"),
        {"id": candidate.id},
    )
    db_session.flush()
    db_session.expire_all()

    [resolved] = _budgets(client)
    assert resolved["statutory_leave_state"] == RESOLVED
    assert resolved["generates_cost"] is True
    assert resolved["generates_revenue"] is False


def test_the_budget_list_returns_the_row_whole_with_its_window_and_key(
    client: TestClient, db_session: Session
) -> None:
    """What a budget row looks like on the way out — the fields a client needs to explain a figure.

    Not a criterion of its own; it is the assertion that keeps the criteria above from passing
    against a payload that carries only the flags. In particular the **window** is carried: K-03's
    first contrast is that the same 26 days over six months and over twelve produce different
    monthly figures, and a client that could not see the window could not explain the difference.
    """
    calendar, dimensions = _profile(db_session)
    budget = make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
    )

    [entry] = _budgets(client)

    assert entry["id"] == str(budget.id)
    assert entry["calendar_id"] == str(calendar.id)
    assert entry["engagement_type_id"] == str(dimensions.engagement_type_id)
    assert entry["budget_days"] == "26.00"
    assert entry["unit"] == "day"
    assert entry["source"] == BUDGET_SOURCE
    assert entry["effective_from"] == "2026-01-01"
    assert entry["effective_to"] == "2026-12-31"
    assert "updated_at" in entry


def test_the_absence_type_dictionary_carries_the_statutory_flag(
    client: TestClient, db_session: Session
) -> None:
    """The seventh dictionary grew a third flag, and it round-trips like the other two (SC-3-03).

    Asserted on the dictionary endpoint as well as through the budget, because that is where a
    client discovers *which* type the organisation settles budgets against — and because an
    implementation that resolved the flag correctly inside the budget read while dropping it from
    the dictionary would leave the screen unable to show it at all.
    """
    make_absence_type(db_session, name="Paid holiday")
    make_absence_type(
        db_session, name=STATUTORY_LEAVE_TYPE_NAME, is_statutory_leave=True
    )

    response = client.get("/catalog/absence-types", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    flags = {
        entry["name"]: entry["is_statutory_leave"] for entry in response.json()["absence_types"]
    }
    assert flags == {"Paid holiday": False, STATUTORY_LEAVE_TYPE_NAME: True}


def test_a_budget_naming_a_calendar_that_does_not_exist_is_a_409_not_a_500(
    client: TestClient, db_session: Session
) -> None:
    """A foreign key reaching the API as a refusal a caller can act on.

    The reference is guaranteed by the database and not by the request schema (which validates the
    field as an *identifier* only), so this is the path by which "you named a calendar nobody
    created" arrives — and it must arrive as a `409` naming the constraint rather than as an
    unhandled `500`.
    """
    _, dimensions = _profile(db_session)

    response = client.post(
        BUDGETS_PATH,
        json=budget_payload(uuid.uuid4(), dimensions.engagement_type_id),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 409, response.text
    assert "fk_absence_budget_calendar_id" in response.json()["detail"]
    assert count_absence_budgets(db_session) == 0
