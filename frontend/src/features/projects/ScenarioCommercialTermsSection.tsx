import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import {
  ApiError,
  RequestTimeoutError,
  createScenarioCommercialTerms,
  deleteScenarioCommercialTerms,
  editScenarioCommercialTerms,
  editScenarioFixedPriceTerms,
  getScenarioCommercialTerms,
} from "../../api/client";
import {
  FIXED_PRICE,
  OUTCOME_BASED,
  OUTCOME_CATEGORIES,
  TIME_AND_MATERIAL,
  STORY_POINTS,
  type CommercialTermsCreateRequest,
  type CommercialTermsEditRequest,
  catalogAssumptionsOf,
  revenueModelKind,
  type CalculatedRevenueRead,
  type CategoryRevenueRead,
  type OutcomeCategory,
  type OutcomeCategoryRead,
  type OutcomeTermsRead,
  type RevenueRead,
  type ScenarioCommercialTerms,
} from "../../api/contracts/commercialTerms";
import { formatCalendarMonth, formatEffectivePeriod } from "../../lib/dates";
import {
  formatMoneyString,
  formatRuleAmountString,
  formatStoredPercentString,
} from "../../lib/money";
import {
  APPROVED_SCENARIO_NOTE,
  AGREED_PRICE_LABEL,
  CATEGORY_PROBABILITY_LABEL,
  CATEGORY_REVENUE_LABEL,
  CATEGORY_UNITS_LABEL,
  COMMERCIAL_MODEL_LABEL,
  COMMERCIAL_TERMS_HEADING,
  EXPECTED_NO_PROBABILITIES,
  EXPECTED_REVENUE_LABEL,
  FIXED_FEE_LABEL,
  GUARANTEED_REVENUE_LABEL,
  NOT_GIVEN,
  NO_RATE_WINDOWS,
  NO_RULE,
  OUTCOME_CATEGORIES_LABEL,
  OUTCOME_CATEGORY_LABELS,
  RATE_SOURCE_LABELS,
  RATE_WINDOWS_LABEL,
  READ_AGAIN,
  READ_DENIED,
  READ_FAILED,
  READ_LOADING,
  READ_NOT_FOUND,
  READ_TIMED_OUT,
  READ_UNREADABLE,
  EDIT_FIXED_PRICE,
  EDIT_OUTCOME_BASED,
  EDIT_STORY_POINTS,
  DELETE_COMMERCIAL_RULE,
  FIXED_PRICE_CURRENCY_FIELD_LABEL,
  FIXED_PRICE_PRICE_LABEL,
  REVENUE_LABEL,
  REVENUE_MAX_LABEL,
  REVENUE_MIN_LABEL,
  RULE_CATEGORIES_LABEL,
  RULE_CATEGORY_PROBABILITY_LABEL,
  RULE_CATEGORY_UNITS_LABEL,
  RULE_CURRENCY_LABEL,
  RULE_PARAMETERS_LABEL,
  RULE_SOURCE_LABELS,
  SAVED,
  SAVING,
  SET_TIME_AND_MATERIAL,
  SET_FIXED_PRICE,
  SET_OUTCOME_BASED,
  SET_STORY_POINTS,
  SAVE_FIXED_PRICE,
  SAVE_COMMERCIAL_RULE,
  DELETE_DENIED,
  DELETE_CONFLICT,
  DELETE_UNRESOLVED,
  STORY_POINTS_PRICE_LABEL,
  STORY_POINTS_ACCEPTED_LABEL,
  OUTCOME_FIELD_LABELS,
  OUTCOME_UNITS_INPUT_LABEL,
  OUTCOME_PROBABILITY_INPUT_LABEL,
  SUCCESS_BONUS_LABEL,
  UNIT_RATE_LABEL,
  UNRESOLVED_MONTHS_LABEL,
  commercialModelName,
  describeCommercialTermsWriteFailure,
  revenueStateMessage,
} from "./commercialTermsText";

/**
 * SC-4-06 — a scenario's commercial rule and revenue, as a section of its card on the project list
 * (gate 1, D-2 = option A: no screen, no router, no `ScreenKey`).
 *
 * The first element on `ProjectListScreen` that writes, and deliberately self-contained: it reads
 * its own `GET …/commercial-terms`, separately from `GET /projects`, so that a refusal of *this* read
 * (`403`, `404`) or a broken answer to it ends here and leaves the scenario's name and status on the
 * card (K-04). A render-phase exception still reaches the one boundary `AppShell` mounts (ADR-0010,
 * point 1) — there is no second boundary here.
 *
 * What it shows comes from the server and from nothing else:
 *   * the revenue only when `state` is `"calculated"`, formatted by `lib/money.ts` from the string it
 *     arrived as, in the currency it arrived with (ADR-0002; K-02);
 *   * after a save, the `201` body — never a state assembled when the button was pressed (ADR-0009,
 *     addendum 2026-09-23; K-05);
 *   * create controls only where the read said there is no rule and the scenario is not approved;
 *     same-model replacement and removal carry the read's concurrency marker and stay hidden for
 *     approved scenarios (SC-4-11, ADR-0003/0004).
 *
 * SC-4-07 (Issue #125): which lines a revenue gets is chosen by `assumptions_used.model_type`, never
 * by `rate_source` (ADR-0003, addendum SC-4-07, point 3). Outcome-based shows the guaranteed and the
 * expected revenue as two separately labelled amounts, the four categories by their word, and the
 * rule's parameters as stored (`null` = none, never `0`); Story Points shows its revenue and its
 * source; neither shows the Time & Material lines about rate windows and months.
 */

type ReadState =
  | { kind: "loading" }
  | { kind: "ready"; terms: ScenarioCommercialTerms }
  | { kind: "denied" }
  | { kind: "not-found" }
  | { kind: "timed-out" }
  | { kind: "unreadable" }
  | { kind: "failed" };

type WriteState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "refused"; message: string }
  | { kind: "unresolved"; message: string };

type FixedPriceFormState = {
  mode: "create" | "edit";
  agreedPrice: string;
  currency: string;
};

type RuleFormState = {
  mode: "create" | "edit";
  modelType: typeof STORY_POINTS | typeof OUTCOME_BASED;
  values: Record<string, string>;
};

function writeFailure(error: unknown): Extract<WriteState, { kind: "refused" | "unresolved" }> {
  const message = describeCommercialTermsWriteFailure(error);
  if (error instanceof RequestTimeoutError) {
    return { kind: "unresolved", message };
  }
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return { kind: "refused", message };
  }
  return { kind: "unresolved", message };
}

function toReadFailure(error: unknown): ReadState {
  if (error instanceof RequestTimeoutError) {
    return { kind: "timed-out" };
  }
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) {
      return { kind: "denied" };
    }
    if (error.status === 404) {
      return { kind: "not-found" };
    }
    if (error.status >= 200 && error.status < 300) {
      // `getScenarioCommercialTerms` keeps the real status on a shape failure: a 2xx here means the
      // server answered and the answer is not the contract.
      return { kind: "unreadable" };
    }
  }
  return { kind: "failed" };
}

const READ_FAILURE_MESSAGES: Readonly<Record<Exclude<ReadState["kind"], "loading" | "ready">, string>> =
  {
    denied: READ_DENIED,
    "not-found": READ_NOT_FOUND,
    "timed-out": READ_TIMED_OUT,
    unreadable: READ_UNREADABLE,
    failed: READ_FAILED,
  };

export interface ScenarioCommercialTermsSectionProps {
  readonly projectId: string;
  readonly scenarioId: string;
  /** Only for accessible names: several cards carry the same controls. */
  readonly scenarioName: string;
}

export function ScenarioCommercialTermsSection({
  projectId,
  scenarioId,
  scenarioName,
}: ScenarioCommercialTermsSectionProps) {
  const headingId = useId();
  const [read, setRead] = useState<ReadState>({ kind: "loading" });
  const [write, setWrite] = useState<WriteState>({ kind: "idle" });
  const [fixedPriceForm, setFixedPriceForm] = useState<FixedPriceFormState | null>(null);
  const [ruleForm, setRuleForm] = useState<RuleFormState | null>(null);
  /** Bumped by "Read commercial terms again" — the read effect depends on it. */
  const [readRequest, setReadRequest] = useState(0);
  const mounted = useRef(true);
  const outcomeRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    // Leaving the card, or asking again, ends the read — it does not merely stop listening to it
    // (ADR-0010, point 7). `controller.abort()` reaches `fetch`; `left` keeps the rejection the abort
    // produces, and an answer that arrived anyway, off a card that no longer wants it. Both halves,
    // for the reason `ProjectListScreen` gives: the flag alone is the `CatalogScreen` R-02 defect.
    const controller = new AbortController();
    let left = false;
    getScenarioCommercialTerms(projectId, scenarioId, controller.signal)
      .then((terms) => {
        if (!left) {
          setRead({ kind: "ready", terms });
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

  useEffect(() => {
    // The control that was pressed is gone once the save has an answer; the answer takes the focus
    // so that keyboard users are not dropped onto the page body.
    if (write.kind === "saved" || write.kind === "refused" || write.kind === "unresolved") {
      outcomeRef.current?.focus();
    }
  }, [write.kind]);

  async function setTimeAndMaterial() {
    setWrite({ kind: "saving" });
    try {
      const terms = await createScenarioCommercialTerms(projectId, scenarioId, {
        model_type: TIME_AND_MATERIAL,
      });
      if (!mounted.current) {
        return;
      }
      // The `201` body, which passed the same shape check a read does, is the new state — nothing
      // assembled here (K-05).
      setRead({ kind: "ready", terms });
      setWrite({ kind: "saved" });
    } catch (error: unknown) {
      if (mounted.current) {
        setWrite(writeFailure(error));
      }
    }
  }

  async function saveFixedPrice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (fixedPriceForm === null) return;
    setWrite({ kind: "saving" });
    try {
      let terms: ScenarioCommercialTerms;
      if (fixedPriceForm.mode === "create") {
        terms = await createScenarioCommercialTerms(projectId, scenarioId, {
          model_type: FIXED_PRICE,
          agreed_price: fixedPriceForm.agreedPrice,
          currency: fixedPriceForm.currency,
        });
      } else {
        const currentTerms = read.kind === "ready" ? read.terms.commercial_terms : null;
        if (currentTerms === null || currentTerms.model_type !== FIXED_PRICE) {
          setWrite({ kind: "unresolved", message: READ_FAILED });
          return;
        }
        terms = await editScenarioFixedPriceTerms(projectId, scenarioId, {
          updated_at: currentTerms.updated_at,
          agreed_price: fixedPriceForm.agreedPrice,
        });
      }
      if (!mounted.current) return;
      setRead({ kind: "ready", terms });
      setFixedPriceForm(null);
      setWrite({ kind: "saved" });
    } catch (error: unknown) {
      if (mounted.current) {
        setWrite(writeFailure(error));
      }
    }
  }

  async function saveRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (ruleForm === null) return;
    const values = ruleForm.values;
    const body = ruleForm.modelType === STORY_POINTS
      ? {
          model_type: STORY_POINTS,
          price_per_point: values.price_per_point ?? "",
          accepted_points: Number(values.accepted_points),
          currency: values.currency ?? "",
          ...(ruleForm.mode === "edit" && read.kind === "ready" && read.terms.commercial_terms !== null
            ? { updated_at: read.terms.commercial_terms.updated_at } : {}),
        }
      : {
          model_type: OUTCOME_BASED,
          currency: values.currency ?? "",
          fixed_fee: values.fixed_fee ?? "",
          success_bonus: values.success_bonus || null,
          unit_rate: values.unit_rate || null,
          revenue_min: values.revenue_min || null,
          revenue_max: values.revenue_max || null,
          categories: Object.fromEntries(OUTCOME_CATEGORIES.map((category) => [category, {
            units: values[`${category}.units`] || null,
            probability: values[`${category}.probability`] || null,
          }])) as Record<OutcomeCategory, OutcomeCategoryRead>,
          ...(ruleForm.mode === "edit" && read.kind === "ready" && read.terms.commercial_terms !== null
            ? { updated_at: read.terms.commercial_terms.updated_at } : {}),
        };
    setWrite({ kind: "saving" });
    try {
      const terms = ruleForm.mode === "create"
        ? await createScenarioCommercialTerms(projectId, scenarioId, body as CommercialTermsCreateRequest)
        : await editScenarioCommercialTerms(projectId, scenarioId, body as CommercialTermsEditRequest);
      if (!mounted.current) return;
      setRead({ kind: "ready", terms });
      setRuleForm(null);
      setWrite({ kind: "saved" });
    } catch (error: unknown) {
      if (mounted.current) setWrite(writeFailure(error));
    }
  }

  async function removeRule() {
    if (read.kind !== "ready" || read.terms.commercial_terms === null) return;
    setWrite({ kind: "saving" });
    try {
      await deleteScenarioCommercialTerms(projectId, scenarioId, {
        updated_at: read.terms.commercial_terms.updated_at,
      });
      if (!mounted.current) return;
      setRuleForm(null);
      setFixedPriceForm(null);
      setWrite({ kind: "idle" });
      setRead({ kind: "loading" });
      setReadRequest((count) => count + 1);
    } catch (error: unknown) {
      if (mounted.current) {
        const message = error instanceof RequestTimeoutError
          ? DELETE_UNRESOLVED
          : error instanceof ApiError && error.status === 403
            ? DELETE_DENIED
            : error instanceof ApiError && error.status === 409
              ? DELETE_CONFLICT
              : describeCommercialTermsWriteFailure(error);
        setWrite({ kind: error instanceof RequestTimeoutError ? "unresolved" : "refused", message });
      }
    }
  }

  function readAgain() {
    setRead({ kind: "loading" });
    setWrite({ kind: "idle" });
    setFixedPriceForm(null);
    setRuleForm(null);
    setReadRequest((count) => count + 1);
  }

  function startRuleForm(modelType: typeof STORY_POINTS | typeof OUTCOME_BASED, mode: "create" | "edit") {
    const current = read.kind === "ready" ? read.terms.commercial_terms : null;
    const initial: Record<string, string> = {};
    if (mode === "edit" && current !== null) {
      if (modelType === STORY_POINTS && current.model_type === STORY_POINTS) {
        initial.price_per_point = current.price_per_point ?? "";
        initial.accepted_points = current.accepted_points?.toString() ?? "";
        initial.currency = current.currency ?? "";
      } else if (modelType === OUTCOME_BASED && current.model_type === OUTCOME_BASED && current.outcome_terms !== null) {
        const outcome = current.outcome_terms;
        initial.currency = outcome.currency;
        initial.fixed_fee = outcome.fixed_fee;
        initial.success_bonus = outcome.success_bonus ?? "";
        initial.unit_rate = outcome.unit_rate ?? "";
        initial.revenue_min = outcome.revenue_min ?? "";
        initial.revenue_max = outcome.revenue_max ?? "";
        for (const category of OUTCOME_CATEGORIES) {
          initial[`${category}.units`] = outcome.categories[category].units ?? "";
          initial[`${category}.probability`] = outcome.categories[category].probability ?? "";
        }
      }
    }
    setWrite({ kind: "idle" });
    setFixedPriceForm(null);
    setRuleForm({ mode, modelType, values: initial });
  }

  const readAgainButton = (
    <button
      type="button"
      className="button button--secondary"
      aria-label={`${READ_AGAIN} for ${scenarioName}`}
      onClick={readAgain}
    >
      {READ_AGAIN}
    </button>
  );

  let body;
  // An answer about another scenario never reaches `ready`: `isScenarioCommercialTermsShape` compares
  // `scenario_id` with the one asked about, for the read and the `201` alike (K-07).
  if (read.kind === "loading") {
    body = <p className="scenario-card__metric">{READ_LOADING}</p>;
  } else if (read.kind !== "ready") {
    // Denied and not-found offer nothing to do — a denied read renders no actions (ADR-0005). The
    // three failures of the answer itself may be worth asking again.
    const retryable = read.kind === "timed-out" || read.kind === "unreadable" || read.kind === "failed";
    body = (
      <>
        <p role="status" className="scenario-card__gaps" data-read-failure={read.kind}>
          {READ_FAILURE_MESSAGES[read.kind]}
        </p>
        {retryable && <div className="commercial-terms__actions">{readAgainButton}</div>}
      </>
    );
  } else {
    const { terms } = read;
    const noRule = terms.commercial_terms === null;
    const offerSet =
      noRule && ruleForm === null &&
      terms.scenario_status !== "Approved" &&
      (write.kind === "idle" || write.kind === "saving");
    const fixedPriceTerms =
      terms.commercial_terms?.model_type === FIXED_PRICE ? terms.commercial_terms : null;
    const canEditFixedPrice =
      fixedPriceTerms !== null &&
      terms.scenario_status !== "Approved" &&
      typeof fixedPriceTerms.agreed_price === "string" &&
      typeof fixedPriceTerms.currency === "string";
    const storyPointsTerms = terms.commercial_terms?.model_type === STORY_POINTS ? terms.commercial_terms : null;
    const hasOutcomeRule = terms.commercial_terms?.model_type === OUTCOME_BASED;
    const canWriteDraft = terms.scenario_status !== "Approved";
    body = (
      <>
        <p className="scenario-card__metric">
          {COMMERCIAL_MODEL_LABEL}{" "}
          {terms.commercial_terms === null
            ? NO_RULE
            : commercialModelName(terms.commercial_terms.model_type)}
        </p>
        <RevenueLine revenue={terms.revenue} />
        {terms.revenue.state === "calculated" &&
          revenueModelKind(terms.revenue) === OUTCOME_BASED && (
            <OutcomeRevenue revenue={terms.revenue} />
          )}
        {terms.commercial_terms !== null && <Assumptions revenue={terms.revenue} />}
        {terms.commercial_terms !== null && terms.commercial_terms.outcome_terms !== null && (
          <OutcomeRuleParameters parameters={terms.commercial_terms.outcome_terms} />
        )}
        {fixedPriceTerms !== null &&
          typeof fixedPriceTerms.agreed_price === "string" &&
          typeof fixedPriceTerms.currency === "string" && (
            <p className="scenario-card__metric" data-fixed-price="agreed-price">
              {AGREED_PRICE_LABEL} {formatRuleAmountString(fixedPriceTerms.agreed_price, fixedPriceTerms.currency)}
            </p>
          )}
        {storyPointsTerms !== null && typeof storyPointsTerms.price_per_point === "string" && typeof storyPointsTerms.currency === "string" && typeof storyPointsTerms.accepted_points === "number" && (
          <>
            <p className="scenario-card__metric">{STORY_POINTS_PRICE_LABEL}: {formatRuleAmountString(storyPointsTerms.price_per_point, storyPointsTerms.currency)}</p>
            <p className="scenario-card__metric">{STORY_POINTS_ACCEPTED_LABEL}: {storyPointsTerms.accepted_points}</p>
          </>
        )}
        {noRule && terms.scenario_status === "Approved" && (
          <p className="scenario-card__metric">{APPROVED_SCENARIO_NOTE}</p>
        )}
        {offerSet && fixedPriceForm === null && (
          <div className="commercial-terms__actions">
            <button
              type="button"
              className="button button--primary"
              aria-label={`${SET_TIME_AND_MATERIAL} for ${scenarioName}`}
              disabled={write.kind === "saving"}
              onClick={() => void setTimeAndMaterial()}
            >
              {SET_TIME_AND_MATERIAL}
            </button>
            <button
              type="button"
              className="button button--secondary"
              aria-label={`${SET_FIXED_PRICE} for ${scenarioName}`}
              disabled={write.kind === "saving"}
              onClick={() => {
                setWrite({ kind: "idle" });
                setFixedPriceForm({ mode: "create", agreedPrice: "", currency: "" });
              }}
            >
              {SET_FIXED_PRICE}
            </button>
            <button type="button" aria-label={`${SET_STORY_POINTS} for ${scenarioName}`} className="button button--secondary" disabled={write.kind === "saving"} onClick={() => startRuleForm(STORY_POINTS, "create")}>{SET_STORY_POINTS}</button>
            <button type="button" aria-label={`${SET_OUTCOME_BASED} for ${scenarioName}`} className="button button--secondary" disabled={write.kind === "saving"} onClick={() => startRuleForm(OUTCOME_BASED, "create")}>{SET_OUTCOME_BASED}</button>
          </div>
        )}
        {canWriteDraft && storyPointsTerms !== null && ruleForm === null && (
          <div className="commercial-terms__actions"><button type="button" aria-label={`${EDIT_STORY_POINTS} for ${scenarioName}`} className="button button--secondary" disabled={write.kind === "saving"} onClick={() => startRuleForm(STORY_POINTS, "edit")}>{EDIT_STORY_POINTS}</button></div>
        )}
        {canWriteDraft && hasOutcomeRule && ruleForm === null && (
          <div className="commercial-terms__actions"><button type="button" aria-label={`${EDIT_OUTCOME_BASED} for ${scenarioName}`} className="button button--secondary" disabled={write.kind === "saving"} onClick={() => startRuleForm(OUTCOME_BASED, "edit")}>{EDIT_OUTCOME_BASED}</button></div>
        )}
        {canWriteDraft && terms.commercial_terms !== null && ruleForm === null && fixedPriceForm === null && (
          <div className="commercial-terms__actions"><button type="button" aria-label={`${DELETE_COMMERCIAL_RULE} for ${scenarioName}`} className="button button--secondary" disabled={write.kind === "saving"} onClick={() => void removeRule()}>{DELETE_COMMERCIAL_RULE}</button></div>
        )}
        {ruleForm !== null && (
          <form className="commercial-terms__form" onSubmit={(event) => void saveRule(event)}>
            {ruleForm.modelType === STORY_POINTS ? (
              <>
                <label>{STORY_POINTS_PRICE_LABEL}<input className="input" inputMode="decimal" required aria-label={`${STORY_POINTS_PRICE_LABEL} for ${scenarioName}`} value={ruleForm.values.price_per_point ?? ""} onChange={(event) => setRuleForm((current) => current === null ? null : { ...current, values: { ...current.values, price_per_point: event.target.value } })} /></label>
                <label>{STORY_POINTS_ACCEPTED_LABEL}<input className="input" inputMode="numeric" type="number" min="0" step="1" required aria-label={`${STORY_POINTS_ACCEPTED_LABEL} for ${scenarioName}`} value={ruleForm.values.accepted_points ?? ""} onChange={(event) => setRuleForm((current) => current === null ? null : { ...current, values: { ...current.values, accepted_points: event.target.value } })} /></label>
                <label>{FIXED_PRICE_CURRENCY_FIELD_LABEL}<input className="input" maxLength={3} required aria-label={`${FIXED_PRICE_CURRENCY_FIELD_LABEL} for ${scenarioName}`} value={ruleForm.values.currency ?? ""} onChange={(event) => setRuleForm((current) => current === null ? null : { ...current, values: { ...current.values, currency: event.target.value } })} /></label>
              </>
            ) : (
              <>
                {Object.entries(OUTCOME_FIELD_LABELS).map(([field, label]) => (
                  <label key={field}>{label}<input className="input" inputMode={field === "currency" ? "text" : "decimal"} required={field === "currency" || field === "fixed_fee"} aria-label={`${label} for ${scenarioName}`} value={ruleForm.values[field] ?? ""} onChange={(event) => setRuleForm((current) => current === null ? null : { ...current, values: { ...current.values, [field]: event.target.value } })} /></label>
                ))}
                {OUTCOME_CATEGORIES.map((category) => (
                  <fieldset key={category}><legend>{OUTCOME_CATEGORY_LABELS[category]}</legend>
                    <label>{OUTCOME_UNITS_INPUT_LABEL}<input className="input" inputMode="decimal" aria-label={`${OUTCOME_CATEGORY_LABELS[category]} units for ${scenarioName}`} value={ruleForm.values[`${category}.units`] ?? ""} onChange={(event) => setRuleForm((current) => current === null ? null : { ...current, values: { ...current.values, [`${category}.units`]: event.target.value } })} /></label>
                    <label>{OUTCOME_PROBABILITY_INPUT_LABEL}<input className="input" inputMode="decimal" aria-label={`${OUTCOME_CATEGORY_LABELS[category]} probability for ${scenarioName}`} value={ruleForm.values[`${category}.probability`] ?? ""} onChange={(event) => setRuleForm((current) => current === null ? null : { ...current, values: { ...current.values, [`${category}.probability`]: event.target.value } })} /></label>
                  </fieldset>
                ))}
              </>
            )}
            <button type="submit" className="button button--primary" disabled={write.kind === "saving"}>{SAVE_COMMERCIAL_RULE}</button>
            <button type="button" className="button button--secondary" disabled={write.kind === "saving"} onClick={() => setRuleForm(null)}>Cancel</button>
          </form>
        )}
        {canEditFixedPrice && fixedPriceForm === null && write.kind !== "refused" && write.kind !== "unresolved" && (
          <div className="commercial-terms__actions">
            <button
              type="button"
              className="button button--secondary"
              aria-label={`${EDIT_FIXED_PRICE} for ${scenarioName}`}
              disabled={write.kind === "saving"}
              onClick={() => {
                setWrite({ kind: "idle" });
                setFixedPriceForm({
                  mode: "edit",
                  agreedPrice: fixedPriceTerms.agreed_price ?? "",
                  currency: fixedPriceTerms.currency ?? "",
                });
              }}
            >
              {EDIT_FIXED_PRICE}
            </button>
          </div>
        )}
        {fixedPriceForm !== null && (
          <form className="commercial-terms__form" onSubmit={(event) => void saveFixedPrice(event)}>
            <label>
              {FIXED_PRICE_PRICE_LABEL}
              <input
                aria-label={`${FIXED_PRICE_PRICE_LABEL} for ${scenarioName}`}
                inputMode="decimal"
                name="agreed_price"
                className="input"
                required
                value={fixedPriceForm.agreedPrice}
                onChange={(event) => setFixedPriceForm((current) =>
                  current === null ? null : { ...current, agreedPrice: event.target.value },
                )}
              />
            </label>
            {fixedPriceForm.mode === "create" ? (
              <label>
                {FIXED_PRICE_CURRENCY_FIELD_LABEL}
                <input
                  aria-label={`${FIXED_PRICE_CURRENCY_FIELD_LABEL} for ${scenarioName}`}
                  autoCapitalize="characters"
                  maxLength={3}
                  name="currency"
                  className="input"
                  required
                  value={fixedPriceForm.currency}
                  onChange={(event) => setFixedPriceForm((current) =>
                    current === null ? null : { ...current, currency: event.target.value },
                  )}
                />
              </label>
            ) : (
              <p className="scenario-card__metric">{FIXED_PRICE_CURRENCY_FIELD_LABEL} {fixedPriceForm.currency}</p>
            )}
            <button type="submit" className="button button--primary" disabled={write.kind === "saving"}>
              {write.kind === "saving" ? SAVING : SAVE_FIXED_PRICE}
            </button>
            <button
              type="button"
              className="button button--secondary"
              disabled={write.kind === "saving"}
              onClick={() => {
                setFixedPriceForm(null);
                setWrite({ kind: "idle" });
              }}
            >
              Cancel
            </button>
          </form>
        )}
        {write.kind === "saving" && (
          <p role="status" className="scenario-card__metric">
            {SAVING}
          </p>
        )}
        {write.kind === "saved" && (
          <p ref={outcomeRef} tabIndex={-1} role="status" className="scenario-card__metric">
            {SAVED}
          </p>
        )}
        {(write.kind === "refused" || write.kind === "unresolved") && (
          <>
            <p ref={outcomeRef} tabIndex={-1} role="status" className="scenario-card__gaps" data-write-outcome={write.kind}>
              {write.message}
            </p>
            <div className="commercial-terms__actions">{readAgainButton}</div>
          </>
        )}
      </>
    );
  }

  return (
    <section className="commercial-terms" aria-labelledby={headingId}>
      <h4 id={headingId} className="commercial-terms__title">
        {COMMERCIAL_TERMS_HEADING}
      </h4>
      {body}
    </section>
  );
}

/** The revenue, or the named state that withholds it — never `0`, never blank (K-01, K-02). For
 * Outcome-based the amount is the guaranteed revenue and is labelled so (SC-4-07, K-02). */
function RevenueLine({ revenue }: { revenue: RevenueRead }) {
  const model = revenueModelKind(revenue);
  if (revenue.state === "calculated") {
    // `isRevenueShape` refuses a calculated revenue without a currency or with the `"n/a"` sentinel.
    // The amount stays a decimal string all the way into `formatMoneyString` (ADR-0002): no
    // `Number()`, and no substitution by the project's reporting currency.
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
 * The expected revenue and the four category revenues of a calculated Outcome-based revenue
 * (F-06.3; SC-4-07, K-02, K-04). Every amount here is the revenue's own and is formatted in the
 * revenue's own currency — the same object's `currency`, never the rule's or the project's.
 * `isRevenueShape` guarantees `expected_amount` is a decimal exactly when `expected_state` is
 * `"calculated"`, and that the four categories are there, each once.
 */
function OutcomeRevenue({ revenue }: { revenue: CalculatedRevenueRead }) {
  return (
    <>
      {revenue.expected_state === "calculated" ? (
        <p className="scenario-card__metric" data-expected-state="calculated">
          {EXPECTED_REVENUE_LABEL} {formatMoneyString(revenue.expected_amount, revenue.currency)}
        </p>
      ) : (
        <p className="scenario-card__gaps" data-expected-state={revenue.expected_state}>
          {EXPECTED_NO_PROBABILITIES}
        </p>
      )}
      <p className="scenario-card__metric">{OUTCOME_CATEGORIES_LABEL}</p>
      <ul className="commercial-terms__list">
        {revenue.category_revenues.map((category) => (
          <li key={category.category} className="scenario-card__metric" data-category={category.category}>
            {categoryLine(category, revenue.currency)}
          </li>
        ))}
      </ul>
    </>
  );
}

/** One category, labelled by its `category` word. Units are the server's string as it came;
 * `null` is "none", never `0` (K-04). */
function categoryLine(category: CategoryRevenueRead, currency: string): string {
  const units = category.units ?? NOT_GIVEN;
  const probability =
    category.probability === null ? NOT_GIVEN : formatStoredPercentString(category.probability);
  return (
    `${OUTCOME_CATEGORY_LABELS[category.category]} — ${CATEGORY_UNITS_LABEL} ${units}; ` +
    `${CATEGORY_PROBABILITY_LABEL} ${probability}; ` +
    `${CATEGORY_REVENUE_LABEL} ${formatMoneyString(category.amount, currency)}`
  );
}

/**
 * The Outcome-based rule's parameters as stored (SC-4-07, K-05; ADR-0003, addendum SC-4-07, point 9):
 * amounts with the four places of their column, in the rule's own currency — the same object's
 * `currency`. An absent component (`null`) is "none"; an explicit `"0.0000"` is the zero it is.
 */
function OutcomeRuleParameters({ parameters }: { parameters: OutcomeTermsRead }) {
  const amount = (value: string | null) =>
    value === null ? NOT_GIVEN : formatRuleAmountString(value, parameters.currency);
  return (
    <>
      <p className="scenario-card__metric">{RULE_PARAMETERS_LABEL}</p>
      <ul className="commercial-terms__list">
        <li className="scenario-card__metric">
          {RULE_CURRENCY_LABEL} {parameters.currency}
        </li>
        <li className="scenario-card__metric">
          {FIXED_FEE_LABEL} {amount(parameters.fixed_fee)}
        </li>
        <li className="scenario-card__metric">
          {SUCCESS_BONUS_LABEL} {amount(parameters.success_bonus)}
        </li>
        <li className="scenario-card__metric">
          {UNIT_RATE_LABEL} {amount(parameters.unit_rate)}
        </li>
        <li className="scenario-card__metric">
          {REVENUE_MIN_LABEL} {amount(parameters.revenue_min)}
        </li>
        <li className="scenario-card__metric">
          {REVENUE_MAX_LABEL} {amount(parameters.revenue_max)}
        </li>
      </ul>
      <p className="scenario-card__metric">{RULE_CATEGORIES_LABEL}</p>
      <ul className="commercial-terms__list">
        {OUTCOME_CATEGORIES.map((category) => (
          <li key={category} className="scenario-card__metric" data-rule-category={category}>
            {ruleCategoryLine(category, parameters.categories[category])}
          </li>
        ))}
      </ul>
    </>
  );
}

/** One of the rule's categories as stored (verification R-04), looked up by its word in
 * `categories`, never by position. Units are the server's string as it came; the probability goes
 * through `formatStoredPercentString`; `null` is "none", an explicit zero is that zero. */
function ruleCategoryLine(category: OutcomeCategory, stored: OutcomeCategoryRead): string {
  const units = stored.units ?? NOT_GIVEN;
  const probability = stored.probability === null ? NOT_GIVEN : formatStoredPercentString(stored.probability);
  return (
    `${OUTCOME_CATEGORY_LABELS[category]} — ${RULE_CATEGORY_UNITS_LABEL} ${units}; ` +
    `${RULE_CATEGORY_PROBABILITY_LABEL} ${probability}`
  );
}

/**
 * The parts of `assumptions_used` a person can read (gate 1, D-1 = option B): where the rates came
 * from, which selling-rate windows were used, and which months had no rate. A month is shown, never
 * the position behind it — naming a position needs `STAFFING_READ`, outside this task.
 */
function Assumptions({ revenue }: { revenue: RevenueRead }) {
  // F06 (verification round 2): for an unsupported model the sources are chosen from the scenario's
  // status, not read by any rate (ADR-0003, addendum SC-7-03, point 1) — nothing to show, exactly as
  // for a scenario without a rule.
  if (revenue.state === "unsupported_model_type") return null;
  const assumptions = catalogAssumptionsOf(revenue);
  // Chosen by `model_type` (ADR-0003, addendum SC-4-07, point 3): a model priced from its own rule
  // reads no hour, no window and no month, so none of the Time & Material lines below is shown for it.
  // An unsupported model is `"catalog"` whatever its word (verification R-01): its sources are the
  // dispatcher's catalogue ones, never the rule's own.
  if (assumptions === null) {
    const model = revenueModelKind(revenue);
    return model === "catalog" ? null : (
      <p className="scenario-card__metric">{RULE_SOURCE_LABELS[model]}</p>
    );
  }
  // Several positions can miss the same month; the month is what is shown, so it is shown once.
  const months = [
    ...new Set(assumptions.unresolved_months.map((month) => formatCalendarMonth(month.period_month))),
  ];
  return (
    <>
      <p className="scenario-card__metric">{RATE_SOURCE_LABELS[assumptions.rate_source]}</p>
      {assumptions.rate_windows.length === 0 ? (
        <p className="scenario-card__metric">{NO_RATE_WINDOWS}</p>
      ) : (
        <>
          <p className="scenario-card__metric">{RATE_WINDOWS_LABEL}</p>
          <ul className="commercial-terms__list">
            {assumptions.rate_windows.map((window, index) => (
              <li key={`${window.source_rate_id}:${index}`} className="scenario-card__metric">
                {formatEffectivePeriod(window.effective_from, window.effective_to)}:{" "}
                {formatMoneyString(window.default_selling_rate, window.currency)}
              </li>
            ))}
          </ul>
        </>
      )}
      {months.length > 0 && (
        <p className="scenario-card__gaps">
          {UNRESOLVED_MONTHS_LABEL} {months.join(", ")}
        </p>
      )}
    </>
  );
}
