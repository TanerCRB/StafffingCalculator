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
//   * `currency` and `unit` travel per row. `unit` is a plain string, not a literal union: the
//     database's CHECK constraint is what limits it to "hour" today, and F-07 adds more (see the
//     `RateUnit` docstring in the backend schema).

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
  /** Fixed-point decimal string. Never null: it is not behind the personnel-cost gate. */
  default_selling_rate: string;
  currency: string;
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
 * `unit` is deliberately **not** a member. The database pins it to `hour` and the backend request
 * model defaults to it, so a client field would offer a choice that does not exist (ADR-0002,
 * addendum 2026-09-21, point 4). `RATE_UNIT_HOUR` below is for *saying* what the unit is, never for
 * asking.
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

  default_cost_rate?: string;
  default_selling_rate?: string;
  currency?: string;

  effective_from?: CalendarDate;
  effective_to?: CalendarDate | null;
}

/**
 * The unit every catalogue rate is priced in, as the database's CHECK constraint enforces it.
 *
 * Here so that a form can *state* the unit (NF-07: "forms shall explain input units") without
 * offering it as a choice, and so that the word is written once. F-07 adds daily and monthly rates;
 * when it does, the unit becomes a field of the request and this constant becomes a default — which
 * is a decision, not a detail of a form.
 */
export const RATE_UNIT_HOUR = "hour";
