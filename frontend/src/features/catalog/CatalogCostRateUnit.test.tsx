import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, createCatalogRate, editCatalogRate, getCatalogRates } from "../../api/client";
import {
  CATALOG_DIMENSIONS,
  COST_RATE_UNITS,
  type CatalogDimension,
  type CatalogRate,
  type DimensionEntry,
} from "../../api/contracts/catalog";
import { CatalogScreen } from "./CatalogScreen";
import { RateForm } from "./RateForm";
import { RESTRICTED_COST_RATE } from "./catalogRows";
import {
  NOTHING_CHANGED,
  SAVE_REFUSED_STALE_MARKER,
  SAVE_REFUSED_UNIT_PRECONDITION,
  SAVE_REFUSED_UNSTATED,
} from "./writeOutcome";

/**
 * SC-5-09, K-01..K-07 (Issue #164) — the cost rate unit on the catalogue screen.
 *
 * The failure class here renders correctly and throws nothing: a cost of "1500.00 EUR / hour" that
 * the API meant per month, a form that saves an hourly rate because `hour` was quietly preselected,
 * a body that carries an amount at one unit and a unit at another. Every test below reads either
 * what the screen printed or the bytes the client sent, and each has a contrast — the same fixture
 * differing in exactly the field under test — so that "the screen shows something" cannot pass for
 * "the screen shows the right thing".
 *
 * The fixtures put the selling unit and the cost unit on *different* values (`unit: "hour"`,
 * `cost_rate_unit: "month"`). With equal values a cell reading the wrong field is indistinguishable
 * from one reading the right one.
 */

const MARKER = "2026-09-20T11:30:00+00:00";

function entry(id: string, name: string): DimensionEntry {
  return { id, name, updated_at: MARKER };
}

const DICTIONARIES: Readonly<Record<CatalogDimension, DimensionEntry[]>> = {
  roles: [entry("a0000000-0000-0000-0000-000000000001", "Backend engineer")],
  seniorities: [entry("b0000000-0000-0000-0000-000000000001", "Senior")],
  locations: [entry("c0000000-0000-0000-0000-000000000001", "Poland")],
  "engagement-types": [entry("d0000000-0000-0000-0000-000000000001", "Time & materials")],
  vendors: [entry("f0000000-0000-0000-0000-000000000001", "Contoso")],
};

/** Cost 150.0050 per month, selling 200.0000 per hour. The cost cell must read "150.01 EUR / month". */
const MONTHLY_COST: CatalogRate = {
  id: "e0000000-0000-0000-0000-000000000001",
  role_id: DICTIONARIES.roles[0].id,
  seniority_id: DICTIONARIES.seniorities[0].id,
  location_id: DICTIONARIES.locations[0].id,
  engagement_type_id: DICTIONARIES["engagement-types"][0].id,
  vendor_id: null,
  default_cost_rate: "150.0050",
  cost_rate_unit: "month",
  default_selling_rate: "200.0000",
  currency: "EUR",
  unit: "hour",
  effective_from: "2026-01-01",
  effective_to: "2026-06-30",
  updated_at: MARKER,
};

const WITHHELD_NULL: CatalogRate = {
  ...MONTHLY_COST,
  default_cost_rate: null,
  cost_rate_unit: null,
};

const WITHHELD_ABSENT: CatalogRate = (() => {
  const rate: CatalogRate = { ...MONTHLY_COST };
  delete rate.default_cost_rate;
  delete rate.cost_rate_unit;
  return rate;
})();

// --- The stub ------------------------------------------------------------------------------------

interface Recorded {
  readonly path: string;
  readonly method: string;
  readonly body: Record<string, unknown> | undefined;
  readonly raw: string | undefined;
}

interface WriteAnswer {
  readonly status: number;
  readonly detail?: unknown;
  readonly payload?: unknown;
}

function stubCatalog(
  options: { rates?: unknown[]; answerWrite?: (call: Recorded) => WriteAnswer } = {},
): Recorded[] {
  const calls: Recorded[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname;
      const method = init?.method ?? "GET";
      const raw = typeof init?.body === "string" ? init.body : undefined;
      const call: Recorded = {
        path,
        method,
        raw,
        body: raw === undefined ? undefined : (JSON.parse(raw) as Record<string, unknown>),
      };
      calls.push(call);
      if (method !== "GET") {
        const answer = options.answerWrite?.(call) ?? { status: 201, payload: MONTHLY_COST };
        if (answer.status >= 400) {
          return { ok: false, status: answer.status, json: async () => ({ detail: answer.detail }) };
        }
        return { ok: true, status: answer.status, json: async () => answer.payload };
      }
      if (path === "/catalog/rates") {
        const rates = options.rates ?? [MONTHLY_COST];
        return { ok: true, status: 200, json: async () => ({ rates, total: rates.length }) };
      }
      const dimension = CATALOG_DIMENSIONS.find(
        (candidate) => `/catalog/dimensions/${candidate}` === path,
      );
      if (dimension === undefined) {
        throw new Error(`unexpected read: ${path}`);
      }
      return { ok: true, status: 200, json: async () => ({ entries: DICTIONARIES[dimension] }) };
    }),
  );
  return calls;
}

/** A `fetch` that answers one JSON body with one status, for the client functions themselves. */
function stubOneAnswer(status: number, payload: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: status < 400, status, json: async () => payload })),
  );
}

function writes(calls: readonly Recorded[]): Recorded[] {
  return calls.filter((call) => call.method !== "GET");
}

// --- Driving the screen ----------------------------------------------------------------------------

async function mounted(): Promise<void> {
  await screen.findByRole("region", { name: "Default rates" });
}

async function press(control: HTMLElement): Promise<void> {
  await act(async () => {
    fireEvent.click(control);
  });
}

function set(form: HTMLElement, label: string | RegExp, value: string): void {
  fireEvent.change(within(form).getByLabelText(label), { target: { value } });
}

async function openAddForm(): Promise<HTMLElement> {
  await press(screen.getByRole("button", { name: "Add default rate" }));
  return screen.getByRole("form", { name: "Add a default rate" });
}

async function openEditForm(): Promise<HTMLElement> {
  const row = within(await screen.findByRole("table")).getAllByRole("row")[1];
  await press(within(row).getByRole("button", { name: /^Edit the default rate/ }));
  return screen.getByRole("form", { name: /^Edit the default rate/ });
}

async function firstRowCells(): Promise<string[]> {
  const row = within(await screen.findByRole("table")).getAllByRole("row")[1];
  return within(row)
    .getAllByRole("cell")
    .map((cell) => cell.textContent ?? "");
}

const SELLING_CELL = 6;
const COST_CELL = 7;

function fillNewRate(form: HTMLElement, costUnit: string | undefined): void {
  set(form, "Role", DICTIONARIES.roles[0].id);
  set(form, "Seniority", DICTIONARIES.seniorities[0].id);
  set(form, "Location", DICTIONARIES.locations[0].id);
  set(form, "Engagement type", DICTIONARIES["engagement-types"][0].id);
  set(form, "Default cost rate", "60.0050");
  if (costUnit !== undefined) {
    set(form, "Cost rate unit", costUnit);
  }
  set(form, "Default selling rate", "100.0050");
  set(form, "Currency", "EUR");
  set(form, "Effective from", "2027-01-01");
}

function alertText(): string {
  return screen.getByRole("alert").textContent ?? "";
}

describe("the cost rate unit on the catalogue screen (SC-5-09)", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  // --- K-01 ----------------------------------------------------------------------------------------

  it("K-01: offers exactly the contract's cost rate units, and the contract's set is the three the API names", async () => {
    expect([...COST_RATE_UNITS]).toEqual(["hour", "day", "month"]);

    stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    const form = await openAddForm();

    const control = within(form).getByLabelText("Cost rate unit") as HTMLSelectElement;
    const values = [...control.options].map((option) => option.value);
    // The empty option is the "no choice yet" state; everything after it is the contract's list.
    expect(values).toEqual(["", ...COST_RATE_UNITS]);
    expect(values).toHaveLength(4);
  });

  it("K-01: refuses a read whose cost unit is outside the set, or is not one of a pair, and renders nothing", async () => {
    const refused: readonly [string, Record<string, unknown>][] = [
      ["week", { cost_rate_unit: "week" }],
      ["empty string", { cost_rate_unit: "" }],
      ["a number", { cost_rate_unit: 12 }],
      ["a unit without a rate (null)", { default_cost_rate: null, cost_rate_unit: "day" }],
      ["a rate without a unit (null)", { cost_rate_unit: null }],
      ["a rate without a unit (absent)", { cost_rate_unit: undefined }],
    ];
    for (const [what, override] of refused) {
      stubOneAnswer(200, { rates: [{ ...MONTHLY_COST, ...override }], total: 1 });
      await expect(getCatalogRates(), what).rejects.toBeInstanceOf(ApiError);
      vi.unstubAllGlobals();

      // On screen: a stated failure and no row, never a cost with a unit guessed for it.
      stubCatalog({ rates: [{ ...MONTHLY_COST, ...override }] });
      render(<CatalogScreen />);
      expect(await screen.findByText("The catalogue could not be loaded."), what).toBeVisible();
      expect(screen.queryByRole("table"), what).toBeNull();
      cleanup();
      vi.unstubAllGlobals();
    }

    // Contrast: the three units, and both spellings of the withheld pair, are accepted.
    for (const good of [
      { cost_rate_unit: "hour" },
      { cost_rate_unit: "day" },
      { cost_rate_unit: "month" },
      { default_cost_rate: null, cost_rate_unit: null },
      { default_cost_rate: undefined, cost_rate_unit: undefined },
    ]) {
      stubOneAnswer(200, { rates: [{ ...MONTHLY_COST, ...good }], total: 1 });
      await expect(getCatalogRates()).resolves.toMatchObject({ total: 1 });
      vi.unstubAllGlobals();
    }
  });

  it("K-01: applies the same shape check to the answer of a create and of an edit", async () => {
    const bodyOfCreate = {
      role_id: "r",
      seniority_id: "s",
      location_id: "l",
      engagement_type_id: "e",
      vendor_id: null,
      default_cost_rate: "1.0000",
      cost_rate_unit: "day" as const,
      default_selling_rate: "2.0000",
      currency: "EUR",
      effective_from: "2027-01-01",
      effective_to: null,
    };
    const bad: readonly Record<string, unknown>[] = [
      { cost_rate_unit: "week" },
      { cost_rate_unit: "" },
      { default_cost_rate: null, cost_rate_unit: "day" },
      { cost_rate_unit: null },
    ];
    for (const override of bad) {
      stubOneAnswer(201, { ...MONTHLY_COST, ...override });
      await expect(createCatalogRate(bodyOfCreate), JSON.stringify(override)).rejects.toBeInstanceOf(
        ApiError,
      );
      vi.unstubAllGlobals();
      stubOneAnswer(200, { ...MONTHLY_COST, ...override });
      await expect(
        editCatalogRate("e", { updated_at: MARKER, currency: "EUR" }),
        JSON.stringify(override),
      ).rejects.toBeInstanceOf(ApiError);
      vi.unstubAllGlobals();
    }

    // Contrast: a well-formed pair, and a withheld pair, are what a write answers with.
    for (const good of [{ cost_rate_unit: "day" }, { default_cost_rate: null, cost_rate_unit: null }]) {
      stubOneAnswer(201, { ...MONTHLY_COST, ...good });
      await expect(createCatalogRate(bodyOfCreate)).resolves.toBeDefined();
      vi.unstubAllGlobals();
    }
  });

  // --- K-02 ----------------------------------------------------------------------------------------

  it("K-02: prints the cost with the unit of the cost rate, never the selling rate's, for month and for day", async () => {
    for (const unit of ["month", "day"] as const) {
      stubCatalog({ rates: [{ ...MONTHLY_COST, cost_rate_unit: unit }] });
      render(<CatalogScreen />);
      await mounted();

      const cells = await firstRowCells();
      expect(cells[COST_CELL]).toBe(`150.01 EUR / ${unit}`);
      // Contrast in the same row: the selling cell still reads the selling unit.
      expect(cells[SELLING_CELL]).toBe("200.00 EUR / hour");
      expect(cells[COST_CELL]).not.toContain("/ hour");

      cleanup();
      vi.unstubAllGlobals();
    }
  });

  it("K-02: seeds the edit form's unit from the response, not from the rendered cell and not from a constant", async () => {
    for (const unit of ["month", "day"] as const) {
      stubCatalog({ rates: [{ ...MONTHLY_COST, cost_rate_unit: unit }] });
      render(<CatalogScreen />);
      await mounted();

      const form = await openEditForm();
      const control = within(form).getByLabelText("Cost rate unit") as HTMLSelectElement;
      expect(control.value).toBe(unit);
      expect(control.value).not.toBe("hour");
      // The amount is the response's full-precision string, not the cell's rounded one.
      expect(within(form).getByLabelText("Default cost rate")).toHaveValue("150.0050");

      cleanup();
      vi.unstubAllGlobals();
    }
  });

  // --- K-03 ----------------------------------------------------------------------------------------

  it("K-03: shows no unit, and no default hour, beside a cost the API withheld — in either spelling — while the selling cell keeps its unit", async () => {
    for (const withheld of [WITHHELD_NULL, WITHHELD_ABSENT]) {
      stubCatalog({ rates: [withheld] });
      render(<CatalogScreen />);
      await mounted();

      const cells = await firstRowCells();
      expect(cells[COST_CELL]).toBe(RESTRICTED_COST_RATE);
      expect(cells[COST_CELL]).not.toMatch(/hour|day|month|\//);
      expect(cells[SELLING_CELL]).toBe("200.00 EUR / hour");

      cleanup();
      vi.unstubAllGlobals();
    }
    // Contrast: the row that carries the pair does show a unit in the same cell.
    stubCatalog({ rates: [MONTHLY_COST] });
    render(<CatalogScreen />);
    await mounted();
    expect((await firstRowCells())[COST_CELL]).toMatch(/\/ month$/);
  });

  // --- K-04 ----------------------------------------------------------------------------------------

  it("K-04: preselects nothing, refuses a save without a choice in the form's own words, and sends no request", async () => {
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    const form = await openAddForm();

    expect(within(form).getByLabelText("Cost rate unit")).toHaveValue("");

    fillNewRate(form, undefined);
    await press(within(form).getByRole("button", { name: "Save new rate" }));

    expect(alertText()).toBe("Not saved — Cost rate unit is required.");
    expect(writes(calls)).toHaveLength(0);

    // Contrast: the same form with a choice made is sent.
    set(form, "Cost rate unit", "day");
    await press(within(form).getByRole("button", { name: "Save new rate" }));
    expect(writes(calls)).toHaveLength(1);
  });

  it("K-04: sends the chosen unit as cost_rate_unit and no unit key, and different choices give different bodies", async () => {
    const bodies: Record<string, unknown>[] = [];
    for (const choice of ["day", "month"]) {
      const calls = stubCatalog();
      render(<CatalogScreen />);
      await mounted();
      const form = await openAddForm();
      fillNewRate(form, choice);
      await press(within(form).getByRole("button", { name: "Save new rate" }));

      const sent = writes(calls);
      expect(sent).toHaveLength(1);
      expect(sent[0].body?.cost_rate_unit).toBe(choice);
      // The selling rate's unit has no key in the request, in either spelling.
      expect(sent[0].body).not.toHaveProperty("unit");
      expect(sent[0].raw).not.toMatch(/"unit"/);
      bodies.push(sent[0].body ?? {});

      cleanup();
      vi.unstubAllGlobals();
    }
    expect(bodies[0]).not.toEqual(bodies[1]);
    expect(bodies[0].cost_rate_unit).not.toBe(bodies[1].cost_rate_unit);
  });

  it("K-04: has exactly one unit control, and it is named as the cost rate's; the selling unit is stated and not offered", async () => {
    stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    const form = await openAddForm();

    const unitControls = within(form).getAllByLabelText(/unit/i);
    expect(unitControls).toHaveLength(1);
    expect(within(form).getByLabelText("Cost rate unit")).toBe(unitControls[0]);
    // The bare "Unit" is a statement of the selling unit, not a control.
    expect(within(form).queryByLabelText("Unit")).toBeNull();
    expect(within(form).getByText("Unit")).toBeVisible();
    expect(within(form).getByText(/Every selling rate is priced per hour/)).toBeVisible();
  });

  // --- K-05 ----------------------------------------------------------------------------------------

  it("K-05: an edit that changes only the selling rate sends neither the cost amount nor the unit", async () => {
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    const form = await openEditForm();
    set(form, "Default selling rate", "210.0000");
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    const body = writes(calls)[0].body ?? {};
    expect(body.default_selling_rate).toBe("210.0000");
    expect(body).not.toHaveProperty("cost_rate_unit");
    expect(body).not.toHaveProperty("default_cost_rate");
  });

  it("K-05: an edit that changes only the unit sends the unit together with the amount as loaded, at full precision", async () => {
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    const form = await openEditForm();
    set(form, "Cost rate unit", "day");
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    const sent = writes(calls)[0];
    expect(sent.body?.cost_rate_unit).toBe("day");
    // 150.0050, not the 150.01 the cell shows.
    expect(sent.body?.default_cost_rate).toBe("150.0050");
    expect(sent.raw).not.toContain("150.01");
    // Nothing else moved.
    expect(Object.keys(sent.body ?? {}).sort()).toEqual(
      ["cost_rate_unit", "default_cost_rate", "updated_at"].sort(),
    );
  });

  it("K-05: an edit that changes only the amount sends the amount together with the stored unit", async () => {
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    const form = await openEditForm();
    set(form, "Default cost rate", "151.0000");
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    const body = writes(calls)[0].body ?? {};
    expect(body.default_cost_rate).toBe("151.0000");
    expect(body.cost_rate_unit).toBe("month");
  });

  it("K-05: warns, in a polite live region, only while the selected unit differs from the stored one, and never for a withheld row", async () => {
    stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    let form = await openEditForm();
    const warning = () =>
      within(form)
        .queryAllByRole("status")
        .map((element) => element.textContent ?? "")
        .join("");

    // Contrast, before any change: the stored unit is selected and nothing is said.
    expect(warning()).toBe("");
    set(form, "Cost rate unit", "day");
    const shown = warning();
    expect(shown).toMatch(/means the rate per the new unit/);
    expect(shown).not.toMatch(/\d/);
    expect(within(form).getAllByRole("status")[0]).toHaveAttribute("aria-live", "polite");
    // No forced re-entry: the amount stays as loaded.
    expect(within(form).getByLabelText("Default cost rate")).toHaveValue("150.0050");
    set(form, "Cost rate unit", "month");
    expect(warning()).toBe("");
    cleanup();
    vi.unstubAllGlobals();

    stubCatalog({ rates: [WITHHELD_NULL] });
    render(<CatalogScreen />);
    await mounted();
    form = await openEditForm();
    expect(warning()).toBe("");
    expect(within(form).queryByLabelText("Cost rate unit")).toBeNull();
  });

  it("K-05: the edit form has no blank unit option, and the create form keeps it", async () => {
    stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    const optionValues = (form: HTMLElement) =>
      [...(within(form).getByLabelText("Cost rate unit") as HTMLSelectElement).options].map(
        (option) => option.value,
      );
    const edit = await openEditForm();
    expect(optionValues(edit)).toEqual([...COST_RATE_UNITS]);
    await press(within(edit).getByRole("button", { name: "Cancel" }));
    const create = await openAddForm();
    expect(optionValues(create)).toEqual(["", ...COST_RATE_UNITS]);
  });

  it("K-05: re-choosing the stored unit is not a change", async () => {
    const calls = stubCatalog();
    render(<CatalogScreen />);
    await mounted();
    const form = await openEditForm();
    set(form, "Cost rate unit", "month");
    await press(within(form).getByRole("button", { name: "Save the changes" }));

    expect(writes(calls)).toHaveLength(0);
    expect(alertText()).toBe(NOTHING_CHANGED);
  });

  it("K-05: a withheld row offers no unit control and no cost-amount control, and never sends a unit", async () => {
    for (const withheld of [WITHHELD_NULL, WITHHELD_ABSENT]) {
      const calls = stubCatalog({ rates: [withheld] });
      render(<CatalogScreen />);
      await mounted();
      const form = await openEditForm();

      // A blind writer is offered no way to change a cost amount (Q-1) and no unit to guess.
      expect(within(form).queryByLabelText("Cost rate unit")).toBeNull();
      expect(within(form).queryByLabelText("Default cost rate")).toBeNull();
      expect(within(form).getByText(RESTRICTED_COST_RATE)).toBeVisible();

      set(form, "Default selling rate", "210.0000");
      await press(within(form).getByRole("button", { name: "Save the changes" }));

      const sent = writes(calls)[0];
      expect(sent.body?.default_selling_rate).toBe("210.0000");
      expect(sent.raw).not.toContain("cost_rate_unit");
      expect(sent.raw).not.toContain("default_cost_rate");

      cleanup();
      vi.unstubAllGlobals();
    }
  });

  // --- K-06 ----------------------------------------------------------------------------------------

  it("K-06: no longer says every rate is hourly or that the unit is not a choice for the cost; still says it for the selling rate", async () => {
    stubCatalog();
    render(<CatalogScreen />);
    await mounted();

    expect(
      screen.getByText("Manage default rates and the shared dictionaries used across staffing plans."),
    ).toBeVisible();
    expect(document.body.textContent).not.toContain("default hourly rates");

    const form = await openAddForm();
    const text = form.textContent ?? "";
    expect(text).not.toContain("Every catalogue rate is priced per hour");
    // The cost unit's note names the amount it belongs to and does not claim an hourly price.
    expect(within(form).getByText("The default cost rate above is an amount per this unit.")).toBeVisible();
    // The selling-rate statement stays, and says which rate it is about.
    expect(text).toContain("Every selling rate is priced per hour. The unit is not a choice.");
  });

  // --- K-07 ----------------------------------------------------------------------------------------

  it("K-07: gives a cost_rate_unit_precondition refusal its own ending, naming no unit, rate or typed value, distinct from the stale-marker and unstated ones", async () => {
    const endings: Record<string, string> = {};
    const refusals: Record<string, string> = {
      unit:
        "Refused by the database. Writing the catalogue rate failed: CatalogWriteRefused, " +
        "condition=cost_rate_unit_precondition. The cost rate unit sent does not match the one " +
        "stored, and this caller may not change it. Nothing was written.",
      marker:
        "Refused by the database. Writing the catalogue rate failed: " +
        "CatalogConcurrentEditConflict, condition=updated_at_marker. The row changed since it was read.",
      unstated: "Refused by the database.",
    };
    for (const [what, detail] of Object.entries(refusals)) {
      stubCatalog({ answerWrite: () => ({ status: 409, detail }) });
      render(<CatalogScreen />);
      await mounted();
      const form = await openEditForm();
      set(form, "Currency", "PLN");
      await press(within(form).getByRole("button", { name: "Save the changes" }));
      endings[what] = alertText();
      cleanup();
      vi.unstubAllGlobals();
    }

    expect(endings.unit).toBe(SAVE_REFUSED_UNIT_PRECONDITION);
    // The marker conflict keeps its message, and the unstated one keeps its own.
    expect(endings.marker).toBe(SAVE_REFUSED_STALE_MARKER);
    expect(endings.unstated).toBe(SAVE_REFUSED_UNSTATED);
    expect(endings.unit).not.toBe(endings.marker);
    expect(endings.unit).not.toBe(endings.unstated);
    // Names no stored unit, no rate, no typed value.
    for (const unit of COST_RATE_UNITS) {
      expect(endings.unit).not.toMatch(new RegExp(`\\b${unit}\\b`));
    }
    expect(endings.unit).not.toMatch(/\d/);
    // Tells the person what to do next, without a value in it.
    expect(endings.unit).toMatch(/reload the page/i);
    expect(endings.unit).not.toContain("PLN");
  });

  // --- Defence in depth ----------------------------------------------------------------------------

  it("K-05: a form handed half a pair (an amount and no unit) treats the cost as withheld: no controls, nothing sent", async () => {
    // The shape check refuses such a row before the screen sees it; this holds the form's own guard,
    // so that removing either layer alone is caught.
    const calls = stubCatalog();
    const half: CatalogRate = { ...MONTHLY_COST, cost_rate_unit: null };
    render(
      <RateForm rate={half} dictionaries={DICTIONARIES} onSaved={() => undefined} onCancel={() => undefined} />,
    );
    const form = screen.getByRole("form", { name: /^Edit the default rate/ });
    expect(within(form).queryByLabelText("Cost rate unit")).toBeNull();
    expect(within(form).queryByLabelText("Default cost rate")).toBeNull();

    set(form, "Default selling rate", "210.0000");
    await press(within(form).getByRole("button", { name: "Save the changes" }));
    const sent = writes(calls)[0];
    expect(sent.raw).not.toContain("cost_rate_unit");
    expect(sent.raw).not.toContain("default_cost_rate");
  });
});
