import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CALLER_ID_HEADER, REQUEST_TIMEOUT_MS } from "../../api/client";
import type { ProjectListItem } from "../../api/contracts/projects";
import { SCREEN_CRASH_MESSAGE } from "../../shell/ScreenErrorBoundary";
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

/**
 * What the API is allowed to send: the backend caps a name at 200 characters and requires no
 * whitespace inside it, and the scenario name in the response contract has no cap at all. A
 * single unbroken word is therefore a legal answer, not an edge case someone has to type on
 * purpose — and the place it breaks is the layout, silently, on someone else's screen.
 */
const NO_SPACES = `Programme-${"Konsolidacja".repeat(15)}-Faza2`;
const UNBROKEN: ProjectListItem = {
  id: "44444444-4444-4444-4444-444444444444",
  name: NO_SPACES,
  client: `Klient-${"Handlowy".repeat(10)}`,
  delivery_period: { start: "2026-02-01", end: "2026-08-31" },
  reporting_currency: "EUR",
  description: "One word, no break opportunities.",
  status: "Active",
  scenarios: [
    {
      id: "cccccccc-0000-0000-0000-000000000001",
      name: `Wariant-${"Optymistyczny".repeat(12)}`,
      status: "Draft",
      missing_inputs: ["currency"],
      ready_for_approval: false,
      target_margin_percent: "4.000",
    },
  ],
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

    // The other side of the contrast drawn by the two "denied" tests below. An empty list is a
    // list: the server answered "you have none", so the screen still offers the list's own
    // controls. Being denied a list and being told the list is empty are different answers, and
    // the toolbar is the observable that separates them — without this assertion the guard could
    // be narrowed to `projects.length > 0` and the suite would stay green.
    expect(screen.getByRole("searchbox", { name: "Search projects" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Add project" })).toBeVisible();
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

  // --- Restyle (layout pass over the same data) ----------------------------------------------

  it("shows search, filters and add-project as reachable controls that are wired to nothing", async () => {
    const fetchMock = stubProjectListResponse([AURORA, HELIOS]);

    render(<ProjectListScreen />);
    await projectRows();

    const search = screen.getByRole("searchbox", { name: "Search projects" });
    const filters = screen.getByRole("button", { name: "Filters" });
    const addProject = screen.getByRole("button", { name: "Add project" });

    for (const control of [search, filters, addProject]) {
      // Visible and in the tab order — the shape of the product ahead of its implementation
      // (F-13), not a hidden control.
      expect(control).toBeVisible();
      expect(control.tabIndex).toBe(0);
      control.focus();
      expect(control).toHaveFocus();
      expect(control).toHaveAttribute("aria-disabled", "true");
      expect(control).toHaveAttribute("title", expect.stringContaining("Not implemented yet"));
    }

    // The box does not accept input it cannot honour. `readOnly` is the affordance, not the
    // mechanism: what makes typing harmless is that there is no change handler and no state behind
    // it (asserted below). Both are pinned, because a box a user can type into that silently
    // ignores every keystroke is a different, worse lie than one that refuses the keystroke.
    expect(search).toHaveAttribute("readonly");

    fireEvent.click(filters);
    fireEvent.click(addProject);
    // Typing in the box filters nothing: the list is the API's answer, never a client-side
    // subset (Issue #3, out of scope 2). Both projects are still there, including the one whose
    // name does not contain the typed text.
    fireEvent.change(search, { target: { value: "Aurora" } });

    const rows = await projectRows();
    expect(rows).toHaveLength(2);
    expect(rowFor(rows, "Helios rollout").getByText("Contoso")).toBeInTheDocument();
    // Nothing was fetched, created or re-read beyond the initial list request.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders no search, filters or add-project controls when the API denies the request", async () => {
    // The contrast to the test above: the same toolbar that is present for a 200 is absent for a
    // 403 — a denied screen offers no actions at all, rather than actions over hidden data.
    stubFailedResponse(403);

    render(<ProjectListScreen />);

    expect(
      await screen.findByText("You do not have permission to view projects."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Filters" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add project" })).toBeNull();
  });

  it("names a server error as a server error and offers no controls over a list it never got", async () => {
    // Two claims the 403 test above cannot make, because 403 is the one status the screen special
    // cases. First: a 500 must not be reported as a permission problem — `return { kind: "denied" }`
    // as the fallback of toFailureState passes every other test in this file, and tells the user
    // they lack access to projects they do have access to. Second: "no toolbar" is a property of
    // *not having a list*, not of being denied one; a guard written as `state.kind !== "denied"`
    // would leak the toolbar over an error screen and nothing here would notice.
    stubFailedResponse(500);

    render(<ProjectListScreen />);

    expect(await screen.findByText("Projects could not be loaded.")).toBeInTheDocument();
    expect(screen.queryByText("You do not have permission to view projects.")).toBeNull();
    expect(screen.queryByText("Projects could not be loaded — request timed out.")).toBeNull();

    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Filters" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add project" })).toBeNull();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("Select a project to see its scenarios.")).toBeNull();
  });

  it("renders a long name with no break opportunities whole, leaving the breaking to the stylesheet", async () => {
    // Truncation belongs to CSS, which can be undone by a wider window, a smaller font or a
    // horizontal scroll. A component that shortens the string decides for every one of those
    // cases at once, and the part it drops is gone from the DOM — unreadable, unsearchable, and
    // invisible to the test that only asks whether the name "is in the document".
    stubProjectListResponse([UNBROKEN]);

    render(<ProjectListScreen />);
    const rows = await projectRows();
    expect(UNBROKEN.name.length).toBeGreaterThan(150);

    const nameControl = within(rows[0]).getByRole("button", { name: UNBROKEN.name });
    expect(nameControl.textContent).toBe(UNBROKEN.name);
    expect(within(rows[0]).getByText(UNBROKEN.client).textContent).toBe(UNBROKEN.client);

    await selectProject(UNBROKEN.name);

    const scenario = UNBROKEN.scenarios[0];
    expect(screen.getByRole("heading", { name: scenario.name }).textContent).toBe(scenario.name);
    // No ellipsis anywhere: nothing on this screen decided the user had read enough.
    expect(document.body.textContent).not.toContain("…");
  });

  it("keeps the project status readable as a word inside its colour-coded badge", async () => {
    stubProjectListResponse([AURORA, HELIOS]);

    render(<ProjectListScreen />);
    const rows = await projectRows();

    const active = rowFor(rows, "Aurora migration").getByText("Active");
    const archived = rowFor(rows, "Helios rollout").getByText("Archived");

    // The fill is an extra channel keyed by the server's own label, on top of the word — never
    // instead of it (NF-08). Two different statuses cannot collapse onto one appearance.
    expect(active).toHaveAttribute("data-project-status", "Active");
    expect(archived).toHaveAttribute("data-project-status", "Archived");
    expect(active.getAttribute("data-project-status")).not.toEqual(
      archived.getAttribute("data-project-status"),
    );

    await selectProject("Aurora migration");
    const draft = within(scenarioCard("Baseline")).getByText("Status: Draft");
    const approved = within(scenarioCard("Signed plan")).getByText("Status: Approved");
    expect(draft).toHaveAttribute("data-scenario-status", "Draft");
    expect(approved).toHaveAttribute("data-scenario-status", "Approved");
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
    // A screen that gave up waiting has no list, so it offers no controls over one either.
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Filters" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add project" })).toBeNull();
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

  // --- SC-1-09, K-01: the contract is checked at the network boundary, not at the render one ----

  /** One field removed from a copy of a row — a payload a real JSON body can carry, which
   * `{ ...row, field: undefined }` is not. */
  function without<T extends object>(row: T, field: keyof T): unknown {
    const copy = { ...row };
    delete copy[field];
    return copy;
  }

  /**
   * The three ways a project row can violate the contract without anything looking wrong until it
   * is too late. Each one is a separate run of the same criterion, because each one fails at a
   * different place in the render and a predicate can be weakened to let exactly one through.
   */
  const CONTRACT_VIOLATIONS: readonly { readonly what: string; readonly row: unknown }[] = [
    {
      // Throws `TypeError` on `project.delivery_period.start` — the original defect.
      what: "a row that carries no delivery period",
      row: without(AURORA, "delivery_period"),
    },
    {
      // ADR-0002: the value crosses the boundary as a fixed-point *string*. As a JSON number it
      // reaches `formatPercentString`, whose `.trim()` throws — but only once a project is
      // selected, i.e. in a render triggered by a click, long after the list looked healthy.
      what: "a scenario whose target margin arrives as a number instead of a decimal string",
      row: {
        ...AURORA,
        scenarios: [{ ...AURORA.scenarios[2], target_margin_percent: 12.5 }],
      },
    },
    {
      // Gate-1 follow-up decision 4. Nothing throws on this one at all: the badge's fill is keyed
      // by the label, so an unlisted status renders as an unrecognised word in an unstyled badge —
      // the error that renders correctly, which no boundary can catch by construction.
      what: "a row whose status is not one of the two the contract lists",
      row: { ...AURORA, status: "Deleted" },
    },
  ];

  for (const { what, row } of CONTRACT_VIOLATIONS) {
    it(`renders a named failure state and no project rows when the API sends ${what}`, async () => {
      // The valid row is in the same response on purpose: the whole response is rejected, never
      // the offending row alone. A silently shortened list is indistinguishable from a complete
      // one, which is worse than the blank page this criterion is about.
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: true,
          status: 200,
          json: async () => ({ projects: [row, HELIOS] }),
        }),
      );

      render(<ProjectListScreen />);

      expect(await screen.findByText("Projects could not be loaded.")).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(screen.queryAllByRole("row")).toHaveLength(0);
      expect(screen.queryByText("Helios rollout")).toBeNull();
      expect(screen.queryByText("No projects to show.")).toBeNull();
      // Stopped at the network boundary, so the render boundary is never involved — the ordering
      // ADR-0010 asks for, and the half that makes the two mechanisms two (K-01 vs K-02).
      expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
    });
  }

  it("renders the rows when the same response carries contract-valid rows", async () => {
    // The contrast to the three runs above, on the response they were derived from: the check
    // rejects a broken payload, not every payload. Without this, `isProjectListItemShape` could
    // return `false` unconditionally and the three tests above would all still pass.
    stubProjectListResponse([AURORA, HELIOS]);

    render(<ProjectListScreen />);

    const rows = await projectRows();
    expect(rows).toHaveLength(2);
    expect(screen.queryByText("Projects could not be loaded.")).toBeNull();

    // And the field each violation attacked arrives intact, through the same render path.
    await selectProject("Aurora migration");
    expect(rowFor(rows, "Aurora migration").getByText("2026-01-01 – 2026-12-31")).toBeVisible();
    expect(rowFor(rows, "Aurora migration").getByText("Active")).toBeVisible();
    expect(
      within(scenarioCard("Signed plan")).getByText("Target margin: 12.50%"),
    ).toBeInTheDocument();
  });

  // --- SC-1-09, K-05: leaving the screen ends the read, it does not merely ignore the answer ----

  /** A backend that accepted the connection and has not answered — the state an in-flight read is
   * actually in when somebody navigates away. */
  function stubPendingProjectListRead() {
    const fetchMock = vi.fn().mockReturnValue(new Promise<never>(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  /** The signal `fetch` was actually handed, or a failure that says so. Not `init.signal ?? …`:
   * a missing signal is the defect this criterion is about and must not read as "not aborted". */
  function signalGivenToFetch(
    fetchMock: { readonly mock: { readonly calls: readonly unknown[][] } },
    call = 0,
  ): AbortSignal {
    const [, init] = (fetchMock.mock.calls[call] ?? []) as [string, RequestInit];
    const signal = init?.signal;
    if (!(signal instanceof AbortSignal)) {
      throw new Error(`fetch call ${call} was made without an AbortSignal`);
    }
    return signal;
  }

  it("cancels the in-flight project list read when the screen is left", () => {
    const fetchMock = stubPendingProjectListRead();

    const { unmount } = render(<ProjectListScreen />);

    const signal = signalGivenToFetch(fetchMock);
    // Both halves matter. A signal that is already aborted here would make the assertion after the
    // unmount true for the wrong reason, and the SC-2-04 defect this criterion exists against —
    // a controller built and never aborted — is invisible without the "before" reading.
    expect(signal.aborted).toBe(false);

    unmount();

    expect(signal.aborted).toBe(true);
  });

  it("keeps the read running while the screen stays mounted", async () => {
    // The contrast: cancellation is tied to leaving, not to time passing or to the effect running.
    // Without it, `getProjects(AbortSignal.abort())` would satisfy the test above forever.
    const fetchMock = stubProjectListResponse([AURORA, HELIOS]);

    render(<ProjectListScreen />);

    const signal = signalGivenToFetch(fetchMock);
    expect(await projectRows()).toHaveLength(2);
    expect(signal.aborted).toBe(false);
  });

  /**
   * A backend that never answers, and a `fetch` that behaves like the platform's: it rejects with
   * an `AbortError` when its signal is aborted, and otherwise hangs.
   *
   * `replaceWith`, when given, is the answer the *second* call gets — the one `StrictMode` issues
   * after abandoning the first.
   */
  function stubAbortableProjectListRead(replaceWith?: { readonly projects: ProjectListItem[] }) {
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      return new Promise<unknown>((resolve, reject) => {
        const answer = fetchMock.mock.calls.length === 2 ? replaceWith : undefined;
        init.signal?.addEventListener("abort", () => {
          reject(new DOMException("The operation was aborted.", "AbortError"));
        });
        if (answer !== undefined) {
          queueMicrotask(() => resolve({ ok: true, status: 200, json: async () => answer }));
        }
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("never renders the project list failure message for a read its own screen abandoned", async () => {
    // `StrictMode`, which is how this application actually runs in development (`src/main.tsx`):
    // React mounts, cleans up and mounts again *on the same component*, so the first read is
    // aborted while the screen is very much still there and its rejection lands on a live
    // component. An abort is the application's own decision arriving dressed as an error; reporting
    // it would state a failure of a read nobody was waiting for.
    //
    // The replacement read is left hanging on purpose. With it answering, a screen that *did*
    // report the abort would flash the failure and then be overwritten by the rows — green suite,
    // real defect. Measured: with a resolving second read, dropping the guard changed nothing any
    // assertion could see (developer's own mutation run, 2026-09-22).
    const fetchMock = stubAbortableProjectListRead();

    render(
      <StrictMode>
        <ProjectListScreen />
      </StrictMode>,
    );

    // The situation the assertions below are about actually happened: two reads, the first
    // abandoned, the second still running. Without this the test would pass against a `StrictMode`
    // that never double-mounted.
    expect(fetchMock.mock.calls.length).toBe(2);
    expect(signalGivenToFetch(fetchMock, 0).aborted).toBe(true);
    expect(signalGivenToFetch(fetchMock, 1).aborted).toBe(false);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // Still waiting for the read that replaced it — which is the truth — and saying nothing about
    // permissions, deadlines or failures.
    expect(screen.getByText("Loading projects…")).toBeVisible();
    expect(screen.queryByText("Projects could not be loaded.")).toBeNull();
    expect(screen.queryByText("Projects could not be loaded — request timed out.")).toBeNull();
    expect(screen.queryByText("You do not have permission to view projects.")).toBeNull();
  });

  /**
   * Two reads where the abandoned one answers *last*, and with a different list.
   *
   * An abort cannot un-deliver a response that already arrived: `controller.abort()` reaching a
   * request whose body is already in hand changes nothing about the promise that is about to
   * resolve. The first read can therefore still succeed after the screen walked away from it — and
   * whether its answer is allowed to decide what is on screen is a question `signal.aborted` cannot
   * answer.
   */
  function stubReadsWhereTheAbandonedOneAnswersLast() {
    let releaseAbandoned: (() => void) | undefined;
    const answer = (projects: ProjectListItem[]) => ({
      ok: true,
      status: 200,
      json: async () => ({ projects }),
    });
    // Neither argument is read here: which read this is depends only on the order it arrived in,
    // and the signal is inspected by the test through `fetchMock.mock.calls`.
    const fetchMock = vi.fn(() => {
      if (fetchMock.mock.calls.length === 1) {
        // The read the screen abandons. Held, and released by the test after the other one landed.
        return new Promise<unknown>((resolve) => {
          releaseAbandoned = () => resolve(answer([AURORA]));
        });
      }
      // The read that replaced it, answering straight away with the list that is actually current.
      return Promise.resolve(answer([AURORA, HELIOS]));
    });
    vi.stubGlobal("fetch", fetchMock);
    return { fetchMock, releaseAbandoned: () => releaseAbandoned?.() };
  }

  it("never lets a read the screen abandoned decide what is on screen, not even when it succeeds", async () => {
    // QA, SC-1-09 — the missing half of K-05's guard.
    //
    // `left` guards two callbacks and only the failure one was proven: dropping `if (!left)` from
    // the success path left the whole suite green (QA mutation run, 2026-09-22, 143/143). What
    // survives that mutation is a screen where an abandoned read's answer overwrites the answer of
    // the read that replaced it — rows that were right, replaced by rows that are stale, with no
    // failure message and nothing for anyone to notice. `signal.aborted` says nothing about this:
    // the abort did happen, it simply arrived after the response had.
    const { fetchMock, releaseAbandoned } = stubReadsWhereTheAbandonedOneAnswersLast();

    render(
      <StrictMode>
        <ProjectListScreen />
      </StrictMode>,
    );

    expect(fetchMock.mock.calls.length).toBe(2);
    expect(signalGivenToFetch(fetchMock, 0).aborted).toBe(true);
    // The current answer is on screen.
    expect(await projectRows()).toHaveLength(2);

    // ...and now the read nobody is waiting for finally answers, with a list of its own.
    await act(async () => {
      releaseAbandoned();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(await projectRows()).toHaveLength(2);
    expect(screen.getByText("Helios rollout")).toBeVisible();
  });

  it("renders the rows the replacement read answered with, after the abandoned one", async () => {
    // The contrast to the test above: the guard silences the read the screen walked away from, not
    // every read. A guard written as `if (false)` would satisfy that test for ever and leave this
    // screen loading until the tab is closed.
    const fetchMock = stubAbortableProjectListRead({ projects: [AURORA, HELIOS] });

    render(
      <StrictMode>
        <ProjectListScreen />
      </StrictMode>,
    );

    expect(fetchMock.mock.calls.length).toBe(2);
    expect(await projectRows()).toHaveLength(2);
    await waitFor(() => expect(screen.queryByText("Loading projects…")).toBeNull());
    expect(screen.queryByText("Projects could not be loaded.")).toBeNull();
  });
});
