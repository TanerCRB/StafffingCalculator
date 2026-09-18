import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CALLER_ID_HEADER, REQUEST_TIMEOUT_MS } from "../../api/client";
import type { ProjectListItem } from "../../api/contracts/projects";
import { ProjectListScreen } from "./ProjectListScreen";

// Fixtures. Two projects with *different* scenario sets, and two drafts with *different* gaps:
// identical fixtures would pass just as happily against a screen rendering a hardcoded string.

const AURORA: ProjectListItem = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Aurora migration",
  client: "Northwind",
  delivery_period: { start: "2026-01-01", end: "2026-12-31" },
  reporting_currency: "EUR",
  description: "Core platform migration.",
  status: "Active",
  scenarios: [
    {
      id: "aaaaaaaa-0000-0000-0000-000000000001",
      name: "Baseline",
      status: "Draft",
      missing_inputs: ["working_calendar", "target_margin_percent"],
      ready_for_approval: false,
      target_margin_percent: null,
    },
    {
      id: "aaaaaaaa-0000-0000-0000-000000000002",
      name: "Aggressive ramp-up",
      status: "Draft",
      missing_inputs: ["start_date", "full_time_hours_per_week", "currency"],
      ready_for_approval: false,
      // Deliberately a value a JS float cannot hold: Number("1.005") is 1.00499999999999989…, so
      // a float path renders 1.00% where the backend's ROUND_HALF_UP gives 1.01%. This pins the
      // call site to the decimal-string formatter, not just the formatter's own unit test.
      target_margin_percent: "1.005",
    },
    {
      id: "aaaaaaaa-0000-0000-0000-000000000003",
      name: "Signed plan",
      status: "Approved",
      missing_inputs: [],
      ready_for_approval: true,
      target_margin_percent: "12.500",
    },
  ],
};

const HELIOS: ProjectListItem = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Helios rollout",
  client: "Contoso",
  delivery_period: { start: "2025-03-01", end: "2025-11-30" },
  reporting_currency: "PLN",
  description: "Retail rollout, archived after handover.",
  status: "Archived",
  scenarios: [
    {
      id: "bbbbbbbb-0000-0000-0000-000000000001",
      name: "Lean team",
      status: "Draft",
      missing_inputs: ["currency"],
      ready_for_approval: false,
      target_margin_percent: "9.000",
    },
  ],
};

/** A project the API returned with no scenarios at all — not a loading state, not an error. */
const VESTA: ProjectListItem = {
  id: "33333333-3333-3333-3333-333333333333",
  name: "Vesta discovery",
  client: "Fabrikam",
  delivery_period: { start: "2026-05-01", end: "2026-09-30" },
  reporting_currency: "USD",
  description: "Discovery phase, nothing planned yet.",
  status: "Active",
  scenarios: [],
};

const ROW_ACTION_LABELS = ["View", "Edit", "Copy", "Archive", "Add scenario"];

function stubProjectListResponse(projects: ProjectListItem[]) {
  const fetchMock = vi
    .fn()
    .mockResolvedValue({ ok: true, status: 200, json: async () => ({ projects }) });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function stubFailedResponse(status: number) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: false, status });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Body rows only — the header row is not a project. */
async function projectRows() {
  const table = await screen.findByRole("table");
  return within(table).getAllByRole("row").slice(1);
}

function rowFor(rows: HTMLElement[], projectName: string) {
  const row = rows.find((candidate) => candidate.textContent?.includes(projectName));
  if (!row) {
    throw new Error(`No project row for ${projectName}`);
  }
  return within(row);
}

/** The scenario block a heading belongs to, so assertions cannot accidentally read a sibling
 * scenario's text. */
function scenarioCard(scenarioName: string) {
  const heading = screen.getByRole("heading", { name: scenarioName });
  const card = heading.closest("li");
  if (!card) {
    throw new Error(`No scenario card for ${scenarioName}`);
  }
  return card;
}

async function selectProject(projectName: string) {
  fireEvent.click(await screen.findByRole("button", { name: projectName }));
}

describe("ProjectListScreen", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("renders one row per project returned by the API, with name, client, delivery period and status", async () => {
    stubProjectListResponse([AURORA, HELIOS]);

    render(<ProjectListScreen />);

    const rows = await projectRows();
    expect(rows).toHaveLength(2);

    const aurora = rowFor(rows, "Aurora migration");
    expect(aurora.getByText("Northwind")).toBeInTheDocument();
    expect(aurora.getByText("2026-01-01 – 2026-12-31")).toBeInTheDocument();
    expect(aurora.getByText("Active")).toBeInTheDocument();

    const helios = rowFor(rows, "Helios rollout");
    expect(helios.getByText("Contoso")).toBeInTheDocument();
    expect(helios.getByText("2025-03-01 – 2025-11-30")).toBeInTheDocument();
    expect(helios.getByText("Archived")).toBeInTheDocument();
  });

  it("renders an empty-state message and no project rows when the API returns an empty list", async () => {
    stubProjectListResponse([]);

    render(<ProjectListScreen />);

    expect(await screen.findByText("No projects to show.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("row")).toHaveLength(0);
    expect(screen.queryByText("Aurora migration")).not.toBeInTheDocument();
    for (const label of ROW_ACTION_LABELS) {
      expect(screen.queryByRole("button", { name: new RegExp(`^${label} `) })).toBeNull();
    }
  });

  it("exposes view, edit, copy, archive and add-scenario controls for every project row as named controls reachable by keyboard", async () => {
    const fetchMock = stubProjectListResponse([AURORA, HELIOS]);

    render(<ProjectListScreen />);
    await projectRows();

    for (const project of [AURORA, HELIOS]) {
      for (const label of ROW_ACTION_LABELS) {
        const control = screen.getByRole("button", { name: `${label} ${project.name}` });

        // Reachable by keyboard: a real control in the tab order that can hold focus — not an
        // icon or a CSS class that only looks like a button.
        expect(control.tabIndex).toBe(0);
        control.focus();
        expect(control).toHaveFocus();

        // Rendered, announced as not yet actionable, and wired to nothing (SC-1-02..04).
        expect(control).toHaveAttribute("aria-disabled", "true");
        expect(control).toHaveAttribute("title", expect.stringContaining("Not implemented yet"));
        fireEvent.click(control);
      }
    }

    // Clicking every control on every row triggered no request beyond the initial list read.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders a prompt instead of scenario details until a project is selected", async () => {
    stubProjectListResponse([AURORA, HELIOS]);

    render(<ProjectListScreen />);
    await projectRows();

    expect(screen.getByText("Select a project to see its scenarios.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Baseline" })).toBeNull();
    expect(screen.queryByText("Status: Draft")).toBeNull();

    await selectProject("Aurora migration");

    expect(screen.queryByText("Select a project to see its scenarios.")).toBeNull();
    expect(screen.getByRole("heading", { name: "Baseline" })).toBeInTheDocument();
  });

  it("lists the scenarios of the selected project with their status as text, not colour alone", async () => {
    stubProjectListResponse([AURORA, HELIOS]);

    render(<ProjectListScreen />);
    await projectRows();

    await selectProject("Aurora migration");

    expect(within(scenarioCard("Baseline")).getByText("Status: Draft")).toBeInTheDocument();
    expect(
      within(scenarioCard("Aggressive ramp-up")).getByText("Status: Draft"),
    ).toBeInTheDocument();
    expect(within(scenarioCard("Signed plan")).getByText("Status: Approved")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Lean team" })).toBeNull();

    // A different project has a different scenario set — the panel follows the selection.
    await selectProject("Helios rollout");

    expect(within(scenarioCard("Lean team")).getByText("Status: Draft")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Baseline" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Aggressive ramp-up" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Signed plan" })).toBeNull();
  });

  it("marks a draft scenario as not ready for approval and names its missing inputs", async () => {
    stubProjectListResponse([AURORA, HELIOS]);

    render(<ProjectListScreen />);
    await projectRows();

    await selectProject("Aurora migration");

    const baseline = within(scenarioCard("Baseline"));
    const rampUp = within(scenarioCard("Aggressive ramp-up"));

    expect(baseline.getByText("Not ready for approval")).toBeInTheDocument();
    expect(rampUp.getByText("Not ready for approval")).toBeInTheDocument();

    // Readable labels, never the backend's attribute names.
    const baselineGaps = baseline.getByText(/^Missing inputs:/);
    const rampUpGaps = rampUp.getByText(/^Missing inputs:/);
    expect(baselineGaps).toHaveTextContent("Missing inputs: Working calendar, Target margin");
    expect(rampUpGaps).toHaveTextContent(
      "Missing inputs: Start date, Full-time hours per week, Currency",
    );
    expect(screen.queryByText(/working_calendar|target_margin_percent|full_time_hours_per_week/))
      .toBeNull();

    // Two drafts with different gaps must not render the same sentence.
    expect(baselineGaps.textContent).not.toEqual(rampUpGaps.textContent);

    // The complete, approved scenario is the contrast: ready, with nothing listed as missing.
    const signed = within(scenarioCard("Signed plan"));
    expect(signed.getByText("Ready for approval")).toBeInTheDocument();
    expect(signed.queryByText(/^Missing inputs:/)).toBeNull();
  });

  // --- Supporting tests (not acceptance criteria) -------------------------------------------

  it("sends the placeholder caller identity header with the project list request", async () => {
    const fetchMock = stubProjectListResponse([AURORA]);

    render(<ProjectListScreen />);
    await projectRows();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)[CALLER_ID_HEADER]).toBeDefined();
  });

  it("renders an archived project exactly as the API returned it, filtering nothing of its own", async () => {
    stubProjectListResponse([HELIOS]);

    render(<ProjectListScreen />);

    const rows = await projectRows();
    expect(rows).toHaveLength(1);
    const helios = rowFor(rows, "Helios rollout");
    expect(helios.getByText("Archived")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add scenario Helios rollout" }),
    ).toBeInTheDocument();
  });

  it("says a selected project has no scenarios yet, distinctly from no project being selected", async () => {
    // The contrast is two-sided on purpose. "Nothing to show here" and "you have not chosen
    // anything yet" are different answers, and a screen that gave the same one for both would
    // still satisfy a test that only looked for the absence of scenario headings.
    stubProjectListResponse([VESTA, AURORA]);

    render(<ProjectListScreen />);
    await projectRows();

    await selectProject("Vesta discovery");

    expect(screen.getByText("This project has no scenarios yet.")).toBeInTheDocument();
    expect(screen.getByText("Scenarios of Vesta discovery")).toBeInTheDocument();
    expect(screen.queryByText("Select a project to see its scenarios.")).toBeNull();
    expect(screen.queryByRole("listitem")).toBeNull();
    // No invented placeholder scenario, and no leakage from the other project in the response.
    expect(screen.queryByRole("heading", { name: "Baseline" })).toBeNull();
    expect(screen.queryByText(/Not ready for approval|Ready for approval/)).toBeNull();
    expect(screen.queryByText(/^Missing inputs:/)).toBeNull();
    expect(screen.queryByText(/^Target margin:/)).toBeNull();

    // One changed element — a project that does have scenarios — and the message is gone.
    await selectProject("Aurora migration");

    expect(screen.queryByText("This project has no scenarios yet.")).toBeNull();
    expect(screen.getByRole("heading", { name: "Baseline" })).toBeInTheDocument();
  });

  it("renders no project rows and no action controls when the API denies the request", async () => {
    stubFailedResponse(403);

    render(<ProjectListScreen />);

    expect(
      await screen.findByText("You do not have permission to view projects."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByText("Select a project to see its scenarios.")).toBeNull();
  });

  it("renders a percentage through the shared formatter, never a raw decimal string", async () => {
    stubProjectListResponse([AURORA]);

    render(<ProjectListScreen />);
    await projectRows();
    await selectProject("Aurora migration");

    expect(
      within(scenarioCard("Signed plan")).getByText("Target margin: 12.50%"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/12\.500/)).toBeNull();
    // A scenario without a target margin says so; it never renders a blank cell or 0%.
    expect(
      within(scenarioCard("Baseline")).getByText("Target margin: Not provided"),
    ).toBeInTheDocument();
  });

  it("rounds a displayed percentage the way the backend does, not the way a JS float does", async () => {
    stubProjectListResponse([AURORA]);

    render(<ProjectListScreen />);
    await projectRows();
    await selectProject("Aurora migration");

    // "1.005" through Number() would render "1.00%" here (ADR-0002: the value stays a string).
    expect(
      within(scenarioCard("Aggressive ramp-up")).getByText("Target margin: 1.01%"),
    ).toBeInTheDocument();
  });

  it("stops waiting and states the failure when the request never completes", async () => {
    vi.useFakeTimers();
    // A backend that accepts the connection and never answers: the promise never settles.
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise<never>(() => {})));

    render(<ProjectListScreen />);
    expect(screen.getByText("Loading projects…")).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(REQUEST_TIMEOUT_MS);
    });

    expect(
      screen.getByText("Projects could not be loaded — request timed out."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Loading projects…")).toBeNull();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("stops waiting when the response headers arrive but the body never finishes", async () => {
    vi.useFakeTimers();
    // The harder half of a hung backend: a 200 with headers, then a stream that stalls — a
    // truncated upstream or a connection killed mid-deploy. Arriving headers are not an answer.
    const readBody = vi.fn().mockReturnValue(new Promise<never>(() => {}));
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: readBody }),
    );

    render(<ProjectListScreen />);

    // Let the header phase finish *before* the clock moves, so the client is genuinely stuck in
    // the body read when the deadline hits. Without this the timeout would fire while the
    // request was still in its first phase, and the test would pass without touching the body
    // case at all.
    await act(async () => {
      await Promise.resolve();
    });
    expect(readBody).toHaveBeenCalled();
    expect(screen.getByText("Loading projects…")).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(REQUEST_TIMEOUT_MS);
    });

    expect(
      screen.getByText("Projects could not be loaded — request timed out."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Loading projects…")).toBeNull();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
