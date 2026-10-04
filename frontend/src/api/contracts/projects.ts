// API response shapes live here and only here — see agents/developer-frontend.md,
// "API response shapes are modeled in exactly one contracts layer".
//
// Mirrors backend/app/api/schemas/project.py (SC-1-05). Two properties of that schema are part
// of the contract and must not be re-interpreted in a component:
//   * a project outside the caller's access scope has NO representation here — no "unavailable"
//     variant, no tombstone. Absence is the only form it takes (F-13, ADR-0005, NF-04).
//   * decimals cross the boundary as fixed-point strings, never JSON floats (NF-01, ADR-0002).

/** Exactly the two values the backend emits for a project (`ProjectStatusLabel`). */
export type ProjectStatus = "Active" | "Archived";

/** Exactly the two values the backend emits for a scenario (`ScenarioStatusLabel`). */
export type ScenarioStatus = "Draft" | "Approved";

export interface DeliveryPeriod {
  /** ISO-8601 calendar date, e.g. "2026-01-01". */
  start: string;
  /** ISO-8601 calendar date, e.g. "2026-12-31". */
  end: string;
}

export interface ScenarioListItem {
  id: string;
  name: string;
  status: ScenarioStatus;
  /** Names of scenario attributes in the backend's Python naming (`working_calendar`, …),
   * computed per scenario. Never rendered raw — see features/projects/scenarioInputLabels.ts. */
  missing_inputs: string[];
  ready_for_approval: boolean;
  /** Fixed-point decimal string (e.g. "12.500"), or null when the scenario has no target yet. */
  target_margin_percent?: string | null;
}

export interface ProjectListItem {
  id: string;
  name: string;
  client: string;
  // No `owner` field: the backend removed it from this payload (B-02 — personal data that
  // nothing renders must not cross the boundary at all). Declaring it here again would be this
  // layer re-guessing a shape the API no longer sends.
  delivery_period: DeliveryPeriod;
  reporting_currency: string;
  description: string;
  status: ProjectStatus;
  scenarios: ScenarioListItem[];
}

/** Detail read returned by GET/PATCH /projects/{id}; the token is passed back unchanged. */
export interface ProjectDetail extends ProjectListItem {
  owner: string;
  updated_at: string;
  target_margin_percent: string | null;
  overload_threshold_percent: string | null;
}

export interface ProjectEditRequest {
  updated_at: string;
  name?: string;
  client?: string;
  owner?: string;
  description?: string;
  reporting_currency?: string;
  delivery_period?: DeliveryPeriod;
}

/** An object, not a bare array — the backend reserves room for filtering/pagination metadata. */
export interface ProjectListResponse {
  projects: ProjectListItem[];
  /** Caller-scoped match count before pagination (ADR-0017). */
  total: number;
}

/** Server-side search, status and offset-page parameters for `GET /projects`. */
export interface ProjectListQuery {
  readonly search?: string;
  readonly status?: ProjectStatus;
  readonly limit?: number;
  readonly offset?: number;
}
