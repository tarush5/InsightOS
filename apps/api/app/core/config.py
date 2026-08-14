"""Environment-driven configuration. No secret has a usable default outside local."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    ENV: Literal["local", "staging", "production"] = "local"
    DEBUG: bool = False

    # --- Core infrastructure -------------------------------------------------
    DATABASE_URL: str = "postgresql+psycopg://insightos:insightos@localhost:5432/insightos"
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Auth ----------------------------------------------------------------
    AUTH_SECRET: str = Field(default="", description="HS256 signing key; required outside local")
    ACCESS_TOKEN_TTL_SECONDS: int = 900
    PASSWORD_HASH_ROUNDS: int = 390_000
    # Optional. When set, triggered alerts POST here in addition to the log.
    ALERT_WEBHOOK_URL: str = ""
    REFRESH_TOKEN_TTL_SECONDS: int = 60 * 60 * 24 * 14

    # --- LLM gateway ---------------------------------------------------------
    LLM_PROVIDER: Literal["anthropic", "openai", "none"] = "none"
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    LLM_PLANNING_MODEL: str = "claude-sonnet-4-6"
    LLM_NARRATIVE_MODEL: str = "claude-sonnet-4-6"
    LLM_TIMEOUT_SECONDS: float = 45.0

    # --- Query safety --------------------------------------------------------
    SQL_STATEMENT_TIMEOUT_MS: int = 15_000
    SQL_MAX_RESULT_ROWS: int = 50_000
    SQL_MAX_JOINS: int = 8
    SQL_MAX_QUERY_DEPTH: int = 6

    # --- Observability -------------------------------------------------------
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    OTEL_SERVICE_NAME: str = "insightos-api"
    PROMETHEUS_ENABLED: bool = True

    CORS_ORIGINS: list[str] = ["*"]

    @field_validator("PASSWORD_HASH_ROUNDS")
    @classmethod
    def _rounds_floor(cls, v: int, info) -> int:
        if info.data.get("ENV") in {"staging", "production"} and v < 390_000:
            raise ValueError(
                "PASSWORD_HASH_ROUNDS may not be lowered outside local development")
        return v

    @field_validator("AUTH_SECRET")
    @classmethod
    def _secret_required(cls, v: str, info) -> str:
        if not v and info.data.get("ENV") in {"staging", "production"}:
            raise ValueError("AUTH_SECRET must be set outside local development")
        return v or "dev-only-insecure-secret-do-not-use-in-production"

    @property
    def llm_enabled(self) -> bool:
        return self.LLM_PROVIDER != "none" and bool(self.ANTHROPIC_API_KEY or self.OPENAI_API_KEY)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
