/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** Placeholder caller identity (ADR-0005, addendum 2026-09-18) — a configured test
   * identifier, not authentication. Replaced once the authentication ADR lands. */
  readonly VITE_CALLER_USER_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
