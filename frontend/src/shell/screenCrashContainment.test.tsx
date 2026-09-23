import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { ProjectListItem } from "../api/contracts/projects";
import { SCREEN_CRASH_MESSAGE, SCREEN_CRASH_RETRY_LABEL } from "./ScreenErrorBoundary";

/**
 * SC-1-09 — the render-error boundary against the running application, with real screens and a real
 * payload (K-02, K-03, K-04, K-06).
 *
 * The crash here is not staged with a component written to throw. It is the one this task exists
 * for: the API answers `200` with a `target_margin_percent` that is a string, and therefore passes
 * the shape check, but is not a fixed-point decimal — so `roundDecimalString` throws, exactly as
 * ADR-0002 says it must, in the middle of a render. That is the division of labour ADR-0010 states:
 * a shape violation never gets this far (K-01), a value violation does, and the boundary is what
 * stands between it and a blank page.
 */

/**
 * The value that must not leak. It looks like an amount because that is what NF-11 is about: the
 * formatter's own exception carries its input verbatim (`Not a fixed-point decimal string: "…"`),
 * so the error object at the boundary holds a number a person may not be allowed to see.
 */
const SENTINEL = "499999.95-NF11-SENTINEL";

const CRASHING_PROJECT: ProjectListItem = {
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
      missing_inputs: [],
      ready_for_approval: true,
      // A string, so the contract check at the network boundary passes it; not a decimal, so the
      // formatter throws on it. Both halves are deliberate.
      target_margin_percent: SENTINEL,
    },
  ],
};

function stubBackend(projects: readonly ProjectListItem[]) {
  const fetchMock = vi.fn(async (url: string) => {
    const path = new URL(url).pathname;
    const body: Record<string, unknown> =
      {
        "/health": { status: "ok" },
        "/projects": { projects },
        "/catalog/rates": { rates: [], total: 0 },
        "/catalog/dimensions/roles": { entries: [] },
        "/catalog/dimensions/seniorities": { entries: [] },
        "/catalog/dimensions/locations": { entries: [] },
        "/catalog/dimensions/engagement-types": { entries: [] },
        "/catalog/dimensions/vendors": { entries: [] },
      }[path] ?? {};
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestedPaths(fetchMock: { readonly mock: { readonly calls: readonly unknown[][] } }) {
  return fetchMock.mock.calls.map(([url]) => new URL(url as string).pathname);
}

/** Every console call the test recorded, arguments and all. */
let consoleCalls: unknown[][];

/**
 * Where each recorded console call was made *from* — a stack captured at the call site, index-aligned
 * with `consoleCalls` (QA, SC-1-09).
 *
 * The reason it exists: `isReactOrJsdomOwnReport` below classifies by what a call *carries*, and
 * "carries an `Error`" is the shape of the most likely application leak there is — a
 * `componentDidCatch` that logs the error it was handed. Under that classifier such a call is filed
 * as React's own and never reaches `expect(applicationCalls).toEqual([])`. Measured, not reasoned:
 * adding `componentDidCatch(e) { console.error("…", e) }` to `ScreenErrorBoundary` left all 142 tests
 * green (QA mutation run, 2026-09-22). Origin cannot be forged by an argument, so the test below
 * judges by it.
 */
let consoleStacks: string[];

/** Frames belonging to the spy itself — `vi.spyOn`'s wrapper and the recorder above it. */
const SPY_PLUMBING = /tinyspy|@vitest[+/]spy/;

/**
 * The frame that actually called `console.*`: the first one below the spy's own plumbing. Returns
 * `""` when no such frame can be read, which the caller must treat as "unknown", never as "not the
 * application".
 */
function callSiteOf(stack: string): string {
  const frames = stack
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("at "));
  let lastPlumbing = -1;
  frames.forEach((frame, index) => {
    if (SPY_PLUMBING.test(frame)) {
      lastPlumbing = index;
    }
  });
  return frames[lastPlumbing + 1] ?? "";
}

/**
 * Whether the call came from this application's own code rather than from a dependency. React's dev
 * report is raised inside `react-dom`, jsdom's inside `jsdom`; both are under `node_modules` and both
 * are the named limitation (gate-1 decision, gap 2). Anything else on the stack is code this
 * repository wrote — including this test file, which is what makes the positive control below run
 * through the same judgement as the mechanism.
 */
function isApplicationOrigin(stack: string): boolean {
  const frame = callSiteOf(stack);
  return frame !== "" && !frame.includes("node_modules");
}

/**
 * React's development build reports a caught render error on the console itself, and jsdom reports
 * the re-thrown error on top of it. Both are outside this mechanism — the scope gate 1 set for K-03
 * is the *application's own* calls (gate-1 decision, gap 2), and neither of these survives a
 * production build.
 *
 * Recognised by carrying the error object itself, or by React's and jsdom's own wording. Everything
 * else in `consoleCalls` is this application talking, and is held to NF-11.
 */
function isReactOrJsdomOwnReport(call: readonly unknown[]): boolean {
  return call.some(
    (argument) =>
      argument instanceof Error ||
      (typeof argument === "string" &&
        (argument.includes("The above error occurred") ||
          argument.includes("Uncaught ") ||
          argument.startsWith("Warning:"))),
  );
}

/** The detector K-03 hangs on: does anything here carry the value? Its own honesty is checked by a
 * positive control in each test that uses it. */
function carries(haystack: unknown, needle: string): boolean {
  if (typeof haystack === "string") {
    return haystack.includes(needle);
  }
  if (haystack instanceof Error) {
    return haystack.message.includes(needle) || (haystack.stack ?? "").includes(needle);
  }
  return false;
}

/** Puts the application in the crashed state the criteria are about, and answers where it got. */
async function crashTheProjectScreen() {
  const fetchMock = stubBackend([CRASHING_PROJECT]);
  render(<App />);

  // The list itself renders: the payload is contract-valid, and the value that is not a decimal is
  // only reached once a project is selected. That is worth pinning — the crash happens in a render
  // triggered by a click, well after the screen looked healthy.
  fireEvent.click(await screen.findByRole("button", { name: "Aurora migration" }));

  return fetchMock;
}

describe("a screen that crashes while rendering, in the running application", () => {
  beforeEach(() => {
    consoleCalls = [];
    consoleStacks = [];
    for (const method of ["error", "warn", "log", "info", "debug"] as const) {
      vi.spyOn(console, method).mockImplementation((...args: unknown[]) => {
        consoleCalls.push(args);
        consoleStacks.push(new Error("console call site").stack ?? "");
      });
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // --- K-02 and K-06 ---------------------------------------------------------------------------

  it("states the crash and keeps the chrome when a value the contract could not check throws mid-render", async () => {
    await crashTheProjectScreen();

    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);

    // The chrome the blank page used to take with it.
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("StafffingCalculator");
    expect(screen.getByTestId("backend-status")).toBeVisible();
    const rail = screen.getByRole("navigation", { name: "Sections" });
    // SC-2-05 (gate-1 decision Q-1): all eleven entries of the mockup's rail, in order — the claim
    // (the chrome survived the crash) held against the whole rail rather than its first two.
    expect(within(rail).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Projects",
      "Compare scenarios",
      "Roles & rates",
      "Working calendars",
      "Organization defaults",
      "Overview",
      "Staffing plan",
      "Additional costs",
      "Commercial terms",
      "Assumptions",
      "Versions & approval",
    ]);
    expect(within(rail).getByRole("button", { name: "Roles & rates" })).toBeVisible();

    // K-06: no substitute number anywhere. The formatter threw rather than softening, and nothing
    // downstream invented a plausible percentage to put in the gap — which is the failure that
    // would have rendered correctly, and that no boundary could catch.
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("NaN");
    expect(text).not.toContain("0.00%");
    expect(text).not.toContain("Target margin");
    expect(screen.queryByRole("heading", { name: "Baseline" })).toBeNull();
  });

  // --- K-03 (NF-11) ----------------------------------------------------------------------------

  it("renders nothing from the payload in the fallback — not as text, not in an attribute", async () => {
    await crashTheProjectScreen();
    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);

    // `innerHTML`, not only `textContent`: an error message parked in a `data-…` or a `title` is
    // invisible on screen, in the page source, and in a screenshot — and completely readable.
    expect(carries(document.body.textContent, SENTINEL)).toBe(false);
    expect(carries(document.body.innerHTML, SENTINEL)).toBe(false);
    // The amount alone, not only the whole sentinel: a fallback rendering a *part* of the value
    // leaks just as much.
    expect(carries(document.body.innerHTML, "499999.95")).toBe(false);

    // Positive control, in the same test and through the same detector: with the mechanism
    // bypassed — a fallback that puts the error where this one puts nothing — the detector sees it.
    // Without this the two assertions above are satisfied by a detector that can never find
    // anything.
    const leaky = document.createElement("div");
    leaky.textContent = `Details: ${SENTINEL}`;
    leaky.setAttribute("data-error", `Not a fixed-point decimal string: "${SENTINEL}"`);
    document.body.appendChild(leaky);
    expect(carries(document.body.textContent, SENTINEL)).toBe(true);
    expect(carries(document.body.innerHTML, SENTINEL)).toBe(true);
    leaky.remove();
  });

  it("puts nothing from the payload on the console either, in any call this application makes", async () => {
    await crashTheProjectScreen();
    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);

    // Something was logged — by React and by jsdom, which is the named limitation, and which also
    // means this test is not passing because nothing reached the console at all.
    expect(consoleCalls.length).toBeGreaterThan(0);
    expect(consoleCalls.some(isReactOrJsdomOwnReport)).toBe(true);

    const applicationCalls = consoleCalls.filter((call) => !isReactOrJsdomOwnReport(call));
    const leaks = applicationCalls.filter((call) =>
      call.some((argument) => carries(argument, SENTINEL)),
    );
    expect(leaks).toEqual([]);
    // Stronger, and the reason the classification above is sound rather than convenient: this
    // application makes no console call at all on this path. Nothing was suppressed, nothing was
    // reported, and there is no reporting hook to grow one (Issue #43, out of scope 3).
    expect(applicationCalls).toEqual([]);

    // Positive control: a call the application might have made *is* caught by the same filter and
    // the same detector.
    console.error("Failed to render the scenario:", `value=${SENTINEL}`);
    const afterPlanting = consoleCalls
      .filter((call) => !isReactOrJsdomOwnReport(call))
      .filter((call) => call.some((argument) => carries(argument, SENTINEL)));
    expect(afterPlanting).toHaveLength(1);
  });

  it("makes no console call of its own on the crash path, judged by where the call came from and not by what it carried", async () => {
    // QA, SC-1-09 — the contrast the test above cannot provide for itself.
    //
    // `isReactOrJsdomOwnReport` excuses any call that carries an `Error`. That is precisely the
    // shape of the leak most likely to be written here: a `componentDidCatch` (or a
    // `window.onerror` hook, or a future telemetry call) logging the error object it was given —
    // whose `message` holds `Not a fixed-point decimal string: "<the amount>"`, the value NF-11 is
    // about. Such a call is filed as React's own and disappears before any assertion sees it, so
    // `expect(applicationCalls).toEqual([])` stays true while the amount is on the console.
    //
    // Mutation run, 2026-09-22 (QA): `componentDidCatch(error) { console.error("…", error); }` added
    // to `ScreenErrorBoundary` — whole suite green, 142/142, including the test above. With this
    // test present the same mutation fails here, and only here.
    await crashTheProjectScreen();
    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);

    // The classifier is not answering "everything is a dependency": the calls React and jsdom make
    // on this path are recognised as theirs, which is also what keeps the named limitation named.
    expect(consoleStacks.filter((stack) => !isApplicationOrigin(stack)).length).toBeGreaterThan(0);
    // ...and not "nothing has an origin": every recorded call was attributable.
    expect(consoleStacks.filter((stack) => callSiteOf(stack) === "")).toEqual([]);

    const fromApplication = consoleStacks
      .map((stack, index) => ({ stack, call: consoleCalls[index] }))
      .filter((entry) => isApplicationOrigin(entry.stack));
    expect(
      fromApplication.map((entry) => `${callSiteOf(entry.stack)} :: ${entry.call.map(String).join(" ")}`),
    ).toEqual([]);

    // Positive control, through the same judgement, in the shape the argument-based filter misses:
    // a sentence plus the error object itself.
    console.error(
      "ScreenErrorBoundary caught a render error:",
      new Error(`Not a fixed-point decimal string: "${SENTINEL}"`),
    );
    const afterPlanting = consoleStacks
      .map((stack, index) => ({ stack, call: consoleCalls[index] }))
      .filter((entry) => isApplicationOrigin(entry.stack));
    expect(afterPlanting).toHaveLength(1);
    expect(afterPlanting[0].call.some((argument) => carries(argument, SENTINEL))).toBe(true);
    // And the filter this file already had does *not* see it — which is the whole finding.
    expect(isReactOrJsdomOwnReport(afterPlanting[0].call)).toBe(true);
  });

  // --- K-04 ------------------------------------------------------------------------------------

  it("does not keep a caught crash on screen after the rail moves to a healthy screen", async () => {
    const fetchMock = await crashTheProjectScreen();
    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);
    expect(requestedPaths(fetchMock)).not.toContain("/catalog/rates");

    fireEvent.click(
      within(screen.getByRole("navigation", { name: "Sections" })).getByRole("button", {
        name: "Roles & rates",
      }),
    );

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
    expect(
      within(screen.getByRole("main")).getByRole("heading", { name: "Roles & rates" }),
    ).toBeVisible();
    // Mounted for real, not merely revealed: the catalogue issued its own six reads. A boundary
    // that cleared its flag without the subtree being mounted would show a heading over nothing.
    await waitFor(() =>
      expect(requestedPaths(fetchMock)).toContain("/catalog/dimensions/engagement-types"),
    );
  });

  it("re-mounts the screen that crashed, live, when the fallback's try-again is pressed", async () => {
    const fetchMock = await crashTheProjectScreen();
    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);
    const readsBefore = requestedPaths(fetchMock).filter((path) => path === "/projects").length;
    expect(readsBefore).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: SCREEN_CRASH_RETRY_LABEL }));

    // The same live-remount result as walking away through the rail, from where the user is
    // standing: the fallback is gone, the project list is back, and it read the list again.
    expect(screen.queryByRole("alert")).toBeNull();
    await waitFor(() =>
      expect(requestedPaths(fetchMock).filter((path) => path === "/projects")).toHaveLength(2),
    );
    expect(await screen.findByRole("button", { name: "Aurora migration" })).toBeVisible();
    expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
  });

  it("shows no fallback at all when the same screens are given a payload they can render", async () => {
    // The contrast for the whole file: the boundary is a backstop for an exception, not a state the
    // application passes through on the way to a working screen.
    const fetchMock = stubBackend([
      { ...CRASHING_PROJECT, scenarios: [{ ...CRASHING_PROJECT.scenarios[0], target_margin_percent: "12.500" }] },
    ]);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Aurora migration" }));

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("Target margin: 12.50%")).toBeVisible();
    expect(requestedPaths(fetchMock)).toContain("/projects");
  });
});
