import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NOT_IMPLEMENTED_PREFIX } from "../lib/notImplemented";
import { AppShell, type ScreenKey } from "./AppShell";

/**
 * The shell is chrome. The claims worth pinning are the ones a screenshot cannot make: that the
 * rail says which screens exist and which do not, that it invents no entry for a screen with
 * nothing behind it, that it reads nothing from the API, that it reports a navigation choice
 * instead of taking one, and that the status indicator the App test looks for is in the topbar.
 */

const SCREEN_CONTENT = "Screen placed in the shell";

/** The rail of `Wymagania/prototyp/screens/15-catalog.png`, top to bottom (SC-2-05). Literals, not
 * the shell's own lists: a test that read its expectation from the component would agree with it. */
const ALL_RAIL_ENTRIES = [
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
];

/** The nine of them this application does not have. */
const PLANNED_RAIL_ENTRIES = ALL_RAIL_ENTRIES.filter(
  (label) => label !== "Projects" && label !== "Roles & rates",
);

interface ShellOptions {
  readonly activeScreen?: ScreenKey;
  readonly onNavigate?: (screen: ScreenKey) => void;
}

/** `activeScreen`/`onNavigate` are required props (SC-2-02): the shell is told which screen is
 * mounted and reports a choice upwards — it decides neither. The defaults here keep every
 * assertion below about the chrome. */
function renderShell(
  backendStatus: "checking" | "ok" | "unreachable" = "ok",
  { activeScreen = "projects", onNavigate = () => {} }: ShellOptions = {},
) {
  return render(
    <AppShell backendStatus={backendStatus} activeScreen={activeScreen} onNavigate={onNavigate}>
      <p>{SCREEN_CONTENT}</p>
    </AppShell>,
  );
}

function rail() {
  return screen.getByRole("navigation", { name: "Sections" });
}

describe("AppShell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("names the application and what it is for", () => {
    renderShell();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("StafffingCalculator");
    expect(
      screen.getByText("IT project staffing, cost, and profitability planner."),
    ).toBeInTheDocument();
  });

  it("offers the Roles & rates entry as a live control that reports the choice instead of taking it", () => {
    // This test previously asserted the entry was `aria-disabled` with a "Not implemented yet"
    // tooltip. SC-2-02 built the screen behind it, so that tooltip became the false statement and
    // the control changed with the code it describes — the claim, that the rail says truthfully
    // what exists, is the same one.
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const onNavigate = vi.fn();

    renderShell("ok", { onNavigate });

    const entry = within(rail()).getByRole("button", { name: "Roles & rates" });

    // Visible and in the tab order. A native `<button type="button">`, so Enter and Space activate
    // it through the platform — jsdom does not synthesise that, and a `<div onClick>` styled like a
    // control would pass every other assertion here while being unreachable by keyboard.
    expect(entry).toBeVisible();
    expect(entry.tagName).toBe("BUTTON");
    expect(entry).toHaveAttribute("type", "button");
    expect(entry.tabIndex).toBe(0);
    entry.focus();
    expect(entry).toHaveFocus();
    // Not announced as pending any more, and carrying no "not implemented" tooltip: the screen it
    // names exists.
    expect(entry).not.toHaveAttribute("aria-disabled");
    expect(entry.getAttribute("title")).toBeNull();

    fireEvent.click(entry);

    // The shell reports the choice and mounts nothing itself — the screen in the frame is still the
    // one it was given — and it read nothing from the API: a rail entry that prefetched the screen
    // behind it would be a read the user was never offered.
    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith("roles-and-rates");
    expect(screen.getByText(SCREEN_CONTENT)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("marks the screen it was told is active, not one of its own choosing", () => {
    // The mirror image of the two tests around it: with the catalogue active, the two rail states
    // swap. A shell that hardcoded "Projects" as current — the shape this file asserted before
    // SC-2-02 — would pass every assertion in this suite except these.
    const onNavigate = vi.fn();
    renderShell("ok", { activeScreen: "roles-and-rates", onNavigate });

    const current = within(rail()).getByText("Roles & rates");
    expect(current).toHaveAttribute("aria-current", "page");
    expect(within(rail()).queryByRole("button", { name: "Roles & rates" })).toBeNull();

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByText("Roles & rates")).toHaveAttribute("aria-current", "page");

    const projects = within(rail()).getByRole("button", { name: "Projects" });
    expect(projects).not.toHaveAttribute("aria-current");
    fireEvent.click(projects);
    expect(onNavigate).toHaveBeenCalledWith("projects");
  });

  it("marks Projects as the current screen and does not offer it as a control", () => {
    renderShell();

    const current = within(rail()).getByText("Projects");
    expect(current).toHaveAttribute("aria-current", "page");
    // The contrast with the entry above: the two states of a rail entry must not collapse onto
    // one. The screen the user is already on is not a button, and it is not announced as pending.
    expect(current).not.toHaveAttribute("aria-disabled");
    expect(within(rail()).queryByRole("button", { name: "Projects" })).toBeNull();
    expect(within(rail()).getByRole("button", { name: "Roles & rates" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("offers as live only the screens this application has, and shows every other screen of the design proposal as not implemented yet", () => {
    // SC-2-05 (Issue #59, gate-1 decision Q-1) rewrote this test. Until then the rail had two
    // entries and this asserted that the mockup's other screens were absent. The rail now shows the
    // mockup's eleven entries, so the claim — the rail says truthfully what exists — is asserted
    // the other way round and more strictly: every entry is pinned by name and position, the two
    // that exist are live, and each of the nine that do not says so, in the one convention
    // `lib/notImplemented.ts` owns, and does nothing when pressed.
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const onNavigate = vi.fn();
    renderShell("ok", { activeScreen: "roles-and-rates", onNavigate });

    const entries = within(rail()).getAllByRole("listitem");
    expect(entries.map((entry) => entry.textContent)).toEqual(ALL_RAIL_ENTRIES);

    // The nine planned entries: a button each, reachable and announced, marked as not available
    // with the tooltip every such control carries, and naming why.
    for (const label of PLANNED_RAIL_ENTRIES) {
      const entry = within(rail()).getByRole("button", { name: label });
      expect(entry, label).toHaveAttribute("aria-disabled", "true");
      expect(entry, label).not.toHaveAttribute("disabled");
      expect(entry.tabIndex, label).toBe(0);
      expect(entry.getAttribute("title"), label).toMatch(new RegExp(`^${NOT_IMPLEMENTED_PREFIX} — .+`));
      expect(entry, label).not.toHaveAttribute("aria-current");
    }

    // The two that exist carry none of that.
    const projects = within(rail()).getByRole("button", { name: "Projects" });
    expect(projects).not.toHaveAttribute("aria-disabled");
    expect(projects.getAttribute("title")).toBeNull();
    expect(within(rail()).getByText("Roles & rates")).toHaveAttribute("aria-current", "page");

    // Pressing a planned entry navigates nowhere and reads nothing.
    for (const label of PLANNED_RAIL_ENTRIES) {
      fireEvent.click(within(rail()).getByRole("button", { name: label }));
    }
    expect(onNavigate).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(SCREEN_CONTENT)).toBeInTheDocument();

    // Contrast: the live entry in the same rail does report its choice — so the silence above is
    // the planned entries', not a rail that reports nothing.
    fireEvent.click(projects);
    expect(onNavigate).toHaveBeenCalledWith("projects");
  });

  it("names the project group in the accessibility tree without a heading, and without the mockup's sample project name", () => {
    renderShell();

    // Gate-1 decision Q-2: "Commerce platform" is the mockup's example project, not a section name.
    const group = within(rail()).getByRole("list", { name: "Project" });
    expect(within(group).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Overview",
      "Staffing plan",
      "Additional costs",
      "Commercial terms",
      "Assumptions",
      "Versions & approval",
    ]);
    expect(within(rail()).queryByText(/Commerce platform/)).toBeNull();
    // Not a heading: the rail adds none, so the page's headings stay the shell's `h1` and the
    // screen's own.
    expect(within(rail()).queryAllByRole("heading")).toHaveLength(0);
  });

  it("says why each planned screen is missing — a missing screen, not a missing feature, where the backend already exists", () => {
    renderShell();

    const hint = (label: string) =>
      within(rail()).getByRole("button", { name: label }).getAttribute("title") ?? "";

    // Gate-1 decision Q-4: the calendars (SC-3-02, SC-3-03) and the staffing lines (SC-3-01) are
    // stored and proven. A tooltip saying the feature does not exist would be the false statement.
    expect(hint("Working calendars")).toBe(
      `${NOT_IMPLEMENTED_PREFIX} — the calendars are stored by the backend (SC-3-02, SC-3-03); the screen is not built`,
    );
    expect(hint("Staffing plan")).toBe(
      `${NOT_IMPLEMENTED_PREFIX} — staffing lines are stored by the backend (SC-3-01); the screen is not built`,
    );

    // The rest name the requirement and the Issue that owns it.
    expect(hint("Compare scenarios")).toContain("F-09 (Issue #11)");
    expect(hint("Organization defaults")).toContain("F-02 (Issue #4)");
    expect(hint("Overview")).toContain("F-10 and F-11 (Issues #12, #13)");
    expect(hint("Additional costs")).toContain("F-08 (Issue #10)");
    expect(hint("Commercial terms")).toContain("F-06 (Issue #8)");
    expect(hint("Assumptions")).toContain("F-02 (Issue #4)");
    expect(hint("Versions & approval")).toContain("F-12 (Issue #14)");
  });

  it("leaves the topbar as it was — no identity, no workspace badge, no currency footer from the mockup", () => {
    renderShell();

    // Gate-1 decision Q-3: nothing in this product backs them.
    for (const absent of ["Project manager", "PM", "Internal workspace", "Planning workspace"]) {
      expect(screen.queryByText(absent)).toBeNull();
    }
    expect(screen.queryByText(/scenario currency/i)).toBeNull();
    expect(screen.getByTestId("backend-status")).toHaveTextContent("Backend: ok");
  });

  it("does not move focus when a planned entry is pressed, because nothing was navigated to", () => {
    const { rerender } = render(
      <AppShell backendStatus="ok" activeScreen="projects" onNavigate={() => {}}>
        <h2 tabIndex={-1}>First screen</h2>
      </AppShell>,
    );
    const planned = within(rail()).getByRole("button", { name: "Compare scenarios" });
    planned.focus();
    fireEvent.click(planned);

    // `App` would re-render the shell with the same `activeScreen` — a planned entry never calls
    // `onNavigate` — and the `[activeScreen]` effect does not fire for that.
    rerender(
      <AppShell backendStatus="ok" activeScreen="projects" onNavigate={() => {}}>
        <h2 tabIndex={-1}>First screen</h2>
      </AppShell>,
    );
    expect(planned).toHaveFocus();
    expect(screen.getByRole("heading", { name: "First screen" })).not.toHaveFocus();
  });

  it("puts the screen it frames inside the main landmark, reachable past the rail", () => {
    renderShell();

    const main = screen.getByRole("main");
    expect(within(main).getByText(SCREEN_CONTENT)).toBeInTheDocument();

    // The rail comes before the content in the document, so there is a way past it that does not
    // depend on tabbing through every entry.
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip.getAttribute("href")).toBe(`#${main.id}`);
    expect(main.id).not.toBe("");
  });

  it("shows the backend status in the topbar, as a word rather than a colour", () => {
    renderShell("unreachable");

    const status = within(screen.getByRole("banner")).getByTestId("backend-status");
    expect(status).toHaveTextContent("Backend: unreachable");
    // `data-state` only selects the fill; the state is readable without it.
    expect(status).toHaveAttribute("data-state", "unreachable");
  });

  it("says where the user is without offering a trail of controls that navigate nowhere", () => {
    renderShell();

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumb).toHaveTextContent("Workspace");
    expect(within(breadcrumb).getByText("Projects")).toHaveAttribute("aria-current", "page");
    expect(within(breadcrumb).queryAllByRole("link")).toHaveLength(0);
    expect(within(breadcrumb).queryAllByRole("button")).toHaveLength(0);
  });

  // --- Reviewer R-03 -------------------------------------------------------------------------

  it("does not move focus to the screen's heading on first render — only after the active screen changes", () => {
    // The active rail entry renders as a `<span>`, not a control (see the test above): activating
    // it removes the element that held focus from the DOM, and the browser drops focus to
    // `document.body`. A component the shell mounted for the first time was never focused
    // anywhere in particular, so stealing focus to its heading here would be a new, unasked-for
    // behaviour rather than a fix for the one the reviewer found.
    render(
      <AppShell backendStatus="ok" activeScreen="projects" onNavigate={() => {}}>
        <h2 tabIndex={-1}>{SCREEN_CONTENT}</h2>
      </AppShell>,
    );

    expect(screen.getByRole("heading", { name: SCREEN_CONTENT })).not.toHaveFocus();
  });

  it("moves focus to the new screen's heading once the active screen changes", () => {
    const { rerender } = render(
      <AppShell backendStatus="ok" activeScreen="projects" onNavigate={() => {}}>
        <h2 tabIndex={-1}>First screen</h2>
      </AppShell>,
    );
    expect(screen.getByRole("heading", { name: "First screen" })).not.toHaveFocus();

    rerender(
      <AppShell backendStatus="ok" activeScreen="roles-and-rates" onNavigate={() => {}}>
        <h2 tabIndex={-1}>Second screen</h2>
      </AppShell>,
    );

    // The new screen's heading now holds focus — not `document.body`, where an unmounted `<span>`
    // would otherwise have dropped it.
    expect(screen.getByRole("heading", { name: "Second screen" })).toHaveFocus();
    expect(document.body).not.toHaveFocus();

    // And a rerender with `activeScreen` unchanged does not move focus again — a screen re-rendering
    // for a reason of its own (new data arriving, say) is not a navigation.
    const heading = screen.getByRole("heading", { name: "Second screen" });
    (document.activeElement as HTMLElement | null)?.blur();
    rerender(
      <AppShell backendStatus="ok" activeScreen="roles-and-rates" onNavigate={() => {}}>
        <h2 tabIndex={-1}>Second screen, updated</h2>
      </AppShell>,
    );
    expect(heading).not.toHaveFocus();
  });
});
