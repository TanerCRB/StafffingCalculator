import { useState, type FormEvent } from "react";
import { ApiError, createProject } from "../../api/client";
import type { ProjectCreateRequest, ProjectDetail } from "../../api/contracts/projects";
import {
  clearPendingProjectCreateKey,
  newProjectCreateKey,
  persistPendingProjectCreateKey,
} from "./projectCreateOperation";
import { PROJECT_CREATE_TEXT as text } from "./projectCreateText";
import "./ProjectCreateForm.css";

export function ProjectCreateForm({
  onCreated,
  onCancel,
  onSubmittingChange,
  pendingKey,
  onPendingKeyChange,
  outcomeUnknown,
  onOutcomeUnknown,
  onStartSeparateProject,
  recoveryStorage,
}: {
  readonly onCreated: (project: ProjectDetail) => void;
  readonly onCancel: () => void;
  readonly onSubmittingChange: (submitting: boolean) => void;
  readonly pendingKey: string | null;
  readonly onPendingKeyChange: (key: string | null) => void;
  readonly outcomeUnknown: boolean;
  readonly onOutcomeUnknown: (unknown: boolean) => void;
  readonly onStartSeparateProject: () => void;
  readonly recoveryStorage: Storage;
}) {
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [conflictNeedsDecision, setConflictNeedsDecision] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    const values = new FormData(event.currentTarget);
    const name = String(values.get("name") ?? "").trim();
    const client = String(values.get("client") ?? "").trim();
    const owner = String(values.get("owner") ?? "").trim();
    const start = String(values.get("start") ?? "");
    const end = String(values.get("end") ?? "");
    const currency = String(values.get("currency") ?? "").trim().toUpperCase();
    const description = String(values.get("description") ?? "");
    if (!name || !client || !owner || !start || !end || end < start || !/^[A-Z]{3}$/.test(currency)) {
      setError(text.invalid);
      return;
    }
    const body: ProjectCreateRequest = {
      name, client, owner, delivery_period: { start, end }, reporting_currency: currency, description,
    };

    let idempotencyKey: string;
    try {
      idempotencyKey = pendingKey ?? newProjectCreateKey();
      if (pendingKey === null) {
        persistPendingProjectCreateKey(idempotencyKey, recoveryStorage);
        onPendingKeyChange(idempotencyKey);
      }
    } catch {
      setError(text.storageUnavailable);
      return;
    }

    setError(null);
    setConflictNeedsDecision(false);
    setSubmitting(true);
    onSubmittingChange(true);
    onOutcomeUnknown(false);
    try {
      const created = await createProject(body, idempotencyKey);
      clearPendingProjectCreateKey(recoveryStorage);
      onPendingKeyChange(null);
      onCreated(created);
    } catch (cause) {
      // Keep the same key for every non-success response. A lost response, an HTTP refusal, or a
      // key/body conflict does not prove that the original operation was not committed.
      onOutcomeUnknown(true);
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) {
        onOutcomeUnknown(false);
        setError(text.forbidden);
      } else if (cause instanceof ApiError && cause.status === 409 &&
        cause.detail?.startsWith("Idempotency key was already used")) {
        onOutcomeUnknown(true);
        setConflictNeedsDecision(true);
        setError(text.conflict);
      } else if (cause instanceof ApiError && cause.status < 500) {
        onOutcomeUnknown(false);
        setError(text.refused(cause.status));
      } else {
        onOutcomeUnknown(true);
        setError(text.timeout);
      }
    } finally {
      setSubmitting(false);
      onSubmittingChange(false);
    }
  }

  return (
    <form id="project-create-form" className="project-create-form" onSubmit={submit} noValidate>
      <h3>{text.heading}</h3>
      {outcomeUnknown && <p className="project-create-form__error" role="status">{text.timeoutRecovery}</p>}
      {error !== null && <p className="project-create-form__error" role="alert">{error}</p>}
      <div className="project-create-form__fields">
        <label>{text.name}<input className="input" name="name" required /></label>
        <label>{text.client}<input className="input" name="client" required /></label>
        <label>{text.owner}<input className="input" name="owner" required /></label>
        <label>{text.start}<input className="input" type="date" name="start" required /></label>
        <label>{text.end}<input className="input" type="date" name="end" required /></label>
        <label>{text.currency}<input className="input" name="currency" maxLength={3} required
          onChange={(event) => { event.currentTarget.value = event.currentTarget.value.toUpperCase(); }} /></label>
        <label className="project-create-form__description">{text.description}
          <textarea className="input" name="description" rows={3} />
        </label>
      </div>
      {conflictNeedsDecision && pendingKey !== null && (
        <div className="project-create-form__recovery">
          <p>{text.conflictDecision}</p>
          <button className="button button--secondary" type="button" disabled={submitting}
            onClick={() => {
              onStartSeparateProject();
              setConflictNeedsDecision(false);
              setError(null);
              onOutcomeUnknown(false);
            }}>{text.startSeparate}</button>
        </div>
      )}
      <div className="project-create-form__actions">
        <button className="button button--primary" type="submit" disabled={submitting}>
          {outcomeUnknown || pendingKey !== null ? text.retry : text.create}
        </button>
        <button className="button button--secondary" type="button" onClick={onCancel} disabled={submitting}>{text.cancel}</button>
      </div>
    </form>
  );
}
