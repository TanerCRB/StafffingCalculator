from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, read from environment variables / .env.

    Nothing here reads a secret at import time from a place other than the environment — see
    agents/security-auditor.md, area III (secrets).
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    app_name: str = "StafffingCalculator"
    environment: str = "development"

    # PostgreSQL only — ADR-0001. The driver is psycopg 3; SQLAlchemy 2.x is the ORM.
    database_url: str = "postgresql+psycopg://staffing:staffing@localhost:5432/staffing"

    # --- TEMPORARY, DATED DEVIATION (ADR-0005, addendum 2026-09-18) ----------------------------
    # There is no authentication mechanism in this repository yet (no user table, no session, no
    # token). Until the separate authentication ADR exists, the caller's identity comes from a
    # request header carrying a test identifier. This is a placeholder, not authentication: it
    # proves the `project_access` filter, nothing about who the caller really is.
    caller_id_header: str = "X-Caller-User-Id"

    # Deny by default, deliberately: running the placeholder identity must be opted into
    # explicitly (`APP_ALLOW_PLACEHOLDER_IDENTITY=true`). A process started with no
    # configuration at all — missing `.env`, wrong working directory, a variable dropped from a
    # deployment manifest — then refuses to start instead of silently trusting a request header.
    # Unlike `environment`, this field has no permissive default to fall back on.
    allow_placeholder_identity: bool = False


settings = Settings()
