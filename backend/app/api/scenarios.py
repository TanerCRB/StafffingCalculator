"""Scenario endpoints — approving a calculation (ADR-0004, F-12; SC-3-02), reading its
assumptions (F-02; SC-1-10), and duplicating it into its own project (F-09 pt.1, AC-02; SC-6-01).

Three endpoints:

- `POST /projects/{project_id}/scenarios/{scenario_id}/approve` — the one-way, human-performed step
  of ADR-0004, and the first path in this repository that ever sets `ScenarioStatus.APPROVED`;
- `GET /projects/{project_id}/scenarios/{scenario_id}/assumptions` — the scenario's resolved
  assumptions with the level each came from (ADR-0012), and the first path that reads an approval
  snapshot back (gate 1, P-B);
- `POST /projects/{project_id}/scenarios/{scenario_id}/duplicate` — the second of ADR-0004's three
  entry points into `app.data.project_writes.copy_scenario` (the first is
  `POST /projects/{id}/copy`, SC-1-03), the one that copies a scenario into its **own** project
  instead of a new one (SC-6-01, Issue #11).

**The address carries both identifiers**, like the staffing path and for the same reason (ADR-0001,
addendum 2026-09-19): the scope predicate lives on the project, `project_for_caller` is the one
entry point that applies it, and an address without the project id would need a second scope
function to compose with. There is none, and there must not be one.

**A router of its own rather than a fourth verb on the staffing router.** Approving is not a
staffing write — it freezes the whole scenario, including tables SC-3-02 does not create — and a
shared router would make `STAFFING_WRITE` and the permission below look interchangeable.

**What this endpoint does not decide: who may approve** (ADR-0005, addendum 2026-09-22, point 9,
and ADR-0004, addendum of the same date, point 5). It is stated there as an accepted, dated risk,
and it is repeated here because this is the file somebody will read first:

- the placeholder identity carries **no role dimension**, so no permission in this system
  distinguishes "may plan a scenario" from "may freeze it irreversibly";
- `PROJECT_EDIT` below is declared so that the deny-by-default rule holds at all, and is **not** a
  decision that everyone holding it may approve. It is in `PLACEHOLDER_PERMISSIONS`, which is what
  makes the endpoint reachable while every caller is one fixed placeholder — the same reservation
  every other write permission in this repository carries;
- there is **no audit trail**: `audit_log` is deferred to plan block 8, so an irreversible action
  leaves no record of who performed it or when.

Closing conditions, both named at gate 1: the authentication ADR (for the role) and plan block 8
(for `audit_log`). Until then an approval in a development or test environment can be performed by
any caller who can reach the port.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_duplicated_scenario, shape_scenario_assumptions
from app.api.schemas.project import ScenarioListItem
from app.api.schemas.scenario import (
    ApprovedSnapshotCounts,
    ScenarioApproval,
    ScenarioAssumptions,
)
from app.core.identity import CallerIdentity, Permission
from app.data.assumptions import scenario_assumptions_for_caller
from app.data.organization_defaults import organization_level_for
from app.data.scenario_approval import (
    ScenarioApprovalRefused,
    ScenarioApprovalRejected,
    approve_scenario,
)
from app.data.scenario_duplication import (
    NoAvailableDuplicateName,
    ScenarioDuplicationRefused,
    duplicate_scenario,
)
from app.db.session import get_session

router = APIRouter(prefix="/projects/{project_id}/scenarios/{scenario_id}", tags=["scenarios"])

SCENARIO_NOT_FOUND_DETAIL = "Scenario not found."
"""One message and one status code for every "there is nothing here for you" case on this path.

Deliberately vague, and deliberately *not* distinguishable from the answer for a scenario that is
already approved but outside the caller's scope (criterion K-21). Naming "already approved" for a
scenario the caller may not see would confirm both its existence and its state — the two facts
ADR-0005's addendum of 2026-09-18 (point 3) puts out of reach. The indistinguishability is not
maintained by this constant: `approve_scenario` resolves the scope before it can reach the state, so
there is nothing here to tell apart."""


@router.post(
    "/approve",
    response_model=ScenarioApproval,
    summary="Approve one scenario: freeze it and snapshot the values it was approved with",
    responses={
        404: {"description": SCENARIO_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario is already approved (approving is one-way — copy "
            "it to open a new version), or the state of the data refused the write."
        },
    },
)
def approve(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PROJECT_EDIT))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioApproval:
    """Freeze one scenario and snapshot the organisational values it was approved with.

    **No request body.** There is nothing to configure: the snapshot is derived from what the
    scenario already points at, and a body would be a place for a client to influence what gets
    frozen. It also means the call is not idempotent in the HTTP sense and cannot be made so — a
    second call is a `409`, which is the honest answer for an operation that has already happened
    once (and a stronger one than `200`, because "approved twice" is a state worth noticing).

    The answers, all decided in `app.data.scenario_approval`:

    - **200** — frozen, with the counts of what was snapshotted per table. The counts are what a
      caller checks against what they expected; nothing reads a snapshot row back yet.
    - **404** — the scenario is not the caller's, does not exist, or belongs to another project.
      Also the answer for an *approved* scenario outside the caller's scope (K-21): `404` takes
      precedence over `409`, structurally, because the scope is resolved first.
    - **409** — there is no draft left to approve. Approving is one-way (ADR-0004); the way forward
      is a copy, which starts as a `draft`.
    - **500** — the approval broke for a reason no SQLSTATE classified. Deliberately not turned
      into a `409`: "something broke" is the true answer and a plausible false one is worse (R-01).
      The scenario is left `draft` with zero snapshot rows in that case (K-18).
    - **403** — the permission dependency. It says the caller may edit projects; it does **not**
      say they may approve (see the module docstring).
    """
    try:
        result = approve_scenario(session, caller, project_id, scenario_id)
    except ScenarioApprovalRejected as refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    except ScenarioApprovalRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Refused by the database. {refusal}"
        ) from None
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_NOT_FOUND_DETAIL
        )
    return ScenarioApproval(
        id=result.scenario_id,
        status="Approved",
        snapshot=ApprovedSnapshotCounts(
            working_calendars=result.working_calendars,
            working_calendar_days=result.working_calendar_days,
            absence_types=result.absence_types,
            absence_budgets=result.absence_budgets,
            organization_defaults=result.organization_defaults,
            catalog_default_rates=result.catalog_default_rates,
        ),
    )


@router.get(
    "/assumptions",
    response_model=ScenarioAssumptions,
    summary="Read a scenario's resolved assumptions and the level each one came from",
    responses={404: {"description": SCENARIO_NOT_FOUND_DETAIL}},
)
def read_assumptions(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PROJECT_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioAssumptions:
    """The target margin and the overload threshold of one scenario, resolved scenario → project →
    organisation (ADR-0012), each with its value, its state and its source.

    - **200** — for a **draft**, against the organisation's live defaults (gate 1, Q-1); for an
      **approved** scenario, against the defaults frozen at its approval, never the live ones
      (AC-04). A missing value is `"n/a"` with state `"no_value"` and no source — never `0`.
    - **404** — the scenario is not the caller's, does not exist, or belongs to another project:
      one answer for all three (ADR-0005), because the scenario is resolved through
      `project_for_caller` and there is nothing here to tell them apart. Never `403` for a row.
    - **403** — the permission dependency, before the database.

    `PROJECT_READ`, the permission every other read of a project's contents declares: the values
    are part of what a caller who may read the project may read.
    """
    view = scenario_assumptions_for_caller(session, caller, project_id, scenario_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_NOT_FOUND_DETAIL
        )
    return shape_scenario_assumptions(view)


@router.post(
    "/duplicate",
    response_model=ScenarioListItem,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate one scenario into the same project as its source",
    responses={
        404: {"description": SCENARIO_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: no unused name could be generated for the duplicate, or a "
            "concurrent duplicate took the chosen name first. Nothing was written."
        },
    },
)
def duplicate(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.SCENARIO_COPY))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioListItem:
    """Duplicate a scenario into its own project (F-09 pt.1, AC-02; SC-6-01) — never into a new
    one, which is what `POST /projects/{id}/copy` (SC-1-03) is for.

    **No request body**, like `POST /projects/{id}/archive` and `POST /projects/{id}/copy`: there
    is nothing to configure. In particular the duplicate's name is never taken from the client —
    `scenarios` carries `UniqueConstraint("project_id", "name")`, the target project already holds
    a scenario named exactly `source.name` (the source itself), and a client-supplied name would
    just move the collision from this endpoint's naming logic to the caller's screen. Instead the
    name is generated: `"<source.name> (copy)"`, then `"(copy 2)"`, `"(copy 3)"`, … — the first not
    already used by a sibling scenario of the same project (`app.data.scenario_duplication`,
    gate 1 decision 1).

    - **201** — the duplicate, shaped exactly like a scenario row of `ProjectDetail.scenarios`. It
      is always `draft` (K-02), whatever the source's status, and the source's own row — name,
      status, every child table — is untouched.
    - **404** — the scenario is not the caller's, does not exist, or belongs to a different
      project than `project_id` names: one answer for all three (ADR-0005), because the scenario
      is resolved through `scenario_in_scope` → `project_for_caller`, exactly as `approve` and
      `read_assumptions` above resolve theirs, and there is nothing here to tell them apart. This
      runs, and fails closed, **before** any candidate name is computed — a 404-vs-409 precedence
      that is structural rather than a rule to remember, the same shape ADR-0004's approval
      endpoint already has (K-21 there; the analogous ordering is required here at gate 1).
    - **409** — either `NoAvailableDuplicateName` (every generated candidate was already taken —
      the pathological case named in `app.data.scenario_duplication`) or a `WriteRefused` from the
      database (a concurrent duplicate committed the same candidate name first). Both leave the
      source and every previously existing scenario untouched.
    - **403** — the permission dependency, before the database. `SCENARIO_COPY`, not `PROJECT_COPY`
      (gate 1 decision 2): duplicating one scenario and copying a whole project are different
      actions, plausibly different people's rights.
    """
    try:
        copy = duplicate_scenario(session, caller, project_id, scenario_id)
    except NoAvailableDuplicateName as refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    except ScenarioDuplicationRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Refused by the database. {refusal}"
        ) from None
    if copy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_NOT_FOUND_DETAIL
        )
    # A fresh `draft` copy is never itself `approved`, so this only ever reads the organisation's
    # live defaults (`organization_level_for`) — the same helper `create_project` and `copy_project`
    # use for the same reason.
    organization_level = organization_level_for(session, [copy])
    return shape_duplicated_scenario(copy, copy.project, organization_level)
