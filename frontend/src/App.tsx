import { useEffect, useState } from "react";

import { getHealth } from "./api/client";
import { CatalogScreen } from "./features/catalog/CatalogScreen";
import { WorkingCalendarsScreen } from "./features/catalog/WorkingCalendarsScreen";
import { CompareScenariosScreen } from "./features/compare/CompareScenariosScreen";
import { ProjectListScreen } from "./features/projects/ProjectListScreen";
import { ProjectOverviewScreen } from "./features/overview/ProjectOverviewScreen";
import { AppShell, type BackendStatus, type ScreenKey } from "./shell/AppShell";

export function App() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("checking");
  /**
   * Which screen is mounted. React state, no router (SC-2-02, gate-1 decision 10): one navigation
   * mechanism, no new dependency, and no URL — a screen is not linkable yet, which is a named
   * limitation rather than an omission.
   */
  const [activeScreen, setActiveScreen] = useState<ScreenKey>("projects");

  useEffect(() => {
    getHealth()
      .then(() => setBackendStatus("ok"))
      .catch(() => setBackendStatus("unreachable"));
  }, []);

  // The shell is chrome only: it renders the screen it is given and reads nothing of its own. The
  // screen not named here is not mounted at all, so it fetches nothing in the background.
  let screen;
  if (activeScreen === "projects") {
    screen = <ProjectListScreen />;
  } else if (activeScreen === "overview") {
    screen = <ProjectOverviewScreen />;
  } else if (activeScreen === "compare-scenarios") {
    screen = <CompareScenariosScreen />;
  } else if (activeScreen === "roles-and-rates") {
    screen = <CatalogScreen />;
  } else {
    screen = <WorkingCalendarsScreen />;
  }

  return (
    <AppShell
      backendStatus={backendStatus}
      activeScreen={activeScreen}
      onNavigate={setActiveScreen}
    >
      {screen}
    </AppShell>
  );
}
