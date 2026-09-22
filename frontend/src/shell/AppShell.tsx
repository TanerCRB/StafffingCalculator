import { useCallback, useEffect, useRef, type ReactNode } from "react";

import { ScreenErrorBoundary } from "./ScreenErrorBoundary";
import "./AppShell.css";

/**
 * The application chrome: topbar, navigation rail, content frame. Presentational only — it takes no
 * decision about what may be read or shown, and it fetches nothing (the screen inside it does).
 *
 * Visual reference: `Wymagania/prototyp/` (design proposal, UI-01). A reference, not a
 * specification: nothing here is pixel-checked, and the rail deliberately does not reproduce the
 * prototype's 24 navigation entries — see RAIL_ITEMS.
 *
 * The name stays "StafffingCalculator" and the entity stays "Scenario"; the prototype's "Staffing
 * planner" and "Calculation details" are product-naming questions, settled elsewhere (gate-1
 * decision 5, Issue #3), not something a visual pass may change.
 *
 * Two behaviours live here rather than in a screen. The second one (SC-1-09, ADR-0010) is the
 * render-error boundary around the screen slot: it belongs to the shell because the hole it covers
 * is the application's, not one screen's — see `ScreenErrorBoundary.tsx`.
 *
 * The other lives here rather than in a screen: after a rail activation, focus moves to the
 * heading of the screen that activation just mounted (Reviewer R-03, SC-2-02) — and, since
 * Reviewer R-02 of SC-1-09, after the error boundary's "Try again" too, which re-mounts a screen
 * without the active one changing. The active rail
 * entry renders as a `<span>`, not a `<button>` (see `RAIL_ORDER` below) — the element holding
 * keyboard focus at the moment of activation is removed from the DOM by that same click, and the
 * browser has nowhere else to put focus but `document.body`. The shell is the one thing that knows
 * both when that happens and which screen replaced it, so it is the one place this fix can live
 * without every screen re-implementing it.
 */

export type BackendStatus = "checking" | "ok" | "unreachable";

/**
 * The screens this application has, as the identifiers the rail navigates between.
 *
 * Navigation is React state held by `App` and handed down here (SC-2-02, gate-1 decision 10): no
 * router library, no URL, no history. That is a named limitation, not an oversight — a screen is not
 * linkable or bookmarkable yet, and choosing a router is a separate architectural decision.
 */
export type ScreenKey = "projects" | "roles-and-rates";

/**
 * The name of each screen, in one place: the rail entry and the breadcrumb are the same word by
 * construction. Exhaustive over `ScreenKey`, so a new screen cannot be navigable and unnamed.
 */
const SCREEN_LABELS: Readonly<Record<ScreenKey, string>> = {
  projects: "Projects",
  "roles-and-rates": "Roles & rates",
};

/**
 * Two entries, against the prototype's twenty-four. The rail names what this application actually
 * has: Projects (SC-1-06) and Roles & rates (SC-2-02). Entries for screens with no backend behind
 * them would be a promise the product cannot keep — and unlike a disabled entry, nothing about them
 * would say so.
 *
 * Both entries are live controls now. Until SC-2-02 the second one was rendered `aria-disabled` with
 * a "Not implemented yet" tooltip (F-13: the shape of the product ahead of its implementation); the
 * screen behind it exists, so that tooltip would now be the false statement. The convention itself
 * is untouched and still lives in `lib/notImplemented.ts`, which the screens use for the controls
 * that really are unbuilt.
 */
const RAIL_ORDER: readonly ScreenKey[] = ["projects", "roles-and-rates"];

/**
 * Where focus goes when the content frame has just been given a different screen — after a rail
 * activation (Reviewer R-03, SC-2-02) and after the error boundary's "Try again" re-mounts the
 * screen that crashed (Reviewer R-02, SC-1-09).
 *
 * One function, called from both, rather than two conventions: both moments end the same way — the
 * control that held keyboard focus is removed from the DOM by the very action it performed, and the
 * browser has nowhere to put focus but `document.body`. Found generically (`h1, h2`) so that a
 * screen added later needs nothing but a heading to participate.
 *
 * Silent when there is no heading to take it — the fallback of a crashed screen has none. Navigating
 * *to* a screen that is already showing the fallback therefore still leaves focus nowhere, which is
 * the open accessibility risk ADR-0010 names in its Konsekwencje and does not close. The one case
 * that is closed is a retry that crashes again, and it is closed inside `ScreenErrorBoundary`, which
 * owns the only element there is to focus at that moment.
 */
function focusScreenHeading(container: HTMLElement | null): void {
  container?.querySelector<HTMLElement>("h1, h2")?.focus();
}

interface AppShellProps {
  readonly backendStatus: BackendStatus;
  /** Which screen is mounted in the frame — the shell reads it, it never decides it. */
  readonly activeScreen: ScreenKey;
  readonly onNavigate: (screen: ScreenKey) => void;
  readonly children: ReactNode;
}

export function AppShell({ backendStatus, activeScreen, onNavigate, children }: AppShellProps) {
  const mainRef = useRef<HTMLElement>(null);
  // Skips the very first render: focus should follow a rail *activation*, not steal it from
  // wherever the page loaded with focus. Every render after the first one where `activeScreen`
  // changed is exactly a rail activation, because `App` holds `activeScreen` as state and only
  // `onNavigate` ever changes it.
  const isFirstRender = useRef(true);

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    // Activating a rail entry replaces it with a `<span>` (Reviewer R-03): the element that held
    // keyboard focus is removed from the DOM by the same click that activates it, so the browser
    // drops focus to `document.body` and a `Tab` afterwards starts back at the top of the page. The
    // shell moves focus to the heading of the screen it was just told to mount instead — the one
    // element every screen has, whichever screen `children` turns out to be, found generically so
    // that a screen added later needs nothing beyond a heading to participate. `main` renders new
    // `children` before this effect runs, so the heading being focused is always the new screen's.
    focusScreenHeading(mainRef.current);
  }, [activeScreen]);

  /**
   * The same landing place, for the other way a screen gets re-mounted inside this frame: the error
   * boundary's "Try again" (Reviewer R-02, SC-1-09). It is not a navigation — `activeScreen` does
   * not change — so the effect above never fires for it, and until this existed a retry dropped
   * focus to `document.body` exactly as a rail activation used to.
   */
  const focusRemountedScreen = useCallback(() => {
    focusScreenHeading(mainRef.current);
  }, []);

  return (
    <div className="app-shell">
      {/* The rail sits between the top of the page and the content, so a keyboard user gets a way
          past it. Visible only while focused. */}
      <a className="app-shell__skip-link" href="#app-shell-content">
        Skip to content
      </a>

      <header className="app-shell__topbar">
        <div className="app-shell__brand">
          {/* A tool mark, not the GlobalLogic logo: three bars, drawn in CSS, carrying no
              information — hence hidden from the accessibility tree (the name next to it is the
              accessible one). */}
          <span className="app-shell__brandmark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span className="app-shell__brand-text">
            <h1 className="app-shell__title">StafffingCalculator</h1>
            <p className="app-shell__subtitle">
              IT project staffing, cost, and profitability planner.
            </p>
          </span>
        </div>

        {/* Where the user is, as text — and it follows the rail, so the two cannot disagree about
            which screen is mounted. Not links: navigation is the rail, and a second set of controls
            doing the same thing is a second mechanism. */}
        <nav className="app-shell__breadcrumb" aria-label="Breadcrumb">
          <ol className="app-shell__breadcrumb-list">
            <li>Workspace</li>
            <li aria-current="page">{SCREEN_LABELS[activeScreen]}</li>
          </ol>
        </nav>

        <div className="app-shell__topright">
          {/* The state name is the text; `data-state` only picks the colour for it. */}
          <p className="app-shell__status" data-testid="backend-status" data-state={backendStatus}>
            Backend: {backendStatus}
          </p>
        </div>
      </header>

      <div className="app-shell__body">
        <nav className="app-shell__rail" aria-label="Sections">
          <ul className="app-shell__nav">
            {RAIL_ORDER.map((key) =>
              key === activeScreen ? (
                <li key={key}>
                  {/* The screen the user is already on. Not a control: there is nowhere for it to
                      go, and a button that does nothing when pressed is indistinguishable from one
                      that is broken. */}
                  <span
                    className="app-shell__nav-item app-shell__nav-item--current"
                    aria-current="page"
                  >
                    {SCREEN_LABELS[key]}
                  </span>
                </li>
              ) : (
                <li key={key}>
                  {/* A real `<button>`, so the platform activates it on Enter and Space; the shell
                      only reports the choice upwards and mounts nothing itself. It fetches nothing
                      either — a rail entry that prefetched the screen behind it would be a read the
                      user never asked for. */}
                  <button
                    type="button"
                    className="app-shell__nav-item"
                    onClick={() => onNavigate(key)}
                  >
                    {SCREEN_LABELS[key]}
                  </button>
                </li>
              ),
            )}
          </ul>
        </nav>

        <main className="app-shell__main" id="app-shell-content" ref={mainRef}>
          {/* The boundary wraps the screen slot, not the shell (SC-1-09, ADR-0010, point 1).
              Around the whole shell it would take the rail down with the screen — turning the one
              remaining way out into a page reload. Here, every screen the shell mounts is covered
              by the one mechanism, including the ones added later, and none of them carries a copy
              of it. `resetKey` is the active screen, so a crash does not follow the user to the
              next one (K-04). */}
          <div className="app-shell__content">
            <ScreenErrorBoundary resetKey={activeScreen} onRetry={focusRemountedScreen}>
              {children}
            </ScreenErrorBoundary>
          </div>
        </main>
      </div>
    </div>
  );
}
