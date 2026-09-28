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
  unassign yourself, even when the Issue stays open and unclosed. Pair every assignment with a
  "Task started" comment (step 0) and every unassignment with a "Task stopped" comment, so a
  second session can tell whether the task is actually taken.
- **A merged code PR is not gate 3.** It moves the Issue to `state:evidence`, still
  `waiting-on-human` — only the separate documentation PR (`Closes #N`) closes it (step 21).

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

**Keep the conversation terse (this project runs caveman mode); keep artifacts in full English.**
Issue bodies, comments, commit messages, pull request descriptions, code comments, and docs are
written in English, per `TEAM-CONTRACT.md`, section 7 — as are identifiers, error/log messages,
and the API surface. Drop compression anywhere it creates ambiguity (irreversible actions,
security).

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

**Pin what the task runs on, and who runs it.** Post one comment on the Issue:

```bash
ROLES=$(cat .claude/agents/*.md .claude/commands/task.md | git hash-object --stdin)
gh issue comment <N> --repo TanerCRB/StafffingCalculator --body \
  "Task started - session: <session id>, worktree: <path>, branch: <branch>, roles: $ROLES"
```

Before each role call, recompute the fingerprint: if it changed mid-task (someone ran the sync, or
edited this command), **stop and ask** — finishing a task under two versions of a role makes its
evidence incomparable. A second session finding a "Task started" comment with no later "Task
stopped" comment treats the task as taken (see "work already in progress elsewhere" above).

---

## Phase `analysis` — up to gate 1

1. **Is the Story complete?** Task identifier, *What and why*, *Done when* (observable), *Out of
   scope (explicit)*, basis in documentation. The `product-owner` role fills gaps.
2. **Plan task number** — reserve the next free `SC-<block>-<seq>` in `docs/PLAN.md` as a separate
   commit before real work, if not already reserved.
3. **`analyst` role** — acceptance criteria + *Done when* row. Every criterion has an observable
   carrier, a contrast, and a named mutation. Verdict: `READY FOR GATE 1` / `STORY NEEDS MORE WORK`.
4. **`architect` role** — **unless the fast lane applies** (see "Fast lane" below). Impact map onto
   `docs/architecture/decisions/`. Outcomes: fits / needs an addendum / needs a new decision
   (deviation Issue + draft in "Draft — pending approval").
5. **GATE 1 — STOP.** Gather: criteria, impact map (or the fast-lane record), open questions
   (options + consequences). Wait for the human. **Don't remove `waiting-on-human` yourself.**
   Post a "Task stopped" comment (see step 0) and unassign yourself.

### Fast lane: when the Architect is skipped

The Architect is skipped only when **every** trigger below is clean, checked mechanically against
the files the task will touch (from the Analyst's criteria and the Issue), not judged:

- no file under `backend/migrations/`;
- no new or changed public API surface (endpoint, contract type, event);
- no new or upgraded dependency (package manifest, lock file);
- no path listed in `docs/architecture/architecture-sensitive-paths.md`;
- the Product Owner raised no deviation label and the Analyst named no "unproven foundation".

If any trigger fires, or you can't tell which files the task will touch, the Architect runs.
Otherwise write a **fast-lane record** as a comment on the Issue: "Architect skipped — triggers
checked: <each trigger, clean>; paths expected: <list>". At gate 1 the human approves that record
together with the criteria — **an approved record takes the place of the impact map** for the
`code` phase's entry condition. The human can require the Architect anyway; that's a gate-1
decision, not a failure of the fast lane.

**The record is checked again against the real diff**, before step 9's local gates: if the
implementation touched any trigger, the record no longer holds — run the Architect before
verification, and go back through gate 1 for what changed.

---

## Phase `code` — implementation

Entry condition: criteria exist **and** either an impact map or an approved fast-lane record
exist, `state:implementation` label present.

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
    in the compare URL). Every section filled or explicitly left blank with a reason. The PR refers
    to the Issue with **`Refs #<N>`, never a closing keyword** — merging it must leave the Issue
    open for gate 3 (step 21).
18. **CI.** Wait for required checks; rule out environmental causes before calling red CI a
    defect (merge-conflict-missing-checks, stale base, transient runner failure).
19. **GATE 2 — STOP.** Human approves the merge. A Guardian/Reviewer/Security-Auditor `STOP` or
    `PASS WITH RESERVATIONS` needs either a fix or a human-recorded exception (owner, reason,
    expiry — `TEAM-CONTRACT.md` §3a) before this gate clears. You don't merge. Post a "Task
    stopped" comment and unassign yourself.

---

## Phase `closure` — after merge

20. **After the merge the Issue is still open**, in `state:evidence` + `waiting-on-human` — merging
    the code PR is not gate 3.
21. **GATE 3 — a documentation PR for the human to decide on.** On a separate branch from the
    updated `main`, commit the entries the roles proposed — `docs/PLAN.md` check-off with
    `**Done <date>:**`, `docs/architecture/capabilities.md` capability + mutation rows — as one
    documentation commit. Every entry links the specific test or artifact it rests on; an entry
    without one isn't written. On the human's request, push it and open the PR with **`Closes
    #<N>`**. **The human's review and merge of that PR is gate 3** — you never merge it, and it is
    that merge that closes the Issue.
22. **Labels** — closing the Issue through the gate-3 documentation PR does not remove process
    labels. The human sets `state:closed` and removes `waiting-on-human` when approving gate 3.
    Confirm assignment is removed — if it never came off at any gate, remove it now.
23. **Clean up the working directory** — release handles/dev servers first, then remove the
    worktree.

---

## Result

At the end of every phase: what passed, what's pending, whose decision it's waiting on, next
action. Compressed per "How you talk to the human" — every question with a recommendation.
