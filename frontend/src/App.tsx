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
    <main>
      <h1>StafffingCalculator</h1>
      <p>IT project staffing, cost, and profitability planner.</p>
      <p data-testid="backend-status">Backend: {backendStatus}</p>
      <ProjectListScreen />
    </main>
  );
}
