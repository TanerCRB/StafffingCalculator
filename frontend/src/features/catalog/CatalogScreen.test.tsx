import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CALLER_ID_HEADER, REQUEST_TIMEOUT_MS } from "../../api/client";
import {
  CATALOG_DIMENSIONS,
  type CatalogDimension,
  type CatalogRate,
  type DimensionEntry,
} from "../../api/contracts/catalog";
import { OPEN_ENDED_PERIOD } from "../../lib/dates";
import { NOT_APPLICABLE } from "../../lib/money";
import { CatalogScreen } from "./CatalogScreen";
import { INTERNAL_RATE, RESTRICTED_COST_RATE } from "./catalogRows";
import { DIMENSION_LABELS, emptyDictionaryLabel, unknownEntryLabel } from "./dimensionLabels";

/**
 * SC-2-02, K-01..K-08, and SC-2-03, K-09..K-10 (the vendor column and the sixth read) and K-12 (the
 * screen names a page as a page once the backend started paginating, K-11). SC-2-02's own K-09 —
 * the screen is reachable from the running application — is in src/App.test.tsx, because it is a
 * claim about the application, not about this component. The two K-09s belong to different tasks;
 * nothing here renumbers them.
 *
 * Every test here stubs the six reads. That is the only input the screen has — which is itself
 * the subject of K-04 and of K-10 — so a fixture is a complete description of what the user sees.
 *
 * **SC-2-04 changed three things here and nothing else.** They are named so that a reader can tell
 * a fixture update from a weakened claim:
 *
 *   1. Every fixture carries `updated_at`. It is a required field of both response shapes now
 *      (ADR-0007's concurrency marker, carried on every read), and `client.ts` refuses a payload
 *      without one for the same reason it refuses a rate row without `vendor_id`. No assertion
 *      changed; the responses did.
 *   2. `COLUMN_COUNT` is 9, not 8: the rates table gained an "Actions" column at the **end**, so
 *      every column SC-2-02 and SC-2-03 placed keeps its index, and the claim the K-03 test makes —
 *      the cost-rate column does not disappear — is asserted unchanged.
 *   3. The test "renders the add-default-rate control as reachable and wired to nothing" is gone.
 *      It encoded SC-2-03's explicit out-of-scope statement, which SC-2-04 reverses by design
 *      (Issue #49, gate-1 decision G-3: accepted as intended, replaced rather than weakened). Its
 *      replacement is `CatalogWrite.test.tsx`, "the add-default-rate control opens a form that
 *      writes", which asserts the opposite and the reason for it.
 */

// --- Fixtures ----------------------------------------------------------------------------------
// The dictionaries carry two entries each, and the rate rows below are built so that no assertion
// can pass by accident: two rows differ in exactly one dimension, one row references an id that is
// in no dictionary, and the amounts are values a JS float cannot hold.

/** SC-2-04. ADR-0007's concurrency marker, as every read now carries it. Its value is never read by
 * this screen — it is handed back to the server on an edit and compared there — so one value for
 * every fixture is enough here; `CatalogWrite.test.tsx` is where it has to be per-row. */
const MARKER = "2026-09-20T09:00:00+00:00";

const ROLES: DimensionEntry[] = [
  { id: "a0000000-0000-0000-0000-000000000001", name: "Backend engineer", updated_at: MARKER },
  { id: "a0000000-0000-0000-0000-000000000002", name: "Project manager", updated_at: MARKER },
];
const SENIORITIES: DimensionEntry[] = [
  { id: "b0000000-0000-0000-0000-000000000001", name: "Senior", updated_at: MARKER },
  { id: "b0000000-0000-0000-0000-000000000002", name: "Junior", updated_at: MARKER },
];
const LOCATIONS: DimensionEntry[] = [
  { id: "c0000000-0000-0000-0000-000000000001", name: "Poland", updated_at: MARKER },
  { id: "c0000000-0000-0000-0000-000000000002", name: "Germany", updated_at: MARKER },
];
const ENGAGEMENT_TYPES: DimensionEntry[] = [
  { id: "d0000000-0000-0000-0000-000000000001", name: "Time & materials", updated_at: MARKER },
  { id: "d0000000-0000-0000-0000-000000000002", name: "Fixed price", updated_at: MARKER },
];
/** SC-2-03. The fifth dictionary, read like the other four. */
const VENDORS: DimensionEntry[] = [
  { id: "f0000000-0000-0000-0000-000000000001", name: "Contoso Sp. z o.o.", updated_at: MARKER },
  { id: "f0000000-0000-0000-0000-000000000002", name: "Northwind GmbH", updated_at: MARKER },
];

const FULL_DICTIONARIES: Readonly<Record<CatalogDimension, DimensionEntry[]>> = {
  roles: ROLES,
  seniorities: SENIORITIES,
  locations: LOCATIONS,
  "engagement-types": ENGAGEMENT_TYPES,
  vendors: VENDORS,
};

/**
 * A caller who may read personnel costs. Not reachable against the running backend today —
 * `PLACEHOLDER_PERMISSIONS` holds no `PERSONNEL_COSTS_READ`, so every real response has
 * `default_cost_rate: null` (Issue #39, "Risk to knowingly accept"). The branch is provable
 * only from a stubbed response, the same way SC-1-08's positive branch is.
 *
 * "100.005" and "60.005" are deliberate: `Number("100.005")` is 100.00499999999999…, so a float
 * path renders "100.00" where the backend's ROUND_HALF_UP gives "100.01".
 */
const RATE_SENIOR: CatalogRate = {
  id: "e0000000-0000-0000-0000-000000000001",
  role_id: ROLES[0].id,
  seniority_id: SENIORITIES[0].id,
  location_id: LOCATIONS[0].id,
  engagement_type_id: ENGAGEMENT_TYPES[0].id,
  // The organisation's own rate. Always in the body, never absent (SC-2-03 contract).
  vendor_id: null,
  default_cost_rate: "60.005",
  cost_rate_unit: "hour",
  default_selling_rate: "100.005",
  currency: "EUR",
  unit: "hour",
  effective_from: "2026-01-01",
  effective_to: "2026-06-30",
  updated_at: MARKER,
};

/** The same rate in every respect but one: the seniority. Two rows that differ by exactly one
 * dimension must not render the same four words (K-01). */
const RATE_JUNIOR: CatalogRate = {
  ...RATE_SENIOR,
  id: "e0000000-0000-0000-0000-000000000002",
  seniority_id: SENIORITIES[1].id,
};

/**
 * An open-ended window, a cost rate this caller was not given, another currency, and another unit.
 *
 * `unit: "day"` is not reachable against today's database either (a CHECK constraint pins it to
 * `hour`, Issue #39 out of scope 3) — which is exactly why it is here: it is the only way to show
 * that the unit on screen came from the response rather than from a constant in the component.
 * The amount has more significant digits than a JS number can hold.
 */
const RATE_OPEN_ENDED: CatalogRate = {
  id: "e0000000-0000-0000-0000-000000000003",
  role_id: ROLES[1].id,
  seniority_id: SENIORITIES[0].id,
  location_id: LOCATIONS[1].id,
  engagement_type_id: ENGAGEMENT_TYPES[1].id,
  vendor_id: null,
  default_cost_rate: null,
  cost_rate_unit: null,
  default_selling_rate: "9007199254740993.004",
  currency: "PLN",
  unit: "day",
  effective_from: "2025-07-01",
  effective_to: null,
  updated_at: MARKER,
};

/**
 * A rate whose role id matches no entry of the roles dictionary — reachable without any read
 * failing, because the six reads are six requests and not one transaction (gate-1 decision 8).
 * It also carries no cost rate and no end date, so one row holds three different kinds of absence
 * at once and they can be compared with each other.
 */
const RATE_UNKNOWN_ROLE: CatalogRate = {
  ...RATE_OPEN_ENDED,
  id: "e0000000-0000-0000-0000-000000000004",
  role_id: "ffffffff-ffff-ffff-ffff-ffffffffffff",
};

/** A rate the response carried with a cost rate omitted rather than nulled — `response_shaping`
 * removes the key, and an `undefined` that fell through to a cell would render nothing at all. */
const RATE_COST_KEY_ABSENT: CatalogRate = (() => {
  const rate: CatalogRate = { ...RATE_SENIOR, id: "e0000000-0000-0000-0000-000000000005" };
  delete rate.default_cost_rate;
  delete rate.cost_rate_unit;
  return rate;
})();

/**
 * SC-2-03. A rate that belongs to a subcontractor. Identical to `RATE_SENIOR` in every field but
 * `vendor_id` — the same tuple, the same window, the same money — because that is the only way to
 * show that what changes on screen is the vendor and nothing else (K-09). On the backend side this
 * pair is exactly the coexistence K-01 proves the database now allows.
 */
const RATE_FROM_VENDOR: CatalogRate = {
  ...RATE_SENIOR,
  id: "e0000000-0000-0000-0000-000000000008",
  vendor_id: VENDORS[0].id,
};

/**
 * A rate naming a vendor that no entry of the dictionary matched — reachable without any read
 * failing, like `RATE_UNKNOWN_ROLE`. "The id on this row matched nothing" and "this rate is ours"
 * are two different facts, and the screen owes them two different words.
 */
const RATE_UNKNOWN_VENDOR: CatalogRate = {
  ...RATE_SENIOR,
  id: "e0000000-0000-0000-0000-000000000009",
  vendor_id: "ffffffff-ffff-ffff-ffff-fffffffffffe",
};

// --- The six reads, stubbed -------------------------------------------------------------------

const RATES_PATH = "/catalog/rates";

function dimensionPath(dimension: CatalogDimension): string {
  return `/catalog/dimensions/${dimension}`;
}

const CATALOG_PATHS: readonly string[] = [RATES_PATH, ...CATALOG_DIMENSIONS.map(dimensionPath)];

function pathOf(url: string): string {
  return new URL(url).pathname;
}

interface StubOptions {
  readonly rates?: CatalogRate[];
  readonly dictionaries?: Partial<Record<CatalogDimension, DimensionEntry[]>>;
  /** Path → HTTP status for the reads that must fail. Everything else answers `200`. */
  readonly failures?: Readonly<Record<string, number>>;
  /** `total` on the `/catalog/rates` response (K-11). Defaults to `rates.length` — a complete
   * catalogue — so every existing test stays a claim about a page that is the whole thing, and only
   * a test that says otherwise (K-12) exercises the truncated case. */
  readonly total?: number;
}

function stubCatalog({ rates, dictionaries, failures = {}, total }: StubOptions = {}) {
  const body = rates ?? [RATE_SENIOR, RATE_JUNIOR];
  const dicts = { ...FULL_DICTIONARIES, ...dictionaries };
  const rateCount = total ?? body.length;

  const fetchMock = vi.fn(async (url: string) => {
    const path = pathOf(url);
    const failure = failures[path];
    if (failure !== undefined) {
      return { ok: false, status: failure };
    }
    if (path === RATES_PATH) {
      return { ok: true, status: 200, json: async () => ({ rates: body, total: rateCount }) };
    }
    const dimension = CATALOG_DIMENSIONS.find((candidate) => dimensionPath(candidate) === path);
    if (dimension !== undefined) {
      return { ok: true, status: 200, json: async () => ({ entries: dicts[dimension] }) };
    }
    throw new Error(`The screen asked for a path it has no business reading: ${path}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The mock is declared with one parameter, because that is all the stub reads; `client.ts` always
 * calls `fetch` with two, so the recorded calls are widened here rather than in the stub. */
function recordedCalls(fetchMock: ReturnType<typeof stubCatalog>): [string, RequestInit][] {
  return fetchMock.mock.calls as unknown as [string, RequestInit][];
}

function requestedPaths(fetchMock: ReturnType<typeof stubCatalog>): string[] {
  return recordedCalls(fetchMock).map(([url]) => pathOf(url));
}

// --- Reading the rendered table ----------------------------------------------------------------

/** Body rows only — the header row is not a rate. */
async function rateRows(): Promise<HTMLElement[]> {
  const table = await screen.findByRole("table");
  return within(table).getAllByRole("row").slice(1);
}

const ROLE_CELL = 0;
const SENIORITY_CELL = 1;
const LOCATION_CELL = 2;
const ENGAGEMENT_CELL = 3;
/** SC-2-03: the fifth dimension column, in the order `CATALOG_DIMENSIONS` states. */
const VENDOR_CELL = 4;
const PERIOD_CELL = 5;
const SELLING_CELL = 6;
const COST_CELL = 7;
/** SC-2-04: the row's own actions, appended after the cost rate so no existing index moved. */
const ACTIONS_CELL = 8;
/** Four dimensions, the vendor, the period, the two rates and the actions. */
const COLUMN_COUNT = 9;

function cellsOf(row: HTMLElement): HTMLElement[] {
  return within(row).getAllByRole("cell");
}

function textOf(row: HTMLElement, index: number): string {
  return cellsOf(row)[index].textContent ?? "";
}

function rowStartingWith(rows: HTMLElement[], roleName: string): HTMLElement {
  const row = rows.find((candidate) => textOf(candidate, ROLE_CELL) === roleName);
  if (!row) {
    throw new Error(`No rate row whose role cell reads "${roleName}"`);
  }
  return row;
}

/** Any UUID at all. A rate row carries nothing but identifiers for its four dimensions, so one
 * reaching the screen means the join did not happen — and it would still look like a table. */
const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

function screenText(): string {
  return document.body.textContent ?? "";
}

describe("CatalogScreen", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  // --- K-01 ------------------------------------------------------------------------------------

  it("renders one row per rate returned by the API, naming role, seniority, location and engagement type as text, not as an identifier", async () => {
    stubCatalog({ rates: [RATE_SENIOR, RATE_JUNIOR, RATE_OPEN_ENDED] });

    render(<CatalogScreen />);

    const rows = await rateRows();
    expect(rows).toHaveLength(3);

    const senior = rowStartingWith(rows, "Backend engineer");
    expect(textOf(senior, SENIORITY_CELL)).toBe("Senior");
    expect(textOf(senior, LOCATION_CELL)).toBe("Poland");
    expect(textOf(senior, ENGAGEMENT_CELL)).toBe("Time & materials");

    const manager = rowStartingWith(rows, "Project manager");
    expect(textOf(manager, SENIORITY_CELL)).toBe("Senior");
    expect(textOf(manager, LOCATION_CELL)).toBe("Germany");
    expect(textOf(manager, ENGAGEMENT_CELL)).toBe("Fixed price");

    // Two rows differing in exactly one dimension must differ on screen in exactly that cell. A
    // join that resolved every id to the first entry of its dictionary would pass both blocks
    // above and fail here.
    const junior = rows.find((row) => textOf(row, SENIORITY_CELL) === "Junior");
    expect(junior).toBeDefined();
    expect(textOf(junior as HTMLElement, ROLE_CELL)).toBe(textOf(senior, ROLE_CELL));
    expect(textOf(junior as HTMLElement, LOCATION_CELL)).toBe(textOf(senior, LOCATION_CELL));
    expect(textOf(junior as HTMLElement, SENIORITY_CELL)).not.toBe(textOf(senior, SENIORITY_CELL));

    // The column headers name the dimensions too, so the four words are readable as a row.
    for (const dimension of CATALOG_DIMENSIONS) {
      expect(
        screen.getAllByRole("columnheader", { name: DIMENSION_LABELS[dimension].column }).length,
      ).toBeGreaterThan(0);
    }

    expect(screenText()).not.toMatch(UUID_PATTERN);
  });

  it("names an identifier that matched no dictionary entry, instead of leaving the cell blank or dropping the rate", async () => {
    stubCatalog({ rates: [RATE_SENIOR, RATE_UNKNOWN_ROLE] });

    render(<CatalogScreen />);

    const rows = await rateRows();
    // The rate is still there. A row silently skipped because one of its ids could not be resolved
    // would make the rate itself vanish, and the table would look complete.
    expect(rows).toHaveLength(2);

    const unresolved = rowStartingWith(rows, unknownEntryLabel("roles"));
    // Only the unresolved dimension is named as absent; the other three are still named.
    expect(textOf(unresolved, SENIORITY_CELL)).toBe("Senior");
    expect(textOf(unresolved, LOCATION_CELL)).toBe("Germany");
    expect(textOf(unresolved, ENGAGEMENT_CELL)).toBe("Fixed price");

    // Never a blank cell, anywhere in the table.
    for (const row of rows) {
      for (const cell of cellsOf(row)) {
        expect(cell.textContent?.trim()).not.toBe("");
      }
    }
    expect(screenText()).not.toMatch(UUID_PATTERN);
  });

  // --- K-02 ------------------------------------------------------------------------------------

  it("renders the effective period of every rate, and an open-ended window as a named absence of an end date, never as a date", async () => {
    // The contrast lives in one response: a closed window and an open one, side by side.
    stubCatalog({ rates: [RATE_SENIOR, RATE_OPEN_ENDED] });

    render(<CatalogScreen />);

    const rows = await rateRows();
    expect(textOf(rowStartingWith(rows, "Backend engineer"), PERIOD_CELL)).toBe(
      "2026-01-01 – 2026-06-30",
    );
    expect(textOf(rowStartingWith(rows, "Project manager"), PERIOD_CELL)).toBe(
      `2025-07-01 – ${OPEN_ENDED_PERIOD}`,
    );

    // No sentinel: an open-ended window is not "9999-12-31", not "31/12/9999" and not any other
    // far-future date a reader would have to recognise (ADR-0008, point 2).
    expect(screenText()).not.toMatch(/9999|2999|2100-/);
    // And not a blank half of a range either.
    expect(screenText()).not.toContain("2025-07-01 –  ");
  });

  // --- K-03 ------------------------------------------------------------------------------------

  it("renders a named refusal in place of a cost rate the API did not carry, and never a zero, never a dash shared with other empty values, never a computed figure", async () => {
    // One row carries a cost rate, two do not — one with the field nulled, one with the key
    // removed, which is what `response_shaping` actually does.
    stubCatalog({ rates: [RATE_SENIOR, RATE_UNKNOWN_ROLE, RATE_COST_KEY_ABSENT] });

    render(<CatalogScreen />);

    const rows = await rateRows();
    const withCost = rows.find((row) => textOf(row, COST_CELL) !== RESTRICTED_COST_RATE);
    expect(withCost).toBeDefined();
    expect(textOf(withCost as HTMLElement, COST_CELL)).toBe("60.01 EUR / hour");

    const refused = rows.filter((row) => row !== withCost);
    expect(refused).toHaveLength(2);
    for (const row of refused) {
      const cell = cellsOf(row)[COST_CELL];
      expect(cell.textContent).toBe(RESTRICTED_COST_RATE);
      // Never a zero, never a computed figure: there is no digit in the cell at all, so neither
      // `?? "0"` nor `?? "0.00 EUR / hour"` nor an amount derived from the selling rate survives.
      expect(cell.textContent).not.toMatch(/\d/);
      // The refusal is a word, not a glyph — and it is visible, not hidden from the layout.
      expect(cell).toBeVisible();
    }

    // Not a mark shared with any other kind of absence. The unresolved row shows three different
    // gaps at once — an id no dictionary matched, an open-ended window, a withheld cost — and a
    // reader must be able to tell them apart without knowing which column is which.
    const unresolved = rowStartingWith(rows, unknownEntryLabel("roles"));
    const absences = [
      textOf(unresolved, ROLE_CELL),
      textOf(unresolved, PERIOD_CELL),
      textOf(unresolved, COST_CELL),
    ];
    expect(new Set(absences).size).toBe(3);
    for (const absence of absences) {
      for (const other of absences) {
        if (absence === other) continue;
        expect(absence).not.toContain(other);
      }
    }
  });

  it("names the withheld cost rate in a word of its own — never a glyph, and never a label this product already spends on a different absence", async () => {
    // QA: the test above reads the refusal through `RESTRICTED_COST_RATE`, imported from the
    // component it is checking, so it says "the cell shows whatever the component decided to show".
    // Two mutations proved that hole: `RESTRICTED_COST_RATE = "—"` and
    // `RESTRICTED_COST_RATE = "Not applicable"` both left all 68 tests green — and the second is
    // the literal `lib/money.ts` already exports as `NOT_APPLICABLE`, for a ratio whose denominator
    // is zero. "You may not see this cost" would then read exactly like "this number has no
    // meaning", which is the conflation K-03 forbids in the same sentence as the zero and the dash.
    //
    // Nothing below names the component's own constant. The refusal is read off the screen and
    // held against the other absences this product ships.
    const carried: CatalogRate = { ...RATE_SENIOR, id: "e0000000-0000-0000-0000-000000000006" };
    // One changed field, and it is the field under test. Everything else — role, seniority,
    // location, engagement type, window, selling rate, currency, unit — is the same object.
    const withheld: CatalogRate = {
      ...carried,
      id: "e0000000-0000-0000-0000-000000000007",
      default_cost_rate: null,
      cost_rate_unit: null,
    };
    stubCatalog({ rates: [carried, withheld] });

    render(<CatalogScreen />);

    const [carriedRow, withheldRow] = await rateRows();
    // The reversed result: the row that carried a cost states it.
    expect(textOf(carriedRow, COST_CELL)).toBe("60.01 EUR / hour");

    // And the two rows differ on screen in exactly the one cell they differ in in the response.
    for (const cell of [
      ROLE_CELL,
      SENIORITY_CELL,
      LOCATION_CELL,
      ENGAGEMENT_CELL,
      VENDOR_CELL,
      PERIOD_CELL,
      SELLING_CELL,
    ]) {
      expect(textOf(withheldRow, cell)).toBe(textOf(carriedRow, cell));
    }
    const refusal = textOf(withheldRow, COST_CELL);
    expect(refusal).not.toBe(textOf(carriedRow, COST_CELL));

    // A word, in letters. A dash, an asterisk, a bracketed glyph or a blank cannot satisfy this,
    // and neither can anything carrying a digit.
    expect(refusal).toMatch(/^[A-Za-z][A-Za-z ]*$/);

    // And not a label already in use elsewhere for an absence that is not this one. Each is taken
    // from the module that owns it, so this check follows them if they are ever reworded.
    const otherAbsences = [
      NOT_APPLICABLE,
      OPEN_ENDED_PERIOD,
      ...CATALOG_DIMENSIONS.map(unknownEntryLabel),
      ...CATALOG_DIMENSIONS.map(emptyDictionaryLabel),
    ];
    for (const other of otherAbsences) {
      expect(refusal.toLowerCase()).not.toBe(other.toLowerCase());
    }
  });

  it("keeps the cost-rate column in place when the API carried no cost rate on any row", async () => {
    // What every browser against the running backend sees today: no `PERSONNEL_COSTS_READ`, so
    // every row is a refusal. Dropping the column here would make "you may not see this cost" and
    // "the catalogue has no costs" the same screen (gate-1 decision 2).
    stubCatalog({ rates: [RATE_OPEN_ENDED, RATE_UNKNOWN_ROLE] });

    render(<CatalogScreen />);

    const rows = await rateRows();
    expect(rows.length).toBeGreaterThan(0);

    const header = screen.getByRole("columnheader", { name: "Default cost rate" });
    expect(header).toBeVisible();
    // Nine columns, not eight: the four dimensions, the vendor (SC-2-03), the period, both rates,
    // and the row's actions (SC-2-04). The claim is unchanged — the cost-rate column is still there
    // when every row's cost was withheld — and the count is still exact, so a column quietly
    // dropped still fails here.
    expect(screen.getAllByRole("columnheader")).toHaveLength(COLUMN_COUNT);

    for (const row of rows) {
      expect(cellsOf(row)).toHaveLength(COLUMN_COUNT);
      expect(textOf(row, COST_CELL)).toBe(RESTRICTED_COST_RATE);
      // The selling rate is not behind the personnel-cost gate and is still there — so "no cost
      // column" could not be excused as "no amounts at all".
      expect(textOf(row, SELLING_CELL)).not.toBe(RESTRICTED_COST_RATE);
      // SC-2-04: the actions column is the last one, and the withheld cost is still the one before
      // it. A control appended in the middle would move the cost rate under another header while
      // every assertion about its *content* kept passing.
      expect(textOf(row, ACTIONS_CELL)).toBe("Edit");
    }
  });

  // --- K-04 ------------------------------------------------------------------------------------

  it("takes no cost-visibility decision of its own", async () => {
    const fetchMock = stubCatalog({ rates: [RATE_SENIOR] });

    render(<CatalogScreen />);
    const first = await rateRows();
    // The authorised branch renders the amount. Nothing on the client suppressed it, because there
    // is nothing on the client that could: the body is the whole input.
    expect(textOf(first[0], COST_CELL)).toBe("60.01 EUR / hour");
    const authorisedHtml = (await screen.findByRole("table")).outerHTML;

    // Exactly six reads, on exactly the six catalogue paths (five since SC-2-03 added the vendor
    // dictionary). No preflight, no `/me`, no permission or role lookup — nothing the screen could
    // have based a decision on (AC-06).
    const paths = requestedPaths(fetchMock);
    expect(paths).toHaveLength(6);
    expect([...paths].sort()).toEqual([...CATALOG_PATHS].sort());
    expect(paths.some((path) => /permission|identity|whoami|\/me\b|auth|role-assignment/i.test(path)))
      .toBe(false);
    for (const [, init] of recordedCalls(fetchMock)) {
      expect(init.method === undefined || init.method === "GET").toBe(true);
      expect(init.body).toBeUndefined();
    }

    // The same body renders the same screen. Re-mounted from scratch against an identical
    // response, byte for byte.
    cleanup();
    vi.unstubAllGlobals();
    stubCatalog({ rates: [RATE_SENIOR] });
    render(<CatalogScreen />);
    await rateRows();
    expect((await screen.findByRole("table")).outerHTML).toBe(authorisedHtml);

    // And a body that differs only in `default_cost_rate` renders differently — so the equality
    // above is not the equality of a screen that ignores the field.
    cleanup();
    vi.unstubAllGlobals();
    stubCatalog({ rates: [{ ...RATE_SENIOR, default_cost_rate: null, cost_rate_unit: null }] });
    render(<CatalogScreen />);
    const refusedRows = await rateRows();
    expect(textOf(refusedRows[0], COST_CELL)).toBe(RESTRICTED_COST_RATE);
    expect((await screen.findByRole("table")).outerHTML).not.toBe(authorisedHtml);
  });

  // --- SC-2-03, K-09 ---------------------------------------------------------------------------

  it('renders the vendor name for a vendor rate and a named "Internal" state for a rate with no vendor', async () => {
    // Three rows, one tuple, one window, one set of amounts. They differ in `vendor_id` and in
    // nothing else, so every difference on screen is a difference the response actually carried.
    const internal: CatalogRate = { ...RATE_SENIOR, id: "e0000000-0000-0000-0000-00000000000a" };
    stubCatalog({ rates: [internal, RATE_FROM_VENDOR, RATE_UNKNOWN_VENDOR] });

    render(<CatalogScreen />);

    const rows = await rateRows();
    expect(rows).toHaveLength(3);
    const [internalRow, vendorRow, unknownVendorRow] = rows;

    // The vendor rate names its vendor, as a name — joined here from the sixth read, exactly the
    // way the other four dimensions are joined.
    expect(textOf(vendorRow, VENDOR_CELL)).toBe("Contoso Sp. z o.o.");
    expect(screen.getByRole("columnheader", { name: "Vendor" })).toBeVisible();

    // The rate with no vendor says which rate it is. Read off the screen first, then held against
    // everything it must not be.
    const internalText = textOf(internalRow, VENDOR_CELL);
    expect(internalText.trim()).not.toBe("");
    // A word, in letters: `?? ""`, a dash, an asterisk or a bracketed glyph all fail here.
    expect(internalText).toMatch(/^[A-Za-z][A-Za-z ]*$/);
    expect(internalText).toBe(INTERNAL_RATE);

    // Not the label for an id that matched nothing — that row is in the same table, two rows down,
    // and it reads differently.
    expect(textOf(unknownVendorRow, VENDOR_CELL)).toBe(unknownEntryLabel("vendors"));
    expect(internalText).not.toBe(textOf(unknownVendorRow, VENDOR_CELL));

    // And not a label this product already spends on some other kind of absence. Each one is taken
    // from the module that owns it, so this check follows them if they are ever reworded. Neither
    // direction of containment either: "Internal" must not be a fragment of one of them, and none
    // of them a fragment of it.
    const otherAbsences = [
      RESTRICTED_COST_RATE,
      OPEN_ENDED_PERIOD,
      NOT_APPLICABLE,
      ...CATALOG_DIMENSIONS.map(unknownEntryLabel),
      ...CATALOG_DIMENSIONS.map(emptyDictionaryLabel),
    ];
    for (const other of otherAbsences) {
      expect(internalText.toLowerCase()).not.toBe(other.toLowerCase());
      expect(internalText.toLowerCase()).not.toContain(other.toLowerCase());
      expect(other.toLowerCase()).not.toContain(internalText.toLowerCase());
    }

    // The three rows differ on screen in exactly the one cell they differ in in the response.
    for (const cell of [
      ROLE_CELL,
      SENIORITY_CELL,
      LOCATION_CELL,
      ENGAGEMENT_CELL,
      PERIOD_CELL,
      SELLING_CELL,
      COST_CELL,
    ]) {
      expect(textOf(vendorRow, cell)).toBe(textOf(internalRow, cell));
      expect(textOf(unknownVendorRow, cell)).toBe(textOf(internalRow, cell));
    }
    expect(new Set(rows.map((row) => textOf(row, VENDOR_CELL))).size).toBe(3);

    // No cell anywhere is blank, and no identifier reached the screen — the vendor column included.
    for (const row of rows) {
      for (const cell of cellsOf(row)) {
        expect(cell.textContent?.trim()).not.toBe("");
      }
    }
    expect(screenText()).not.toMatch(UUID_PATTERN);

    // The vendor dictionary is listed like the other four, so a reader can see which vendors the
    // catalogue knows — not only the ones some rate happens to reference.
    const vendors = within(screen.getByRole("region", { name: "Vendors" }));
    expect(vendors.getByText("Contoso Sp. z o.o.")).toBeVisible();
    expect(vendors.getByText("Northwind GmbH")).toBeVisible();
  });

  // --- SC-2-03, K-10 ---------------------------------------------------------------------------

  it("takes no vendor-visibility decision of its own", async () => {
    const fetchMock = stubCatalog({ rates: [RATE_SENIOR, RATE_FROM_VENDOR] });

    render(<CatalogScreen />);
    const rows = await rateRows();
    // Every rate the response carried is on screen, the vendor's included. Nothing here decides
    // that a vendor's price list is not for this caller: `CATALOG_READ` covers every vendor
    // (ADR-0005, addendum 2026-09-21, point 2), and the screen has nothing it could decide with.
    expect(rows).toHaveLength(2);
    expect(textOf(rows[1], VENDOR_CELL)).toBe("Contoso Sp. z o.o.");
    const html = (await screen.findByRole("table")).outerHTML;

    // Exactly six reads, on exactly the six catalogue paths. No preflight, no `/me`, no permission,
    // role or vendor-access lookup — nothing the screen could have based a decision on.
    const paths = requestedPaths(fetchMock);
    expect(paths).toHaveLength(6);
    expect([...paths].sort()).toEqual([...CATALOG_PATHS].sort());
    expect(paths).toContain("/catalog/dimensions/vendors");
    expect(
      paths.some((path) =>
        /permission|identity|whoami|\/me\b|auth|role-assignment|vendor-access/i.test(path),
      ),
    ).toBe(false);
    for (const [, init] of recordedCalls(fetchMock)) {
      expect(init.method === undefined || init.method === "GET").toBe(true);
      expect(init.body).toBeUndefined();
    }

    // The same body renders the same screen, byte for byte, from a fresh mount.
    cleanup();
    vi.unstubAllGlobals();
    stubCatalog({ rates: [RATE_SENIOR, RATE_FROM_VENDOR] });
    render(<CatalogScreen />);
    await rateRows();
    expect((await screen.findByRole("table")).outerHTML).toBe(html);

    // A body differing only in `vendor_id` renders differently, so the equality above is not the
    // equality of a screen that ignores the field.
    cleanup();
    vi.unstubAllGlobals();
    stubCatalog({ rates: [RATE_SENIOR, { ...RATE_FROM_VENDOR, vendor_id: VENDORS[1].id }] });
    render(<CatalogScreen />);
    const changed = await rateRows();
    expect(textOf(changed[1], VENDOR_CELL)).toBe("Northwind GmbH");
    expect((await screen.findByRole("table")).outerHTML).not.toBe(html);

    // And the vendor dictionary is read whatever the rates turn out to be — including when not one
    // of them names a vendor, and when there are no rates at all. A read conditioned on any state
    // of this client would be the screen deciding whether vendors are worth asking about.
    for (const rates of [[RATE_SENIOR], []]) {
      cleanup();
      vi.unstubAllGlobals();
      const mock = stubCatalog({ rates });
      render(<CatalogScreen />);
      await screen.findByRole("region", { name: "Vendors" });

      expect(requestedPaths(mock)).toContain("/catalog/dimensions/vendors");
      expect(requestedPaths(mock)).toHaveLength(6);
    }
  });

  // --- SC-2-03, K-12 -----------------------------------------------------------------------------

  it("names a page as a page, never as the whole catalogue, when the backend's total exceeds the rows it sent", async () => {
    // Contrast, in one test: the same two rows, once as everything the catalogue holds and once as
    // page one of more. The only thing that differs between the two stubs is `total` — never the
    // rows — so any difference on screen is a difference the screen drew from `total` alone.
    stubCatalog({ rates: [RATE_SENIOR, RATE_JUNIOR], total: 2 });

    render(<CatalogScreen />);
    await rateRows();

    const completeLabel = screen.getByText("2 default rates");
    expect(completeLabel).toBeVisible();
    expect(completeLabel.className).not.toContain("catalog__count--truncated");
    expect(screenText()).not.toMatch(/showing/i);

    // Re-mounted from scratch with a `total` larger than `rates.length` — the mutation this test
    // exists to kill is a screen that ignores `total` and always renders `rates.length` as if it
    // were complete, which would print "2 default rates" here too, exactly as above.
    cleanup();
    vi.unstubAllGlobals();
    stubCatalog({ rates: [RATE_SENIOR, RATE_JUNIOR], total: 2347 });
    render(<CatalogScreen />);
    await rateRows();

    // Not the complete-catalogue literal, with or without the true count spliced in — either would
    // claim the two rows on screen are all there is, or claim 2347 rows are on screen when there
    // are two.
    expect(screen.queryByText("2 default rates")).not.toBeInTheDocument();
    expect(screen.queryByText("2347 default rates")).not.toBeInTheDocument();

    // A named, distinct sentence that states both numbers — what was shown and how large the
    // catalogue actually is — read off the screen first, then held against the complete-catalogue
    // wording so neither is a fragment of the other.
    const truncatedLabel = screen.getByText("Showing first 2 of 2347 default rates");
    expect(truncatedLabel).toBeVisible();
    expect(truncatedLabel.className).toContain("catalog__count--truncated");
    expect(truncatedLabel.textContent).not.toBe(completeLabel.textContent);
    expect(truncatedLabel.textContent).not.toContain(completeLabel.textContent);

    // The table itself is unaffected: this criterion is about naming the count, not about hiding or
    // adding rows, and it builds no pagination control of its own (no "load more", no page number).
    const rows = await rateRows();
    expect(rows).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /load more|next page/i })).not.toBeInTheDocument();
  });

  // --- Reviewer R-03 -----------------------------------------------------------------------------

  it("tells a page that carried no rows apart from a catalogue that holds none, instead of calling both an empty catalogue", async () => {
    // Contrast, in one test, and the only thing that differs between the two stubs is `total` —
    // the rate list is empty in both. `{rates: [], total: N>0}` is a `200` the backend sends for an
    // `offset` past the end of a non-empty result set (K-11: `offset` and `on_date` are parameters
    // of `GET /catalog/rates`), and it is the error that renders correctly — nothing throws, the
    // screen reads as a calm answer, and it tells a project manager the organisation has no price
    // list at all.
    stubCatalog({ rates: [], total: 0 });
    render(<CatalogScreen />);

    const emptyCatalogue = await within(
      await screen.findByRole("region", { name: "Default rates" }),
    ).findByRole("status");
    const emptyCatalogueText = emptyCatalogue.textContent ?? "";
    expect(emptyCatalogue).toBeVisible();
    expect(emptyCatalogueText).toBe("The catalogue holds no default rates.");
    // A statement about the catalogue, carrying no count: nothing here for the page-level case to
    // be mistaken for.
    expect(emptyCatalogueText).not.toMatch(/\d|showing/i);

    // Re-mounted from scratch, same empty list, a `total` that contradicts it.
    cleanup();
    vi.unstubAllGlobals();
    stubCatalog({ rates: [], total: 2347 });
    render(<CatalogScreen />);

    const ratesPanel = within(await screen.findByRole("region", { name: "Default rates" }));
    // The mutation this kills: an empty branch that ignores `total` and states the catalogue is
    // empty whenever the page is — which renders exactly the sentence above, here.
    expect(screen.queryByText("The catalogue holds no default rates.")).toBeNull();

    // Something is still said — a screen that fell silent on this branch would leave the reader
    // with a heading, a dead button and nothing else.
    const emptyPage = await ratesPanel.findByRole("status");
    const emptyPageText = emptyPage.textContent ?? "";
    expect(emptyPage).toBeVisible();
    // Read off the screen, then held against the catalogue-level sentence: two different facts,
    // two different sentences, and neither one a fragment of the other.
    expect(emptyPageText).not.toBe(emptyCatalogueText);
    expect(emptyPageText).not.toContain(emptyCatalogueText);
    expect(emptyCatalogueText).not.toContain(emptyPageText);
    // In words, and it states how large the catalogue actually is — the same two facts
    // `truncatedRateCountLabel` states for a page that did carry rows (K-12).
    expect(emptyPageText).toMatch(/[A-Za-z]/);
    expect(emptyPageText).toContain("2347");
    // Not the complete-count literal with a zero in it either: "0 default rates" would say the
    // catalogue holds none, in the wording reserved for a count that is complete (K-07).
    expect(ratesPanel.queryByText("0 default rates")).toBeNull();
    expect(ratesPanel.queryByText("2347 default rates")).toBeNull();
    // Set apart by more than the wording, the way the truncated count is (NF-08).
    expect(emptyPage.className).not.toBe(emptyCatalogue.className);

    // No table and no pager: this branch names the state, it does not invent a control this screen
    // does not have (K-12).
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryAllByRole("row")).toHaveLength(0);
    expect(
      screen.queryByRole("button", { name: /load more|next page|previous page/i }),
    ).not.toBeInTheDocument();

    // The five dictionaries were read and are still shown, exactly as on the truly empty catalogue
    // (K-07): an empty page of rates says nothing about them.
    for (const dimension of CATALOG_DIMENSIONS) {
      expect(screen.getByRole("region", { name: DIMENSION_LABELS[dimension].section })).toBeVisible();
    }
  });

  // --- K-05 ------------------------------------------------------------------------------------

  it("renders a rate amount from the decimal string the API sent, in the currency and unit the API sent, never through a JavaScript number and never at a fixed scale", async () => {
    stubCatalog({ rates: [RATE_SENIOR, RATE_OPEN_ENDED] });

    render(<CatalogScreen />);

    const rows = await rateRows();
    const senior = rowStartingWith(rows, "Backend engineer");
    const manager = rowStartingWith(rows, "Project manager");

    // "100.005" through Number() is 100.00499999999999…, so a float path renders "100.00" here.
    expect(textOf(senior, SELLING_CELL)).toBe("100.01 EUR / hour");
    expect(textOf(senior, COST_CELL)).toBe("60.01 EUR / hour");
    expect(screenText()).not.toContain("100.00 EUR");
    expect(screenText()).not.toContain("60.00 EUR");
    // Not the raw string either: the amount was rounded for display, not passed through.
    expect(screenText()).not.toContain("100.005");

    // More significant digits than a JS number can hold: a float path corrupts the last ones.
    expect(textOf(manager, SELLING_CELL)).toBe("9007199254740993.00 PLN / day");
    expect(screenText()).not.toContain("9007199254740992");

    // Currency and unit come from the row, not from a constant: two rows, two currencies, two
    // units. A component that hardcoded "EUR" or "/ hour" satisfies the first row and fails here.
    expect(textOf(senior, SELLING_CELL)).toContain("EUR");
    expect(textOf(manager, SELLING_CELL)).toContain("PLN");
    expect(textOf(senior, SELLING_CELL)).toContain("/ hour");
    expect(textOf(manager, SELLING_CELL)).toContain("/ day");
    // No currency symbol standing in for a code — "$" and "kr" are each shared by several
    // currencies, so two different rows would read identically.
    expect(screenText()).not.toMatch(/[$€£¥]/);
  });

  // --- K-06 ------------------------------------------------------------------------------------

  it("lists the entries of each dimension dictionary, and names a dictionary the API returned empty instead of dropping its section", async () => {
    stubCatalog({ rates: [RATE_SENIOR], dictionaries: { locations: [] } });

    render(<CatalogScreen />);
    await rateRows();

    // Five sections from five responses, each one named.
    for (const dimension of CATALOG_DIMENSIONS) {
      expect(
        screen.getByRole("region", { name: DIMENSION_LABELS[dimension].section }),
      ).toBeVisible();
    }

    const roles = within(screen.getByRole("region", { name: "Roles" }));
    expect(roles.getByText("Backend engineer")).toBeVisible();
    expect(roles.getByText("Project manager")).toBeVisible();

    const engagements = within(screen.getByRole("region", { name: "Engagement types" }));
    expect(engagements.getByText("Time & materials")).toBeVisible();
    expect(engagements.getByText("Fixed price")).toBeVisible();

    // The empty dictionary keeps its heading and says it is empty. A section that disappeared
    // would be indistinguishable from a dimension that does not exist.
    const locations = screen.getByRole("region", { name: "Locations" });
    expect(within(locations).getByText(emptyDictionaryLabel("locations"))).toBeVisible();
    expect(within(locations).queryAllByRole("listitem")).toHaveLength(0);
    expect(within(locations).queryByText("Poland")).toBeNull();

    // The rate that referenced a location says so in its cell, rather than showing nothing — the
    // two mechanisms agree about the same missing dictionary.
    const rows = await rateRows();
    expect(textOf(rows[0], LOCATION_CELL)).toBe(unknownEntryLabel("locations"));
  });

  // --- K-07 ------------------------------------------------------------------------------------

  it("states how many rates the response carried, and states an empty catalogue instead of rendering a table with no rows", async () => {
    stubCatalog({ rates: [RATE_SENIOR, RATE_JUNIOR, RATE_OPEN_ENDED] });
    render(<CatalogScreen />);

    expect(await screen.findByText("3 default rates")).toBeVisible();
    expect(await rateRows()).toHaveLength(3);

    // A different response says a different number, so the sentence is not a literal — and it is
    // singular for one, so it is not a template that only ever reads well in the plural.
    cleanup();
    vi.unstubAllGlobals();
    stubCatalog({ rates: [RATE_SENIOR] });
    render(<CatalogScreen />);

    expect(await screen.findByText("1 default rate")).toBeVisible();
    expect(await rateRows()).toHaveLength(1);
    expect(screen.queryByText("3 default rates")).toBeNull();

    // An empty catalogue is a statement, not a table with nothing in it. "0 default rates" over an
    // empty grid leaves the reader to work out whether anything was asked for at all.
    cleanup();
    vi.unstubAllGlobals();
    stubCatalog({ rates: [] });
    render(<CatalogScreen />);

    expect(await screen.findByText("The catalogue holds no default rates.")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryAllByRole("row")).toHaveLength(0);
    expect(screen.queryByText(/default rates$/)).toBeNull();
    // The dictionaries were still read and are still shown: an empty rate list says nothing about
    // them, and the five sections are the only statement that the dimensions exist.
    for (const dimension of CATALOG_DIMENSIONS) {
      expect(screen.getByRole("region", { name: DIMENSION_LABELS[dimension].section })).toBeVisible();
    }
  });

  // --- K-08 ------------------------------------------------------------------------------------

  it("renders a stated failure, not a loading state, when a catalogue read is denied, fails or times out", async () => {
    stubCatalog({ failures: { [RATES_PATH]: 403 } });
    render(<CatalogScreen />);

    const denied = await screen.findByText("You do not have permission to view the catalogue.");
    expect(denied).toBeVisible();
    expect(screen.queryByText("Loading the catalogue…")).toBeNull();
    // This refusal is the catalogue's and says nothing about any other part of the product.
    expect(denied.textContent?.toLowerCase()).not.toContain("project");

    // A server error is not a permission problem. `toFailureState` returning "denied" as its
    // fallback would tell a user they lack access they actually have.
    cleanup();
    vi.unstubAllGlobals();
    stubCatalog({ failures: { [RATES_PATH]: 500 } });
    render(<CatalogScreen />);

    expect(await screen.findByText("The catalogue could not be loaded.")).toBeVisible();
    expect(screen.queryByText("You do not have permission to view the catalogue.")).toBeNull();
    expect(screen.queryByText("The catalogue could not be loaded — request timed out.")).toBeNull();

    // A backend that accepts the connection and never answers ends in a stated failure, not in a
    // loading state that never resolves.
    cleanup();
    vi.unstubAllGlobals();
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise<never>(() => {})));
    render(<CatalogScreen />);
    expect(screen.getByText("Loading the catalogue…")).toBeVisible();

    await act(async () => {
      vi.advanceTimersByTime(REQUEST_TIMEOUT_MS);
    });

    expect(screen.getByText("The catalogue could not be loaded — request timed out.")).toBeVisible();
    expect(screen.queryByText("Loading the catalogue…")).toBeNull();
    expect(screen.queryByText("The catalogue could not be loaded.")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("states the failure of a single catalogue read without presenting the rest as complete", async () => {
    // Six requests are six chances to fail, and five of them are dictionaries. A screen that
    // rendered the rates it did get, with one column of unnamed ids, would look like data
    // (gate-1 decision 9).
    for (const failingPath of CATALOG_PATHS) {
      stubCatalog({ rates: [RATE_SENIOR, RATE_JUNIOR], failures: { [failingPath]: 500 } });

      render(<CatalogScreen />);

      expect(
        await screen.findByText("The catalogue could not be loaded."),
        `${failingPath} failed without the screen saying so`,
      ).toBeVisible();

      // Zero rate rows, whichever read failed — including the four cases where the rate list
      // itself came back `200`.
      expect(screen.queryByRole("table")).toBeNull();
      expect(screen.queryAllByRole("row")).toHaveLength(0);
      expect(screen.queryByText("Backend engineer")).toBeNull();
      expect(screen.queryByText(/default rates?$/)).toBeNull();

      // No dictionary section is presented as complete either, and a failed screen offers no
      // actions over data it never got.
      for (const dimension of CATALOG_DIMENSIONS) {
        expect(
          screen.queryByRole("region", { name: DIMENSION_LABELS[dimension].section }),
        ).toBeNull();
      }
      expect(screen.queryByRole("button", { name: "Add default rate" })).toBeNull();
      expect(screen.queryByText("Loading the catalogue…")).toBeNull();

      cleanup();
      vi.unstubAllGlobals();
    }
  });

  // --- Supporting tests (not acceptance criteria) ----------------------------------------------

  // The SC-2-02 test "renders the add-default-rate control as reachable and wired to nothing" stood
  // here. It asserted `aria-disabled="true"` and a "Not implemented yet" tooltip — the explicit
  // out-of-scope statement of Issue #39, which SC-2-04 reverses by construction. Gate 1 of Issue #49
  // accepted that as intended (decision G-3) and required a replacement rather than a deletion: see
  // CatalogWrite.test.tsx, "the add-default-rate control opens a form that writes to the catalogue".

  it("sends the placeholder caller identity header with every catalogue read", async () => {
    const fetchMock = stubCatalog();

    render(<CatalogScreen />);
    await rateRows();

    for (const [, init] of recordedCalls(fetchMock)) {
      expect((init.headers as Record<string, string>)[CALLER_ID_HEADER]).toBeDefined();
    }
  });

  // --- Reviewer R-01 -----------------------------------------------------------------------------

  it("aborts all six reads when the screen unmounts before they settle, instead of letting them run to completion", async () => {
    // A `fetch` that only ever resolves once its own signal aborts — the shape a hung or slow read
    // has in the running application, and the one case that shows whether the abort actually
    // reaches the network layer rather than only the component's own `cancelled` flag.
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise<never>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<CatalogScreen />);
    expect(fetchMock).toHaveBeenCalledTimes(6);
    const signals = recordedCalls(fetchMock).map(([, init]) => init.signal as AbortSignal);
    expect(signals.every((signal) => signal.aborted)).toBe(false);

    unmount();

    // Every one of the six reads carried a signal, and every one of them is now aborted — a
    // screen bounced away from cannot leave any of its six requests still occupying a socket.
    expect(signals).toHaveLength(6);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
  });

  it("does not resurrect a stated failure for a read that was aborted after the screen unmounted", async () => {
    // The abort rejects the read after the component is already gone; that rejection reaches the
    // effect's existing `.catch`, and the pre-existing `cancelled` guard must still keep it from
    // calling `setState` on an unmounted component — exercised here together with the new abort
    // rather than in isolation.
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise<never>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container, unmount } = render(<CatalogScreen />);
    unmount();

    // Let the abort's rejection settle.
    await Promise.resolve();
    await Promise.resolve();

    // Nothing this screen would have rendered is anywhere in the document any more — not the
    // component's own container (removed by `unmount`), and no failure text leaked past it either.
    expect(container.textContent).toBe("");
    expect(screen.queryByText("The catalogue could not be loaded.")).toBeNull();
    expect(screen.queryByText("Loading the catalogue…")).toBeNull();
  });

  // --- Reviewer R-06 -----------------------------------------------------------------------------

  it("aborts the other five reads as soon as one of the six rejects, without waiting for the screen to unmount", async () => {
    // The rates read fails fast (a real 500, not an abort); the other five hang until their own
    // signal aborts — the same shape as the R-01 fixture above, but nothing here ever unmounts the
    // screen. If the fix regresses to "only the cleanup aborts", this test hangs instead of failing
    // false-green, because nothing else would ever settle these five promises.
    const pendingSignals: AbortSignal[] = [];
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const path = pathOf(url);
      if (path === RATES_PATH) {
        return Promise.resolve({ ok: false, status: 500 });
      }
      const signal = init?.signal as AbortSignal;
      pendingSignals.push(signal);
      return new Promise<never>((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CatalogScreen />);

    // The genuine failure is still what renders — aborting the rest must not turn into a second,
    // different outcome (and, in particular, never the "aborted" branch: `toFailureState` never
    // sees an `AbortError` for the rejection that actually decided this).
    expect(await screen.findByText("The catalogue could not be loaded.")).toBeVisible();
    expect(screen.queryByText("You do not have permission to view the catalogue.")).toBeNull();

    // The screen is still mounted — nothing here ever called `unmount` — and yet every one of the
    // five dictionary reads has its signal aborted, because the rejection of the sixth stopped them
    // on its own (Reviewer R-06).
    expect(pendingSignals).toHaveLength(5);
    expect(pendingSignals.every((signal) => signal.aborted)).toBe(true);
  });

  // --- Reviewer R-02 -----------------------------------------------------------------------------

  it("renders a stated failure, never a blank screen or a literal \"undefined\", when a rate row is missing a field the contract requires", async () => {
    // `response_shaping.CATALOG_PERSONNEL_COST_FIELDS` already removes a key conditionally; this is
    // the same mechanism reaching a field the screen has no named absence for. `Array.isArray`
    // alone would let this through, and `formatRatePerUnit` would receive `undefined` where it
    // expects a decimal string.
    const malformed = { ...RATE_SENIOR } as Record<string, unknown>;
    delete malformed.default_selling_rate;
    stubCatalog({ rates: [malformed as unknown as CatalogRate] });

    render(<CatalogScreen />);

    expect(await screen.findByText("The catalogue could not be loaded.")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screenText()).not.toContain("undefined");
  });

  it("renders a stated failure when a rate row carries a field of the wrong type", async () => {
    // A `number` where the contract promises a decimal `string` — the shape a naive backend
    // refactor could produce without touching `Array.isArray` at all.
    const malformed = { ...RATE_SENIOR, default_selling_rate: 100.01 } as unknown as CatalogRate;
    stubCatalog({ rates: [malformed] });

    render(<CatalogScreen />);

    expect(await screen.findByText("The catalogue could not be loaded.")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("renders a stated failure when a rate row does not say whose rate it is, rather than calling it internal", async () => {
    // SC-2-03. `vendor_id` is always in the body; `null` is the statement "ours". A row that simply
    // does not mention a vendor — an older backend, a proxy dropping fields — is a payload this
    // client cannot read. Treating the absent key as `null` would put a subcontractor's price on
    // screen labelled as the organisation's own, with nothing thrown and nothing to notice.
    const malformed = { ...RATE_SENIOR } as Record<string, unknown>;
    delete malformed.vendor_id;
    stubCatalog({ rates: [malformed as unknown as CatalogRate] });

    render(<CatalogScreen />);

    expect(await screen.findByText("The catalogue could not be loaded.")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screenText()).not.toContain(INTERNAL_RATE);
  });

  it("renders a stated failure when a dictionary entry is missing its name", async () => {
    const malformed = { id: ROLES[0].id } as Record<string, unknown>;
    stubCatalog({ dictionaries: { roles: [malformed as unknown as DimensionEntry] } });

    render(<CatalogScreen />);

    expect(await screen.findByText("The catalogue could not be loaded.")).toBeVisible();
    expect(screen.queryByRole("region", { name: "Roles" })).toBeNull();
  });
});
