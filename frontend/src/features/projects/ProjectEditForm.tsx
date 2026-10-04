import { useEffect, useState, type FormEvent } from "react";

import { ApiError, getProject, editProject } from "../../api/client";
import type { ProjectDetail, ProjectEditRequest } from "../../api/contracts/projects";

interface ProjectEditFormProps {
  readonly projectId: string;
  readonly projectName: string;
  readonly onSaved: (project: ProjectDetail) => void;
  readonly onCancel: () => void;
}

interface ProjectDraft {
  name: string;
  client: string;
  owner: string;
  description: string;
  reportingCurrency: string;
  deliveryStart: string;
  deliveryEnd: string;
}

function draftFrom(project: ProjectDetail): ProjectDraft {
  return {
    name: project.name,
    client: project.client,
    owner: project.owner,
    description: project.description,
    reportingCurrency: project.reporting_currency,
    deliveryStart: project.delivery_period.start,
    deliveryEnd: project.delivery_period.end,
  };
}

export function ProjectEditForm({ projectId, projectName, onSaved, onCancel }: ProjectEditFormProps) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [draft, setDraft] = useState<ProjectDraft | null>(null);
  const [loadingError, setLoadingError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [isSaved, setIsSaved] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let left = false;
    getProject(projectId, controller.signal)
      .then((loaded) => {
        if (!left) {
          setProject(loaded);
          setDraft(draftFrom(loaded));
        }
      })
      .catch(() => {
        if (!left) {
          setLoadingError(true);
        }
      });
    return () => {
      left = true;
      controller.abort();
    };
  }, [projectId]);

  if (loadingError) {
    return (
      <div>
        <h3 className="project-edit__title">Edit {projectName}</h3>
        <p role="alert">Project details could not be loaded.</p>
        <button type="button" className="button button--quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    );
  }

  if (project === null || draft === null) {
    return <p role="status">Loading project details…</p>;
  }

  const fieldsFrozen = project.scenarios.some((scenario) => scenario.status === "Approved");
  const dirty =
    draft.name !== project.name ||
    draft.client !== project.client ||
    draft.owner !== project.owner ||
    draft.description !== project.description ||
    draft.reportingCurrency !== project.reporting_currency ||
    draft.deliveryStart !== project.delivery_period.start ||
    draft.deliveryEnd !== project.delivery_period.end;

  function update<K extends keyof ProjectDraft>(field: K, value: ProjectDraft[K]) {
    setDraft((current) => (current === null ? current : { ...current, [field]: value }));
    setSaveMessage(null);
    setIsSaved(false);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dirty || saving || project === null || draft === null) {
      return;
    }

    const currentProject = project;
    const currentDraft = draft;
    const body: ProjectEditRequest = { updated_at: currentProject.updated_at };
    if (currentDraft.name !== currentProject.name) body.name = currentDraft.name;
    if (currentDraft.client !== currentProject.client) body.client = currentDraft.client;
    if (currentDraft.owner !== currentProject.owner) body.owner = currentDraft.owner;
    if (currentDraft.description !== currentProject.description) body.description = currentDraft.description;
    if (currentDraft.reportingCurrency !== currentProject.reporting_currency) {
      body.reporting_currency = currentDraft.reportingCurrency;
    }
    if (
      (currentDraft.deliveryStart !== currentProject.delivery_period.start ||
        currentDraft.deliveryEnd !== currentProject.delivery_period.end)
    ) {
      body.delivery_period = {
        start: currentDraft.deliveryStart,
        end: currentDraft.deliveryEnd,
      };
    }

    setSaving(true);
    setSaveMessage(null);
    setIsSaved(false);
    try {
      const updated = await editProject(projectId, body);
      setProject(updated);
      setDraft(draftFrom(updated));
      setIsSaved(true);
      onSaved(updated);
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 409) {
        const detail = error.detail?.toLowerCase() ?? "";
        setSaveMessage(
          detail.includes("approved scenario")
            ? "Reporting currency and delivery period cannot be edited while this project has an approved scenario. Your entered values are still here."
            : "This project changed while you were editing. Your changes are still here; review them before retrying.",
        );
      } else {
        setSaveMessage("Project changes were refused and have not been saved.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="project-edit" onSubmit={save} aria-label={`Edit ${project.name}`}>
      <h3 className="project-edit__title">Edit project details</h3>
      {fieldsFrozen && (
        <p className="project-edit__notice">
          Reporting currency and delivery period may be frozen while an approved scenario exists. If a save is refused, your entered values remain here.
        </p>
      )}
      <label className="project-edit__field">
        <span>Project name</span>
        <input className="input" name="name" required value={draft.name} onChange={(event) => update("name", event.target.value)} />
      </label>
      <label className="project-edit__field">
        <span>Client</span>
        <input className="input" name="client" required value={draft.client} onChange={(event) => update("client", event.target.value)} />
      </label>
      <label className="project-edit__field">
        <span>Owner</span>
        <input className="input" name="owner" required value={draft.owner} onChange={(event) => update("owner", event.target.value)} />
      </label>
      <label className="project-edit__field">
        <span>Description</span>
        <textarea className="input project-edit__textarea" name="description" value={draft.description} onChange={(event) => update("description", event.target.value)} />
      </label>
      <label className="project-edit__field">
        <span>Reporting currency</span>
        <input className="input" name="reporting_currency" required maxLength={3} value={draft.reportingCurrency} onChange={(event) => update("reportingCurrency", event.target.value.toUpperCase())} />
      </label>
      <div className="project-edit__period">
        <label className="project-edit__field">
          <span>Delivery period start</span>
          <input className="input" type="date" name="delivery_period_start" required value={draft.deliveryStart} onChange={(event) => update("deliveryStart", event.target.value)} />
        </label>
        <label className="project-edit__field">
          <span>Delivery period end</span>
          <input className="input" type="date" name="delivery_period_end" required value={draft.deliveryEnd} onChange={(event) => update("deliveryEnd", event.target.value)} />
        </label>
      </div>
      {saveMessage !== null && <p role="alert" className="project-edit__error">{saveMessage}</p>}
      {isSaved && <p role="status">Project updated.</p>}
      <div className="project-edit__actions">
        <button type="submit" className="button button--primary" disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save changes"}
        </button>
        <button type="button" className="button button--quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
