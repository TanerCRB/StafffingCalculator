"""SC-3-02, K-11 — an absence type carries two flags, independently (F-05).

**Mandatory annotation, and it is the point of this file rather than a caveat to it: these tests
prove persistence and independence and NOTHING ELSE.** No cost calculation and no revenue
calculation reads `generates_cost` or `generates_revenue` — there is no cost or revenue calculation
in this repository at all (F-07 / F-08, plan block 5). A reader who takes a green test here as
evidence that "paid holiday costs money in the model" is reading a claim this file does not make.
What it does make:

- the two flags round-trip through the database and the API in all **four** combinations;
- they are two columns and not one, so neither is derived from, aliased to, or the negation of the
  other.

The mutation the criterion names — `generates_revenue` aliased to `generates_cost` — survives any
test that only ever writes types where the two happen to agree, which is why all four combinations
are written in one call and read back in one payload.
"""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AbsenceType
from tests.conftest import IN_SCOPE_USER, as_caller, make_absence_type

FOUR_COMBINATIONS = (
    ("Paid holiday", True, False),
    ("Billable training", True, True),
    ("Unpaid leave", False, False),
    ("Client-funded cover", False, True),
)
"""All four assignments of two booleans, each with a name that makes it a real case.

Four and not two: with only `(True, False)` and `(False, True)` an implementation storing one flag
and returning its negation for the other passes; with only `(True, True)` and `(False, False)` an
implementation storing one flag and returning it twice passes. Both mutations need all four to die.
"""


def test_k_11_an_absence_type_round_trips_its_two_flags_independently(
    client: TestClient, db_session: Session
) -> None:
    """K-11 — four combinations in, four combinations out, through the real read path.

    **This test proves trust in the storage, not in any calculation.** Nothing in SC-3-02 reads
    these flags: they are written, they are returned, and the first task that prices an absence
    (F-07) has to prove for itself that a calculation consults them. See the module docstring.

    The read goes through `GET /catalog/absence-types` rather than through the ORM, so it covers the
    response-shaping layer as well: `shape_absence_type` reads both columns off the row, and an
    implementation that built the payload as `generates_revenue=not generates_cost` would fail here
    while an ORM-only assertion would not notice.

    The two `assert` blocks at the end are the contrast the criterion asks for: at least two rows
    whose flags differ *from each other*, and at least one row where the two flags differ *within
    the row*. Without the second, a payload in which both fields carried the same value would still
    satisfy a per-row equality check against the wrong expectation.
    """
    for name, cost, revenue in FOUR_COMBINATIONS:
        make_absence_type(
            db_session, name=name, generates_cost=cost, generates_revenue=revenue
        )

    response = client.get("/catalog/absence-types", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    returned = {
        entry["name"]: (entry["generates_cost"], entry["generates_revenue"])
        for entry in response.json()["absence_types"]
    }
    assert returned == {name: (cost, revenue) for name, cost, revenue in FOUR_COMBINATIONS}

    # Two rows that disagree with each other, and one row whose two flags disagree with each other.
    assert returned["Paid holiday"] != returned["Unpaid leave"]
    assert returned["Paid holiday"][0] != returned["Paid holiday"][1], (
        "no returned row has two different flags — an implementation aliasing one to the other "
        "would satisfy every assertion above"
    )
    assert returned["Billable training"][0] == returned["Billable training"][1]


def test_k_11_the_flags_are_two_columns_in_the_database_and_neither_defaults_to_the_other(
    db_session: Session,
) -> None:
    """K-11's other half, asserted against the schema rather than against a payload.

    Two independent columns, both `NOT NULL`, both defaulting to `false`. The default matters: an
    unanswered flag must read as "no", because the opposite direction would make a type created by
    an import quietly claim that time booked against it is billable.

    A row written with neither flag named is read back as `(False, False)` — which is also the proof
    that the defaults are in the *database* and not in the Python model: this insert goes through
    Core, so a `default=` on the mapped column would not apply to it.
    """
    columns = dict(
        db_session.execute(
            sa.text(
                "SELECT column_name, is_nullable FROM information_schema.columns"
                " WHERE table_name = 'absence_type'"
                " AND column_name IN ('generates_cost', 'generates_revenue')"
            )
        ).all()
    )
    assert columns == {"generates_cost": "NO", "generates_revenue": "NO"}

    db_session.execute(
        sa.text("INSERT INTO absence_type (id, name) VALUES (gen_random_uuid(), 'Bare')")
    )
    bare = db_session.execute(
        sa.select(AbsenceType).where(AbsenceType.name == "Bare")
    ).scalars().one()
    assert (bare.generates_cost, bare.generates_revenue) == (False, False)


def test_two_absence_types_whose_names_differ_only_in_case_or_spacing_are_the_same_type(
    db_session: Session,
) -> None:
    """The seventh dictionary inherits the normalised-name uniqueness of the other six (R-04).

    Two "Paid holiday"s would be two types with two independent pairs of flags, every absence
    pointing at one of them and no screen able to tell them apart — the same defect five "Senior"s
    were for the rate table. The contrast is in the same test: a genuinely different name is
    accepted.
    """
    make_absence_type(db_session, name="Paid holiday")

    with pytest.raises(IntegrityError) as error:
        make_absence_type(db_session, name="  paid   HOLIDAY ")
    assert "uq_absence_type_name_normalized" in str(error.value)
    db_session.rollback()

    make_absence_type(db_session, name="Paid holiday")
    make_absence_type(db_session, name="Sick leave")
