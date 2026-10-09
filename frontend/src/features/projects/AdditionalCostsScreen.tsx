import { useEffect, useState } from "react";

import { ApiError, getProjects } from "../../api/client";
import type { ProjectListItem } from "../../api/contracts/projects";
import { ScenarioAdditionalCostsSection } from "./ScenarioAdditionalCostsSection";
import { PROJECT_LIST_MESSAGES } from "./projectListMessages";
import {
  COSTS_NO_PROJECTS, COSTS_NO_SCENARIOS, COSTS_NO_SELECTION, COSTS_PROJECT_LABEL,
  COSTS_PROJECTS_DENIED, COSTS_PROJECTS_FAILED, COSTS_PROJECTS_LOADING, COSTS_PROJECTS_RETRY,
  COSTS_SCENARIO_LABEL, COSTS_SCREEN_DESCRIPTION, COSTS_SCREEN_TITLE, COSTS_SELECT_PROJECT,
  COSTS_SELECT_SCENARIO,
} from "./additionalCostsScreenText";
import "./AdditionalCostsScreen.css";

type ProjectsState =
  | { kind: "loading" }
  | { kind: "ready"; projects: ProjectListItem[]; total: number }
  | { kind: "denied" | "failed" };

const PAGE_SIZE = 20;

/** A cost-only destination: project and scenario context comes from the validated API list. */
export function AdditionalCostsScreen() {
  const [projects, setProjects] = useState<ProjectsState>({ kind: "loading" });
  const [projectId, setProjectId] = useState("");
  const [scenarioId, setScenarioId] = useState("");
  const [page, setPage] = useState(0);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let left = false;
    setProjects({ kind: "loading" });
    getProjects(controller.signal, { limit: PAGE_SIZE, offset: page * PAGE_SIZE })
      .then((response) => {
        if (!left) setProjects({ kind: "ready", projects: response.projects, total: response.total });
      })
      .catch((error: unknown) => {
        if (!left) setProjects({
          kind: error instanceof ApiError && (error.status === 401 || error.status === 403)
            ? "denied" : "failed",
        });
      });
    return () => { left = true; controller.abort(); };
  }, [page, retry]);

  function changePage(next: number) {
    setProjectId("");
    setScenarioId("");
    setProjects({ kind: "loading" });
    setPage(next);
  }

  const selectedProject = projects.kind === "ready"
    ? projects.projects.find((project) => project.id === projectId) : undefined;
  const selectedScenario = selectedProject?.scenarios.find((scenario) => scenario.id === scenarioId);

  return (
    <div className="costs-screen">
      <h2 className="costs-screen__title" tabIndex={-1}>{COSTS_SCREEN_TITLE}</h2>
      <p className="costs-screen__description">{COSTS_SCREEN_DESCRIPTION}</p>
      {projects.kind === "loading" && <p role="status">{COSTS_PROJECTS_LOADING}</p>}
      {projects.kind === "denied" && <p role="alert">{COSTS_PROJECTS_DENIED}</p>}
      {projects.kind === "failed" && <div role="alert">
        <p>{COSTS_PROJECTS_FAILED}</p>
        <button type="button" className="button button--quiet" onClick={() => setRetry((n) => n + 1)}>{COSTS_PROJECTS_RETRY}</button>
      </div>}
      {projects.kind === "ready" && (projects.total === 0
        ? <p>{COSTS_NO_PROJECTS}</p>
        : <>
          <div className="costs-screen__context">
            <nav className="costs-screen__pages" aria-label={PROJECT_LIST_MESSAGES.paginationLabel}>
              <button type="button" className="button button--quiet" disabled={page === 0}
                onClick={() => changePage(page - 1)}>{PROJECT_LIST_MESSAGES.previous}</button>
              <span>{PROJECT_LIST_MESSAGES.page(page + 1, Math.ceil(projects.total / PAGE_SIZE))}</span>
              <button type="button" className="button button--quiet"
                disabled={(page + 1) * PAGE_SIZE >= projects.total}
                onClick={() => changePage(page + 1)}>{PROJECT_LIST_MESSAGES.next}</button>
            </nav>
            <label className="costs-screen__selector">
              {COSTS_PROJECT_LABEL}
              <select className="input" value={projectId} onChange={(event) => {
                setProjectId(event.target.value);
                setScenarioId("");
              }}>
                <option value="">{COSTS_SELECT_PROJECT}</option>
                {projects.projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            </label>
            {selectedProject && (selectedProject.scenarios.length === 0
              ? <p>{COSTS_NO_SCENARIOS}</p>
              : <label className="costs-screen__selector">
                {COSTS_SCENARIO_LABEL}
                <select className="input" value={scenarioId} onChange={(event) => setScenarioId(event.target.value)}>
                  <option value="">{COSTS_SELECT_SCENARIO}</option>
                  {selectedProject.scenarios.map((scenario) => <option key={scenario.id} value={scenario.id}>{scenario.name}</option>)}
                </select>
              </label>)}
          </div>
          {!selectedScenario && selectedProject?.scenarios.length !== 0 && <p>{COSTS_NO_SELECTION}</p>}
          {selectedProject && selectedScenario && <ScenarioAdditionalCostsSection
            key={`${selectedProject.id}:${selectedScenario.id}`}
            projectId={selectedProject.id}
            scenarioId={selectedScenario.id}
            scenarioName={selectedScenario.name}
            scenarioStatus={selectedScenario.status}
            reportingCurrency={selectedProject.reporting_currency}
          />}
        </>)}
    </div>
  );
}
