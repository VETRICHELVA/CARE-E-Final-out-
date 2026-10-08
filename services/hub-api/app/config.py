from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://care:care@127.0.0.1:5434/care"
    redis_url: str = "redis://127.0.0.1:6379/0"
    jwt_secret: str = "dev-only-change-me-dev-only-change-me"
    # Bearer token for services/iot-ingest; grants scope `telemetry.write` only.
    ingest_token: str = "dev-only-ingest-token-change-me"
    # Bearer token for services/ai-service; grants scope `ai.read` only, i.e. GET /ai/read/*
    # on behalf of the signed-in user whose access token rides in X-On-Behalf-Of (S13).
    ai_service_token: str = "dev-only-ai-token-change-me"
    # OSRM server for road distances and routes (S11; infra/osrm/README.md), e.g.
    # http://127.0.0.1:5000. Empty: haversine x ROAD_FACTOR only (business-rules.md §4).
    osrm_url: str = ""
    # A call slower than this falls back to haversine; after a failure OSRM is skipped for
    # `osrm_cooldown_seconds`, so a down server does not cost every distance this timeout.
    osrm_timeout_seconds: float = 2.0
    osrm_cooldown_seconds: float = 30.0


settings = Settings()
