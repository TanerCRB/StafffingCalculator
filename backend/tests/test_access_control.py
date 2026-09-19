"""Denial tests for the project-read permission, and the startup guard on the identity
placeholder.

ADR-0005 ("Konsekwencje"): a refusal test is mandatory for every new permission. The placeholder
identity (addendum 2026-09-18) is allowed only in development/test — the guard proving that is
exercised here both directly and through a real interpreter start.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    PlaceholderIdentityNotAllowedError,
    assert_identity_mechanism_allowed,
    get_caller_identity,
)
from app.core.config import Settings
from app.core.identity import CallerIdentity, Permission
from app.main import app
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    as_caller,
    count_projects,
    make_project,
    project_payload,
)


def test_project_list_denies_caller_without_identity(
    client: TestClient, db_session: Session
) -> None:
    make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    response = client.get("/projects")

    assert response.status_code == 401
    assert "Aurora migration" not in response.text


def test_project_list_denies_caller_without_project_read_permission(
    client: TestClient, db_session: Session
) -> None:
    make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    app.dependency_overrides[get_caller_identity] = lambda: CallerIdentity(
        user_id=IN_SCOPE_USER, permissions=frozenset()
    )
    try:
        response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))
    finally:
        app.dependency_overrides.pop(get_caller_identity, None)

    assert response.status_code == 403
    assert "Aurora migration" not in response.text


def test_project_read_by_id_denies_caller_without_identity(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    response = client.get(f"/projects/{project.id}")

    assert response.status_code == 401
    assert "Aurora migration" not in response.text


def test_project_create_denies_caller_without_identity(
    client: TestClient, db_session: Session
) -> None:
    """No identity, no write — and the refusal leaves the table exactly as it was."""
    response = client.post("/projects", json=project_payload())

    assert response.status_code == 401
    assert count_projects(db_session) == 0


def test_project_create_denies_caller_holding_only_project_read(
    client: TestClient, db_session: Session
) -> None:
    """The refusal test ADR-0005 makes mandatory for every new permission.

    `PROJECT_CREATE` has to be its own permission for this test to be possible at all: a caller
    with read access and nothing else is exactly the read-only viewer of F-13, and a viewer that
    can create projects is the failure this separation exists to prevent.
    """
    app.dependency_overrides[get_caller_identity] = lambda: CallerIdentity(
        user_id=IN_SCOPE_USER, permissions=frozenset({Permission.PROJECT_READ})
    )
    try:
        response = client.post(
            "/projects", json=project_payload(), headers=as_caller(IN_SCOPE_USER)
        )
    finally:
        app.dependency_overrides.pop(get_caller_identity, None)

    assert response.status_code == 403
    assert count_projects(db_session) == 0


def test_personnel_cost_permission_is_not_granted_by_the_placeholder_identity() -> None:
    """The placeholder grants exactly the five project actions plus catalogue read/write — no more.

    Asserted as set equality rather than membership: ADR-0005's addendum requires every widening
    of this set to be a deliberate, dated entry in the decision, and a membership assertion
    cannot tell a widening apart from the status quo — it stays green while the set silently
    grows. `PERSONNEL_COSTS_READ` is still absent, so the deny path of F-13/AC-06 remains real;
    so is every permission that has not been added yet, and that is now part of the claim.

    A failure here is not a broken test: it means the placeholder identity grew a permission,
    and the question to answer is whether ADR-0005's addendum grew with it.

    It grew three times on 2026-09-18: by `PROJECT_EDIT` (SC-1-02), `PROJECT_COPY` (SC-1-03) and
    `PROJECT_ARCHIVE` (SC-1-04). ADR-0005's addendum "uprawnienia akcji zapisu na Projekcie i
    dostęp do kopii" names all three widenings, so the expected set below was updated with them
    and not around them. The assertion stays a set equality — the canary is re-armed around the
    new set, not softened into a membership check.

    It grew twice more on 2026-09-19, by `CATALOG_READ` and `CATALOG_WRITE` (SC-2-01): ADR-0005's
    addendum "pierwszy zbiór danych bez zasięgu projektu" (point 6) names those two widenings and
    only those two, and says in the same breath that `PERSONNEL_COSTS_READ` stays out. So the
    catalogue's cost-rate deny path is the real one for every caller the running system has, and its
    positive branch is reachable from a test only (`dependency_overrides`) — the same shape SC-1-08
    accepted, and the reason the second assertion below is a separate line rather than an
    afterthought.
    """
    from app.api.deps import PLACEHOLDER_PERMISSIONS

    assert PLACEHOLDER_PERMISSIONS == frozenset(
        {
            Permission.PROJECT_READ,
            Permission.PROJECT_CREATE,
            Permission.PROJECT_EDIT,
            Permission.PROJECT_COPY,
            Permission.PROJECT_ARCHIVE,
            Permission.CATALOG_READ,
            Permission.CATALOG_WRITE,
        }
    )
    assert Permission.PERSONNEL_COSTS_READ not in PLACEHOLDER_PERMISSIONS


@pytest.mark.parametrize("environment", ["development", "test", "production", ""])
def test_placeholder_identity_refuses_to_run_without_an_explicit_opt_in(
    environment: str,
) -> None:
    """The case that matters most: nobody configured anything.

    `allow_placeholder_identity` defaults to False, so an absent configuration reaches the guard
    as False and the guard refuses — including for `environment="development"`, the value an
    unset `APP_ENVIRONMENT` silently produces. Missing configuration must mean the most
    restrictive state, not the most permissive one.
    """
    with pytest.raises(PlaceholderIdentityNotAllowedError):
        assert_identity_mechanism_allowed(
            allow_placeholder_identity=False, environment=environment
        )


def test_allow_placeholder_identity_defaults_to_denied_when_nothing_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deny-by-default lives in the settings field itself, not in the caller's argument."""
    monkeypatch.delenv("APP_ALLOW_PLACEHOLDER_IDENTITY", raising=False)

    assert Settings(_env_file=None).allow_placeholder_identity is False


@pytest.mark.parametrize("environment", ["production", "staging", "prod", "", "Development"])
def test_placeholder_identity_refuses_to_run_outside_development_and_test(
    environment: str,
) -> None:
    """Second condition, independent of the first: even an explicit opt-in cannot carry the
    placeholder into a real environment. Matching is exact — `Development` is not
    `development`."""
    with pytest.raises(PlaceholderIdentityNotAllowedError):
        assert_identity_mechanism_allowed(
            allow_placeholder_identity=True, environment=environment
        )


@pytest.mark.parametrize("environment", ["development", "test"])
def test_placeholder_identity_runs_only_with_opt_in_and_a_dev_or_test_environment(
    environment: str,
) -> None:
    assert (
        assert_identity_mechanism_allowed(
            allow_placeholder_identity=True, environment=environment
        )
        is None
    )


def test_application_import_fails_when_placeholder_identity_meets_a_real_environment() -> None:
    """The guard is wired into startup, not merely available: importing `app.main` with
    `APP_ENVIRONMENT=production` aborts the process, so `uvicorn app.main:app` cannot come up."""
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_ROOT,
        # An environment variable wins over any `.env` entry (pydantic-settings precedence).
        env=os.environ | {"APP_ENVIRONMENT": "production"},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, "the application must refuse to start"
    assert "PlaceholderIdentityNotAllowedError" in result.stderr


def test_application_import_fails_when_nothing_is_configured_at_all(
    tmp_path: Path,
) -> None:
    """The deployment accident this guard exists for: the process starts somewhere with no
    `APP_*` variables and no `.env` to read — wrong working directory, missing config file,
    forgotten manifest entry. It must refuse, not fall back to `development` and trust a header.

    Run from an empty directory so no `.env` can be picked up, with every `APP_*` variable
    stripped from the environment.
    """
    stripped = {key: value for key, value in os.environ.items() if not key.startswith("APP_")}

    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=tmp_path,
        env=stripped,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, "no configuration must mean refusal, not permission"
    assert "PlaceholderIdentityNotAllowedError" in result.stderr
    assert "APP_ALLOW_PLACEHOLDER_IDENTITY" in result.stderr
