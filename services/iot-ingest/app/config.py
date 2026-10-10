from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV = "dev"
# The committed dev default; outside dev the ingest refuses it (as the hub does, S20).
DEV_INGEST_TOKEN = "dev-only-ingest-token-change-me"
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # `dev` for local development. Unset or anything else counts as production: the ingest
    # then refuses to start with the committed dev token or one shorter than 32 characters.
    app_env: str = ""
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    hub_api_url: str = "http://127.0.0.1:8000/api/v1"
    # Same value as the hub's INGEST_TOKEN; it grants scope `telemetry.write` only.
    ingest_token: str = DEV_INGEST_TOKEN
    flush_interval_s: float = Field(default=2.0, gt=0)
    max_batch: int = Field(default=500, ge=1, le=1000)  # the hub takes at most 1000 per POST
    max_pending: int = Field(default=10_000, ge=1)  # while the hub is down; oldest dropped first
    dedup_window: int = Field(default=50_000, ge=1)  # (device_id, ts) keys remembered

    @property
    def is_dev(self) -> bool:
        return self.app_env == DEV

    @model_validator(mode="after")
    def _real_token_outside_dev(self) -> "Settings":
        """A leaked dev token would let anyone post readings (fake cold-chain excursions)."""
        token = self.ingest_token
        if not self.is_dev and (token == DEV_INGEST_TOKEN or len(token) < MIN_SECRET_LENGTH):
            env = f"APP_ENV={self.app_env}" if self.app_env else "APP_ENV is unset (not dev)"
            raise ValueError(
                f"{env}: set INGEST_TOKEN to the hub's random value of at least "
                f"{MIN_SECRET_LENGTH} characters (the dev default is only for APP_ENV=dev)."
            )
        return self
