"""A scenario's commercial rule and its revenue (F-06, F-06.1, F-06.2, F-06.3, F-06.4; SC-4-01,
SC-4-02, SC-4-03, SC-4-04).

Under `/projects/{project_id}/scenarios/{scenario_id}/commercial-terms`:

- `GET  ""` — the rule and the revenue derived from it (`COMMERCIAL_READ`);
- `POST ""` — create the rule, with its details row (`COMMERCIAL_WRITE`) — Time & Material, Story
  Points (SC-4-04, F-06.4), Outcome-based (SC-4-03, F-06.3) or Fixed Price with its agreed price
  (SC-4-02, F-06.2), chosen by `model_type`;
- `PATCH ""` — change the agreed price of a draft's Fixed Price rule (`COMMERCIAL_WRITE`; SC-4-02,
  D-6 = A).

**A router of its own**, not a verb on the staffing or scenario routers: it declares its own pair of
permissions (ADR-0005, addendum 2026-09-23 SC-4-01, point 2), and a shared router would make them
look interchangeable with `STAFFING_*` or `PROJECT_EDIT`.

**The address carries both identifiers**, for the reason every nested path here does (ADR-0001,
addendum 2026-09-19): the scope predicate lives on the project and `project_for_caller` is its only
entry point.

**Every "nothing here for you" answers with one body** (`COMMERCIAL_TERMS_NOT_FOUND_DETAIL`): a
project outside the caller's scope, a project that does not exist and a scenario of another project
are three facts and one `404` — for the write as much as for the read, and before any `409` can be
reached (criterion K-05). A scenario **with no rule** is not one of them: it is a `200` whose
revenue is the named `no_commercial_terms` state, because "no rule yet" is information about a
scenario the caller may see, not about whether it exists.

**One edit path, and no delete path.** The model is immutable after the write (ADR-0003, point 2);
`tm_terms` has no column to edit and neither `story_points_terms` nor `outcome_terms` has an edit
path — a changed Story Points `accepted_points` needs a copy of the scenario (ADR-0003 addendum
2026-09-25, D-5/A), the same mechanism a changed model would; editing and deleting an
Outcome-based rule is a separate task (ADR-0003, addendum 2026-09-25 SC-4-03, point 9). The only
editable value is the Fixed Price agreed price (and its currency), through `PATCH` with ADR-0007's
marker of the rule — guarded, raced and tested like the creation (ADR-0004, addendum 2026-09-25
SC-4-02, point 1).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_scenario_commercial_terms
from app.api.schemas.commercial_terms import (
    CommercialTermsCreateRequest,
    FixedPriceEditRequest,
    FixedPriceTermsCreateRequest,
    OutcomeBasedTermsCreateRequest,
    ScenarioCommercialTerms,
    StoryPointsTermsCreateRequest,
    TimeAndMaterialTermsCreateRequest,
)
from app.core.identity import CallerIdentity, Permission
from app.data.commercial_terms import (
    CommercialTermsNotFound,
    CommercialTermsWriteRefused,
    CommercialTermsWriteRejected,
    commercial_terms_for_caller,
    create_commercial_terms,
    update_fixed_price,
)
from app.db.session import get_session
from app.models.commercial_terms import OUTCOME_CATEGORIES, probability_column, units_column

router = APIRouter(
    prefix="/projects/{project_id}/scenarios/{scenario_id}/commercial-terms",
    tags=["commercial-terms"],
)

COMMERCIAL_TERMS_NOT_FOUND_DETAIL = "Scenario not found."
"""One message and one status for every "there is nothing here for you" case on this path.

The same wording as `app.api.scenarios.SCENARIO_NOT_FOUND_DETAIL`, deliberately: the thing that may
not exist is the scenario, and a second phrasing of the same absence on a sibling path would be one
more string for a client to tell apart. The indistinguishability is not maintained by this constant
alone — `app.data.commercial_terms` returns the same `None` for every such case."""


def _details_of(payload: CommercialTermsCreateRequest) -> dict[str, object]:
    """Domain-specific columns of the details row from the request body — empty for T&M.

    Just field names rewritten as column names (`units_column`/`probability_column` — one
    spelling matching the model); no default value: an omitted optional component stays `None`,
    i.e. `NULL` in the database, never `0` (ADR-0003, addendum 2026-09-25 SC-4-03, point 2).

    **An explicit branch for every model** (verification round 2 of SC-4-03, R-02): a body type
    this function does not know — a model added to the request union without a branch here — is a
    programmer error, and ends in an exception, never a rule written with no parameters.
    """
    if isinstance(payload, TimeAndMaterialTermsCreateRequest):
        return {}
    if isinstance(payload, StoryPointsTermsCreateRequest):
        return {
            "price_per_point": payload.price_per_point,
            "accepted_points": payload.accepted_points,
            "currency": payload.currency,
        }
    if isinstance(payload, OutcomeBasedTermsCreateRequest):
        details: dict[str, object] = {
            "currency": payload.currency,
            "fixed_fee": payload.fixed_fee,
            "success_bonus": payload.success_bonus,
            "unit_rate": payload.unit_rate,
            "revenue_min": payload.revenue_min,
            "revenue_max": payload.revenue_max,
        }
        for category in OUTCOME_CATEGORIES:
            entry = getattr(payload.categories, category)
            details[units_column(category)] = entry.units
            details[probability_column(category)] = entry.probability
        return details
    if isinstance(payload, FixedPriceTermsCreateRequest):
        return {"agreed_price": payload.agreed_price, "currency": payload.currency}
    raise TypeError(
        f"No details mapping for commercial terms payload {type(payload).__name__}: add a "
        "branch to _details_of together with the model's request schema."
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=COMMERCIAL_TERMS_NOT_FOUND_DETAIL
    )


@router.get(
    "",
    response_model=ScenarioCommercialTerms,
    summary="Read a scenario's commercial rule and the revenue derived from it",
    responses={404: {"description": COMMERCIAL_TERMS_NOT_FOUND_DETAIL}},
)
def read_commercial_terms(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.COMMERCIAL_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioCommercialTerms:
    """The rule and the revenue — or a `404` that says nothing about whether the scenario exists.

    - **200** — for a **draft**, priced against the live catalogue; for an **approved** scenario,
      against the windows frozen at its approval, never the live ones (AC-04; criterion K-09). A
      revenue that cannot be stated is `"n/a"` with a named `state` — no rule, an incomplete rule, a
      month with no whole-month rate, mixed currencies — never `0` (K-10).
    - **404** — the scenario is not the caller's, does not exist, or belongs to another project.
    - **403** — the permission dependency, before the database.
    """
    view = commercial_terms_for_caller(session, caller, project_id, scenario_id)
    if view is None:
        raise _not_found()
    return shape_scenario_commercial_terms(view)


@router.post(
    "",
    response_model=ScenarioCommercialTerms,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Set a scenario's commercial rule (Time & Material, Story Points, Outcome-based or Fixed "
        "Price)"
    ),
    responses={
        404: {"description": COMMERCIAL_TERMS_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario is approved (its commercial terms are part of an "
            "approved calculation — copy it to change them), or it already has a rule (one rule "
            "per scenario, refused by the database)."
        },
    },
)
def create_scenario_commercial_terms(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    # `discriminator` restated on `Body()` (SC-4-02, sync of 2026-09-28): the bare `Body()` replaces
    # the `Field(discriminator="model_type")` carried by `CommercialTermsCreateRequest`, and FastAPI
    # then validates the union member by member — an unknown `model_type` came back as one error
    # per model instead of the discriminator's `union_tag_invalid` (ADR-0003 addendum 2026-09-25,
    # D-6/A).
    payload: Annotated[CommercialTermsCreateRequest, Body(discriminator="model_type")],
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.COMMERCIAL_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioCommercialTerms:
    """Create the rule and its details row in one guarded statement — or refuse.

    - **201** — the rule, and the revenue it now yields.
    - **422** — an invalid body, decided before any write: for Outcome-based a probability set that
      is incomplete, does not sum to exactly 100.00 or has a third decimal place, `revenue_min >
      revenue_max`, a negative amount (ADR-0003, addendum 2026-09-25 SC-4-03, points 2-4). Nothing
      is written.
      For Fixed Price (SC-4-02), a rule without its agreed price or currency, or a price more
      precise than `NUMERIC(14,4)`; for any model, an unknown `model_type` or a field the model
      does not take.
    - **404** — as for the `GET`, decided before any `409` can be reached (K-05).
    - **409, "approved"** — refused by the `INSERT … SELECT … WHERE status <> 'approved' FOR
    UPDATE`,
      in the statement that writes (ADR-0004; K-06). Nothing is written.
    - **409, refused by the database** — a second rule for the scenario
      (`uq_commercial_terms_scenario_id`), named by SQLSTATE and constraint, quoting no value.
    - **500** — a write that broke for a reason no SQLSTATE classified; never dressed as a `409`.
    - **403** — the permission dependency, before the database.
    """
    try:
        view = create_commercial_terms(
            session,
            caller,
            project_id,
            scenario_id,
            model_type=payload.model_type,
            domain_values=_details_of(payload),
        )
    except CommercialTermsWriteRejected as refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    except CommercialTermsWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Refused by the database. {refusal}"
        ) from None
    if view is None:
        raise _not_found()
    return shape_scenario_commercial_terms(view)


@router.patch(
    "",
    response_model=ScenarioCommercialTerms,
    summary="Change the agreed price of a draft scenario's Fixed Price rule",
    responses={
        404: {
            "description": f"{COMMERCIAL_TERMS_NOT_FOUND_DETAIL} — or the scenario (in scope) has "
            "no Fixed Price commercial terms to edit."
        },
        409: {
            "description": "Refused: the scenario is approved (copy it to change the "
            "price), or the commercial terms changed since they were read (concurrency "
            "marker: re-read them), or the database refused the value (named by its "
            "constraint)."
        },
    },
)
def edit_scenario_fixed_price(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: FixedPriceEditRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.COMMERCIAL_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioCommercialTerms:
    """Change the price in one guarded statement — or refuse (SC-4-02, D-6 = A).

    - **200** — the rule with its new price and new marker, and the revenue it now yields.
    - **404** — the scenario is not the caller's, does not exist or belongs to another project
      (the `GET`'s body, decided first — K-05); or, for a scenario in scope, it has no Fixed Price
      rule with a price to edit (even when it is `approved`: copying would not help).
    - **409, "approved"** — refused inside the `UPDATE … WHERE … status <> 'approved' FOR UPDATE`
      (ADR-0004). Permanent. Nothing is written.
    - **409, "concurrency marker"** — `updated_at` is not the rule's current marker (ADR-0007).
      Transient: re-read and apply again. The two `409`s are told apart by their message.
    - **409, refused by the database** — e.g. a negative price that got past the schema,
      `ck_fixed_price_terms_agreed_price_non_negative`.
    - **422** — no field to change, a `null`, `model_type` or any other field, or a price more
      precise than `NUMERIC(14,4)`.
    - **403** — the permission dependency, before the database.
    """
    try:
        view = update_fixed_price(
            session,
            caller,
            project_id,
            scenario_id,
            expected_updated_at=payload.updated_at,
            changes=payload.changes(),
        )
    except CommercialTermsNotFound as missing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(missing)) from None
    except CommercialTermsWriteRejected as refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    except CommercialTermsWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Refused by the database. {refusal}"
        ) from None
    if view is None:
        raise _not_found()
    return shape_scenario_commercial_terms(view)
