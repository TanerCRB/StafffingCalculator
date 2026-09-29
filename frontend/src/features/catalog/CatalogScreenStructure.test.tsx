import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CatalogRate, DimensionEntry } from "../../api/contracts/catalog";
import { CatalogScreen } from "./CatalogScreen";
import * as outcomes from "./writeOutcome";

/**
 * SC-2-05, K-27 — the accessibility tree and the reading order of the catalogue screen, pinned in
 * literals.
 *
 * SC-2-05 is a restyle: it may change how the screen looks and nothing about what the screen *is*
 * to anybody not looking at it. That claim is only testable against a record of the screen as it
 * was, so this file is that record — every heading (with its level), button, table, column header,
 * cell, form, field, list and list item, in document order, each with the accessible name the
 * platform computes for it, and every piece of text on the screen, in the order it is read.
 *
 * Every expected value is a literal typed here, never a constant imported from the component: a
 * test that reads its expectation from the code under test agrees with any change to that code.
 *
 * The accessible name is the one Testing Library computes (the `name` matcher receives it), not a
 * `textContent` this file reconstructs. That is what makes the icons SC-2-05 adds checkable here: an
 * icon that leaked a `<title>` or lost `aria-hidden` would change a name below, while `textContent`
 * — which an `<svg>` without text does not touch — would stay the same.
 *
 * **Written first and green on the branch point** (merge-base of `SC-2-05` with `main`, before any
 * CSS or JSX change) — the evidence that it describes the screen as it was, not a screen invented to
 * fit the change. The two differences it has accepted since are named where they are, and only
 * these (Issue #59, gate-1):
 *
 *   * **G-2** — the new static text from the mockup: the screen description, the rates section
 *     description, the "Dimensions" heading with its description, and the rates table footer.
 *   * **G-9** — Cancel now comes before Save in every catalogue form, in the DOM and on screen.
 */

// --- Fixtures ----------------------------------------------------------------------------------

const MARKER = "2026-09-20T09:00:00+00:00";

function entry(id: string, name: string): DimensionEntry {
  return { id, name, updated_at: MARKER };
}

const DICTIONARIES: Readonly<Record<string, DimensionEntry[]>> = {
  roles: [
    entry("a0000000-0000-0000-0000-000000000001", "Backend engineer"),
    entry("a0000000-0000-0000-0000-000000000002", "Project manager"),
  ],
  seniorities: [entry("b0000000-0000-0000-0000-000000000001", "Senior")],
  locations: [entry("c0000000-0000-0000-0000-000000000001", "Poland")],
  "engagement-types": [entry("d0000000-0000-0000-0000-000000000001", "Contractor")],
  vendors: [entry("f0000000-0000-0000-0000-000000000001", "Acme Technology")],
};

/** Internal, cost withheld, closed window. */
const RATE_INTERNAL: CatalogRate = {
  id: "e0000000-0000-0000-0000-000000000001",
  role_id: "a0000000-0000-0000-0000-000000000001",
  seniority_id: "b0000000-0000-0000-0000-000000000001",
  location_id: "c0000000-0000-0000-0000-000000000001",
  engagement_type_id: "d0000000-0000-0000-0000-000000000001",
  vendor_id: null,
  default_cost_rate: null,
  cost_rate_unit: null,
  default_selling_rate: "260.00",
  currency: "PLN",
  unit: "hour",
  effective_from: "2026-09-01",
  effective_to: "2027-02-28",
  updated_at: MARKER,
};

/** A vendor's rate, cost carried, open-ended window. */
const RATE_VENDOR: CatalogRate = {
  ...RATE_INTERNAL,
  id: "e0000000-0000-0000-0000-000000000002",
  role_id: "a0000000-0000-0000-0000-000000000002",
  vendor_id: "f0000000-0000-0000-0000-000000000001",
  default_cost_rate: "180.00",
  cost_rate_unit: "hour",
  default_selling_rate: "280.00",
  effective_to: null,
};

function stubCatalog() {
  const fetchMock = vi.fn(async (url: string) => {
    const path = new URL(url).pathname;
    if (path === "/catalog/rates") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ rates: [RATE_INTERNAL, RATE_VENDOR], total: 2 }),
      };
    }
    const dimension = path.replace("/catalog/dimensions/", "");
    const entries = DICTIONARIES[dimension];
    if (entries === undefined) {
      throw new Error(`Unexpected read: ${path}`);
    }
    return { ok: true, status: 200, json: async () => ({ entries }) };
  });
  vi.stubGlobal("fetch", fetchMock);
}

// --- Reading the tree --------------------------------------------------------------------------

const ROLES = [
  "region",
  "heading",
  "button",
  "table",
  "columnheader",
  "cell",
  "form",
  "textbox",
  "combobox",
  "status",
  "alert",
  "list",
  "listitem",
] as const;

/**
 * `role | accessible name`, with the heading level appended to `heading`, for every element of the
 * roles above, in document order. The name is captured from the matcher Testing Library calls with
 * the computed accessible name — so this is the platform's answer, not a guess at it.
 */
function accessibilityTree(container: HTMLElement): string[] {
  const names = new Map<Element, string>();
  const roles = new Map<Element, string>();
  for (const role of ROLES) {
    const found = within(container).queryAllByRole(role, {
      name: (name, element) => {
        if (element !== null) names.set(element, name);
        return true;
      },
    });
    for (const element of found) {
      if (!roles.has(element)) roles.set(element, role);
    }
  }
  return [...roles.keys()]
    .sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1))
    .map((element) => {
      const role = roles.get(element) ?? "";
      const level = role === "heading" ? ` ${element.tagName.toLowerCase()}` : "";
      return `${role}${level} | ${names.get(element) ?? ""}`;
    });
}

/** Every non-empty text node, whitespace-normalised, in document order — the order it is read. */
function readingOrder(container: Element): string[] {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const texts: string[] = [];
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    const text = (node.textContent ?? "").replace(/\s+/g, " ").trim();
    if (text !== "") texts.push(text);
  }
  return texts;
}

/** `list` with `insert` placed directly after the one entry equal to `anchor`. */
function insertedAfter(list: readonly string[], anchor: string, insert: readonly string[]): string[] {
  const index = list.indexOf(anchor);
  if (index === -1 || list.indexOf(anchor, index + 1) !== -1) {
    throw new Error(`Anchor must occur exactly once: ${anchor}`);
  }
  return [...list.slice(0, index + 1), ...insert, ...list.slice(index + 1)];
}

async function renderReady(): Promise<HTMLElement> {
  stubCatalog();
  const { container } = render(<CatalogScreen />);
  await screen.findByRole("table");
  return container;
}

// --- The record --------------------------------------------------------------------------------

const EDIT_INTERNAL =
  "Edit the default rate for Backend engineer, Senior, Poland, Contractor, Internal, 2026-09-01 – 2027-02-28";
const EDIT_VENDOR =
  "Edit the default rate for Project manager, Senior, Poland, Contractor, Acme Technology, 2026-09-01 – Open-ended";

/** The screen once the six reads answered, no form open. */
const READY_TREE: readonly string[] = [
  "region | Roles & rates",
  "heading h2 | Roles & rates",
  "region | Default rates",
  "heading h3 | Default rates",
  "button | Add default rate",
  "table | Default rates in the catalogue",
  "columnheader | Role",
  "columnheader | Seniority",
  "columnheader | Location",
  "columnheader | Engagement type",
  "columnheader | Vendor",
  "columnheader | Effective period",
  "columnheader | Default selling rate",
  "columnheader | Default cost rate",
  "columnheader | Actions",
  "cell | Backend engineer",
  "cell | Senior",
  "cell | Poland",
  "cell | Contractor",
  "cell | Internal",
  "cell | 2026-09-01 – 2027-02-28",
  "cell | 260.00 PLN / hour",
  "cell | Restricted",
  "cell | Edit",
  `button | ${EDIT_INTERNAL}`,
  "cell | Project manager",
  "cell | Senior",
  "cell | Poland",
  "cell | Contractor",
  "cell | Acme Technology",
  "cell | 2026-09-01 – Open-ended",
  "cell | 280.00 PLN / hour",
  "cell | 180.00 PLN / hour",
  "cell | Edit",
  `button | ${EDIT_VENDOR}`,
  // G-2: the one element SC-2-05 adds to the tree. An `h3`, a sibling of the panels' own `h3`s —
  // demoting those to `h4` would change five heading levels (out of scope 11).
  "heading h3 | Dimensions",
  "region | Roles",
  "heading h3 | Roles",
  "button | Add role",
  "list | ",
  "listitem | ",
  "button | Rename Backend engineer in the roles dictionary",
  "listitem | ",
  "button | Rename Project manager in the roles dictionary",
  "region | Seniorities",
  "heading h3 | Seniorities",
  "button | Add seniority",
  "list | ",
  "listitem | ",
  "button | Rename Senior in the seniorities dictionary",
  "region | Locations",
  "heading h3 | Locations",
  "button | Add location",
  "list | ",
  "listitem | ",
  "button | Rename Poland in the locations dictionary",
  "region | Engagement types",
  "heading h3 | Engagement types",
  "button | Add engagement type",
  "list | ",
  "listitem | ",
  "button | Rename Contractor in the engagement types dictionary",
  "region | Vendors",
  "heading h3 | Vendors",
  "button | Add vendor",
  "list | ",
  "listitem | ",
  "button | Rename Acme Technology in the vendors dictionary",
];

const READY_TEXT: readonly string[] = [
  "Roles & rates",
  // G-2
  "Manage default rates and the shared dictionaries used across staffing plans.",
  "Default rates",
  // G-2
  "Rates are matched by role, seniority, location, engagement type and vendor.",
  "Add default rate",
  "2 default rates",
  "Default rates in the catalogue",
  "Role",
  "Seniority",
  "Location",
  "Engagement type",
  "Vendor",
  "Effective period",
  "Default selling rate",
  "Default cost rate",
  "Actions",
  "Backend engineer",
  "Senior",
  "Poland",
  "Contractor",
  "Internal",
  "2026-09-01 – 2027-02-28",
  "260.00 PLN / hour",
  "Restricted",
  "Edit",
  "Project manager",
  "Senior",
  "Poland",
  "Contractor",
  "Acme Technology",
  "2026-09-01 – Open-ended",
  "280.00 PLN / hour",
  "180.00 PLN / hour",
  "Edit",
  // G-2: the table's footnote, then the "Dimensions" heading and its description.
  "Cost rates may be restricted by access permissions.",
  "Dimensions",
  "Shared name dictionaries used to configure staffing and default rates.",
  "Roles",
  "Add role",
  "Backend engineer",
  "Rename",
  "Project manager",
  "Rename",
  "Seniorities",
  "Add seniority",
  "Senior",
  "Rename",
  "Locations",
  "Add location",
  "Poland",
  "Rename",
  "Engagement types",
  "Add engagement type",
  "Contractor",
  "Rename",
  "Vendors",
  "Add vendor",
  "Acme Technology",
  "Rename",
];

/** The dictionary-entry form, as it appears inside the roles panel. */
const ENTRY_FORM_TREE: readonly string[] = [
  "form | Add an entry to the roles dictionary",
  "textbox | Role name",
  // G-9: Cancel before Save (gate-1). The one change of order this task makes.
  "button | Cancel",
  "button | Save new entry",
];

const ENTRY_FORM_TEXT: readonly string[] = [
  "Add an entry to the roles dictionary",
  "Role name",
  // G-9: Cancel before Save (gate-1). The one change of order this task makes.
  "Cancel",
  "Save new entry",
];

/** The rate form, as it appears inside the rates panel. */
const RATE_FORM_TREE: readonly string[] = [
  "form | Add a default rate",
  "combobox | Role",
  "combobox | Seniority",
  "combobox | Location",
  "combobox | Engagement type",
  "combobox | Vendor",
  "textbox | Default cost rate",
  "combobox | Cost rate unit",
  "textbox | Default selling rate",
  "textbox | Currency",
  // G-9: Cancel before Save (gate-1). The one change of order this task makes.
  "button | Cancel",
  "button | Save new rate",
];

const RATE_FORM_TEXT: readonly string[] = [
  "Add a default rate",
  "Role",
  "Choose from the roles dictionary",
  "Backend engineer",
  "Project manager",
  "Seniority",
  "Choose from the seniorities dictionary",
  "Senior",
  "Location",
  "Choose from the locations dictionary",
  "Poland",
  "Engagement type",
  "Choose from the engagement types dictionary",
  "Contractor",
  "Vendor",
  "Internal",
  "Acme Technology",
  '"Internal" is the organisation\'s own rate — a choice, not an empty field.',
  "Default cost rate",
  "A decimal amount, up to four decimal places. It is sent exactly as typed — nothing here rounds it. The catalogue returns personnel costs only to callers permitted to read them. If yours is not, this value will read \"Restricted\" after saving — to you as well.",
  "Cost rate unit",
  "Choose a unit",
  "hour",
  "day",
  "month",
  "The default cost rate above is an amount per this unit.",
  "Default selling rate",
  "A decimal amount, up to four decimal places. It is sent exactly as typed — nothing here rounds it.",
  "Currency",
  "An ISO-4217 code in capitals, for example EUR or PLN. The server decides which codes it accepts; this form keeps no list of its own.",
  "Unit",
  "hour",
  "Every selling rate is priced per hour. The unit is not a choice.",
  "Effective from",
  "The first day this rate applies.",
  "Effective to",
  "The last day this rate applies, included. Leave it empty for an open-ended window.",
  // G-9: Cancel before Save (gate-1). The one change of order this task makes.
  "Cancel",
  "Save new rate",
];

describe("the catalogue screen's accessibility tree and reading order (SC-2-05, K-27)", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("keeps every role, heading level and accessible name of the ready screen, in the same order", async () => {
    const container = await renderReady();

    expect(accessibilityTree(container)).toEqual(READY_TREE);
    expect(readingOrder(container)).toEqual(READY_TEXT);
  });

  it("opens the dictionary-entry form in the same place in the reading order, with the same names", async () => {
    const container = await renderReady();

    fireEvent.click(screen.getByRole("button", { name: "Add role" }));

    // Directly after the panel's own "Add role" control and before the panel's list — the form sits
    // above the entries it changes (G-5, variant B: the place is unchanged, only the look).
    expect(accessibilityTree(container)).toEqual(
      insertedAfter(READY_TREE, "button | Add role", ENTRY_FORM_TREE),
    );
    expect(readingOrder(screen.getByRole("form"))).toEqual(ENTRY_FORM_TEXT);
  });

  it("opens the rate form in the same place in the reading order, with the same names", async () => {
    const container = await renderReady();

    fireEvent.click(screen.getByRole("button", { name: "Add default rate" }));

    expect(accessibilityTree(container)).toEqual(
      insertedAfter(READY_TREE, "button | Add default rate", RATE_FORM_TREE),
    );
    expect(readingOrder(screen.getByRole("form"))).toEqual(RATE_FORM_TEXT);
  });
});

/**
 * SC-2-05, K-28 — the screen's state words, character for character.
 *
 * Most of them are already pinned as literals by `CatalogScreen.test.tsx` (the failure states, the
 * counts, the empty catalogue). This covers the ones that were asserted only through the constant
 * or the function that produces them — which agrees with any rewording of that constant — so a
 * restyle that "tidied" a sentence in `dimensionLabels.ts` or `writeOutcome.ts` would have passed.
 */
describe("the catalogue screen's state words (SC-2-05, K-28)", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("names every empty dictionary, a withheld cost, an internal rate and an unmatched id in the same words as before", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const path = new URL(url).pathname;
      if (path === "/catalog/rates") {
        return {
          ok: true,
          status: 200,
          // Matches no dictionary entry at all: every dictionary below is empty.
          json: async () => ({ rates: [RATE_INTERNAL], total: 1 }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ entries: [] }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<CatalogScreen />);
    await screen.findByRole("table");

    for (const sentence of [
      "The roles dictionary is empty.",
      "The seniorities dictionary is empty.",
      "The locations dictionary is empty.",
      "The engagement types dictionary is empty.",
      "The vendors dictionary is empty.",
    ]) {
      expect(screen.getByText(sentence)).toBeVisible();
    }

    const row = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "Not in the roles dictionary",
      "Not in the seniorities dictionary",
      "Not in the locations dictionary",
      "Not in the engagement types dictionary",
      "Internal",
      "2026-09-01 – 2027-02-28",
      "260.00 PLN / hour",
      "Restricted",
      "Edit",
    ]);
    expect(screen.getByText("1 default rate")).toBeVisible();
  });

  it("states the three outcomes of a save in the same words as before", () => {
    // Imported here, compared to literals: the rendering of each is proven in
    // `CatalogWrite.test.tsx` through these same constants; what this adds is that the constants
    // still say what they said.
    expect(outcomes.RE_READING_AFTER_SAVE).toBe(
      "The change was accepted. Re-reading the catalogue…",
    );
    expect(outcomes.SAVED_AND_REREAD).toBe(
      "Saved. The catalogue below was re-read from the server; a rate window starting earlier " +
        "than the ones shown may be on a page this screen does not display.",
    );
    expect(outcomes.SAVED_BUT_NOT_REREAD).toBe(
      "Saved, but the catalogue could not be re-read afterwards. The rows below are from before " +
        "this change; reload the screen to see the catalogue as it is now.",
    );
  });
});
