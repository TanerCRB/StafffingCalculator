// API response shapes live here and only here — see agents/developer-frontend.md,
// "API response shapes are modeled in exactly one contracts layer".
//
// Mirrors backend/app/api/schemas/catalog.py (SC-2-01). Four properties of that schema are part of
// the contract and must not be re-interpreted in a component:
//   * `default_cost_rate` is absent/null for a caller without PERSONNEL_COSTS_READ, and that is
//     the *only* reason it can be null — the column is NOT NULL (ADR-0005, addendum 2026-09-19,
//     point 5). "You may not see this cost" is therefore a statement, not missing data, and the
//     screen renders it as a named refusal rather than as a gap.
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

/** One entry of one dimension dictionary. No `kind` field: the kind is the endpoint's path. */
export interface DimensionEntry {
  id: string;
  name: string;
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

  /** Fixed-point decimal string, or null/absent when this caller may not read personnel costs. */
  default_cost_rate?: string | null;
  /** Fixed-point decimal string. Never null: it is not behind the personnel-cost gate. */
  default_selling_rate: string;
  currency: string;
  unit: string;

  effective_from: CalendarDate;
  /** Inclusive; null for an open-ended window. */
  effective_to?: CalendarDate | null;
}

export interface CatalogRateList {
  rates: CatalogRate[];
}

/**
 * The four dictionaries, as the exact path segments that address them — mirrors
 * `DIMENSION_MODELS` in backend/app/data/catalog.py. One list, so the screen cannot read three
 * dictionaries and name four, and so a fifth dimension is added in one place.
 */
export const CATALOG_DIMENSIONS = [
  "roles",
  "seniorities",
  "locations",
  "engagement-types",
] as const;

export type CatalogDimension = (typeof CATALOG_DIMENSIONS)[number];
