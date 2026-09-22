// How a rate row reads: the id → name join across the five dictionaries, and the two named states a
// rate row renders in place of a name.
//
// Extracted from CatalogScreen.tsx when the edit form became a second reader of the same facts
// (SC-2-04). The reason is the one the criteria are about rather than tidiness: the edit form has to
// say *which* rate is being edited, and if it built that sentence itself, the table and the form
// could start naming the same row two different ways — "Internal" in one and a blank in the other,
// or a vendor name in one and "Not in the vendors dictionary" in the other. One join, one set of
// words, two readers.

import type { CatalogDimension, CatalogRate, DimensionEntry } from "../../api/contracts/catalog";
import { unknownEntryLabel } from "./dimensionLabels";

/**
 * Rendered in a cost-rate cell the response did not carry — and, since SC-2-04, in the cost field of
 * an edit form the response did not carry a cost for. The decided literal (SC-2-02, gate-1 decision
 * 2) — not a dash, not a zero, and not a symbol shared with any other kind of absence.
 *
 * The form reuses this constant rather than spelling its own: a caller without
 * `PERSONNEL_COSTS_READ` must meet the same word in the table and in the form, because it is the
 * same statement about the same field, and a second wording would read like a second reason.
 */
export const RESTRICTED_COST_RATE = "Restricted";

/**
 * Rendered in the vendor cell of a rate the response carried with `vendor_id: null` (SC-2-03,
 * K-09), and offered as the named choice in the add form's vendor control (SC-2-04, K-14).
 *
 * A *state*, not an absence: `null` on that field means "this is the organisation's own rate", it
 * is the same answer for every caller, and the backend says so in the same words (see the
 * `vendor_id` docstring in backend/app/api/schemas/catalog.py). It is therefore a fourth literal,
 * deliberately sharing nothing with the three kinds of *missing* this screen renders —
 * `RESTRICTED_COST_RATE` ("removed for you"), `OPEN_ENDED_PERIOD` ("no end date") and
 * `unknownEntryLabel` ("this id matched no dictionary entry"). A dash, a blank, or any placeholder
 * borrowed from one of those would say "we do not know whose price this is" about a row where the
 * server knows exactly.
 *
 * In the form it carries a second weight: it is the *default* choice and it is a choice, so that
 * "no vendor" is something a person selected rather than a field they left empty. An empty option
 * would mean "any", which is a resolution rule the backend refuses to have.
 */
export const INTERNAL_RATE = "Internal";

/** All five dictionaries, always. The record is exhaustive over `CatalogDimension`, so a sixth
 * dimension added to the contract fails the build here instead of quietly going unread. */
export type Dictionaries = Readonly<Record<CatalogDimension, DimensionEntry[]>>;

/** One id → name map per dictionary. */
export type DimensionNames = Readonly<Record<CatalogDimension, ReadonlyMap<string, string>>>;

function namesById(entries: DimensionEntry[]): ReadonlyMap<string, string> {
  return new Map(entries.map((entry) => [entry.id, entry.name]));
}

/** One id → name map per dictionary, built once per render rather than a linear scan per cell. */
export function nameLookups(dictionaries: Dictionaries): DimensionNames {
  return {
    roles: namesById(dictionaries.roles),
    seniorities: namesById(dictionaries.seniorities),
    locations: namesById(dictionaries.locations),
    "engagement-types": namesById(dictionaries["engagement-types"]),
    vendors: namesById(dictionaries.vendors),
  };
}

/** The four dimensions a rate row must name. `vendors` is not among them: it is the one dimension
 * whose id is allowed to be absent, and "absent" there is a state with its own word. */
export type RequiredDimension = Exclude<CatalogDimension, "vendors">;

/** The four dimensions, in the order the table and the form both present them. */
export const REQUIRED_DIMENSIONS: readonly RequiredDimension[] = [
  "roles",
  "seniorities",
  "locations",
  "engagement-types",
];

/** Which id on a rate row addresses which dictionary. An exhaustive `switch`, so a new dimension
 * cannot be forgotten silently. */
export function dimensionIdOf(rate: CatalogRate, dimension: RequiredDimension): string {
  switch (dimension) {
    case "roles":
      return rate.role_id;
    case "seniorities":
      return rate.seniority_id;
    case "locations":
      return rate.location_id;
    case "engagement-types":
      return rate.engagement_type_id;
  }
}

/**
 * What one dimension of one rate row is called on screen.
 *
 * The vendor column is the only one that can read two different ways for reasons that are not a
 * failure of the join (SC-2-03, K-09):
 *
 *   * `vendor_id: null` — the row *is* an internal rate. A named state, `INTERNAL_RATE`.
 *   * `vendor_id` naming a vendor no entry of the dictionary matched — the same gap between two
 *     reads the other four columns already have, and it keeps their wording.
 *
 * Conflating the two is what this function exists to make impossible: `?? ""`, a dash, or reusing
 * `unknownEntryLabel` for the null case would all turn "this is our own rate" into "we do not know
 * whose rate this is".
 */
export function dimensionNameOf(
  rate: CatalogRate,
  dimension: CatalogDimension,
  names: DimensionNames,
): string {
  if (dimension === "vendors") {
    if (rate.vendor_id === null) {
      return INTERNAL_RATE;
    }
    return names.vendors.get(rate.vendor_id) ?? unknownEntryLabel("vendors");
  }
  return names[dimension].get(dimensionIdOf(rate, dimension)) ?? unknownEntryLabel(dimension);
}
