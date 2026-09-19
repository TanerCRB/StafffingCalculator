from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.catalog import router as catalog_router
from app.api.deps import assert_identity_mechanism_allowed
from app.api.health import router as health_router
from app.api.projects import router as projects_router
from app.api.staffing import router as staffing_router
from app.core.config import settings

# Fail closed before anything is served: running the placeholder caller identity (ADR-0005,
# addendum 2026-09-18) has to be opted into explicitly, and only in development/test. Raising
# here stops `uvicorn app.main:app` from starting at all — a warning in a log would not. A
# process started with no configuration whatsoever takes this path too; that is the point.
assert_identity_mechanism_allowed(
    allow_placeholder_identity=settings.allow_placeholder_identity,
    environment=settings.environment,
)

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=[settings.caller_id_header, "Content-Type"],
)
app.include_router(health_router)
app.include_router(projects_router)
app.include_router(catalog_router)
# Nested under `/projects/{project_id}/scenarios/{scenario_id}` but a router of its own: the
# staffing endpoints declare `STAFFING_READ`/`STAFFING_WRITE`, never the project permissions, and a
# shared router would make the two sets look interchangeable (ADR-0005, addendum 2026-09-19).
app.include_router(staffing_router)
