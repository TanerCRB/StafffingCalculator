"""SC-2-01 (K-04, K-07) and SC-2-03 (K-03, K-04) — which rate applies on a day, whose it is, and
how an amount crosses the boundary.

SC-2-03 adds a second axis to the same question. "Which rate applies" now has a second half —
*whose* — and the rule is one sentence: **omitting the vendor means the organisation's own rate,
never "any vendor"**. Both tests at the end of this file exist because the two plausible ways of
getting that wrong (matching every vendor; falling back to a vendor when there is nothing internal)
are different mistakes with different consequences, and each survives the other's test.

K-04 is the read half of Invariant Guardian rule 13: a rate lookup resolves **by effective window**,
and "latest row wins" is not a fallback it is allowed to reach for. The mutation the criterion names
(`ORDER BY effective_from DESC LIMIT 1`) answers the *newest* rate to every question, so every
assertion below that asks about a day in an earlier window kills it.

The window boundaries are asserted as well as its middle. That is not thoroughness for its own sake:
`effective_to` is inclusive in the API and the database stores a half-open `daterange`, so the whole
conversion is a single `+ 1 day` inside the generated column (ADR-0008, point 3). A test that only
ever asked about mid-window dates would pass with the conversion missing, doubled or applied to the
wrong end — and would be exactly the blind spot that decision was written to close.

K-07's second half lives here too: an amount crosses the API boundary as a fixed-point string, for a
value whose default serialisation would differ.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas.catalog import CatalogRate
from app.core.identity import Permission
from tests.conftest import (
    IN_SCOPE_USER,
    DimensionTuple,
    as_caller,
    caller_holding,
    make_dimension_tuple,
    make_rate,
    make_vendor,
)

EARLY = (date(2025, 1, 1), date(2025, 12, 31))
MIDDLE = (date(2026, 1, 1), date(2026, 6, 30))
OPEN_ENDED_FROM = date(2026, 7, 1)
"""Three disjoint windows for one tuple: two closed and one open-ended.

Adjacent rather than separated by a gap (the middle window ends the day before the last one starts),
because adjacency is where an off-by-one in the inclusive→half-open conversion shows up: with the
conversion missing, these three windows overlap and the `EXCLUDE` constraint would refuse the setup
outright — so this fixture is itself a check on the boundary."""

EARLY_SELLING = Decimal("100.0000")
MIDDLE_SELLING = Decimal("150.0000")
OPEN_SELLING = Decimal("200.0000")


def _three_windows(session: Session) -> DimensionTuple:
    """One dimension tuple, three consecutive rate windows, each with a distinguishable rate."""
    dimensions = make_dimension_tuple(session)
    make_rate(
        session,
        dimensions,
        effective_from=EARLY[0],
        effective_to=EARLY[1],
        default_selling_rate=EARLY_SELLING,
    )
    make_rate(
        session,
        dimensions,
        effective_from=MIDDLE[0],
        effective_to=MIDDLE[1],
        default_selling_rate=MIDDLE_SELLING,
    )
    make_rate(
        session,
        dimensions,
        effective_from=OPEN_ENDED_FROM,
        effective_to=None,
        default_selling_rate=OPEN_SELLING,
    )
    return dimensions


def _selling_rate_on(
    client: TestClient, dimensions: DimensionTuple, on_date: date
) -> tuple[int, str | None]:
    response = client.get(
        "/catalog/rates/effective",
        params={**dimensions.as_query(), "on_date": on_date.isoformat()},
        headers=as_caller(IN_SCOPE_USER),
    )
    body = response.json()
    is_success = response.status_code < 400
    return response.status_code, body.get("default_selling_rate") if is_success else None


def test_k_04_a_date_inside_an_earlier_window_resolves_to_that_windows_rate(
    client: TestClient, db_session: Session
) -> None:
    """K-04. Three disjoint windows; a date in the middle one answers the middle one's rate.

    This is the criterion, stated once and asserted in the middle of each window so that no single
    reading can be a coincidence: the earliest window answers 100, the middle 150, the open-ended
    one
    200. A resolution ordered by `effective_from DESC LIMIT 1` answers 200 to all three.

    A day before every window is a `404` — "no rate is effective then" — which is a statement
    about the catalogue and not about the caller, and is the reason the same endpoint can afford
    a `404` at all while a denied cost rate is answered with `200` and a removed field."""
    dimensions = _three_windows(db_session)

    assert _selling_rate_on(client, dimensions, date(2025, 6, 15)) == (200, str(EARLY_SELLING))
    assert _selling_rate_on(client, dimensions, date(2026, 3, 15)) == (200, str(MIDDLE_SELLING))
    assert _selling_rate_on(client, dimensions, date(2027, 3, 15)) == (200, str(OPEN_SELLING))
    assert _selling_rate_on(client, dimensions, date(2024, 12, 31))[0] == 404


def test_k_04_effective_to_is_inclusive_and_the_following_day_is_the_next_window(
    client: TestClient, db_session: Session
) -> None:
    """K-04, at the boundary — the day the two representations of a window could disagree.

    `effective_to = 2026-06-30` means the 30th is covered; the 1st of July belongs to the next
    window. Both ends of the middle window are asserted, plus the day before its start, so the
    inclusive→ half-open conversion is pinned at each edge rather than assumed from a mid-window
    read.

    If that `+ 1 day` were dropped, `2026-06-30` would answer the *early* window's rate (the middle
    window would end on the 29th) and this test would fail while the previous one still passed.
    """
    dimensions = _three_windows(db_session)

    assert _selling_rate_on(client, dimensions, date(2025, 12, 31)) == (200, str(EARLY_SELLING))
    assert _selling_rate_on(client, dimensions, MIDDLE[0]) == (200, str(MIDDLE_SELLING))
    assert _selling_rate_on(client, dimensions, MIDDLE[1]) == (200, str(MIDDLE_SELLING))
    assert _selling_rate_on(client, dimensions, OPEN_ENDED_FROM) == (200, str(OPEN_SELLING))


def test_k_04_the_list_filter_and_the_single_lookup_agree_about_one_day(
    client: TestClient, db_session: Session
) -> None:
    """K-04. `GET /catalog/rates?on_date=…` answers with exactly the row the resolution answers.

    Two entry points, one predicate (`app.data.catalog.covering`). Asserted together because a
    list filter that rebuilt the window expression for itself is precisely the second copy
    ADR-0008 forbids — and it would be a copy that disagrees only at the edges, i.e. only where
    nobody looks."""
    dimensions = _three_windows(db_session)

    listed = client.get(
        "/catalog/rates",
        params={"on_date": MIDDLE[1].isoformat()},
        headers=as_caller(IN_SCOPE_USER),
    )
    resolved = client.get(
        "/catalog/rates/effective",
        params={**dimensions.as_query(), "on_date": MIDDLE[1].isoformat()},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert listed.status_code == 200, listed.text
    assert resolved.status_code == 200, resolved.text
    assert listed.json()["rates"] == [resolved.json()]


def test_k_04_an_unfiltered_list_still_carries_every_window(
    client: TestClient, db_session: Session
) -> None:
    """The contrast to the filter above: without `on_date`, all three windows are returned.

    Without this, a filter that returned nothing — or a `covering` predicate that matched nothing —
    would satisfy the "only the middle row" assertion as well.
    """
    _three_windows(db_session)

    response = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert [rate["default_selling_rate"] for rate in response.json()["rates"]] == [
        str(OPEN_SELLING),
        str(MIDDLE_SELLING),
        str(EARLY_SELLING),
    ], "list_rates orders newest effective_from first (R-02, reviewer 2026-09-21)"
    assert response.json()["rates"][0]["effective_to"] is None, (
        "an open-ended window must cross the boundary as null, not as a sentinel date"
    )


GAP_EARLY = (date(2025, 1, 1), date(2025, 12, 31))
GAP_LATER = (date(2026, 7, 1), date(2026, 12, 31))
GAP_DAY = date(2026, 3, 15)
"""Two closed windows for one tuple with **six months of nothing between them**.

Every other fixture in this file is gapless and open-ended at the top, and that is what let a
mutation survive (see the test below): with adjacent windows, "the newest row whose
`effective_from` has passed" and "the row whose window covers this day" are the same row for every
date either test asks about. A gap is the only shape that separates them."""

GAP_EARLY_SELLING = Decimal("110.0000")
GAP_LATER_SELLING = Decimal("220.0000")


def _two_windows_with_a_gap(session: Session) -> DimensionTuple:
    dimensions = make_dimension_tuple(session, suffix=" (gap)")
    make_rate(
        session,
        dimensions,
        effective_from=GAP_EARLY[0],
        effective_to=GAP_EARLY[1],
        default_selling_rate=GAP_EARLY_SELLING,
    )
    make_rate(
        session,
        dimensions,
        effective_from=GAP_LATER[0],
        effective_to=GAP_LATER[1],
        default_selling_rate=GAP_LATER_SELLING,
    )
    return dimensions


def test_k_04_a_day_in_a_gap_between_windows_has_no_rate_rather_than_the_previous_one(
    client: TestClient, db_session: Session
) -> None:
    """K-04, the half the delivered suite did not force: an **expired** window stops applying.

    Added by QA after a surviving mutation. `resolve_rate` rewritten as

        WHERE effective_from <= :on_date ORDER BY effective_from DESC LIMIT 1

    — i.e. Invariant Guardian rule 13's forbidden "latest row wins", in the form somebody would
    actually write — passed all 174 delivered tests. The reason is the shape of the fixtures, not
    the strength of the assertions: `_three_windows` is adjacent and gapless with an open-ended
    top, so on every date those tests ask about, "the newest row that has started" and "the row
    whose window covers this day" are the same row. `effective_to` was therefore never load-bearing
    for the resolution — only for the generated column.

    Here it is. The early window ends on 2025-12-31, the next starts on 2026-07-01, and the
    question is asked in March 2026: the answer must be `404` (no rate is effective), never the
    expired window's rate. A rate that keeps answering after its window closed is how a calculation
    quotes a price nobody agreed to — the concrete harm rule 13 names.

    The contrast is in the same test, one day either side of the gap: the day the early window ends
    and the day the later window starts both answer, and they answer *different* rates — so a
    resolution that refused everything, or one that answered a constant, fails here too.
    """
    dimensions = _two_windows_with_a_gap(db_session)

    assert _selling_rate_on(client, dimensions, GAP_DAY)[0] == 404, (
        "a day in the gap answered with a rate — the resolution is falling back to a window that "
        "has already ended, which is 'latest row wins' (rule 13)"
    )
    assert _selling_rate_on(client, dimensions, date(2026, 6, 30))[0] == 404
    # The contrast: the two windows themselves still answer, and with their own rates.
    assert _selling_rate_on(client, dimensions, GAP_EARLY[1]) == (200, str(GAP_EARLY_SELLING))
    assert _selling_rate_on(client, dimensions, GAP_LATER[0]) == (200, str(GAP_LATER_SELLING))


def test_k_04_the_list_filter_also_drops_a_window_that_has_ended(
    client: TestClient, db_session: Session
) -> None:
    """The same claim for the other entry point — `GET /catalog/rates?on_date=…`.

    Both paths share `app.data.catalog.covering`, so this is one predicate proven twice rather than
    two mechanisms; the point is that neither entry point may answer with an expired window. The
    contrast is the unfiltered list in the same test: both rows exist, so an empty filtered list is
    not an empty table.
    """
    _two_windows_with_a_gap(db_session)

    in_the_gap = client.get(
        "/catalog/rates",
        params={"on_date": GAP_DAY.isoformat()},
        headers=as_caller(IN_SCOPE_USER),
    )
    unfiltered = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))

    assert in_the_gap.status_code == 200, in_the_gap.text
    assert in_the_gap.json()["rates"] == [], (
        "the list filter answered with a window that had already ended"
    )
    assert [rate["default_selling_rate"] for rate in unfiltered.json()["rates"]] == [
        str(GAP_LATER_SELLING),
        str(GAP_EARLY_SELLING),
    ], "list_rates orders newest effective_from first (R-02, reviewer 2026-09-21)"


def test_k_04_the_effective_date_is_required_and_never_taken_from_the_clock(
    client: TestClient, db_session: Session
) -> None:
    """`on_date` has no default, and specifically not `date.today()`.

    Added by QA after a surviving mutation: giving the parameter a default of `date.today()`
    (evaluated once, at import) passed all 174 delivered tests, because every one of them sends the
    date explicitly. Both `app.data.catalog.resolve_rate` and the endpoint state in prose that the
    parameter must never come from this process's clock — nothing asserted it.

    Why it matters beyond tidiness: with an implicit "today", the same request answers differently
    tomorrow, a historical or forward-dated question silently becomes a question about now, and
    (with the default bound at import time) the answer depends on when the process was started. It
    is also the class of defect a test suite only catches by accident, and only if it happens to run
    on the wrong day — this repository's mutation log already records one such find.

    The contrast: the identical request *with* the date is a `200`.
    """
    dimensions = _two_windows_with_a_gap(db_session)

    without_date = client.get(
        "/catalog/rates/effective",
        params=dimensions.as_query(),
        headers=as_caller(IN_SCOPE_USER),
    )
    with_date = client.get(
        "/catalog/rates/effective",
        params={**dimensions.as_query(), "on_date": GAP_EARLY[1].isoformat()},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert without_date.status_code == 422, (
        f"the effective date is optional — the endpoint answered {without_date.status_code}, so it "
        "resolved a rate for a day nobody named"
    )
    assert "on_date" in without_date.text
    assert with_date.status_code == 200, with_date.text
    assert with_date.json()["default_selling_rate"] == str(GAP_EARLY_SELLING)


# --- SC-2-03, K-03 / K-04: omitting the vendor means "internal", never "any" ---------------------


VENDOR_SELLING = Decimal("320.0000")
INTERNAL_SELLING = Decimal("180.0000")
COVERED_DAY = date(2026, 3, 15)


def test_k_03_a_lookup_naming_no_vendor_resolves_the_internal_rate_and_never_has_to_choose(
    client: TestClient, db_session: Session
) -> None:
    """K-03 (SC-2-03). One tuple, one day, two rates: the internal one and vendor V's.

    This shape is exactly what SC-2-03 made legal and what the resolution has to survive. A `WHERE`
    clause without a vendor predicate matches both rows, and `resolve_rate` ends in `one_or_none()`
    — deliberately, so a broken invariant raises instead of being resolved by a coin toss. The
    mutation the criterion names (dropping the predicate) therefore does not return the wrong rate;
    it turns a `200` into a `500` on data that is perfectly valid, which is why this test asserts
    the status code as well as the amount.

    The request naming no vendor answers the **internal** rate — the organisation's own price — and
    the identical request naming `V` answers `V`'s. Those two assertions together also kill a
    predicate written backwards (`IS NOT NULL`) and one that ignores the parameter.
    """
    dimensions = make_dimension_tuple(db_session)
    vendor = make_vendor(db_session, name="Contoso")
    make_rate(
        db_session,
        dimensions,
        effective_from=MIDDLE[0],
        effective_to=MIDDLE[1],
        default_selling_rate=INTERNAL_SELLING,
    )
    make_rate(
        db_session,
        dimensions,
        effective_from=MIDDLE[0],
        effective_to=MIDDLE[1],
        vendor_id=vendor.id,
        default_selling_rate=VENDOR_SELLING,
    )

    internal = client.get(
        "/catalog/rates/effective",
        params={**dimensions.as_query(), "on_date": COVERED_DAY.isoformat()},
        headers=as_caller(IN_SCOPE_USER),
    )
    of_the_vendor = client.get(
        "/catalog/rates/effective",
        params={
            **dimensions.as_query(),
            "on_date": COVERED_DAY.isoformat(),
            "vendor_id": str(vendor.id),
        },
        headers=as_caller(IN_SCOPE_USER),
    )

    assert internal.status_code == 200, internal.text
    assert internal.json()["default_selling_rate"] == str(INTERNAL_SELLING), (
        "a request naming no vendor answered something other than the internal rate"
    )
    assert internal.json()["vendor_id"] is None
    assert of_the_vendor.status_code == 200, of_the_vendor.text
    assert of_the_vendor.json()["default_selling_rate"] == str(VENDOR_SELLING)
    assert of_the_vendor.json()["vendor_id"] == str(vendor.id)


def test_k_04_a_tuple_priced_only_by_a_vendor_has_no_internal_rate_rather_than_the_vendors_one(
    client: TestClient, db_session: Session
) -> None:
    """K-04 (SC-2-03). No internal rate is `404` — never a quiet fallback to a subcontractor's.

    A separate criterion from K-03 and a separate mutation: a resolution that fell back (`ORDER BY
    vendor_id NULLS FIRST LIMIT 1`, or any "if nothing internal, take what there is") still answers
    K-03 correctly, because there the internal row exists and sorts first. Only a tuple priced
    *exclusively* by a vendor separates the two.

    The harm is concrete and it is the same one rule 13 names for "latest row wins": a plan quoting
    a subcontractor's price as the organisation's own cost is wrong in the direction nobody checks —
    it looks like an answer. "No internal rate for this tuple" is a fact about the catalogue and has
    to stay visible as one.

    The contrast, in the same test: the identical request naming the vendor answers `200`, so a
    resolution that refused everything would fail here.
    """
    dimensions = make_dimension_tuple(db_session, suffix=" (vendor only)")
    vendor = make_vendor(db_session, name="Fabrikam")
    make_rate(
        db_session,
        dimensions,
        effective_from=MIDDLE[0],
        effective_to=MIDDLE[1],
        vendor_id=vendor.id,
        default_selling_rate=VENDOR_SELLING,
    )

    without_vendor = client.get(
        "/catalog/rates/effective",
        params={**dimensions.as_query(), "on_date": COVERED_DAY.isoformat()},
        headers=as_caller(IN_SCOPE_USER),
    )
    with_vendor = client.get(
        "/catalog/rates/effective",
        params={
            **dimensions.as_query(),
            "on_date": COVERED_DAY.isoformat(),
            "vendor_id": str(vendor.id),
        },
        headers=as_caller(IN_SCOPE_USER),
    )

    assert without_vendor.status_code == 404, (
        "a tuple priced only by a subcontractor answered a request about the organisation's own "
        f"rate: {without_vendor.text}"
    )
    assert str(VENDOR_SELLING) not in without_vendor.text
    assert with_vendor.status_code == 200, with_vendor.text
    assert with_vendor.json()["default_selling_rate"] == str(VENDOR_SELLING)


def test_k_07_an_amount_crosses_the_api_boundary_as_a_fixed_point_string(
    client: TestClient, db_session: Session
) -> None:
    """K-07, second half. An exponent-form Decimal reaches the payload as fixed-point text.

    Two levels, because each catches something the other cannot:

    1. **The schema**, fed the exponent form directly. Pydantic's own Decimal serialisation is
       `str`, which renders `Decimal("1.85E+3")` as `"1.85E+3"` — so this assertion fails the
       moment `DecimalString`'s `format(value, "f")` is replaced by the default, which is the
       mutation SC-1-05 recorded as having survived a "pretty round number" assertion.

       **The exponent must survive `Decimal.__init__` for that to be true, and most spellings do
       not.** QA ran the mutation on the first version of this test, which used
       `Decimal("1.85E+2")`, and it *survived*: that value is `Decimal("185")` when constructed —
       coefficient 185, exponent 0 — so no serializer could ever have rendered it as `"1.85E+2"`
       and the assertion held with `str` just as well as with `format(…, "f")`. `1.85E+3` keeps
       coefficient 185 with exponent 1, i.e. `str` gives `"1.85E+3"` and `format(…, "f")` gives
       `"1850"`, which is the only reason this line now distinguishes the two. Any future edit to
       this literal has to preserve that property — assert it first, below, rather than trust it.
    2. **The endpoint**, for a value stored in the `NUMERIC(14,4)` column. The raw response text is
       inspected, not only the parsed body: `json.loads` turns `185.0` and `"185.0000"` into values
       that compare equal in a lax assertion, and the difference between a JSON number and a JSON
       string is exactly what NF-01/ADR-0002 are about.

    The stored amounts also prove nothing is rounded on the way in: `123.4567` survives at full
    scale (ADR-0008, point 6 — rounding is the consumer's rule, `round_money`, not the catalogue's).
    """
    # The premise of assertion 1: without this, the assertion below is about a value that was
    # already fixed point before any serializer saw it, and it proves nothing about the serializer.
    assert str(Decimal("1.85E+3")) == "1.85E+3", (
        "this literal no longer carries an exponent — the next assertion cannot distinguish "
        "format(value, 'f') from str(value) and is vacuous"
    )

    exponent_form = CatalogRate(
        id=uuid.uuid4(),
        role_id=uuid.uuid4(),
        seniority_id=uuid.uuid4(),
        location_id=uuid.uuid4(),
        engagement_type_id=uuid.uuid4(),
        default_cost_rate=Decimal("1.85E+3"),
        default_selling_rate=Decimal("1.85E+3"),
        currency="EUR",
        unit="hour",
        # SC-5-02: required since the field is never gated (K-04) — a fixed, uninteresting value,
        # this test is about `default_cost_rate`'s exponent form, not about the surcharge.
        surcharge_percent=Decimal("0"),
        includes_surcharge=False,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    ).model_dump(mode="json")

    assert exponent_form["default_selling_rate"] == "1850"
    assert exponent_form["default_cost_rate"] == "1850"
    assert "E" not in exponent_form["default_selling_rate"]

    dimensions = make_dimension_tuple(db_session, suffix=" (stored)")
    make_rate(
        db_session,
        dimensions,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        default_cost_rate=Decimal("123.4567"),
        default_selling_rate=Decimal("1.85E+2"),
    )

    with caller_holding(Permission.CATALOG_READ, Permission.PERSONNEL_COSTS_READ):
        response = client.get(
            "/catalog/rates/effective",
            params={**dimensions.as_query(), "on_date": "2026-03-15"},
            headers=as_caller(IN_SCOPE_USER),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["default_selling_rate"] == "185.0000"
    assert isinstance(body["default_selling_rate"], str)
    assert body["default_cost_rate"] == "123.4567", "the stored scale was rounded away"
    assert '"default_selling_rate":"185.0000"' in response.text.replace(" ", ""), (
        "the amount is a JSON number, not a fixed-point string"
    )
