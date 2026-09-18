---
description: StafffingCalculator — drives a task through the full process, from Issue to PR, with stops at the human gates
argument-hint: <Issue link or #N> [phase: analysis|code|verification|pr|closure]
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Task, TodoWrite
---

# Task: $1

You are driving **one** task through the process. Argument `$1` points at the Issue (full URL or
`#N`) in `TanerCRB/StafffingCalculator`. Optional `$2` says which phase to start from — without
it, determine the phase yourself from the Issue's state, the branch, and open PRs.

`gh` is sometimes off PATH — call it by full path if a bare `gh` fails. Every `gh` call carries
`--repo TanerCRB/StafffingCalculator` explicitly.

## Overriding rules

- **Status is raised by evidence, not conviction.** You don't check off the task and you don't
  raise its status.
- **You don't commit or push without an explicit request.** Never merge a pull request.
- **Gates 1, 2 and 3 belong to the human.** You work up to them, prepare the material, and
  **stop**.
- `.claude/` is out of reach — it holds this tool's own configuration and is git-ignored.
- A task that writes code works in its own `git worktree`, in `backend/` or `frontend/`
  respectively (see `role:backend` / `role:frontend` on the Issue). The main checkout stays on
  `main`.
- **Assignment for the duration of the work.** You start working a task → assign the Issue to
  yourself. You stop working it — a STOP gate, a collision, a merged PR, anything else — →
  unassign yourself, even when the Issue stays open and unclosed.

Keep a TODO list with the steps below — one entry per step, so it's visible where the process
currently stands.

## How you call a role

Roles live in `agents/`, synced into `.claude/agents/` via `node tools/sync-agents.mjs`. If a role
isn't registered as a subagent type in this session (e.g. the session started above this
repository), call it through the general-purpose execution mechanism and paste the role's
definition file content as the first part of the prompt, ahead of the task context (Issue, impact
map, criteria).

**Subagent model — the same as yours**, if calling through the general-purpose mechanism.
Raising the model requires the human's consent.

Roles: `product-owner`, `analyst`, `architect`, `developer-backend`, `developer-frontend`, `qa`,
`invariant-guardian`, `reviewer`, `security-auditor`.

## How you talk to the human

Every question and every decision comes with a recommendation:

```
Question: <what needs to be decided>
Options:  A — <consequence>.  B — <consequence>.
Recommendation: A, because <reason>.
What would change it: <what I don't know>
```

A recommendation **is not a decision** — you still stop and wait.

**Keep the conversation terse (this project runs caveman mode); keep artifacts in full Polish.**
Issue bodies, comments, commit messages, and pull request descriptions are written in Polish, per
`TEAM-CONTRACT.md`, section 7 — identifiers, error/log messages, and the API surface stay in
English. Drop compression anywhere it creates ambiguity (irreversible actions, security).

---

## Step 0 — Recon

```bash
gh issue view <N> --repo TanerCRB/StafffingCalculator --json number,title,body,labels,state,assignees,comments
gh pr list --repo TanerCRB/StafffingCalculator --state open --json number,title,headRefName,isDraft
git branch -a --list '*<identifier>*'
```

Determine and **say out loud**: the task's identifier and whether it's in `docs/PLAN.md`; the
`state:*` / `role:*` / `waiting-on-human` / `adr-deviation` labels; whether work is already in
progress (open PR, branch, someone else's worktree).

**Work already in progress elsewhere = stop.** A PR with a merge conflict looks abandoned, but
usually isn't. Check whether `docs/PLAN.md`'s *Done when* condition is already met by current
code before starting implementation — if so, close with evidence instead of reimplementing.

Once collision is ruled out, assign the Issue to yourself.

---

## Phase `analysis` — up to gate 1

1. **Is the Story complete?** Task identifier, *What and why*, *Done when* (observable), *Out of
   scope (explicit)*, basis in documentation. The `product-owner` role fills gaps.
2. **Plan task number** — reserve the next free `SC-<block>-<seq>` in `docs/PLAN.md` as a separate
   commit before real work, if not already reserved.
3. **`analyst` role** — acceptance criteria + *Done when* row. Every criterion has an observable
   carrier, a contrast, and a named mutation. Verdict: `READY FOR GATE 1` / `STORY NEEDS MORE WORK`.
4. **`architect` role** — impact map onto `docs/architecture/decisions/`. Outcomes: fits / needs
   an addendum / needs a new decision (deviation Issue + draft in "Draft — pending approval").
5. **GATE 1 — STOP.** Gather: criteria, impact map, open questions (options + consequences). Wait
   for the human. **Don't remove `waiting-on-human` yourself.** Unassign yourself.

---

## Phase `code` — implementation

Entry condition: impact map **and** criteria exist, `state:implementation` label present.

6. **Working directory.** `git worktree add ../StafffingCalculator-<identifier> -b <identifier>`,
   inside a location your formatter/linter actually scan (not `.claude/`).
7. **Developer role** — `developer-backend` for `backend/` work, `developer-frontend` for
   `frontend/` work (both, if the task spans both — run them as two separate reports). Order:
   - backend: migration (Alembic) before code, backward compatible, expand → deploy → contract;
   - money as `Decimal`, explicit rounding; zero-denominator metrics return `"n/a"`;
   - authorization/personnel-cost checks in the shared dependency/response-shaping layer, not
     ad-hoc per endpoint;
   - a write to an `approved` calculation is rejected at the data-access layer;
   - frontend: contracts layer for API shapes, shared money-formatting helper, permission-gated
     screens don't fetch data they can't show.
8. **Tests per criterion**, against real infrastructure where a fake proves nothing (e.g. the
   effective-date exclusion constraint needs a real PostgreSQL, not a mock).
9. **Local quality gates** (from the repository root):
   ```bash
   ( cd backend && pytest && ruff check . )    # if backend/ was touched
   ( cd frontend && pnpm test && pnpm lint && pnpm build )   # if frontend/ was touched
   ```
10. **Developer report**, mandatory "What this change does not prove" section, proposed gate-3
    entries. No commit, no check-off.

---

## Phase `verification` — before gate 2

11. **`qa` role** — contrast test, mutation with a result recorded. Mutation label derived from
    the task identifier. Restore working-tree state from a patch/diff, never a hard checkout.
12. **`invariant-guardian` role** — audits the diff against `agents/invariant-guardian.md`'s
    checklist. `PASS` / `STOP`.
13. **`reviewer` role** — design flaws outside the checklist.
14. **`security-auditor` role** — conditionally, per its "When to run you" trigger list
    (auth, personal data, dependencies, CI/CD, migrations, config/secrets).

Call roles 12–14 **in parallel** — they read, they write nothing.

---

## Phase `pr`

15. **Sync with `main` before the PR.**
    ```bash
    git fetch origin main
    git log HEAD..origin/main --oneline
    git merge origin/main
    ```
    Conflict only in `docs/architecture/capabilities.md`'s append-only tables — resolve yourself,
    keep both rows. Any other conflict — STOP, escalate with a recommendation. A non-empty merge
    repeats step 9's local gates.
16. **Commit and push only on explicit request.** List paths explicitly. Push from the worktree.
17. **PR** using `.github/PULL_REQUEST_TEMPLATE/backend.md` or `frontend.md` (pick via the PR
    "Preview" template dropdown, or `?template=backend.md&expand=1` / `?template=frontend.md&expand=1`
    in the compare URL). Every section filled or explicitly left blank with a reason.
18. **CI.** Wait for required checks; rule out environmental causes before calling red CI a
    defect (merge-conflict-missing-checks, stale base, transient runner failure).
19. **GATE 2 — STOP.** Human approves the merge. Unassign yourself.

---

## Phase `closure` — after merge

20. **GATE 3 — material for the human.** Ready-to-paste: `docs/PLAN.md` check-off with
    `**Done <date>:**`, `docs/architecture/capabilities.md` capability + mutation rows.
21. **Labels** — merge doesn't remove process labels automatically. Remove by hand, set
    `state:closed`. Confirm assignment is removed.
22. **Clean up the working directory** — release handles/dev servers first, then remove the
    worktree.

---

## Result

At the end of every phase: what passed, what's pending, whose decision it's waiting on, next
action. Compressed per "How you talk to the human" — every question with a recommendation.
