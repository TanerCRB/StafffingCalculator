# SC-1-27 running-app walkthrough evidence

- Date: 2026-10-08
- Application: local frontend at `http://localhost:5173`, source revision `8d38327` (merged PR #294).
- Tester: repository owner, confirmed in the task conversation that the UI had been tested.
- Observed boundary: after the project and scenario UI was open, the browser sent a `PATCH` to `/projects/{project_id}/scenarios/{scenario_id}/assumptions`. The local API returned `403` with `Caller lacks permission 'scenario_assumptions:write'.`
- Interpretation: the browser reached the selected project's scenario UI through the Projects-to-Overview flow. The 403 is the local placeholder identity's permission refusal; it does not demonstrate a failed project handoff. No successful assumption write is claimed.
- Automated contrast: `frontend/src/App.test.tsx`, test `opens Overview for the selected Projects row with that project's scenarios`, selects two API-listed projects in turn and asserts each Overview exposes its own scenario and not the other project's scenario.
- Limits: this is user-reported manual walkthrough evidence; no screenshot or browser recording was retained. Server-side project authorization remains evidenced by the existing SC-1-05/06 tests, not by this frontend walkthrough.
