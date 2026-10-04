import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import { REQUEST_TIMEOUT_MS } from "../../api/client";
import type {
  RevenueAssumptionsRead,
  RevenueRead,
  ScenarioCommercialTerms,
  WithheldRevenueState,
} from "../../api/contracts/commercialTerms";
import type { ProjectListItem, ScenarioStatus } from "../../api/contracts/projects";
import { SCREEN_CRASH_MESSAGE } from "../../shell/ScreenErrorBoundary";
import { ProjectListScreen } from "./ProjectListScreen";
import {
  APPROVED_SCENARIO_NOTE,
  READ_DENIED,
  READ_FAILED,
  READ_NOT_FOUND,
  READ_TIMED_OUT,
  READ_UNREADABLE,
  REVENUE_STATE_MESSAGES,
  SAVED,
  SAVE_DENIED,
  SAVE_FAILED,
  SAVE_INVALID,
  SAVE_NOT_FOUND,
  SAVE_REFUSED_APPROVED,
  SAVE_REFUSED_RULE_EXISTS,
  SAVE_REFUSED_SCENARIO_CHANGED,
  SAVE_REFUSED_UNSTATED,
  SAVE_UNRESOLVED,
  SAVE_UNRESOLVED_UNREADABLE_ANSWER,
} from "./commercialTermsText";

/**
 * SC-4-06 — a scenario's commercial rule and revenue, as a section of its card on the project list
 * (Issue #71; gate 1: D-1 = B, D-2 = A, D-3 = b, Q-2 = B, Q-3 = B). One `describe` per criterion.
 *
 * Every test goes through the mounted `ProjectListScreen` (and K-07's last ones through `<App/>`),
 * not the section alone: K-04 is a claim about what the *rest of the card* does while this section
 * fails, and that cannot be observed on the section by itself.
 */

// --- Fixtures ------------------------------------------------------------------------------------

const BASELINE = "aaaaaaaa-0000-0000-0000-000000000001";
const STRETCH = "aaaaaaaa-0000-0000-0000-000000000002";
const SIGNED = "aaaaaaaa-0000-0000-0000-000000000003";
const LEAN = "bbbbbbbb-0000-0000-0000-000000000001";

function scenario(id: string, name: string, status: ScenarioStatus) {
  return {
    id,
    name,
    status,
    missing_inputs: [],
    ready_for_approval: status === "Approved",
    target_margin_percent: null,
  };
}

/** Reporting currency EUR on purpose: every revenue below is in another currency, so a screen that
 * substituted the project's currency for the revenue's would be visible (K-02). */
const AURORA: ProjectListItem = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Aurora migration",
  client: "Northwind",
  delivery_period: { start: "2026-01-01", end: "2026-12-31" },
  reporting_currency: "EUR",
  description: "Core platform migration.",
  status: "Active",
  scenarios: [
    scenario(BASELINE, "Baseline", "Draft"),
    scenario(STRETCH, "Stretch", "Draft"),
    scenario(SIGNED, "Signed plan", "Approved"),
  ],
};

const HELIOS: ProjectListItem = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Helios rollout",
  client: "Contoso",
  delivery_period: { start: "2025-03-01", end: "2025-11-30" },
  reporting_currency: "PLN",
  description: "Retail rollout.",
  status: "Active",
  scenarios: [scenario(LEAN, "Lean team", "Draft")],
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

const TM_ASSUMPTIONS: RevenueAssumptionsRead = {
  ...NO_ASSUMPTIONS,
  model_type: "time_and_material",
  rate_windows: [
    {
      source_rate_id: "cccccccc-0000-0000-0000-000000000001",
      effective_from: "2026-01-01",
      effective_to: "2026-06-30",
      default_selling_rate: "150.005",
      currency: "PLN",
    },
    {
      source_rate_id: "cccccccc-0000-0000-0000-000000000002",
      effective_from: "2026-07-01",
      effective_to: null,
      default_selling_rate: "160.00",
      currency: "PLN",
    },
  ],
  currencies: ["PLN"],
};

const FIXED_PRICE_ASSUMPTIONS: RevenueAssumptionsRead = {
  model_type: "fixed_price",
  hours_source: "not_applicable",
  vendor_axis: "not_applicable",
  rate_source: "fixed_price_terms",
  rate_windows: [],
  unresolved_months: [],
  currencies: ["PLN"],
};

const RULE = {
  id: "dddddddd-0000-0000-0000-000000000001",
  model_type: "time_and_material",
  updated_at: "2026-09-23T10:00:00Z",
  outcome_terms: null,
};

function noRule(scenarioId: string, status: ScenarioStatus = "Draft"): ScenarioCommercialTerms {
  return {
    scenario_id: scenarioId,
    scenario_status: status,
    commercial_terms: null,
    revenue: {
      state: "no_commercial_terms",
      amount: "n/a",
      currency: null,
      assumptions_used: NO_ASSUMPTIONS,
      expected_state: "not_applicable", expected_amount: "n/a", category_revenues: [],
    },
  };
}

function withRule(
  scenarioId: string,
  revenue: RevenueRead,
  modelType = "time_and_material",
): ScenarioCommercialTerms {
  return {
    scenario_id: scenarioId,
    scenario_status: "Draft",
    commercial_terms: {
      ...RULE,
      model_type: modelType,
      ...(modelType === "fixed_price" ? { agreed_price: "150000.0050", currency: "PLN" } : {}),
    },
    revenue,
  };
}

function calculated(amount: string, currency: string): RevenueRead {
  return { state: "calculated", amount, currency, assumptions_used: TM_ASSUMPTIONS, expected_state: "not_applicable", expected_amount: "n/a", category_revenues: [] };
}

function fixedPriceTerms(
  scenarioId: string,
  status: ScenarioStatus,
  agreedPrice: string,
  currency: string,
  revenue: RevenueRead,
): ScenarioCommercialTerms {
  return {
    scenario_id: scenarioId,
    scenario_status: status,
    commercial_terms: {
      ...RULE,
      model_type: "fixed_price",
      agreed_price: agreedPrice,
      currency,
    },
    revenue,
  };
}

function fixedPriceRevenue(amount = "150000.01", currency = "PLN"): RevenueRead {
  return {
    state: "calculated",
    amount,
    currency,
    assumptions_used: FIXED_PRICE_ASSUMPTIONS,
    expected_state: "not_applicable",
    expected_amount: "n/a",
    category_revenues: [],
  };
}

function withheld(
  state: WithheldRevenueState,
  assumptions: RevenueAssumptionsRead = TM_ASSUMPTIONS,
): RevenueRead {
  return { state, amount: "n/a", currency: null, assumptions_used: assumptions, expected_state: "not_applicable", expected_amount: "n/a", category_revenues: [] };
}

// --- A backend, by path and method ---------------------------------------------------------------

/** What one request is answered with: a status and a body, a body that is not JSON, or no answer at
 * all — in which case the request behaves like the platform's `fetch` and rejects when aborted. */
type Answer =
  | { readonly status: number; readonly body?: unknown }
  | { readonly hang: true }
  | { readonly deferred: Promise<{ readonly status: number; readonly body?: unknown }> };

const TERMS_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/commercial-terms$/;

interface Backend {
  readonly projects?: ProjectListItem[];
  /** The `GET` answer per scenario id — a function to answer the n-th read differently. */
  readonly reads?: Record<string, Answer | ((call: number) => Answer)>;
  /** The `POST` answer per scenario id. */
  readonly writes?: Record<string, Answer>;
  /** The `PATCH` answer per scenario id. */
  readonly edits?: Record<string, Answer>;
}

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

function stubBackend(backend: Backend) {
  const readCounts = new Map<string, number>();
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    const method = init.method ?? "GET";
    let answer: Answer | undefined;
    const match = TERMS_PATH.exec(path);
    if (path === "/health") {
      answer = { status: 200, body: { status: "ok" } };
    } else if (path === "/projects") {
      const projects = backend.projects ?? [AURORA, HELIOS];
      answer = { status: 200, body: { projects, total: projects.length } };
    } else if (path === "/catalog/rates") {
      answer = { status: 200, body: { rates: [], total: 0 } };
    } else if (path.startsWith("/catalog/dimensions/")) {
      answer = { status: 200, body: { entries: [] } };
    } else if (match !== null && method === "GET") {
      const count = (readCounts.get(match[2]) ?? 0) + 1;
      readCounts.set(match[2], count);
      const configured = backend.reads?.[match[2]];
      answer = typeof configured === "function" ? configured(count) : configured;
    } else if (match !== null && method === "POST") {
      answer = backend.writes?.[match[2]];
    } else if (match !== null && method === "PATCH") {
      answer = backend.edits?.[match[2]];
    }
    if (answer === undefined) {
      throw new Error(`No answer stubbed for ${method} ${path}`);
    }
    if ("hang" in answer) {
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () =>
          reject(new DOMException("The operation was aborted.", "AbortError")),
        );
      });
    }
    if ("deferred" in answer) {
      return answer.deferred.then(({ status, body }) => response(status, body));
    }
    return Promise.resolve(response(answer.status, answer.body));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubBackend>;

function termsCalls(fetchMock: FetchMock, scenarioId: string, method: "GET" | "POST" | "PATCH") {
  return fetchMock.mock.calls.filter(([url, init]) => {
    const match = TERMS_PATH.exec(new URL(url).pathname);
    return match?.[2] === scenarioId && ((init?.method ?? "GET") === method);
  });
}

/** The signal the n-th `GET` for a scenario was handed — failing loudly when there was none, since a
 * missing signal is the defect K-07 is about and must not read as "not aborted". */
function readSignal(fetchMock: FetchMock, scenarioId: string, call = 0): AbortSignal {
  const signal = termsCalls(fetchMock, scenarioId, "GET")[call]?.[1]?.signal;
  if (!(signal instanceof AbortSignal)) {
    throw new Error(`GET commercial-terms #${call} for ${scenarioId} carried no AbortSignal`);
  }
  return signal;
}

// --- Screen helpers ------------------------------------------------------------------------------

async function openProject(name = "Aurora migration") {
  fireEvent.click(await screen.findByRole("button", { name }));
}

function card(scenarioName: string): HTMLElement {
  const item = screen.getByRole("heading", { name: scenarioName }).closest("li");
  if (item === null) {
    throw new Error(`No scenario card for ${scenarioName}`);
  }
  return item;
}

function section(scenarioName: string): HTMLElement {
  return within(card(scenarioName)).getByRole("region", { name: "Commercial terms" });
}

/** Waits until the section has left its loading state, whatever it then says. */
async function settledSection(scenarioName: string): Promise<HTMLElement> {
  await waitFor(() =>
    expect(within(section(scenarioName)).queryByText("Loading commercial terms.")).toBeNull(),
  );
  return section(scenarioName);
}

function setButton(scenarioName: string) {
  return within(section(scenarioName)).queryByRole("button", {
    name: `Set Time & Material for ${scenarioName}`,
  });
}

function setFixedPriceButton(scenarioName: string) {
  return within(section(scenarioName)).queryByRole("button", {
    name: `Set Fixed Price for ${scenarioName}`,
  });
}

function editFixedPriceButton(scenarioName: string) {
  return within(section(scenarioName)).queryByRole("button", {
    name: `Edit agreed price for ${scenarioName}`,
  });
}

/** No message may be a substring of another — "distinguishable" as a property of the set. */
function expectPairwiseDistinct(messages: readonly string[]) {
  for (const [i, a] of messages.entries()) {
    expect(a.trim()).not.toBe("");
    for (const [j, b] of messages.entries()) {
      if (i !== j) {
        expect(b.includes(a), `"${a}" is contained in "${b}"`).toBe(false);
      }
    }
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// --- K-01 ---------------------------------------------------------------------------------------

const WITHHELD_STATES: readonly WithheldRevenueState[] = [
  "no_commercial_terms",
  "incomplete_commercial_terms",
  "unsupported_model_type",
  "no_rate",
  "currency_mismatch",
  "no_revenue_currency",
];

describe("K-01 — a withheld revenue is a named state, never 0 and never blank", () => {
  for (const state of WITHHELD_STATES) {
    it(`states "${state}" in its own words, with no amount`, async () => {
      const answer =
        state === "no_commercial_terms" ? noRule(BASELINE) : withRule(BASELINE, withheld(state));
      stubBackend({ reads: { [BASELINE]: { status: 200, body: answer }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } } });

      render(<ProjectListScreen />);
      await openProject();
      const terms = await settledSection("Baseline");

      expect(within(terms).getByText(REVENUE_STATE_MESSAGES[state])).toBeVisible();
      // Not one of the other five, and no amount in any spelling.
      for (const other of WITHHELD_STATES.filter((candidate) => candidate !== state)) {
        expect(within(terms).queryByText(REVENUE_STATE_MESSAGES[other])).toBeNull();
      }
      expect(terms.textContent).not.toMatch(/Revenue:|n\/a|\b0(\.0+)?\b/);
      expect(within(terms).queryByText(READ_UNREADABLE)).toBeNull();
    });
  }

  it("words the six states so that none contains another, and none is empty or a number", () => {
    const messages = WITHHELD_STATES.map((state) => REVENUE_STATE_MESSAGES[state]);
    expectPairwiseDistinct(messages);
    for (const message of messages) {
      expect(message).not.toMatch(/\d/);
    }
  });

  for (const [what, revenue] of [
    ["a revenue state outside the seven the contract lists", { ...withheld("no_rate"), state: "estimated" }],
    ["a calculated revenue carrying the n/a sentinel", { ...calculated("1.00", "PLN"), amount: "n/a" }],
    ["a calculated revenue with no currency", { ...calculated("1.00", "PLN"), currency: null }],
  ] as const) {
    it(`reads ${what} as a named read failure, not an amount and not a blank`, async () => {
      stubBackend({
        reads: {
          [BASELINE]: { status: 200, body: withRule(BASELINE, revenue as RevenueRead) },
          [STRETCH]: { hang: true },
          [SIGNED]: { hang: true },
        },
      });

      render(<ProjectListScreen />);
      await openProject();
      const terms = await settledSection("Baseline");

      expect(within(terms).getByText(READ_UNREADABLE)).toBeVisible();
      expect(terms.textContent).not.toContain("Revenue:");
      for (const state of WITHHELD_STATES) {
        expect(within(terms).queryByText(REVENUE_STATE_MESSAGES[state])).toBeNull();
      }
      // Stopped at the network boundary: the render boundary never saw it (ADR-0010, point 2), and
      // the rest of the card is still there.
      expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
      expect(within(card("Baseline")).getByText("Status: Draft")).toBeVisible();
    });
  }

  it("renders a calculated zero as an amount — a computed 0.00 is legal", async () => {
    // The contrast to every test above: "never 0" is about withheld states, not about zero.
    stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: withRule(BASELINE, calculated("0.00", "PLN")) },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");

    expect(within(terms).getByText("Revenue: 0.00 PLN")).toBeVisible();
    expect(within(terms).queryByText(READ_UNREADABLE)).toBeNull();
  });
});

// --- K-02 ---------------------------------------------------------------------------------------

describe("K-02 — the amount and currency are exactly the revenue's, never through Number()", () => {
  it("renders an amount beyond a double's precision digit for digit, in the revenue's own currency", async () => {
    // Number("12345678901234567.89") is 12345678901234568 — a float path cannot render this.
    stubBackend({
      reads: {
        [BASELINE]: {
          status: 200,
          body: withRule(BASELINE, calculated("12345678901234567.89", "PLN")),
        },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");

    expect(within(terms).getByText("Revenue: 12345678901234567.89 PLN")).toBeVisible();
    // The project reports in EUR; the revenue is in PLN, and PLN is what is shown.
    expect(screen.getByText("Reporting currency: EUR")).toBeVisible();
    expect(terms.textContent).not.toContain("EUR");
  });

  it("rounds half-up on the decimal string, as the backend does, in the amount and in the rate windows", async () => {
    // Number("1.005").toFixed(2) is "1.00"; the backend's ROUND_HALF_UP gives "1.01".
    stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: withRule(BASELINE, calculated("1.005", "PLN")) },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");

    expect(within(terms).getByText("Revenue: 1.01 PLN")).toBeVisible();
    expect(within(terms).getByText("2026-01-01 – 2026-06-30: 150.01 PLN")).toBeVisible();
  });
});

// --- K-03 (and D-1: assumptions_used, readably) -------------------------------------------------

describe("K-03 — the rule's presence decides whether 'Set Time & Material' is offered", () => {
  it("offers a keyboard-reachable 'Set Time & Material' when the scenario has no rule", async () => {
    stubBackend({
      reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");

    expect(within(terms).getByText("Commercial model: not set")).toBeVisible();
    const control = setButton("Baseline");
    expect(control).not.toBeNull();
    expect(control).toBeEnabled();
    expect(control?.tabIndex).toBe(0);
    control?.focus();
    expect(control).toHaveFocus();
  });

  it("names the Time & Material model and offers no way to set it again", async () => {
    stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: withRule(BASELINE, calculated("100.00", "PLN")) },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");

    expect(within(terms).getByText("Commercial model: Time & Material")).toBeVisible();
    expect(setButton("Baseline")).toBeNull();
    expect(within(terms).queryAllByRole("button")).toHaveLength(0);
  });

  it("shows a model this version does not know by the server's word, as the unsupported state and not as a broken read", async () => {
    stubBackend({
      reads: {
        [BASELINE]: {
          status: 200,
          body: withRule(BASELINE, withheld("unsupported_model_type", { ...NO_ASSUMPTIONS, model_type: "future_model" }), "future_model"),
        },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");

    expect(within(terms).getByText("Commercial model: future_model")).toBeVisible();
    expect(within(terms).getByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeVisible();
    expect(within(terms).queryByText(READ_UNREADABLE)).toBeNull();
    expect(setButton("Baseline")).toBeNull();
  });

  for (const [what, answer] of [
    [
      "no rule beside a calculated revenue",
      { ...withRule(BASELINE, calculated("5.00", "PLN")), commercial_terms: null },
    ],
    [
      "a rule beside the 'no commercial terms' state",
      { ...noRule(BASELINE), commercial_terms: RULE },
    ],
  ] as const) {
    it(`reads an answer carrying ${what} as a named read failure, offering no action`, async () => {
      // The schema pairs the two in prose ("`null` when the scenario has no rule — and then
      // `revenue.state` is `no_commercial_terms`"). Rendered as it stands, the first would offer
      // "Set Time & Material" beside an amount, the second name a model beside "no rule is set".
      stubBackend({
        reads: { [BASELINE]: { status: 200, body: answer }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
      });

      render(<ProjectListScreen />);
      await openProject();
      const terms = await settledSection("Baseline");

      expect(within(terms).getByText(READ_UNREADABLE)).toBeVisible();
      expect(setButton("Baseline")).toBeNull();
      expect(terms.textContent).not.toMatch(/Revenue:|Commercial model:/);
    });
  }

  it("does not offer the action on a scenario the read reports as approved, and says why", async () => {
    // Gate 1, Q-3: presentation of the server's status — the 409 race is K-06's own test.
    stubBackend({
      reads: {
        [BASELINE]: { hang: true },
        [STRETCH]: { hang: true },
        [SIGNED]: { status: 200, body: noRule(SIGNED, "Approved") },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Signed plan");

    expect(within(terms).getByText(REVENUE_STATE_MESSAGES.no_commercial_terms)).toBeVisible();
    expect(within(terms).getByText(APPROVED_SCENARIO_NOTE)).toBeVisible();
    expect(setButton("Signed plan")).toBeNull();
  });

  it("shows where the rates came from, the windows used and the months without a rate — by month, never by position", async () => {
    const position = "eeeeeeee-0000-0000-0000-000000000001";
    const other = "eeeeeeee-0000-0000-0000-000000000002";
    stubBackend({
      reads: {
        [BASELINE]: {
          status: 200,
          body: withRule(
            BASELINE,
            withheld("no_rate", {
              ...TM_ASSUMPTIONS,
              rate_source: "approved_snapshot",
              unresolved_months: [
                { position_id: position, period_month: "2026-03-01" },
                { position_id: other, period_month: "2026-03-01" },
                { position_id: position, period_month: "2026-04-01" },
              ],
            }),
          ),
        },
        [STRETCH]: { status: 200, body: withRule(STRETCH, calculated("5.00", "PLN")) },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");

    expect(
      within(terms).getByText("Rates taken from: the rates frozen when the scenario was approved"),
    ).toBeVisible();
    expect(within(terms).getByText("2026-01-01 – 2026-06-30: 150.01 PLN")).toBeVisible();
    expect(within(terms).getByText("2026-07-01 – Open-ended: 160.00 PLN")).toBeVisible();
    expect(within(terms).getByText("Months without a rate: 2026-03, 2026-04")).toBeVisible();
    expect(terms.textContent).not.toContain(position);
    expect(terms.textContent).not.toContain(other);

    // The contrast: a draft priced against the live catalogue says so, and names no missing month.
    const stretch = await settledSection("Stretch");
    expect(within(stretch).getByText("Rates taken from: the live catalogue")).toBeVisible();
    expect(within(stretch).queryByText(/^Months without a rate:/)).toBeNull();
  });
});

// --- K-04 ---------------------------------------------------------------------------------------

describe("K-04 — a refused read of the commercial terms stays inside its section", () => {
  it("keeps the scenario's name and status on the card when the commercial terms are refused with 403", async () => {
    stubBackend({
      reads: {
        [BASELINE]: { status: 403, body: { detail: "Forbidden" } },
        [STRETCH]: { status: 200, body: withRule(STRETCH, calculated("5.00", "PLN")) },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");

    expect(within(terms).getByText(READ_DENIED)).toBeVisible();
    // A denied read offers nothing to do (ADR-0005) — least of all the write.
    expect(within(terms).queryAllByRole("button")).toHaveLength(0);
    // The rest of the card, the rest of the list and the other cards are untouched.
    expect(within(card("Baseline")).getByRole("heading", { name: "Baseline" })).toBeVisible();
    expect(within(card("Baseline")).getByText("Status: Draft")).toBeVisible();
    expect(screen.getByRole("table")).toBeVisible();
    expect(within(await settledSection("Stretch")).getByText("Revenue: 5.00 PLN")).toBeVisible();
  });

  it("says 404 in words of its own — not 'no rule yet', not 'no permission' — and offers no write", async () => {
    stubBackend({
      reads: {
        [BASELINE]: { status: 404, body: { detail: "Scenario not found." } },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");

    expect(within(terms).getByText(READ_NOT_FOUND)).toBeVisible();
    expect(within(terms).queryByText(REVENUE_STATE_MESSAGES.no_commercial_terms)).toBeNull();
    expect(within(terms).queryByText(READ_DENIED)).toBeNull();
    expect(setButton("Baseline")).toBeNull();
    expect(within(card("Baseline")).getByText("Status: Draft")).toBeVisible();
    // The detail is the server's; it is not repeated on screen.
    expect(terms.textContent).not.toContain("Scenario not found.");
  });

  it("words every read failure differently from every other and from every revenue state", () => {
    expectPairwiseDistinct([
      READ_DENIED,
      READ_NOT_FOUND,
      READ_TIMED_OUT,
      READ_UNREADABLE,
      READ_FAILED,
      ...WITHHELD_STATES.map((state) => REVENUE_STATE_MESSAGES[state]),
    ]);
  });

  it("names a server failure of the read as a failure, and lets it be read again", async () => {
    stubBackend({
      reads: {
        [BASELINE]: (call) =>
          call === 1
            ? { status: 500 }
            : { status: 200, body: withRule(BASELINE, calculated("42.00", "PLN")) },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");
    expect(within(terms).getByText(READ_FAILED)).toBeVisible();

    fireEvent.click(
      within(terms).getByRole("button", { name: "Read commercial terms again for Baseline" }),
    );

    expect(within(await settledSection("Baseline")).getByText("Revenue: 42.00 PLN")).toBeVisible();
  });
});

// --- K-05 ---------------------------------------------------------------------------------------

/** A `POST` held open until the test releases it. */
function heldWrite() {
  let release: (answer: { status: number; body?: unknown }) => void = () => {};
  const deferred = new Promise<{ status: number; body?: unknown }>((resolve) => {
    release = resolve;
  });
  return { answer: { deferred } as Answer, release: (answer: { status: number; body?: unknown }) => release(answer) };
}

describe("K-05 — the state after a save is the 201 body, never a state assumed at click time", () => {
  it("renders the rule and revenue the 201 carried, and nothing before it arrived", async () => {
    const write = heldWrite();
    const fetchMock = stubBackend({
      reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
      writes: { [BASELINE]: write.answer },
    });

    render(<ProjectListScreen />);
    await openProject();
    await settledSection("Baseline");

    fireEvent.click(setButton("Baseline") as HTMLElement);

    // Sent, not answered: nothing on screen claims a rule or a revenue yet.
    await waitFor(() => expect(termsCalls(fetchMock, BASELINE, "POST")).toHaveLength(1));
    const terms = section("Baseline");
    expect(within(terms).getByText("Commercial model: not set")).toBeVisible();
    expect(within(terms).queryByText("Commercial model: Time & Material")).toBeNull();
    expect(terms.textContent).not.toContain("Revenue:");
    expect(setButton("Baseline")).toBeDisabled();

    // The request says exactly one thing.
    const [, init] = termsCalls(fetchMock, BASELINE, "POST")[0];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ model_type: "time_and_material" });

    await act(async () => {
      write.release({ status: 201, body: withRule(BASELINE, calculated("777.77", "PLN")) });
    });

    expect(within(terms).getByText("Commercial model: Time & Material")).toBeVisible();
    expect(within(terms).getByText("Revenue: 777.77 PLN")).toBeVisible();
    expect(within(terms).getByText(SAVED)).toHaveFocus();
    expect(setButton("Baseline")).toBeNull();
    // From the body, not from a second read (ADR-0009, addendum 2026-09-23).
    expect(termsCalls(fetchMock, BASELINE, "GET")).toHaveLength(1);
  });

  it("renders a withheld state the 201 carried as that state — the save does not imply a revenue", async () => {
    // The body decides, including when it says "no revenue yet": a screen that showed an amount or
    // a generic success line after a 201 would be deciding for the server.
    stubBackend({
      reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
      writes: { [BASELINE]: { status: 201, body: withRule(BASELINE, withheld("no_rate")) } },
    });

    render(<ProjectListScreen />);
    await openProject();
    await settledSection("Baseline");
    fireEvent.click(setButton("Baseline") as HTMLElement);

    const terms = section("Baseline");
    expect(await within(terms).findByText(SAVED)).toBeVisible();
    expect(within(terms).getByText("Commercial model: Time & Material")).toBeVisible();
    expect(within(terms).getByText(REVENUE_STATE_MESSAGES.no_rate)).toBeVisible();
    expect(terms.textContent).not.toContain("Revenue:");
  });

  it("keeps 'no rule' when the save is refused with 409 — no model name and no amount appear", async () => {
    stubBackend({
      reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
      writes: { [BASELINE]: { status: 409, body: { detail: "Refused by the database." } } },
    });

    render(<ProjectListScreen />);
    await openProject();
    await settledSection("Baseline");
    fireEvent.click(setButton("Baseline") as HTMLElement);

    const terms = section("Baseline");
    expect(await within(terms).findByText(SAVE_REFUSED_UNSTATED)).toBeVisible();
    expect(within(terms).getByText("Commercial model: not set")).toBeVisible();
    expect(within(terms).getByText(REVENUE_STATE_MESSAGES.no_commercial_terms)).toBeVisible();
    expect(within(terms).queryByText("Commercial model: Time & Material")).toBeNull();
    expect(terms.textContent).not.toContain("Revenue:");
    expect(within(terms).queryByText(SAVED)).toBeNull();
  });

  it("does not render a 201 body that fails the read's shape check — the outcome is unresolved", async () => {
    // The same check as the GET (ADR-0010, point 2): here a well-formed answer about *another*
    // scenario, which is not an answer to this save however valid it looks.
    stubBackend({
      reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
      writes: { [BASELINE]: { status: 201, body: withRule(STRETCH, calculated("777.77", "PLN")) } },
    });

    render(<ProjectListScreen />);
    await openProject();
    await settledSection("Baseline");
    fireEvent.click(setButton("Baseline") as HTMLElement);

    const terms = section("Baseline");
    expect(await within(terms).findByText(SAVE_UNRESOLVED_UNREADABLE_ANSWER)).toBeVisible();
    expect(terms.textContent).not.toContain("777.77");
    expect(within(terms).getByText("Commercial model: not set")).toBeVisible();
  });
});

// --- SC-4-10: Fixed Price creation and editing --------------------------------------------------

describe("SC-4-10 K-03 — a draft with no rule can create a whole-scenario Fixed Price rule", () => {
  it("sends the entered amount and currency, then renders the server's 201 answer", async () => {
    const write = heldWrite();
    const fetchMock = stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: noRule(BASELINE) },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
      writes: { [BASELINE]: write.answer },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");
    fireEvent.click(setFixedPriceButton("Baseline") as HTMLElement);
    fireEvent.change(within(terms).getByRole("textbox", { name: "Agreed price for Baseline" }), {
      target: { value: "175000.0050" },
    });
    fireEvent.change(within(terms).getByRole("textbox", { name: "Currency for Baseline" }), {
      target: { value: "USD" },
    });
    fireEvent.click(within(terms).getByRole("button", { name: "Save price" }));

    await waitFor(() => expect(termsCalls(fetchMock, BASELINE, "POST")).toHaveLength(1));
    const [, init] = termsCalls(fetchMock, BASELINE, "POST")[0];
    expect(JSON.parse(String(init?.body))).toEqual({
      model_type: "fixed_price",
      agreed_price: "175000.0050",
      currency: "USD",
    });
    expect(within(terms).getByText("Commercial model: not set")).toBeVisible();
    expect(within(terms).queryByText("Commercial model: Fixed Price")).toBeNull();

    await act(async () => {
      write.release({
        status: 201,
        body: fixedPriceTerms(
          BASELINE,
          "Draft",
          "175000.0050",
          "USD",
          fixedPriceRevenue("175000.01", "USD"),
        ),
      });
    });

    expect(within(terms).getByText("Commercial model: Fixed Price")).toBeVisible();
    expect(within(terms).getByText("Agreed price: 175000.0050 USD")).toBeVisible();
    expect(within(terms).getByText("Revenue: 175000.01 USD")).toBeVisible();
    expect(within(terms).getByText(SAVED)).toBeVisible();
  });

  it("does not offer rule creation on an approved scenario", async () => {
    stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: noRule(BASELINE, "Approved") },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });
    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");
    expect(setFixedPriceButton("Baseline")).toBeNull();
    expect(within(terms).getByText(APPROVED_SCENARIO_NOTE)).toBeVisible();
  });
});

describe("SC-4-10 K-04 — a draft Fixed Price rule can edit its agreed price", () => {
  it("sends the read marker with the new value and renders the successful PATCH response", async () => {
    const responseTerms = fixedPriceTerms(
      BASELINE,
      "Draft",
      "165000.0000",
      "PLN",
      fixedPriceRevenue("165000.00", "PLN"),
    );
    const fetchMock = stubBackend({
      reads: {
        [BASELINE]: {
          status: 200,
          body: fixedPriceTerms(BASELINE, "Draft", "150000.0050", "PLN", fixedPriceRevenue()),
        },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
      edits: { [BASELINE]: { status: 200, body: responseTerms } },
    });

    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");
    expect(within(terms).getByText("Agreed price: 150000.0050 PLN")).toBeVisible();
    fireEvent.click(editFixedPriceButton("Baseline") as HTMLElement);
    fireEvent.change(within(terms).getByRole("textbox", { name: "Agreed price for Baseline" }), {
      target: { value: "160000.0000" },
    });
    fireEvent.click(within(terms).getByRole("button", { name: "Save price" }));

    await waitFor(() => expect(termsCalls(fetchMock, BASELINE, "PATCH")).toHaveLength(1));
    const [, init] = termsCalls(fetchMock, BASELINE, "PATCH")[0];
    expect(JSON.parse(String(init?.body))).toEqual({
      updated_at: RULE.updated_at,
      agreed_price: "160000.0000",
    });
    expect(await within(terms).findByText("Agreed price: 165000.0000 PLN")).toBeVisible();
    expect(within(terms).getByText("Revenue: 165000.00 PLN")).toBeVisible();
    expect(within(terms).queryByText("Agreed price: 160000.0000 PLN")).toBeNull();
    expect(within(terms).getByText(SAVED)).toBeVisible();
  });

  it("does not offer price editing on an approved scenario", async () => {
    stubBackend({
      reads: {
        [BASELINE]: {
          status: 200,
          body: fixedPriceTerms(BASELINE, "Approved", "150000.0050", "PLN", fixedPriceRevenue()),
        },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });
    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");
    expect(editFixedPriceButton("Baseline")).toBeNull();
    expect(within(terms).getByText("Agreed price: 150000.0050 PLN")).toBeVisible();
  });
});

describe("SC-4-10 K-05 — Fixed Price write refusals and unresolved outcomes stay distinct", () => {
  it("shows a refusal without claiming the entered price was saved", async () => {
    stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: noRule(BASELINE) },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
      writes: { [BASELINE]: { status: 409, body: { detail: FROZEN_DETAIL } } },
    });
    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");
    fireEvent.click(setFixedPriceButton("Baseline") as HTMLElement);
    fireEvent.change(within(terms).getByRole("textbox", { name: "Agreed price for Baseline" }), {
      target: { value: "888888.0000" },
    });
    fireEvent.change(within(terms).getByRole("textbox", { name: "Currency for Baseline" }), {
      target: { value: "USD" },
    });
    fireEvent.click(within(terms).getByRole("button", { name: "Save price" }));

    const refusal = await within(terms).findByText(SAVE_REFUSED_APPROVED);
    expect(refusal).toHaveAttribute("data-write-outcome", "refused");
    expect(within(terms).queryByText(SAVED)).toBeNull();
    expect(within(terms).getByText("Commercial model: not set")).toBeVisible();
    expect(refusal.textContent).not.toContain("888888");
  });

  it("shows a timeout as unresolved, not as a refusal or a save", async () => {
    stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: noRule(BASELINE) },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
      writes: { [BASELINE]: { hang: true } },
    });
    render(<ProjectListScreen />);
    await openProject();
    const terms = await settledSection("Baseline");
    vi.useFakeTimers();
    fireEvent.click(setFixedPriceButton("Baseline") as HTMLElement);
    fireEvent.change(within(terms).getByRole("textbox", { name: "Agreed price for Baseline" }), {
      target: { value: "888888.0000" },
    });
    fireEvent.change(within(terms).getByRole("textbox", { name: "Currency for Baseline" }), {
      target: { value: "USD" },
    });
    fireEvent.click(within(terms).getByRole("button", { name: "Save price" }));

    await act(async () => {
      vi.advanceTimersByTime(REQUEST_TIMEOUT_MS);
    });
    const unresolved = within(terms).getByText(SAVE_UNRESOLVED);
    expect(unresolved).toHaveAttribute("data-write-outcome", "unresolved");
    expect(within(terms).queryByText(SAVED)).toBeNull();
    expect(within(terms).getByText("Commercial model: not set")).toBeVisible();
  });
});

// --- K-06 ---------------------------------------------------------------------------------------

/** The `detail` each refusal really carries — the backend's own sentences (held against the
 * backend source by `api/contracts/commercialTermsRefusals.test.ts`). */
const FROZEN_DETAIL =
  "This scenario is approved, so its commercial terms are part of an approved calculation and " +
  "cannot be changed. Copy the scenario to open a new version and change the copy.";
const CHANGED_DETAIL =
  "The scenario changed since it was read. Re-read it and apply the change again.";
const RULE_EXISTS_DETAIL =
  "Refused by the database. Writing the commercial terms failed: IntegrityError, " +
  "sqlstate=23505, constraint=uq_commercial_terms_scenario_id";

const REFUSALS: readonly {
  readonly what: string;
  readonly answer: { status: number; body?: unknown };
  readonly expected: string;
}[] = [
  { what: "409, scenario approved", answer: { status: 409, body: { detail: FROZEN_DETAIL } }, expected: SAVE_REFUSED_APPROVED },
  { what: "409, rule already exists", answer: { status: 409, body: { detail: RULE_EXISTS_DETAIL } }, expected: SAVE_REFUSED_RULE_EXISTS },
  { what: "409, scenario changed", answer: { status: 409, body: { detail: CHANGED_DETAIL } }, expected: SAVE_REFUSED_SCENARIO_CHANGED },
  { what: "409 naming no cause", answer: { status: 409, body: { detail: "Conflict." } }, expected: SAVE_REFUSED_UNSTATED },
  { what: "403", answer: { status: 403, body: { detail: "Forbidden" } }, expected: SAVE_DENIED },
  { what: "404", answer: { status: 404, body: { detail: "Scenario not found." } }, expected: SAVE_NOT_FOUND },
  { what: "422", answer: { status: 422, body: { detail: [{ loc: ["body", "model_type"] }] } }, expected: SAVE_INVALID },
  { what: "500 with no JSON body", answer: { status: 500 }, expected: SAVE_FAILED },
  { what: "2xx with an unreadable body", answer: { status: 201, body: { scenario_id: BASELINE } }, expected: SAVE_UNRESOLVED_UNREADABLE_ANSWER },
];

describe("K-06 — every refused save has an ending of its own", () => {
  it("words every ending differently from every other", () => {
    expectPairwiseDistinct([...REFUSALS.map((refusal) => refusal.expected), SAVE_UNRESOLVED, SAVED]);
  });

  for (const { what, answer, expected } of REFUSALS) {
    it(`ends a save answered with ${what} in its own sentence, carrying no value from the form or the body`, async () => {
      stubBackend({
        reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
        writes: { [BASELINE]: answer },
      });

      render(<ProjectListScreen />);
      await openProject();
      await settledSection("Baseline");
      fireEvent.click(setButton("Baseline") as HTMLElement);

      const terms = section("Baseline");
      const message = await within(terms).findByText(expected);
      expect(message).toHaveFocus();
      for (const other of REFUSALS.filter((refusal) => refusal.expected !== expected)) {
        expect(within(terms).queryByText(other.expected)).toBeNull();
      }
      // NF-11; ADR-0009, point 6: neither the form's value nor the body's words are repeated.
      expect(terms.textContent).not.toContain("time_and_material");
      const detail = (answer.body as { detail?: unknown } | undefined)?.detail;
      if (typeof detail === "string") {
        expect(terms.textContent).not.toContain(detail);
      }
      // The save is over: it is not offered again, reading again is.
      expect(setButton("Baseline")).toBeNull();
      expect(
        within(terms).getByRole("button", { name: "Read commercial terms again for Baseline" }),
      ).toBeEnabled();
    });
  }

  it("handles the race the hidden control cannot close: a draft approved between the read and the save", async () => {
    // Gate 1, Q-3 / ADR-0009 addendum 2026-09-23: the read said Draft, so the action is offered; the
    // server approved the scenario before the POST arrived. The 409 is a live path, not a dead one.
    stubBackend({
      reads: {
        [BASELINE]: (call) =>
          call === 1
            ? { status: 200, body: noRule(BASELINE, "Draft") }
            : { status: 200, body: noRule(BASELINE, "Approved") },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
      writes: { [BASELINE]: { status: 409, body: { detail: FROZEN_DETAIL } } },
    });

    render(<ProjectListScreen />);
    await openProject();
    await settledSection("Baseline");
    expect(setButton("Baseline")).toBeEnabled();

    fireEvent.click(setButton("Baseline") as HTMLElement);

    const terms = section("Baseline");
    const refusal = await within(terms).findByText(SAVE_REFUSED_APPROVED);
    expect(refusal.textContent).toMatch(/Copy the scenario/);
    expect(within(terms).queryByText(SAVE_REFUSED_RULE_EXISTS)).toBeNull();
    expect(within(terms).queryByText(SAVE_REFUSED_UNSTATED)).toBeNull();

    // Reading again shows what the server now says: approved, and no action.
    fireEvent.click(
      within(terms).getByRole("button", { name: "Read commercial terms again for Baseline" }),
    );
    const reread = await settledSection("Baseline");
    expect(within(reread).getByText(APPROVED_SCENARIO_NOTE)).toBeVisible();
    expect(setButton("Baseline")).toBeNull();
  });

  it("offers the save again after a refusal once reading again reports the scenario still a draft with no rule", async () => {
    // QA (SC-4-06): the contrast to the race test above, which re-reads into "Approved" and so
    // expects no action. Here the re-read says what the first read said — draft, no rule — and the
    // action must come back: a refusal hides the button until the server is asked again, it does
    // not end the section's ability to write. Both saves go out, one each.
    let saves = 0;
    const fetchMock = stubBackend({
      reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
      writes: {
        get [BASELINE]() {
          saves += 1;
          return saves === 1
            ? { status: 500 }
            : { status: 201, body: withRule(BASELINE, calculated("321.00", "PLN")) };
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    await settledSection("Baseline");
    fireEvent.click(setButton("Baseline") as HTMLElement);

    expect(await within(section("Baseline")).findByText(SAVE_FAILED)).toBeVisible();
    expect(setButton("Baseline")).toBeNull();

    fireEvent.click(
      within(section("Baseline")).getByRole("button", { name: "Read commercial terms again for Baseline" }),
    );
    const reread = await settledSection("Baseline");
    expect(within(reread).queryByText(SAVE_FAILED)).toBeNull();
    expect(setButton("Baseline")).toBeEnabled();

    fireEvent.click(setButton("Baseline") as HTMLElement);
    expect(await within(section("Baseline")).findByText(SAVED)).toBeVisible();
    expect(within(section("Baseline")).getByText("Revenue: 321.00 PLN")).toBeVisible();
    expect(termsCalls(fetchMock, BASELINE, "POST")).toHaveLength(2);
    expect(termsCalls(fetchMock, BASELINE, "GET")).toHaveLength(2);
  });

  it("ends a save the server never answers as unresolved, after the request budget", async () => {
    stubBackend({
      reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
      writes: { [BASELINE]: { hang: true } },
    });

    render(<ProjectListScreen />);
    await openProject();
    await settledSection("Baseline");

    // Fake timers only now: the write's deadline is the one under test.
    vi.useFakeTimers();
    fireEvent.click(setButton("Baseline") as HTMLElement);
    const terms = section("Baseline");

    await act(async () => {
      vi.advanceTimersByTime(REQUEST_TIMEOUT_MS - 1);
    });
    expect(within(terms).queryByText(SAVE_UNRESOLVED)).toBeNull();
    expect(within(terms).getByText("Setting the commercial rule.")).toBeVisible();

    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    expect(within(terms).getByText(SAVE_UNRESOLVED)).toBeVisible();
    expect(within(terms).getByText("Commercial model: not set")).toBeVisible();
    expect(terms.textContent).not.toContain("Revenue:");
  });
});

// --- K-07 ---------------------------------------------------------------------------------------

describe("K-07 — each card shows its own scenario's revenue, and abandoned reads are aborted", () => {
  it("renders different answers for different scenarios on the cards they belong to", async () => {
    stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: withRule(BASELINE, calculated("111.11", "PLN")) },
        [STRETCH]: { status: 200, body: withRule(STRETCH, calculated("222.22", "USD")) },
        [SIGNED]: { status: 200, body: noRule(SIGNED, "Approved") },
      },
    });

    render(<ProjectListScreen />);
    await openProject();

    const baseline = await settledSection("Baseline");
    const stretch = await settledSection("Stretch");
    const signed = await settledSection("Signed plan");

    expect(within(baseline).getByText("Revenue: 111.11 PLN")).toBeVisible();
    expect(within(stretch).getByText("Revenue: 222.22 USD")).toBeVisible();
    expect(within(signed).getByText(REVENUE_STATE_MESSAGES.no_commercial_terms)).toBeVisible();
    expect(baseline.textContent).not.toContain("222.22");
    expect(stretch.textContent).not.toContain("111.11");
    expect(signed.textContent).not.toMatch(/111\.11|222\.22/);
  });

  it("refuses an answer about another scenario, however well-formed", async () => {
    stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: withRule(STRETCH, calculated("222.22", "USD")) },
        [STRETCH]: { hang: true },
        [SIGNED]: { hang: true },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const baseline = await settledSection("Baseline");

    expect(within(baseline).getByText(READ_UNREADABLE)).toBeVisible();
    expect(baseline.textContent).not.toContain("222.22");
  });

  it("aborts the cards' reads when another project is selected, and not the new project's", async () => {
    const fetchMock = stubBackend({
      reads: { [BASELINE]: { hang: true }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true }, [LEAN]: { hang: true } },
    });

    render(<ProjectListScreen />);
    await openProject("Aurora migration");
    await waitFor(() => expect(termsCalls(fetchMock, SIGNED, "GET")).toHaveLength(1));
    const auroraSignals = [BASELINE, STRETCH, SIGNED].map((id) => readSignal(fetchMock, id));
    // Before: live. A signal already aborted here would make the assertion below vacuous.
    expect(auroraSignals.map((signal) => signal.aborted)).toEqual([false, false, false]);

    await openProject("Helios rollout");
    await waitFor(() => expect(termsCalls(fetchMock, LEAN, "GET")).toHaveLength(1));

    expect(auroraSignals.map((signal) => signal.aborted)).toEqual([true, true, true]);
    expect(readSignal(fetchMock, LEAN).aborted).toBe(false);
    // And the abort is not reported as a failure on the card that replaced them.
    expect(within(section("Lean team")).getByText("Loading commercial terms.")).toBeVisible();
  });

  it("aborts a card's read when the screen unmounts", async () => {
    const fetchMock = stubBackend({
      reads: { [BASELINE]: { hang: true }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
    });

    const { unmount } = render(<ProjectListScreen />);
    await openProject();
    await waitFor(() => expect(termsCalls(fetchMock, BASELINE, "GET")).toHaveLength(1));
    const signal = readSignal(fetchMock, BASELINE);
    expect(signal.aborted).toBe(false);

    unmount();

    expect(signal.aborted).toBe(true);
  });

  it("does nothing further with a save answered after its card has gone, and renders it when the card is still there", async () => {
    // QA (SC-4-06): the write's continuation outlives the card — the POST is deliberately not
    // aborted (a write already sent is not undone by abandoning it). What must not happen when its
    // 201 lands on a card that is gone is anything observable: no further request, no error logged,
    // no crash. React 18 turns a state update on an unmounted component into a silent no-op, so the
    // `mounted` guard alone is not what this observes — the request count is (the CatalogScreen
    // R-03 defect: a save's continuation starting reads no cleanup can abort).
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const gone = heldWrite();
    const fetchMock = stubBackend({
      reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
      writes: { [BASELINE]: gone.answer },
    });

    const { unmount } = render(<ProjectListScreen />);
    await openProject();
    await settledSection("Baseline");
    fireEvent.click(setButton("Baseline") as HTMLElement);
    await waitFor(() => expect(termsCalls(fetchMock, BASELINE, "POST")).toHaveLength(1));
    const requestsBeforeLeaving = fetchMock.mock.calls.length;

    unmount();
    await act(async () => {
      gone.release({ status: 201, body: withRule(BASELINE, calculated("555.55", "PLN")) });
    });
    await act(async () => {});

    expect(fetchMock.mock.calls.length).toBe(requestsBeforeLeaving);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();

    // The contrast, through the identical sequence with the card still mounted: the same 201 is
    // rendered, so the silence above is because the card was gone, not because the answer is lost.
    vi.unstubAllGlobals();
    const kept = heldWrite();
    stubBackend({
      reads: { [BASELINE]: { status: 200, body: noRule(BASELINE) }, [STRETCH]: { hang: true }, [SIGNED]: { hang: true } },
      writes: { [BASELINE]: kept.answer },
    });
    render(<ProjectListScreen />);
    await openProject();
    await settledSection("Baseline");
    fireEvent.click(setButton("Baseline") as HTMLElement);
    await act(async () => {
      kept.release({ status: 201, body: withRule(BASELINE, calculated("555.55", "PLN")) });
    });
    expect(within(section("Baseline")).getByText("Revenue: 555.55 PLN")).toBeVisible();
    expect(within(section("Baseline")).getByText(SAVED)).toHaveFocus();
  });

  it("shows each scenario's revenue in the running application, and aborts its read when the rail leaves the screen", async () => {
    const fetchMock = stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: withRule(BASELINE, calculated("12345678901234567.89", "PLN")) },
        [STRETCH]: { status: 200, body: noRule(STRETCH) },
        [SIGNED]: { hang: true },
      },
    });

    render(<App />);
    const main = screen.getByRole("main");
    fireEvent.click(await within(main).findByRole("button", { name: "Aurora migration" }));

    expect(
      within(await settledSection("Baseline")).getByText("Revenue: 12345678901234567.89 PLN"),
    ).toBeVisible();
    await settledSection("Stretch");
    // The action sits on the card whose read said "no rule", and on no other.
    expect(setButton("Stretch")).toBeEnabled();
    expect(setButton("Baseline")).toBeNull();

    const pending = readSignal(fetchMock, SIGNED);
    expect(pending.aborted).toBe(false);

    fireEvent.click(
      within(screen.getByRole("navigation", { name: "Sections" })).getByRole("button", {
        name: "Roles & rates",
      }),
    );

    expect(pending.aborted).toBe(true);
    expect(screen.queryByRole("region", { name: "Commercial terms" })).toBeNull();
  });
});

// --- Reviewer R-01 (SC-4-06) ---------------------------------------------------------------------

/** A decimal the formatter cannot read, in the two spellings the review named: not a number at all,
 * and a locale's decimal comma — both strings, so a type check alone lets them through. */
const NOT_DECIMALS = ["abc", "1,00"] as const;

describe("R-01 — an amount or rate that is not a decimal is stopped at the network boundary, not by the screen's crash", () => {
  // Through the running application, so that the render boundary is really there: a malformed value
  // that slipped past the shape check would throw in `formatMoneyString`, and `ScreenErrorBoundary`
  // would replace the whole project list — every card, not this section (ADR-0010, point 2).
  async function openInApp() {
    render(<App />);
    const main = screen.getByRole("main");
    fireEvent.click(await within(main).findByRole("button", { name: "Aurora migration" }));
  }

  function expectScreenAlive() {
    expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
    expect(within(card("Baseline")).getByRole("heading", { name: "Baseline" })).toBeVisible();
    expect(within(card("Baseline")).getByText("Status: Draft")).toBeVisible();
  }

  for (const amount of NOT_DECIMALS) {
    it(`reads a calculated revenue of "${amount}" as this section's unreadable answer, and the other cards keep theirs`, async () => {
      stubBackend({
        reads: {
          [BASELINE]: { status: 200, body: withRule(BASELINE, calculated(amount, "PLN")) },
          [STRETCH]: { status: 200, body: withRule(STRETCH, calculated("5.00", "PLN")) },
          [SIGNED]: { hang: true },
        },
      });

      await openInApp();
      const terms = await settledSection("Baseline");

      expect(within(terms).getByText(READ_UNREADABLE)).toBeVisible();
      expect(terms.textContent).not.toContain("Revenue:");
      expect(terms.textContent).not.toContain(amount);
      expectScreenAlive();
      expect(within(await settledSection("Stretch")).getByText("Revenue: 5.00 PLN")).toBeVisible();
    });

    it(`reads a rate window whose selling rate is "${amount}" as this section's unreadable answer`, async () => {
      const assumptions: RevenueAssumptionsRead = {
        ...TM_ASSUMPTIONS,
        rate_windows: [{ ...TM_ASSUMPTIONS.rate_windows[0], default_selling_rate: amount }],
      };
      stubBackend({
        reads: {
          [BASELINE]: {
            status: 200,
            body: withRule(BASELINE, { ...calculated("5.00", "PLN"), assumptions_used: assumptions }),
          },
          [STRETCH]: { status: 200, body: withRule(STRETCH, calculated("5.00", "PLN")) },
          [SIGNED]: { hang: true },
        },
      });

      await openInApp();
      const terms = await settledSection("Baseline");

      expect(within(terms).getByText(READ_UNREADABLE)).toBeVisible();
      expect(terms.textContent).not.toContain(amount);
      expectScreenAlive();
      expect(within(await settledSection("Stretch")).getByText("Revenue: 5.00 PLN")).toBeVisible();
    });

    it(`ends a save whose 201 carries a revenue of "${amount}" as unresolved, without taking the screen down`, async () => {
      // The server committed the rule; its answer cannot be read. The same check as the GET (ADR-0009,
      // addendum 2026-09-23) turns that into the unresolved save — not into a crash that would hide
      // every card, this save's outcome among them.
      stubBackend({
        reads: {
          [BASELINE]: { status: 200, body: noRule(BASELINE) },
          [STRETCH]: { status: 200, body: withRule(STRETCH, calculated("5.00", "PLN")) },
          [SIGNED]: { hang: true },
        },
        writes: { [BASELINE]: { status: 201, body: withRule(BASELINE, calculated(amount, "PLN")) } },
      });

      await openInApp();
      await settledSection("Baseline");
      fireEvent.click(setButton("Baseline") as HTMLElement);

      const terms = section("Baseline");
      expect(await within(terms).findByText(SAVE_UNRESOLVED_UNREADABLE_ANSWER)).toBeVisible();
      expect(within(terms).queryByText(SAVED)).toBeNull();
      expect(terms.textContent).not.toContain("Revenue:");
      expectScreenAlive();
      expect(within(await settledSection("Stretch")).getByText("Revenue: 5.00 PLN")).toBeVisible();
    });
  }

  it("still renders a well-formed calculated revenue and its windows in the same application — the refusal above is about the grammar", async () => {
    // Contrast: the identical path with legal decimals, including a rate with more places than it
    // is shown with, is rendered and not refused.
    stubBackend({
      reads: {
        [BASELINE]: { status: 200, body: withRule(BASELINE, calculated("1.00", "PLN")) },
        [STRETCH]: { status: 200, body: withRule(STRETCH, calculated("5.00", "PLN")) },
        [SIGNED]: { hang: true },
      },
    });

    await openInApp();
    const terms = await settledSection("Baseline");

    expect(within(terms).getByText("Revenue: 1.00 PLN")).toBeVisible();
    expect(within(terms).getByText("2026-01-01 – 2026-06-30: 150.01 PLN")).toBeVisible();
    expect(within(terms).queryByText(READ_UNREADABLE)).toBeNull();
    expectScreenAlive();
  });
});
