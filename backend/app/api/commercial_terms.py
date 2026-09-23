"""A scenario's commercial rule and its revenue (F-06, F-06.1; SC-4-01).

Under `/projects/{project_id}/scenarios/{scenario_id}/commercial-terms`:

- `GET  ""` — the rule and the revenue derived from it (`COMMERCIAL_READ`);
- `POST ""` — create the rule, with its details row (`COMMERCIAL_WRITE`).

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

**No edit and no delete path.** The model is immutable after the write (ADR-0003, point 2) and
`tm_terms` has no column to edit; the one write path is the one guarded, raced and tested.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_scenario_commercial_terms
from app.api.schemas.commercial_terms import (
    CommercialTermsCreateRequest,
    ScenarioCommercialTerms,
)
from app.core.identity import CallerIdentity, Permission
from app.data.commercial_terms import (
    CommercialTermsWriteRefused,
    CommercialTermsWriteRejected,
    commercial_terms_for_caller,
    create_commercial_terms,
)
from app.db.session import get_session

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
    summary="Set a scenario's commercial rule (Time & Material)",
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
    payload: CommercialTermsCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.COMMERCIAL_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioCommercialTerms:
    """Create the rule and its details row in one guarded statement — or refuse.

    - **201** — the rule, and the revenue it now yields.
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
            session, caller, project_id, scenario_id, model_type=payload.model_type
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
