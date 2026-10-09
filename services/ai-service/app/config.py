import os

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment (services/ai-service/.env.example). With no API key the service still
    starts and answers every question with `ai_not_configured`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The hub, which the AI reads only through GET /ai/read/* (apps-ai-iot.md, AI service).
    hub_api_url: str = "http://127.0.0.1:8000/api/v1"
    # Must equal the hub's AI_SERVICE_TOKEN (scope ai.read). Dev default matches the hub's.
    ai_service_token: str = "dev-only-ai-token-change-me"

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
