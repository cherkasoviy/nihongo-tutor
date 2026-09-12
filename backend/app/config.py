"""Application settings loaded from the environment (or a local ``.env``).

Every secret lives only in the server-side ``.env``; nothing here has a real default
that could work against production by accident.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # --- storage -------------------------------------------------------------
    db_url: str = Field(
        default="postgresql+asyncpg://nihongo:nihongo@localhost:5432/nihongo",
        description="SQLAlchemy async URL",
    )
    redis_url: str = "redis://localhost:6379/0"
    audio_dir: str = "/data/audio"

    # --- telegram ------------------------------------------------------------
    bot_token: SecretStr = SecretStr("")
    public_url: str = Field(default="http://localhost:8000", description="Public https origin served by Caddy")
    webhook_path: str = "/tg/webhook"
    webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Value Telegram echoes in X-Telegram-Bot-Api-Secret-Token; generated with `openssl rand -hex 32`",
    )
    miniapp_url: str = Field(default="http://localhost:5173", description="Where the Mini App is served")
    admin_tg_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list, description="Comma-separated Telegram user ids bootstrapped as admins"
    )

    # --- auth ----------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("")
    jwt_ttl_seconds: int = 3600
    initdata_max_age_seconds: int = 24 * 3600

    # --- AI providers --------------------------------------------------------
    # Claude is reached through Vertex AI when a project is set, and through the first-party API
    # otherwise. Vertex authenticates with the same Google service account as TTS/STT, so no
    # Anthropic key exists anywhere in the deployment.
    anthropic_vertex_project: str = ""
    anthropic_vertex_region: str = "global"
    anthropic_api_key: SecretStr = SecretStr("")
    # Where a refusal is retried. Server-side fallbacks are not offered on Vertex, so the SDK's
    # client-side middleware needs an explicit model rather than the "default" routing policy.
    anthropic_fallback_model: str = "claude-opus-4-8"
    google_application_credentials: str | None = None
    default_daily_budget_usd: float = 0.35
    model_strong: str = "claude-opus-5"
    model_fast: str = "claude-opus-5"

    @property
    def uses_vertex(self) -> bool:
        return bool(self.anthropic_vertex_project)

    @field_validator("admin_tg_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
        return value

    @property
    def webhook_url(self) -> str:
        return self.public_url.rstrip("/") + self.webhook_path

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
