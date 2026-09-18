# Task plan register

One row per task, one task identifier per row: `SC-<block>-<seq>`. A task is checked off only with
a `**Done <date>:**` line pointing at a specific test or artifact — never on the strength of code
existing (see `FrameworkDoc.md`, section 7). Checking off a row is gate 3 and is done by the human,
never by an agent.

Process rollout tasks (adapting `agents/`, `process/`, this bootstrap) are **not** listed here —
per `FrameworkDoc.md`, section 3, they have no Issue and live only in the repository's commit
history / this file's own change log, not as tracked product work.

## Blocks

| Block | Area | Requirements |
|---|---|---|
| 1 | Projects, scenarios, access control | F-01, F-02, F-13 |
| 2 | Roles, resources, rates | F-03 |
| 3 | Staffing planning, calendars, absences | F-04, F-05 |
| 4 | Commercial models & revenue | F-06 |
| 5 | Personnel & additional costs | F-07, F-08 |
| 6 | Scenario comparison & sensitivity | F-09 |
| 7 | Results, metrics, visualization, export | F-10, F-11 |
| 8 | History, reproducibility, versioning | F-12 |

## Tasks

- [ ] **SC-1-01** — Persist a Project: create/read a Project with name, client, owner, delivery
  period, reporting currency, description; server-side access restricted to the project's
  assigned users.
  *Done when:* `backend/tests` prove: (1) a created Project is retrievable with the fields above,
  and (2) a user without access to a Project gets a response indistinguishable from
  "does not exist" — not a 403 that confirms the Project's existence.
  **Out of scope (explicit):** editing, archiving, copying (F-01) — separate tasks. Scenario
  creation (needs the Project to exist first).

*(further rows are added by the Product Owner role, one per task, following gate 1)*
