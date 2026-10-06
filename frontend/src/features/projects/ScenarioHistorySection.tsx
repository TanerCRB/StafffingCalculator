import { useEffect, useId, useRef, useState } from "react";

import { ApiError, RequestTimeoutError, getScenarioHistory } from "../../api/client";
import type {
  ApprovedScenarioSnapshot,
  ScenarioHistory,
  SnapshotCollectionKey,
} from "../../api/contracts/scenarioHistory";
import { formatMoneyString, formatPercentString } from "../../lib/money";
import { SCENARIO_HISTORY_TEXT as text, scenarioHistoryFieldLabel } from "./scenarioHistoryText";
import "./ScenarioHistorySection.css";

type ReadState =
  | { kind: "idle" | "loading" | "denied" | "timed-out" | "unreadable" | "failed" }
  | { kind: "ready"; history: ScenarioHistory };

type ReadFailure = Exclude<ReadState["kind"], "idle" | "loading" | "ready">;

function readFailure(error: unknown): ReadFailure {
  if (error instanceof RequestTimeoutError) return "timed-out";
  if (error instanceof ApiError) {
    if ([401, 403, 404].includes(error.status)) return "denied";
    if (error.status >= 200 && error.status < 300) return "unreadable";
  }
  return "failed";
}

function mergeSnapshotPages(
  previous: ApprovedScenarioSnapshot | null,
  next: ApprovedScenarioSnapshot | null,
): ApprovedScenarioSnapshot | null {
  if (previous === null || next === null) return next;
  const merge = <T,>(
    oldPage: { items: T[]; total: number; limit: number; offset: number },
    newPage: { items: T[]; total: number; limit: number; offset: number },
  ) => {
    if (oldPage.offset === newPage.offset) return oldPage;
    return {
      ...newPage,
      items: newPage.offset === oldPage.offset + oldPage.limit
        ? [...oldPage.items, ...newPage.items]
        : newPage.items,
    };
  };
  return {
    ...next,
    working_calendars: merge(previous.working_calendars, next.working_calendars),
    working_calendar_days: merge(previous.working_calendar_days, next.working_calendar_days),
    absence_types: merge(previous.absence_types, next.absence_types),
    absence_budgets: merge(previous.absence_budgets, next.absence_budgets),
    catalog_rates: merge(previous.catalog_rates, next.catalog_rates),
    exchange_rates: merge(previous.exchange_rates, next.exchange_rates),
  };
}

const FAILURE_COPY: Readonly<Record<Exclude<ReadState["kind"], "idle" | "loading" | "ready">, string>> = {
  denied: text.denied,
  "timed-out": text.timedOut,
  unreadable: text.unreadable,
  failed: text.failed,
};

function renderValue(key: string, value: unknown, currency: string | null): string {
  if (value === null) return "Not provided";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (typeof value !== "string") return String(value);
  if ((key === "default_selling_rate" || key === "default_cost_rate") && currency !== null) {
    return formatMoneyString(value, currency);
  }
  if (key === "surcharge_percent" || key === "target_margin_percent" || key === "overload_threshold_percent") {
    return formatPercentString(value);
  }
  return value;
}

function FieldList({
  values,
  currency = null,
}: {
  readonly values: object;
  readonly currency?: string | null;
}) {
  return (
    <dl className="scenario-history__fields">
      {Object.entries(values).map(([key, value]) => (
        <div className="scenario-history__field" key={key}>
          <dt>{scenarioHistoryFieldLabel(key)}</dt>
          <dd>{renderValue(key, value, currency)}</dd>
        </div>
      ))}
    </dl>
  );
}

function SnapshotCollection({
  title,
  rows,
  hasMore,
  onLoadMore,
}: {
  readonly title: string;
  readonly rows: readonly object[];
  readonly hasMore?: boolean;
  readonly onLoadMore?: () => void;
}) {
  return (
    <section className="scenario-history__collection" aria-label={title}>
      <h6>{title}</h6>
      {rows.length === 0 ? (
        <p className="scenario-card__metric">{text.noRows}</p>
      ) : (
        <ol className="scenario-history__rows">
          {rows.map((row, index) => {
            const fields = row as Record<string, unknown>;
            return <li className="scenario-history__row" key={`${String(fields.source_rate_id ?? fields.source_calendar_id ?? fields.source_absence_type_id ?? "saved-row")}-${index}`}>
              <FieldList values={row} currency={typeof fields.currency === "string" ? fields.currency : null} />
            </li>;
          })}
        </ol>
      )}
      {hasMore && (
        <button type="button" className="button button--secondary" onClick={onLoadMore}>
          {text.loadMore}
        </button>
      )}
    </section>
  );
}

function HistoryContent({ history, onLoadMore }: {
  readonly history: ScenarioHistory;
  readonly onLoadMore: (collection: SnapshotCollectionKey) => void;
}) {
  const snapshot = history.approved_snapshot;
  const snapshotRows = snapshot === null ? null : [
    ["working_calendars", text.workingCalendars, snapshot.working_calendars],
    ["working_calendar_days", text.workingCalendarDays, snapshot.working_calendar_days],
    ["absence_types", text.absenceTypes, snapshot.absence_types],
    ["absence_budgets", text.absenceBudgets, snapshot.absence_budgets],
    ["catalog_rates", text.catalogRates, snapshot.catalog_rates],
    ["exchange_rates", text.exchangeRates, snapshot.exchange_rates],
  ] as const;

  return (
    <>
      {history.approval_event === null ? (
        <p className="scenario-card__metric">{text.noApproval}</p>
      ) : (
        <section aria-label={text.event} className="scenario-history__event">
          <h5>{text.event}</h5>
          <p className="scenario-card__metric">
            <time dateTime={history.approval_event.created_at}>{history.approval_event.created_at}</time>
          </p>
          <p className="scenario-card__metric">
            {text.actor}: <code>{history.approval_event.performed_by}</code>
          </p>
          <p className="scenario-history__placeholder scenario-card__gaps">{text.unverified}</p>
        </section>
      )}

      <section className="scenario-history__inputs" aria-label={text.currentInputs}>
        <h5>{text.currentInputs}</h5>
        <FieldList values={history.inputs} currency={history.inputs.currency} />
        <p className="scenario-card__metric">
          {text.updated}: <time dateTime={history.updated_at}>{history.updated_at}</time>
        </p>
      </section>

      {snapshot !== null && snapshotRows !== null && (
        <section className="scenario-history__snapshot" aria-label={text.approvedInputs}>
          <h5>{text.approvedInputs}</h5>
          {snapshot.organization_defaults !== null && (
            <SnapshotCollection
              title={text.organizationDefaults}
              rows={[snapshot.organization_defaults]}
            />
          )}
          {snapshotRows.map(([key, title, page]) => (
            <SnapshotCollection
              key={key}
              title={title}
              rows={page.items}
              hasMore={page.offset + page.limit < page.total}
              onLoadMore={() => onLoadMore(key)}
            />
          ))}
        </section>
      )}
    </>
  );
}

export interface ScenarioHistorySectionProps {
  readonly projectId: string;
  readonly scenarioId: string;
  readonly scenarioName: string;
}

/** Explicitly opened, server-authorized read on the scenario card (SC-8-02). */
export function ScenarioHistorySection({ projectId, scenarioId, scenarioName }: ScenarioHistorySectionProps) {
  const headingId = useId();
  const [read, setRead] = useState<ReadState>({ kind: "idle" });
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  async function loadHistory(loadMoreFor?: SnapshotCollectionKey) {
    const previous = read.kind === "ready" ? read.history : null;
    const snapshot = previous?.approved_snapshot;
    const offsets = snapshot === null || snapshot === undefined
      ? undefined
      : {
        working_calendars: snapshot.working_calendars.offset,
        working_calendar_days: snapshot.working_calendar_days.offset,
        absence_types: snapshot.absence_types.offset,
        absence_budgets: snapshot.absence_budgets.offset,
        catalog_rates: snapshot.catalog_rates.offset,
        exchange_rates: snapshot.exchange_rates.offset,
      };
    const limits = snapshot === null || snapshot === undefined || loadMoreFor === undefined
      ? undefined
      : { [loadMoreFor]: snapshot[loadMoreFor].limit };
    if (loadMoreFor !== undefined && offsets !== undefined && snapshot != null) {
      offsets[loadMoreFor] = snapshot[loadMoreFor].offset + snapshot[loadMoreFor].limit;
    }
    controller.current?.abort();
    const requestController = new AbortController();
    controller.current = requestController;
    setRead({ kind: "loading" });
    try {
      const page = await getScenarioHistory(
        projectId,
        scenarioId,
        requestController.signal,
        { limits, offsets },
      );
      if (!requestController.signal.aborted) {
        const history = previous === null
          ? page
          : { ...page, approved_snapshot: mergeSnapshotPages(previous.approved_snapshot, page.approved_snapshot) };
        setRead({ kind: "ready", history });
      }
    } catch (error: unknown) {
      if (!requestController.signal.aborted) setRead({ kind: readFailure(error) });
    }
  }

  let body;
  if (read.kind === "idle") {
    body = (
      <button
        type="button"
        className="button button--secondary"
        aria-label={`${text.open} for ${scenarioName}`}
        onClick={() => void loadHistory()}
      >
        {text.open}
      </button>
    );
  } else if (read.kind === "loading") {
    body = <p className="scenario-card__metric" role="status">{text.loading}</p>;
  } else if (read.kind !== "ready") {
    body = (
      <p role="status" className="scenario-card__gaps" data-history-read-state={read.kind}>
        {FAILURE_COPY[read.kind]}
      </p>
    );
  } else {
    body = <HistoryContent history={read.history} onLoadMore={(collection) => void loadHistory(collection)} />;
  }

  return (
    <section className="scenario-history" aria-labelledby={headingId}>
      <h4 id={headingId} className="scenario-history__title">{text.heading}</h4>
      {body}
      {read.kind !== "idle" && read.kind !== "denied" && read.kind !== "loading" && read.kind !== "ready" && (
        <button type="button" className="button button--secondary" onClick={() => void loadHistory()}>
          {text.retry}
        </button>
      )}
    </section>
  );
}
