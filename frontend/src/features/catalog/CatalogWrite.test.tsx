import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { REQUEST_TIMEOUT_MS } from "../../api/client";
import {
  CATALOG_DIMENSIONS,
  type CatalogDimension,
  type CatalogRate,
  type DimensionEntry,
} from "../../api/contracts/catalog";
import { CONCURRENCY_MARKER_CONDITION, REFUSAL_SQLSTATE } from "../../api/contracts/writeRefusals";
import { CatalogScreen } from "./CatalogScreen";
import { INTERNAL_RATE, RESTRICTED_COST_RATE } from "./catalogRows";
import { DIMENSION_LABELS } from "./dimensionLabels";

/**
 * SC-2-04, K-13..K-23 — writing to the catalogue from the browser (Issue #49).
 *
 * Every test stubs `fetch` and reads what the screen sent, because the criteria are almost all about
 * the *request*: which verb, how many, with which body, carrying which spelling of "no vendor" and
 * of "no end date", at what precision. A screen that renders a convincing table while sending
 * `vendor_id: "00000000-…"` or a cost rate rounded to two places is the failure class this file
 * exists for — nothing throws, and the table looks right.
 *
 * The other half is about what the screen says afterwards, and the same rule applies to it: the
 * sentences are read *off the screen* and held against each other, not compared to a constant
 * imported from the component under test. A test that asserts `text === MY_OWN_CONSTANT` says only
 * that the component renders what the component decided to render — proved in SC-2-02, where two
 * mutations of `RESTRICTED_COST_RATE` left the suite green.
 */

// --- Fixtures ----------------------------------------------------------------------------------

/** ADR-0007's marker. Different per row on purpose: an edit that sent "some marker the screen had
 * lying around" rather than *this row's* would pass with one shared value. */
const ROLE_MARKER = "2026-09-20T09:00:00+00:00";
const RATE_MARKER = "2026-09-20T11:30:00+00:00";

function entry(id: string, name: string, marker = ROLE_MARKER): DimensionEntry {
  return { id, name, updated_at: marker };
}

const ROLES: DimensionEntry[] = [
  entry("a0000000-0000-0000-0000-000000000001", "Backend engineer"),
  entry("a0000000-0000-0000-0000-000000000002", "Project manager", "2026-09-20T09:15:00+00:00"),
];
const SENIORITIES: DimensionEntry[] = [
  entry("b0000000-0000-0000-0000-000000000001", "Senior"),
  entry("b0000000-0000-0000-0000-000000000002", "Junior"),
];
const LOCATIONS: DimensionEntry[] = [entry("c0000000-0000-0000-0000-000000000001", "Poland")];
const ENGAGEMENT_TYPES: DimensionEntry[] = [
  entry("d0000000-0000-0000-0000-000000000001", "Time & materials"),
];
const VENDORS: DimensionEntry[] = [entry("f0000000-0000-0000-0000-000000000001", "Contoso")];

const FULL_DICTIONARIES: Readonly<Record<CatalogDimension, DimensionEntry[]>> = {
  roles: ROLES,
  seniorities: SENIORITIES,
  locations: LOCATIONS,
  "engagement-types": ENGAGEMENT_TYPES,
  vendors: VENDORS,
};

/**
 * The row the catalogue already holds. Its amounts have four decimal places and the last of them
 * matters: `150.0055` renders as `150.01 EUR / hour`, and `99.9999` renders as `100.00 EUR / hour`.
 * A form seeded from the cell instead of from the response would therefore offer to save `150.01`
 * and `100.00` — a correction of the dates that silently rewrites the money (K-20).
 */
const EXISTING_RATE: CatalogRate = {
  id: "e0000000-0000-0000-0000-000000000001",
  role_id: ROLES[0].id,
  seniority_id: SENIORITIES[0].id,
  location_id: LOCATIONS[0].id,
  engagement_type_id: ENGAGEMENT_TYPES[0].id,
  vendor_id: null,
  default_cost_rate: "99.9999",
  default_selling_rate: "150.0055",
  currency: "EUR",
  unit: "hour",
  effective_from: "2026-01-01",
  effective_to: "2026-06-30",
  updated_at: RATE_MARKER,
};

/** The same row as the caller without `PERSONNEL_COSTS_READ` receives it: the field is `null`. */
const RATE_COST_WITHHELD: CatalogRate = { ...EXISTING_RATE, default_cost_rate: null };

/** And as `response_shaping` actually sends it — the key removed, not nulled. */
const RATE_COST_KEY_ABSENT: CatalogRate = (() => {
  const rate: CatalogRate = { ...EXISTING_RATE };
  delete rate.default_cost_rate;
  return rate;
})();

/**
 * What a write answers with. Deliberately unlike anything the re-read serves — a different amount,
 * a different currency, a different window — so that a screen building its table out of the write's
 * response body (instead of re-reading) renders values no fixture of the re-read contains (K-13).
 */
const WRITE_ECHO: CatalogRate = {
  ...EXISTING_RATE,
  id: "e0000000-0000-0000-0000-0000000000ff",
  default_cost_rate: null,
  default_selling_rate: "999.9900",
  currency: "USD",
  effective_from: "2001-01-01",
  effective_to: null,
  updated_at: "2026-09-21T08:00:00+00:00",
};

const ENTRY_ECHO: DimensionEntry = entry(
  "a0000000-0000-0000-0000-0000000000ff",
  "Echoed by the write, never rendered",
  "2026-09-21T08:00:00+00:00",
);

// --- The stub ----------------------------------------------------------------------------------

interface Recorded {
  readonly path: string;
  readonly method: string;
  readonly body: Record<string, unknown> | undefined;
  /** The body exactly as it left the client, before parsing — the only way to tell "the key is
   * absent" from "the key is there holding `undefined`" (K-21). */
  readonly raw: string | undefined;
  /** The signal `fetch` was handed: the client's own controller, which aborts when the screen's
   * does. Whether it is `aborted` after a bounce off the screen is the observable half of "a read
   * this screen no longer wants is actually ended" (Reviewer R-02). */
  readonly signal: AbortSignal | undefined;
}

interface WriteAnswer {
  readonly status: number;
  /** A refusal's `detail`, in whichever of FastAPI's two shapes this answer uses. */
  readonly detail?: unknown;
  /** A success's body. */
  readonly payload?: unknown;
}

interface StubOptions {
  readonly rates?: CatalogRate[];
  readonly dictionaries?: Partial<Record<CatalogDimension, DimensionEntry[]>>;
  /** What `GET /catalog/rates` answers *after* a write has been sent. The point of K-13: if this
   * differs from `rates`, the table can only show it by having read again. */
  readonly ratesAfterWrite?: CatalogRate[];
  readonly dictionariesAfterWrite?: Partial<Record<CatalogDimension, DimensionEntry[]>>;
  /** Status every read answers once a write has been sent — the G-6 case (K-19). */
  readonly readsFailAfterWrite?: number;
  readonly answerWrite?: (call: Recorded) => WriteAnswer;
  /**
   * Awaited before every read that follows a write, so the post-save re-read window can be held
   * open and looked at (Reviewer R-02). Without it that window is a handful of microtasks — real
   * against a real server, unobservable in a test, which is why nothing covered it.
   *
   * A read aborted while it waits here rejects the way `fetch` does, rather than answering: the
   * client's `AbortController` is what the screen's cleanup aborts, and an answer arriving after an
   * abort would be a stub politely doing what no browser does.
   */
  readonly holdReadsAfterWrite?: PromiseLike<void>;
}

/** A promise with its resolution in the test's hands. */
function deferred(): { readonly promise: Promise<void>; readonly release: () => void } {
  let release: () => void = () => {};
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

function abortError(): Error {
  const error = new Error("The operation was aborted.");
  error.name = "AbortError";
  return error;
}

const RATES_PATH = "/catalog/rates";

function dimensionPath(dimension: CatalogDimension): string {
  return `/catalog/dimensions/${dimension}`;
}

const CATALOG_PATHS: readonly string[] = [RATES_PATH, ...CATALOG_DIMENSIONS.map(dimensionPath)];

function pathOf(url: string): string {
  return new URL(url).pathname;
}

function stubCatalog(options: StubOptions = {}) {
  const calls: Recorded[] = [];
  let written = false;

  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = pathOf(url);
    const method = init?.method ?? "GET";
    const raw = typeof init?.body === "string" ? init.body : undefined;
    const call: Recorded = {
      path,
      method,
      raw,
      body: raw === undefined ? undefined : (JSON.parse(raw) as Record<string, unknown>),
      signal: init?.signal ?? undefined,
    };
    calls.push(call);

    if (method !== "GET") {
      written = true;
      const answer = options.answerWrite?.(call) ?? {
        status: 201,
        payload: path === RATES_PATH || path.startsWith(`${RATES_PATH}/`) ? WRITE_ECHO : ENTRY_ECHO,
      };
      if (answer.status >= 400) {
        return { ok: false, status: answer.status, json: async () => ({ detail: answer.detail }) };
      }
      return { ok: true, status: answer.status, json: async () => answer.payload };
    }

    if (written && options.holdReadsAfterWrite !== undefined) {
      await options.holdReadsAfterWrite;
      if (init?.signal?.aborted === true) {
        throw abortError();
      }
    }

    if (written && options.readsFailAfterWrite !== undefined) {
      return { ok: false, status: options.readsFailAfterWrite };
    }

    const rates =
      written && options.ratesAfterWrite !== undefined
        ? options.ratesAfterWrite
        : (options.rates ?? [EXISTING_RATE]);
    const dictionaries = {
      ...FULL_DICTIONARIES,
      ...options.dictionaries,
      ...(written ? options.dictionariesAfterWrite : undefined),
    };

    if (path === RATES_PATH) {
      return { ok: true, status: 200, json: async () => ({ rates, total: rates.length }) };
    }
    const dimension = CATALOG_DIMENSIONS.find((candidate) => dimensionPath(candidate) === path);
    if (dimension !== undefined) {
      return { ok: true, status: 200, json: async () => ({ entries: dictionaries[dimension] }) };
    }
    throw new Error(`The screen asked for a path it has no business reading: ${path}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function writes(calls: readonly Recorded[]): Recorded[] {
  return calls.filter((call) => call.method !== "GET");
}

function reads(calls: readonly Recorded[]): Recorded[] {
  return calls.filter((call) => call.method === "GET");
}

// --- Driving the screen ------------------------------------------------------------------------

async function mounted(): Promise<void> {
  await screen.findByRole("region", { name: "Default rates" });
}

/** Clicks a control and lets everything it started settle — the write, and the six reads that
 * follow it. `act` rather than `waitFor` so that a submit which sends nothing at all is still a
 * complete, observable step rather than a timeout. */
async function press(control: HTMLElement): Promise<void> {
  await act(async () => {
    fireEvent.click(control);
  });
}

async function openAddRateForm(): Promise<HTMLElement> {
  await press(screen.getByRole("button", { name: "Add default rate" }));
  return screen.getByRole("form", { name: "Add a default rate" });
}

async function openEditRateForm(): Promise<HTMLElement> {
  const row = within(await screen.findByRole("table")).getAllByRole("row")[1];
  await press(within(row).getByRole("button", { name: /^Edit the default rate/ }));
  return screen.getByRole("form", { name: /^Edit the default rate/ });
}

function set(form: HTMLElement, label: string | RegExp, value: string): void {
  fireEvent.change(within(form).getByLabelText(label), { target: { value } });
}

interface RateValues {
  role?: string;
  seniority?: string;
  location?: string;
  engagement?: string;
  vendor?: string;
  cost?: string;
  selling?: string;
  currency?: string;
  from?: string;
  to?: string;
}

/** Fills the add form with a complete, plausible rate, overridden field by field. */
function fillNewRate(form: HTMLElement, values: RateValues = {}): void {
  set(form, "Role", values.role ?? ROLES[0].id);
  set(form, "Seniority", values.seniority ?? SENIORITIES[0].id);
  set(form, "Location", values.location ?? LOCATIONS[0].id);
  set(form, "Engagement type", values.engagement ?? ENGAGEMENT_TYPES[0].id);
  if (values.vendor !== undefined) {
    set(form, "Vendor", values.vendor);
  }
  set(form, "Default cost rate", values.cost ?? "60.0050");
  set(form, "Default selling rate", values.selling ?? "100.0050");
  set(form, "Currency", values.currency ?? "EUR");
  set(form, "Effective from", values.from ?? "2027-01-01");
  if (values.to !== undefined) {
    set(form, "Effective to", values.to);
  }
}

function alertText(): string {
  return screen.getByRole("alert").textContent ?? "";
}

function screenText(): string {
  return document.body.textContent ?? "";
}

/** Any UUID at all — a rate row carries nothing but identifiers, so one on screen means the join
 * did not happen, and the form's own controls must not leak them into text either. */
const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

/**
 * Every pair of sentences differs, and neither is a fragment of the other. The second half is the
 * one that matters: "Not saved." would be distinct from every message below and would still be the
 * generic failure ADR-0009 point 5 forbids.
 *
 * Indexed rather than compared by value, and that is not a stylistic choice. Written as
 * `for (const one of messages) for (const other of messages) if (one === other) continue`, the
 * guard meant to skip *self-comparison* skips *equal strings* instead — so the one thing the check
 * exists to catch, two endings that render the same sentence, is the one thing it lets through.
 * Measured: with that spelling, merging the two `409` branches into one message left this test
 * green (developer's own mutation run, 2026-09-21).
 */
function expectPairwiseDistinct(messages: readonly string[]): void {
  for (let i = 0; i < messages.length; i += 1) {
    expect(messages[i].trim()).not.toBe("");
    for (let j = i + 1; j < messages.length; j += 1) {
      expect(messages[i], `messages ${i} and ${j} read the same`).not.toBe(messages[j]);
      expect(messages[i]).not.toContain(messages[j]);
      expect(messages[j]).not.toContain(messages[i]);
    }
  }
}

describe("writing to the catalogue", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  // --- Replaces the SC-2-02 test that asserted this control was wired to nothing ----------------

  it("the add-default-rate control opens a form that writes to the catalogue", async () => {
    // Issue #39 (SC-2-02) shipped this button `aria-disabled` with a "Not implemented yet" tooltip
    // and a test asserting exactly that. Issue #49 reverses it deliberately (gate-1 decision G-3),
    // so the old assertion is replaced by its opposite rather than removed: the control is live, it
    // opens a form, and the form reaches the endpoint that was already there.
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();

    const control = screen.getByRole("button", { name: "Add default rate" });
    expect(control).toBeVisible();
    expect(control).not.toHaveAttribute("aria-disabled");
    expect(control).not.toHaveAttribute("title", expect.stringContaining("Not implemented yet"));

    const form = await openAddRateForm();
    // Opening a form asks the server nothing: the dictionaries it needs were read on mount.
    expect(writes(calls)).toHaveLength(0);
    expect(reads(calls)).toHaveLength(6);

    fillNewRate(form);
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0].method).toBe("POST");
    expect(writes(calls)[0].path).toBe(RATES_PATH);
  });

  // --- K-13 ------------------------------------------------------------------------------------

  it("sends exactly one write per submit and then shows what a fresh read answered, never the write's own response body", async () => {
    // The re-read serves a row the write's answer never mentioned, and the write's answer carries
    // values no read ever serves. Only one of the two can be on screen afterwards, and which one it
    // is, is the whole criterion. The mutation this kills: dropping the re-read and appending the
    // `201` body (or the typed values) to the list — the row appears, and it is in no database.
    const afterWrite: CatalogRate = {
      ...EXISTING_RATE,
      id: "e0000000-0000-0000-0000-00000000000b",
      default_selling_rate: "111.1100",
      currency: "PLN",
    };
    const calls = stubCatalog({ rates: [EXISTING_RATE], ratesAfterWrite: [afterWrite] });
    render(<CatalogScreen />);
    await mounted();

    const form = await openAddRateForm();
    fillNewRate(form, { selling: "222.2200", currency: "CHF" });
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    // One write. Not two (a double submit), not zero (a screen that only pretended).
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]).toMatchObject({ method: "POST", path: RATES_PATH });
    // And the catalogue was read again afterwards: six more reads, the same six paths.
    expect(reads(calls)).toHaveLength(12);
    expect([...new Set(reads(calls).slice(6).map((call) => call.path))].sort()).toEqual(
      [...CATALOG_PATHS].sort(),
    );

    // What is on screen is the re-read's answer.
    expect(await screen.findByText("111.11 PLN / hour")).toBeVisible();
    // Not the write's response body …
    expect(screenText()).not.toContain("999.99");
    expect(screenText()).not.toContain("USD");
    // … and not what was typed into the form either.
    expect(screenText()).not.toContain("222.22");
    expect(screenText()).not.toContain("CHF");

    // The save is *stated*, not left to be inferred from a table that may or may not have changed:
    // `GET /catalog/rates` is paged and ordered newest-window-first, so a window starting earlier
    // than the page can be saved and still not appear (Issue #49, criterion 6).
    const notice = within(screen.getByRole("region", { name: "Roles & rates" }))
      .getAllByRole("status")
      .map((element) => element.textContent ?? "")
      .find((text) => /saved/i.test(text));
    expect(notice).toBeDefined();
    expect(notice).toMatch(/page/i);
  });

  it("sends a PATCH, once, when correcting an existing rate, and shows the re-read rather than the answer to the PATCH", async () => {
    const afterWrite: CatalogRate = { ...EXISTING_RATE, default_selling_rate: "333.3300" };
    const calls = stubCatalog({ rates: [EXISTING_RATE], ratesAfterWrite: [afterWrite] });
    render(<CatalogScreen />);
    await mounted();

    const form = await openEditRateForm();
    set(form, "Default selling rate", "175.5000");
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0].method).toBe("PATCH");
    expect(writes(calls)[0].path).toBe(`${RATES_PATH}/${EXISTING_RATE.id}`);
    // The marker of *this* row went back, untouched.
    expect(writes(calls)[0].body?.updated_at).toBe(RATE_MARKER);

    expect(reads(calls)).toHaveLength(12);
    expect(await screen.findByText("333.33 EUR / hour")).toBeVisible();
    expect(screenText()).not.toContain("175.50");
    expect(screenText()).not.toContain("999.99");
  });

  it("writes to every one of the five dictionaries through the segment that names it, adding and renaming alike", async () => {
    // Gate-1 decision P-4: all five dictionaries, and one mechanism for them. A per-dictionary form
    // would be five chances for the fifth one — vendors, the one SC-2-03 added last — to address the
    // wrong segment or to forget the marker, and a screen showing "Saved" would look identical.
    for (const dimension of CATALOG_DIMENSIONS) {
      const labels = DIMENSION_LABELS[dimension];
      const calls = stubCatalog();
      render(<CatalogScreen />);
      await mounted();

      const panel = within(screen.getByRole("region", { name: labels.section }));
      await press(panel.getByRole("button", { name: `Add ${labels.column.toLowerCase()}` }));
      const addForm = screen.getByRole("form", {
        name: `Add an entry to the ${labels.inSentence} dictionary`,
      });
      set(addForm, `${labels.column} name`, "A new entry");
      await press(within(addForm).getByRole("button", { name: "Save new entry" }));

      expect(writes(calls), `${dimension}: not exactly one write`).toHaveLength(1);
      expect(writes(calls)[0]).toMatchObject({
        method: "POST",
        path: dimensionPath(dimension),
        body: { name: "A new entry" },
      });
      // A create carries no marker: there is no row yet whose marker it could be.
      expect(writes(calls)[0].body).not.toHaveProperty("updated_at");

      const existing = FULL_DICTIONARIES[dimension][0];
      await press(
        panel.getByRole("button", {
          name: `Rename ${existing.name} in the ${labels.inSentence} dictionary`,
        }),
      );
      const editForm = screen.getByRole("form", {
        name: `Rename an entry of the ${labels.inSentence} dictionary`,
      });
      // The form opened with the value the *response* carried for this row.
      expect(within(editForm).getByLabelText(`${labels.column} name`)).toHaveValue(existing.name);
      set(editForm, `${labels.column} name`, "A renamed entry");
      await press(within(editForm).getByRole("button", { name: "Save the new name" }));

      expect(writes(calls), `${dimension}: the rename did not send exactly one write`).toHaveLength(
        2,
      );
      expect(writes(calls)[1]).toMatchObject({
        method: "PATCH",
        path: `${dimensionPath(dimension)}/${existing.id}`,
        body: { name: "A renamed entry", updated_at: existing.updated_at },
      });

      cleanup();
      vi.unstubAllGlobals();
    }
  });

  // --- K-14 ------------------------------------------------------------------------------------

  it("says 'no vendor' and 'no end date' in the API's own words — an explicit null, never a sentinel and never a shifted date", async () => {
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();

    // The vendor control is left at its default. That default is a *named choice* the person can
    // read, not an empty box: "Internal" is selectable, selected, and it is the first option.
    let form = await openAddRateForm();
    const vendorControl = within(form).getByLabelText("Vendor");
    expect(vendorControl).toHaveDisplayValue(INTERNAL_RATE);
    fillNewRate(form);
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    const internal = writes(calls)[0].body ?? {};
    // `null`, spelled out, present in the body. Not a nil UUID, not the string "internal", not the
    // empty string, and not simply missing — the mutation the analyst named is `uuid_nil()`.
    expect(internal.vendor_id).toBeNull();
    expect(writes(calls)[0].raw).toContain('"vendor_id":null');
    expect(writes(calls)[0].raw).not.toMatch(/00000000-0000-0000-0000-000000000000/);
    // An empty end date is the named state "open-ended", in the same spelling.
    expect(internal.effective_to).toBeNull();
    expect(writes(calls)[0].raw).not.toMatch(/9999|2999|2100-/);

    // The other two states, from the same control set: a vendor chosen, and a closed window whose
    // last day travels exactly as typed. The `+ 1 day` conversion to PostgreSQL's half-open form
    // lives in the generated column (ADR-0008, point 3) and nothing here reproduces it.
    cleanup();
    vi.unstubAllGlobals();
    const second = stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    form = await openAddRateForm();
    fillNewRate(form, { vendor: VENDORS[0].id, from: "2027-01-01", to: "2027-06-30" });
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    const named = writes(second)[0].body ?? {};
    expect(named.vendor_id).toBe(VENDORS[0].id);
    expect(named.effective_from).toBe("2027-01-01");
    expect(named.effective_to).toBe("2027-06-30");
    // Neither end moved by a day in either direction.
    expect(writes(second)[0].raw).not.toContain("2027-07-01");
    expect(writes(second)[0].raw).not.toContain("2026-12-31");
  });

  it("does not offer the unit as a choice, and does not send one", async () => {
    // The database pins `unit` to 'hour'. A control offering a choice would promise a capability
    // that does not exist (ADR-0002, addendum 2026-09-21, point 4); the unit is *stated* instead, so
    // NF-07 is satisfied by naming it rather than by faking a decision.
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();

    const form = await openAddRateForm();
    expect(within(form).queryByLabelText("Unit")).toBeNull();
    expect(within(form).getByText("Unit")).toBeVisible();
    expect(within(form).getByText(/priced per hour/i)).toBeVisible();

    fillNewRate(form);
    await press(within(form).getByRole("button", { name: "Save new rate" }));
    expect(writes(calls)[0].body).not.toHaveProperty("unit");
  });

  // --- K-15 ------------------------------------------------------------------------------------

  it("puts the typed amount on the wire at the precision it was typed, as a string, with nothing rounded away", async () => {
    // `Number("100.005")` is 100.00499999999999…, and `roundDecimalString(_, 2)` is what the table
    // does for *display*. Either one on the way in changes the value being stored, in a column that
    // keeps four decimal places. The two mutations named at gate 1 are exactly those.
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();

    const form = await openAddRateForm();
    fillNewRate(form, { cost: "60.0055", selling: "100.0050" });
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    const body = writes(calls)[0].body ?? {};
    expect(body.default_cost_rate).toBe("60.0055");
    expect(body.default_selling_rate).toBe("100.0050");
    // Strings, not JSON numbers: `"100.0050"` and `100.005` are different bytes on the wire, and the
    // second one is a float before it reaches the column.
    expect(typeof body.default_cost_rate).toBe("string");
    expect(typeof body.default_selling_rate).toBe("string");
    expect(writes(calls)[0].raw).toContain('"default_selling_rate":"100.0050"');
    // Not the display rounding, and not a truncation to the column's scale either.
    expect(writes(calls)[0].raw).not.toContain("100.01");
    expect(writes(calls)[0].raw).not.toContain("100.00\"");
    expect(writes(calls)[0].raw).not.toContain("60.01");
  });

  it("sends more precision than the column can hold rather than shortening it, and lets the server refuse", async () => {
    // ADR-0002's addendum is explicit: a value whose precision cannot be kept is a request to be
    // *refused*, not adjusted (R-05). A client that trimmed `123.456789` to `123.4568` would store
    // a number nobody typed, and the `422` that exists to catch it would never fire.
    const calls = stubCatalog({
      answerWrite: () => ({
        status: 422,
        detail: [{ loc: ["body", "default_selling_rate"], msg: "decimal places", type: "value" }],
      }),
    });
    render(<CatalogScreen />);
    await mounted();

    const form = await openAddRateForm();
    fillNewRate(form, { selling: "123.456789" });
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    expect(writes(calls)[0].body?.default_selling_rate).toBe("123.456789");
    expect(writes(calls)[0].raw).not.toContain("123.4568");
    // And the refusal names the field the server named — not a rule this form invented.
    expect(alertText()).toMatch(/default selling rate/i);
    // The typed value is still in the field: a refusal is not a reason to retype it.
    expect(within(form).getByLabelText("Default selling rate")).toHaveValue("123.456789");
  });

  // --- K-20 ------------------------------------------------------------------------------------

  it("fills the edit form from the API's full-precision answer, never from the rounded amount on screen", async () => {
    // ADR-0008, addendum 2026-09-19, point 2 promised this criterion to "the first task with an edit
    // of a rate". This is that task. The contrast is inside one screen: the cell says 150.01, the
    // stored value is 150.0055, and the form must say the second.
    const calls = stubCatalog({ rates: [EXISTING_RATE] });
    render(<CatalogScreen />);
    await mounted();

    // What the table shows — rounded to two places for display, by the one formatter.
    expect(await screen.findByText("150.01 EUR / hour")).toBeVisible();
    expect(screen.getByText("100.00 EUR / hour")).toBeVisible();

    const form = await openEditRateForm();
    // What the form holds — the response's own strings.
    expect(within(form).getByLabelText("Default selling rate")).toHaveValue("150.0055");
    expect(within(form).getByLabelText("Default cost rate")).toHaveValue("99.9999");
    expect(within(form).getByLabelText("Currency")).toHaveValue("EUR");
    expect(within(form).getByLabelText("Effective from")).toHaveValue("2026-01-01");
    expect(within(form).getByLabelText("Effective to")).toHaveValue("2026-06-30");

    // And a correction of the *dates* leaves the amounts alone rather than saving the rounded ones.
    set(form, "Effective to", "2026-12-31");
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    const raw = writes(calls)[0].raw ?? "";
    expect(raw).not.toContain("150.01");
    expect(raw).not.toContain("100.00");
    expect(writes(calls)[0].body).not.toHaveProperty("default_selling_rate");
    expect(writes(calls)[0].body?.effective_to).toBe("2026-12-31");
  });

  it("makes a window open-ended by sending an explicit null, and leaves it alone by sending nothing", async () => {
    // The one field whose explicit `null` is a value rather than a mistake. Absent and `null` are
    // two different requests (`NULLABLE_RATE_EDIT_FIELDS`), and a form that could only express one
    // of them could not close a window's end date at all.
    const calls = stubCatalog({ rates: [EXISTING_RATE] });
    render(<CatalogScreen />);
    await mounted();

    const form = await openEditRateForm();
    set(form, "Effective to", "");
    set(form, "Default selling rate", "151.0000");
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    expect(writes(calls)[0].raw).toContain('"effective_to":null');
    expect(writes(calls)[0].body?.default_selling_rate).toBe("151.0000");
    // The window's start was not touched, so it is not in the body at all.
    expect(writes(calls)[0].body).not.toHaveProperty("effective_from");
  });

  // --- K-17 ------------------------------------------------------------------------------------

  it("never shows back a cost rate the caller wrote and the server did not return, in either spelling of 'not returned'", async () => {
    // The caller holds `CATALOG_WRITE` and not `PERSONNEL_COSTS_READ` — the only state today's
    // system can actually be in. They type a cost, it is stored, and the response carries no cost.
    // From then on, the screen showing it would be the single place in the product where a withheld
    // number is visible, and "the save worked" would still be true (Issue #49, criterion 4).
    for (const rate of [RATE_COST_WITHHELD, RATE_COST_KEY_ABSENT]) {
      const calls = stubCatalog({
        rates: [rate],
        ratesAfterWrite: [rate],
        answerWrite: () => ({ status: 201, payload: rate }),
      });
      render(<CatalogScreen />);
      await mounted();

      const form = await openAddRateForm();
      fillNewRate(form, { cost: "77.7777" });
      await press(within(form).getByRole("button", { name: "Save new rate" }));

      // It reached the server — the criterion is about what comes back, not about refusing to send.
      expect(writes(calls)[0].body?.default_cost_rate).toBe("77.7777");

      // And it is nowhere on the screen afterwards: not in a cell, not in a message, and not left
      // sitting in a form control (the form is gone — a form still holding the value would keep it
      // out of `textContent` and in plain sight).
      await waitFor(() => {
        expect(screen.queryByRole("form", { name: "Add a default rate" })).toBeNull();
      });
      expect(screenText()).not.toContain("77.7777");
      expect(screenText()).not.toContain("77.78");
      expect(screen.queryByDisplayValue("77.7777")).toBeNull();

      const cells = within(within(await screen.findByRole("table")).getAllByRole("row")[1])
        .getAllByRole("cell")
        .map((cell) => cell.textContent);
      expect(cells[7]).toBe(RESTRICTED_COST_RATE);

      cleanup();
      vi.unstubAllGlobals();
    }
  });

  // --- K-21 ------------------------------------------------------------------------------------

  it("offers no cost-rate value to edit, and sends none, when the response carried none", async () => {
    for (const rate of [RATE_COST_WITHHELD, RATE_COST_KEY_ABSENT]) {
      const calls = stubCatalog({ rates: [rate] });
      render(<CatalogScreen />);
      await mounted();

      const form = await openEditRateForm();

      // No control at all — not a control holding a made-up number, and not a disabled control
      // holding one either, which would still imply the form is carrying a value it could submit.
      expect(within(form).queryByLabelText("Default cost rate")).toBeNull();
      // The field is still named and still accounted for: it reads as the same withheld state the
      // table shows, so "you may not see this" is said in one word in both places.
      const stated = within(form).getByText(RESTRICTED_COST_RATE);
      expect(stated).toBeVisible();
      expect(stated.textContent).not.toMatch(/\d/);

      set(form, "Default selling rate", "160.0000");
      await press(within(form).getByRole("button", { name: "Save the changes" }));

      // The key is absent from the body — which is the whole reason the backend's edit request is
      // partial (gate-1 decision Q-2). Absent, not null: a `null` there is a `422`, and a value
      // there would overwrite a cost this caller was never shown.
      expect(writes(calls)[0].raw).not.toContain("default_cost_rate");
      expect(writes(calls)[0].body).not.toHaveProperty("default_cost_rate");
      expect(writes(calls)[0].body?.default_selling_rate).toBe("160.0000");

      cleanup();
      vi.unstubAllGlobals();
    }
  });

  it("does edit the cost rate when the response did carry one, so the omission above is a consequence of the answer and not of the form", async () => {
    // The contrast for the test above: without it, a form that simply never sends a cost rate would
    // satisfy K-21 completely.
    const calls = stubCatalog({ rates: [EXISTING_RATE] });
    render(<CatalogScreen />);
    await mounted();

    const form = await openEditRateForm();
    set(form, "Default cost rate", "88.8888");
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    expect(writes(calls)[0].body?.default_cost_rate).toBe("88.8888");
  });

  // --- K-16 ------------------------------------------------------------------------------------

  it("gives every shape of refusal its own answer, asserts no cause the response did not report, keeps the typed values and adds no row", async () => {
    const refusals: readonly { readonly what: string; readonly answer: WriteAnswer }[] = [
      { what: "denied", answer: { status: 403, detail: "Missing permission: CATALOG_WRITE" } },
      {
        what: "overlap",
        answer: {
          status: 409,
          detail:
            "Refused by the database. Writing the catalogue rate failed: IntegrityError, " +
            `sqlstate=${REFUSAL_SQLSTATE.exclusionViolation}, ` +
            "constraint=ex_catalog_default_rates_no_overlapping_periods. It overlaps an existing " +
            "row for the same key over an intersecting period (exclusion constraint).",
        },
      },
      {
        what: "missing reference",
        answer: {
          status: 409,
          detail:
            "Refused by the database. Writing the catalogue rate failed: IntegrityError, " +
            `sqlstate=${REFUSAL_SQLSTATE.foreignKeyViolation}, ` +
            "constraint=fk_catalog_default_rates_vendor_id. It references a row that does not " +
            "exist (foreign key).",
        },
      },
      {
        what: "stale marker",
        answer: {
          status: 409,
          detail:
            "Refused by the database. Writing the catalogue rate failed: " +
            `CatalogConcurrentEditConflict, condition=${CONCURRENCY_MARKER_CONDITION}. The row ` +
            "changed since it was read (concurrency marker). Re-read it and edit again.",
        },
      },
      {
        // A `409` the backend produced without naming a mechanism. It has to have an answer of its
        // own, because the alternative is a screen picking the most plausible of the four above.
        what: "unstated conflict",
        answer: { status: 409, detail: "Refused by the database." },
      },
      {
        what: "invalid",
        answer: {
          status: 422,
          detail: [{ loc: ["body", "currency"], msg: "string does not match", type: "value" }],
        },
      },
      { what: "server error", answer: { status: 500, detail: "Internal Server Error" } },
    ];

    const messages: string[] = [];
    for (const refusal of refusals) {
      const calls = stubCatalog({
        rates: [EXISTING_RATE],
        answerWrite: () => refusal.answer,
      });
      render(<CatalogScreen />);
      await mounted();
      const rowsBefore = within(await screen.findByRole("table")).getAllByRole("row").length;

      const form = await openAddRateForm();
      fillNewRate(form, { selling: "100.0050", currency: "EUR" });
      await press(within(form).getByRole("button", { name: "Save new rate" }));

      const message = alertText();
      messages.push(message);

      // The refusal is announced, not merely coloured (NF-08, NF-05).
      expect(screen.getByRole("alert"), refusal.what).toBeVisible();
      // The typed values survived it — all of them, not only the last one touched.
      expect(within(form).getByLabelText("Default selling rate")).toHaveValue("100.0050");
      expect(within(form).getByLabelText("Currency")).toHaveValue("EUR");
      // No row was added, and no re-read was attempted: a refused write has nothing to refresh.
      expect(within(await screen.findByRole("table")).getAllByRole("row")).toHaveLength(rowsBefore);
      expect(reads(calls), refusal.what).toHaveLength(6);
      // And the refusal quotes nothing that was typed (NF-11 holds on this side too).
      expect(message).not.toContain("100.0050");
      expect(message).not.toMatch(UUID_PATTERN);

      cleanup();
      vi.unstubAllGlobals();
    }

    // Seven endings, seven answers. The mutation named at gate 1 — both `409` branches collapsed
    // into one overlap message — dies here, and so does a single "Could not save" for all seven.
    expectPairwiseDistinct(messages);

    // The `409` that named no mechanism says so, instead of borrowing a cause from one that did.
    const unstated = messages[4];
    expect(unstated).toMatch(/did not say|not say which/i);
    expect(unstated.toLowerCase()).not.toContain("overlap");
    expect(unstated.toLowerCase()).not.toContain("already holds");
    expect(unstated.toLowerCase()).not.toContain("somebody else");
    expect(unstated.toLowerCase()).not.toContain("no longer exists");
  });

  it("calls an unanswered write unresolved rather than failed, because it cannot know which it was", async () => {
    // ADR-0009, point 2: no idempotency key, so "it never arrived" and "it committed and the answer
    // was lost" are indistinguishable from here. Naming it a failure would invite a second attempt,
    // which the database answers with a conflict about the row this very person just created.
    // A `fetch` that answers every read and never answers the write — the shape a hung backend, a
    // dropped connection or a proxy swallowing the response actually has.
    const sent: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = pathOf(url);
        const method = init?.method ?? "GET";
        sent.push(method);
        if (method !== "GET") {
          return new Promise<never>(() => {});
        }
        if (path === RATES_PATH) {
          return { ok: true, status: 200, json: async () => ({ rates: [EXISTING_RATE], total: 1 }) };
        }
        const dimension = CATALOG_DIMENSIONS.find((candidate) => dimensionPath(candidate) === path);
        if (dimension === undefined) {
          throw new Error(`Unexpected path: ${path}`);
        }
        return { ok: true, status: 200, json: async () => ({ entries: FULL_DICTIONARIES[dimension] }) };
      }),
    );

    // Mounted and filled on real timers; only the submit runs against the clock, so nothing here
    // depends on a polling query resolving while time is frozen.
    render(<CatalogScreen />);
    await mounted();
    const form = await openAddRateForm();
    fillNewRate(form);

    vi.useFakeTimers();
    fireEvent.click(within(form).getByRole("button", { name: "Save new rate" }));
    await act(async () => {
      vi.advanceTimersByTime(REQUEST_TIMEOUT_MS);
    });

    const message = alertText();
    expect(message).toMatch(/unresolved|may or may not/i);
    // Not "not saved": the screen does not know that, and saying it is a claim about the database.
    expect(message.toLowerCase()).not.toContain("not saved");
    // The button is usable again, and nothing retried on its own — one write attempt, full stop.
    expect(within(form).getByRole("button", { name: "Save new rate" })).toBeEnabled();
    expect(sent.filter((method) => method !== "GET")).toEqual(["POST"]);
  });

  // --- K-22 / K-23 -----------------------------------------------------------------------------

  it("tells the two meanings of a 409 apart on the edit path, with different words and different advice", async () => {
    // One status code, two unrelated causes, two different next actions (ADR-0007, addendum
    // 2026-09-21, point 4). "Re-read the row and try again" and "this will never save as written"
    // are not interchangeable, and merging them sends a person into a loop of re-reading a row that
    // was never the problem.
    const overlap =
      "Refused by the database. Writing the catalogue rate failed: IntegrityError, " +
      `sqlstate=${REFUSAL_SQLSTATE.exclusionViolation}, ` +
      "constraint=ex_catalog_default_rates_no_overlapping_periods. It overlaps an existing row " +
      "for the same key over an intersecting period (exclusion constraint).";
    const stale =
      "Refused by the database. Writing the catalogue rate failed: CatalogConcurrentEditConflict, " +
      `condition=${CONCURRENCY_MARKER_CONDITION}. The row changed since it was read (concurrency ` +
      "marker). Re-read it and edit again.";

    const seen: Record<string, string> = {};
    for (const [what, detail] of Object.entries({ overlap, stale })) {
      stubCatalog({ rates: [EXISTING_RATE], answerWrite: () => ({ status: 409, detail }) });
      render(<CatalogScreen />);
      await mounted();

      const form = await openEditRateForm();
      set(form, "Effective to", "2026-12-31");
      await press(within(form).getByRole("button", { name: "Save the changes" }));

      seen[what] = alertText();

      // K-22: a refusal never arrives as a success, and never as the screen's blanket failure.
      expect(screen.queryByText(/^Saved\./)).toBeNull();
      expect(screen.queryByText("The catalogue could not be loaded.")).toBeNull();
      expect(await screen.findByRole("table")).toBeVisible();

      cleanup();
      vi.unstubAllGlobals();
    }

    expectPairwiseDistinct([seen.overlap, seen.stale]);
    // Different advice, not merely different wording: one says reload and retry, the other says the
    // dates have to change. A person acting on the wrong one gets nowhere.
    expect(seen.stale).toMatch(/reload/i);
    expect(seen.stale.toLowerCase()).not.toContain("dates");
    expect(seen.overlap).toMatch(/dates/i);
    expect(seen.overlap.toLowerCase()).not.toContain("reload");
    // Neither quotes a row value — neither the backend's message nor this screen adds one (NF-11).
    for (const message of [seen.overlap, seen.stale]) {
      expect(message).not.toContain("150.0055");
      expect(message).not.toContain("99.9999");
      expect(message).not.toMatch(/\d{4}-\d{2}-\d{2}/);
    }
  });

  // --- K-18 ------------------------------------------------------------------------------------

  it("asks nobody who the caller is — not before offering a write, not before sending one", async () => {
    // K-04 and K-10 proved this for reading. The write path is where it is most tempting to break:
    // a control shown "only if permitted" needs a permission from somewhere, and the only somewhere
    // is an endpoint that does not exist. The refusal is the server's `403` and nothing else
    // (AC-06, NF-04).
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();

    // Six reads to render the screen, and every write control is already there.
    expect(reads(calls)).toHaveLength(6);
    expect(screen.getByRole("button", { name: "Add default rate" })).toBeEnabled();
    for (const dimension of CATALOG_DIMENSIONS) {
      const labels = DIMENSION_LABELS[dimension];
      expect(
        within(screen.getByRole("region", { name: labels.section })).getByRole("button", {
          name: `Add ${labels.column.toLowerCase()}`,
        }),
      ).toBeEnabled();
    }
    const row = within(await screen.findByRole("table")).getAllByRole("row")[1];
    expect(within(row).getByRole("button", { name: /^Edit the default rate/ })).toBeEnabled();

    // Opening a form asks nothing either.
    const form = await openAddRateForm();
    expect(calls).toHaveLength(6);

    fillNewRate(form);
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    // Every request the screen ever made, over the whole sequence, is a catalogue path.
    const paths = calls.map((call) => call.path);
    for (const path of paths) {
      expect(
        CATALOG_PATHS.includes(path) || path.startsWith(`${RATES_PATH}/`),
        `an unexpected path: ${path}`,
      ).toBe(true);
    }
    expect(
      paths.some((path) =>
        /permission|identity|whoami|\/me\b|auth|role-assignment|capabilit/i.test(path),
      ),
    ).toBe(false);
    // And exactly one of them was the write, so no preflight was disguised as one.
    expect(writes(calls)).toHaveLength(1);
  });

  // --- K-19 ------------------------------------------------------------------------------------

  it("says the save went through and the list did not refresh, in words that are neither a failed save nor a catalogue that could not load", async () => {
    // G-6, the gate-1 decision this criterion was created for. The dangerous outcome is not an ugly
    // message — it is the screen's existing all-or-nothing failure state, which would blank
    // everything and say the catalogue could not be loaded about a change that is already stored.
    // A person reading that saves again, into a conflict with their own row.
    let refuse = true;
    const calls = stubCatalog({
      rates: [EXISTING_RATE],
      readsFailAfterWrite: 500,
      answerWrite: () =>
        refuse
          ? { status: 409, detail: "Refused by the database." }
          : { status: 201, payload: ENTRY_ECHO },
    });
    render(<CatalogScreen />);
    await mounted();

    const panel = within(screen.getByRole("region", { name: "Roles" }));
    await press(panel.getByRole("button", { name: "Add role" }));
    const form = screen.getByRole("form", { name: "Add an entry to the roles dictionary" });
    set(form, "Role name", "Data engineer");
    await press(within(form).getByRole("button", { name: "Save new entry" }));

    // Ending one: the save was refused.
    const refusedMessage = alertText();
    expect(reads(calls)).toHaveLength(6);

    // Ending two: the save went through and the read after it did not.
    refuse = false;
    await press(within(form).getByRole("button", { name: "Save new entry" }));

    await waitFor(() => {
      expect(screen.queryByRole("form", { name: "Add an entry to the roles dictionary" })).toBeNull();
    });
    const notice = within(screen.getByRole("region", { name: "Roles & rates" }))
      .getAllByRole("status")
      .map((element) => element.textContent ?? "")
      .find((text) => /saved/i.test(text));
    expect(notice).toBeDefined();
    const savedNotRefreshed = notice ?? "";

    // The write did happen and the read after it did fail — the state under test is reachable and is
    // the one being rendered, not a lucky third path.
    expect(writes(calls)).toHaveLength(2);
    expect(reads(calls).length).toBeGreaterThan(6);

    // Ending three: the screen's pre-existing blanket failure, taken from the screen rather than
    // assumed — a separate mount where a read fails before anything was ever saved.
    cleanup();
    vi.unstubAllGlobals();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500 })),
    );
    render(<CatalogScreen />);
    const blankedOut = (await screen.findByText(/catalogue could not be loaded\.$/)).textContent ?? "";

    // Three states, three sentences, and none of them a fragment of another (K-19's own wording).
    expectPairwiseDistinct([refusedMessage, savedNotRefreshed, blankedOut]);
    // The one that matters most: the successful-save state does not claim the save failed, and does
    // not claim the catalogue could not be loaded.
    expect(savedNotRefreshed).toMatch(/saved/i);
    expect(savedNotRefreshed).not.toContain(blankedOut);
    expect(savedNotRefreshed.toLowerCase()).not.toContain("not saved");
  });

  it("leaves the rows and the dictionary sections a person was looking at on screen when the read after a save fails", async () => {
    // The other half of G-6, and the half the blanket failure state would destroy: the previous
    // answer is still the best thing anybody has, and it is still true — it is merely older than the
    // change. Blanking it would lose the only copy of the catalogue on the screen.
    const calls = stubCatalog({ rates: [EXISTING_RATE], readsFailAfterWrite: 500 });
    render(<CatalogScreen />);
    await mounted();

    const form = await openAddRateForm();
    fillNewRate(form);
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    expect(writes(calls)).toHaveLength(1);
    expect(reads(calls).length).toBeGreaterThan(6);

    // The table and every section are still there.
    expect(await screen.findByRole("table")).toBeVisible();
    expect(screen.getByText("150.01 EUR / hour")).toBeVisible();
    for (const dimension of CATALOG_DIMENSIONS) {
      expect(
        screen.getByRole("region", { name: DIMENSION_LABELS[dimension].section }),
      ).toBeVisible();
    }
    // And the screen is not in its failure state.
    expect(screen.queryByText("The catalogue could not be loaded.")).toBeNull();
    expect(screen.queryByText("You do not have permission to view the catalogue.")).toBeNull();
  });

  // --- Shape checks the form does, and the ones it deliberately does not ------------------------

  it("names a required field it is missing instead of sending an incomplete body", async () => {
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();

    const form = await openAddRateForm();
    fillNewRate(form, { currency: "" });
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    expect(alertText()).toMatch(/currency/i);
    expect(writes(calls)).toHaveLength(0);
  });

  it("does not check what the database checks — an overlapping window is sent and refused, never predicted", async () => {
    // ADR-0009, point 4. The row on screen covers 2026-01-01 – 2026-06-30 for this exact tuple; a
    // client that "helpfully" compared the new window against it would be doing check-then-act
    // against one page of a paginated list, and would refuse writes the database would have
    // accepted. The refusal must come from the server, which means the request must be sent.
    const calls = stubCatalog({
      rates: [EXISTING_RATE],
      answerWrite: () => ({
        status: 409,
        detail:
          "Refused by the database. Writing the catalogue rate failed: IntegrityError, " +
          `sqlstate=${REFUSAL_SQLSTATE.exclusionViolation}, constraint=ex_catalog_default_rates_` +
          "no_overlapping_periods. It overlaps an existing row for the same key over an " +
          "intersecting period (exclusion constraint).",
      }),
    });
    render(<CatalogScreen />);
    await mounted();

    const form = await openAddRateForm();
    // Exactly the tuple and the window already on screen.
    fillNewRate(form, { from: "2026-02-01", to: "2026-03-31" });
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    expect(writes(calls)).toHaveLength(1);
    expect(alertText()).toMatch(/overlaps/i);
  });

  it("refuses to send an edit that changes nothing, because such a body only moves the marker", async () => {
    const calls = stubCatalog({ rates: [EXISTING_RATE] });
    render(<CatalogScreen />);
    await mounted();

    const form = await openEditRateForm();
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    expect(writes(calls)).toHaveLength(0);
    expect(alertText()).toMatch(/nothing/i);
  });

  // --- Reviewer R-01 (2026-09-22): the stale-marker refusal blamed a third party ------------------

  /** The `409` the backend raises when ADR-0007's marker in the `UPDATE`'s `WHERE` matched no row. */
  const STALE_MARKER_DETAIL =
    "Refused by the database. Writing the catalogue rate failed: CatalogConcurrentEditConflict, " +
    `condition=${CONCURRENCY_MARKER_CONDITION}. The row changed since it was read (concurrency ` +
    "marker). Re-read it and edit again.";

  /** Ways of naming an author, none of which the response reports. A refusal that contains any of
   * them is asserting who did it — the module's own rule, broken by exactly one sentence until
   * R-01 (`writeOutcome.ts`, the two rules at the top of the file). */
  const NAMES_AN_AUTHOR = [
    "somebody else",
    "someone else",
    "another user",
    "another person",
    "other user",
    "third party",
    "colleague",
  ];

  it("names nobody when the concurrency marker moved, because the response names nobody", async () => {
    stubCatalog({
      rates: [EXISTING_RATE],
      answerWrite: () => ({ status: 409, detail: STALE_MARKER_DETAIL }),
    });
    render(<CatalogScreen />);
    await mounted();

    const form = await openEditRateForm();
    set(form, "Effective to", "2026-12-31");
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    const message = alertText();
    // The backend reported one fact: the row changed since it was read. The sentence may state that
    // fact and the action that settles it, and no more.
    expect(message).toMatch(/changed since/i);
    expect(message).toMatch(/reload/i);
    for (const author of NAMES_AN_AUTHOR) {
      expect(message.toLowerCase(), `the refusal names an author: "${author}"`).not.toContain(
        author,
      );
    }
    // And it admits the competing change may be this caller's own, which the next test shows is a
    // reachable state of this very screen rather than a hypothetical.
    expect(message).toMatch(/your own|own earlier save|own save/i);
  });

  it("does not blame a third party for a conflict with the caller's own save, which this screen can produce unaided", async () => {
    // The path, end to end, entirely inside one session: a save goes through, the re-read after it
    // fails (G-6), so the row on screen keeps its pre-edit marker. Opening *Edit* on that row — the
    // obvious way to check what was stored — fills the form from that stale marker, and the next
    // submit is refused as a stale-marker conflict. The competing writer is the person reading the
    // message. "Somebody else changed this row" was false here, and this is not a rare interleaving:
    // it is what the screen does when a network hiccup lands between a write and the read after it.
    let written = 0;
    const calls = stubCatalog({
      rates: [EXISTING_RATE],
      readsFailAfterWrite: 500,
      answerWrite: () => {
        written += 1;
        return written === 1
          ? { status: 201, payload: WRITE_ECHO }
          : { status: 409, detail: STALE_MARKER_DETAIL };
      },
    });
    render(<CatalogScreen />);
    await mounted();

    const firstForm = await openEditRateForm();
    set(firstForm, "Default selling rate", "175.5000");
    await press(within(firstForm).getByRole("button", { name: "Save the changes" }));

    // G-6: saved, not re-read. The row below is the pre-edit one, marker included.
    const notice = within(screen.getByRole("region", { name: "Roles & rates" }))
      .getAllByRole("status")
      .map((element) => element.textContent ?? "")
      .find((text) => /saved/i.test(text));
    expect(notice).toBeDefined();
    expect(notice).toMatch(/could not be re-read|not be re-read/i);

    // The same row, opened again to see what was stored.
    const secondForm = await openEditRateForm();
    set(secondForm, "Default selling rate", "176.5000");
    await press(within(secondForm).getByRole("button", { name: "Save the changes" }));

    // The second write carried the same, now superseded, marker the first one did — which is why
    // the server refuses it. Without this the test would pass against a screen that never reached
    // the self-conflict at all.
    expect(writes(calls)).toHaveLength(2);
    expect(writes(calls)[0].body?.updated_at).toBe(RATE_MARKER);
    expect(writes(calls)[1].body?.updated_at).toBe(RATE_MARKER);

    const message = alertText();
    expect(message).toMatch(/changed since/i);
    for (const author of NAMES_AN_AUTHOR) {
      expect(message.toLowerCase(), `a self-conflict blamed on "${author}"`).not.toContain(author);
    }
  });

  // --- Reviewer R-02 (2026-09-22): the post-save re-read window ----------------------------------

  it("ends the six reads it started when the screen is left during a post-save re-read", async () => {
    // The mount read has had this discipline since Reviewer R-01 of SC-2-02 ("a bounce off this
    // screen cannot leave some of the six reads still running"); the re-read after a save did not.
    // It built an `AbortController` only because `readCatalogue` takes one, aborted it nowhere, and
    // belonged to no cleanup — so navigating to the Projects rail mid-re-read left six `GET`s
    // running for a screen nobody is on, each holding one of the browser's six sockets.
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const gate = deferred();
    const calls = stubCatalog({ rates: [EXISTING_RATE], holdReadsAfterWrite: gate.promise });
    const { unmount } = render(<CatalogScreen />);
    await mounted();

    const form = await openAddRateForm();
    fillNewRate(form);
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    // All six re-reads are in flight, and none of them has been given up on yet.
    await waitFor(() => {
      expect(reads(calls)).toHaveLength(12);
    });
    const reread = reads(calls).slice(6);
    expect(reread.map((call) => call.signal?.aborted)).toEqual(Array(6).fill(false));

    unmount();

    // Every one of them, not one of them: `readCatalogue` is handed a single controller precisely so
    // that this cannot be partial.
    expect(reread.map((call) => call.signal?.aborted)).toEqual(Array(6).fill(true));

    // And when they settle — as aborted requests, the way `fetch` settles them — nothing is
    // restarted and nothing is logged. React 18 no longer warns about a state update on an unmounted
    // component, so this half is a smoke check, not a proof; the abort above is the proof.
    await act(async () => {
      gate.release();
      await gate.promise;
    });
    expect(reads(calls)).toHaveLength(12);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it("opens no form while a post-save re-read is in flight, and states why the controls are unavailable", async () => {
    // The window is short and it is not empty: the rows on screen are already known to be superseded
    // — the read replacing them is what P-3a is — so a form opened now would be seeded from a row,
    // and a concurrency marker, this screen is in the middle of throwing away. The save notice
    // landing afterwards would then appear above a form it is not about.
    const afterWrite: CatalogRate = { ...EXISTING_RATE, default_selling_rate: "444.4400" };
    const gate = deferred();
    const calls = stubCatalog({
      rates: [EXISTING_RATE],
      ratesAfterWrite: [afterWrite],
      holdReadsAfterWrite: gate.promise,
    });
    render(<CatalogScreen />);
    await mounted();

    const form = await openAddRateForm();
    fillNewRate(form);
    await press(within(form).getByRole("button", { name: "Save new rate" }));
    await waitFor(() => {
      expect(reads(calls)).toHaveLength(12);
    });

    // The window is named rather than silent: a control disabled for an unstated reason reads as a
    // broken screen (NF-05).
    const stated = screen
      .getAllByRole("status")
      .map((element) => element.textContent ?? "")
      .find((text) => /re-reading/i.test(text));
    expect(stated).toBeDefined();

    // Every control that would open a form is unavailable, in all three places they live.
    const addRate = screen.getByRole("button", { name: "Add default rate" });
    const editRate = within(within(screen.getByRole("table")).getAllByRole("row")[1]).getByRole(
      "button",
      { name: /^Edit the default rate/ },
    );
    const addRole = within(screen.getByRole("region", { name: "Roles" })).getByRole("button", {
      name: "Add role",
    });
    const rename = within(screen.getByRole("region", { name: "Roles" })).getAllByRole("button", {
      name: /^Rename /,
    })[0];
    for (const control of [addRate, editRate, addRole, rename]) {
      expect(control).toBeDisabled();
    }

    // Clicking them anyway opens nothing — the form that was submitted is gone and no other appears.
    for (const control of [addRate, editRate, addRole, rename]) {
      fireEvent.click(control);
    }
    expect(screen.queryByRole("form")).toBeNull();

    // When the read answers, the notice is the save's, there is still no form under it, and the
    // table is the read's answer.
    await act(async () => {
      gate.release();
      await gate.promise;
    });
    expect(screen.queryByRole("form")).toBeNull();
    const notice = within(screen.getByRole("region", { name: "Roles & rates" }))
      .getAllByRole("status")
      .map((element) => element.textContent ?? "")
      .find((text) => /saved/i.test(text));
    expect(notice).toBeDefined();
    expect(screen.getByText("444.44 EUR / hour")).toBeVisible();
    expect(screenText()).not.toMatch(/re-reading/i);

    // And the controls come back — the block is the window, not a state the screen gets stuck in.
    expect(screen.getByRole("button", { name: "Add default rate" })).toBeEnabled();
    await press(screen.getByRole("button", { name: "Add default rate" }));
    expect(screen.getByRole("form", { name: "Add a default rate" })).toBeVisible();
  });

  // --- Reviewer R-03 (2026-09-22): a 2xx this client cannot read is unresolved, not failed --------

  it("calls a committed write whose answer it cannot read unresolved, not failed", async () => {
    // The status code is the whole argument. A `2xx` is the server saying it wrote the row; a body
    // missing `updated_at` says only that the two sides disagree about the shape of the answer —
    // what a rolling deploy looks like from a browser running a version ahead of the backend.
    // Reporting that as "the server failed while writing" states a failure nothing reported, and
    // sends somebody to retry a change that is already stored.
    const committedButUnreadable: Record<string, unknown> = { ...WRITE_ECHO };
    delete committedButUnreadable.updated_at;

    const answers: readonly { readonly what: string; readonly answer: WriteAnswer }[] = [
      { what: "2xx, unreadable body", answer: { status: 201, payload: committedButUnreadable } },
      { what: "500", answer: { status: 500, detail: "Internal Server Error" } },
    ];

    const messages: string[] = [];
    for (const { what, answer } of answers) {
      const calls = stubCatalog({ rates: [EXISTING_RATE], answerWrite: () => answer });
      render(<CatalogScreen />);
      await mounted();

      const form = await openAddRateForm();
      fillNewRate(form);
      await press(within(form).getByRole("button", { name: "Save new rate" }));

      expect(writes(calls), what).toHaveLength(1);
      // Neither ending is a success: no re-read, and the form is still open with its values.
      expect(reads(calls), what).toHaveLength(6);
      messages.push(alertText());

      cleanup();
      vi.unstubAllGlobals();
    }

    const [unreadable, serverFailed] = messages;
    expectPairwiseDistinct([unreadable, serverFailed]);

    // The one that matters: a write the server committed is not reported as a write that failed.
    expect(unreadable).toMatch(/unresolved/i);
    expect(unreadable).toMatch(/may or may not/i);
    expect(unreadable.toLowerCase()).not.toContain("failed");
    expect(unreadable.toLowerCase()).not.toContain("not saved");
    // It carries the warning the unresolved ending exists for — a repeat of a change that did go
    // through comes back as a conflict (ADR-0009, point 2).
    expect(unreadable).toMatch(/conflict/i);
    // And it does not borrow the timeout's cause: the server answered, and promptly.
    expect(unreadable.toLowerCase()).not.toContain("in time");

    // The contrast: a genuine non-2xx failure still says what it always said.
    expect(serverFailed).toMatch(/failed/i);
    expect(serverFailed).toMatch(/not saved/i);
    expect(serverFailed).not.toMatch(/unresolved/i);
  });
});
