import { useEffect, useId, useState } from "react";

import { ApiError, RequestTimeoutError, getScenarioResults } from "../../api/client";
import {
  RESULTS_NOT_APPLICABLE,
  type AdditionalCostSource,
  type GatedResultField,
  type PersonnelCostSource,
  type ScenarioResults,
} from "../../api/contracts/scenarioResults";
import {
  OUTCOME_BASED,
  revenueModelKind,
  type RevenueRead,
} from "../../api/contracts/commercialTerms";
import { NOT_APPLICABLE, formatMoneyString, formatPercentString } from "../../lib/money";
import {
  ADDITIONAL_COST_LABEL,
  ADDITIONAL_COST_STATE_MESSAGES,
  GUARANTEED_REVENUE_LABEL,
  INCLUDED_COST_LABEL,
  MARGIN_LABEL,
  MARKUP_LABEL,
  PERSONNEL_COST_LABEL,
  PERSONNEL_COST_STATE_MESSAGES,
  PROFIT_LABEL,
  PROFITABILITY_CURRENCY_MISMATCH,
  RESULTS_CONFLICT,
  RESULTS_FAILED,
  RESULTS_FIELD_UNAVAILABLE,
  RESULTS_HEADING,
  RESULTS_LOADING,
  RESULTS_READ_AGAIN,
  RESULTS_REFUSED,
  RESULTS_TIMED_OUT,
  RESULTS_UNREADABLE,
  REVENUE_LABEL,
  revenueStateMessage,
} from "./scenarioResultsText";

/**
 * SC-7-02 — a scenario's whole-life profit, margin, markup and cost, as a second section of its
 * card on the project list (Issue #94, gate 1: Q1 = option A, Q2 = option b).
 *
 * Self-contained, the same pattern `ScenarioCommercialTermsSection` already established (ADR-0010):
 * its own `GET …/results`, its own read-state machine, its own `AbortController` per mount/unmount/
 * re-read (point 7). A refusal or a broken answer to *this* read ends inside this section — the
 * card's name and status, the commercial-terms section beside it, and every other card, are
 * untouched (K-06).
 *
 * Two things this section renders that the commercial-terms one does not have to:
 *
 *   * **`403` and `404` render identically** (K-04) — the opposite choice from
 *     `ScenarioCommercialTermsSection`'s `READ_DENIED`/`READ_NOT_FOUND`, and deliberate: gate 1 for
 *     this task asked for one merged, silent refusal here.
 *   * **A `null` field (the personnel-cost gate closed) and an `"n/a"` field (not computable) are
 *     read as the two different values they are** — never inferred from one another, and never
 *     rendered as the same sentence (K-02). The generic "unavailable" wording never explains why
 *     (gate 1, Q2 = option b) — not here, and not in `personnel_cost`'s own line, which the same
 *     gate can null independently of its own `state` (Architect's impact map).
 *
 * SC-4-07 (Issue #125): for Outcome-based only the guaranteed revenue, labelled so — the expected
 * revenue and the categories stay in the commercial-terms section (Q2 = A; a presentation choice,
 * not an access control: the payload still carries them). And `profitability_state =
 * "currency_mismatch"` is a status line of the section, independent of the personnel-cost gate
 * (Q-A = B): the gated fields keep the generic "unavailable" sentence, the line states only that the
 * currencies differ, with no amount and no currency code.
 */

type ReadState =
  | { kind: "loading" }
  | { kind: "ready"; results: ScenarioResults }
  | { kind: "refused" }
  | { kind: "conflict" }
  | { kind: "timed-out" }
  | { kind: "unreadable" }
  | { kind: "failed" };

function toReadFailure(error: unknown): ReadState {
  if (error instanceof RequestTimeoutError) {
    return { kind: "timed-out" };
  }
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403 || error.status === 404) {
      // One rendered state for both (K-04) — unlike the commercial-terms section, which keeps them
      // apart. The merge happens here, in the screen, not by collapsing the two statuses upstream:
      // `ApiError.status` still carries the real number for anything that needs it later.
      return { kind: "refused" };
    }
    if (error.status === 409) {
      return { kind: "conflict" };
    }
    if (error.status >= 200 && error.status < 300) {
      // `getScenarioResults` keeps the real status on a shape failure: a 2xx here means the server
      // answered and the answer is not the contract.
      return { kind: "unreadable" };
    }
  }
  return { kind: "failed" };
}

const READ_FAILURE_MESSAGES: Readonly<Record<Exclude<ReadState["kind"], "loading" | "ready">, string>> =
  {
    refused: RESULTS_REFUSED,
    conflict: RESULTS_CONFLICT,
    "timed-out": RESULTS_TIMED_OUT,
    unreadable: RESULTS_UNREADABLE,
    failed: RESULTS_FAILED,
  };

/** Every failure but the silent refusal is worth asking again — `409` most of all (K-05): the race
 * it names is a live one, and the very next read is the expected way past it. */
const RETRYABLE: ReadonlySet<ReadState["kind"]> = new Set(["conflict", "timed-out", "unreadable", "failed"]);

export interface ScenarioResultsSectionProps {
  readonly projectId: string;
  readonly scenarioId: string;
  /** Only for accessible names: several cards carry the same controls. */
  readonly scenarioName: string;
}

export function ScenarioResultsSection({
  projectId,
  scenarioId,
  scenarioName,
}: ScenarioResultsSectionProps) {
  const headingId = useId();
  const [read, setRead] = useState<ReadState>({ kind: "loading" });
  /** Bumped by "Read scenario results again" — the read effect depends on it. */
  const [readRequest, setReadRequest] = useState(0);

  useEffect(() => {
    // Leaving the card, or asking again, ends the read — it does not merely stop listening to it
    // (ADR-0010, point 7), the same discipline `ScenarioCommercialTermsSection` already applies.
    const controller = new AbortController();
    let left = false;
    getScenarioResults(projectId, scenarioId, controller.signal)
      .then((results) => {
        if (!left) {
          setRead({ kind: "ready", results });
        }
      })
      .catch((error: unknown) => {
        if (!left) {
          setRead(toReadFailure(error));
        }
      });
    return () => {
      left = true;
      controller.abort();
    };
  }, [projectId, scenarioId, readRequest]);

  function readAgain() {
    setRead({ kind: "loading" });
    setReadRequest((count) => count + 1);
  }

  let body;
  if (read.kind === "loading") {
    body = <p className="scenario-card__metric">{RESULTS_LOADING}</p>;
  } else if (read.kind !== "ready") {
    body = (
      <>
        <p role="status" className="scenario-card__gaps" data-read-failure={read.kind}>
          {READ_FAILURE_MESSAGES[read.kind]}
        </p>
        {RETRYABLE.has(read.kind) && (
          <div className="scenario-results__actions">
            <button
              type="button"
              className="button button--secondary"
              aria-label={`${RESULTS_READ_AGAIN} for ${scenarioName}`}
              onClick={readAgain}
            >
              {RESULTS_READ_AGAIN}
            </button>
          </div>
        )}
      </>
    );
  } else {
    const { results } = read;
    // `profit`/`included_cost` carry no currency of their own — the revenue's, and only when the
    // revenue itself is calculated (Architect's impact map; guaranteed whenever the aggregate is a
    // real number, since every source's own `currency_mismatch` check runs against the scenario's
    // currency). Never substituted by a project's reporting currency.
    const revenueCurrency = results.revenue.state === "calculated" ? results.revenue.currency : null;
    body = (
      <>
        <RevenueLine revenue={results.revenue} />
        {results.profitability_state === "currency_mismatch" && (
          <p role="status" className="scenario-card__gaps" data-profitability-state="currency_mismatch">
            {PROFITABILITY_CURRENCY_MISMATCH}
          </p>
        )}
        <PersonnelCostLine source={results.personnel_cost} />
        <AdditionalCostLine source={results.additional_cost} />
        <GatedMoneyLine label={INCLUDED_COST_LABEL} value={results.included_cost} currency={revenueCurrency} />
        <GatedMoneyLine label={PROFIT_LABEL} value={results.profit} currency={revenueCurrency} />
        <GatedPercentLine label={MARGIN_LABEL} value={results.margin} />
        <GatedPercentLine label={MARKUP_LABEL} value={results.markup} />
      </>
    );
  }

  return (
    <section className="scenario-results" aria-labelledby={headingId}>
      <h4 id={headingId} className="scenario-results__title">
        {RESULTS_HEADING}
      </h4>
      {body}
    </section>
  );
}

/** The revenue, or the named state that withholds it — never `0`, never blank (K-01, K-03). The
 * label and the sentence are chosen by `assumptions_used.model_type`, never by `rate_source`
 * (SC-4-07): Outcome-based's amount is its guaranteed revenue; Time & Material and Story Points keep
 * the plain label. */
function RevenueLine({ revenue }: { revenue: RevenueRead }) {
  const model = revenueModelKind(revenue);
  if (revenue.state === "calculated") {
    return (
      <p className="scenario-card__metric" data-revenue-state={revenue.state}>
        {model === OUTCOME_BASED ? GUARANTEED_REVENUE_LABEL : REVENUE_LABEL}{" "}
        {formatMoneyString(revenue.amount, revenue.currency)}
      </p>
    );
  }
  return (
    <p className="scenario-card__gaps" data-revenue-state={revenue.state}>
      {revenueStateMessage(revenue.state, model)}
    </p>
  );
}

/**
 * The base personnel cost, or the reason it is not shown.
 *
 * `amount` and `state` are read independently (Architect's impact map): the personnel-cost gate can
 * null `amount` while `state` still says `"calculated"` — that combination renders the same generic
 * "unavailable" sentence the four aggregate fields use below, never `state`'s own wording (K-02). A
 * gate that is open but a `state` naming a cause renders that cause, never the generic sentence
 * (K-03).
 */
function PersonnelCostLine({ source }: { source: PersonnelCostSource }) {
  if (source.amount === null) {
    return (
      <p className="scenario-card__gaps" data-personnel-cost-state="unavailable">
        {PERSONNEL_COST_LABEL} {RESULTS_FIELD_UNAVAILABLE}
      </p>
    );
  }
  if (source.state !== "calculated") {
    return (
      <p className="scenario-card__gaps" data-personnel-cost-state={source.state}>
        {PERSONNEL_COST_STATE_MESSAGES[source.state]}
      </p>
    );
  }
  return (
    <p className="scenario-card__metric" data-personnel-cost-state="calculated">
      {PERSONNEL_COST_LABEL} {formatMoneyString(source.amount, source.currency ?? "")}
    </p>
  );
}

/** The additional-cost sum, or the reason it is not shown. Never gated (ADR-0014, point 11) — no
 * "unavailable" branch here, only `state`'s own named causes (K-03). */
function AdditionalCostLine({ source }: { source: AdditionalCostSource }) {
  if (source.state !== "calculated") {
    return (
      <p className="scenario-card__gaps" data-additional-cost-state={source.state}>
        {ADDITIONAL_COST_STATE_MESSAGES[source.state]}
      </p>
    );
  }
  return (
    <p className="scenario-card__metric" data-additional-cost-state="calculated">
      {ADDITIONAL_COST_LABEL} {formatMoneyString(source.amount, source.currency ?? "")}
    </p>
  );
}

/**
 * One of the four gated money fields (`included_cost`, `profit`) exactly as the backend sent it:
 * `null` (gate closed) renders the generic "unavailable" sentence, the sentinel `"n/a"` renders
 * `lib/money.ts`'s own "Not applicable" — two different sentences for two different reasons (K-02) —
 * and a real decimal string is formatted by `formatMoneyString` and nothing else (K-01).
 */
function GatedMoneyLine({
  label,
  value,
  currency,
}: {
  label: string;
  value: GatedResultField;
  currency: string | null;
}) {
  if (value === null) {
    return (
      <p className="scenario-card__gaps" data-result-state="unavailable">
        {label} {RESULTS_FIELD_UNAVAILABLE}
      </p>
    );
  }
  if (value === RESULTS_NOT_APPLICABLE) {
    return (
      <p className="scenario-card__metric" data-result-state="not-applicable">
        {label} {NOT_APPLICABLE}
      </p>
    );
  }
  return (
    <p className="scenario-card__metric" data-result-state="calculated">
      {label} {formatMoneyString(value, currency ?? "")}
    </p>
  );
}

/**
 * One of the four gated percent fields (`margin`, `markup`). `formatPercentString` already turns the
 * backend's `"n/a"` sentinel into "Not applicable" on its own (K-01) — the gate's `null` is handled
 * here, before that function ever sees the value, so the two withheld reasons stay two sentences
 * (K-02).
 */
function GatedPercentLine({ label, value }: { label: string; value: GatedResultField }) {
  if (value === null) {
    return (
      <p className="scenario-card__gaps" data-result-state="unavailable">
        {label} {RESULTS_FIELD_UNAVAILABLE}
      </p>
    );
  }
  return (
    <p
      className="scenario-card__metric"
      data-result-state={value === RESULTS_NOT_APPLICABLE ? "not-applicable" : "calculated"}
    >
      {label} {formatPercentString(value)}
    </p>
  );
}
