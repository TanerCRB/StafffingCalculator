import { type FormEvent, useEffect, useState } from "react";

import {
  ApiError,
  editScenarioAssumptions,
  getScenarioAssumptionResetPreview,
  getScenarioAssumptions,
  getOrganizationDefaults,
  editOrganizationDefaults,
} from "../../api/client";
import type {
  AssumptionSource,
  OrganizationDefaults,
  ResolvedAssumption,
  ScenarioAssumptionResetPreview,
  ScenarioAssumptions,
} from "../../api/contracts/scenarioAssumptions";
import { formatPercentString, NOT_APPLICABLE } from "../../lib/money";
import {
  APPROVED_NOTE,
  ASSUMPTIONS_DENIED,
  ASSUMPTIONS_FAILED,
  ASSUMPTIONS_HEADING,
  ASSUMPTIONS_LOADING,
  ORG_DEFAULTS_DENIED,
  ORG_DEFAULTS_FAILED,
  ORG_DEFAULTS_HEADING,
  ORG_DEFAULTS_LOADING,
  ORG_DEFAULTS_RELOAD,
  ORG_DEFAULTS_RELOADING,
  ORG_DEFAULTS_RELOAD_FAILED,
  ORG_DEFAULTS_RECOVERY_NOTE,
  OVERLOAD_THRESHOLD,
  RESET,
  RESET_CANCEL,
  RESET_CONFIRM,
  RESET_FRESHNESS_NOTE,
  RESET_PREVIEW,
  RESET_PREVIEW_FAILED,
  RESET_PREVIEW_RETRY,
  SAVED,
  SAVE,
  SAVING,
  SOURCE_LABELS,
  TARGET_MARGIN,
  WRITE_CONFLICT,
  WRITE_DENIED,
  WRITE_REFUSED,
  WRITE_UNRESOLVED,
  WRITE_VALIDATION,
} from "./scenarioAssumptionsText";
import "./ScenarioAssumptionsSection.css";

type FieldName = "target_margin_percent" | "overload_threshold_percent";
const FIELDS: readonly { readonly key: FieldName; readonly label: string }[] = [
  { key: "target_margin_percent", label: TARGET_MARGIN },
  { key: "overload_threshold_percent", label: OVERLOAD_THRESHOLD },
];

type ReadState = { kind: "loading" } | { kind: "denied" } | { kind: "failed" } | { kind: "ready"; value: ScenarioAssumptions };
type PreviewState = { kind: "idle" | "loading" | "failed" } | { kind: "ready"; value: ScenarioAssumptionResetPreview; field: FieldName };
type WriteState = { kind: "idle" | "saving" | "saved" } | { kind: "error"; message: string };

function sourceLabel(source: AssumptionSource | null): string {
  return source === null ? SOURCE_LABELS.none : SOURCE_LABELS[source];
}

function resolvedValue(assumption: ResolvedAssumption): string {
  return assumption.value === "n/a" ? NOT_APPLICABLE : formatPercentString(assumption.value);
}

function readFailure(error: unknown): ReadState {
  return error instanceof ApiError && (error.status === 401 || error.status === 403)
    ? { kind: "denied" }
    : { kind: "failed" };
}

function writeFailure(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return WRITE_DENIED;
    if (error.status === 409) return WRITE_CONFLICT;
    if (error.status === 422) return WRITE_VALIDATION;
    if (error.status >= 400 && error.status < 500) return WRITE_REFUSED;
  }
  return WRITE_UNRESOLVED;
}

export function ScenarioAssumptionsSection({
  projectId,
  scenarioId,
  scenarioStatus,
}: {
  readonly projectId: string;
  readonly scenarioId: string;
  readonly scenarioStatus: "Draft" | "Approved";
}) {
  const [read, setRead] = useState<ReadState>({ kind: "loading" });
  const [reload, setReload] = useState(0);
  const [values, setValues] = useState<Record<FieldName, string>>({ target_margin_percent: "", overload_threshold_percent: "" });
  const [write, setWrite] = useState<WriteState>({ kind: "idle" });
  const [busyField, setBusyField] = useState<FieldName | null>(null);
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });

  useEffect(() => {
    const controller = new AbortController();
    let left = false;
    setRead({ kind: "loading" });
    setPreview({ kind: "idle" });
    getScenarioAssumptions(projectId, scenarioId, controller.signal)
      .then((value) => {
        if (left) return;
        setRead({ kind: "ready", value });
        setValues({
          target_margin_percent: value.target_margin_percent.source === "scenario" && value.target_margin_percent.value !== "n/a" ? value.target_margin_percent.value : "",
          overload_threshold_percent: value.overload_threshold_percent.source === "scenario" && value.overload_threshold_percent.value !== "n/a" ? value.overload_threshold_percent.value : "",
        });
      })
      .catch((error: unknown) => { if (!left) setRead(readFailure(error)); });
    return () => { left = true; controller.abort(); };
  }, [projectId, scenarioId, scenarioStatus, reload]);

  async function save(event: FormEvent<HTMLFormElement>, field: FieldName) {
    event.preventDefault();
    if (read.kind !== "ready" || read.value.status !== "Draft" || busyField !== null) return;
    setBusyField(field);
    setWrite({ kind: "saving" });
    setPreview({ kind: "idle" });
    try {
      const saved = await editScenarioAssumptions(projectId, scenarioId, {
        updated_at: read.value.updated_at,
        [field]: values[field],
      });
      const savedValue = saved[field];
      if (savedValue !== null) {
        setRead((current) => current.kind === "ready" ? {
          kind: "ready",
          value: {
            ...current.value,
            updated_at: saved.updated_at,
            [field]: { value: savedValue, state: "resolved", source: "scenario" },
          },
        } : current);
        setValues((current) => ({ ...current, [field]: savedValue }));
      } else {
        setReload((current) => current + 1);
      }
      setWrite({ kind: "saved" });
    } catch (error) {
      setWrite({ kind: "error", message: writeFailure(error) });
    } finally {
      setBusyField(null);
    }
  }

  async function loadResetPreview(field: FieldName) {
    setPreview({ kind: "loading" });
    try {
      const value = await getScenarioAssumptionResetPreview(projectId, scenarioId);
      setPreview({ kind: "ready", value, field });
    } catch {
      setPreview({ kind: "failed" });
    }
  }

  async function confirmReset(field: FieldName) {
    if (read.kind !== "ready" || read.value.status !== "Draft" || preview.kind !== "ready" || preview.field !== field || busyField !== null) return;
    setBusyField(field);
    setWrite({ kind: "saving" });
    try {
      await editScenarioAssumptions(projectId, scenarioId, {
        updated_at: read.value.updated_at,
        [field]: null,
      });
      setValues((current) => ({ ...current, [field]: "" }));
      setWrite({ kind: "saved" });
      setPreview({ kind: "idle" });
      setRead({ kind: "loading" });
      setReload((current) => current + 1);
    } catch (error) {
      setWrite({ kind: "error", message: writeFailure(error) });
    } finally {
      setBusyField(null);
    }
  }

  if (read.kind === "loading") return <section className="scenario-assumptions"><h4>{ASSUMPTIONS_HEADING}</h4><p>{ASSUMPTIONS_LOADING}</p></section>;
  if (read.kind === "denied") return <section className="scenario-assumptions"><h4>{ASSUMPTIONS_HEADING}</h4><p role="alert">{ASSUMPTIONS_DENIED}</p></section>;
  if (read.kind === "failed") return <section className="scenario-assumptions"><h4>{ASSUMPTIONS_HEADING}</h4><p role="alert">{ASSUMPTIONS_FAILED}</p><button type="button" onClick={() => setReload((n) => n + 1)}>Reload assumptions</button></section>;

  return (
    <section className="scenario-assumptions" aria-label={`${ASSUMPTIONS_HEADING} for ${scenarioId}`}>
      <h4>{ASSUMPTIONS_HEADING}</h4>
      {read.value.status === "Approved" && <p>{APPROVED_NOTE}</p>}
      {FIELDS.map(({ key, label }) => {
        const resolved = read.value[key];
        const isDraft = scenarioStatus === "Draft" && read.value.status === "Draft";
        const thisPreview = preview.kind === "ready" && preview.field === key;
        return (
          <form className="scenario-assumptions__field" key={key} onSubmit={(event) => void save(event, key)}>
            <div className="scenario-assumptions__resolved">
              <strong>{label}: {resolvedValue(resolved)}</strong>
              <span>{sourceLabel(resolved.source)}</span>
            </div>
            {isDraft && <>
              <label>
                {label} override (%)
                <input aria-label={`${label} override`} inputMode="decimal" value={values[key]} onChange={(event) => { setValues((current) => ({ ...current, [key]: event.target.value })); setWrite({ kind: "idle" }); }} />
              </label>
              <div className="scenario-assumptions__actions">
                <button type="submit" disabled={busyField !== null || values[key] === "" || (resolved.source === "scenario" && values[key] === resolved.value)}>{busyField === key ? SAVING : SAVE}</button>
                {resolved.source === "scenario" && <button type="button" disabled={busyField !== null || preview.kind === "loading"} onClick={() => void loadResetPreview(key)}>{preview.kind === "loading" ? RESET_PREVIEW : RESET}</button>}
              </div>
              {preview.kind === "failed" && resolved.source === "scenario" && <p role="alert">{RESET_PREVIEW_FAILED} <button type="button" onClick={() => void loadResetPreview(key)}>{RESET_PREVIEW_RETRY}</button></p>}
              {thisPreview && <div className="scenario-assumptions__preview" role="group" aria-label="Reset preview">
                <p>{RESET_PREVIEW}</p>
                {FIELDS.map((item) => <p key={item.key}><strong>{item.label}:</strong> {resolvedValue(preview.value[item.key])} — {sourceLabel(preview.value[item.key].source)}</p>)}
                <p>{RESET_FRESHNESS_NOTE}</p>
                <button type="button" disabled={busyField !== null} onClick={() => void confirmReset(key)}>{RESET_CONFIRM}</button>
                <button type="button" onClick={() => setPreview({ kind: "idle" })}>{RESET_CANCEL}</button>
              </div>}
            </>}
          </form>
        );
      })}
      {write.kind === "saved" && <p role="status">{SAVED}</p>}
      {write.kind === "error" && <p role="alert">{write.message}</p>}
      <button type="button" onClick={() => { setWrite({ kind: "idle" }); setRead({ kind: "loading" }); setReload((n) => n + 1); }}>Reload assumptions</button>
    </section>
  );
}

type DefaultsState = { kind: "loading" } | { kind: "denied" } | { kind: "failed" } | { kind: "ready"; value: OrganizationDefaults };
type DefaultsRecoveryState = { kind: "idle" | "loading" | "failed" } | { kind: "ready"; value: OrganizationDefaults };

/** Organization defaults are global values; their endpoint, not project access, authorizes writes. */
export function OrganizationDefaultsSection() {
  const [read, setRead] = useState<DefaultsState>({ kind: "loading" });
  const [values, setValues] = useState<Record<FieldName, string>>({ target_margin_percent: "", overload_threshold_percent: "" });
  const [write, setWrite] = useState<WriteState>({ kind: "idle" });
  const [busy, setBusy] = useState<FieldName | null>(null);
  const [reload, setReload] = useState(0);
  const [recovery, setRecovery] = useState<DefaultsRecoveryState>({ kind: "idle" });

  useEffect(() => {
    const controller = new AbortController();
    let left = false;
    setRead({ kind: "loading" });
    getOrganizationDefaults(controller.signal).then((value) => {
      if (left) return;
      setRead({ kind: "ready", value });
      setValues({ target_margin_percent: value.target_margin_percent ?? "", overload_threshold_percent: value.overload_threshold_percent ?? "" });
    }).catch((error: unknown) => {
      if (left) return;
      setRead(error instanceof ApiError && (error.status === 401 || error.status === 403) ? { kind: "denied" } : { kind: "failed" });
    });
    return () => { left = true; controller.abort(); };
  }, [reload]);

  async function reloadLatest() {
    setRecovery({ kind: "loading" });
    try {
      const latest = await getOrganizationDefaults();
      setRead({ kind: "ready", value: latest });
      // Keep the typed values visible so the caller can compare and choose whether to reapply them.
      setRecovery({ kind: "ready", value: latest });
    } catch {
      setRecovery({ kind: "failed" });
    }
  }

  async function save(event: FormEvent<HTMLFormElement>, field: FieldName) {
    event.preventDefault();
    if (read.kind !== "ready" || busy !== null) return;
    setBusy(field);
    setWrite({ kind: "saving" });
    try {
      const saved = await editOrganizationDefaults({ updated_at: read.value.updated_at, [field]: values[field] });
      setRead({ kind: "ready", value: saved });
      setValues({ target_margin_percent: saved.target_margin_percent ?? "", overload_threshold_percent: saved.overload_threshold_percent ?? "" });
      setWrite({ kind: "saved" });
    } catch (error) {
      setWrite({ kind: "error", message: writeFailure(error) });
    } finally { setBusy(null); }
  }

  return <section className="organization-defaults" aria-label={ORG_DEFAULTS_HEADING}>
    <h4>{ORG_DEFAULTS_HEADING}</h4>
    {read.kind === "loading" && <p>{ORG_DEFAULTS_LOADING}</p>}
    {read.kind === "denied" && <p role="alert">{ORG_DEFAULTS_DENIED}</p>}
    {read.kind === "failed" && <><p role="alert">{ORG_DEFAULTS_FAILED}</p><button type="button" onClick={() => setReload((n) => n + 1)}>Reload defaults</button></>}
    {read.kind === "ready" && FIELDS.map(({ key, label }) => <form key={key} onSubmit={(event) => void save(event, key)} className="scenario-assumptions__field">
      <label>{label} (%)<input aria-label={`Organization ${label}`} inputMode="decimal" value={values[key]} onChange={(event) => { setValues((current) => ({ ...current, [key]: event.target.value })); setWrite({ kind: "idle" }); }} /></label>
      <button type="submit" disabled={busy !== null || (write.kind === "error" && write.message === WRITE_DENIED) || values[key] === "" || values[key] === (read.value[key] ?? "")}>{busy === key ? SAVING : SAVE}</button>
    </form>)}
    {write.kind === "saved" && <p role="status">{SAVED}</p>}
    {write.kind === "error" && <p role="alert">{write.message}</p>}
    {write.kind === "error" && (write.message === WRITE_CONFLICT || write.message === WRITE_UNRESOLVED) && <button type="button" disabled={recovery.kind === "loading"} onClick={() => void reloadLatest()}>{recovery.kind === "loading" ? ORG_DEFAULTS_RELOADING : ORG_DEFAULTS_RELOAD}</button>}
    {recovery.kind === "failed" && <p role="alert">{ORG_DEFAULTS_RELOAD_FAILED}</p>}
    {recovery.kind === "ready" && <div className="scenario-assumptions__preview" role="group" aria-label="Latest organization defaults">
      <p>{ORG_DEFAULTS_RECOVERY_NOTE}</p>
      {FIELDS.map(({ key, label }) => <p key={key}><strong>{label}:</strong> {recovery.value[key] === null ? NOT_APPLICABLE : formatPercentString(recovery.value[key]!)}</p>)}
    </div>}
  </section>;
}
