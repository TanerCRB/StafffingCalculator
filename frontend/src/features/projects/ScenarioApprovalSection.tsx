import { useEffect, useRef, useState } from "react";

import { ApiError, RequestTimeoutError, approveScenario } from "../../api/client";
import type { ScenarioApproval } from "../../api/contracts/scenarioApproval";
import type { ScenarioStatus } from "../../api/contracts/projects";
import { SCENARIO_APPROVAL_TEXT as text } from "./scenarioApprovalText";

type State = { kind: "idle" | "saving" } | { kind: "approved" } | { kind: "refused"; message: string };

function refusalMessage(error: unknown): string {
  if (error instanceof RequestTimeoutError) return text.timedOut;
  if (!(error instanceof ApiError)) return text.failed;
  if (error.status === 401 || error.status === 403 || error.status === 404) return text.denied;
  if (error.status === 409) {
    return error.detail?.toLowerCase().includes("already approved") ? text.alreadyApproved : text.conflict;
  }
  return text.failed;
}

export interface ScenarioApprovalSectionProps {
  readonly projectId: string;
  readonly scenarioId: string;
  readonly scenarioName: string;
  readonly status: ScenarioStatus;
  readonly ready: boolean;
  readonly onApproved: (scenarioId: string, result: ScenarioApproval) => void;
}

/** Approval uses only server-reported readiness and status; it makes no role or permission claim. */
export function ScenarioApprovalSection({ projectId, scenarioId, scenarioName, status, ready, onApproved }: ScenarioApprovalSectionProps) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const mounted = useRef(true);
  const outcomeRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (state.kind === "approved" || state.kind === "refused") outcomeRef.current?.focus();
  }, [state.kind]);

  async function handleApprove() {
    setState({ kind: "saving" });
    try {
      const result = await approveScenario(projectId, scenarioId);
      if (!mounted.current) return;
      onApproved(scenarioId, result);
      setState({ kind: "approved" });
    } catch (error: unknown) {
      if (mounted.current) setState({ kind: "refused", message: refusalMessage(error) });
    }
  }

  if (status === "Approved") {
    return <p className="scenario-card__metric">{text.alreadyApproved}</p>;
  }
  return (
    <section aria-label={`Approval for ${scenarioName}`}>
      {ready && state.kind !== "approved" && (
        <button type="button" className="button button--secondary" disabled={state.kind === "saving"}
          aria-label={`${text.approve}: ${scenarioName}`} onClick={() => void handleApprove()}>
          {state.kind === "saving" ? text.saving : text.approve}
        </button>
      )}
      {state.kind === "saving" && <p role="status" className="scenario-card__metric">{text.saving}</p>}
      {state.kind === "approved" && <p ref={outcomeRef} tabIndex={-1} role="status" className="scenario-card__metric">{text.approved}</p>}
      {state.kind === "refused" && <p ref={outcomeRef} tabIndex={-1} role="status" className="scenario-card__gaps">{state.message}</p>}
    </section>
  );
}
