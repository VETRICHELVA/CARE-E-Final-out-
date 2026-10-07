from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://care:care@127.0.0.1:5434/care"
    redis_url: str = "redis://127.0.0.1:6379/0"
    jwt_secret: str = "dev-only-change-me-dev-only-change-me"
    # Bearer token for services/iot-ingest; grants scope `telemetry.write` only.
    ingest_token: str = "dev-only-ingest-token-change-me"


settings = Settings()
