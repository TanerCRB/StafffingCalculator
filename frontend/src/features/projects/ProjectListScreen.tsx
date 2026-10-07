import { useEffect, useRef, useState } from "react";

import { ApiError, RequestTimeoutError, archiveProject, copyProject, getProjects } from "../../api/client";
import type { ProjectDetail, ProjectListItem, ScenarioListItem } from "../../api/contracts/projects";
import type { ScenarioApproval } from "../../api/contracts/scenarioApproval";
import { formatDeliveryPeriod } from "../../lib/dates";
import { formatPercentString } from "../../lib/money";
import { handleNotYetImplemented, notImplementedHint } from "../../lib/notImplemented";
import { DuplicateScenarioControl } from "./DuplicateScenarioControl";
import { ScenarioCommercialTermsSection } from "./ScenarioCommercialTermsSection";
import { ScenarioAdditionalCostsSection } from "./ScenarioAdditionalCostsSection";
import { ScenarioResultsSection } from "./ScenarioResultsSection";
import { ScenarioHistorySection } from "./ScenarioHistorySection";
import { ScenarioApprovalSection } from "./ScenarioApprovalSection";
import { ScenarioAssumptionsSection } from "./ScenarioAssumptionsSection";
import { StaffingPlanSection } from "./StaffingPlanSection";
import { ProjectEditForm } from "./ProjectEditForm";
import { ScenarioCreateForm } from "./ScenarioCreateForm";
import { missingInputLabel } from "./scenarioInputLabels";
import { PROJECT_ARCHIVE_MESSAGES, PROJECT_COPY_MESSAGES, PROJECT_LIST_MESSAGES } from "./projectListMessages";
import { ProjectCreateForm } from "./ProjectCreateForm";
import { clearPendingProjectCreateKey, readPendingProjectCreateKey } from "./projectCreateOperation";
import "./ProjectListScreen.css";

/**
 * SC-1-06 — the project list with the scenarios of the selected project.
 *
 * Read only, with one exception added by SC-4-06 (gate 1, D-2 = option A): every scenario card
 * carries a `ScenarioCommercialTermsSection`, which reads that scenario's commercial rule and revenue
 * itself and can set a Time & Material rule. SC-1-16 adds project metadata editing from each row.
 *
 * The screen takes no access or visibility decision of its own (NF-04, ADR-0005): every row it
 * shows came from the API in that shape, including an archived project. There is no client-side
 * filter, no client-side sort and no locally invented empty list — an empty list is something
 * only the server can say.
 *
 * Edit (SC-1-16), copy (SC-1-19), search, filters and pagination (SC-1-17) are wired.
 * Add project opens the F-01 creation form (SC-1-18), and Add scenario creates a draft under its
 * selected project (SC-1-26); remaining unimplemented row actions are placeholders.
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
type ScreenState =
  | { kind: "loading" }
  | { kind: "ready"; projects: ProjectListItem[]; total: number }
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

export function ProjectListScreen({ recoveryStorage = window.sessionStorage, onViewProject }: { readonly recoveryStorage?: Storage; readonly onViewProject?: (projectId: string) => void } = {}) {
  const [state, setState] = useState<ScreenState>({ kind: "loading" });
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [scenarioCreateProjectId, setScenarioCreateProjectId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"" | ProjectListItem["status"]>("");
  const [page, setPage] = useState(0);
  const [accessibleTotal, setAccessibleTotal] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const [copyingProjectId, setCopyingProjectId] = useState<string | null>(null);
  const [archivingProjectId, setArchivingProjectId] = useState<string | null>(null);
  const archivingProjectIdRef = useRef<string | null>(null);
  const [archiveMessage, setArchiveMessage] = useState<string | null>(null);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const [copiedProjectSnapshot, setCopiedProjectSnapshot] = useState<ProjectListItem | null>(null);
  const copiedProjectSnapshotRef = useRef<ProjectListItem | null>(null);
  const [createdProjectSnapshot, setCreatedProjectSnapshot] = useState<ProjectListItem | null>(null);
  const createdProjectSnapshotRef = useRef<ProjectListItem | null>(null);
  const [pendingCreateKey, setPendingCreateKey] = useState<string | null>(() => readPendingProjectCreateKey(recoveryStorage));
  const [showCreateForm, setShowCreateForm] = useState(() => readPendingProjectCreateKey(recoveryStorage) !== null);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createOutcomeUnknown, setCreateOutcomeUnknown] = useState(() => readPendingProjectCreateKey(recoveryStorage) !== null);

  function clearCopiedProjectSnapshot() {
    copiedProjectSnapshotRef.current = null;
    setCopiedProjectSnapshot(null);
    setCopyMessage(null);
  }

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
    setRefreshing(true);
    getProjects(controller.signal, {
      search,
      status: status || undefined,
      limit: 20,
      offset: page * 20,
      })
      .then((response) => {
        if (!left) {
          const copied = copiedProjectSnapshotRef.current;
          const copyIsOnPage = copied !== null &&
            response.projects.some((project) => project.id === copied.id);
          const created = createdProjectSnapshotRef.current;
          const createdIsOnPage = created !== null &&
            response.projects.some((project) => project.id === created.id);
          const total = response.total;
          setState({ kind: "ready", projects: response.projects, total });
          setArchiveMessage(null);
          if (copyIsOnPage) {
            copiedProjectSnapshotRef.current = null;
            setCopiedProjectSnapshot(null);
            setCopyMessage(null);
          } else if (copied !== null && search === copied.name && status === "") {
            setCopyMessage(PROJECT_COPY_MESSAGES.copiedNotOnPage);
          }
          if (createdIsOnPage) {
            createdProjectSnapshotRef.current = null;
            setCreatedProjectSnapshot(null);
          }
          if (search.trim() === "" && status === "") setAccessibleTotal(total);
        }
      })
      .catch((error: unknown) => {
        if (!left) {
          setState(toFailureState(error));
        }
      })
      .finally(() => {
        if (!left) setRefreshing(false);
      });
    return () => {
      left = true;
      controller.abort();
    };
  }, [page, retryCount, search, status]);

  const projects = state.kind === "ready" ? state.projects : [];
  const selectedProject = projects.find((project) => project.id === selectedProjectId) ??
    (copiedProjectSnapshot?.id === selectedProjectId ? copiedProjectSnapshot : undefined) ??
    (createdProjectSnapshot?.id === selectedProjectId ? createdProjectSnapshot : undefined);

  function addCreatedProject(detail: ProjectDetail) {
    // The detail response has owner; the list contract deliberately omits it (B-02).
    const { id, name, client, delivery_period, reporting_currency, description, status: projectStatus, scenarios } = detail;
    const project: ProjectListItem = { id, name, client, delivery_period, reporting_currency, description, status: projectStatus, scenarios };
    clearCopiedProjectSnapshot();
    setSearch("");
    setStatus("");
    setPage(0);
    setEditingProjectId(null);
    setSelectedProjectId(project.id);
    const alreadyVisible = state.kind === "ready" && state.projects.some((item) => item.id === project.id);
    const remainsOnCurrentPage = search === "" && status === "" && page === 0;
    if (alreadyVisible && remainsOnCurrentPage) {
      createdProjectSnapshotRef.current = null;
      setCreatedProjectSnapshot(null);
    } else {
      createdProjectSnapshotRef.current = project;
      setCreatedProjectSnapshot(project);
    }
    setShowCreateForm(false);
    // Refresh the server-owned page and count. The created response stays pinned in the details
    // panel until pagination returns it in a real page; it never occupies a synthetic list row.
    setRetryCount((current) => current + 1);
  }

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
        total: previous.total,
      };
    });
  }

  function addCreatedScenario(projectId: string, scenario: ScenarioListItem) {
    setState((previous) => previous.kind !== "ready" ? previous : {
      ...previous,
      projects: previous.projects.map((project) => project.id === projectId
        ? { ...project, scenarios: [...project.scenarios, scenario] }
        : project),
    });
  }

  function markScenarioApproved(scenarioId: string, result: ScenarioApproval) {
    setState((previous) => previous.kind !== "ready" ? previous : {
      ...previous,
      projects: previous.projects.map((project) => ({
        ...project,
        scenarios: project.scenarios.map((scenario) => scenario.id === scenarioId
          ? { ...scenario, status: result.status, ready_for_approval: false }
          : scenario),
      })),
    });
  }

  function updateProject(updated: ProjectDetail) {
    setState((previous) => {
      if (previous.kind !== "ready") {
        return previous;
      }
      return {
        kind: "ready",
        projects: previous.projects.map((project) =>
          project.id === updated.id
            ? {
                ...project,
                name: updated.name,
                client: updated.client,
                delivery_period: updated.delivery_period,
                reporting_currency: updated.reporting_currency,
                description: updated.description,
              }
            : project,
        ),
        total: previous.total,
      };
    });
    // Editing a searchable field can move this row outside the active server query or onto a
    // different page. Apply the PATCH response immediately, then reconcile the current page.
    setRetryCount((current) => current + 1);
  }

  function onRowAction(actionKey: string, projectId: string) {
    if (actionKey === "view") {
      onViewProject?.(projectId);
    } else if (actionKey === "edit") {
      setScenarioCreateProjectId(null);
      setSelectedProjectId(projectId);
      setEditingProjectId(projectId);
    } else if (actionKey === "add-scenario") {
      setSelectedProjectId(projectId);
      setEditingProjectId(null);
      setScenarioCreateProjectId(projectId);
    } else if (actionKey === "copy") {
      void copyProjectRow(projectId);
    } else if (actionKey === "archive") {
      const project = projects.find((candidate) => candidate.id === projectId);
      if (project?.status === "Active") void archiveProjectRow(project);
    } else {
      handleNotYetImplemented();
    }
  }

  async function archiveProjectRow(project: ProjectListItem) {
    if (archivingProjectIdRef.current !== null || project.status !== "Active") return;
    if (!window.confirm(PROJECT_ARCHIVE_MESSAGES.confirmation(project.name))) return;
    archivingProjectIdRef.current = project.id;
    setArchivingProjectId(project.id);
    setArchiveMessage(PROJECT_ARCHIVE_MESSAGES.archiving);
    try {
      const archived = await archiveProject(project.id);
      if (archived.status !== "Archived") throw new Error("Archive response did not report Archived status");
      setArchiveMessage(PROJECT_ARCHIVE_MESSAGES.reconciling);
      setRetryCount((count) => count + 1);
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) {
        setArchiveMessage(PROJECT_ARCHIVE_MESSAGES.refused);
      } else {
        setArchiveMessage(PROJECT_ARCHIVE_MESSAGES.unresolved);
        setRetryCount((count) => count + 1);
      }
    } finally {
      archivingProjectIdRef.current = null;
      setArchivingProjectId(null);
    }
  }

  async function copyProjectRow(projectId: string) {
    if (copyingProjectId !== null) return;
    setCopyingProjectId(projectId);
    setCopyMessage(PROJECT_COPY_MESSAGES.copying);
    try {
      const detail = await copyProject(projectId);
      const listItem: ProjectListItem = {
        id: detail.id,
        name: detail.name,
        client: detail.client,
        delivery_period: detail.delivery_period,
        reporting_currency: detail.reporting_currency,
        description: detail.description,
        status: detail.status,
        scenarios: detail.scenarios,
      };
      copiedProjectSnapshotRef.current = listItem;
      setCopiedProjectSnapshot(listItem);
      setSelectedProjectId(listItem.id);
      setEditingProjectId(null);
      setCopyMessage(PROJECT_COPY_MESSAGES.reconciling);
      setPage(0);
      setStatus("");
      setSearch(detail.name);
      // Reconcile ordering, filters and the total from the server's current page. Keep the
      // validated response selected until its server-paginated row is opened (ADR-0009).
      setRetryCount((count) => count + 1);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setCopyMessage(PROJECT_COPY_MESSAGES.denied);
      } else if (error instanceof RequestTimeoutError) {
        setCopyMessage(PROJECT_COPY_MESSAGES.timedOut);
      } else if (
        error instanceof ApiError && error.status === 409 && error.detail !== undefined &&
        /^The scenario's commercial terms use the model .+, which this version of the application cannot copy\. Nothing was copied; retry once every instance runs a version that supports it\.$/.test(error.detail)
      ) {
        setCopyMessage(PROJECT_COPY_MESSAGES.unsupportedModel(error.detail));
      } else {
        setCopyMessage(error instanceof ApiError ? error.detail ?? PROJECT_COPY_MESSAGES.refused : PROJECT_COPY_MESSAGES.refused);
      }
    } finally {
      setCopyingProjectId(null);
    }
  }

  return (
    <section className="project-list" aria-labelledby="project-list-heading">
      {copyMessage !== null && <p role="status" className="project-list__message">{copyMessage}</p>}
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
            {(state.kind === "ready" ||
              (accessibleTotal !== null && (state.kind === "failed" || state.kind === "timed-out"))) && (
              <ListToolbar
                search={search}
                status={status}
                onSearchChange={(value) => {
                  clearCopiedProjectSnapshot();
                  setPage(0);
                  setSearch(value);
                }}
                onStatusChange={(value) => {
                  clearCopiedProjectSnapshot();
                  setPage(0);
                  setStatus(value);
                }}
                onReset={() => {
                  clearCopiedProjectSnapshot();
                  setSearch("");
                  setStatus("");
                  setPage(0);
                }}
                createOpen={showCreateForm}
                createPending={createSubmitting || state.kind !== "ready"}
                onAddProject={() => setShowCreateForm((open) => !open)}
              />
            )}
          </div>

          {state.kind === "ready" && showCreateForm && (
            <ProjectCreateForm onCreated={addCreatedProject}
              onCancel={() => setShowCreateForm(false)}
              onSubmittingChange={setCreateSubmitting}
              pendingKey={pendingCreateKey}
              onPendingKeyChange={(key) => setPendingCreateKey(key)}
              outcomeUnknown={createOutcomeUnknown}
              onOutcomeUnknown={setCreateOutcomeUnknown}
              onStartSeparateProject={() => {
                clearPendingProjectCreateKey(recoveryStorage);
                setPendingCreateKey(null);
                setCreateOutcomeUnknown(false);
              }}
              recoveryStorage={recoveryStorage} />
          )}

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
              <button type="button" className="button button--secondary" disabled={refreshing} onClick={() => setRetryCount((count) => count + 1)}>
                {PROJECT_LIST_MESSAGES.retry}
              </button>
            </p>
          )}
          {state.kind === "failed" && (
            <p role="status" className="project-list__message project-list__message--attention">
              Projects could not be loaded.
              <button type="button" className="button button--secondary" disabled={refreshing} onClick={() => setRetryCount((count) => count + 1)}>
                {PROJECT_LIST_MESSAGES.retry}
              </button>
            </p>
          )}

          {state.kind === "ready" && refreshing && (
            <p role="status" className="project-list__message">{PROJECT_LIST_MESSAGES.updating}</p>
          )}
          {archiveMessage !== null && <p role="status">{archiveMessage}</p>}

          {state.kind === "ready" && !refreshing && projects.length === 0 && (
            <p role="status" className="project-list__message">
              {accessibleTotal === 0
                ? PROJECT_LIST_MESSAGES.noProjects
                : PROJECT_LIST_MESSAGES.noMatches}
            </p>
          )}

          {state.kind === "ready" && !refreshing && projects.length > 0 && (
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
                        onClick={() => {
                          clearCopiedProjectSnapshot();
                          setSelectedProjectId(project.id);
                          setEditingProjectId(null);
                          setScenarioCreateProjectId(null);
                        }}
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
                            aria-disabled={action.key === "view" || action.key === "edit" || action.key === "copy" || action.key === "add-scenario" || (action.key === "archive" && project.status === "Active") ? undefined : "true"}
                            disabled={(action.key === "copy" && copyingProjectId !== null) || (action.key === "archive" && (project.status !== "Active" || archivingProjectId !== null))}
                            title={action.key === "view" || action.key === "edit" || action.key === "copy" || action.key === "archive" || action.key === "add-scenario" ? undefined : NOT_IMPLEMENTED_HINT}
                            onClick={() => onRowAction(action.key, project.id)}
                          >
                            {action.key === "copy" && copyingProjectId === project.id ? PROJECT_COPY_MESSAGES.copying : action.key === "archive" && archivingProjectId === project.id ? PROJECT_ARCHIVE_MESSAGES.archiving : action.label}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {state.kind === "ready" && !refreshing && (
            <nav className="project-list__pagination" aria-label={PROJECT_LIST_MESSAGES.paginationLabel}>
              <span role="status">
                {state.total === 0
                  ? PROJECT_LIST_MESSAGES.noPages
                  : PROJECT_LIST_MESSAGES.page(page + 1, Math.ceil(state.total / 20))}
              </span>
              <button
                type="button"
                className="button button--secondary"
                aria-label={PROJECT_LIST_MESSAGES.previousPage}
                disabled={page === 0 || refreshing}
                onClick={() => {
                  setPage((current) => Math.max(0, current - 1));
                }}
              >{PROJECT_LIST_MESSAGES.previous}</button>
              <button
                type="button"
                className="button button--secondary"
                aria-label={PROJECT_LIST_MESSAGES.nextPage}
                disabled={(page + 1) * 20 >= state.total || refreshing}
                onClick={() => {
                  setPage((current) => current + 1);
                }}
              >{PROJECT_LIST_MESSAGES.next}</button>
            </nav>
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
            {selectedProject === undefined ? (
              <>
                <h2 id="scenario-details-heading" className="project-list__details-title">
                  Scenario details
                </h2>
              <p className="project-list__details-empty">Select a project to see its scenarios.</p>
              </>
            ) : editingProjectId === selectedProject.id ? (
              <>
                <h2 id="scenario-details-heading" className="project-list__details-title">
                  Project details
                </h2>
                <ProjectEditForm
                  projectId={selectedProject.id}
                  projectName={selectedProject.name}
                  onSaved={updateProject}
                  onCancel={() => setEditingProjectId(null)}
                />
              </>
            ) : (
              <>
                <h2 id="scenario-details-heading" className="project-list__details-title">
                  Scenario details
                </h2>
              {scenarioCreateProjectId === selectedProject.id && (
                <ScenarioCreateForm
                  projectId={selectedProject.id}
                  projectName={selectedProject.name}
                  onCreated={addCreatedScenario}
                  onRefresh={() => setRetryCount((count) => count + 1)}
                />
              )}
              <ScenarioDetails
                project={selectedProject}
                onScenarioDuplicated={addDuplicatedScenario}
                onScenarioApproved={markScenarioApproved}
              />
              </>
            )}
          </section>
        )}
      </div>
    </section>
  );
}

/**
 * Search, status and pagination controls address the server list; Add project opens the form.
 */
interface ListToolbarProps {
  readonly search: string;
  readonly status: "" | ProjectListItem["status"];
  readonly onSearchChange: (value: string) => void;
  readonly onStatusChange: (value: "" | ProjectListItem["status"]) => void;
  readonly onReset: () => void;
  readonly createOpen: boolean;
  readonly createPending: boolean;
  readonly onAddProject: () => void;
}

function ListToolbar({ search, status, onSearchChange, onStatusChange, onReset, createOpen, createPending, onAddProject }: ListToolbarProps) {
  return (
    <div className="project-list__toolbar">
      <input
        type="search"
        className="input project-list__search"
        aria-label="Search projects"
        placeholder={PROJECT_LIST_MESSAGES.searchPlaceholder}
        value={search}
        onChange={(event) => onSearchChange(event.currentTarget.value)}
      />
      <label className="project-list__status-filter">
        {PROJECT_LIST_MESSAGES.statusLabel}
        <select
          aria-label="Filter projects by status"
          className="input"
          value={status}
          onChange={(event) =>
            onStatusChange(event.currentTarget.value as "" | ProjectListItem["status"])
          }
        >
          <option value="">{PROJECT_LIST_MESSAGES.statusAll}</option>
          <option value="Active">Active</option>
          <option value="Archived">Archived</option>
        </select>
      </label>
      <button type="button" className="button button--secondary" onClick={onReset}>
        {PROJECT_LIST_MESSAGES.resetFilters}
      </button>
      <button
        type="button"
        className="button button--primary"
        aria-expanded={createOpen}
        aria-controls="project-create-form"
        disabled={createPending}
        onClick={onAddProject}
      >
        {createOpen ? "Close create form" : "Add project"}
      </button>
    </div>
  );
}

interface ScenarioDetailsProps {
  readonly project: ProjectListItem;
  readonly onScenarioDuplicated: (projectId: string, scenario: ScenarioListItem) => void;
  readonly onScenarioApproved: (scenarioId: string, result: ScenarioApproval) => void;
}

function ScenarioDetails({ project, onScenarioDuplicated, onScenarioApproved }: ScenarioDetailsProps) {
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
            <ScenarioAssumptionsSection
              projectId={project.id}
              scenarioId={scenario.id}
              scenarioStatus={scenario.status}
            />
            {/* SC-4-06: its own read, its own failure states, its own write. Keyed by the card, so
                switching projects unmounts it and aborts its read (ADR-0010, point 7). */}
            <ScenarioCommercialTermsSection
              projectId={project.id}
              scenarioId={scenario.id}
              scenarioName={scenario.name}
            />
            <ScenarioAdditionalCostsSection
              projectId={project.id}
              scenarioId={scenario.id}
              scenarioName={scenario.name}
              reportingCurrency={project.reporting_currency}
              scenarioStatus={scenario.status}
            />
            {/* SC-7-02: a second, independent read on the same card — its own state machine, its
                own abort on unmount/re-select (ADR-0010, point 7). Q1 = option A: no new router, no
                new screen. */}
            <ScenarioResultsSection
              projectId={project.id}
              scenarioId={scenario.id}
              scenarioName={scenario.name}
            />
            <ScenarioHistorySection
              projectId={project.id}
              scenarioId={scenario.id}
              scenarioName={scenario.name}
            />
            <ScenarioApprovalSection
              projectId={project.id}
              scenarioId={scenario.id}
              scenarioName={scenario.name}
              status={scenario.status}
              ready={scenario.ready_for_approval}
              onApproved={onScenarioApproved}
            />
            {/* SC-3-09 adds writes to the existing staffing read. Backend permission checks remain
                authoritative; this client has no permission preflight endpoint. */}
            <StaffingPlanSection projectId={project.id} scenarioId={scenario.id} scenarioStatus={scenario.status} />
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
