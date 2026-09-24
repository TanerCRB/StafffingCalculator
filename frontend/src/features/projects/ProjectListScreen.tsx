import { useEffect, useState } from "react";

import { ApiError, RequestTimeoutError, getProjects } from "../../api/client";
import type { ProjectListItem, ScenarioListItem } from "../../api/contracts/projects";
import { formatDeliveryPeriod } from "../../lib/dates";
import { formatPercentString } from "../../lib/money";
import { handleNotYetImplemented, notImplementedHint } from "../../lib/notImplemented";
import { DuplicateScenarioControl } from "./DuplicateScenarioControl";
import { ScenarioCommercialTermsSection } from "./ScenarioCommercialTermsSection";
import { missingInputLabel } from "./scenarioInputLabels";
import "./ProjectListScreen.css";

/**
 * SC-1-06 — the project list with the scenarios of the selected project.
 *
 * Read only, with one exception added by SC-4-06 (gate 1, D-2 = option A): every scenario card
 * carries a `ScenarioCommercialTermsSection`, which reads that scenario's commercial rule and revenue
 * itself and can set a Time & Material rule. Everything else on this screen still writes nothing.
 *
 * The screen takes no access or visibility decision of its own (NF-04, ADR-0005): every row it
 * shows came from the API in that shape, including an archived project. There is no client-side
 * filter, no client-side sort and no locally invented empty list — an empty list is something
 * only the server can say.
 *
 * The row controls (View/Edit/Copy/Archive/Add scenario) and the list toolbar (search, filters,
 * add project) are rendered and keyboard reachable but wired to nothing: the screens behind them
 * are SC-1-02..04 (Issue #3, out of scope 4) and a separate search/filter story (Issue #3, out of
 * scope 2). They are `aria-disabled` with a tooltip rather than `disabled`, so that they stay in
 * the tab order and remain announced — a user may see the shape of the product ahead of its
 * implementation.
 *
 * Layout reference: `Wymagania/UI/Project List.jpeg` — a reference, not a specification (Issue #3,
 * out of scope 1). Colours and type come from `src/styles/tokens.css`, never from a literal here.
 */

type RowAction = { readonly key: string; readonly label: string };

const ROW_ACTIONS: readonly RowAction[] = [
  { key: "view", label: "View" },
  { key: "edit", label: "Edit" },
  { key: "copy", label: "Copy" },
  { key: "archive", label: "Archive" },
  { key: "add-scenario", label: "Add scenario" },
];

/*
 * The wording and the placeholder handler come from `src/lib/notImplemented.ts` — one convention
 * for every control that is rendered, announced and wired to nothing (the navigation rail is the
 * other user). The reasons below are this screen's own; the shape of the sentence is not.
 */
const NOT_IMPLEMENTED_HINT = notImplementedHint("planned in SC-1-02..04");
const SEARCH_AND_FILTER_HINT = notImplementedHint(
  "search and filtering are a separate story (Issue #3, out of scope 2)",
);
const ADD_PROJECT_HINT = notImplementedHint(
  "creating a project from this screen is a separate task",
);

type ScreenState =
  | { kind: "loading" }
  | { kind: "ready"; projects: ProjectListItem[] }
  | { kind: "denied" }
  | { kind: "timed-out" }
  | { kind: "failed" };

function toFailureState(error: unknown): ScreenState {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return { kind: "denied" };
  }
  if (error instanceof RequestTimeoutError) {
    return { kind: "timed-out" };
  }
  return { kind: "failed" };
}

export function ProjectListScreen() {
  const [state, setState] = useState<ScreenState>({ kind: "loading" });
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);

  useEffect(() => {
    // Leaving this screen ends the read, it does not merely stop listening to it (SC-1-09, K-05).
    //
    // The two halves do different jobs and both are needed. `controller.abort()` reaches `fetch`
    // and ends the request itself: a bounce off this screen used to leave a `GET /projects` running
    // to completion, holding one of the browser's six same-origin HTTP/1.1 sockets against whatever
    // screen the user actually moved to (the catalogue reads ~19.7 MB — docs/PLAN.md). `left`
    // guards the state updates, including the rejection the abort itself produces: an aborted read
    // is the user's decision, not a failure, and must never render as "Projects could not be
    // loaded." on a screen that is already gone. The flag is set in the same cleanup that aborts,
    // so it is always true by the time that rejection arrives.
    //
    // The flag alone — which is what this effect had — is the defect this replaces, and it is not a
    // hypothetical one: the same shape survived a green suite in `CatalogScreen` until Reviewer
    // R-02 read it (SC-2-04). It looks like cancellation in a diff and cancels nothing.
    const controller = new AbortController();
    let left = false;
    getProjects(controller.signal)
      .then((response) => {
        if (!left) {
          setState({ kind: "ready", projects: response.projects });
        }
      })
      .catch((error: unknown) => {
        if (!left) {
          setState(toFailureState(error));
        }
      });
    return () => {
      left = true;
      controller.abort();
    };
  }, []);

  const projects = state.kind === "ready" ? state.projects : [];
  const selectedProject = projects.find((project) => project.id === selectedProjectId);

  /**
   * SC-6-03 — the `201` body of a successful duplicate, inserted into the scenario array of the
   * project it belongs to and nothing else. Matched by `project.id`, never by position in
   * `projects`: the array a `.map` walks does not promise the duplicated project is first, and an
   * insertion keyed by index would land the new row on the wrong project as soon as it is not
   * (K-06; ADR-0009, addendum 2026-09-24). No `GET /projects` runs here (gate 1, Q2).
   */
  function addDuplicatedScenario(projectId: string, scenario: ScenarioListItem) {
    setState((previous) => {
      if (previous.kind !== "ready") {
        return previous;
      }
      return {
        kind: "ready",
        projects: previous.projects.map((project) =>
          project.id === projectId
            ? { ...project, scenarios: [...project.scenarios, scenario] }
            : project,
        ),
      };
    });
  }

  return (
    <section className="project-list" aria-labelledby="project-list-heading">
      <div className="project-list__grid">
        <div className="card project-list__main">
          <div className="project-list__toolbar-row">
            {/* `tabIndex={-1}`: focusable by script, never by Tab. `AppShell` focuses this heading
                after a rail activation mounts this screen, so keyboard focus does not fall through
                to `document.body` when the rail entry that held it leaves the DOM (Reviewer
                R-03, SC-2-02). */}
            <h2 id="project-list-heading" className="card__title" tabIndex={-1}>
              Projects
            </h2>
            {/* The toolbar belongs to a list that exists. A denied read renders a screen with no
                action controls at all — not a toolbar above an empty table (ADR-0005). */}
            {state.kind === "ready" && <ListToolbar />}
          </div>

          {state.kind === "loading" && <p className="project-list__message">Loading projects…</p>}
          {/* A denied request renders a screen with no rows and no action controls — never data
              that is hidden afterwards (ADR-0005). */}
          {state.kind === "denied" && (
            <p role="status" className="project-list__message project-list__message--attention">
              You do not have permission to view projects.
            </p>
          )}
          {state.kind === "timed-out" && (
            <p role="status" className="project-list__message project-list__message--attention">
              Projects could not be loaded — request timed out.
            </p>
          )}
          {state.kind === "failed" && (
            <p role="status" className="project-list__message project-list__message--attention">
              Projects could not be loaded.
            </p>
          )}

          {state.kind === "ready" && projects.length === 0 && (
            <p role="status" className="project-list__message">
              No projects to show.
            </p>
          )}

          {state.kind === "ready" && projects.length > 0 && (
            <table className="project-list__table">
              {/* The layout has no room for a visible caption; a screen reader still gets one. */}
              <caption className="visually-hidden">Projects you have access to</caption>
              <thead>
                <tr>
                  <th scope="col">Project name</th>
                  <th scope="col">Client</th>
                  <th scope="col">Implementation period</th>
                  <th scope="col">Status</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {projects.map((project) => (
                  <tr
                    key={project.id}
                    className={
                      project.id === selectedProjectId
                        ? "project-list__row project-list__row--selected"
                        : "project-list__row"
                    }
                  >
                    <th scope="row">
                      <button
                        type="button"
                        className="project-list__name-button"
                        aria-pressed={project.id === selectedProjectId}
                        onClick={() => setSelectedProjectId(project.id)}
                      >
                        {project.name}
                      </button>
                    </th>
                    <td className="project-list__client">{project.client}</td>
                    <td className="project-list__period">
                      {formatDeliveryPeriod(
                        project.delivery_period.start,
                        project.delivery_period.end,
                      )}
                    </td>
                    {/* The status label is the server's word, rendered as text — the badge fill is
                        an additional signal on top of it, never a replacement (NF-08). */}
                    <td>
                      <span className="badge" data-project-status={project.status}>
                        {project.status}
                      </span>
                    </td>
                    <td>
                      <div className="project-list__row-actions">
                        {ROW_ACTIONS.map((action) => (
                          <button
                            key={action.key}
                            type="button"
                            className="button button--quiet"
                            aria-label={`${action.label} ${project.name}`}
                            aria-disabled="true"
                            title={NOT_IMPLEMENTED_HINT}
                            onClick={handleNotYetImplemented}
                          >
                            {action.label}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {state.kind === "ready" && (
          <section
            className="card project-list__details"
            aria-labelledby="scenario-details-heading"
          >
            {/* The mockup calls this panel "Calculation details". The word here stays "Scenario":
                gate-1 decision 5 (Issue #3) made Scenario the single name of that entity, in the
                API, the data model and the UI. */}
            <h2 id="scenario-details-heading" className="project-list__details-title">
              Scenario details
            </h2>
            {selectedProject === undefined ? (
              <p className="project-list__details-empty">Select a project to see its scenarios.</p>
            ) : (
              <ScenarioDetails project={selectedProject} onScenarioDuplicated={addDuplicatedScenario} />
            )}
          </section>
        )}
      </div>
    </section>
  );
}

/**
 * Search, Filters and Add project as they appear in the mockup — rendered, focusable, announced,
 * and connected to nothing. No filtering runs on the client (Issue #3, out of scope 2: the list
 * is exactly the API's answer), and Add project does not call `POST /projects` even though the
 * endpoint exists (SC-1-01) — that screen is a separate task with its own criteria.
 */
function ListToolbar() {
  return (
    <div className="project-list__toolbar">
      <input
        type="search"
        className="input project-list__search"
        aria-label="Search projects"
        placeholder="Search"
        readOnly
        aria-disabled="true"
        title={SEARCH_AND_FILTER_HINT}
      />
      <button
        type="button"
        className="button button--secondary"
        aria-disabled="true"
        title={SEARCH_AND_FILTER_HINT}
        onClick={handleNotYetImplemented}
      >
        Filters
      </button>
      <button
        type="button"
        className="button button--primary"
        aria-disabled="true"
        title={ADD_PROJECT_HINT}
        onClick={handleNotYetImplemented}
      >
        Add project
      </button>
    </div>
  );
}

interface ScenarioDetailsProps {
  readonly project: ProjectListItem;
  readonly onScenarioDuplicated: (projectId: string, scenario: ScenarioListItem) => void;
}

function ScenarioDetails({ project, onScenarioDuplicated }: ScenarioDetailsProps) {
  if (project.scenarios.length === 0) {
    return (
      <>
        <p className="project-list__details-subtitle">Scenarios of {project.name}</p>
        <p className="project-list__details-empty">This project has no scenarios yet.</p>
      </>
    );
  }

  return (
    <>
      <p className="project-list__details-subtitle">Scenarios of {project.name}</p>
      <p className="project-list__details-meta">
        Reporting currency: {project.reporting_currency}
      </p>
      <ul className="scenario-list">
        {project.scenarios.map((scenario) => (
          <li className="scenario-card" key={scenario.id}>
            <h3 className="scenario-card__title">{scenario.name}</h3>
            {/* Status in words. Colour alone would carry the same information for nobody who
                cannot see it, and for no test — the fill is added to the word, not instead of
                it. */}
            <p className="scenario-card__status badge" data-scenario-status={scenario.status}>
              Status: {scenario.status}
            </p>
            <p className="scenario-card__readiness" data-ready={String(scenario.ready_for_approval)}>
              {scenario.ready_for_approval ? "Ready for approval" : "Not ready for approval"}
            </p>
            {!scenario.ready_for_approval && scenario.missing_inputs.length > 0 && (
              <p className="scenario-card__gaps">
                Missing inputs: {scenario.missing_inputs.map(missingInputLabel).join(", ")}
              </p>
            )}
            <p className="scenario-card__metric">
              Target margin:{" "}
              {/* The API value stays a decimal string all the way to the screen (ADR-0002) —
                  Number() here would round "1.005" down to 1.00%. */}
              {scenario.target_margin_percent === null ||
              scenario.target_margin_percent === undefined
                ? "Not provided"
                : formatPercentString(scenario.target_margin_percent)}
            </p>
            {/* SC-4-06: its own read, its own failure states, its own write. Keyed by the card, so
                switching projects unmounts it and aborts its read (ADR-0010, point 7). */}
            <ScenarioCommercialTermsSection
              projectId={project.id}
              scenarioId={scenario.id}
              scenarioName={scenario.name}
            />
            {/* SC-6-03: available regardless of scenario.status — duplication never writes to the
                source, so it is not subject to the approved-immutability hiding rule above (K-02). */}
            <DuplicateScenarioControl
              projectId={project.id}
              scenarioId={scenario.id}
              scenarioName={scenario.name}
              onDuplicated={onScenarioDuplicated}
            />
          </li>
        ))}
      </ul>
    </>
  );
}
