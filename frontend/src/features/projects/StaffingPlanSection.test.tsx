import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  StaffingAbsence,
  StaffingAllocation,
  StaffingPositionRead,
} from "../../api/contracts/staffing";
import type { ProjectListItem, ScenarioStatus } from "../../api/contracts/projects";
import { ProjectListScreen } from "./ProjectListScreen";
import { SET_TIME_AND_MATERIAL } from "./commercialTermsText";
import {
  ABSENCE_BUDGET_STATE_MESSAGES,
  ABSENCES_EMPTY,
  ALLOCATIONS_EMPTY,
  CATALOG_NAME_UNAVAILABLE,
  DERIVED_CAPACITY_STATE_MESSAGES,
  STAFFING_EMPTY,
  STAFFING_REFUSED,
  STAFFING_UNREADABLE,
} from "./staffingPlanText";

/**
 * SC-3-04 (Issue #135) — the scenario's staffing plan, as a third section of its card on the
 * project list (gate 1: Q1/Q3 = the recommended options, Q2 = accepted risk, no countermeasure).
 * One `describe` per criterion, the same convention `ScenarioResults.test.tsx` uses.
 *
 * Every test mounts `ProjectListScreen` (K-06's isolation test mounts nothing less, either): a
 * card is not this section alone, and K-06 is a claim about the rest of the card while this
 * section fails.
 */

// --- Fixtures --------------------------------------------------------------------------------------

const BASELINE = "aaaaaaaa-0000-0000-0000-000000000001";
const STRETCH = "aaaaaaaa-0000-0000-0000-000000000002";

const ROLE_ID = "cccccccc-0000-0000-0000-000000000001";
const SENIORITY_ID = "cccccccc-0000-0000-0000-000000000002";
const LOCATION_ID = "cccccccc-0000-0000-0000-000000000003";
const ENGAGEMENT_TYPE_ID = "cccccccc-0000-0000-0000-000000000004";
const ABSENCE_TYPE_ID = "cccccccc-0000-0000-0000-000000000005";

const ROLE_NAME = "Backend developer";
const SENIORITY_NAME = "Senior";
const LOCATION_NAME = "Warsaw";
const ENGAGEMENT_TYPE_NAME = "B2B";
const ABSENCE_TYPE_NAME = "Annual leave";

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
  scenarios: [scenario(BASELINE, "Baseline"), scenario(STRETCH, "Stretch")],
};

function allocation(overrides: Partial<StaffingAllocation> = {}): StaffingAllocation {
  return {
    id: "eeeeeeee-0000-0000-0000-000000000001",
    period_month: "2026-01-01",
    availability_hours: "160.00",
    planned_allocation_hours: "150.00",
    billable_hours: "140.00",
    derived_capacity_hours: "155.00",
    derived_capacity_state: "resolved",
    absence_budget_hours: "8.00",
    absence_budget_state: "resolved",
    ...overrides,
  };
}

function absence(overrides: Partial<StaffingAbsence> = {}): StaffingAbsence {
  return {
    id: "ffffffff-0000-0000-0000-000000000001",
    absence_type_id: ABSENCE_TYPE_ID,
    start_date: "2026-03-02",
    end_date: "2026-03-06",
    ...overrides,
  };
}

function position(overrides: Partial<StaffingPositionRead> = {}): StaffingPositionRead {
  return {
    id: "dddddddd-0000-0000-0000-000000000001",
    role_id: ROLE_ID,
    seniority_id: SENIORITY_ID,
    location_id: LOCATION_ID,
    engagement_type_id: ENGAGEMENT_TYPE_ID,
    headcount: 2,
    start_date: "2026-01-01",
    end_date: null,
    updated_at: "2026-01-01T00:00:00.123456+00:00",
    allocations: [allocation()],
    absences: [],
    ...overrides,
  };
}

// --- A backend, by path and method -------------------------------------------------------------

type Answer =
  | { readonly status: number; readonly body?: unknown; readonly gate?: Promise<void> }
  | { readonly hang: true };

const STAFFING_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/staffing-positions$/;
const CATALOG_DIMENSION_PATH = /^\/catalog\/dimensions\/([^/]+)$/;
const TERMS_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/commercial-terms$/;
const RESULTS_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/results$/;

interface Backend {
  readonly projects?: ProjectListItem[];
  /** The `GET …/staffing-positions` answer per scenario id — a function to answer the n-th read
   * differently. A scenario with none configured hangs. */
  readonly staffing?: Record<string, Answer | ((call: number) => Answer)>;
  /** Answers every one of the five name-resolving reads (four dimensions + absence types) the
   * same way — the merged single mechanism gate 1 decided on (K-04, Q6). Defaults to the fixture
   * names above. */
  readonly catalog?: Answer;
  /** Overrides `catalog`/`defaultCatalogAnswer` for one or more of the five name-resolving reads
   * individually — the fixture for the QA contrast that a **partial** catalogue failure (four of
   * five reads succeed, one fails) still ends in the single merged `unavailable` state for all
   * five, not four resolved names beside one gap. Any dimension not named here still answers
   * through `defaultCatalogAnswer`. */
  readonly catalogPartial?: Partial<
    Record<"roles" | "seniorities" | "locations" | "engagement-types" | "absence-types", Answer>
  >;
  /** Set to render a "Set Time & Material" button on the sibling commercial-terms section, for the
   * K-07 contrast. Left hanging (no rule ever resolved) otherwise. */
  readonly commercialTermsAnswer?: Answer;
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

function defaultCatalogAnswer(path: string): Answer {
  if (path === "/catalog/absence-types") {
    return { status: 200, body: { absence_types: [{ id: ABSENCE_TYPE_ID, name: ABSENCE_TYPE_NAME }] } };
  }
  const dimensionMatch = CATALOG_DIMENSION_PATH.exec(path);
  const dimension = dimensionMatch?.[1];
  const entriesByDimension: Record<string, unknown[]> = {
    roles: [{ id: ROLE_ID, name: ROLE_NAME, updated_at: "2026-01-01T00:00:00Z" }],
    seniorities: [{ id: SENIORITY_ID, name: SENIORITY_NAME, updated_at: "2026-01-01T00:00:00Z" }],
    locations: [{ id: LOCATION_ID, name: LOCATION_NAME, updated_at: "2026-01-01T00:00:00Z" }],
    "engagement-types": [
      { id: ENGAGEMENT_TYPE_ID, name: ENGAGEMENT_TYPE_NAME, updated_at: "2026-01-01T00:00:00Z" },
    ],
    vendors: [],
  };
  return { status: 200, body: { entries: dimension === undefined ? [] : entriesByDimension[dimension] } };
}

/** The fixture's own answer for `path`, held open behind `gate` — used to force a genuine overlap
 * window between two concurrently mounted sections' catalogue reads (R-01), rather than relying on
 * both effects merely firing in the same microtask. */
function delayedCatalogAnswer(path: string, gate: Promise<void>): Answer {
  const answer = defaultCatalogAnswer(path);
  return { ...answer, gate };
}

function stubBackend(backend: Backend) {
  const staffingReadCounts = new Map<string, number>();
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    const method = init.method ?? "GET";
    let answer: Answer | undefined;
    if (path === "/health") {
      answer = { status: 200, body: { status: "ok" } };
    } else if (path === "/projects") {
      answer = { status: 200, body: { projects: backend.projects ?? [PROJECT] } };
    } else if (path === "/catalog/rates") {
      answer = { status: 200, body: { rates: [], total: 0 } };
    } else if (RESULTS_PATH.test(path)) {
      // The results section beside this one is not under test — left open forever.
      answer = { hang: true };
    } else if (TERMS_PATH.test(path)) {
      answer = backend.commercialTermsAnswer ?? { hang: true };
    } else if (path === "/catalog/absence-types" || CATALOG_DIMENSION_PATH.test(path)) {
      const dimensionKey: keyof NonNullable<Backend["catalogPartial"]> | undefined =
        path === "/catalog/absence-types" ? "absence-types" : (CATALOG_DIMENSION_PATH.exec(path)?.[1] as
          | keyof NonNullable<Backend["catalogPartial"]>
          | undefined);
      const override = dimensionKey === undefined ? undefined : backend.catalogPartial?.[dimensionKey];
      answer = override ?? backend.catalog ?? defaultCatalogAnswer(path);
    } else {
      const staffingMatch = STAFFING_PATH.exec(path);
      if (staffingMatch !== null && method === "GET") {
        const scenarioId = staffingMatch[2];
        const count = (staffingReadCounts.get(scenarioId) ?? 0) + 1;
        staffingReadCounts.set(scenarioId, count);
        const configured = backend.staffing?.[scenarioId];
        answer = typeof configured === "function" ? configured(count) : (configured ?? { hang: true });
      }
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
    if (answer.gate !== undefined) {
      // Held open until the test releases it — the concurrency window R-01's coalescing exists
      // for, made real rather than assumed from same-tick effect timing.
      return answer.gate.then(() => response(answer.status, answer.body));
    }
    return Promise.resolve(response(answer.status, answer.body));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// --- Screen helpers ------------------------------------------------------------------------------

async function openProject() {
  fireEvent.click(await screen.findByRole("button", { name: "Aurora migration" }));
}

function card(scenarioName: string): HTMLElement {
  const item = screen.getByRole("heading", { name: scenarioName }).closest("li");
  if (item === null) {
    throw new Error(`No scenario card for ${scenarioName}`);
  }
  return item;
}

function staffingSection(scenarioName: string): HTMLElement {
  return within(card(scenarioName)).getByRole("region", { name: "Staffing plan" });
}

/** Waits until the staffing read has left its loading state, whatever it then says. The
 * catalogue-name read may still be pending, loading or unavailable independently (K-04). */
async function settledStaffing(scenarioName: string): Promise<HTMLElement> {
  await waitFor(() =>
    expect(within(staffingSection(scenarioName)).queryByText("Loading staffing plan.")).toBeNull(),
  );
  return staffingSection(scenarioName);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- K-01 -----------------------------------------------------------------------------------------

describe("K-01 — hours and headcount render only through the shared hours formatter", () => {
  it("rounds ROUND_HALF_UP on the .005 boundary for each of the three independent hours values, matching the backend", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: {
            positions: [
              position({
                headcount: 3,
                allocations: [
                  allocation({
                    // `Number("100.005").toFixed(2)` is "100.00", `Number("2.675").toFixed(2)` is
                    // "2.67" — a call-site `toFixed()` would fail this (money.test.ts already
                    // proves the same two values for money/percentages).
                    availability_hours: "100.005",
                    planned_allocation_hours: "2.675",
                    billable_hours: "140.00",
                  }),
                ],
              }),
            ],
          },
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    expect(within(staffing).getByText("Headcount: 3")).toBeVisible();
    expect(within(staffing).getByText("Availability: 100.01 h")).toBeVisible();
    expect(within(staffing).getByText("Planned allocation: 2.68 h")).toBeVisible();
    expect(within(staffing).getByText("Billable: 140.00 h")).toBeVisible();
    // Changing one of the three changed only its own line — the other two are still their own,
    // independently correct, values.
    expect(within(staffing).queryByText(/Availability: 100.00 h/)).toBeNull();
    expect(within(staffing).queryByText(/Planned allocation: 2.67 h/)).toBeNull();
  });
});

// --- K-02 -----------------------------------------------------------------------------------------

describe("K-02 — derived_capacity_state's two states never collapse into a silent 0.00", () => {
  it("renders a number for 'resolved' and this field's own named state for 'no_calendar', on the same allocation row of two positions", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: {
            positions: [
              position({
                id: "dddddddd-0000-0000-0000-000000000001",
                allocations: [allocation({ derived_capacity_state: "resolved", derived_capacity_hours: "120.00" })],
              }),
              position({
                id: "dddddddd-0000-0000-0000-000000000002",
                allocations: [allocation({ derived_capacity_state: "no_calendar", derived_capacity_hours: "n/a" })],
              }),
            ],
          },
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    expect(within(staffing).getByText("Derived capacity: 120.00 h")).toBeVisible();
    expect(within(staffing).getByText(DERIVED_CAPACITY_STATE_MESSAGES.no_calendar)).toBeVisible();
    expect(staffing.querySelectorAll('[data-derived-capacity-state="resolved"]')).toHaveLength(1);
    expect(staffing.querySelectorAll('[data-derived-capacity-state="no_calendar"]')).toHaveLength(1);
    expect(within(staffing).queryByText("Derived capacity: 0.00 h")).toBeNull();
    expect(within(staffing).queryByText("Derived capacity:")).toBeNull();
  });
});

// --- K-03 -----------------------------------------------------------------------------------------

describe("K-03 — each of absence_budget_state's four named states gets its own, distinct message", () => {
  it("renders four different messages for four positions in four different non-computable states", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: {
            positions: (["no_budget", "no_statutory_leave_type", "no_calendar"] as const).map(
              (state, index) =>
                position({
                  id: `dddddddd-0000-0000-0000-00000000000${index + 1}`,
                  allocations: [allocation({ absence_budget_state: state, absence_budget_hours: "n/a" })],
                }),
            ),
          },
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    const messages = (["no_budget", "no_statutory_leave_type", "no_calendar"] as const).map((state) =>
      within(staffing).getByText(ABSENCE_BUDGET_STATE_MESSAGES[state]).textContent,
    );
    expect(new Set(messages).size).toBe(3);
    for (const message of messages) {
      expect(message).not.toBe("0.00 h");
      expect(message).not.toBe("");
    }
  });

  it("renders the real number for 'resolved', even when it is exactly 0.00 — never confused with a state's own message", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: {
            positions: [
              position({
                allocations: [allocation({ absence_budget_state: "resolved", absence_budget_hours: "0.00" })],
              }),
            ],
          },
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    expect(within(staffing).getByText("Leave budget: 0.00 h")).toBeVisible();
    expect(staffing.querySelector('[data-absence-budget-state="resolved"]')).not.toBeNull();
    for (const state of ["no_budget", "no_statutory_leave_type", "no_calendar"] as const) {
      expect(within(staffing).queryByText(ABSENCE_BUDGET_STATE_MESSAGES[state])).toBeNull();
    }
  });
});

// --- K-04 -----------------------------------------------------------------------------------------

describe("K-04 — the five catalogue identifiers resolve through their own CATALOG_READ, independent of STAFFING_READ", () => {
  it("resolves all five identifiers to names when both reads succeed", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: { positions: [position({ absences: [absence()] })] },
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    expect(await within(staffing).findByText(`Role: ${ROLE_NAME}`)).toBeVisible();
    expect(within(staffing).getByText(`Seniority: ${SENIORITY_NAME}`)).toBeVisible();
    expect(within(staffing).getByText(`Location: ${LOCATION_NAME}`)).toBeVisible();
    expect(within(staffing).getByText(`Engagement type: ${ENGAGEMENT_TYPE_NAME}`)).toBeVisible();
    expect(await within(staffing).findByText(new RegExp(`^${ABSENCE_TYPE_NAME}:`))).toBeVisible();
  });

  it("still renders headcount, hours and named states when the catalogue read is denied — identifiers show a distinguishable state, never the raw UUID", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: { positions: [position({ absences: [absence()] })] },
        },
      },
      catalog: { status: 403, body: { detail: "Forbidden" } },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    const roleLine = await within(staffing).findByText(`Role: ${CATALOG_NAME_UNAVAILABLE}`);
    expect(roleLine).toBeVisible();
    expect(within(staffing).getByText(`Seniority: ${CATALOG_NAME_UNAVAILABLE}`)).toBeVisible();
    expect(within(staffing).getByText(`Location: ${CATALOG_NAME_UNAVAILABLE}`)).toBeVisible();
    expect(within(staffing).getByText(`Engagement type: ${CATALOG_NAME_UNAVAILABLE}`)).toBeVisible();
    expect(within(staffing).getByText(new RegExp(`^${CATALOG_NAME_UNAVAILABLE}:`))).toBeVisible();
    // Never the raw id, on any of the five.
    expect(staffing.textContent).not.toContain(ROLE_ID);
    expect(staffing.textContent).not.toContain(ABSENCE_TYPE_ID);
    // The rest of the row is unaffected by the catalogue denial.
    expect(within(staffing).getByText("Headcount: 2")).toBeVisible();
    expect(within(staffing).getByText("Availability: 160.00 h")).toBeVisible();
    expect(within(staffing).getByText("Derived capacity: 155.00 h")).toBeVisible();
  });

  it("treats a partial catalogue failure (four of five reads succeed, one fails) as the same merged unavailable state for all five — never four resolved names beside one gap", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: { positions: [position({ absences: [absence()] })] },
        },
      },
      // Only `engagement-types` fails; `roles`/`seniorities`/`locations`/`absence-types` still
      // answer through `defaultCatalogAnswer` with the fixture names above. `Promise.all` (K-04,
      // Q6) means one failing identifier is documented to withhold all five — this is the test
      // that actually forces that direction, rather than trusting the docstring.
      catalogPartial: {
        "engagement-types": { status: 500, body: { detail: "Internal error" } },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    // The one dimension that failed:
    expect(await within(staffing).findByText(`Engagement type: ${CATALOG_NAME_UNAVAILABLE}`)).toBeVisible();
    // The four dimensions whose *own* read succeeded — still merged into the same unavailable
    // state, not resolved to their fixture names. A mutation that resolved these four while only
    // the failing one fell back would satisfy every assertion above alone and must fail here.
    expect(within(staffing).getByText(`Role: ${CATALOG_NAME_UNAVAILABLE}`)).toBeVisible();
    expect(within(staffing).getByText(`Seniority: ${CATALOG_NAME_UNAVAILABLE}`)).toBeVisible();
    expect(within(staffing).getByText(`Location: ${CATALOG_NAME_UNAVAILABLE}`)).toBeVisible();
    expect(within(staffing).getByText(new RegExp(`^${CATALOG_NAME_UNAVAILABLE}:`))).toBeVisible();
    expect(within(staffing).queryByText(`Role: ${ROLE_NAME}`)).toBeNull();
    expect(within(staffing).queryByText(`Seniority: ${SENIORITY_NAME}`)).toBeNull();
    expect(within(staffing).queryByText(`Location: ${LOCATION_NAME}`)).toBeNull();
    expect(within(staffing).queryByText(new RegExp(`^${ABSENCE_TYPE_NAME}:`))).toBeNull();
    // The rest of the row is unaffected by the catalogue denial, exactly as a full failure.
    expect(within(staffing).getByText("Headcount: 2")).toBeVisible();
  });

  it("shows a loading placeholder, never the raw UUID, while the catalogue read has not answered yet", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: { positions: [position()] },
        },
      },
      catalog: { hang: true },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    expect(within(staffing).getByText("Role: Loading role…")).toBeVisible();
    expect(within(staffing).getByText("Seniority: Loading seniority…")).toBeVisible();
    expect(staffing.textContent).not.toContain(ROLE_ID);
    // The staffing figures do not wait for the catalogue read.
    expect(within(staffing).getByText("Headcount: 2")).toBeVisible();
  });
});

// --- K-05 -----------------------------------------------------------------------------------------

describe("K-05 — 403 and 404 render identically, and neither is confused with a legitimate empty list", () => {
  it("renders the same message and the same state marker for a 403 and a 404", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: { status: 403, body: { detail: "Forbidden" } },
        [STRETCH]: { status: 404, body: { detail: "Scenario staffing not found." } },
      },
    });

    render(<ProjectListScreen />);
    await openProject();

    const forbidden = await settledStaffing("Baseline");
    const notFound = await settledStaffing("Stretch");

    expect(within(forbidden).getByText(STAFFING_REFUSED)).toBeVisible();
    expect(within(notFound).getByText(STAFFING_REFUSED)).toBeVisible();
    expect(forbidden.querySelector("[data-staffing-read-failure]")?.getAttribute("data-staffing-read-failure")).toBe(
      "unavailable",
    );
    expect(notFound.querySelector("[data-staffing-read-failure]")?.getAttribute("data-staffing-read-failure")).toBe(
      "unavailable",
    );
  });

  it("renders a distinct, named 'no positions' state for a 200 carrying an empty list — never treated as a read failure", async () => {
    stubBackend({
      staffing: { [BASELINE]: { status: 200, body: { positions: [] } } },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    expect(within(staffing).getByText(STAFFING_EMPTY)).toBeVisible();
    expect(staffing.querySelector("[data-staffing-read-failure]")).toBeNull();
    expect(within(staffing).queryByText(STAFFING_REFUSED)).toBeNull();
  });
});

// --- K-06 -----------------------------------------------------------------------------------------

describe("K-06 — a broken payload ends in this section's own unreadable state, never the screen's error boundary, and never another card", () => {
  it("keeps a malformed allocation inside this section, leaving the card's heading/status and the sibling card intact", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: {
            positions: [
              position({
                // `derived_capacity_state` names "resolved" but the hours field carries the
                // sentinel — a pairing `isStaffingAllocationShape` refuses.
                allocations: [
                  allocation({ derived_capacity_state: "resolved", derived_capacity_hours: "n/a" }),
                ],
              }),
            ],
          },
        },
        [STRETCH]: { status: 200, body: { positions: [position()] } },
      },
    });

    render(<ProjectListScreen />);
    await openProject();

    const broken = await settledStaffing("Baseline");
    expect(within(broken).getByText(STAFFING_UNREADABLE)).toBeVisible();
    expect(within(card("Baseline")).getByRole("heading", { name: "Baseline" })).toBeVisible();
    expect(within(card("Baseline")).getByText("Status: Draft")).toBeVisible();

    const healthy = await settledStaffing("Stretch");
    expect(within(healthy).getByText("Headcount: 2")).toBeVisible();
    expect(within(healthy).queryByText(STAFFING_UNREADABLE)).toBeNull();
  });

  it("renders an empty allocations list and an empty absences list as legal, empty, non-error states — the nested-list contrast to the top-level empty list of K-05", async () => {
    stubBackend({
      staffing: {
        [BASELINE]: {
          status: 200,
          body: { positions: [position({ allocations: [], absences: [] })] },
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    expect(within(staffing).getByText(ALLOCATIONS_EMPTY)).toBeVisible();
    expect(within(staffing).getByText(ABSENCES_EMPTY)).toBeVisible();
    expect(within(staffing).queryByText(STAFFING_UNREADABLE)).toBeNull();
    // The position itself still rendered — an empty nested list is not the position missing.
    expect(within(staffing).getByText("Headcount: 2")).toBeVisible();
  });
});

// --- R-01 (Reviewer, gate 2) ------------------------------------------------------------------------

describe("R-01 — the five catalogue-name reads coalesce across concurrently mounted sections instead of fanning out per card", () => {
  it("calls fetch exactly once per catalogue path, even though two scenario cards mount the same five reads at the same time", async () => {
    // Both scenario cards of one project (Baseline, Stretch) mount their own `StaffingPlanSection`
    // instance together — exactly the N-scenarios-per-project shape the reviewer's finding is
    // about, made real without needing a second project fixture.
    let releaseGate: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      releaseGate = resolve;
    });

    const fetchMock = stubBackend({
      staffing: {
        [BASELINE]: { status: 200, body: { positions: [position()] } },
        [STRETCH]: {
          status: 200,
          body: { positions: [position({ id: "dddddddd-0000-0000-0000-000000000002" })] },
        },
      },
      // All five catalogue reads held open behind the same gate — both cards' reads are
      // genuinely in flight together, not merely issued in the same microtask.
      catalogPartial: {
        roles: delayedCatalogAnswer("/catalog/dimensions/roles", gate),
        seniorities: delayedCatalogAnswer("/catalog/dimensions/seniorities", gate),
        locations: delayedCatalogAnswer("/catalog/dimensions/locations", gate),
        "engagement-types": delayedCatalogAnswer("/catalog/dimensions/engagement-types", gate),
        "absence-types": delayedCatalogAnswer("/catalog/absence-types", gate),
      },
    });

    render(<ProjectListScreen />);
    await openProject();

    // Both cards' staffing reads (not gated) settle first — their catalogue reads are already
    // in flight, held by the gate, by the time this resolves.
    await settledStaffing("Baseline");
    await settledStaffing("Stretch");

    const pathsOf = (calls: unknown[][]) =>
      calls
        .map((call) => new URL(call[0] as string).pathname)
        .filter((path) => path.startsWith("/catalog/dimensions/") || path === "/catalog/absence-types");

    // The concurrency window is real: both cards' reads to every one of the five paths are already
    // outstanding before either is allowed to answer.
    const inFlightPaths = pathsOf(fetchMock.mock.calls as unknown[][]);
    expect(new Set(inFlightPaths).size).toBe(5);

    releaseGate();
    await within(await settledStaffing("Baseline")).findByText(`Role: ${ROLE_NAME}`);
    await within(await settledStaffing("Stretch")).findByText(`Role: ${ROLE_NAME}`);

    const finalPaths = pathsOf(fetchMock.mock.calls as unknown[][]);
    const countByPath = new Map<string, number>();
    for (const path of finalPaths) {
      countByPath.set(path, (countByPath.get(path) ?? 0) + 1);
    }

    // Five distinct paths, and not one of them called twice — the fan-out the reviewer measured
    // (two cards × five reads = ten calls) would fail this with every count at 2.
    expect(countByPath.size).toBe(5);
    for (const [path, count] of countByPath) {
      expect(count, `expected exactly one fetch to ${path}, got ${count}`).toBe(1);
    }
  });

  it("still resolves independently for a later, non-concurrent read — coalescing never answers a caller with a stale result", async () => {
    // The contrast K-04's existing "loading" test does not cover: a second read of the same
    // dimension, issued once the first has already settled, must not be silently answered from a
    // discarded cache entry — it is a fresh request, exactly as before this fix.
    stubBackend({
      staffing: {
        [BASELINE]: { status: 200, body: { positions: [position()] } },
      },
    });

    const { unmount } = render(<ProjectListScreen />);
    await openProject();
    await within(await settledStaffing("Baseline")).findByText(`Role: ${ROLE_NAME}`);
    unmount();

    // A second, independent mount — after the first has fully settled and gone — reads roles
    // again rather than reusing anything left behind by the first.
    const fetchMock = stubBackend({
      staffing: {
        [BASELINE]: { status: 200, body: { positions: [position()] } },
      },
    });
    render(<ProjectListScreen />);
    await openProject();
    await within(await settledStaffing("Baseline")).findByText(`Role: ${ROLE_NAME}`);

    const rolesCalls = (fetchMock.mock.calls as unknown[][]).filter(
      (call) => new URL(call[0] as string).pathname === "/catalog/dimensions/roles",
    );
    expect(rolesCalls).toHaveLength(1);
  });
});

// --- K-07 -----------------------------------------------------------------------------------------

describe("K-07 — the section renders no interactive element, under any state", () => {
  it("offers no button/textbox/spinbutton for a healthy Draft scenario, unlike the sibling commercial-terms section", async () => {
    stubBackend({
      staffing: { [BASELINE]: { status: 200, body: { positions: [position({ absences: [absence()] })] } } },
      commercialTermsAnswer: {
        status: 200,
        body: {
          scenario_id: BASELINE,
          scenario_status: "Draft",
          commercial_terms: null,
          revenue: {
            state: "no_commercial_terms",
            amount: "n/a",
            currency: null,
            assumptions_used: {
              model_type: null,
              hours_source: "billable_hours",
              vendor_axis: "internal",
              rate_source: "live_catalog",
              rate_windows: [],
              unresolved_months: [],
              currencies: [],
            },
            expected_state: "not_applicable",
            expected_amount: "n/a",
            category_revenues: [],
          },
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const staffing = await settledStaffing("Baseline");

    // The contrast: the sibling section on the very same, still-Draft card does offer a button.
    expect(await within(card("Baseline")).findByRole("button", { name: new RegExp(SET_TIME_AND_MATERIAL) })).toBeVisible();

    expect(within(staffing).queryAllByRole("button")).toHaveLength(0);
    expect(within(staffing).queryAllByRole("textbox")).toHaveLength(0);
    expect(within(staffing).queryAllByRole("spinbutton")).toHaveLength(0);
  });

  it("offers no button/textbox/spinbutton for an Approved scenario, and none for every non-computable state at once", async () => {
    const approvedProject: ProjectListItem = {
      ...PROJECT,
      scenarios: [scenario(BASELINE, "Baseline", "Approved"), scenario(STRETCH, "Stretch")],
    };
    stubBackend({
      projects: [approvedProject],
      staffing: {
        [BASELINE]: {
          status: 200,
          body: {
            positions: [
              position({
                allocations: [
                  allocation({
                    derived_capacity_state: "no_calendar",
                    derived_capacity_hours: "n/a",
                    absence_budget_state: "no_statutory_leave_type",
                    absence_budget_hours: "n/a",
                  }),
                ],
              }),
            ],
          },
        },
        [STRETCH]: { status: 403, body: { detail: "Forbidden" } },
      },
      catalog: { status: 403, body: { detail: "Forbidden" } },
    });

    render(<ProjectListScreen />);
    await openProject();

    const approved = await settledStaffing("Baseline");
    expect(within(approved).queryAllByRole("button")).toHaveLength(0);
    expect(within(approved).queryAllByRole("textbox")).toHaveLength(0);
    expect(within(approved).queryAllByRole("spinbutton")).toHaveLength(0);

    const refused = await settledStaffing("Stretch");
    expect(within(refused).queryAllByRole("button")).toHaveLength(0);
    expect(within(refused).queryAllByRole("textbox")).toHaveLength(0);
    expect(within(refused).queryAllByRole("spinbutton")).toHaveLength(0);
  });
});
