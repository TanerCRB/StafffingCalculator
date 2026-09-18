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

- [x] **SC-1-01** — Persist a Project: create/read a Project with name, client, owner, delivery
  period, reporting currency, description; server-side access restricted to the project's
  assigned users.
  *Done when:* `backend/tests` prove: (1) a created Project is retrievable with the fields above,
  and (2) a user without access to a Project gets a response indistinguishable from
  "does not exist" — not a 403 that confirms the Project's existence.
  **Out of scope (explicit):** editing, archiving, copying (F-01) — separate tasks. Scenario
  creation (needs the Project to exist first).
  **Done 2026-09-18:** `backend/tests/test_project_create_read.py::test_sc_1_01_01_created_project_is_retrievable_with_all_its_fields`
  + `::test_sc_1_01_01_created_project_is_committed_and_readable_from_another_connection` (kryt. 1),
  `::test_sc_1_01_02_out_of_scope_project_is_indistinguishable_from_one_that_does_not_exist`
  (kryt. 2, z kontrastem: ten sam id, wołający z dostępem → 200) + `::test_sc_1_01_02_unknown_project_id_is_not_found` —
  88 testów backendowych zielono na prawdziwym PostgreSQL (testcontainers); migracja
  `backend/migrations/versions/4f0a9c1b7d62_require_non_blank_project_name_client_owner.py`.
  PR #20.

- [x] **SC-1-05** — Zwróć listę projektów wołającego użytkownika (odczyt, bez akcji zapisu).
  *Done when:* `backend/tests` prove: (1) `test_project_list_omits_projects_outside_caller_access`
  passes — an in-scope project is present, an out-of-scope project is absent (not marked
  unavailable), both asserted in the same response; (2) a project supports 2 independent
  scenarios, proven at the data layer (fixture-level writes, no edit endpoint required); (3) a
  draft with missing inputs is returned with its own missing-field list and a not-ready flag,
  two drafts with different gaps produce different lists; (4) an already-archived project (state
  set by fixture, not by an archive action) still appears on the default list, marked Archived,
  while an out-of-scope project stays absent regardless of archive state.
  **Out of scope (explicit):** search/filter/pagination (separate Story once the list exceeds
  ~20 projects); Commercial Model column (data doesn't exist until plan block 4); the archive/
  edit/copy *actions* themselves (SC-1-02..04) — this task only reads state, never writes it.
  Real authentication — caller identity is a dated placeholder deviation recorded in
  `docs/architecture/decisions/ADR-0005-model-dostepu.md`, addendum 2026-09-18.
  **Done 2026-09-18:** `backend/tests/test_project_list.py::test_project_list_omits_projects_outside_caller_access`
  (kryt. 1), `::test_sc_1_05_02_project_supports_two_independent_scenarios` (kryt. 2),
  `::test_sc_1_05_03_drafts_report_their_own_missing_inputs_and_are_not_ready` +
  `::test_sc_1_05_03_approved_scenario_with_a_gap_is_still_reported_as_not_ready` (kryt. 3),
  `::test_sc_1_05_04_archived_project_stays_on_the_default_list_marked_archived` (kryt. 4) —
  30 testów backendowych zielono na prawdziwym PostgreSQL (testcontainers) po `alembic upgrade
  head`; migracja `backend/migrations/versions/09191236aba0_create_projects_scenarios_project_access.py`.
  PR #18.

- [x] **SC-1-06** — Pokaż PM-owi ekran listy projektów ze scenariuszami wybranego projektu (odczyt).
  Blocked by SC-1-05.
  *Done when:* `frontend/src` vitest tests pass under the names: `renders one row per project
  returned by the API, with name, client, delivery period and status`; `renders an empty-state
  message and no project rows when the API returns an empty list`; `exposes view, edit, copy,
  archive and add-scenario controls for every project row as named controls reachable by
  keyboard`; `renders a prompt instead of scenario details until a project is selected`; `lists
  the scenarios of the selected project with their status as text, not colour alone` (two
  projects with different scenario sets must render differently); `marks a draft scenario as not
  ready for approval and names its missing inputs` (two drafts with different gaps render
  differently).
  **Out of scope (explicit):** pixel fidelity to `Wymagania/UI/Project List.jpeg` (reference, not
  spec — no visual-regression tests); the screens the row controls point to (F-02, SC-1-02..04)
  — controls are rendered and reachable, not wired to any write; role-based control hiding
  (F-13); mobile/responsive layout (NF-09); the list taking any access/visibility decision of its
  own — screen content is a pure function of the API response (NF-04, ADR-0005).
  **Done 2026-09-18:** `frontend/src/features/projects/ProjectListScreen.test.tsx` +
  `frontend/src/lib/money.test.ts` — 25 testów frontendowych zielono, w tym wszystkich 6
  wymaganych nazw kryteriów, dwa projekty z różnymi zestawami scenariuszy, dwa drafty z różnymi
  brakami, zaokrąglanie ADR-0002 na stringu dziesiętnym ("1.005" → "1.01%"), timeout obejmujący
  fazę nagłówków i ciała odpowiedzi. PR #18.

*(further rows are added by the Product Owner role, one per task, following gate 1)*
