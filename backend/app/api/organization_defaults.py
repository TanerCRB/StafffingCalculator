"""Read and update the deployment's organization-default assumptions (ADR-0012, ADR-0022)."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission, require_permissions
from app.api.schemas.organization_defaults import (
    OrganizationDefaultsPatch,
    OrganizationDefaultsRead,
)
from app.core.identity import CallerIdentity, Permission
from app.data.organization_defaults import (
    OrganizationDefaultsConcurrentEditConflict,
    update_organization_defaults,
)
from app.db.session import get_session
from app.models.organization_defaults import OrganizationDefaults

router = APIRouter(prefix="/organization-defaults", tags=["organization defaults"])


def _response(row: OrganizationDefaults | None) -> OrganizationDefaultsRead:
    if row is None:
        return OrganizationDefaultsRead(
            target_margin_percent=None,
            overload_threshold_percent=None,
            updated_at=None,
        )
    return OrganizationDefaultsRead(
        target_margin_percent=row.target_margin_percent,
        overload_threshold_percent=row.overload_threshold_percent,
        updated_at=row.updated_at,
    )


@router.get("", response_model=OrganizationDefaultsRead)
def read_organization_defaults(
    caller: Annotated[
        CallerIdentity, Depends(require_permission(Permission.ORGANIZATION_DEFAULTS_READ))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> OrganizationDefaultsRead:
    """Return the singleton; organization defaults have no organization ID column by design."""
    row = session.execute(sa.select(OrganizationDefaults)).scalars().one_or_none()
    return _response(row)


@router.patch(
    "",
    response_model=OrganizationDefaultsRead,
    responses={
        409: {
            "description": (
                "The absent/present state or updated_at marker changed since it was read."
            )
        }
    },
)
def edit_organization_defaults(
    payload: OrganizationDefaultsPatch,
    caller: Annotated[
        CallerIdentity,
        Depends(
            require_permissions(
                Permission.ORGANIZATION_DEFAULTS_READ,
                Permission.ORGANIZATION_DEFAULTS_WRITE,
            )
        ),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> OrganizationDefaultsRead:
    """Partially update the singleton under separate read and write grants.

    The endpoint returns the server-authoritative saved state as required by ADR-0009. This does
    Both permissions are required because the response contains saved values; WRITE alone still
    cannot call the standalone GET endpoint.
    """
    try:
        row = update_organization_defaults(
            session,
            expected_updated_at=payload.updated_at,
            changes=payload.changes(),
        )
    except OrganizationDefaultsConcurrentEditConflict as conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(conflict),
        ) from None
    return _response(row)
