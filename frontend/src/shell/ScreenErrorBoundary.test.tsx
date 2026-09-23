import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import catalogScreenSource from "../features/catalog/CatalogScreen.tsx?raw";
import projectListScreenSource from "../features/projects/ProjectListScreen.tsx?raw";
import { AppShell, type ScreenKey } from "./AppShell";
import {
  SCREEN_CRASH_MESSAGE,
  SCREEN_CRASH_PERSISTENT_MESSAGE,
  SCREEN_CRASH_RETRY_LABEL,
  screenCrashRepeatNotice,
} from "./ScreenErrorBoundary";

/**
 * SC-1-09, ADR-0010 — the render-error boundary, at the level it is mounted: the shell's screen
 * slot.
 *
 * The children here are synthetic on purpose. A screen that throws *because of a real payload* is
 * proven against the running application in `screenCrashContainment.test.tsx`; what this file
 * pins is the mechanism itself — that it is in the shell rather than in one screen, that the chrome
 * outlives a crash, that a caught crash does not follow the user, and that its sentence is its own.
 */

/** A child that throws while rendering — the class of exception a shape check cannot catch, because
 * it is not about the shape of anything. */
function Boom({ message = "Boom" }: { readonly message?: string }): never {
  throw new Error(message);
}

/**
 * A screen that throws for as long as the condition behind the crash lasts, and works once it is
 * over — the shape of a crash caused by one bad response, where trying again is how a person finds
 * out whether it is over.
 *
 * The condition is a flag the *test* flips, not a render counter, and neither detail is arbitrary.
 * A `useRef` counter would be reset by the retry, because the retry is a fresh mount of a component
 * whose previous instance React unmounted when it caught the throw; a closure counter would be
 * consumed before the boundary ever saw the error, because React re-renders a failing tree once
 * more before giving up on it. Both were measured while writing this file.
 */
function screenBrokenUntilFixed(onMount: () => void) {
  const condition = { broken: true };
  function Screen() {
    if (condition.broken) {
      throw new Error("Boom");
    }
    return <HealthyScreen name="Recovered screen" onMount={onMount} />;
  }
  return { Screen, condition };
}

/**
 * A screen that reports having been *mounted*, not merely rendered. The distinction is the whole of
 * K-04: a fallback that disappears while the screen behind it never re-runs its effects is a screen
 * that shows stale chrome and fetches nothing.
 */
function HealthyScreen({
  name,
  onMount,
}: {
  readonly name: string;
  readonly onMount?: () => void;
}) {
  useEffect(() => {
    onMount?.();
  }, [onMount]);
  return <h2 tabIndex={-1}>{name}</h2>;
}

function renderShell(activeScreen: ScreenKey, children: React.ReactNode) {
  return render(
    <AppShell backendStatus="ok" activeScreen={activeScreen} onNavigate={() => {}}>
      {children}
    </AppShell>,
  );
}

function rail() {
  return screen.getByRole("navigation", { name: "Sections" });
}

/**
 * Every pair of sentences differs, and neither is a fragment of the other — the check
 * `CatalogWrite.test.tsx` states for the write endings, applied to the read ones (K-07).
 *
 * Indexed rather than compared by value, for the reason measured there: written as a nested
 * `for…of` with `if (one === other) continue`, the guard meant to skip self-comparison skips *equal
 * strings* instead, so two endings rendering the same sentence — the one thing the check exists to
 * catch — is the one thing it lets through.
 */
function expectPairwiseDistinct(messages: readonly string[]): void {
  for (let i = 0; i < messages.length; i += 1) {
    expect(messages[i].trim()).not.toBe("");
    for (let j = i + 1; j < messages.length; j += 1) {
      expect(messages[i], `messages ${i} and ${j} read the same`).not.toBe(messages[j]);
      expect(messages[i], `message ${j} is a fragment of message ${i}`).not.toContain(messages[j]);
      expect(messages[j], `message ${i} is a fragment of message ${j}`).not.toContain(messages[i]);
    }
  }
}

describe("the shell's render-error boundary", () => {
  beforeEach(() => {
    // React's development build logs a caught error and its component stack itself, and jsdom
    // reports the re-thrown error on top of that. Both are React's and jsdom's own output, not this
    // application's (ADR-0010, point 4, named limitation) — silenced so a deliberate crash does not
    // read as a failing test. What the *application* puts on the console is asserted, positively and
    // negatively, in `screenCrashContainment.test.tsx`.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // --- K-02 ------------------------------------------------------------------------------------

  it("keeps the application usable when a screen throws during render, instead of blanking the page", () => {
    renderShell("projects", <Boom />);

    // Both halves of the criterion. First: the crash is stated, in a sentence of its own.
    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);

    // Second: the chrome is still there — and "still there" means reachable, not merely present in
    // the DOM. A message with no way out is the same dead end as the blank page, one sentence
    // richer.
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("StafffingCalculator");
    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByText("Projects")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("backend-status")).toHaveTextContent("Backend: ok");

    // SC-2-05 (gate-1 decision Q-1): the rail grew from two entries to the mockup's eleven, nine of
    // them planned. The claim here is unchanged — the whole rail survived the crash — and it is now
    // held against all eleven, in order, not two.
    const entries = within(rail()).getAllByRole("listitem");
    expect(entries.map((entry) => entry.textContent)).toEqual([
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
    const otherScreen = within(rail()).getByRole("button", { name: "Roles & rates" });
    expect(otherScreen.tabIndex).toBe(0);
    otherScreen.focus();
    expect(otherScreen).toHaveFocus();

    // And a way out from where the user is standing, not only through the rail (gate-1 decision,
    // gap 3).
    const retry = screen.getByRole("button", { name: SCREEN_CRASH_RETRY_LABEL });
    expect(retry).toBeVisible();
    expect(retry.tabIndex).toBe(0);
  });

  it("contains a crash in whichever screen the shell mounts, not only in the project list", () => {
    // The second mutation this criterion names: the boundary moved into `ProjectListScreen` instead
    // of into the screen slot. Every assertion in the test above would survive it; this one does
    // not, because the screen crashing here is the catalogue (SC-2-02 carries the same unprotected
    // defect today, which is why the boundary is in the shell — gate-1 decision Q-1, variant A).
    renderShell("roles-and-rates", <Boom message="The catalogue blew up" />);

    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);
    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByText("Roles & rates")).toHaveAttribute("aria-current", "page");
    expect(within(rail()).getByRole("button", { name: "Projects" })).toBeVisible();
  });

  it("renders the screen and no fallback when nothing throws", () => {
    // The contrast. Without it, a boundary whose `render` returned the fallback unconditionally
    // would satisfy both tests above.
    const onMount = vi.fn();
    renderShell("projects", <HealthyScreen name="A screen that works" onMount={onMount} />);

    expect(screen.getByRole("heading", { name: "A screen that works" })).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
    expect(screen.queryByRole("button", { name: SCREEN_CRASH_RETRY_LABEL })).toBeNull();
    expect(onMount).toHaveBeenCalledTimes(1);
  });

  // --- K-04 ------------------------------------------------------------------------------------

  it("does not keep showing a caught crash after the shell mounts a different screen", () => {
    const onMount = vi.fn();
    const { rerender } = renderShell("projects", <Boom />);
    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);
    expect(onMount).not.toHaveBeenCalled();

    rerender(
      <AppShell backendStatus="ok" activeScreen="roles-and-rates" onNavigate={() => {}}>
        <HealthyScreen name="Roles &amp; rates" onMount={onMount} />
      </AppShell>,
    );

    // Gone, and the screen behind it is *live*: its effect ran, which is what a screen does when it
    // reads. A boundary that cleared its flag but left the subtree unmounted would pass the first
    // two assertions and show an empty frame.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
    expect(onMount).toHaveBeenCalledTimes(1);
  });

  it("keeps the fallback in place across a re-render that is not a navigation", () => {
    // The contrast to the test above, and the reason the reset is keyed to the active screen rather
    // than to "any new props". A screen re-rendering for a reason of its own — the shell's backend
    // status changing, say — is not the user going somewhere else, and clearing on it would put the
    // crashing screen straight back and crash it again, on a loop nobody asked for.
    const { rerender } = renderShell("projects", <Boom />);
    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);

    rerender(
      <AppShell backendStatus="unreachable" activeScreen="projects" onNavigate={() => {}}>
        <Boom />
      </AppShell>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);
    expect(screen.getByTestId("backend-status")).toHaveTextContent("Backend: unreachable");
  });

  it("re-mounts the crashed screen from the same place when the fallback's try-again is pressed", () => {
    // The matching contrast gate-1 asked for (gap 3): retrying produces the same live-remount
    // result as walking away through the rail, without the walk.
    const onMount = vi.fn();
    const { Screen, condition } = screenBrokenUntilFixed(onMount);
    renderShell("projects", <Screen />);

    expect(screen.getByRole("alert")).toHaveTextContent(SCREEN_CRASH_MESSAGE);
    expect(onMount).not.toHaveBeenCalled();

    condition.broken = false;
    fireEvent.click(screen.getByRole("button", { name: SCREEN_CRASH_RETRY_LABEL }));

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("heading", { name: "Recovered screen" })).toBeVisible();
    // Live, not merely visible: the screen mounted, so its reads were issued again. A retry that
    // only cleared a flag would leave a screen that never asks the server anything.
    expect(onMount).toHaveBeenCalledTimes(1);
  });

  // --- Reviewer R-02 (2026-09-22): the retry control drops keyboard focus ------------------------

  it("puts focus on the heading of the screen a retry re-mounted, not on the document body", () => {
    // The same defect class as Reviewer R-03 of SC-2-02 (the rail entry that removes itself),
    // reproduced by a new control: pressing "Try again" succeeds, the button it was pressed with
    // leaves the DOM with the fallback, and the browser has nowhere to put focus but
    // `document.body` — so the next `Tab` starts again at the top of the page, past the whole rail.
    //
    // The landing place is the shell's existing one (`h1, h2` inside `main`), not a second
    // convention invented in the boundary.
    const onMount = vi.fn();
    const { Screen, condition } = screenBrokenUntilFixed(onMount);
    renderShell("projects", <Screen />);

    const retry = screen.getByRole("button", { name: SCREEN_CRASH_RETRY_LABEL });
    // The situation the finding is about: a keyboard user is standing on the control they press.
    retry.focus();
    expect(retry).toHaveFocus();

    condition.broken = false;
    fireEvent.click(retry);

    const heading = screen.getByRole("heading", { name: "Recovered screen" });
    expect(heading).toHaveFocus();
    expect(document.body).not.toHaveFocus();
    // And the screen really is live under it — focus on a heading over an unmounted subtree would
    // be a worse answer than no focus at all.
    expect(onMount).toHaveBeenCalledTimes(1);
  });

  it("leaves focus on the retry control when the screen crashes again, rather than dropping it", () => {
    // The contrast. A fallback re-announced by re-keying its own element would satisfy the repeat
    // criterion below and lose focus here, because re-keying re-mounts the button underneath the
    // hand that is on it.
    renderShell("projects", <Boom />);

    const retry = screen.getByRole("button", { name: SCREEN_CRASH_RETRY_LABEL });
    retry.focus();
    fireEvent.click(retry);

    expect(screen.getByRole("button", { name: SCREEN_CRASH_RETRY_LABEL })).toHaveFocus();
    expect(document.body).not.toHaveFocus();
  });

  // --- Reviewer R-04 (2026-09-22): retrying a crash that reproduces every time -------------------

  function retryOnce(): void {
    fireEvent.click(screen.getByRole("button", { name: SCREEN_CRASH_RETRY_LABEL }));
  }

  function alertText(): string {
    return screen.getByRole("alert").textContent ?? "";
  }

  it("stops advising a retry that has already failed twice in a row, without taking the button away", () => {
    // A persistent cause — a permanently malformed response — makes every press re-run the same
    // expensive mount (the catalogue read is ~19.7 MB) and answer with the identical sentence. The
    // sentence is what changes; the control stays, because the cause may still clear and removing
    // the one way out is a worse answer than an honest one.
    renderShell("projects", <Boom />);

    expect(alertText()).toContain(SCREEN_CRASH_MESSAGE);
    expect(alertText()).not.toContain(SCREEN_CRASH_PERSISTENT_MESSAGE);
    expect(alertText()).not.toContain(screenCrashRepeatNotice(1));

    retryOnce();
    // One failure is not a pattern: the advice is unchanged. Without this assertion the threshold
    // could be one, or zero, and the test below would not notice.
    expect(alertText()).toContain(SCREEN_CRASH_MESSAGE);
    expect(alertText()).not.toContain(SCREEN_CRASH_PERSISTENT_MESSAGE);
    // The repeat is announced even here: `role="alert"` re-announces on a *content* change, and the
    // same words rendered again are silence to a screen reader while a sighted user sees a flash.
    expect(alertText()).toContain(screenCrashRepeatNotice(1));

    retryOnce();
    expect(alertText()).toContain(SCREEN_CRASH_PERSISTENT_MESSAGE);
    expect(alertText()).not.toContain(SCREEN_CRASH_MESSAGE);
    expect(alertText()).toContain(screenCrashRepeatNotice(2));
    expect(screen.getByRole("button", { name: SCREEN_CRASH_RETRY_LABEL })).toBeEnabled();
  });

  it("counts repeats, not retries: a render that works in between clears the tally", () => {
    // "Consecutive" is the whole claim. A counter that only ever went up would reach the threshold
    // on a screen that has been working happily between two unrelated crashes, and tell the user
    // their application is stuck when it is not.
    const onMount = vi.fn();
    const { Screen, condition } = screenBrokenUntilFixed(onMount);
    const { rerender } = renderShell("projects", <Screen />);

    retryOnce();
    expect(alertText()).toContain(screenCrashRepeatNotice(1));

    // One retry that works.
    condition.broken = false;
    retryOnce();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(onMount).toHaveBeenCalledTimes(1);

    // And the same screen crashes again later, for a reason of its own — a re-render that is not a
    // navigation, so nothing else resets the boundary either.
    condition.broken = true;
    rerender(
      <AppShell backendStatus="ok" activeScreen="projects" onNavigate={() => {}}>
        <Screen />
      </AppShell>,
    );

    // A first crash again, worded as one.
    expect(alertText()).toContain(SCREEN_CRASH_MESSAGE);
    expect(alertText()).not.toContain(SCREEN_CRASH_PERSISTENT_MESSAGE);
    for (const repeat of [1, 2, 3]) {
      expect(alertText()).not.toContain(screenCrashRepeatNotice(repeat));
    }

    // One more press is therefore one repeat, not the third.
    retryOnce();
    expect(alertText()).toContain(screenCrashRepeatNotice(1));
    expect(alertText()).not.toContain(SCREEN_CRASH_PERSISTENT_MESSAGE);
  });

  it("does not carry a screen's repeat count to the screen the user navigates to", () => {
    const { rerender } = renderShell("projects", <Boom />);
    retryOnce();
    retryOnce();
    expect(alertText()).toContain(SCREEN_CRASH_PERSISTENT_MESSAGE);

    // The catalogue crashes for the first time, on its own account.
    rerender(
      <AppShell backendStatus="ok" activeScreen="roles-and-rates" onNavigate={() => {}}>
        <Boom message="The catalogue blew up" />
      </AppShell>,
    );

    expect(alertText()).toContain(SCREEN_CRASH_MESSAGE);
    expect(alertText()).not.toContain(SCREEN_CRASH_PERSISTENT_MESSAGE);
    expect(alertText()).not.toContain(screenCrashRepeatNotice(1));
  });

  // --- K-07 ------------------------------------------------------------------------------------

  it("gives a crashed screen a sentence of its own, distinct from every other failure sentence in the application", () => {
    /**
     * Every sentence either screen shows when a read does not produce data, plus the new one. The
     * six existing ones are spelled out here and then held against the screens that render them
     * (below), so the list cannot come to describe an application that no longer exists — and each
     * of them is separately asserted *as rendered output* by that screen's own suite.
     */
    const PROJECT_SCREEN_FAILURES = [
      "You do not have permission to view projects.",
      "Projects could not be loaded — request timed out.",
      "Projects could not be loaded.",
    ];
    const CATALOGUE_FAILURES = [
      "You do not have permission to view the catalogue.",
      "The catalogue could not be loaded — request timed out.",
      "The catalogue could not be loaded.",
    ];

    expectPairwiseDistinct([
      ...PROJECT_SCREEN_FAILURES,
      ...CATALOGUE_FAILURES,
      SCREEN_CRASH_MESSAGE,
      // Reviewer R-04: the repeat wording is a ninth sentence, and it is held to the same rule.
      // Written as an addition to the plain one ("… and it keeps happening"), it would be a
      // superstring of it, and a screen reader would hear the first sentence twice.
      SCREEN_CRASH_PERSISTENT_MESSAGE,
    ]);

    // Pinned to the code: reword a message on either screen without revisiting this list and the
    // check fails, rather than going on comparing six strings the application stopped using.
    for (const sentence of PROJECT_SCREEN_FAILURES) {
      expect(projectListScreenSource, `the project list no longer says: ${sentence}`).toContain(
        sentence,
      );
    }
    for (const sentence of CATALOGUE_FAILURES) {
      expect(catalogScreenSource, `the catalogue no longer says: ${sentence}`).toContain(sentence);
    }

    // And the crash sentence belongs to the boundary alone — a screen that grew a copy of it would
    // be a second mechanism wearing this one's words.
    for (const sentence of [SCREEN_CRASH_MESSAGE, SCREEN_CRASH_PERSISTENT_MESSAGE]) {
      expect(projectListScreenSource).not.toContain(sentence);
      expect(catalogScreenSource).not.toContain(sentence);
    }
  });
});
