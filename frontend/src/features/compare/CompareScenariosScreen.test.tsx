import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  RevenueAssumptionsRead,
  RevenueRead,
  WithheldRevenueState,
} from "../../api/contracts/commercialTerms";
import type { ProjectListItem, ScenarioStatus } from "../../api/contracts/projects";
import type {
  AdditionalCostSource,
  AdditionalCostState,
  PersonnelCostSource,
  PersonnelCostState,
  ScenarioResults,
} from "../../api/contracts/scenarioResults";
import { NOT_APPLICABLE } from "../../lib/money";
import {
  ADDITIONAL_COST_STATE_MESSAGES,
  PERSONNEL_COST_STATE_MESSAGES,
  RESULTS_FIELD_UNAVAILABLE,
  REVENUE_STATE_MESSAGES,
} from "../projects/scenarioResultsText";
import { CompareScenariosScreen } from "./CompareScenariosScreen";

/**
 * SC-7-04 (Issue #108) — the "Compare scenarios" workspace screen: a project, then a multi-select
 * of that project's scenarios, then a table over `GET .../scenarios/compare` (SC-6-02). One
 * `describe` per analyst criterion (K-01..K-05), plus the mechanical wiring (project/scenario
 * selection) and the abort/unmount discipline every screen with its own read keeps (ADR-0010,
 * point 7).
 */

// --- Fixtures --------------------------------------------------------------------------------------

const ALPHA = "aaaaaaaa-0000-0000-0000-000000000001";
const BETA = "aaaaaaaa-0000-0000-0000-000000000002";
const GAMMA = "aaaaaaaa-0000-0000-0000-000000000003";
const FIXED = "aaaaaaaa-0000-0000-0000-000000000006";

function scenario(id: string, name: string, status: ScenarioStatus = "Draft") {
  return {
    id,
    name,
    status,
    missing_inputs: [],
    ready_for_approval: status === "Approved",
    target_margin_percent: null,
  };
}

const PROJECT: ProjectListItem = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Aurora migration",
  client: "Northwind",
  delivery_period: { start: "2026-01-01", end: "2026-12-31" },
  reporting_currency: "EUR",
  description: "Core platform migration.",
  status: "Active",
  scenarios: [
    scenario(ALPHA, "Alpha"),
    scenario(BETA, "Beta"),
    scenario(GAMMA, "Gamma"),
  ],
};

const DELTA = "aaaaaaaa-0000-0000-0000-000000000004";
const EPSILON = "aaaaaaaa-0000-0000-0000-000000000005";

/** A second, unrelated project — none of its scenario ids overlap `PROJECT`'s. */
const OTHER_PROJECT: ProjectListItem = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Nova rollout",
  client: "Southgate",
  delivery_period: { start: "2026-01-01", end: "2026-12-31" },
  reporting_currency: "EUR",
  description: "Second, unrelated project.",
  status: "Active",
  scenarios: [scenario(DELTA, "Delta"), scenario(EPSILON, "Epsilon")],
};

const NO_ASSUMPTIONS: RevenueAssumptionsRead = {
  model_type: null,
  hours_source: "billable_hours",
  vendor_axis: "internal",
  rate_source: "live_catalog",
  rate_windows: [],
  unresolved_months: [],
  currencies: [],
};

function revenueCalculated(amount: string, currency: string): RevenueRead {
  return {
    state: "calculated",
    amount,
    currency,
    assumptions_used: NO_ASSUMPTIONS,
    expected_state: "not_applicable",
    expected_amount: "n/a",
    category_revenues: [],
  };
}

function fixedPriceRevenue(amount: string, currency: string): RevenueRead {
  return {
    ...revenueCalculated(amount, currency),
    assumptions_used: {
      model_type: "fixed_price",
      hours_source: "not_applicable",
      vendor_axis: "not_applicable",
      rate_source: "fixed_price_terms",
      rate_windows: [],
      unresolved_months: [],
      currencies: [],
    },
  };
}

function revenueWithheld(state: WithheldRevenueState): RevenueRead {
  return {
    state,
    amount: "n/a",
    currency: null,
    assumptions_used: NO_ASSUMPTIONS,
    expected_state: "not_applicable",
    expected_amount: "n/a",
    category_revenues: [],
  };
}

function personnelCostCalculated(amount: string, currency: string): PersonnelCostSource {
  return { state: "calculated", amount, currency, paid_absence_state: "calculated", paid_absence_amount: "25.00", paid_absence_currency: currency };
}

function personnelCostWithheld(state: Exclude<PersonnelCostState, "calculated">): PersonnelCostSource {
  return { state, amount: "n/a", currency: null, paid_absence_state: state, paid_absence_amount: "n/a", paid_absence_currency: null };
}

function personnelCostGated(): PersonnelCostSource {
  return { state: "calculated", amount: null, currency: null, paid_absence_state: "calculated", paid_absence_amount: null, paid_absence_currency: null };
}

function additionalCostCalculated(amount: string, currency: string): AdditionalCostSource {
  return { state: "calculated", amount, currency };
}

function additionalCostWithheld(
  state: Exclude<AdditionalCostState, "calculated">,
): AdditionalCostSource {
  return { state, amount: "n/a", currency: null };
}

/** Every field calculated and gate open — the baseline every test below narrows from. */
function baseResults(id: string, overrides: Partial<ScenarioResults> = {}): ScenarioResults {
  return {
    scenario_id: id,
    scenario_status: "Draft",
    revenue: revenueCalculated("1000.00", "PLN"),
    personnel_cost: personnelCostCalculated("400.00", "PLN"),
    additional_cost: additionalCostCalculated("50.00", "PLN"),
    included_cost: "450.00",
    profit: "550.00",
    margin: "55.00",
    markup: "122.22",
    profitability_state: "calculated",
    ...overrides,
  };
}

// --- A backend, by path ------------------------------------------------------------------------

const COMPARE_PATH = /^\/projects\/([^/]+)\/scenarios\/compare$/;
const SINGLE_RESULTS_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/results$/;

type ComparisonAnswer =
  | { readonly status: number; readonly body?: unknown }
  | { readonly hang: true };

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (body === undefined) {
        throw new SyntaxError("Unexpected end of JSON input");
      }
      return body;
    },
  };
}

interface Backend {
  readonly projects?: ProjectListItem[];
  /** The `.../scenarios/compare` answer — a function to answer the n-th call differently. */
  readonly compare?: ComparisonAnswer | ((call: number, url: string) => ComparisonAnswer);
}

function stubBackend(backend: Backend) {
  let compareCalls = 0;
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    let answer: ComparisonAnswer | undefined;
    if (path === "/health") {
      answer = { status: 200, body: { status: "ok" } };
    } else if (path === "/projects") {
      answer = { status: 200, body: { projects: backend.projects ?? [PROJECT] } };
    } else if (COMPARE_PATH.test(path)) {
      compareCalls += 1;
      const configured = backend.compare;
      answer =
        typeof configured === "function"
          ? configured(compareCalls, url)
          : (configured ?? { hang: true });
    }
    if (answer === undefined) {
      throw new Error(`No answer stubbed for GET ${path}`);
    }
    if ("hang" in answer) {
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () =>
          reject(new DOMException("The operation was aborted.", "AbortError")),
        );
      });
    }
    return Promise.resolve(response(answer.status, answer.body));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubBackend>;

function compareCalls(fetchMock: FetchMock) {
  return fetchMock.mock.calls.filter(([url]) => COMPARE_PATH.test(new URL(url).pathname));
}

function singleResultsCalls(fetchMock: FetchMock) {
  return fetchMock.mock.calls.filter(([url]) => SINGLE_RESULTS_PATH.test(new URL(url).pathname));
}

// --- Screen helpers ------------------------------------------------------------------------------

async function openProject(name = "Aurora migration") {
  fireEvent.click(await screen.findByRole("button", { name }));
}

function check(scenarioName: string) {
  fireEvent.click(screen.getByRole("checkbox", { name: scenarioName }));
}

function compareSelected() {
  fireEvent.click(screen.getByRole("button", { name: "Compare selected" }));
}

function table(): HTMLElement {
  return screen.getByRole("table");
}

function rows(): HTMLElement[] {
  return within(table()).getAllByRole("row").slice(1); // drop the header row
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- Wiring: project then scenario selection ------------------------------------------------------

describe("selection mechanism — a project, then a multi-select of its own scenarios", () => {
  it("shows no scenario picker until a project is chosen, then lists that project's scenarios as checkboxes", async () => {
    stubBackend({});
    render(<CompareScenariosScreen />);

    await screen.findByRole("button", { name: "Aurora migration" });
    expect(screen.queryByRole("checkbox")).toBeNull();

    await openProject();

    expect(screen.getByRole("checkbox", { name: "Alpha" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Beta" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Gamma" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeDisabled();
  });

  it("sends exactly the checked scenario_id values, and none of the unchecked ones", async () => {
    const fetchMock = stubBackend({
      compare: { status: 200, body: { results: [baseResults(ALPHA), baseResults(GAMMA)] } },
    });
    render(<CompareScenariosScreen />);
    await openProject();

    check("Alpha");
    check("Gamma");
    expect(screen.getByRole("button", { name: "Compare selected" })).not.toBeDisabled();
    compareSelected();

    await waitFor(() => expect(compareCalls(fetchMock)).toHaveLength(1));
    const [url] = compareCalls(fetchMock)[0];
    const params = new URL(url).searchParams.getAll("scenario_id");
    expect(params).toEqual([ALPHA, GAMMA]);
    expect(params).not.toContain(BETA);
  });

  it("clears the scenario selection when a different project is chosen — 'Compare selected' is disabled again, not merely showing unchecked boxes for the wrong reason", async () => {
    stubBackend({ projects: [PROJECT, OTHER_PROJECT] });
    render(<CompareScenariosScreen />);
    await openProject("Aurora migration");

    check("Alpha");
    check("Beta");
    expect(screen.getByRole("button", { name: "Compare selected" })).not.toBeDisabled();

    await openProject("Nova rollout");

    // The contrast a mutant (dropping the `setSelectedScenarioIds(new Set())` reset in
    // `selectProject`) would fail: `selectedScenarioIds` would still hold ALPHA/BETA — ids that
    // don't belong to this project's own scenarios, so no checkbox here would render checked
    // (none of Delta/Epsilon match), yet the button would stay enabled from the stale, invisible
    // selection. A real reset disables it.
    expect(screen.getByRole("checkbox", { name: "Delta" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Epsilon" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Compare selected" })).toBeDisabled();
  });
});

// --- K-01 -----------------------------------------------------------------------------------------

describe("K-01 — no mixing of data between rows", () => {
  it("changing only one scenario's included_cost leaves the other two rows exactly as they were", async () => {
    stubBackend({
      compare: {
        status: 200,
        body: {
          results: [
            baseResults(ALPHA),
            baseResults(BETA, { included_cost: "999.99" }),
            baseResults(GAMMA),
          ],
        },
      },
    });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    check("Beta");
    check("Gamma");
    compareSelected();

    await screen.findByRole("table");
    const [alphaRow, betaRow, gammaRow] = rows();

    expect(within(alphaRow).getByText("450.00 PLN")).toBeVisible();
    expect(within(betaRow).getByText("999.99 PLN")).toBeVisible();
    expect(within(gammaRow).getByText("450.00 PLN")).toBeVisible();
    // The contrast a mutant (always reading `results[0]`) would fail: three distinct
    // `included_cost` cells are not all "450.00 PLN".
    expect(within(betaRow).queryByText("450.00 PLN")).toBeNull();
  });

  it("refuses the whole comparison as unreadable when a row other than the first is malformed — the shape check is not only applied to results[0]", async () => {
    stubBackend({
      compare: {
        status: 200,
        body: {
          results: [
            baseResults(ALPHA),
            // A malformed second row: `personnel_cost.state` is not one of PERSONNEL_COST_STATES.
            // A first-row-only shape check would let this through; a per-element one must not.
            { ...baseResults(BETA), personnel_cost: { state: "not_a_real_state", amount: "n/a", currency: null, paid_absence_state: "calculated", paid_absence_amount: "10.00", paid_absence_currency: "PLN" } },
          ],
        },
      },
    });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    check("Beta");
    compareSelected();

    expect(
      await screen.findByText(
        "This comparison could not be loaded — the server's answer was not in a form this screen can read.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

// --- Fixed Price revenue --------------------------------------------------------------------------

describe("Fixed Price revenue", () => {
  it("renders the server-calculated Fixed Price amount and currency in Compare", async () => {
    const fixedProject: ProjectListItem = {
      ...PROJECT,
      scenarios: [...PROJECT.scenarios, scenario(FIXED, "Fixed")],
    };
    stubBackend({
      projects: [fixedProject],
      compare: {
        status: 200,
        body: { results: [baseResults(FIXED, { revenue: fixedPriceRevenue("150000.0100", "PLN") })] },
      },
    });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Fixed");
    compareSelected();

    await screen.findByRole("table");
    expect(within(rows()[0]).getByText("150000.01 PLN")).toBeVisible();
    expect(within(rows()[0]).queryByText("150000.0100 PLN")).toBeNull();
  });
});

// --- K-02 -----------------------------------------------------------------------------------------

describe("K-02 — the personnel-cost gate is read independently, per row", () => {
  it("shows one row gated and the other with real numbers, regardless of which row is gated", async () => {
    stubBackend({
      compare: {
        status: 200,
        body: {
          results: [
            baseResults(ALPHA, {
              personnel_cost: personnelCostGated(),
              included_cost: null,
              profit: null,
              margin: null,
              markup: null,
            }),
            baseResults(BETA),
          ],
        },
      },
    });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    check("Beta");
    compareSelected();

    await screen.findByRole("table");
    const [gatedRow, openRow] = rows();

    expect(within(gatedRow).getAllByText(RESULTS_FIELD_UNAVAILABLE)).toHaveLength(5); // personnel_cost + 4 gated
    expect(within(openRow).queryByText(RESULTS_FIELD_UNAVAILABLE)).toBeNull();
    expect(within(openRow).getByText("550.00 PLN")).toBeVisible(); // profit, real number
    expect(within(gatedRow).getByText("1000.00 PLN")).toBeVisible(); // revenue stays visible
  });
});

// --- K-03 -----------------------------------------------------------------------------------------

describe("K-03 — each row's own named non-computable state is never collapsed into a shared sentinel", () => {
  it("renders three different messages for three rows in three different non-computable states", async () => {
    stubBackend({
      compare: {
        status: 200,
        body: {
          results: [
            baseResults(ALPHA, {
              personnel_cost: personnelCostWithheld("no_cost_rate"),
              included_cost: "n/a",
              profit: "n/a",
              margin: "n/a",
              markup: "n/a",
            }),
            baseResults(BETA, {
              personnel_cost: personnelCostWithheld("currency_mismatch"),
              included_cost: "n/a",
              profit: "n/a",
              margin: "n/a",
              markup: "n/a",
            }),
            baseResults(GAMMA),
          ],
        },
      },
    });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    check("Beta");
    check("Gamma");
    compareSelected();

    await screen.findByRole("table");
    const [alphaRow, betaRow, gammaRow] = rows();

    expect(within(alphaRow).getByText(PERSONNEL_COST_STATE_MESSAGES.no_cost_rate)).toBeVisible();
    expect(within(betaRow).getByText(PERSONNEL_COST_STATE_MESSAGES.currency_mismatch)).toBeVisible();
    expect(within(gammaRow).getByText("400.00 PLN")).toBeVisible();

    const texts = [
      within(alphaRow).getByText(PERSONNEL_COST_STATE_MESSAGES.no_cost_rate).textContent,
      within(betaRow).getByText(PERSONNEL_COST_STATE_MESSAGES.currency_mismatch).textContent,
    ];
    expect(new Set(texts).size).toBe(2);
  });

  it("keeps additional-cost and revenue non-computable wording distinct, per row, from personnel cost's", async () => {
    stubBackend({
      compare: {
        status: 200,
        body: {
          results: [
            baseResults(ALPHA, {
              revenue: revenueWithheld("no_commercial_terms"),
              additional_cost: additionalCostWithheld("currency_mismatch"),
              included_cost: "n/a",
              profit: "n/a",
              margin: "n/a",
              markup: "n/a",
            }),
          ],
        },
      },
    });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    compareSelected();

    await screen.findByRole("table");
    const [row] = rows();
    expect(within(row).getByText(REVENUE_STATE_MESSAGES.no_commercial_terms)).toBeVisible();
    expect(within(row).getByText(ADDITIONAL_COST_STATE_MESSAGES.currency_mismatch)).toBeVisible();
    expect(within(row).getAllByText(NOT_APPLICABLE)).toHaveLength(4); // the 4 gated fields, n/a
  });
});

// --- K-04 -----------------------------------------------------------------------------------------

describe("K-04 — all-or-nothing 404 renders one state for the whole comparison, never a fallback fan-out", () => {
  it("renders one denial, no table, and makes exactly one request to /compare — never N calls to the single-scenario endpoint", async () => {
    const fetchMock = stubBackend({ compare: { status: 404, body: { detail: "Scenario not found." } } });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    check("Beta");
    check("Gamma");
    compareSelected();

    expect(await screen.findByText("This comparison could not be shown.")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
    expect(compareCalls(fetchMock)).toHaveLength(1);
    // The mutation this guards against: a client-side fallback that, on failure, fans out to N
    // independent `getScenarioResults` calls and renders whatever succeeds.
    expect(singleResultsCalls(fetchMock)).toHaveLength(0);
    // No retry control on a plain denial (mirrors K-04 of SC-7-02).
    expect(screen.queryByRole("button", { name: "Compare again" })).toBeNull();
  });

  it("renders real content instead when every named scenario resolves", async () => {
    const fetchMock = stubBackend({
      compare: { status: 200, body: { results: [baseResults(ALPHA)] } },
    });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    compareSelected();

    expect(await screen.findByRole("table")).toBeVisible();
    expect(screen.queryByText("This comparison could not be shown.")).toBeNull();
    expect(singleResultsCalls(fetchMock)).toHaveLength(0);
  });

  it("renders a distinct, retryable state for a 409 rate-source race across the compared scenarios", async () => {
    stubBackend({ compare: { status: 409, body: { detail: "Conflict." } } });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    compareSelected();

    expect(
      await screen.findByText(
        "This comparison could not be loaded — a scenario changed while it was being read. Compare again.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Compare again" })).toBeVisible();
  });
});

// --- K-05 -----------------------------------------------------------------------------------------

describe("K-05 — row order and count are exactly the response's, never re-sorted", () => {
  it("renders three rows in the response's own order, which differs from the highest-profit-first order a sort would produce", async () => {
    // Gamma has the lowest profit, Alpha the highest — a sort by profit descending would read
    // Alpha, Beta, Gamma; the response (and the request, C/A/B) says Gamma, Alpha, Beta.
    stubBackend({
      compare: {
        status: 200,
        body: {
          results: [
            baseResults(GAMMA, { profit: "10.00" }),
            baseResults(ALPHA, { profit: "900.00" }),
            baseResults(BETA, { profit: "500.00" }),
          ],
        },
      },
    });
    render(<CompareScenariosScreen />);
    await openProject();
    // Selected in an order that does not match request/response order either — the point is that
    // rendering follows the *response*, not the click order and not a sort of any field.
    check("Gamma");
    check("Alpha");
    check("Beta");
    compareSelected();

    await screen.findByRole("table");
    const scenarioNames = rows().map((row) => within(row).getAllByRole("rowheader")[0].textContent);
    expect(scenarioNames).toEqual(["Gamma", "Alpha", "Beta"]);
  });

  it("renders exactly as many rows as the response carries — no more, no fewer", async () => {
    stubBackend({
      compare: { status: 200, body: { results: [baseResults(ALPHA), baseResults(BETA)] } },
    });
    render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    check("Beta");
    check("Gamma");
    compareSelected();

    await screen.findByRole("table");
    expect(rows()).toHaveLength(2);
  });
});

// --- Abort/unmount discipline (ADR-0010, point 7; mirrors WorkingCalendarsScreen.test.tsx) -------

describe("abort/unmount — leaving the screen ends its reads, it does not merely stop listening to them", () => {
  it("aborts the /projects read when the screen unmounts before it settles, and never sets state afterwards", async () => {
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

    const { container, unmount } = render(<CompareScenariosScreen />);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    const signal = (init as RequestInit).signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    unmount();

    expect(signal.aborted).toBe(true);

    await Promise.resolve();
    await Promise.resolve();
    expect(container.textContent).toBe("");
    expect(screen.queryByText("Projects could not be loaded.")).toBeNull();
    expect(screen.queryByText("Loading projects…")).toBeNull();
  });

  it("aborts the in-flight /compare read when the screen unmounts before it settles, and never sets state afterwards", async () => {
    let compareSignal: AbortSignal | undefined;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url).pathname;
      if (path === "/projects") {
        return Promise.resolve(response(200, { projects: [PROJECT] }));
      }
      compareSignal = init?.signal as AbortSignal;
      return new Promise<never>((_resolve, reject) => {
        compareSignal?.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<CompareScenariosScreen />);
    await openProject();
    check("Alpha");
    compareSelected();

    await screen.findByText("Comparing scenarios…");
    expect(compareSignal).toBeDefined();
    expect(compareSignal?.aborted).toBe(false);

    unmount();

    expect(compareSignal?.aborted).toBe(true);

    // Let the abort's rejection settle — no React "update on unmounted component" warning, and
    // nothing rendered afterwards to observe (the container is gone with `unmount`).
    await Promise.resolve();
    await Promise.resolve();
  });
});
