"""SC-2-01, K-01 and K-02 — the catalogue is organisational, and it still denies by default.

Two claims that pull in opposite directions and are proven here side by side:

- **K-01: no subject predicate.** A caller with zero `project_access` rows sees the same catalogue
  as everybody else. The catalogue is the first set of data in this system that belongs to no
  project (ADR-0005, addendum 2026-09-19 "first dataset without project scope", SC-2-01, point
  1), so the absence of a scope filter here is a decision — and the contrast in the same test is
  that the *same* caller's project list is empty, which is a different boundary guarded by a
  different mechanism.
- **K-02: deny by default.** "No scope" is not "no permission". Every catalogue endpoint declares
  `CATALOG_READ` or `CATALOG_WRITE`, and the denied caller in every test below holds *all five
  project permissions* — so a `require_permission` mutated to any project permission would let
  them through and these tests would fail. A denied caller holding nothing at all would prove
  much less.

SC-2-03 adds **K-05**: the vendor dictionary is the fifth entry in `DIMENSION_MODELS` and no
mechanism of its own. It belongs in this file because the claim is about the guards — the same two
permissions, no third one — and because the two tests above already cover vendors without being
edited, deriving their paths from that mapping."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.data.catalog import DIMENSION_MODELS
from app.models import CatalogRole, WorkingCalendarDayKind
from tests.conftest import (
    IN_SCOPE_USER,
    UNKNOWN_USER,
    DimensionTuple,
    as_caller,
    caller_holding,
    count_dimension_entries,
    count_rates,
    make_absence_type,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_vendor,
    make_working_calendar,
    rate_payload,
)

WINDOW_START = date(2026, 1, 1)
WINDOW_END = date(2026, 6, 30)

EVERY_PROJECT_PERMISSION = (
    Permission.PROJECT_READ,
    Permission.PROJECT_CREATE,
    Permission.PROJECT_EDIT,
    Permission.PROJECT_COPY,
    Permission.PROJECT_ARCHIVE,
)
"""What the denied caller in the K-02 tests holds instead of a catalogue permission.

The point of handing them everything *but* `CATALOG_*`: ADR-0005's addendum (point 2) refuses to
widen `PROJECT_READ` onto the catalogue, and that refusal is only tested if a caller with full
project rights is still refused. It also makes the mutation K-02 names — swapping the required
permission for one the denied caller holds — fail these tests rather than survive them."""


def _dimension_paths() -> list[str]:
    """Every dictionary endpoint, from the mapping the router itself uses.

    Derived rather than typed out, so a fifth dictionary added later is covered by these tests
    on the day it appears instead of on the day somebody remembers to extend a literal list."""
    return [f"/catalog/dimensions/{segment}" for segment in sorted(DIMENSION_MODELS)]


def _one_rate(session: Session) -> DimensionTuple:
    dimensions = make_dimension_tuple(session)
    make_rate(session, dimensions, effective_from=WINDOW_START, effective_to=WINDOW_END)
    return dimensions


def test_k_01_a_caller_with_no_project_access_sees_the_whole_catalogue(
    client: TestClient, db_session: Session
) -> None:
    """K-01. The catalogue is not narrowed per caller; the project list is. One test, both facts.

    `UNKNOWN_USER` holds no `project_access` row at all — neither granted nor denied. Their project
    list is empty and their catalogue is not, while `IN_SCOPE_USER` (who has a project) receives an
    identical catalogue payload. Both mutations K-01 names die here:

    - a subject filter on the catalogue read (a join to `project_access`) empties `UNKNOWN_USER`'s
      rates and dictionaries, and makes the two callers' payloads differ;
    - a catalogue read returning a constant empty list fails the assertions naming the row that must
      be present — which is why the entry name and the rate's tuple are asserted, not just a length.
    """
    dimensions = _one_rate(db_session)
    make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    with_projects = {
        path: client.get(path, headers=as_caller(IN_SCOPE_USER)) for path in _dimension_paths()
    }
    with_projects["/catalog/rates"] = client.get(
        "/catalog/rates", headers=as_caller(IN_SCOPE_USER)
    )
    without_projects = {
        path: client.get(path, headers=as_caller(UNKNOWN_USER)) for path in _dimension_paths()
    }
    without_projects["/catalog/rates"] = client.get(
        "/catalog/rates", headers=as_caller(UNKNOWN_USER)
    )
    projects_of_each = {
        user: client.get("/projects", headers=as_caller(user))
        for user in (IN_SCOPE_USER, UNKNOWN_USER)
    }

    # The contrast, first: the project boundary is real and it is a different mechanism.
    assert [row["name"] for row in projects_of_each[IN_SCOPE_USER].json()["projects"]] == [
        "Aurora migration"
    ]
    assert projects_of_each[UNKNOWN_USER].json()["projects"] == [], (
        "the caller without project access can see a project — the contrast in this test is void"
    )

    for path, response in without_projects.items():
        assert response.status_code == 200, f"{path}: {response.text}"
        assert response.json() == with_projects[path].json(), (
            f"{path} answers differently for a caller with no project access"
        )

    # Named rows, so a constant empty list cannot satisfy the equality above.
    assert "Backend Engineer" in [
        entry["name"] for entry in without_projects["/catalog/dimensions/roles"].json()["entries"]
    ]
    rates = without_projects["/catalog/rates"].json()["rates"]
    assert [rate["role_id"] for rate in rates] == [str(dimensions.role_id)]


def test_the_catalogue_scope_exception_covers_vendor_rows_and_the_vendor_dictionary(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0005, addendum 2026-09-21, point 1 — in the one fixture that can show it (SC-2-03, QA).

    That point says SC-2-01's K-01 "applies unchanged and covers subcontractor rows": the
    catalogue has no per-caller predicate, *including* on the rows that name a subcontractor. The
    test above derives its paths from `DIMENSION_MODELS`, so it does call
    `/catalog/dimensions/vendors` — but its fixture creates no vendor and no vendor rate, so for
    this dimension it compares `[]` with `[]` and asserts nothing at all. A narrowing applied only
    to vendors (the "price list of vendor X only for roles working with X" model ADR-0005's
    addendum point 4 puts out of scope and makes expiry-triggering) survives it.

    Measured, not assumed: that narrowing — vendor rows and vendor entries hidden from a caller
    with no `project_access` row — left this file's K-01 green. It was caught only by K-05 and by
    K-06, i.e. by the dictionary-mechanism test and by the cost-gate test, whose fixtures happen to
    contain a vendor. Neither of them is a claim about scope, and either one's fixture could stop
    containing a vendor without anybody noticing what it had been holding up.

    So the fixture here carries what the claim is about: one tuple priced **both** internally and
    by a subcontractor — the coexistence SC-2-03 exists to allow — plus a vendor in the dictionary.

    Two contrasts, both in this test:

    - across callers on the same data: `UNKNOWN_USER` (no `project_access` row at all) and
      `IN_SCOPE_USER` (who owns a project) receive byte-identical catalogue payloads, while their
      project lists differ. The scope boundary is real; it just is not this one.
    - within the payload: the vendor row and the vendor entry are asserted **by value**, so a read
      that answered a constant empty list — the way a negative test is most often satisfied — fails
      here instead of passing.
    """
    dimensions = make_dimension_tuple(db_session)
    vendor = make_vendor(db_session, name="Contoso Sp. z o.o.")
    make_rate(db_session, dimensions, effective_from=WINDOW_START, effective_to=WINDOW_END)
    make_rate(
        db_session,
        dimensions,
        effective_from=WINDOW_START,
        effective_to=WINDOW_END,
        vendor_id=vendor.id,
    )
    make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    paths = ("/catalog/rates", "/catalog/dimensions/vendors")
    with_projects = {path: client.get(path, headers=as_caller(IN_SCOPE_USER)) for path in paths}
    without_projects = {path: client.get(path, headers=as_caller(UNKNOWN_USER)) for path in paths}
    projects_of_each = {
        user: client.get("/projects", headers=as_caller(user))
        for user in (IN_SCOPE_USER, UNKNOWN_USER)
    }

    # The contrast that keeps this from being a test of two empty payloads: the same pair of
    # callers is told different things about projects.
    assert [row["name"] for row in projects_of_each[IN_SCOPE_USER].json()["projects"]] == [
        "Aurora migration"
    ]
    assert projects_of_each[UNKNOWN_USER].json()["projects"] == [], (
        "the caller without project access can see a project — the contrast in this test is void"
    )

    for path, response in without_projects.items():
        assert response.status_code == 200, f"{path}: {response.text}"
        assert response.json() == with_projects[path].json(), (
            f"{path} answers differently for a caller with no project access — a per-caller "
            "predicate on subcontractor data expires both the ADR-0001 and the ADR-0005 exception"
        )

    # By value, on the caller who has nothing: the subcontractor's price row is present, and it is
    # present *beside* the internal one for the same tuple and window.
    listed = without_projects["/catalog/rates"].json()["rates"]
    assert sorted((rate["vendor_id"] for rate in listed), key=str) == sorted(
        [None, str(vendor.id)], key=str
    ), (
        "a caller with no project access did not receive both the internal rate and the vendor's"
    )
    assert [entry["name"] for entry in without_projects[paths[1]].json()["entries"]] == [
        "Contoso Sp. z o.o."
    ], "the vendor dictionary was narrowed for a caller with no project access"


def test_k_02_reading_the_catalogue_is_denied_without_catalog_read(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the read half: `403` for a caller with every project permission and no catalogue one.

    Every catalogue read path is asserted, the dictionaries and the two rate paths alike — a guard
    is per endpoint, so "the catalogue denies" is a claim about all of them and not about the first
    one. The payload is checked to carry no catalogue content, because a `403` whose body still
    named the row would be a leak with a status code on it.
    """
    dimensions = _one_rate(db_session)
    read_paths = [
        *_dimension_paths(),
        "/catalog/rates",
        "/catalog/rates/effective",
    ]

    with caller_holding(*EVERY_PROJECT_PERMISSION):
        responses = {
            path: client.get(
                path,
                params=(
                    {**dimensions.as_query(), "on_date": WINDOW_START.isoformat()}
                    if path.endswith("/effective")
                    else {}
                ),
                headers=as_caller(IN_SCOPE_USER),
            )
            for path in read_paths
        }

    for path, response in responses.items():
        assert response.status_code == 403, f"{path}: {response.status_code} {response.text}"
        assert "Backend Engineer" not in response.text, path
        assert str(dimensions.role_id) not in response.text, path


def test_k_02_reading_the_catalogue_is_denied_without_any_identity(
    client: TestClient, db_session: Session
) -> None:
    """K-02. No identity at all is a `401`, before any permission question and before the
    database."""
    _one_rate(db_session)

    response = client.get("/catalog/rates")

    assert response.status_code == 401
    assert "default_cost_rate" not in response.text


def test_k_02_writing_a_dimension_entry_is_denied_without_catalog_write(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the write half for the dictionaries: `CATALOG_READ` is not enough, nothing is saved.

    Zero rows written is asserted from the database, not inferred from the status code: a refusal
    that answered `403` after having inserted the row would satisfy a status-only assertion.
    """
    with caller_holding(Permission.CATALOG_READ, *EVERY_PROJECT_PERMISSION):
        response = client.post(
            "/catalog/dimensions/roles",
            json={"name": "Backend Engineer"},
            headers=as_caller(IN_SCOPE_USER),
        )

    assert response.status_code == 403, response.text
    assert count_dimension_entries(db_session, CatalogRole) == 0


def test_k_02_writing_a_rate_is_denied_without_catalog_write(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the write half for rates: a reader cannot write one, and the table stays empty.

    The caller here holds `CATALOG_READ` — the closest permission there is — plus every project
    permission, so the refusal cannot be an accident of holding nothing.
    """
    dimensions = make_dimension_tuple(db_session)

    with caller_holding(Permission.CATALOG_READ, *EVERY_PROJECT_PERMISSION):
        response = client.post(
            "/catalog/rates", json=rate_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
        )

    assert response.status_code == 403, response.text
    assert count_rates(db_session) == 0


def test_k_02_a_caller_holding_the_catalogue_permissions_is_not_refused(
    client: TestClient, db_session: Session
) -> None:
    """The positive side of K-02 — without it, a guard that refused everybody would pass the four
    tests above and the catalogue would be unreachable rather than protected.

    Run as the placeholder identity, i.e. the caller the running system actually has (ADR-0005,
    addendum 2026-09-19 point 6 adds `CATALOG_READ`/`CATALOG_WRITE` to it and nothing else).
    """
    dimensions = make_dimension_tuple(db_session)

    created = client.post(
        "/catalog/dimensions/locations",
        json={"name": "Germany"},
        headers=as_caller(IN_SCOPE_USER),
    )
    listed = client.get("/catalog/dimensions/locations", headers=as_caller(IN_SCOPE_USER))
    rate = client.post(
        "/catalog/rates", json=rate_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
    )

    assert created.status_code == 201, created.text
    assert listed.status_code == 200, listed.text
    assert "Germany" in [entry["name"] for entry in listed.json()["entries"]]
    assert rate.status_code == 201, rate.text


def test_k_05_the_vendor_dictionary_is_the_fifth_dictionary_not_a_fifth_mechanism(
    client: TestClient, db_session: Session
) -> None:
    """K-05 (SC-2-03). Vendors arrive as a row in `DIMENSION_MODELS` and nothing else.

    Four claims in one test, because they are one claim: the vendor dictionary reuses the mechanism
    the other four use, so there is nothing vendor-specific to get wrong.

    1. `"vendors"` is in `DIMENSION_MODELS`, which is what makes the existing pair of endpoints
       serve it — and what makes every other test in this file (the K-01 catalogue comparison, the
       K-02 denials) cover it automatically, since they derive their paths from that mapping.
    2. `GET` and `POST` work through the same `CATALOG_READ`/`CATALOG_WRITE` gates. **No new
       permission was added**, which was a business decision at gate 1 and not an omission
       (ADR-0005, addendum 2026-09-21, point 2): everyone who may read the catalogue sees every
       subcontractor's price list. The permission canary in `test_access_control.py` stays exactly
       as it was — asserted here by name, so a vendor-specific permission slipped into the enum
       fails this test rather than passing unnoticed.
    3. A duplicate name is refused by the normalised-name index, like every other dictionary: two
       "Contoso"s would split one supplier's price list into two halves that look like one (R-04).
    4. The contrast: a caller with every project permission and no catalogue one is refused on both
       verbs, and nothing is written.

    The mutation the criterion names — deleting the `"vendors"` entry from `DIMENSION_MODELS` —
    fails assertion 1 immediately and takes the endpoints with it.
    """
    from app.core.identity import Permission as PermissionEnum
    from app.models import CatalogVendor

    assert "vendors" in DIMENSION_MODELS
    assert DIMENSION_MODELS["vendors"] is CatalogVendor
    assert "/catalog/dimensions/vendors" in _dimension_paths()
    assert not [name for name in PermissionEnum if "vendor" in name.lower()], (
        "a vendor-specific permission exists — gate 1 decided CATALOG_READ/CATALOG_WRITE cover "
        "subcontractors, and a new permission needs its own dated entry in ADR-0005"
    )

    created = client.post(
        "/catalog/dimensions/vendors",
        json={"name": "Contoso Sp. z o.o."},
        headers=as_caller(IN_SCOPE_USER),
    )
    duplicate = client.post(
        "/catalog/dimensions/vendors",
        json={"name": "  contoso   sp. z o.o. "},
        headers=as_caller(IN_SCOPE_USER),
    )
    listed = client.get("/catalog/dimensions/vendors", headers=as_caller(IN_SCOPE_USER))

    assert created.status_code == 201, created.text
    assert duplicate.status_code == 409, duplicate.text
    assert "uq_catalog_vendors_name_normalized" in duplicate.json()["detail"]
    assert listed.status_code == 200, listed.text
    assert [entry["name"] for entry in listed.json()["entries"]] == ["Contoso Sp. z o.o."]
    assert count_dimension_entries(db_session, CatalogVendor) == 1

    with caller_holding(*EVERY_PROJECT_PERMISSION):
        denied_read = client.get(
            "/catalog/dimensions/vendors", headers=as_caller(IN_SCOPE_USER)
        )
        denied_write = client.post(
            "/catalog/dimensions/vendors",
            json={"name": "Fabrikam"},
            headers=as_caller(IN_SCOPE_USER),
        )

    assert denied_read.status_code == 403, denied_read.text
    assert "Contoso" not in denied_read.text
    assert denied_write.status_code == 403, denied_write.text
    assert count_dimension_entries(db_session, CatalogVendor) == 1, "the refused write was saved"


SC_3_02_CATALOGUE_PATHS = ("/catalog/working-calendars", "/catalog/absence-types")
"""The two SC-3-02 catalogue endpoints, spelled once for the K-09 assertions below."""


def test_k_09_the_calendar_and_absence_type_dictionaries_are_the_sixth_and_seventh_dictionaries_not_a_new_mechanism(  # noqa: E501 — the criterion names this test; the name is the contract, not a style choice
    client: TestClient, db_session: Session
) -> None:
    """K-09 (SC-3-02) — same exemption from scope, same permission pair, **no new permission**.

    The claim has three halves and each is one of the criterion's contrasts.

    **(a) No subject predicate.** `UNKNOWN_USER` holds no `project_access` row at all and receives
    both dictionaries in full — byte for byte the payload `IN_SCOPE_USER` receives, while that same
    caller's project list is empty. The mutation "a join to `project_access` added to the calendar
    read" empties one of the two payloads and makes them differ.

    **(b) Deny by default.** The denied caller holds *every project permission* and is still
    refused, so the mutation "`require_permission(CATALOG_READ)` swapped for `PROJECT_READ`" lets
    them through and fails here. A caller holding nothing at all would prove much less.

    **(c) The canary: `Permission` still has exactly ten members.** ADR-0005's addendum of
    2026-09-22 (point 3) says in so many words that these are the sixth and seventh dictionaries and
    that `PLACEHOLDER_PERMISSIONS` does not grow. A `CALENDAR_READ` or an `ABSENCE_READ` added "for
    clarity" fails this assertion the day it is written, which is the only moment at which undoing
    it is cheap. Asserted by set *equality*, not by length: a permission renamed or swapped keeps
    the count and changes the meaning.

    The named rows are what stop (a) from being satisfied by two endpoints that both return nothing.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    make_calendar_day(
        db_session, calendar, day=date(2026, 12, 25), kind=WorkingCalendarDayKind.NON_WORKING
    )
    make_absence_type(db_session, name="Paid holiday")
    make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    with_projects = {
        path: client.get(path, headers=as_caller(IN_SCOPE_USER))
        for path in SC_3_02_CATALOGUE_PATHS
    }
    without_projects = {
        path: client.get(path, headers=as_caller(UNKNOWN_USER))
        for path in SC_3_02_CATALOGUE_PATHS
    }

    # (a) the contrast, first: the project boundary is real and it is a different mechanism.
    assert client.get("/projects", headers=as_caller(UNKNOWN_USER)).json()["projects"] == []
    for path, response in without_projects.items():
        assert response.status_code == 200, f"{path}: {response.text}"
        assert response.json() == with_projects[path].json(), (
            f"{path} answers differently for a caller with no project access"
        )

    # Named rows, so a pair of endpoints returning constant empty lists cannot satisfy the above.
    calendars = without_projects["/catalog/working-calendars"].json()["calendars"]
    assert [entry["name"] for entry in calendars] == ["Poland 7.5h"]
    assert calendars[0]["standard_hours_per_day"] == "7.50"
    assert [day["day"] for day in calendars[0]["days"]] == ["2026-12-25"]
    types = without_projects["/catalog/absence-types"].json()["absence_types"]
    assert [entry["name"] for entry in types] == ["Paid holiday"]

    # (b) every project permission, and still refused.
    with caller_holding(*EVERY_PROJECT_PERMISSION):
        denied = {
            path: client.get(path, headers=as_caller(IN_SCOPE_USER))
            for path in SC_3_02_CATALOGUE_PATHS
        }
    for path, response in denied.items():
        assert response.status_code == 403, f"{path}: {response.text}"
        assert "Poland 7.5h" not in response.text
        assert "Paid holiday" not in response.text

    # (c) the canary.
    assert set(Permission) == {
        Permission.PROJECT_READ,
        Permission.PROJECT_CREATE,
        Permission.PROJECT_EDIT,
        Permission.PROJECT_COPY,
        Permission.PROJECT_ARCHIVE,
        Permission.PERSONNEL_COSTS_READ,
        Permission.CATALOG_READ,
        Permission.CATALOG_WRITE,
        Permission.STAFFING_READ,
        Permission.STAFFING_WRITE,
        # Re-armed in SC-4-01, not loosened: ADR-0005's addendum of 2026-09-23 (SC-4-01, point 2)
        # adds this pair for the commercial rule — a table of a scenario, not of the catalogue — so
        # the claim of this canary (the catalogue tables take no permission of their own) holds.
        Permission.COMMERCIAL_READ,
        Permission.COMMERCIAL_WRITE,
        # Re-armed in SC-7-01, not loosened: ADR-0005's 2026-09-24 addendum adds this one for
        # the whole-scenario result — a composition over three already-permissioned calculations,
        # not a catalogue table — so the claim of this canary holds unchanged.
        Permission.RESULTS_READ,
        # Re-armed in SC-6-01 (Issue #11, gate 1 decision 2), not loosened: `SCENARIO_COPY` is a
        # scenario action, not a catalogue table, so this canary's claim is unaffected by it.
        Permission.SCENARIO_COPY,
        # Re-armed in SC-2-06 (Issue #31, gate 1 decision 6), not loosened: ADR-0005's addendum of
        # 2026-09-27 (point 3) adds this pair for the person register — a register of its own,
        # explicitly *not* a catalogue dictionary — so this canary's claim holds unchanged.
        Permission.PEOPLE_READ,
        Permission.PEOPLE_WRITE,
        # Added by SC-4-09 for explicit Fixed Price adjustment decisions.
        Permission.COMMERCIAL_ADJUSTMENT_APPROVE,
    }, (
        "the permission vocabulary changed in SC-3-02. ADR-0005's addendum of 2026-09-22 (point 3) "
        "decides that calendars and absence types are the sixth and seventh dictionaries of the "
        "catalogue and take no permission of their own."
    )
    assert len(Permission) == 17


def test_k_09_the_two_new_dictionaries_hold_a_caller_with_no_identity_out_as_well(
    client: TestClient, db_session: Session
) -> None:
    """K-09's deny-by-default half, one step further out: no identity header at all.

    A `401` and not a `200` with an empty body: the catalogue has no scope, but "no scope" is not
    "no authentication", and an endpoint reachable without a caller would be the one place where
    that distinction is quietly lost. The same claim
    `test_k_02_reading_the_catalogue_is_denied_without_any_identity` makes for the first five
    dictionaries.
    """
    make_working_calendar(db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50"))

    for path in SC_3_02_CATALOGUE_PATHS:
        response = client.get(path)
        assert response.status_code == 401, f"{path}: {response.text}"
        assert "Poland 7.5h" not in response.text


def test_an_unknown_dimension_segment_is_a_404_naming_the_known_ones(
    client: TestClient,
) -> None:
    """Not a criterion — the branch that resolves a path segment to a table.

    A `404` rather than a silent fallback to the first dictionary: reading a seniority list
    under the name "grades" and getting roles back is the kind of wrong answer no later test
    would question."""
    response = client.get("/catalog/dimensions/grades", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 404, response.text
    assert "roles" in response.json()["detail"]
