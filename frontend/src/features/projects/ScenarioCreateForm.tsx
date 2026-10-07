import { useEffect, useRef, useState, type FormEvent } from "react";

import { createScenario } from "../../api/client";
import type { ScenarioListItem } from "../../api/contracts/projects";
import { describeScenarioCreateFailure, SCENARIO_CREATE_MESSAGES } from "./scenarioCreateText";

type WriteState = "idle" | "saving" | "created" | "refused";

interface ScenarioCreateFormProps {
  readonly projectId: string;
  readonly projectName: string;
  readonly onCreated: (projectId: string, scenario: ScenarioListItem) => void;
  readonly onRefresh: () => void;
}

export function ScenarioCreateForm({ projectId, projectName, onCreated, onRefresh }: ScenarioCreateFormProps) {
  const [name, setName] = useState("");
  const [writeState, setWriteState] = useState<WriteState>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const submitting = useRef(false);
  const mounted = useRef(true);
  const outcomeRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (writeState === "created" || writeState === "refused") outcomeRef.current?.focus();
  }, [writeState]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    submitting.current = true;
    setWriteState("saving");
    setMessage(null);
    try {
      // Do not trim or normalize: the accepted contract treats exact stored names as distinct.
      const scenario = await createScenario(projectId, { name });
      onCreated(projectId, scenario);
      if (!mounted.current) return;
      setWriteState("created");
      setMessage(SCENARIO_CREATE_MESSAGES.created);
      setName("");
    } catch (error: unknown) {
      if (!mounted.current) return;
      const refusal = describeScenarioCreateFailure(error);
      setWriteState("refused");
      setMessage(refusal);
    } finally {
      submitting.current = false;
    }
  }

  return (
    <div className="scenario-create">
      <h3>{SCENARIO_CREATE_MESSAGES.heading} in {projectName}</h3>
      <form onSubmit={(event) => void handleSubmit(event)}>
        <label htmlFor={`scenario-name-${projectId}`}>{SCENARIO_CREATE_MESSAGES.nameLabel}</label>
        <input
          id={`scenario-name-${projectId}`}
          className="input"
          name="name"
          required
          maxLength={200}
          value={name}
          disabled={writeState === "saving"}
          onChange={(event) => setName(event.currentTarget.value)}
        />
        <button className="button button--primary" type="submit" disabled={writeState === "saving" || name.length === 0}>
          {writeState === "saving" ? SCENARIO_CREATE_MESSAGES.creating : SCENARIO_CREATE_MESSAGES.create}
        </button>
      </form>
      {message !== null && (
        <p ref={outcomeRef} tabIndex={-1} role="status" className="scenario-card__metric">
          {message}
          {message === SCENARIO_CREATE_MESSAGES.unresolved && (
            <> <button type="button" className="button button--secondary" onClick={onRefresh}>{SCENARIO_CREATE_MESSAGES.refresh}</button></>
          )}
        </p>
      )}
    </div>
  );
}
