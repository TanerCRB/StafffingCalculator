import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell, type ScreenKey } from "./AppShell";

/**
 * The shell is chrome. The claims worth pinning are the ones a screenshot cannot make: that the
 * rail says which screens exist and which do not, that it invents no entry for a screen with
 * nothing behind it, that it reads nothing from the API, that it reports a navigation choice
 * instead of taking one, and that the status indicator the App test looks for is in the topbar.
 */

const SCREEN_CONTENT = "Screen placed in the shell";

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

  it("lists only the screens this application has, not the screens the design proposal shows", () => {
    renderShell();

    // Two entries, both named. An entry for a screen with no backend behind it would be a promise
    // the product cannot keep, and — unlike a disabled entry — nothing about it would say so.
    const entries = within(rail()).getAllByRole("listitem");
    expect(entries.map((entry) => entry.textContent)).toEqual(["Projects", "Roles & rates"]);

    for (const absent of [
      "Compare scenarios",
      "Working calendars",
      "Organization defaults",
      "Staffing plan",
      "Commercial terms",
      "Versions & approval",
    ]) {
      expect(within(rail()).queryByText(absent)).toBeNull();
    }
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
