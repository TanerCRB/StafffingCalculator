import { useEffect, useId, useState, type ReactNode } from "react";

import { ApiError, RequestTimeoutError, getProjects, getScenarioPeriodResults, getScenarioResults } from "../../api/client";
import type { ProjectListItem, ScenarioListItem } from "../../api/contracts/projects";
import type { ScenarioPeriodResult, ScenarioPeriodResults, ScenarioResults } from "../../api/contracts/scenarioResults";
import { formatCalendarMonth } from "../../lib/dates";
import { formatFteString } from "../../lib/fte";
import { NOT_APPLICABLE, formatMoneyString, formatPercentString } from "../../lib/money";
import {
  OVERVIEW_ADDITIONAL_COST,
  OVERVIEW_APPROVED_SNAPSHOT,
  OVERVIEW_BELOW_TARGET,
  OVERVIEW_DESCRIPTION,
  OVERVIEW_EMPTY_PERIODS,
  OVERVIEW_FIELD_UNAVAILABLE,
  OVERVIEW_LOADING_PROJECTS,
  OVERVIEW_LOADING_RESULTS,
  OVERVIEW_MARGIN,
  OVERVIEW_NEGATIVE_PROFIT,
  OVERVIEW_NO_PROJECTS,
  OVERVIEW_NO_SCENARIOS,
  OVERVIEW_PERSONNEL_COST,
  OVERVIEW_PERIOD_COST,
  OVERVIEW_PLANNED_FTE,
  OVERVIEW_PROJECTS_DENIED,
  OVERVIEW_PROJECTS_FAILED,
  OVERVIEW_PROJECTS_TIMEOUT,
  OVERVIEW_PROJECT_LABEL,
  OVERVIEW_PROFIT,
  OVERVIEW_READ_AGAIN,
  OVERVIEW_REPORTING_PERIOD,
  OVERVIEW_REVENUE,
  OVERVIEW_RESULTS_CONFLICT,
  OVERVIEW_RESULTS_FAILED,
  OVERVIEW_RESULTS_REFUSED,
  OVERVIEW_RESULTS_TIMEOUT,
  OVERVIEW_RESULTS_UNREADABLE,
  OVERVIEW_SCENARIO_LABEL,
  OVERVIEW_SELECT_PROJECT,
  OVERVIEW_SELECT_SCENARIO,
  OVERVIEW_TARGET_MARGIN,
  OVERVIEW_TITLE,
  OVERVIEW_TOTAL_COST,
  OVERVIEW_MONTHLY_TREND,
  OVERVIEW_COST_COMPOSITION,
  OVERVIEW_APPROVAL_READINESS,
  OVERVIEW_READY_FOR_APPROVAL,
  OVERVIEW_INPUTS_REQUIRED,
  OVERVIEW_PERIODLESS_NOTE,
  OVERVIEW_LOADING_SUMMARY,
  OVERVIEW_SUMMARY_READ_AGAIN,
  OVERVIEW_SUMMARY_UNAVAILABLE,
  OVERVIEW_PERIOD_SECTION_LABEL,
  OVERVIEW_TABLE_CAPTION,
  OVERVIEW_UNALLOCATED_EXPLANATION,
  OVERVIEW_UNALLOCATED_FIXED_COST,
  OVERVIEW_UNALLOCATED_HEADING,
  OVERVIEW_UNALLOCATED_REVENUE,
} from "./projectOverviewText";
import "./ProjectOverviewScreen.css";

type ProjectsState =
  | { kind: "loading" }
  | { kind: "ready"; projects: ProjectListItem[] }
  | { kind: "denied" }
  | { kind: "timed-out" }
  | { kind: "failed" };

type ResultsState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; projectId: string; scenarioId: string; results: ScenarioPeriodResults }
  | { kind: "refused" }
  | { kind: "conflict" }
  | { kind: "timed-out" }
  | { kind: "unreadable" }
  | { kind: "failed" };

type SummaryState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; projectId: string; scenarioId: string; results: ScenarioResults }
  | { kind: "unavailable" };

function projectsFailure(error: unknown): ProjectsState {
  if (error instanceof RequestTimeoutError) return { kind: "timed-out" };
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return { kind: "denied" };
  return { kind: "failed" };
}

function resultsFailure(error: unknown): ResultsState {
  if (error instanceof RequestTimeoutError) return { kind: "timed-out" };
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403 || error.status === 404) return { kind: "refused" };
    if (error.status === 409) return { kind: "conflict" };
    if (error.status >= 200 && error.status < 300) return { kind: "unreadable" };
  }
  return { kind: "failed" };
}

const RESULTS_FAILURE_TEXT: Readonly<Record<Exclude<ResultsState["kind"], "idle" | "loading" | "ready">, string>> = {
  refused: OVERVIEW_RESULTS_REFUSED,
  conflict: OVERVIEW_RESULTS_CONFLICT,
  "timed-out": OVERVIEW_RESULTS_TIMEOUT,
  unreadable: OVERVIEW_RESULTS_UNREADABLE,
  failed: OVERVIEW_RESULTS_FAILED,
};

const RETRYABLE = new Set<ResultsState["kind"]>(["conflict", "timed-out", "unreadable", "failed"]);

export function ProjectOverviewScreen({ initialProjectId = null }: { readonly initialProjectId?: string | null } = {}) {
  const headingId = useId();
  const [projects, setProjects] = useState<ProjectsState>({ kind: "loading" });
  const [projectId, setProjectId] = useState(initialProjectId ?? "");
  const [scenarioId, setScenarioId] = useState("");
  const [read, setRead] = useState<ResultsState>({ kind: "idle" });
  const [summary, setSummary] = useState<SummaryState>({ kind: "idle" });
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let left = false;
    getProjects(controller.signal)
      .then((response) => { if (!left) setProjects({ kind: "ready", projects: response.projects }); })
      .catch((error: unknown) => { if (!left) setProjects(projectsFailure(error)); });
    return () => { left = true; controller.abort(); };
  }, []);

  useEffect(() => {
    if (initialProjectId === null || initialProjectId === undefined || projects.kind !== "ready") return;
    if (projects.projects.some((project) => project.id === initialProjectId)) {
      setProjectId(initialProjectId);
      setScenarioId("");
    }
  }, [initialProjectId, projects]);

  const projectList = projects.kind === "ready" ? projects.projects : [];
  const selectedProject = projectList.find((project) => project.id === projectId);
  const selectedScenario = selectedProject?.scenarios.find((scenario) => scenario.id === scenarioId);

  useEffect(() => {
    if (!projectId || !scenarioId) return;
    const controller = new AbortController();
    let left = false;
    setRead({ kind: "loading" });
    setSummary({ kind: "loading" });
    getScenarioPeriodResults(projectId, scenarioId, controller.signal)
      .then((results) => {
        if (!left) setRead({ kind: "ready", projectId, scenarioId, results });
      })
      .catch((error: unknown) => { if (!left) setRead(resultsFailure(error)); });
    getScenarioResults(projectId, scenarioId, controller.signal)
      .then((results) => {
        if (!left) setSummary({ kind: "ready", projectId, scenarioId, results });
      })
      .catch(() => { if (!left) setSummary({ kind: "unavailable" }); });
    return () => { left = true; controller.abort(); };
  }, [projectId, scenarioId, retry]);

  function chooseProject(nextProjectId: string) {
    setProjectId(nextProjectId);
    setScenarioId("");
    setRead({ kind: "idle" });
    setSummary({ kind: "idle" });
  }

  function chooseScenario(nextScenarioId: string) {
    setScenarioId(nextScenarioId);
    setRead({ kind: "loading" });
    setSummary({ kind: "loading" });
  }

  function readAgain() {
    setRead({ kind: "loading" });
    setSummary({ kind: "loading" });
    setRetry((count) => count + 1);
  }

  const visibleRead = read.kind === "ready" &&
    (read.projectId !== projectId || read.scenarioId !== scenarioId)
    ? { kind: "loading" as const }
    : read;
  const visibleSummary = summary.kind === "ready" &&
    (summary.projectId !== projectId || summary.scenarioId !== scenarioId)
    ? { kind: "loading" as const }
    : summary;

  return (
    <section className="overview" aria-labelledby={headingId}>
      <div className="overview__page-heading">
        <div>
          <h2 id={headingId} className="overview__title" tabIndex={-1}>
            {selectedProject?.name ?? OVERVIEW_TITLE}
          </h2>
          <p className="overview__description">
            {selectedProject ? `${selectedProject.client} / Project economics` : OVERVIEW_DESCRIPTION}
          </p>
        </div>
      </div>

      {projects.kind === "loading" && <p role="status">{OVERVIEW_LOADING_PROJECTS}</p>}
      {projects.kind === "denied" && <p role="status" className="overview__attention">{OVERVIEW_PROJECTS_DENIED}</p>}
      {projects.kind === "timed-out" && <p role="status" className="overview__attention">{OVERVIEW_PROJECTS_TIMEOUT}</p>}
      {projects.kind === "failed" && <p role="status" className="overview__attention">{OVERVIEW_PROJECTS_FAILED}</p>}
      {projects.kind === "ready" && (
        <div className="overview__scenario-bar">
          <span className="overview__scenario-label">Scenario</span>
          <div className="overview__selectors">
          <label>
            {OVERVIEW_PROJECT_LABEL}
            <select value={projectId} onChange={(event) => chooseProject(event.currentTarget.value)}>
              <option value="">{OVERVIEW_SELECT_PROJECT}</option>
              {projects.projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </label>
          {selectedProject && (
            <label>
              {OVERVIEW_SCENARIO_LABEL}
              <select value={scenarioId} onChange={(event) => chooseScenario(event.currentTarget.value)}>
                <option value="">{OVERVIEW_SELECT_SCENARIO}</option>
                {selectedProject.scenarios.map((scenario) => <option key={scenario.id} value={scenario.id}>{scenario.name}</option>)}
              </select>
            </label>
          )}
          {selectedProject && selectedProject.scenarios.length === 0 && <p role="status">{OVERVIEW_NO_SCENARIOS}</p>}
          {projects.projects.length === 0 && <p role="status">{OVERVIEW_NO_PROJECTS}</p>}
          </div>
          {selectedProject && (
            <div className="overview__project-context">
              <span>{formatCalendarMonth(selectedProject.delivery_period.start)} – {formatCalendarMonth(selectedProject.delivery_period.end)}</span>
              <span>{selectedProject.reporting_currency}</span>
              {selectedScenario && <span className="overview__status" data-scenario-status={selectedScenario.status}>{selectedScenario.status}</span>}
            </div>
          )}
        </div>
      )}

      {selectedScenario && visibleRead.kind === "loading" && <p role="status">{OVERVIEW_LOADING_RESULTS}</p>}
      {selectedScenario && visibleSummary.kind === "loading" && visibleRead.kind !== "loading" && <p role="status">{OVERVIEW_LOADING_SUMMARY}</p>}
      {selectedScenario && visibleSummary.kind === "unavailable" && (
        <div>
          <p role="status" className="overview__summary-note">{OVERVIEW_SUMMARY_UNAVAILABLE}</p>
          <button type="button" className="button button--secondary" onClick={readAgain}>{OVERVIEW_SUMMARY_READ_AGAIN}</button>
        </div>
      )}
      {visibleRead.kind !== "idle" && visibleRead.kind !== "loading" && visibleRead.kind !== "ready" && (
        <div>
          <p role="status" className="overview__attention" data-results-failure={visibleRead.kind}>
            {RESULTS_FAILURE_TEXT[visibleRead.kind]}
          </p>
          {RETRYABLE.has(visibleRead.kind) && <button type="button" className="button button--secondary" onClick={readAgain}>{OVERVIEW_READ_AGAIN}</button>}
        </div>
      )}
      {visibleRead.kind === "ready" && selectedScenario && (
        <PeriodResults
          results={visibleRead.results}
          summary={visibleSummary.kind === "ready" ? visibleSummary.results : null}
          scenario={selectedScenario}
        />
      )}
    </section>
  );
}

function PeriodResults({ results, summary, scenario }: { results: ScenarioPeriodResults; summary: ScenarioResults | null; scenario: ScenarioListItem }) {
  const reportingCurrency = results.reporting_currency;
  return (
    <div className="overview__results" data-scenario-status={results.scenario_status}>
      {results.scenario_status === "Approved" && <p className="overview__snapshot">{OVERVIEW_APPROVED_SNAPSHOT}</p>}
      {results.target_margin_percent !== null && summary === null && (
        <p className="overview__target">{OVERVIEW_TARGET_MARGIN} {formatPercentString(results.target_margin_percent)}</p>
      )}
      {summary && <SummaryCards summary={summary} target={results.target_margin_percent} />}
      <div className="overview__dashboard-grid">
        <section className="overview__panel overview__trend" aria-labelledby="overview-trend-heading">
          <div className="overview__panel-heading">
            <div>
              <h3 id="overview-trend-heading">{OVERVIEW_MONTHLY_TREND}</h3>
              <p>{OVERVIEW_PERIODLESS_NOTE}</p>
            </div>
            <div className="overview__legend" aria-hidden="true">
              <span><i className="overview__legend-revenue" />{OVERVIEW_REVENUE}</span>
              <span><i className="overview__legend-cost" />{OVERVIEW_PERIOD_COST}</span>
            </div>
          </div>
          {results.periods.length === 0 ? <p role="status">{OVERVIEW_EMPTY_PERIODS}</p> : (
            <MonthlyTrend periods={results.periods} reportingCurrency={reportingCurrency} />
          )}
        </section>
        <div className="overview__side-panels">
          {summary && <CostComposition summary={summary} />}
          <section className="overview__panel overview__readiness" aria-labelledby="overview-readiness-heading">
            <h3 id="overview-readiness-heading">{OVERVIEW_APPROVAL_READINESS}</h3>
            <p className={scenario.ready_for_approval ? "overview__readiness-state" : "overview__readiness-state overview__readiness-state--pending"}>
              {scenario.ready_for_approval ? OVERVIEW_READY_FOR_APPROVAL : `${OVERVIEW_INPUTS_REQUIRED}: ${scenario.missing_inputs.length}`}
            </p>
          </section>
        </div>
      </div>
      <section aria-label={OVERVIEW_PERIOD_SECTION_LABEL}>
        {results.periods.length === 0 ? <p role="status">{OVERVIEW_EMPTY_PERIODS}</p> : (
          <div className="overview__table-wrap">
            <table className="overview__table">
              <caption className="visually-hidden">{OVERVIEW_TABLE_CAPTION} {scenario.name}</caption>
              <thead><tr>
                <th scope="col">{OVERVIEW_REPORTING_PERIOD}</th>
                <th scope="col">{OVERVIEW_REVENUE}</th>
                <th scope="col">{OVERVIEW_PERSONNEL_COST}</th>
                <th scope="col">{OVERVIEW_ADDITIONAL_COST}</th>
                <th scope="col">{OVERVIEW_PERIOD_COST}</th>
                <th scope="col">{OVERVIEW_PROFIT}</th>
                <th scope="col">{OVERVIEW_MARGIN}</th>
                <th scope="col">{OVERVIEW_PLANNED_FTE}</th>
              </tr></thead>
              <tbody>{results.periods.map((period) => (
                <PeriodRow key={period.period_month} period={period} reportingCurrency={reportingCurrency} />
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
      <section className="overview__unallocated" aria-labelledby="overview-unallocated-heading">
        <h4 id="overview-unallocated-heading">{OVERVIEW_UNALLOCATED_HEADING}</h4>
        <p>{OVERVIEW_UNALLOCATED_EXPLANATION}</p>
        <dl>
          <dt>{OVERVIEW_UNALLOCATED_REVENUE}</dt>
          <dd>{periodMoney(results.unallocated.revenue, results.unallocated.revenue_currency)}</dd>
          <dt>{OVERVIEW_UNALLOCATED_FIXED_COST}</dt>
          <dd>{periodMoney(results.unallocated.fixed_amount_cost, results.unallocated.fixed_amount_cost_currency)}</dd>
        </dl>
      </section>
    </div>
  );
}

function SummaryCards({ summary, target }: { summary: ScenarioResults; target: string | null }) {
  const currency = summary.revenue.state === "calculated" ? summary.revenue.currency : null;
  return (
    <section className="overview__metrics" aria-label="Scenario summary">
      <Metric label={OVERVIEW_REVENUE} value={periodMoney(summary.revenue.amount, currency)} />
      <Metric label={OVERVIEW_TOTAL_COST} value={periodMoney(summary.included_cost, currency)} />
      <Metric label={OVERVIEW_PROFIT} value={periodMoney(summary.profit, currency)} />
      <Metric label={OVERVIEW_MARGIN} value={periodPercent(summary.margin)} accent>
        {target !== null && <span>{OVERVIEW_TARGET_MARGIN} {formatPercentString(target)}</span>}
      </Metric>
    </section>
  );
}

function Metric({ label, value, accent = false, children }: { label: string; value: string; accent?: boolean; children?: ReactNode }) {
  return (
    <article className={`overview__metric${accent ? " overview__metric--accent" : ""}`}>
      <h3>{label}</h3>
      <p>{value}</p>
      {children && <div className="overview__metric-note">{children}</div>}
    </article>
  );
}

function MonthlyTrend({ periods, reportingCurrency }: { periods: ScenarioPeriodResult[]; reportingCurrency: string | null }) {
  const values = periods.flatMap((period) => [chartAmount(period.revenue), chartAmount(period.period_cost)]).filter((value): value is number => value !== null);
  const max = Math.max(...values, 0);
  return (
    <figure className="overview__chart" aria-label="Monthly revenue and period cost">
      <div className="overview__chart-grid" aria-hidden="true">
        {periods.map((period) => {
          const revenue = chartAmount(period.revenue);
          const cost = chartAmount(period.period_cost);
          return (
            <div className="overview__chart-month" key={period.period_month}>
              <div className="overview__bars">
                <span className="overview__bar overview__bar--revenue" style={{ height: chartHeight(revenue, max) }} />
                <span className="overview__bar overview__bar--cost" style={{ height: chartHeight(cost, max) }} />
              </div>
              <span className="overview__chart-label">{formatCalendarMonth(period.period_month)}</span>
            </div>
          );
        })}
      </div>
      <figcaption className="visually-hidden">
        {periods.map((period) => `${formatCalendarMonth(period.period_month)}: ${periodMoney(period.revenue, period.revenue_currency ?? reportingCurrency)}, ${OVERVIEW_PERIOD_COST.toLowerCase()} ${periodMoney(period.period_cost, period.period_cost_currency ?? reportingCurrency)}.`).join(" ")}
      </figcaption>
    </figure>
  );
}

function CostComposition({ summary }: { summary: ScenarioResults }) {
  const base = periodMoney(summary.personnel_cost.amount, summary.personnel_cost.currency);
  const absence = periodMoney(summary.personnel_cost.paid_absence_amount, summary.personnel_cost.paid_absence_currency);
  const additional = periodMoney(summary.additional_cost.amount, summary.additional_cost.currency);
  const revenueCurrency = summary.revenue.state === "calculated" ? summary.revenue.currency : null;
  return (
    <section className="overview__panel overview__composition" aria-labelledby="overview-composition-heading">
      <h3 id="overview-composition-heading">{OVERVIEW_COST_COMPOSITION}</h3>
      <CostLine label={OVERVIEW_PERSONNEL_COST} value={base} />
      <CostLine label="Paid absence" value={absence} />
      <CostLine label={OVERVIEW_ADDITIONAL_COST} value={additional} />
      <div className="overview__composition-total">
        <CostLine label={OVERVIEW_TOTAL_COST} value={periodMoney(summary.included_cost, revenueCurrency)} />
      </div>
    </section>
  );
}

function CostLine({ label, value }: { label: string; value: string }) {
  return <div className="overview__cost-line"><span>{label}</span><strong>{value}</strong></div>;
}

function chartAmount(value: string | null): number | null {
  if (value === null || value === "n/a") return null;
  const amount = Number(value);
  return Number.isFinite(amount) && amount >= 0 ? amount : null;
}

function chartHeight(value: number | null, max: number): string {
  if (value === null || max <= 0 || value <= 0) return "0%";
  // Numeric conversion is used only for visual bar geometry. Displayed money always stays on the
  // exact fixed-point formatting path above.
  return `${Math.max(2, (value / max) * 100)}%`;
}

function PeriodRow({ period, reportingCurrency }: { period: ScenarioPeriodResult; reportingCurrency: string | null }) {
  const profitCurrency = reportingCurrency ?? period.revenue_currency ?? period.period_cost_currency;
  return (
    <tr data-period-month={period.period_month}>
      <th scope="row">{formatCalendarMonth(period.period_month)}</th>
      <td>{periodMoney(period.revenue, period.revenue_currency)}</td>
      <td>{periodMoney(period.personnel_cost, period.personnel_cost_currency)}</td>
      <td>{periodMoney(period.additional_cost, period.additional_cost_currency)}</td>
      <td>{periodMoney(period.period_cost, period.period_cost_currency)}</td>
      <td>
        {periodMoney(period.profit, profitCurrency)}
        {period.negative_profit === true && <span className="overview__cue">{OVERVIEW_NEGATIVE_PROFIT}</span>}
      </td>
      <td>
        {periodPercent(period.margin)}
        {period.below_target_margin === true && <span className="overview__cue">{OVERVIEW_BELOW_TARGET}</span>}
      </td>
      <td>{period.planned_fte === "n/a" ? NOT_APPLICABLE : formatFteString(period.planned_fte)}</td>
    </tr>
  );
}

function periodMoney(value: string | null, currency: string | null): string {
  if (value === null) return OVERVIEW_FIELD_UNAVAILABLE;
  if (value === "n/a") return NOT_APPLICABLE;
  return currency === null ? OVERVIEW_FIELD_UNAVAILABLE : formatMoneyString(value, currency);
}

function periodPercent(value: string | null): string {
  if (value === null) return OVERVIEW_FIELD_UNAVAILABLE;
  return formatPercentString(value);
}
