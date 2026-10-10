import os

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV = "dev"
# The committed dev default; outside dev the service refuses it (as the hub does, S20).
DEV_AI_SERVICE_TOKEN = "dev-only-ai-token-change-me"
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Environment (services/ai-service/.env.example). With no API key the service still
    starts and answers every question with `ai_not_configured`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # `dev` for local development. Unset or anything else counts as production: the service
    # then refuses to start with the committed dev token or one shorter than 32 characters.
    app_env: str = ""
    # The hub, which the AI reads only through GET /ai/read/* (apps-ai-iot.md, AI service).
    hub_api_url: str = "http://127.0.0.1:8000/api/v1"
    # Must equal the hub's AI_SERVICE_TOKEN (scope ai.read). Dev default matches the hub's.
    ai_service_token: str = DEV_AI_SERVICE_TOKEN

    ai_provider: str = "anthropic"
    ai_model: str = "claude-opus-5-5"
    # AI_API_KEY, or ANTHROPIC_API_KEY when AI_API_KEY is unset. Never commit a key.
    ai_api_key: str = Field(
        default="", validation_alias=AliasChoices("ai_api_key", "anthropic_api_key")
    )
    # Copilot answers are short lookups over a few tool results.
    ai_effort: str = "medium"
    # Chat ordering (S17) only extracts fields from one message; dates, products and checks
    # are resolved in code.
    ai_chat_effort: str = "medium"
    # Tool calls the model may make per question (S13 brief: the loop is capped at 6).
    ai_max_tool_calls: int = 6
    hub_timeout_seconds: float = 10.0

    @property
    def is_dev(self) -> bool:
        return self.app_env == DEV

    @model_validator(mode="after")
    def _real_token_outside_dev(self) -> "Settings":
        """A leaked dev token would let anyone read the hub as the AI service."""
        token = self.ai_service_token
        if not self.is_dev and (token == DEV_AI_SERVICE_TOKEN or len(token) < MIN_SECRET_LENGTH):
            env = f"APP_ENV={self.app_env}" if self.app_env else "APP_ENV is unset (not dev)"
            raise ValueError(
                f"{env}: set AI_SERVICE_TOKEN to the hub's random value of at least "
                f"{MIN_SECRET_LENGTH} characters (the dev default is only for APP_ENV=dev)."
            )
        return self

    @model_validator(mode="after")
    def _key_from_anthropic_env(self) -> "Settings":
        # An empty AI_API_KEY (e.g. a blank line in .env) must not hide ANTHROPIC_API_KEY.
        if not self.ai_api_key.strip():
            self.ai_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        return self

    @property
    def configured(self) -> bool:
        return self.ai_provider == "anthropic" and bool(self.ai_api_key.strip())


settings = Settings()
