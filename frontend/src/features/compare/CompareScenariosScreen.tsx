import { useCallback, useEffect, useId, useState } from "react";

import {
  ApiError,
  RequestTimeoutError,
  getProjects,
  getScenarioResultsComparison,
} from "../../api/client";
import { revenueModelKind, type RevenueRead } from "../../api/contracts/commercialTerms";
import type { ProjectListItem, ScenarioListItem } from "../../api/contracts/projects";
import {
  RESULTS_NOT_APPLICABLE,
  type AdditionalCostSource,
  type GatedResultField,
  type PersonnelCostSource,
  type ScenarioResults,
} from "../../api/contracts/scenarioResults";
import { NOT_APPLICABLE, formatMoneyString, formatPercentString } from "../../lib/money";
import {
  ADDITIONAL_COST_STATE_MESSAGES,
  PERSONNEL_COST_STATE_MESSAGES,
  PROFITABILITY_CURRENCY_MISMATCH,
  RESULTS_FIELD_UNAVAILABLE,
  revenueStateMessage,
} from "../projects/scenarioResultsText";
import "./CompareScenariosScreen.css";

/**
 * SC-7-04 — a workspace-level screen comparing the whole-life results of N scenarios of one
 * project (F-09 point 2, F-11; Issue #108), consuming the comparison endpoint SC-6-02 already
 * built and never computing anything of its own (ADR-0002 not engaged).
 *
 * **Two-level state, own to this screen (gate 1, Q1 = option A):** first a project, chosen from
 * `GET /projects` (reused, not re-fetched by a second mechanism — the same read
 * `ProjectListScreen` makes, including the scenarios already embedded per project); then a
 * multi-select of that project's own scenarios (checkboxes, gate 1: no client-side cap on how many
 * may be checked — `MAX_COMPARE_SCENARIOS` is the server's bound, and a caller who names more than
 * it gets the same `422` any other over-length request would, rendered as this screen's ordinary
 * "could not be loaded" state, gate 1 Q3). Only once "Compare selected" is pressed does this screen
 * read `GET …/scenarios/compare` — selecting scenarios is free, reading them is not.
 *
 * **Every row is read from itself, never from a shared, once-computed value** (K-01, K-02): each
 * of `RevenueCell`/`PersonnelCostCell`/`AdditionalCostCell`/`GatedMoneyCell`/`GatedPercentCell`
 * below takes exactly the one row's own field and nothing else — the same per-field decisions
 * `ScenarioResultsSection.tsx` (SC-7-02) already made for a single scenario, applied N times, once
 * per `<tr>`. A named non-computable state (`no_cost_rate`, `currency_mismatch`, …) is one row's
 * own word, never merged with a neighbour's (K-03).
 *
 * **All-or-nothing, never a partial table** (K-04): `getScenarioResultsComparison` is the *only*
 * read this screen makes for the compared rows — a `404` (one named scenario out of scope or
 * gone) or a `409` (a rate-source race on one of them) refuses the whole request, and this screen
 * renders that as one recognisable state for the whole comparison, with no table at all. There is
 * no fallback to N separate `getScenarioResults` calls anywhere in this file — a partial result
 * the server never sent would be one this client invented.
 *
 * **Row order and count are exactly the response's** (K-05): `results.map((row, index) => …)`
 * below is the only place rows are produced, and nothing sorts, re-keys or drops from that array —
 * the order scenarios were requested in (this screen's own selection order, the project's
 * scenario list order, filtered down to what was checked) is preserved all the way to the DOM.
 *
 * Out of scope, deliberately (Issue #108): staffing/FTE metrics (F-10 unbuilt), a chart (a table
 * satisfies "renders the metrics" without committing to a charting library), export, and any
 * client-side minimum/maximum on the number of scenarios compared.
 */

// --- The two reads this screen makes, as named outcomes -----------------------------------------

type ProjectsState =
  | { kind: "loading" }
  | { kind: "ready"; projects: ProjectListItem[] }
  | { kind: "denied" }
  | { kind: "timed-out" }
  | { kind: "failed" };

function toProjectsFailure(error: unknown): ProjectsState {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return { kind: "denied" };
  }
  if (error instanceof RequestTimeoutError) {
    return { kind: "timed-out" };
  }
  return { kind: "failed" };
}

type CompareReadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; results: ScenarioResults[] }
  | { kind: "refused" }
  | { kind: "conflict" }
  | { kind: "timed-out" }
  | { kind: "unreadable" }
  | { kind: "failed" };

function toCompareFailure(error: unknown): CompareReadState {
  if (error instanceof RequestTimeoutError) {
    return { kind: "timed-out" };
  }
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403 || error.status === 404) {
      // One rendered state for a denial and for the all-or-nothing 404 alike (mirrors K-04 of
      // SC-7-02) — the whole comparison is unavailable, never a table missing one row.
      return { kind: "refused" };
    }
    if (error.status === 409) {
      return { kind: "conflict" };
    }
    if (error.status >= 200 && error.status < 300) {
      return { kind: "unreadable" };
    }
  }
  return { kind: "failed" };
}

const COMPARE_FAILURE_MESSAGES: Readonly<Record<Exclude<CompareReadState["kind"], "idle" | "loading" | "ready">, string>> =
  {
    refused: "This comparison could not be shown.",
    conflict:
      "This comparison could not be loaded — a scenario changed while it was being read. Compare again.",
    "timed-out": "This comparison could not be loaded — the server did not answer in time.",
    unreadable:
      "This comparison could not be loaded — the server's answer was not in a form this screen can read.",
    failed: "This comparison could not be loaded — the server failed to answer.",
  };

/** Mirrors `ScenarioResultsSection.tsx`'s `RETRYABLE` (SC-7-02): every failure but the silent
 * refusal is worth asking again, `409` most of all — the race it names is a live one. */
const RETRYABLE_COMPARE: ReadonlySet<CompareReadState["kind"]> = new Set([
  "conflict",
  "timed-out",
  "unreadable",
  "failed",
]);

function idNameMap(entries: readonly { id: string; name: string }[]): ReadonlyMap<string, string> {
  return new Map(entries.map((entry) => [entry.id, entry.name]));
}

const UNKNOWN_SCENARIO_LABEL = "Unknown scenario";

interface CompareRequest {
  readonly projectId: string;
  readonly scenarioIds: readonly string[];
}

export function CompareScenariosScreen() {
  const headingId = useId();
  const [projectsState, setProjectsState] = useState<ProjectsState>({ kind: "loading" });
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedScenarioIds, setSelectedScenarioIds] = useState<ReadonlySet<string>>(new Set());
  const [compareRequest, setCompareRequest] = useState<CompareRequest | null>(null);
  const [compareRead, setCompareRead] = useState<CompareReadState>({ kind: "idle" });

  // --- Read 1: the project list, on mount (reused from `ProjectListScreen`'s own mechanism) -----

  useEffect(() => {
    const controller = new AbortController();
    let left = false;
    getProjects(controller.signal)
      .then((response) => {
        if (!left) {
          setProjectsState({ kind: "ready", projects: response.projects });
        }
      })
      .catch((error: unknown) => {
        if (!left) {
          setProjectsState(toProjectsFailure(error));
        }
      });
    return () => {
      left = true;
      controller.abort();
    };
  }, []);

  // --- Read 2: the comparison, only once "Compare selected" is pressed (K-04) --------------------

  useEffect(() => {
    if (compareRequest === null) {
      return;
    }
    const controller = new AbortController();
    let left = false;
    getScenarioResultsComparison(compareRequest.projectId, compareRequest.scenarioIds, controller.signal)
      .then((comparison) => {
        if (!left) {
          setCompareRead({ kind: "ready", results: comparison.results });
        }
      })
      .catch((error: unknown) => {
        if (!left) {
          setCompareRead(toCompareFailure(error));
        }
      });
    return () => {
      left = true;
      controller.abort();
    };
  }, [compareRequest]);

  const projects = projectsState.kind === "ready" ? projectsState.projects : [];
  const selectedProject = projects.find((project) => project.id === selectedProjectId);

  const selectProject = useCallback((projectId: string) => {
    setSelectedProjectId(projectId);
    setSelectedScenarioIds(new Set());
    setCompareRequest(null);
    setCompareRead({ kind: "idle" });
  }, []);

  const toggleScenario = useCallback((scenarioId: string) => {
    setSelectedScenarioIds((previous) => {
      const next = new Set(previous);
      if (next.has(scenarioId)) {
        next.delete(scenarioId);
      } else {
        next.add(scenarioId);
      }
      return next;
    });
  }, []);

  /** The project's own scenario order, filtered to what is checked — the request order this
   * screen chooses, and the one `compareRequest.scenarioIds` ever carries (K-05). */
  function orderedSelection(project: ProjectListItem): string[] {
    return project.scenarios
      .filter((scenario) => selectedScenarioIds.has(scenario.id))
      .map((scenario) => scenario.id);
  }

  function compareSelected() {
    if (selectedProject === undefined) {
      return;
    }
    setCompareRead({ kind: "loading" });
    setCompareRequest({ projectId: selectedProject.id, scenarioIds: orderedSelection(selectedProject) });
  }

  function compareAgain() {
    if (compareRequest === null) {
      return;
    }
    setCompareRead({ kind: "loading" });
    // A fresh object even with the same ids/project — the effect above keys on identity, not on a
    // deep comparison, so retrying the exact same request still triggers a new read.
    setCompareRequest({ ...compareRequest });
  }

  return (
    <section className="cs" aria-labelledby={headingId}>
      <h2 id={headingId} className="cs__title" tabIndex={-1}>
        Compare scenarios
      </h2>
      {projectsState.kind === "ready" && (
        <p className="cs__description">
          Pick one project, choose which of its scenarios to compare, then read the comparison.
        </p>
      )}

      {projectsState.kind === "loading" && (
        <p role="status" className="cs__message">
          Loading projects…
        </p>
      )}
      {/* A denied read renders no projects and no controls — never data that is hidden afterwards
          (ADR-0005), the same discipline `ProjectListScreen`/`WorkingCalendarsScreen` already
          keep. */}
      {projectsState.kind === "denied" && (
        <p role="status" className="cs__message cs__message--attention">
          You do not have permission to view projects.
        </p>
      )}
      {projectsState.kind === "timed-out" && (
        <p role="status" className="cs__message cs__message--attention">
          Projects could not be loaded — request timed out.
        </p>
      )}
      {projectsState.kind === "failed" && (
        <p role="status" className="cs__message cs__message--attention">
          Projects could not be loaded.
        </p>
      )}

      {projectsState.kind === "ready" && (
        <div className="cs__grid">
          <ProjectPicker
            projects={projects}
            selectedProjectId={selectedProjectId}
            onSelect={selectProject}
          />

          {selectedProject !== undefined && (
            <div className="cs__panel card">
              <ScenarioPicker
                project={selectedProject}
                selected={selectedScenarioIds}
                onToggle={toggleScenario}
              />
              <div className="cs__actions">
                <button
                  type="button"
                  className="button button--primary"
                  disabled={selectedScenarioIds.size === 0}
                  onClick={compareSelected}
                >
                  Compare selected
                </button>
              </div>

              <CompareResultsSection
                state={compareRead}
                scenarioNames={idNameMap(selectedProject.scenarios)}
                onRetry={compareAgain}
              />
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function ProjectPicker({
  projects,
  selectedProjectId,
  onSelect,
}: {
  projects: ProjectListItem[];
  selectedProjectId: string | null;
  onSelect: (projectId: string) => void;
}) {
  if (projects.length === 0) {
    return (
      <p role="status" className="cs__message">
        No projects to show.
      </p>
    );
  }
  return (
    <div className="cs__panel card" aria-labelledby="cs-project-picker-heading">
      <h3 id="cs-project-picker-heading" className="cs__panel-title">
        Project
      </h3>
      <ul className="cs__project-list">
        {projects.map((project) => (
          <li key={project.id}>
            <button
              type="button"
              className="cs__project-button"
              aria-pressed={project.id === selectedProjectId}
              onClick={() => onSelect(project.id)}
            >
              {project.name}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ScenarioPicker({
  project,
  selected,
  onToggle,
}: {
  project: ProjectListItem;
  selected: ReadonlySet<string>;
  onToggle: (scenarioId: string) => void;
}) {
  if (project.scenarios.length === 0) {
    return (
      <p role="status" className="cs__message">
        {project.name} has no scenarios to compare.
      </p>
    );
  }
  return (
    <fieldset className="cs__scenario-picker">
      <legend className="cs__panel-title">Scenarios of {project.name}</legend>
      <ul className="cs__scenario-list">
        {project.scenarios.map((scenario: ScenarioListItem) => (
          <li key={scenario.id}>
            <label className="cs__scenario-label">
              <input
                type="checkbox"
                checked={selected.has(scenario.id)}
                onChange={() => onToggle(scenario.id)}
              />
              {scenario.name}
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

function CompareResultsSection({
  state,
  scenarioNames,
  onRetry,
}: {
  state: CompareReadState;
  scenarioNames: ReadonlyMap<string, string>;
  onRetry: () => void;
}) {
  if (state.kind === "idle") {
    return null;
  }
  if (state.kind === "loading") {
    return (
      <p role="status" className="cs__message">
        Comparing scenarios…
      </p>
    );
  }
  if (state.kind !== "ready") {
    return (
      <>
        <p role="status" className="cs__message cs__message--attention" data-compare-failure={state.kind}>
          {COMPARE_FAILURE_MESSAGES[state.kind]}
        </p>
        {RETRYABLE_COMPARE.has(state.kind) && (
          <div className="cs__actions">
            <button type="button" className="button button--secondary" onClick={onRetry}>
              Compare again
            </button>
          </div>
        )}
      </>
    );
  }

  const { results } = state;
  if (results.length === 0) {
    return (
      <p role="status" className="cs__message">
        No scenarios selected.
      </p>
    );
  }

  return (
    <table className="cs__table">
      <caption className="visually-hidden">Comparison of the selected scenarios</caption>
      <thead>
        <tr>
          <th scope="col">Scenario</th>
          <th scope="col">Revenue</th>
          <th scope="col">Personnel cost</th>
          <th scope="col">Additional costs</th>
          <th scope="col">Scenario cost</th>
          <th scope="col">Profit</th>
          <th scope="col">Margin</th>
          <th scope="col">Markup</th>
          <th scope="col">Notes</th>
        </tr>
      </thead>
      <tbody>
        {/* `results.map` in the response's own order — nothing here sorts or re-keys by a field of
            the row (K-05). Every cell below reads only this one row's own fields (K-01, K-02,
            K-03): there is no value hoisted out of the loop and shared across rows. */}
        {results.map((row, index) => (
          <tr key={`${row.scenario_id}-${index}`}>
            <th scope="row">{scenarioNames.get(row.scenario_id) ?? UNKNOWN_SCENARIO_LABEL}</th>
            <RevenueCell revenue={row.revenue} />
            <PersonnelCostCell source={row.personnel_cost} />
            <AdditionalCostCell source={row.additional_cost} />
            <GatedMoneyCell
              value={row.included_cost}
              currency={row.revenue.state === "calculated" ? row.revenue.currency : null}
            />
            <GatedMoneyCell
              value={row.profit}
              currency={row.revenue.state === "calculated" ? row.revenue.currency : null}
            />
            <GatedPercentCell value={row.margin} />
            <GatedPercentCell value={row.markup} />
            <td data-profitability-state={row.profitability_state}>
              {row.profitability_state === "currency_mismatch" ? PROFITABILITY_CURRENCY_MISMATCH : ""}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** This row's revenue, or the named state that withholds it — read from this row's own `revenue`
 * and nothing else (K-01, K-03). */
function RevenueCell({ revenue }: { revenue: RevenueRead }) {
  const model = revenueModelKind(revenue);
  if (revenue.state === "calculated") {
    return (
      <td data-revenue-state={revenue.state}>{formatMoneyString(revenue.amount, revenue.currency)}</td>
    );
  }
  return <td data-revenue-state={revenue.state}>{revenueStateMessage(revenue.state, model)}</td>;
}

/** This row's own personnel-cost gate and state, read independently of every other row (K-02):
 * `amount === null` (the gate closed for this project) renders the same generic sentence
 * `ScenarioResultsSection` uses, regardless of what `state` says on this very row. */
function PersonnelCostCell({ source }: { source: PersonnelCostSource }) {
  if (source.amount === null) {
    return <td data-personnel-cost-state="unavailable">{RESULTS_FIELD_UNAVAILABLE}</td>;
  }
  if (source.state !== "calculated") {
    return (
      <td data-personnel-cost-state={source.state}>{PERSONNEL_COST_STATE_MESSAGES[source.state]}</td>
    );
  }
  return (
    <td data-personnel-cost-state="calculated">
      {formatMoneyString(source.amount, source.currency ?? "")}
    </td>
  );
}

/** Never gated (ADR-0014, point 11) — this row's own named state, or its number. */
function AdditionalCostCell({ source }: { source: AdditionalCostSource }) {
  if (source.state !== "calculated") {
    return (
      <td data-additional-cost-state={source.state}>{ADDITIONAL_COST_STATE_MESSAGES[source.state]}</td>
    );
  }
  return (
    <td data-additional-cost-state="calculated">
      {formatMoneyString(source.amount, source.currency ?? "")}
    </td>
  );
}

/** One of `included_cost`/`profit`, exactly as this row's own backend answer carries it: `null`
 * (gate closed) is the generic sentence, `"n/a"` is `lib/money.ts`'s own "Not applicable", a real
 * decimal string goes through `formatMoneyString` and nothing else (K-01, K-02). */
function GatedMoneyCell({ value, currency }: { value: GatedResultField; currency: string | null }) {
  if (value === null) {
    return <td data-result-state="unavailable">{RESULTS_FIELD_UNAVAILABLE}</td>;
  }
  if (value === RESULTS_NOT_APPLICABLE) {
    return <td data-result-state="not-applicable">{NOT_APPLICABLE}</td>;
  }
  return <td data-result-state="calculated">{formatMoneyString(value, currency ?? "")}</td>;
}

/** One of `margin`/`markup`, this row's own value (K-01, K-02). */
function GatedPercentCell({ value }: { value: GatedResultField }) {
  if (value === null) {
    return <td data-result-state="unavailable">{RESULTS_FIELD_UNAVAILABLE}</td>;
  }
  return (
    <td data-result-state={value === RESULTS_NOT_APPLICABLE ? "not-applicable" : "calculated"}>
      {formatPercentString(value)}
    </td>
  );
}
