import type { ReactNode } from "react";

import { handleNotYetImplemented, notImplementedHint } from "../lib/notImplemented";
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
 */

export type BackendStatus = "checking" | "ok" | "unreachable";

interface RailItem {
  readonly key: string;
  readonly label: string;
  /** Absent for the screen the user is on; otherwise the tooltip saying what has to exist first. */
  readonly hint?: string;
}

/**
 * Two entries, against the prototype's twenty-four. The rail names what this application actually
 * has: Projects (SC-1-06, wired) and Roles & rates (SC-2-02, in progress on another branch).
 * Entries for screens with no backend behind them would be a promise the product cannot keep — and
 * unlike a disabled entry, nothing about them would say so.
 */
const RAIL_ITEMS: readonly RailItem[] = [
  { key: "projects", label: "Projects" },
  { key: "roles-and-rates", label: "Roles & rates", hint: notImplementedHint("planned in SC-2-02") },
];

interface AppShellProps {
  readonly backendStatus: BackendStatus;
  readonly children: ReactNode;
}

export function AppShell({ backendStatus, children }: AppShellProps) {
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

        {/* Where the user is, as text. Not links: there is one screen, and a breadcrumb trail of
            controls that navigate nowhere would be a worse lie than plain words. */}
        <nav className="app-shell__breadcrumb" aria-label="Breadcrumb">
          <ol className="app-shell__breadcrumb-list">
            <li>Workspace</li>
            <li aria-current="page">Projects</li>
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
            {RAIL_ITEMS.map((item) =>
              item.hint === undefined ? (
                <li key={item.key}>
                  {/* The screen the user is already on. Not a control: there is nowhere for it to
                      go, and a button that does nothing when pressed is indistinguishable from one
                      that is broken. */}
                  <span
                    className="app-shell__nav-item app-shell__nav-item--current"
                    aria-current="page"
                  >
                    {item.label}
                  </span>
                </li>
              ) : (
                <li key={item.key}>
                  <button
                    type="button"
                    className="app-shell__nav-item"
                    aria-disabled="true"
                    title={item.hint}
                    onClick={handleNotYetImplemented}
                  >
                    {item.label}
                  </button>
                </li>
              ),
            )}
          </ul>
        </nav>

        <main className="app-shell__main" id="app-shell-content">
          <div className="app-shell__content">{children}</div>
        </main>
      </div>
    </div>
  );
}
