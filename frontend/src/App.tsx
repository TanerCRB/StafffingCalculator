import { useEffect, useState } from "react";

import { getHealth } from "./api/client";
import { ProjectListScreen } from "./features/projects/ProjectListScreen";

type BackendStatus = "checking" | "ok" | "unreachable";

export function App() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("checking");

  useEffect(() => {
    getHealth()
      .then(() => setBackendStatus("ok"))
      .catch(() => setBackendStatus("unreachable"));
  }, []);

  return (
    <main className="app-shell">
      <header className="app-shell__header">
        <div>
          <h1 className="app-shell__title">StafffingCalculator</h1>
          <p className="app-shell__subtitle">
            IT project staffing, cost, and profitability planner.
          </p>
        </div>
        {/* The state name is the text; `data-state` only picks the colour for it. */}
        <p className="app-shell__status" data-testid="backend-status" data-state={backendStatus}>
          Backend: {backendStatus}
        </p>
      </header>
      <ProjectListScreen />
    </main>
  );
}
