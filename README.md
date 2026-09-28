<p align="center">
  <img src="assets/stafffing-calculator-logo.svg" alt="StafffingCalculator — IT project staffing and profitability planner" width="720">
</p>

# StafffingCalculator

An IT project staffing and profitability planner. A web application for Project Managers at an IT
outsourcing company: it helps prepare a staffing plan, estimate a project's costs and revenue,
assess profitability, and compare the financial impact of alternative delivery configurations.

The project is also **a working example of the [Ninefold](https://github.com/TanerCRB/NineFold)
framework** — an SDLC process for software development driven by AI agents (Spec-Driven
Development + Human-in-the-Loop). All code in this repository was produced through that process:
every task went from an Issue through acceptance criteria, an architecture impact map,
implementation, mutation testing and independent reviews, with three gates an agent never crosses.

## What the application does

Requirements: [`Wymagania/Requirements_EN.md`](Wymagania/Requirements_EN.md). Main areas:

- **Projects and scenarios** — multiple independent calculation scenarios per project, drafts,
  copying, archiving, detection of missing inputs (an incomplete scenario is never presented as
  ready for approval).
- **Configurable assumptions** — an organization → project → scenario override chain that shows
  the source of every value; working calendars, absence budgets, delivery phases.
- **Roles and rates catalogue** — role, seniority, location, engagement type, cost and selling
  rates with effective date ranges, subcontractor rates, a register of named people.
- **Staffing plan** — staffing positions with monthly allocation (hours/FTE), anonymous roles or
  named people.
- **Commercial models** — Time & Material, Fixed Price, Outcome-based, Story Points.
- **Costs** — personnel cost (base rate, overheads, fixed amount), paid absence cost, additional
  costs.
- **Results** — profit, margin, markup; scenario comparison; what-if analysis (e.g. a salary
  increase) without saving.
- **Access control** — project visibility per assignment, a separate gate for personnel-cost
  fields, change history on scenario approval.

Progress: [`docs/PLAN.md`](docs/PLAN.md) (task register) and
[`docs/architecture/capabilities.md`](docs/architecture/capabilities.md) — a register of what is
**proven by a test**, kept separate from what has merely been decided. Architecture decisions:
[`docs/architecture/decisions/`](docs/architecture/decisions/).

> **Status:** under development, no production environment. Caller identity is still a
> request-header placeholder, not authentication (ADR-0005) — the application refuses to start
> outside `development`/`test` without an explicit opt-in.

## Tech stack

| Layer | Technologies |
|---|---|
| Backend (`backend/`) | Python 3.12+, FastAPI, Pydantic, SQLAlchemy 2.x, Alembic, PostgreSQL |
| Frontend (`frontend/`) | React 18, TypeScript, Vite, pnpm, Vitest |
| Tests | pytest + testcontainers (a real PostgreSQL in Docker), Vitest + Testing Library |

Money is always `Decimal`, rounded only through `backend/app/core/money.py` /
`frontend/src/lib/money.ts` (ADR-0002). Decimals cross the API boundary as strings, never as JSON
floats.

## Getting started

### Prerequisites

- Python 3.12+
- Node.js 20+ and pnpm 9 (`corepack enable` activates the version pinned in
  `frontend/package.json`)
- PostgreSQL 13+ with the `btree_gist` extension (easiest via Docker, see below)
- Docker — also needed for the backend tests (testcontainers starts a throwaway PostgreSQL)

### 1. Database

```bash
docker run -d --name staffing-db \
  -e POSTGRES_USER=staffing -e POSTGRES_PASSWORD=staffing -e POSTGRES_DB=staffing \
  -p 5432:5432 postgres:16
```

These credentials match the default `APP_DATABASE_URL` in `backend/.env.example`. With your own
PostgreSQL server, the role running migrations must be able to create the `btree_gist` extension
(details: [`backend/README.md`](backend/README.md#wymagane-uprawnienie-bazy-create-extension-btree_gist)).

### 2. Backend — API at `http://localhost:8000`

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate     # Windows (Git Bash); PowerShell: .venv\Scripts\Activate.ps1
                                  # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head              # database schema
uvicorn app.main:app --reload
```

Check: `http://localhost:8000/health`; API docs: `http://localhost:8000/docs`.

`.env.example` sets `APP_ENVIRONMENT=development` and `APP_ALLOW_PLACEHOLDER_IDENTITY=true` —
without both, the application deliberately **refuses to start**, because caller identity is for
now the `X-Caller-User-Id` header rather than authentication (ADR-0005).

### 3. Frontend — `http://localhost:5173`

```bash
cd frontend
pnpm install
cp .env.example .env
```

In `frontend/.env`, set any development user id, e.g. `VITE_CALLER_USER_ID=dev-anna` — the
frontend sends it in the `X-Caller-User-Id` header. `VITE_API_BASE_URL` defaults to
`http://localhost:8000`. Then:

```bash
pnpm dev
```

### 4. Sample data (optional)

An empty database means an empty project list — a project is visible only to a user assigned to
it. A development script fills the database with synthetic data (marked `(seed)`) and grants
access to the same id the frontend uses:

```bash
cd backend
python -m scripts.seed_dev_data --caller-user-id dev-anna
```

Without `--caller-user-id`, the script reads `VITE_CALLER_USER_ID` from the environment or from
`frontend/.env`. Running it again duplicates nothing.

### Tests and lint

```bash
( cd backend && pytest && ruff check . )      # requires Docker running
( cd frontend && pnpm test && pnpm lint && pnpm build )
```

The pre-push hook runs the same tests — activate the backend virtualenv before `git push`.

Details: [`backend/README.md`](backend/README.md), [`frontend/README.md`](frontend/README.md).

## Process: Ninefold

[Ninefold](https://github.com/TanerCRB/NineFold) is a portable starter kit for an AI-agent SDLC
process. The name comes from its nine actors: eight specialized agent roles plus the human. The
core principle: **a role that produces something never evaluates it**, and every handoff between
roles puts a fresh set of eyes on the result.

This repository is a monorepo instantiation of Ninefold. Carried over from the framework and
adapted:

- **Roles** ([`agents/`](agents/)) — nine definitions (backend and frontend as separate
  developers):
  - producing: `product-owner`, `analyst`, `developer-backend`, `developer-frontend`, `qa`,
  - evaluating: `invariant-guardian`, `architect`, `reviewer`, `security-auditor`.

  Synced to `.claude/agents/` with `node tools/sync-agents.mjs`.
- **Team contract** ([`TEAM-CONTRACT.md`](TEAM-CONTRACT.md)) — who does what, gates, hard stops;
  takes precedence over the role files.
- **State machine and labels** ([`process/sdlc-flow.md`](process/sdlc-flow.md),
  [`process/labels.json`](process/labels.json)), Issue/PR templates, a pre-push hook.
- **Commands** — `/task #N` drives one task from Issue to PR, `/task-status #N` is a read-only
  report (`.claude/commands/`).
- **Calibration** ([`calibration/`](calibration/)) — how to check that an evaluating role
  actually evaluates before you start trusting it.

### Three human gates

1. **Scope and architecture** — before any code is written.
2. **Merge into `main`** — a code PR (`Refs #N`) moves the Issue to `state:evidence`; it does not
   close it.
3. **Merge of the documentation PR** (`Closes #N`) — raises the status in `docs/PLAN.md` and in
   the capabilities register.

The `waiting-on-human` label marks all three — `is:open label:waiting-on-human` shows everything
waiting on a human.

### Proof, not claims

An acceptance criterion has an observable carrier, an opposite, and a named mutation that must
kill it. QA removes the mechanism and records whether the test actually failed. An entry in
[`capabilities.md`](docs/architecture/capabilities.md) points to a specific test and the kind of
evidence (`mutation-checked test`, `test, no mutation`, `no evidence`).

The reasoning behind the whole approach: [`FrameworkDoc.md`](FrameworkDoc.md) and
[`process/`](process/); the current version lives in
[TanerCRB/NineFold](https://github.com/TanerCRB/NineFold).

## Conventions

- English: commit messages, PR/Issue descriptions and comments, code comments, documentation,
  identifiers, error and log messages, the API surface.
- A task that writes code works in its own `git worktree`, not in the main checkout.
- An existing test that starts failing because of a change is never weakened or removed.

Full rules: [`TEAM-CONTRACT.md`](TEAM-CONTRACT.md), [`CLAUDE.md`](CLAUDE.md).

## License

Copyright © 2026 Mariusz Miziołek.

StafffingCalculator is licensed under the
**[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)**
— full text in [`LICENSE`](LICENSE).

- **Noncommercial use is free** — learning, research, personal and hobby projects, nonprofit
  organizations, educational and public institutions, under the terms of the license.
- **Commercial use requires the author's prior written permission** (a separate commercial
  license). This covers, among others, use within a company, in services delivered to clients, and
  in products sold or offered for a fee. For a commercial license, contact the author via
  [GitHub](https://github.com/TanerCRB).

When redistributing, keep the license text and the `Required Notice:` lines from `LICENSE`.

**Files originating from Ninefold.** The [Ninefold](https://github.com/TanerCRB/NineFold)
framework is licensed under the Apache License 2.0. The files based on it (`FrameworkDoc.md`,
`agents/`, `process/`, `tools/`, `calibration/`) originate from that project; to use the process
itself — commercially as well — use the original Ninefold repository under its license.
