# StafffingCalculator

IT project staffing/profitability planner. Monorepo: `backend/` (Python 3.12, FastAPI, Pydantic —
no DB yet, see `docs/PLAN.md` task `SC-1-01`), `frontend/` (React + TypeScript, Vite, pnpm).
Requirements: [`Wymagania/Requirements_EN.md`](Wymagania/Requirements_EN.md). Money is always
`Decimal`, rounded only through `backend/app/core/money.py` / `frontend/src/lib/money.ts` — never
ad hoc.

This repo is also the source of its own process: a monorepo instantiation of **Ninefold**, an
SDLC framework for AI-agent-driven development (Spec-Driven Development + Human-in-the-Loop).
Read in this order:

1. **[`TEAM-CONTRACT.md`](TEAM-CONTRACT.md)** — who does what, gates, hard stops. Governs over
   role files on discrepancy.
2. **[`process/sdlc-flow.md`](process/sdlc-flow.md)** — state machine, labels.
3. **[`docs/PLAN.md`](docs/PLAN.md)** — task register. **[`docs/architecture/capabilities.md`](docs/architecture/capabilities.md)**
   — what's actually proven to work, separate from what's decided. `docs/architecture/decisions/`
   — adopted architecture decisions.
4. **[`FrameworkDoc.md`](FrameworkDoc.md)** and **[`process/`](process/)** — the general, portable
   pattern this project's config was adapted from. Reference for *why*, not day-to-day use.

## Roles and gates

Nine roles in `agents/` (synced to `.claude/agents/` via `node tools/sync-agents.mjs` — that
directory is git-ignored, run the sync after editing a role). Four evaluate
(`invariant-guardian`, `architect`, `reviewer`, `security-auditor`), five produce (`product-owner`,
`analyst`, `developer-backend`, `developer-frontend`, `qa`). A role never evaluates its own
output. Full mechanics: `.claude/commands/task.md` (drives one task end to end) and
`.claude/commands/task-status.md` (read-only status report).

Three human gates, never crossed by an agent: **1** scope/architecture before code,
**2** merge into `main`, **3** merge of the separate documentation PR (`Closes #N`) that raises
status in `docs/PLAN.md` / capabilities registry — merging the code PR (`Refs #N`) only moves the
Issue to `state:evidence`, it doesn't close it. The `waiting-on-human` label marks all three —
`is:open label:waiting-on-human` finds everything stuck on you.

## Hard rules for every session in this repo

- **No `git commit`, `git push`, `git merge`, or `gh pr merge` without an explicit request** —
  ask first, every time, regardless of how obvious the next step looks.
- **No `gh api` call that mutates repo settings, labels, or branches without an explicit
  request.**
- One writing session per working tree; a task that writes code uses its own `git worktree`, not
  the main checkout.
- An existing test that starts failing because of a change is never weakened or removed — stop
  and report the conflict.
- **Write every artifact in English** (since 2026-09-28): commit messages, PR/Issue descriptions
  and comments, code comments, docs — as well as identifiers, error/log messages, the API surface.
  Existing Polish text is not translated retroactively; it becomes English when it is next
  edited. See `TEAM-CONTRACT.md` §7.

## Commands

```bash
( cd backend && pytest && ruff check . )
( cd frontend && pnpm test && pnpm lint && pnpm build )
node tools/sync-agents.mjs [--check]
node tools/sync-github.mjs [--labels]
```
