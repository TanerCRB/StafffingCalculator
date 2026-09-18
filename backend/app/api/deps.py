"""Shared request-scoped dependencies: who is calling, and may they do this.

What this module actually guarantees: an endpoint that declares
`Depends(require_permission(...))` is refused unless the caller holds that permission, and the
caller is rebuilt from the request on every call. What it does **not** guarantee: that an
endpoint declares anything at all. `get_session` can be injected without any permission
dependency, so "no declaration" means "no check", not "no database" — the only thing standing
between a new endpoint and unfiltered data is review plus the fact that project rows can only be
read through `app.data.project_reads` (ADR-0001 addendum; Invariant Guardian rules 4 and 6).
Machine-enforcing the declaration itself needs a router-level guard that does not exist yet.
"""

import threading
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings
from app.core.identity import CallerIdentity, Permission

# --- TEMPORARY, DATED DEVIATION (ADR-0005, addendum 2026-09-18) --------------------------------
# No authentication exists in this repository: no user table, no session, no token. Until the
# separate authentication ADR lands, the caller is whoever the request header says they are, and
# the permission set is fixed. This proves the `project_access` scope filter and nothing about
# authentication. `PERSONNEL_COSTS_READ` is deliberately absent, so the deny path is real.
#
# `PROJECT_CREATE` is in the set because SC-1-01 needs the create endpoint to be reachable at all
# while every caller is this one fixed placeholder; it is *not* a statement that everyone may
# create projects. ADR-0005 assigns that to the `author`/`admin` roles, and the role dimension
# arrives with the authentication ADR — until then the permission is declared and enforced per
# endpoint (see `require_permission`), while who holds it is not yet a real decision.
#
# `PROJECT_EDIT` joins the set for the same reason and under the same reservation (ADR-0005,
# addendum 2026-09-18, "uprawnienia akcji zapisu na Projekcie"): while every caller is this one
# fixed placeholder, `PATCH /projects/{id}` would otherwise be unreachable. It is *not* a decision
# that everyone may edit projects — the role dimension arrives with the authentication ADR.
PLACEHOLDER_PERMISSIONS: frozenset[Permission] = frozenset(
    {Permission.PROJECT_READ, Permission.PROJECT_CREATE, Permission.PROJECT_EDIT}
)

USES_PLACEHOLDER_IDENTITY: bool = True
"""True while caller identity comes from a request header instead of authentication. The task
that lands the authentication ADR flips this to False — and the guard below stops firing."""

PLACEHOLDER_IDENTITY_ENVIRONMENTS: frozenset[str] = frozenset({"development", "test"})
"""Environments in which the placeholder may run — checked *in addition to* the opt-in flag
below, never instead of it. `environment` has a permissive default (`"development"`), so on its
own it turns a missing configuration into permission."""

_identity_guard_lock = threading.Lock()


class PlaceholderIdentityNotAllowedError(RuntimeError):
    """Raised at startup when the placeholder identity is not explicitly allowed to run."""


def assert_identity_mechanism_allowed(
    *, allow_placeholder_identity: bool, environment: str
) -> None:
    """Refuse to start unless running the header placeholder has been explicitly opted into.

    Fail closed and loudly: an unauthenticated header that says "I am the admin" is a total
    authentication bypass, so this raises during application import rather than logging a
    warning somebody may never read.

    Two conditions, and the first one carries the weight:

    1. `allow_placeholder_identity` must be **explicitly** true (`APP_ALLOW_PLACEHOLDER_IDENTITY`,
       default `False`). The realistic deployment accident is not "someone set the wrong value",
       it is "nobody set anything" — no `.env`, wrong working directory, a variable missing from
       the manifest. With a deny-by-default flag that accident stops the process; with only the
       `environment` field it would sail through, because unset `environment` *is*
       `"development"`.
    2. `environment` must still be one of `PLACEHOLDER_IDENTITY_ENVIRONMENTS` — so an opt-in
       copied into a production manifest by accident is caught as well.

    ADR-0005's addendum makes this deviation conditional on never leaving development/test;
    this function is that condition, enforced mechanically.
    """
    with _identity_guard_lock:
        if not USES_PLACEHOLDER_IDENTITY:
            return
        if not allow_placeholder_identity:
            raise PlaceholderIdentityNotAllowedError(
                "Caller identity is still the "
                f"{settings.caller_id_header!r} header placeholder (ADR-0005, addendum "
                "2026-09-18), and APP_ALLOW_PLACEHOLDER_IDENTITY is not set to true. "
                "Running it must be opted into explicitly — an absent or unreadable "
                "configuration means refusal, not permission. Set "
                "APP_ALLOW_PLACEHOLDER_IDENTITY=true for a development/test run, or land the "
                "authentication ADR before starting this service anywhere else."
            )
        if environment not in PLACEHOLDER_IDENTITY_ENVIRONMENTS:
            raise PlaceholderIdentityNotAllowedError(
                f"APP_ENVIRONMENT={environment!r} but caller identity is still the "
                f"{settings.caller_id_header!r} header placeholder (ADR-0005, addendum "
                "2026-09-18). The placeholder may only run in "
                f"{sorted(PLACEHOLDER_IDENTITY_ENVIRONMENTS)}, whatever "
                "APP_ALLOW_PLACEHOLDER_IDENTITY says."
            )


def get_caller_identity(request: Request) -> CallerIdentity:
    """Resolve the caller from this request, on every request.

    Never cached at module or session scope, never taken from a body field: a cached or
    client-supplied identity is how a scope filter silently stops being one.
    """
    user_id = request.headers.get(settings.caller_id_header, "").strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing caller identity header {settings.caller_id_header!r}.",
        )
    return CallerIdentity(user_id=user_id, permissions=PLACEHOLDER_PERMISSIONS)


def require_permission(permission: Permission) -> Callable[..., CallerIdentity]:
    """Build the dependency an endpoint uses to declare the permission it needs.

    Deny by default: the check is an explicit membership test, so a permission nobody grants
    yet results in a refusal, not in a pass-through.
    """

    def dependency(caller: CallerIdentity = Depends(get_caller_identity)) -> CallerIdentity:  # noqa: B008
        if not caller.has(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Caller lacks permission {permission.value!r}.",
            )
        return caller

    return dependency
