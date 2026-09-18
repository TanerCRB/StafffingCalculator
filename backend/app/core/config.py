from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, read from environment variables / .env.

    Nothing here reads a secret at import time from a place other than the environment — see
    agents/security-auditor.md, area III (secrets).
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    app_name: str = "StafffingCalculator"
    environment: str = "development"


settings = Settings()
