from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.additional_cost import router as additional_cost_router
from app.api.catalog import router as catalog_router
from app.api.commercial_terms import router as commercial_terms_router
from app.api.deps import assert_identity_mechanism_allowed
from app.api.health import router as health_router
from app.api.personnel_cost import router as personnel_cost_router
from app.api.projects import router as projects_router
from app.api.scenario_results import compare_router as scenario_results_compare_router
from app.api.scenario_results import router as scenario_results_router
from app.api.scenarios import router as scenarios_router
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
    # `DELETE` joins the list for SC-3-02: removing a planned absence is the first delete this API
    # has. Without it a browser's preflight refuses the request before it reaches the permission
    # dependency, and the failure looks like a CORS misconfiguration rather than a missing method.
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=[settings.caller_id_header, "Content-Type"],
)
app.include_router(health_router)
app.include_router(projects_router)
app.include_router(catalog_router)
# Nested under `/projects/{project_id}/scenarios/{scenario_id}` but a router of its own: the
# staffing endpoints declare `STAFFING_READ`/`STAFFING_WRITE`, never the project permissions, and a
# shared router would make the two sets look interchangeable (ADR-0005, addendum 2026-09-19).
app.include_router(staffing_router)
# Same nesting, third router: approving a scenario is not a staffing write (it freezes the whole
# calculation, including tables SC-3-02 does not create), and it declares a different permission —
# which a shared router would make look interchangeable with `STAFFING_WRITE`.
app.include_router(scenarios_router)
# Same nesting, fourth router (SC-4-01): the commercial rule declares `COMMERCIAL_READ`/`WRITE`
# (ADR-0005, addendum 2026-09-23 SC-4-01), which a shared router would make look interchangeable
# with the staffing or project permissions.
app.include_router(commercial_terms_router)
# Same nesting, fifth router (SC-5-01): the base personnel cost declares `STAFFING_READ` on the
# endpoint and gates its figure on `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs` in response
# shaping (ADR-0005, aneks 2026-09-23 SC-5-01). Not a verb on the commercial router — cost and
# revenue are independent calculations (F-06) under different permissions.
app.include_router(personnel_cost_router)
# Same nesting, sixth router (SC-5-05): a scenario's additional costs, under `STAFFING_READ`/`WRITE`
# with no cost conjunction (ADR-0014, point 11; ADR-0005, aneks 2026-09-23 SC-5-05). Not a verb on
# the personnel-cost router: the two modules never import each other (ADR-0014, "Konsekwencje").
app.include_router(additional_cost_router)
# Same nesting, seventh router (SC-7-01): the scenario-wide profit, margin and markup declares
# `RESULTS_READ` on the endpoint and gates four of its fields on `PERSONNEL_COSTS_READ` ∧
# `can_view_personnel_costs` in response shaping (ADR-0005, aneks 2026-09-24) — a composition over
# the three routers above, never a fourth independent calculation (F-06).
app.include_router(scenario_results_router)
# Same nesting, an eighth router (SC-6-02): comparing several scenarios of the same project in one
# call — a composition over `scenario_results_router` above (same `RESULTS_READ`, same functions,
# same gates), never a new calculation. A router of its own because its path
# (`/projects/{project_id}/scenarios/compare`) does not carry `{scenario_id}/results`.
app.include_router(scenario_results_compare_router)
