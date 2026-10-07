from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    hub_api_url: str = "http://127.0.0.1:8000/api/v1"
    # Same value as the hub's INGEST_TOKEN; it grants scope `telemetry.write` only.
    ingest_token: str = "dev-only-ingest-token-change-me"
    flush_interval_s: float = Field(default=2.0, gt=0)
    max_batch: int = Field(default=500, ge=1, le=1000)  # the hub takes at most 1000 per POST
    max_pending: int = Field(default=10_000, ge=1)  # while the hub is down; oldest dropped first
    dedup_window: int = Field(default=50_000, ge=1)  # (device_id, ts) keys remembered
