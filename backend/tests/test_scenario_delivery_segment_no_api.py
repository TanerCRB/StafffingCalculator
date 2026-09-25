"""SC-1-11, K-08 — no HTTP endpoint exposes a scenario delivery segment (ADR-0016, point 8, Q2 = A).

Two independent structural proofs, so neither can pass by accident:

1. **No route of the running application answers about a segment** — read from the application's
   own OpenAPI schema, which is built from every router actually mounted on `app` (`app.main`), not
   from a guess at what "should" exist.
2. **No module under `app/api/` even imports the table's model or its data-layer module** — read
   from each file's own syntax tree (the pattern of
   `test_additional_cost.py::test_k_10_the_additional_cost_and_the_revenue_and_personnel_cost_modules_never_meet`),
   so a future endpoint built without ever importing the *write* function (reading the table with a
   hand-rolled query, say) still fails this half.
"""

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import BACKEND_ROOT

_FORBIDDEN_MODULES = {
    "app.data.scenario_delivery_segment",
    "app.models.scenario_delivery_segment",
}


def _imports_of(relative_path: str) -> set[str]:
    """Every module a source file imports, read from its syntax tree (the helper of
    `test_personnel_cost.py`/`test_additional_cost.py`)."""
    tree = ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_k_08_no_route_of_the_running_application_mentions_a_segment_or_a_workstream(
    client: TestClient,
) -> None:
    """Read from the application's own OpenAPI document — every path FastAPI actually serves, not
    a list maintained by hand that could drift from what is mounted."""
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert paths, "the OpenAPI document is empty — this test would prove nothing"
    for path in paths:
        lowered = path.lower()
        assert "segment" not in lowered, path
        assert "workstream" not in lowered, path
        assert "delivery-phase" not in lowered, path


def test_k_08_no_module_under_app_api_imports_the_segment_model_or_its_data_layer() -> None:
    """Every `.py` file under `app/api/` (including `app/api/__init__.py` and `deps.py`), read from
    its own syntax tree — an import added anywhere in that package, however indirect the resulting
    endpoint, fails here on the day it is added."""
    api_directory = Path(BACKEND_ROOT, "app", "api")
    modules = sorted(api_directory.rglob("*.py"))
    assert modules, "no files found under app/api — this test would prove nothing"

    for module_path in modules:
        relative = str(module_path.relative_to(BACKEND_ROOT)).replace("\\", "/")
        imports = _imports_of(relative)
        crossing = imports & _FORBIDDEN_MODULES
        assert not crossing, f"{relative} imports {sorted(crossing)}"
