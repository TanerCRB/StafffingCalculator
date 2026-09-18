import { useEffect, useState } from "react";

import { ApiError, RequestTimeoutError, getProjects } from "../../api/client";
import type { ProjectListItem } from "../../api/contracts/projects";
import { formatDeliveryPeriod } from "../../lib/dates";
import { formatPercentString } from "../../lib/money";
import { missingInputLabel } from "./scenarioInputLabels";

/**
 * SC-1-06 — the project list with the scenarios of the selected project. Read only.
 *
 * The screen takes no access or visibility decision of its own (NF-04, ADR-0005): every row it
 * shows came from the API in that shape, including an archived project. There is no client-side
 * filter, no client-side sort and no locally invented empty list — an empty list is something
 * only the server can say.
 *
 * The row controls (View/Edit/Copy/Archive/Add scenario) are rendered and keyboard reachable but
 * wired to nothing: the screens behind them are SC-1-02..04 (Issue #3, out of scope 4). They are
 * `aria-disabled` with a tooltip rather than `disabled`, so that they stay in the tab order and
 * remain announced — a user may see the shape of the product ahead of its implementation.
 */

type RowAction = { readonly key: string; readonly label: string };

const ROW_ACTIONS: readonly RowAction[] = [
  { key: "view", label: "View" },
  { key: "edit", label: "Edit" },
  { key: "copy", label: "Copy" },
  { key: "archive", label: "Archive" },
  { key: "add-scenario", label: "Add scenario" },
];

const NOT_IMPLEMENTED_HINT = "Not implemented yet — planned in SC-1-02..04";

/**
 * The placeholder handler for every row control. It does nothing, on purpose, and it is named so
 * that the next developer sees a placeholder to replace (SC-1-02..04) rather than a mechanism to
 * add code next to. Nothing here protects anything: a real handler hung on the same button would
 * run, `aria-disabled` or not — the block, when these actions exist, is the server's.
 */
function handleNotYetImplemented(): void {
  // Intentionally empty — see the comment above.
}

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
    let cancelled = false;
    getProjects()
      .then((response) => {
        if (!cancelled) {
          setState({ kind: "ready", projects: response.projects });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState(toFailureState(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const projects = state.kind === "ready" ? state.projects : [];
  const selectedProject = projects.find((project) => project.id === selectedProjectId);

  return (
    <section aria-labelledby="project-list-heading">
      <h2 id="project-list-heading">Projects</h2>

      {state.kind === "loading" && <p>Loading projects…</p>}
      {/* A denied request renders a screen with no rows and no action controls — never data
          that is hidden afterwards (ADR-0005). */}
      {state.kind === "denied" && (
        <p role="status">You do not have permission to view projects.</p>
      )}
      {state.kind === "timed-out" && (
        <p role="status">Projects could not be loaded — request timed out.</p>
      )}
      {state.kind === "failed" && <p role="status">Projects could not be loaded.</p>}

      {state.kind === "ready" && projects.length === 0 && (
        <p role="status">No projects to show.</p>
      )}

      {state.kind === "ready" && projects.length > 0 && (
        <table>
          <caption>Projects you have access to</caption>
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
              <tr key={project.id}>
                <th scope="row">
                  <button
                    type="button"
                    aria-pressed={project.id === selectedProjectId}
                    onClick={() => setSelectedProjectId(project.id)}
                  >
                    {project.name}
                  </button>
                </th>
                <td>{project.client}</td>
                <td>
                  {formatDeliveryPeriod(project.delivery_period.start, project.delivery_period.end)}
                </td>
                {/* The status label is the server's word, rendered as text — not a colour, not a
                    locally computed state. */}
                <td>{project.status}</td>
                <td>
                  {ROW_ACTIONS.map((action) => (
                    <button
                      key={action.key}
                      type="button"
                      aria-label={`${action.label} ${project.name}`}
                      aria-disabled="true"
                      title={NOT_IMPLEMENTED_HINT}
                      onClick={handleNotYetImplemented}
                    >
                      {action.label}
                    </button>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {state.kind === "ready" && (
        <section aria-labelledby="scenario-details-heading">
          <h2 id="scenario-details-heading">Scenario details</h2>
          {selectedProject === undefined ? (
            <p>Select a project to see its scenarios.</p>
          ) : (
            <ScenarioDetails project={selectedProject} />
          )}
        </section>
      )}
    </section>
  );
}

function ScenarioDetails({ project }: { project: ProjectListItem }) {
  if (project.scenarios.length === 0) {
    return (
      <>
        <p>Scenarios of {project.name}</p>
        <p>This project has no scenarios yet.</p>
      </>
    );
  }

  return (
    <>
      <p>Scenarios of {project.name}</p>
      <p>Reporting currency: {project.reporting_currency}</p>
      <ul>
        {project.scenarios.map((scenario) => (
          <li key={scenario.id}>
            <h3>{scenario.name}</h3>
            {/* Status in words. Colour alone would carry the same information for nobody who
                cannot see it, and for no test. */}
            <p>Status: {scenario.status}</p>
            <p>
              {scenario.ready_for_approval
                ? "Ready for approval"
                : "Not ready for approval"}
            </p>
            {!scenario.ready_for_approval && scenario.missing_inputs.length > 0 && (
              <p>
                Missing inputs: {scenario.missing_inputs.map(missingInputLabel).join(", ")}
              </p>
            )}
            <p>
              Target margin:{" "}
              {/* The API value stays a decimal string all the way to the screen (ADR-0002) —
                  Number() here would round "1.005" down to 1.00%. */}
              {scenario.target_margin_percent === null ||
              scenario.target_margin_percent === undefined
                ? "Not provided"
                : formatPercentString(scenario.target_margin_percent)}
            </p>
          </li>
        ))}
      </ul>
    </>
  );
}
