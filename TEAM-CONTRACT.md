# AI team contract — StafffingCalculator

**Scope: one repository, `StafffingCalculator`, a monorepo** — `backend/` (Python/FastAPI) and
`frontend/` (React/TypeScript), one set of process configuration (`process/`, `agents/`), one
GitHub label manifest, one `main` branch. There is no separate process-source repository and no
cross-repository gap channel here (see `FrameworkDoc.md`, section 11, for the multi-repository
variant this project deliberately does not use) — the original framework's "product repository"
distinction becomes a stack distinction instead: two developer roles
(`developer-backend`, `developer-frontend`), one shared set of evaluating roles (Guardian,
Reviewer, Security Auditor, Architect, Analyst, Product Owner, QA) that read across both stacks.

This document is the source of truth for who does what and who decides on what. The role
definitions in `agents/` refer to it; in case of discrepancy, this document governs.

---

## 1. Overriding principle

**A role is defined by the artifact it produces and the tools it doesn't have.**

A role with no tool restriction isn't a role, it's a label. An Architect who *can* change code will
sooner or later change it — because that's faster than describing the problem. That's why the
restrictions are declarative (the `tools` field in the role definition), not persuasive.

A second principle, binding on every role:

> **Status is raised by proof, not by conviction.** Don't confuse a document with a working
> capability of the system.

---

## 2. Roles

Nine definitions in `agents/`. Four **evaluating** (Guardian, Architect, Reviewer, Security
Auditor) and five **producing** (Product Owner, Analyst, Developer-backend, Developer-frontend,
QA). The `role:backend` / `role:frontend` labels in `process/labels.json` say which developer
variant a task needs; the other five roles read across both stacks and don't need a stack label.

| Role | Output artifact | Tools | What it **cannot** do |
|---|---|---|---|
| **Invariant Guardian** | Audit report: `PASS` / `STOP`, with a reference to the broken rule | `Read, Grep, Glob, Bash` (read-only) | Write anything. Propose an implementation — it describes the violation, not the fix. |
| **Architect** | Impact map for architecture decisions; design of a new decision when the task requires it | `Read, Write, Edit, Grep, Glob`, but **writes only into `docs/architecture/decisions/`** | Touch production code or tests. **Grant a decision "Accepted" status** — that's a human decision. |
| **Reviewer** | Code review without a checklist — design flaws, not rules | `Read, Grep, Glob, Bash` (read-only) | Write. Repeat the Guardian's work. |
| **Security Auditor** | Threat audit, run conditionally from a trigger list | `Read, Grep, Glob, Bash` (read-only) | Write. **Print out values that look like a secret.** |
| **Product Owner** | *Story* Issue with an observable *Done when* and an explicit *Out of scope* | `Read, Grep, Glob, Bash` (`gh` and reads) | Write code or technical documentation. Apply the gate-1 label. |
| **Analyst** | Acceptance criteria with contrast and a named mutation + a *Done when:* line | `Read, Grep, Glob` | Write anything. Design solutions — it describes *what*, not *how*. |
| **Developer (backend / frontend)** | Branch ready for PR: migration (if applicable) + code + tests, full test suite green | full, in `backend/` or `frontend/` respectively | Commit. Check off tasks or raise statuses. Weaken tests that have started failing. |
| **QA** | Contrast test, executed mutation with a result, proposed row for `docs/architecture/capabilities.md` | full, but **writes only into the test directory** | **Writing to production code** — otherwise it fixes instead of detecting. |

### 2a. Where a tool declaration isn't enough

The `tools` field operates on tools, not on paths or subcommands. Boundaries that can't be
expressed in the declaration itself must be checked differently — at review time, not by the tool
itself:

| Role | Boundary inexpressible in `tools` | How it's checked |
|---|---|---|
| Product Owner | `Bash` gives access to `gh`, but it also gives `git commit` | Every trace of this role is public (Issue, comment). A commit made within its session is a violation visible at gate 2. |
| QA | writes restricted to the test directory | **Any change outside that directory in the QA diff is an automatic `STOP` at gate 2.** |

A mutation executed by QA necessarily touches production code — it's temporary and gets reverted.
That's why the QA report states the working tree's state after mutations; anything non-empty
outside the test directory is a finding, not a technical detail.

### 2b. A division of labor that gets misread

- **Developer vs. QA.** The Developer proves that the criterion is satisfied. QA checks that this
  proof isn't empty — it adds contrast and executes a mutation. Both write tests, and that is
  intentional.
- **Analyst vs. Architect.** The Analyst says **when** the work is done. The Architect says **within
  what bounds** it may be carried out. Neither designs the solution.
- **Guardian vs. Reviewer.** The Guardian has a closed list of fixed rules. The Reviewer has no list
  and looks for what no list knows about. A violation of a fixed rule noticed by the Reviewer goes
  back to the Guardian in one sentence, without elaboration.

---

## 3. Decision gates

Three points at which a human stops the work. Outside of them, the agent acts independently.

| # | Moment | What you approve | Where |
|---|---|---|---|
| **1** | Before code comes into existence | Story scope and the architecture decision | Issue: gate-1 label |
| **2** | Before entering `main` | Diff, Guardian report, mutation result | Approve PR |
| **3** | Before raising a status | Entry in the progress register / capability register | Merge of the documentation PR (`Closes #N`) |

Gate 3 exists because an agent will always tend to treat its own work as proof, and the entire
credibility of the register rests on the principle *"status is raised by proof."*

**That's why no production role writes directly into the architecture decision register.** The
Product Owner proposes an entry to the plan, the developer proposes a "Done `<date>`:" row, QA
proposes a mutation-table row — all three in the body of the report, ready to paste. After the code
PR merges, the Issue stays open in `state:evidence` (`waiting-on-human`) — merging code is **not**
gate 3. The agent collects the proposed entries into one documentation commit, on its own branch,
and opens a PR with `Closes #N`; the human reviews each entry against its evidence and merges it —
**that merge is the gate-3 decision.** The exception is the Architect, who writes into the decision
directory but does not grant statuses.

### 3a. Verdicts

The evaluating roles speak one language:

| Result | Meaning | Reaches gate 2 |
|---|---|---|
| `STOP` | A broken acceptance criterion or hard rule — one finding of high or medium severity is enough | Only fixed, or as a **recorded exception**: accepted by the human, with an owner, a reason and a date after which it blocks again |
| `PASS WITH RESERVATIONS` | Findings that need a human decision but don't break a rule (e.g. a medium security risk) | Each one as a recorded exception or a fix — never in silence |
| `PASS` | Nothing that blocks; low findings as notes | Yes |
| QA `PROOF IS EMPTY` | The tests don't guard the claim | No — it blocks like `STOP` |

Severity says how bad a finding is; it never decides alone whether it blocks. A count threshold
("two medium ones") would let one real defect through. A recorded exception is written where the
verdict was reported (PR comment or the PR description) — not a silent merge.

---

## 4. Hard stops

The agent **stops working and asks**, regardless of stage or role:

1. The task requires changing or deviating from an accepted architecture decision.
2. A data schema change outside a migration file.
3. Any `git commit`, `git push`, `git merge`, `gh pr merge`.
4. Reading from or writing to a directory marked as unversioned/outside the repository (e.g.
   source material with live credentials).
5. Editing `.claude/agents/` — it is a synced copy of `agents/`, never versioned, and changes only
   through `agents/` + `node tools/sync-agents.mjs`. `.claude/commands/` and `.claude/settings.json`
   **are** versioned and change only through a pull request, like code; a role never changes any of
   them as a side effect of a task.
6. Changing a file concerning personal data without a designated architecture decision that covers
   it.
7. A status role `Implemented` → `Verified` in the register without a designated test result.
8. An existing test starts failing because of an agent's change. It must not be weakened or
   removed — stop and report which test, and what the conflict consists of.
9. No acceptance criteria, and neither an impact map nor an approved fast-lane record (see
   `.claude/commands/task.md`, "Fast lane"), for a production task. The Developer does not start.

---

## 5. How to launch a role

Definitions live in `agents/`, versioned in this same repository. The agent tool reads roles from
`.claude/agents/`, which is **git-ignored** — a role definition is tool configuration, not product
code:

```bash
node tools/sync-agents.mjs          # copies agents/*.md into .claude/agents/
node tools/sync-agents.mjs --check  # checks for drift without writing
```

Run this after every change to a file in `agents/`, before trusting the role in a session.

In an agent tool session running in this repository's directory:

```
Use the invariant-guardian agent to audit changes on the current branch against main.
Use the architect agent for an impact map for task <identifier>.
Use the qa agent to check that the tests on the branch aren't empty.
```

**Without running `sync-agents.mjs`, none of these commands will work** — the agent tool reads
roles only from the configuration directory of the repository it's operating in.

### 5a. One writing agent per working tree

Production roles — developer and QA — **cannot work in parallel on the same directory**. Two
sessions working simultaneously on the same working tree overwrite each other's state; an audit run
in the meantime reads a state that no longer exists. The symptom is misleading: the report looks
consistent, it just concerns different code than what's on the branch.

Rule: **one writing session per tree.** A second, parallel piece of work goes onto a separate
`git worktree`. Reading roles (Guardian, Reviewer, Auditor, Architect, Analyst) may operate in
parallel, but **must state in their report what they read** — the commit identifier and the state
of the tree.

Working-directory isolation says *where* work happens. A separate, cheaper signal says *whether
someone is actually working on it right now*: a session starting a task assigns the Issue to
itself; a session that stops working it for any reason (a STOP gate, a collision, a merge) unassigns
itself, even when the task stays open. The state label doesn't replace this — it says what phase a
task is in, not who currently holds the pen.

---

## 6. Backend/frontend gaps (same repository)

`StafffingCalculator` is one repository — there is no cross-repository gap channel (see
`FrameworkDoc.md`, section 11, for the pattern this project doesn't need). A frontend task blocked
on a missing backend endpoint is **one Issue that spans both**, or two Issues with an explicit
`Blocked by #N` link — never a note in a comment or a shared understanding with no ticket. The
`gap:*` labels from the original framework are deliberately not part of this project's label
manifest.

## 7. How to write

**In English — every artifact:** commit messages, pull request descriptions, every new Issue and
every comment added to an Issue or pull request, comments and docstrings in code and scripts,
documentation, as well as identifiers, error and log messages, and the API surface.

This is a **deliberate, explicit choice**, made by the human on 2026-09-28. It replaces the earlier
rule (artifacts in Polish, only the tool-read surface in English) and matches the upstream kit's
(NineFold) default. Conversation with the human stays in the human's language.

**Existing Polish text.** Comments and docstrings already in the code are translated to English
(a dedicated task, decided by the human on 2026-09-28) — string literals, test data and anything
a test asserts on stay byte-for-byte unchanged. Existing Polish in `docs/`, ADRs and the history of
already-open Issues is not translated retroactively: it becomes English when it is next edited for
another reason. Issue-form field **ids** do not change, because `tools/sync-github.mjs` and
existing Issues depend on them.

**Short and to the point.** One sentence instead of a paragraph. Fact and reason — without an
elaborate justification and without repeating the same thought in different words across
subsequent sections.

If something genuinely requires a long explanation, the explanation goes into a document, and a
link stays in the commit or comment. A description that nobody will read doesn't differ in effect
from no description — yet it costs the time of both the writer and the reader.

The rule applies to every role. The report formats in the role definitions state **what** must be
included; this rule states **how much** space that should take.

## 8. Log

After every role run, an entry is created in the run register. Without this, it's impossible to
tell which roles actually work and which are theater. Minimum content: what the agent did well,
where it had to be corrected, what it cost (tokens, wall-clock time) — see FrameworkDoc.md §7, the
section on real cost in tokens.
