import { OrganizationDefaultsSection } from "./ScenarioAssumptionsSection";

/** Workspace-level controls; this screen has no project identifier or project read dependency. */
export function OrganizationDefaultsScreen() {
  return (
    <section className="card organization-defaults-screen" aria-labelledby="organization-defaults-title">
      <h2 id="organization-defaults-title" className="card__title" tabIndex={-1}>
        Organization defaults
      </h2>
      <p>Defaults for target margin and overload threshold.</p>
      <OrganizationDefaultsSection />
    </section>
  );
}
