---
description: StafffingCalculator — reports where a task stands: gate, evidence, CI, blockers. Read-only, touches nothing
argument-hint: <Issue link or #N or a plan task identifier>
allowed-tools: Read, Grep, Glob, Bash
---

# Task status: $1

A report on **where the task stands**. You change nothing: no writes to files, no commit, no
worktree, no calling roles, no writes through `gh`. You work in the main checkout on `main`.

Argument `$1` accepts an Issue URL, `#N`, or a `SC-<block>-<seq>` identifier. With a plan
identifier, look it up in `docs/PLAN.md` first to find the Issue number.

## What to gather

Run in parallel, one message:

```bash
gh issue view <N> --repo TanerCRB/StafffingCalculator --json number,title,state,labels,assignees,updatedAt,comments
gh pr list --repo TanerCRB/StafffingCalculator --state all --search "<N>" --json number,title,state,isDraft,headRefName,reviewDecision,mergedAt,updatedAt
gh pr checks <PR number> --repo TanerCRB/StafffingCalculator   # once a PR exists — report, don't wait
```

From the repository (read-only): `docs/PLAN.md`'s row for this task; `docs/architecture/capabilities.md`'s
capability and mutation rows; `git log --oneline -15 main -- <task paths>`;
`git branch -a --list '*<identifier>*'`.

## How to read the state

| Signal | Where the task stands |
|---|---|
| Issue missing *Done when* or *Out of scope* | before analysis — Story incomplete |
| `state:analysis`, no impact map or criteria | analysis, before gate 1 |
| `state:decision`, `adr-deviation`, or `waiting-on-human` | **waiting on a human decision** |
| `state:implementation`, no branch and no PR | ready for `code`, nobody has started |
| branch/worktree exists, no PR | code in progress |
| PR open, no Mutation section filled | before `verification` |
| PR open, CI red | rule out environment first (see below) |
| PR open, CI green, no approval | **gate 2**, waiting on the human |
| PR merged, `state:evidence` still set | **gate 3**, waiting on the documentation PR (`Closes #N`) |
| Issue `CLOSED` but not `state:closed` | closed before gate 3 — a finding, flag for reopening |
| `docs/PLAN.md` row checked off with no test/artifact reference | status raised without evidence — a finding |

Before calling red CI a defect: missing checks from a merge conflict, a stale base after `main`
moved on, a transient runner failure, secrets not reaching an automated run.

## Response format

```
<identifier> — <title>            Issue #<N> (<state>) • PR #<M> (<state>)
Position: <phase / gate>          Waiting on: <human / CI / author / nobody>

Evidence
- Done when: <condition> — <satisfied by what / missing>
- Mutations: <list, result> — <none, if there aren't any>
- CI: <list of red checks or "green">

Blockers
- <blocker and what it blocks — or "none">

Next action: <one sentence, who and what>
```

Always give a recommendation — one action, one reason. If sitting at a human gate, say explicitly
whose decision it needs. The full process runs through `.claude/commands/task.md`.
