import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { vi } from "vitest";

import type {
  RevenueAssumptionsRead,
  ScenarioCommercialTerms,
} from "../../api/contracts/commercialTerms";
import type { ProjectListItem, ScenarioListItem, ScenarioStatus } from "../../api/contracts/projects";
import { ProjectListScreen } from "./ProjectListScreen";
import {
  DUPLICATE_CONFLICT,
  DUPLICATE_DENIED,
  DUPLICATE_FAILED,
  DUPLICATE_LABEL,
  DUPLICATE_NOT_FOUND,
  DUPLICATE_UNRESOLVED_UNREADABLE_ANSWER,
  DUPLICATED,
} from "./duplicateScenarioText";

/**
 * SC-6-03 (Issue #95) — the "Duplicate" control a scenario card offers, consuming SC-6-01's
 * `POST .../duplicate` (ADR-0009, addendum 2026-09-24). One `describe` per acceptance criterion.
 *
 * Every test goes through the mounted `ProjectListScreen`, not `DuplicateScenarioControl` alone:
 * K-06 is a claim about the *other* project's own array, and K-02's contrast is a claim about a
 * sibling section on the very same card — neither is observable on the control by itself.
 */

// --- Fixtures ------------------------------------------------------------------------------------

const AURORA_ID = "11111111-1111-1111-1111-111111111111";
const HELIOS_ID = "22222222-2222-2222-2222-222222222222";
const SOLO_ID = "33333333-3333-3333-3333-333333333333";

const BASELINE_ID = "aaaaaaaa-0000-0000-0000-000000000001";
const SIGNED_ID = "aaaaaaaa-0000-0000-0000-000000000003";
const LEAN_ID = "bbbbbbbb-0000-0000-0000-000000000001";
const ONLY_PLAN_ID = "cccccccc-0000-0000-0000-000000000001";

function scenario(id: string, name: string, status: ScenarioStatus): ScenarioListItem {
  return {
    id,
    name,
    status,
    missing_inputs: [],
    ready_for_approval: status === "Approved",
    target_margin_percent: null,
  };
}

const AURORA: ProjectListItem = {
  id: AURORA_ID,
  name: "Aurora migration",
  client: "Northwind",
  delivery_period: { start: "2026-01-01", end: "2026-12-31" },
  reporting_currency: "EUR",
  description: "Core platform migration.",
  status: "Active",
  scenarios: [scenario(BASELINE_ID, "Baseline", "Draft"), scenario(SIGNED_ID, "Signed plan", "Approved")],
};

const HELIOS: ProjectListItem = {
  id: HELIOS_ID,
  name: "Helios rollout",
  client: "Contoso",
  delivery_period: { start: "2025-03-01", end: "2025-11-30" },
  reporting_currency: "PLN",
  description: "Retail rollout.",
  status: "Active",
  scenarios: [scenario(LEAN_ID, "Lean team", "Draft")],
};

/** A project with exactly one scenario — the fixture K-01 asks for literally. */
const SOLO: ProjectListItem = {
  id: SOLO_ID,
  name: "Solo project",
  client: "Fabrikam",
  delivery_period: { start: "2026-05-01", end: "2026-09-30" },
  reporting_currency: "USD",
  description: "One scenario, nothing else.",
  status: "Active",
  scenarios: [scenario(ONLY_PLAN_ID, "Only plan", "Draft")],
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

function noRuleTerms(scenarioId: string, status: ScenarioStatus): ScenarioCommercialTerms {
  return {
    scenario_id: scenarioId,
    scenario_status: status,
    commercial_terms: null,
    revenue: { state: "no_commercial_terms", amount: "n/a", currency: null, assumptions_used: NO_ASSUMPTIONS, expected_state: "not_applicable", expected_amount: "n/a", category_revenues: [] },
  };
}

// --- A backend, by path and method ---------------------------------------------------------------

type Answer = { readonly status: number; readonly body?: unknown } | { readonly hang: true };

const DUPLICATE_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/duplicate$/;
const TERMS_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/commercial-terms$/;

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
  readonly projects: ProjectListItem[];
  /** The `GET .../commercial-terms` answer per scenario id. Unspecified: hangs, so that section
   * stays loading and never interferes with an assertion about the Duplicate control. */
  readonly commercialTerms?: Record<string, Answer>;
  /** The `POST .../duplicate` answer per scenario id. */
  readonly duplicates?: Record<string, Answer>;
}

function stubBackend(backend: Backend) {
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    const method = init.method ?? "GET";

    if (path === "/projects" && method === "GET") {
      return Promise.resolve(response(200, { projects: backend.projects }));
    }

    const termsMatch = TERMS_PATH.exec(path);
    if (termsMatch !== null && method === "GET") {
      const answer = backend.commercialTerms?.[termsMatch[2]];
      if (answer === undefined || "hang" in answer) {
        return new Promise<never>(() => {});
      }
      return Promise.resolve(response(answer.status, answer.body));
    }

    const dupMatch = DUPLICATE_PATH.exec(path);
    if (dupMatch !== null && method === "POST") {
      const answer = backend.duplicates?.[dupMatch[2]];
      if (answer === undefined) {
        throw new Error(`No duplicate answer stubbed for scenario ${dupMatch[2]}`);
      }
      if ("hang" in answer) {
        return new Promise<never>(() => {});
      }
      return Promise.resolve(response(answer.status, answer.body));
    }

    throw new Error(`No answer stubbed for ${method} ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubBackend>;

// --- Screen helpers ------------------------------------------------------------------------------

async function openProject(name: string) {
  fireEvent.click(await screen.findByRole("button", { name }));
}

async function projectRows() {
  const table = await screen.findByRole("table");
  return within(table).getAllByRole("row").slice(1);
}

function detailsPanel(): HTMLElement {
  return screen.getByRole("region", { name: "Scenario details" });
}

/** One heading per scenario card — the panel's only level-3 headings. */
function scenarioHeadings(): HTMLElement[] {
  return within(detailsPanel()).getAllByRole("heading", { level: 3 });
}

function card(scenarioName: string): HTMLElement {
  const heading = screen.getByRole("heading", { name: scenarioName });
  const item = heading.closest("li");
  if (item === null) {
    throw new Error(`No scenario card for ${scenarioName}`);
  }
  return item;
}

function duplicateButton(scenarioName: string): HTMLElement {
  return within(card(scenarioName)).getByRole("button", { name: `${DUPLICATE_LABEL} ${scenarioName}` });
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

function duplicateResponseFor(id: string, name: string, status: ScenarioStatus = "Draft"): ScenarioListItem {
  return { id, name, status, missing_inputs: [], ready_for_approval: false, target_margin_percent: null };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- K-01 -----------------------------------------------------------------------------------------

describe("K-01 — a successful duplicate adds a new row built solely from the response, with no refetch", () => {
  it("adds a new row matching the 201 body, without re-reading the project list", async () => {
    const duplicate = duplicateResponseFor(
      "dddddddd-0000-0000-0000-000000000001",
      "Only plan (copy)",
      "Draft",
    );
    const fetchMock: FetchMock = stubBackend({
      projects: [SOLO],
      duplicates: { [ONLY_PLAN_ID]: { status: 201, body: duplicate } },
    });

    render(<ProjectListScreen />);
    await openProject(SOLO.name);
    expect(scenarioHeadings()).toHaveLength(1);

    fireEvent.click(duplicateButton("Only plan"));

    expect(await screen.findByRole("heading", { name: "Only plan (copy)" })).toBeInTheDocument();
    expect(within(card("Only plan (copy)")).getByText("Status: Draft")).toBeInTheDocument();
    expect(scenarioHeadings()).toHaveLength(2);

    const projectListCalls = fetchMock.mock.calls.filter(
      ([url]) => new URL(url as string).pathname === "/projects",
    );
    expect(projectListCalls).toHaveLength(1);
  });

  it("adds no row and ends in a named unresolved state when the 201 body is not a valid ScenarioListItem", async () => {
    // `status` outside "Draft"|"Approved" — a `201` this client cannot read.
    const invalidBody = {
      id: "dddddddd-0000-0000-0000-000000000002",
      name: "Bad copy",
      status: "Archived",
      missing_inputs: [],
      ready_for_approval: false,
      target_margin_percent: null,
    };
    stubBackend({
      projects: [SOLO],
      duplicates: { [ONLY_PLAN_ID]: { status: 201, body: invalidBody } },
    });

    render(<ProjectListScreen />);
    await openProject(SOLO.name);

    fireEvent.click(duplicateButton("Only plan"));

    expect(
      await within(card("Only plan")).findByText(DUPLICATE_UNRESOLVED_UNREADABLE_ANSWER),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Bad copy" })).toBeNull();
    expect(scenarioHeadings()).toHaveLength(1);
  });
});

// --- R-01 (reviewer finding) ------------------------------------------------------------------------

describe("R-01 — a successful duplicate gives a discoverable, focused confirmation", () => {
  it("shows a role=status confirmation message and moves focus to it", async () => {
    const duplicate = duplicateResponseFor(
      "dddddddd-0000-0000-0000-000000000007",
      "Only plan (copy)",
      "Draft",
    );
    stubBackend({
      projects: [SOLO],
      duplicates: { [ONLY_PLAN_ID]: { status: 201, body: duplicate } },
    });

    render(<ProjectListScreen />);
    await openProject(SOLO.name);

    fireEvent.click(duplicateButton("Only plan"));

    const confirmation = await within(card("Only plan")).findByText(DUPLICATED);
    expect(confirmation).toHaveAttribute("role", "status");
    expect(confirmation).toHaveFocus();
  });
});

// --- K-02 -----------------------------------------------------------------------------------------

describe("K-02 — Duplicate is offered for both a draft and an approved scenario", () => {
  it("renders Duplicate, keyboard-reachable, for Draft and Approved alike — unlike Set Time & Material, hidden only for Approved", async () => {
    stubBackend({
      projects: [AURORA],
      commercialTerms: {
        [BASELINE_ID]: { status: 200, body: noRuleTerms(BASELINE_ID, "Draft") },
        [SIGNED_ID]: { status: 200, body: noRuleTerms(SIGNED_ID, "Approved") },
      },
    });

    render(<ProjectListScreen />);
    await openProject(AURORA.name);

    for (const name of ["Baseline", "Signed plan"]) {
      const control = duplicateButton(name);
      expect(control).toBeEnabled();
      expect(control.tabIndex).toBe(0);
      control.focus();
      expect(control).toHaveFocus();
    }

    // Wait for both commercial-terms reads to settle, so the contrast below is meaningful — during
    // "Loading commercial terms." neither control renders, which would make the absence trivial.
    await waitFor(() => {
      expect(within(card("Baseline")).queryByText("Loading commercial terms.")).toBeNull();
      expect(within(card("Signed plan")).queryByText("Loading commercial terms.")).toBeNull();
    });

    // The contrast, in this same file, on these same two fixtures (K-02's highest-risk mistake).
    expect(
      within(card("Baseline")).getByRole("button", { name: "Set Time & Material for Baseline" }),
    ).toBeInTheDocument();
    expect(
      within(card("Signed plan")).queryByRole("button", {
        name: "Set Time & Material for Signed plan",
      }),
    ).toBeNull();

    // Duplicate itself is unaffected by either status.
    expect(within(card("Baseline")).getByRole("button", { name: "Duplicate Baseline" })).toBeVisible();
    expect(
      within(card("Signed plan")).getByRole("button", { name: "Duplicate Signed plan" }),
    ).toBeVisible();
  });
});

// --- K-03 -----------------------------------------------------------------------------------------

describe("K-03 — the new row's status comes from the response, never from the source's own state", () => {
  it("keeps an Approved source unchanged and renders the new row as the Draft the response names", async () => {
    const duplicate = duplicateResponseFor(
      "dddddddd-0000-0000-0000-000000000003",
      "Signed plan (copy)",
      "Draft",
    );
    stubBackend({
      projects: [AURORA],
      duplicates: { [SIGNED_ID]: { status: 201, body: duplicate } },
    });

    render(<ProjectListScreen />);
    await openProject(AURORA.name);

    fireEvent.click(duplicateButton("Signed plan"));

    expect(await screen.findByRole("heading", { name: "Signed plan (copy)" })).toBeInTheDocument();
    // The new row is Draft — if it had been built from `sourceScenario.status` it would read
    // "Approved" here, since the source card (asserted right below) still is.
    expect(within(card("Signed plan (copy)")).getByText("Status: Draft")).toBeInTheDocument();
    expect(within(card("Signed plan")).getByText("Status: Approved")).toBeInTheDocument();
  });
});

// --- K-04 -----------------------------------------------------------------------------------------

describe("K-04 — 403 and 404 end in two separate, named denial states", () => {
  it("shows a named denial and adds no row on 403", async () => {
    stubBackend({ projects: [SOLO], duplicates: { [ONLY_PLAN_ID]: { status: 403 } } });

    render(<ProjectListScreen />);
    await openProject(SOLO.name);
    fireEvent.click(duplicateButton("Only plan"));

    expect(await within(card("Only plan")).findByText(DUPLICATE_DENIED)).toBeInTheDocument();
    expect(scenarioHeadings()).toHaveLength(1);
  });

  it("shows a named, textually distinct denial and adds no row on 404", async () => {
    stubBackend({ projects: [SOLO], duplicates: { [ONLY_PLAN_ID]: { status: 404 } } });

    render(<ProjectListScreen />);
    await openProject(SOLO.name);
    fireEvent.click(duplicateButton("Only plan"));

    expect(await within(card("Only plan")).findByText(DUPLICATE_NOT_FOUND)).toBeInTheDocument();
    expect(scenarioHeadings()).toHaveLength(1);
    expectPairwiseDistinct([DUPLICATE_DENIED, DUPLICATE_NOT_FOUND]);
  });

  it("adds a row and shows neither denial message on 201 — the contrast", async () => {
    const duplicate = duplicateResponseFor("dddddddd-0000-0000-0000-000000000004", "Only plan (copy)");
    stubBackend({ projects: [SOLO], duplicates: { [ONLY_PLAN_ID]: { status: 201, body: duplicate } } });

    render(<ProjectListScreen />);
    await openProject(SOLO.name);
    fireEvent.click(duplicateButton("Only plan"));

    expect(await screen.findByRole("heading", { name: "Only plan (copy)" })).toBeInTheDocument();
    expect(screen.queryByText(DUPLICATE_DENIED)).toBeNull();
    expect(screen.queryByText(DUPLICATE_NOT_FOUND)).toBeNull();
  });
});

// --- K-05 -----------------------------------------------------------------------------------------

describe("K-05 — 409 leaves the list exactly as it was, with its own named conflict message", () => {
  it("adds no row, not even transiently, and shows a named conflict message distinguishable from 403/404/failed", async () => {
    stubBackend({ projects: [SOLO], duplicates: { [ONLY_PLAN_ID]: { status: 409 } } });

    render(<ProjectListScreen />);
    await openProject(SOLO.name);

    fireEvent.click(duplicateButton("Only plan"));
    // Immediately after the click — before the mocked response has been awaited — there is still
    // no optimistic row (this action never renders one; see gate-1 note, K-05 contrast).
    expect(scenarioHeadings()).toHaveLength(1);

    expect(await within(card("Only plan")).findByText(DUPLICATE_CONFLICT)).toBeInTheDocument();
    expect(scenarioHeadings()).toHaveLength(1);

    expectPairwiseDistinct([DUPLICATE_CONFLICT, DUPLICATE_DENIED, DUPLICATE_NOT_FOUND, DUPLICATE_FAILED]);
  });

  it("increases the row count by exactly one on 201 — the contrast", async () => {
    const duplicate = duplicateResponseFor("dddddddd-0000-0000-0000-000000000005", "Only plan (copy)");
    stubBackend({ projects: [SOLO], duplicates: { [ONLY_PLAN_ID]: { status: 201, body: duplicate } } });

    render(<ProjectListScreen />);
    await openProject(SOLO.name);
    expect(scenarioHeadings()).toHaveLength(1);

    fireEvent.click(duplicateButton("Only plan"));

    await screen.findByRole("heading", { name: "Only plan (copy)" });
    expect(scenarioHeadings()).toHaveLength(2);
  });
});

// --- K-06 -----------------------------------------------------------------------------------------

describe("K-06 — the new row lands in the correct project's own scenario array", () => {
  it("adds the duplicate to the source's own project only, leaving the other project and the project count unchanged", async () => {
    // The project being duplicated from is deliberately NOT first in the response, so an
    // insertion keyed by array index rather than by the response's own `project_id` context would
    // land the new row on the wrong project.
    const duplicate = duplicateResponseFor("dddddddd-0000-0000-0000-000000000006", "Baseline (copy)");
    stubBackend({
      projects: [HELIOS, AURORA],
      duplicates: { [BASELINE_ID]: { status: 201, body: duplicate } },
    });

    render(<ProjectListScreen />);
    expect(await projectRows()).toHaveLength(2);

    await openProject(AURORA.name);
    expect(scenarioHeadings()).toHaveLength(2);

    fireEvent.click(duplicateButton("Baseline"));

    expect(await screen.findByRole("heading", { name: "Baseline (copy)" })).toBeInTheDocument();
    expect(scenarioHeadings()).toHaveLength(3);
    // No new project was created.
    expect(await projectRows()).toHaveLength(2);

    // Project B's own array is untouched.
    await openProject(HELIOS.name);
    expect(scenarioHeadings()).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "Baseline (copy)" })).toBeNull();
  });
});
