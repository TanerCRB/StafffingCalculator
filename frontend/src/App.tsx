import { useEffect, useState } from "react";

import { getHealth } from "./api/client";
import { ProjectListScreen } from "./features/projects/ProjectListScreen";
import { AppShell, type BackendStatus } from "./shell/AppShell";

export function App() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("checking");

  useEffect(() => {
    getHealth()
      .then(() => setBackendStatus("ok"))
      .catch(() => setBackendStatus("unreachable"));
  }, []);

  // The shell is chrome only: it renders the screen it is given and reads nothing of its own.
  return (
    <AppShell backendStatus={backendStatus}>
      <ProjectListScreen />
    </AppShell>
  );
}
