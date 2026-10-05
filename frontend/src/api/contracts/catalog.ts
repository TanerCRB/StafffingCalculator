// API response shapes live here and only here — see agents/developer-frontend.md,
// "API response shapes are modeled in exactly one contracts layer".
//
// Mirrors backend/app/api/schemas/catalog.py (SC-2-01, SC-2-03). Five properties of that schema are
// part of the contract and must not be re-interpreted in a component:
//   * `default_cost_rate` is absent/null for a caller without PERSONNEL_COSTS_READ, and that is
//     the *only* reason it can be null — the column is NOT NULL (ADR-0005, addendum 2026-09-19,
//     point 5). "You may not see this cost" is therefore a statement, not missing data, and the
//     screen renders it as a named refusal rather than as a gap.
//   * `vendor_id` is a *named state*, not an optional field: it is always in the body, and `null`
//     means "this is the organisation's own rate" — the same answer for every caller, because
//     nothing gates it (ADR-0005, addendum 2026-09-21, point 2). It is spelled `string | null` and
//     not `string | null | undefined` on purpose: an absent key would be a payload this client
//     cannot read, never an internal rate inferred from a gap (SC-2-03, K-09).
//   * `effective_to` is inclusive, and `null` means an open-ended window (ADR-0008, point 2) —
//     never a far-future sentinel a client would have to recognise.
//   * amounts cross the boundary as fixed-point decimal strings, never JSON floats (NF-01,
//     ADR-0002). They stay strings all the way to `lib/money.ts`.
//   * `currency` and `unit` travel per row. `unit` is the *selling* rate's unit, a plain string: the
//     database's CHECK constraint limits it to "hour" and it is not a choice on this screen.
//   * `cost_rate_unit` is the *cost* rate's unit, a closed set (`COST_RATE_UNITS`) that travels and
//     is withheld together with `default_cost_rate` — both present, or both null/absent, never one
//     without the other (SC-5-08; ADR-0002 addendum 2026-09-29). The cost is never read through
//     `unit`, and a withheld unit is never defaulted to "hour".

/** ISO-8601 calendar date, e.g. "2026-01-01". Rendered literally — see lib/dates.ts for why it is
 * never routed through `new Date(...)`. */
export type CalendarDate = string;

/**
 * ADR-0007's concurrency marker, as it crosses the boundary: an ISO-8601 timestamp *with an
 * offset*, carried on every read of a catalogue row and required back on every edit (SC-2-04).
 *
 * A string here and nowhere a `Date`: the client never reads it, never compares it and never
 * formats it — it hands the server back the exact bytes the server sent. Parsing it would create a
 * value the round trip could lose (a millisecond, a microsecond, an offset), and the comparison it
 * feeds happens inside the backend's `UPDATE` statement, not here.
 */
export type ConcurrencyMarker = string;

/** One entry of one dimension dictionary. No `kind` field: the kind is the endpoint's path. */
export interface DimensionEntry {
  id: string;
  name: string;
  /** Required on the list representation too — a dictionary has no detail endpoint, so a marker
   * absent here would be a marker no client could obtain (see the backend schema's docstring). */
  updated_at: ConcurrencyMarker;
}

/** An object, not a bare array — the backend reserves room for filtering/pagination metadata. */
export interface DimensionEntryList {
  entries: DimensionEntry[];
}

/**
 * The units a cost rate can be priced per — exactly the backend's closed set (`COST_RATE_UNITS`,
 * SC-5-08). The response shape check and the create form's control both read this one list, so a
 * fourth unit is added here or nowhere.
 */
export const COST_RATE_UNITS = ["hour", "day", "month"] as const;

export type CostRateUnit = (typeof COST_RATE_UNITS)[number];

export interface CatalogRate {
  id: string;

  role_id: string;
  seniority_id: string;
  location_id: string;
  engagement_type_id: string;

  /**
   * The subcontractor this price belongs to, or `null` for the organisation's own rate (SC-2-03).
   *
   * Required, and nullable: the key is always present. `null` is the named state "internal" — it is
   * not the shape `default_cost_rate` takes when a field was removed for this caller, and the two
   * must not be rendered as the same kind of gap (K-09).
   */
  vendor_id: string | null;

  /** Fixed-point decimal string, or null/absent when this caller may not read personnel costs. */
  default_cost_rate?: string | null;
  /** The unit `default_cost_rate` is an amount per. Gated together with `default_cost_rate`: null or
   * absent exactly when it is, and the client refuses a payload where only one of the two is. */
  cost_rate_unit?: CostRateUnit | null;
  /** Fixed-point decimal string. Never null: it is not behind the personnel-cost gate. */
  default_selling_rate: string;
  currency: string;
  /** The *selling* rate's unit. Never the cost rate's — that is `cost_rate_unit`. */
  unit: string;

  effective_from: CalendarDate;
  /** Inclusive; null for an open-ended window. */
  effective_to?: CalendarDate | null;

  /** ADR-0007's concurrency marker (SC-2-04). Never gated: it is a timestamp of the row, not a
   * fact about a person or a cost, so a caller without `PERSONNEL_COSTS_READ` receives it too —
   * they need it to edit the fields they *can* see without overwriting somebody else's change. */
  updated_at: ConcurrencyMarker;
}

export interface CatalogRateList {
  /** At most `limit` rows (default 2000, K-11) — a page, not necessarily the whole catalogue. */
  rates: CatalogRate[];
  /**
   * The count of every row matching the request's filter, without the page limit applied — never
   * `rates.length`, which is only true while the catalogue fits in one page (K-11). A client
   * compares the two to know whether it is holding everything or page one of more (K-12): render
   * `rates.length` as a complete count only when `total === rates.length`.
   */
  total: number;
}

/**
 * The five dictionaries, as the exact path segments that address them — mirrors
 * `DIMENSION_MODELS` in backend/app/data/catalog.py. One list, so the screen cannot read four
 * dictionaries and name five, and so a sixth dimension is added in one place.
 *
 * `vendors` is the fifth dictionary, not a fifth mechanism (SC-2-03, K-05 on the backend side): it
 * is read, joined and listed exactly like the other four. What is different about it lives on the
 * rate row, not here — `vendor_id` is nullable and the others are not.
 */
export const CATALOG_DIMENSIONS = [
  "roles",
  "seniorities",
  "locations",
  "engagement-types",
  "vendors",
] as const;

export type CatalogDimension = (typeof CATALOG_DIMENSIONS)[number];

/** `cost-categories` shares the backend dictionary route but is not part of the rates screen's
 * five editable dimensions. */
export type CatalogDimensionPath = CatalogDimension | "cost-categories";

/**
 * One row of `GET /catalog/absence-types` (SC-3-04, Issue #135), as much of the backend's
 * `AbsenceTypeEntry` as this client reads: an id and a name, to resolve a staffing absence's
 * `absence_type_id` (`contracts/staffing.ts`). `generates_cost`/`generates_revenue`/
 * `is_statutory_leave` are not declared here — this client renders no flag, only the name, the
 * same "a field this client never reads is a field this contract does not need to promise a shape
 * for" rule `contracts/scenarioResults.ts` states for `assumptions_used`.
 *
 * **Not one of the five `CATALOG_DIMENSIONS`** — the absence-type dictionary has its own route
 * (`/catalog/absence-types`, not `/catalog/dimensions/{dimension}`), for the same reason
 * `working-calendars` does (see backend `app.api.catalog` module docstring: "the first attribute
 * beyond `name` takes a dictionary out of" the shared route). Read through `getCatalogAbsenceTypes`
 * (`api/client.ts`), not `getCatalogDimension`.
 */
export interface CatalogAbsenceTypeEntry {
  id: string;
  name: string;
}

export interface CatalogAbsenceTypeList {
  absence_types: CatalogAbsenceTypeEntry[];
}

// --- Working calendars (F-05, SC-3-02, consumed by SC-3-06) --------------------------------------
// Mirrors backend/app/api/schemas/catalog.py's WorkingCalendarDayEntry/WorkingCalendarEntry/
// WorkingCalendarList. Read only — no PATCH/POST exists for a calendar, its basis or its days
// (Issue #140, "Out of scope": SC-3-02 shipped no edit form and none has been added since).

/** One exceptional day of one calendar — an override of what `week_pattern` would otherwise say for
 * that date. */
export interface WorkingCalendarDayEntry {
  day: CalendarDate;
  /** `"non_working"` (a holiday the week pattern would have made a working day) or `"working"` (an
   * extra working day the pattern would not have) — a plain string validated by the database's enum
   * type, not a closed union here, for the reason `RateUnit` above gives for its own field: the
   * schema is not where the list of values belongs. Rendered through `calendarDayKindLabel`
   * (`features/catalog/workingCalendarLabels.ts`), never printed as this raw string (criterion
   * K-02). */
  kind: string;
}

export interface WorkingCalendarEntry {
  id: string;
  name: string;
  /** Fixed-point decimal string (`NUMERIC(4,2)`) — an hours figure, "one multiplication away from
   * money" (the backend schema's own phrase). Rendered through `lib/hours.ts`'s
   * `formatHoursString`, the established convention for an hours value (ADR-0002, addendum
   * 2026-09-26) — never through `lib/days.ts`, which is for `budget_days` alone. */
  standard_hours_per_day: string;
  /** Seven characters, Monday first, `'1'` for a working day (`backend/app/api/schemas/catalog.py`).
   * Never rendered as this raw string — always interpreted per weekday through `weekPatternDays`
   * (`features/catalog/workingCalendarLabels.ts`, criterion K-01). */
  week_pattern: string;
  days: WorkingCalendarDayEntry[];
  updated_at: ConcurrencyMarker;
}

export interface WorkingCalendarList {
  calendars: WorkingCalendarEntry[];
}

// --- The absence budget (F-05, SC-3-03, consumed by SC-3-06) --------------------------------------
// Mirrors backend/app/api/schemas/catalog.py's StatutoryLeaveRegime/AbsenceBudgetEntry/
// AbsenceBudgetList/AbsenceBudgetCreateRequest. `CATALOG_READ`/`CATALOG_WRITE` gate it, the same pair
// as every other catalogue dictionary and no new permission (ADR-0005, addendum 2026-09-22 SC-3-03,
// points 2-3) — `budget_days` is a day count, not a personnel cost, and stays that way only while no
// response derives a monetary figure from it (see `docs/architecture/architecture-sensitive-paths.md`).

/** The backend's `NOT_APPLICABLE` sentinel (`app.core.money`), exactly as `generates_cost`/
 * `generates_revenue` carry it when `statutory_leave_state` is `"no_statutory_leave_type"` — each
 * domain names its own copy of this literal rather than sharing one constant across domains (the
 * established convention: `contracts/staffing.ts`'s `HOURS_NOT_APPLICABLE`,
 * `contracts/scenarioResults.ts`'s `RESULTS_NOT_APPLICABLE`). */
export const BUDGET_REGIME_NOT_APPLICABLE = "n/a";

/** Every `statutory_leave_state` the backend can emit — exactly the backend's closed set
 * (`AbsenceBudgetEntry.statutory_leave_state` docstring). */
export const STATUTORY_LEAVE_STATES = ["resolved", "no_statutory_leave_type"] as const;

export type StatutoryLeaveState = (typeof STATUTORY_LEAVE_STATES)[number];

/** The regime a resolved budget's statutory-leave type carries, nested under
 * `AbsenceBudgetEntry.statutory_leave` — read from the single absence type flagged
 * `is_statutory_leave`, never recomputed by this client from any other dictionary. */
export interface StatutoryLeaveRegime {
  absence_type_id: string;
  name: string;
  generates_cost: boolean;
  generates_revenue: boolean;
}

export interface AbsenceBudgetEntry {
  id: string;
  calendar_id: string;
  engagement_type_id: string;

  /** Fixed-point decimal string (`NUMERIC(6,2)`) — the fourth named class of decimal value in this
   * codebase, after money, percentage and hours (ADR-0002, addendum 2026-09-27 SC-3-06). Rendered
   * through `lib/days.ts`'s `formatBudgetDaysString`, never `formatHoursString` (wrong unit suffix)
   * and never a call site's own `toFixed()`. */
  budget_days: string;
  unit: string;
  /** Mandatory and non-blank — a number nobody can trace to a rule is a number nobody can check.
   * **Never the author of the entry**: there is no column for one (ADR-0005, addendum 2026-09-22
   * SC-3-03, point 6; criterion K-06). */
  source: string;

  /** Inclusive at both ends and never absent/`null` — this is the one table of ADR-0008's pattern
   * whose window may not be open-ended (addendum SC-3-03, point 10b): a screen has no "unbounded"
   * case to render here, unlike a catalogue rate's `effective_to`. */
  effective_from: CalendarDate;
  effective_to: CalendarDate;

  statutory_leave_state: StatutoryLeaveState;
  statutory_leave: StatutoryLeaveRegime | null;
  /** The regime, flattened beside the budget's own fields for a table to render directly.
   * `"n/a"` — never `false` — **exactly when** `statutory_leave_state` is
   * `"no_statutory_leave_type"` (criterion K-03): a `false` there would read as a decided answer
   * ("this leave never costs money") that nobody, in fact, decided. */
  generates_cost: boolean | typeof BUDGET_REGIME_NOT_APPLICABLE;
  generates_revenue: boolean | typeof BUDGET_REGIME_NOT_APPLICABLE;

  updated_at: ConcurrencyMarker;
}

export interface AbsenceBudgetList {
  budgets: AbsenceBudgetEntry[];
}

/** The unit every absence budget is counted in, as the database's CHECK constraint enforces it —
 * the budget's counterpart to `RATE_UNIT_HOUR` below. */
export const BUDGET_UNIT_DAY = "day";

/**
 * The body of `POST /catalog/absence-budgets` (SC-3-03). `extra="forbid"` on the backend: a body
 * naming `author`/`entered_by`/`user`/`approved_by` is a `422` naming the field, not a silently
 * dropped one — there is no column for a person's name, and this type carries none to send
 * (criterion K-06).
 */
export interface AbsenceBudgetCreateRequest {
  calendar_id: string;
  engagement_type_id: string;
  budget_days: string;
  unit?: string;
  source: string;
  effective_from: CalendarDate;
  effective_to: CalendarDate;
}

// --- Request shapes (SC-2-04, ADR-0009) --------------------------------------------------------
// Mirrors the four request models in backend/app/api/schemas/catalog.py. They live here, beside the
// response shapes, for the reason the file header gives: one contracts layer. A form that assembled
// its own object literal would be a second, undeclared description of the same endpoint — and the
// two would diverge at the first backend change, silently, because `fetch` takes any body at all.
//
// Every request model on the backend is `extra="forbid"`, so a key this client invents is a `422`,
// not a silently dropped field. That is the reason these are exact types rather than
// `Record<string, unknown>`: the compiler is the first place the mistake is visible.

/** The body of `POST /catalog/dimensions/{dimension}`. The dimension is the path, not a field. */
export interface DimensionEntryCreateRequest {
  name: string;
}

/** The body of `PATCH /catalog/dimensions/{dimension}/{entry_id}`.
 *
 * Both fields are required by the backend: a dictionary entry *is* its name, so "partial" and
 * "complete" would be the same request, and an edit without a marker is a malformed request rather
 * than an edit that skips the check. */
export interface DimensionEntryEditRequest {
  updated_at: ConcurrencyMarker;
  name: string;
}

/**
 * The body of `POST /catalog/rates`.
 *
 * `unit` (the selling rate's) is deliberately **not** a member. The database pins it to `hour` and
 * the backend request model defaults to it, so a client field would offer a choice that does not
 * exist (ADR-0002, addendum 2026-09-21, point 4). `RATE_UNIT_HOUR` below is for *saying* what that
 * unit is, never for asking. The cost rate's unit is a member, and a real choice (SC-5-09).
 */
export interface CatalogRateCreateRequest {
  role_id: string;
  seniority_id: string;
  location_id: string;
  engagement_type_id: string;

  /** `null` is the named state "the organisation's own rate", never "any vendor" and never a nil
   * UUID sentinel — the same convention the response uses (SC-2-03). */
  vendor_id: string | null;

  /** Fixed-point decimal strings, at the precision the human typed. Never a JS `number`, and never
   * rounded on the way in: `NUMERIC(14,4)` is the column, a value the backend cannot keep is a
   * `422`, and a client that rounded would silently change somebody's cost rate (ADR-0002,
   * addendum 2026-09-21, points 1-2). */
  default_cost_rate: string;
  /** What `default_cost_rate` is an amount per. Required: the backend defaults it to "hour", and the
   * form asks for an explicit choice instead of inheriting that default (SC-5-09). The selling
   * rate's `unit` is not sent — it is not a choice. */
  cost_rate_unit: CostRateUnit;
  default_selling_rate: string;

  /** ISO-4217, uppercase. Not normalised here: the backend rejects `eur` rather than upper-casing
   * it (`Iso4217Code`), precisely so that two spellings of one currency cannot reach one column — a
   * client that "helped" would hide the bug the boundary exists to surface (ADR-0002, addendum
   * 2026-09-21, point 3; gate-1 decision P-5). */
  currency: string;

  effective_from: CalendarDate;
  /** Inclusive, exactly as sent; `null` is the named state "open-ended". The `+ 1 day` conversion
   * to PostgreSQL's half-open form lives in the generated column and nowhere else (ADR-0008,
   * point 3) — nothing on this side reproduces it. */
  effective_to: CalendarDate | null;
}

/**
 * The body of `PATCH /catalog/rates/{rate_id}` — **partial, and that is the load-bearing property**
 * (Issue #49, gate-1 decision Q-2).
 *
 * A field absent from the object is a field the edit does not touch. The case it exists for is
 * `default_cost_rate`: a caller without `PERSONNEL_COSTS_READ` never receives it on any read, so
 * their correction of a selling rate or a window has to be expressible without it. Whole-row
 * semantics would leave them sending a number they invented.
 *
 * `effective_to` is the one field whose explicit `null` is a value rather than a mistake — it makes
 * the window open-ended. Absent and `null` are therefore two different requests, and the backend
 * reads the difference off `model_fields_set`. Every other field sent as `null` is a `422`.
 */
export interface CatalogRateEditRequest {
  updated_at: ConcurrencyMarker;

  /** `default_cost_rate` and `cost_rate_unit` go together or not at all: the backend answers `422`
   * to exactly one of them. A caller who never received either sends neither. */
  default_cost_rate?: string;
  cost_rate_unit?: CostRateUnit;
  default_selling_rate?: string;
  currency?: string;

  effective_from?: CalendarDate;
  effective_to?: CalendarDate | null;
}

/**
 * The unit every catalogue *selling* rate is priced in, as the database's CHECK constraint enforces
 * it (the cost rate's unit is `COST_RATE_UNITS`).
 *
 * Here so that a form can *state* the unit (NF-07: "forms shall explain input units") without
 * offering it as a choice, and so that the word is written once.
 */
export const RATE_UNIT_HOUR = "hour";
