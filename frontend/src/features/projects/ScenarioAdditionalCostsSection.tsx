import { type FormEvent, useEffect, useRef, useState } from "react";

import {
  ApiError,
  createScenarioAdditionalCost,
  deleteScenarioAdditionalCost,
  editScenarioAdditionalCost,
  getCatalogDimension,
  getScenarioAdditionalCosts,
} from "../../api/client";
import type {
  AdditionalCostCreateRequest,
  AdditionalCostEditRequest,
  AdditionalCostRead,
  AdditionalCostType,
  AdditionalCostFundingSource,
} from "../../api/contracts/additionalCosts";
import type { ScenarioStatus } from "../../api/contracts/projects";
import type { DimensionEntry } from "../../api/contracts/catalog";
import { formatMoneyString } from "../../lib/money";
import {
  clearPendingAdditionalCostCreate,
  keyForAdditionalCostCreate,
} from "./additionalCostCreateOperation";
import {
  ADDITIONAL_COSTS_HEADING,
  APPROVED_ADDITIONAL_COSTS_NOTE,
  ADD_COST,
  AMOUNT,
  CANCEL,
  CATEGORIES_LOADING,
  CATEGORIES_UNAVAILABLE,
  CATEGORY,
  CATEGORY_REQUIRED,
  COST_TYPE_LABELS,
  CURRENCY,
  EDIT_COST,
  END_MONTH,
  FUNDING_SOURCE,
  FUNDING_SOURCE_LABELS,
  NO_COSTS,
  PERIOD,
  READ_AGAIN,
  READ_DENIED,
  READ_FAILED,
  READ_LOADING,
  READ_NOT_FOUND,
  RECURRING,
  REMOVE_COST,
  REMOVED,
  SAVED,
  SAVE,
  SELECT_CATEGORY,
  SAVING,
  START_MONTH,
  TYPE,
  WRITE_DENIED,
  WRITE_REFUSED,
  WRITE_UNRESOLVED,
  WRITING,
  ONE_OFF,
} from "./additionalCostsText";
import "./ScenarioAdditionalCostsSection.css";

type ReadState =
  | { kind: "loading" }
  | { kind: "ready"; costs: AdditionalCostRead[]; scenarioStatus: ScenarioStatus }
  | { kind: "denied" | "not-found" | "failed" };

type CategoriesState =
  | { kind: "idle" | "loading" | "denied" | "failed" }
  | { kind: "ready"; entries: DimensionEntry[] };

type WriteState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "removed" }
  | { kind: "refused"; message: string }
  | { kind: "unresolved"; message: string };

interface CostForm {
  readonly mode: "create" | "edit";
  readonly cost?: AdditionalCostRead;
  readonly categoryId: string;
  readonly amount: string;
  readonly currency: string;
  readonly costType: AdditionalCostType;
  readonly startMonth: string;
  readonly endMonth: string;
  readonly fundingSource: AdditionalCostFundingSource;
}

interface PendingCreate {
  readonly signature: string;
  readonly key: string;
}

export interface ScenarioAdditionalCostsSectionProps {
  readonly projectId: string;
  readonly scenarioId: string;
  readonly scenarioName: string;
  readonly reportingCurrency: string;
  readonly scenarioStatus?: ScenarioStatus;
}

function readFailure(error: unknown): ReadState {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return { kind: "denied" };
  if (error instanceof ApiError && error.status === 404) return { kind: "not-found" };
  return { kind: "failed" };
}

function writeFailure(error: unknown): WriteState {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return {
      kind: "refused",
      message: error.status === 401 || error.status === 403 ? WRITE_DENIED : WRITE_REFUSED,
    };
  }
  return { kind: "unresolved", message: WRITE_UNRESOLVED };
}

function initialForm(
  mode: CostForm["mode"],
  reportingCurrency: string,
  cost?: AdditionalCostRead,
): CostForm {
  return {
    mode,
    ...(cost === undefined ? {} : { cost }),
    categoryId: cost?.category_id ?? "",
    amount: cost?.amount ?? "",
    currency: cost?.currency ?? reportingCurrency,
    costType: cost?.cost_type ?? "one_off",
    startMonth: cost?.start_month.slice(0, 7) ?? "",
    endMonth: cost?.end_month?.slice(0, 7) ?? "",
    fundingSource: cost?.funding_source ?? "internal",
  };
}

function monthStart(month: string): string {
  return `${month}-01`;
}

function costPeriod(cost: AdditionalCostRead): string {
  return cost.cost_type === "one_off"
    ? cost.start_month.slice(0, 7)
    : `${cost.start_month.slice(0, 7)} – ${cost.end_month?.slice(0, 7) ?? ""}`;
}

export function ScenarioAdditionalCostsSection({
  projectId,
  scenarioId,
  scenarioName,
  reportingCurrency,
  scenarioStatus = "Draft",
}: ScenarioAdditionalCostsSectionProps) {
  const [read, setRead] = useState<ReadState>({ kind: "loading" });
  const [readRequest, setReadRequest] = useState(0);
  const [categories, setCategories] = useState<CategoriesState>({ kind: "idle" });
  const [form, setForm] = useState<CostForm | null>(null);
  const [write, setWrite] = useState<WriteState>({ kind: "idle" });
  const [writing, setWriting] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const pendingCreate = useRef<PendingCreate | null>(null);
  const mounted = useRef(true);
  const categoriesController = useRef<AbortController | null>(null);
  const isApproved = scenarioStatus === "Approved" || (read.kind === "ready" && read.scenarioStatus === "Approved");

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      categoriesController.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (isApproved) {
      setForm(null);
      setConfirmDeleteId(null);
    }
  }, [isApproved]);

  useEffect(() => {
    const controller = new AbortController();
    let left = false;
    getScenarioAdditionalCosts(projectId, scenarioId, controller.signal)
      .then((result) => {
        if (!left) setRead({ kind: "ready", costs: result.costs, scenarioStatus: result.scenario_status });
      })
      .catch((error: unknown) => {
        if (!left) setRead(readFailure(error));
      });
    return () => {
      left = true;
      controller.abort();
    };
  }, [projectId, scenarioId, readRequest]);

  async function loadCategories() {
    if (categories.kind === "loading" || categories.kind === "ready") return;
    const controller = new AbortController();
    categoriesController.current = controller;
    setCategories({ kind: "loading" });
    try {
      const result = await getCatalogDimension("cost-categories", controller.signal);
      if (mounted.current) setCategories({ kind: "ready", entries: result.entries });
    } catch (error: unknown) {
      if (!mounted.current) return;
      setCategories({ kind: error instanceof ApiError && [401, 403].includes(error.status) ? "denied" : "failed" });
    }
  }

  function openCreate() {
    setWrite({ kind: "idle" });
    setForm(initialForm("create", reportingCurrency));
    void loadCategories();
  }

  function openEdit(cost: AdditionalCostRead) {
    setWrite({ kind: "idle" });
    setForm(initialForm("edit", reportingCurrency, cost));
    void loadCategories();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (form === null || writing) return;
    if (form.categoryId === "") {
      setWrite({ kind: "refused", message: CATEGORY_REQUIRED });
      return;
    }
    const shared = {
      category_id: form.categoryId,
      amount: form.amount,
      currency: form.currency.toUpperCase(),
      cost_type: form.costType,
      start_month: monthStart(form.startMonth),
      end_month: form.costType === "recurring" ? monthStart(form.endMonth) : null,
      funding_source: form.fundingSource,
    };
    setWriting(true);
    setWrite({ kind: "saving" });
    let createBody: AdditionalCostCreateRequest | null = null;
    try {
      let saved: AdditionalCostRead;
      if (form.mode === "create") {
        const body: AdditionalCostCreateRequest = shared;
        createBody = body;
        const signature = JSON.stringify(body);
        const key = pendingCreate.current?.signature === signature
          ? pendingCreate.current.key
          : await keyForAdditionalCostCreate(projectId, scenarioId, body);
        pendingCreate.current = { signature, key };
        saved = await createScenarioAdditionalCost(
          projectId,
          scenarioId,
          body,
          key,
        );
        pendingCreate.current = null;
        await clearPendingAdditionalCostCreate(projectId, scenarioId, body);
      } else {
        const body: AdditionalCostEditRequest = {
          updated_at: form.cost!.updated_at,
          ...shared,
        };
        saved = await editScenarioAdditionalCost(projectId, scenarioId, form.cost!.id, body);
      }
      if (!mounted.current) return;
      setRead((current) => {
        if (current.kind !== "ready") return current;
        const found = current.costs.some((cost) => cost.id === saved.id);
        return {
          ...current,
          costs: found
            ? current.costs.map((cost) => (cost.id === saved.id ? saved : cost))
            : [...current.costs, saved],
        };
      });
      setForm(null);
      setWrite({ kind: "saved" });
    } catch (error: unknown) {
      const outcome = writeFailure(error);
      if (outcome.kind === "refused" && form.mode === "create" && createBody !== null) {
        pendingCreate.current = null;
        await clearPendingAdditionalCostCreate(projectId, scenarioId, createBody);
      }
      if (mounted.current) setWrite(outcome);
    } finally {
      if (mounted.current) setWriting(false);
    }
  }

  async function remove(cost: AdditionalCostRead) {
    setDeletingId(cost.id);
    setWrite({ kind: "saving" });
    try {
      await deleteScenarioAdditionalCost(projectId, scenarioId, cost.id, {
        updated_at: cost.updated_at,
      });
      if (!mounted.current) return;
      setRead((current) =>
        current.kind === "ready"
          ? { ...current, costs: current.costs.filter((row) => row.id !== cost.id) }
          : current,
      );
      setWrite({ kind: "removed" });
      setConfirmDeleteId(null);
    } catch (error: unknown) {
      if (mounted.current) setWrite(writeFailure(error));
    } finally {
      if (mounted.current) setDeletingId(null);
    }
  }

  return (
    <section className="additional-costs" aria-label={`${ADDITIONAL_COSTS_HEADING} for ${scenarioName}`}>
      <div className="additional-costs__heading-row">
        <h4 className="additional-costs__title">{ADDITIONAL_COSTS_HEADING}</h4>
        {!isApproved && (
          <button type="button" className="button button--secondary" onClick={openCreate} disabled={read.kind !== "ready" || writing || deletingId !== null}>
            {ADD_COST}
          </button>
        )}
      </div>
      {isApproved && <p className="scenario-card__metric">{APPROVED_ADDITIONAL_COSTS_NOTE}</p>}

      {read.kind === "loading" && <p role="status">{READ_LOADING}</p>}
      {read.kind === "denied" && <p role="alert">{READ_DENIED}</p>}
      {read.kind === "not-found" && <p role="alert">{READ_NOT_FOUND}</p>}
      {read.kind === "failed" && (
        <div role="alert">
          <p>{READ_FAILED}</p>
          <button type="button" className="button button--quiet" onClick={() => setReadRequest((n) => n + 1)}>{READ_AGAIN}</button>
        </div>
      )}

      {read.kind === "ready" && (
        read.costs.length === 0 ? (
          <p className="additional-costs__empty">{NO_COSTS}</p>
        ) : (
          <ul className="additional-costs__list">
            {read.costs.map((cost) => (
              <li className="additional-costs__row" key={cost.id}>
                <div className="additional-costs__details">
                  <strong>{cost.category_name}</strong>
                  <span>{formatMoneyString(cost.amount, cost.currency)}</span>
                  <span>{PERIOD}: {costPeriod(cost)}</span>
                  <span>{COST_TYPE_LABELS[cost.cost_type]}</span>
                  <span>{FUNDING_SOURCE}: {FUNDING_SOURCE_LABELS[cost.funding_source]}</span>
                </div>
                {!isApproved && <div className="additional-costs__actions">
                  <button type="button" className="button button--quiet" onClick={() => openEdit(cost)} disabled={writing || deletingId !== null}>
                    {EDIT_COST}
                  </button>
                  {confirmDeleteId === cost.id ? (
                    <>
                      <button type="button" className="button button--secondary" onClick={() => void remove(cost)} disabled={deletingId !== null}>
                        {deletingId === cost.id ? SAVING : REMOVE_COST}
                      </button>
                      <button type="button" className="button button--quiet" onClick={() => setConfirmDeleteId(null)} disabled={deletingId !== null}>{CANCEL}</button>
                    </>
                  ) : (
                    <button type="button" className="button button--quiet" onClick={() => setConfirmDeleteId(cost.id)} disabled={writing || deletingId !== null}>
                      {REMOVE_COST}
                    </button>
                  )}
                </div>}
              </li>
            ))}
          </ul>
        )
      )}

      {form !== null && !isApproved && (
        <form className="additional-costs__form" onSubmit={(event) => void submit(event)}>
          <label>
            {CATEGORY}
            <select className="input" required disabled={categories.kind !== "ready"} value={form.categoryId} onChange={(event) => setForm((current) => current && ({ ...current, categoryId: event.target.value }))}>
              <option value="">{SELECT_CATEGORY}</option>
              {form.cost !== undefined &&
                (categories.kind !== "ready" || !categories.entries.some((entry) => entry.id === form.cost?.category_id)) && (
                <option value={form.cost.category_id}>{form.cost.category_name}</option>
              )}
              {categories.kind === "ready" && categories.entries.map((entry) => (
                <option key={entry.id} value={entry.id}>{entry.name}</option>
              ))}
            </select>
          </label>
          {categories.kind === "loading" && <p role="status">{CATEGORIES_LOADING}</p>}
          {(categories.kind === "denied" || categories.kind === "failed") && <p role="alert">{CATEGORIES_UNAVAILABLE}</p>}
          {categories.kind === "ready" && categories.entries.length === 0 && form.mode === "create" && <p role="alert">{CATEGORIES_UNAVAILABLE}</p>}
          <label>
            {AMOUNT}
            <input className="input" name="amount" inputMode="decimal" required value={form.amount} onChange={(event) => setForm((current) => current && ({ ...current, amount: event.target.value }))} />
          </label>
          <label>
            {CURRENCY}
            <input className="input" name="currency" maxLength={3} minLength={3} required value={form.currency} onChange={(event) => setForm((current) => current && ({ ...current, currency: event.target.value }))} />
          </label>
          <label>
            {TYPE}
            <select className="input" value={form.costType} onChange={(event) => setForm((current) => current && ({ ...current, costType: event.target.value as AdditionalCostType }))}>
              <option value="one_off">{ONE_OFF}</option>
              <option value="recurring">{RECURRING}</option>
            </select>
          </label>
          <label>
            {START_MONTH}
            <input className="input" type="month" required value={form.startMonth} onChange={(event) => setForm((current) => current && ({ ...current, startMonth: event.target.value }))} />
          </label>
          {form.costType === "recurring" && (
            <label>
              {END_MONTH}
              <input className="input" type="month" required value={form.endMonth} onChange={(event) => setForm((current) => current && ({ ...current, endMonth: event.target.value }))} />
            </label>
          )}
          <label>
            {FUNDING_SOURCE}
            <select className="input" value={form.fundingSource} onChange={(event) => setForm((current) => current && ({ ...current, fundingSource: event.target.value as AdditionalCostFundingSource }))}>
              <option value="internal">{FUNDING_SOURCE_LABELS.internal}</option>
              <option value="rebilled_to_client">{FUNDING_SOURCE_LABELS.rebilled_to_client}</option>
            </select>
          </label>
          <div className="additional-costs__form-actions">
            <button type="submit" className="button button--primary" disabled={writing || deletingId !== null || (form.mode === "create" && (categories.kind !== "ready" || categories.entries.length === 0))}>
              {writing ? SAVING : SAVE}
            </button>
            <button type="button" className="button button--quiet" onClick={() => setForm(null)} disabled={writing}>{CANCEL}</button>
          </div>
        </form>
      )}

      {write.kind === "saving" && <p role="status">{WRITING}</p>}
      {write.kind === "saved" && <p role="status">{SAVED}</p>}
      {write.kind === "removed" && <p role="status">{REMOVED}</p>}
      {(write.kind === "refused" || write.kind === "unresolved") && <p role="alert">{write.message}</p>}
    </section>
  );
}
