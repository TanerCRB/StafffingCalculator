# Architecture-sensitive paths

Read by the task command's fast lane (`.claude/commands/task.md`, "Fast lane: when the Architect
is skipped"): a task touching any path below always goes through the Architect. A path missing
from this list is a path the fast lane will wave through — when in doubt, add it. Keep this file
current when a new sensitive area is created; the Architect maintains it, not just consults it.

| Path (glob) | Governing decisions | Why it's sensitive |
|---|---|---|
| `backend/migrations/**` | ADR-0001, ADR-0008 | Schema change; expand/deploy/contract, effective-date overlap rules |
| `backend/app/core/money.py`, `frontend/src/lib/money.ts` | ADR-0002, ADR-0006 | The only place `Decimal`/rounding and currency conversion are allowed to happen |
| `backend/app/models/**` | ADR-0004, ADR-0007, ADR-0009 | Calculation versioning, concurrent-edit rules, write-from-UI contract |
| `backend/app/api/**`, `frontend/src/api/contracts/**` | ADR-0005, ADR-0009 | Access model / permission pipeline, API surface contract |
| `frontend/src/api/client.ts` | ADR-0009, ADR-0010 | The single network entry point for every read and write — the write-from-UI contract (timeout budget, per-endpoint no-refetch narrowing, refusal shape) and the shape-check-before-render boundary live here, not in a screen component; a task adding a write function here extends a decision even when it adds no new contract type (impact map SC-6-03, 2026-09-24) |
| `backend/app/domain/**` | ADR-0002, ADR-0003, ADR-0004, ADR-0005, ADR-0013, ADR-0014 | Revenue/personnel-cost/additional-cost calculation rules; code combining amounts from more than one of these (e.g. profit/margin/markup, F-10) crosses money handling (ADR-0002), the revenue model (ADR-0003), snapshot-vs-live reading (ADR-0004) and the personnel-cost gate (ADR-0005) at once — row widened 2026-09-24, impact map SC-7-01 |
| any new/changed public API endpoint, response field or event | ADR-0005, ADR-0009 | Public contract surface |
| `frontend/src/features/projects/**` (screens/sections rendering `RESULTS_READ` or personnel-cost-gated data — revenue, personnel cost and additional cost shown together) | ADR-0002, ADR-0005, ADR-0010 | Frontend render of the personnel-cost gate's `null` next to a source's own named non-computable state; a wrong precedence, a shared sentinel across sources, or a currency inferred from outside the payload misstates a gated figure without touching `frontend/src/api/contracts/**` or `frontend/src/lib/money.ts` — row added 2026-09-24, impact map SC-7-02 |
| any new or upgraded dependency (package manifest, lock file) | — | Supply-chain surface, see `security-auditor.md` |
