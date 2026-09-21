// The dimension path segments (`roles`, `engagement-types`, …) are addresses, not labels — see
// backend/app/data/catalog.py, DIMENSION_MODELS. The words a project manager reads are here, in one
// place, so a section heading and a table column cannot start naming the same dimension two ways.
//
// Note: this is NOT a translation catalog — no locale mechanism exists in this repository yet (see
// frontend/README.md and features/projects/scenarioInputLabels.ts, which says the same).

import type { CatalogDimension } from "../../api/contracts/catalog";

interface DimensionLabels {
  /** Heading of the dictionary's own section. */
  readonly section: string;
  /** Column header in the rates table — one entry of the dimension, not the collection. */
  readonly column: string;
  /** The dictionary's name inside a sentence ("the roles dictionary is empty"). */
  readonly inSentence: string;
}

export const DIMENSION_LABELS: Readonly<Record<CatalogDimension, DimensionLabels>> = {
  roles: { section: "Roles", column: "Role", inSentence: "roles" },
  seniorities: { section: "Seniorities", column: "Seniority", inSentence: "seniorities" },
  locations: { section: "Locations", column: "Location", inSentence: "locations" },
  "engagement-types": {
    section: "Engagement types",
    column: "Engagement type",
    inSentence: "engagement types",
  },
  vendors: { section: "Vendors", column: "Vendor", inSentence: "vendors" },
};

/**
 * What a rate row says when the id it carries is in no entry of the dictionary that was read.
 *
 * This is reachable without any read failing: the six reads are six requests, not one
 * transaction, so an entry can be renamed or removed between them (SC-2-02, gate-1 decision 8). The
 * answer is a named absence — never a blank cell, and never a row quietly dropped from the table,
 * which would make the rate itself disappear.
 *
 * On the `vendors` dimension this is **not** the same statement as a rate with no vendor at all:
 * "the id on this row matched no vendor" is a gap between two reads, while `vendor_id: null` is the
 * named state "internal" (`INTERNAL_RATE` in CatalogScreen.tsx). Two different facts, two different
 * words — never the one placeholder that would make them read alike (SC-2-03, K-09).
 */
export function unknownEntryLabel(dimension: CatalogDimension): string {
  return `Not in the ${DIMENSION_LABELS[dimension].inSentence} dictionary`;
}

/** The dictionary's own empty state. The section heading stays; only the list is replaced. */
export function emptyDictionaryLabel(dimension: CatalogDimension): string {
  return `The ${DIMENSION_LABELS[dimension].inSentence} dictionary is empty.`;
}
