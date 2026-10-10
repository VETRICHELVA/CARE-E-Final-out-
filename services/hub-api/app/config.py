from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV = "dev"
# api-and-events.md: 5 login attempts per minute per client IP. Only a dev hub may raise it.
DEFAULT_LOGIN_RATE_LIMIT = 5
DEFAULT_LOGIN_RATE_WINDOW_SECONDS = 60
# The committed dev defaults. Outside dev the hub refuses to start with any of them (S20).
DEV_JWT_SECRET = "dev-only-change-me-dev-only-change-me"
DEV_INGEST_TOKEN = "dev-only-ingest-token-change-me"
DEV_AI_SERVICE_TOKEN = "dev-only-ai-token-change-me"
MIN_SECRET_LENGTH = 32
# The three apps' Vite dev servers (CLAUDE.md, `pnpm --filter <app> dev`).
DEV_APP_ORIGINS = ",".join(
    f"http://{host}:{port}" for port in (5173, 5174, 5175) for host in ("localhost", "127.0.0.1")
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # `dev` for local development, anything else (e.g. `production`) outside it. Unset counts as
    # outside dev (fail closed): the hub then refuses to start with a dev default or short
    # secret, webhooks must be https, private webhook targets are always refused and
    # LOGIN_RATE_LIMIT cannot be raised. The Makefile, CI, Playwright and the tests set `dev`.
    app_env: str = ""
    # The hub and worker connect with this URL. Outside dev it must be the least-privilege role
    # `care_app` (migration 0017_s20fix), not the owner of the tables: the hub refuses to start
    # as an owner or superuser, which could disable the audit_log trigger (README, Configuration).
    database_url: str = "postgresql+asyncpg://care:care@127.0.0.1:5434/care"
    # Migrations (and `make demo-reset`) run as the owner of the tables. Empty: DATABASE_URL,
    # as in local dev where both are the owner `care`.
    migration_database_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    jwt_secret: str = DEV_JWT_SECRET
    # Bearer token for services/iot-ingest; grants scope `telemetry.write` only.
    ingest_token: str = DEV_INGEST_TOKEN
    # Bearer token for services/ai-service; grants scope `ai.read` only, i.e. GET /ai/read/*
    # on behalf of the signed-in user whose access token rides in X-On-Behalf-Of (S13).
    ai_service_token: str = DEV_AI_SERVICE_TOKEN
    # OSRM server for road distances and routes (S11; infra/osrm/README.md), e.g.
    # http://127.0.0.1:5000. Empty: haversine x ROAD_FACTOR only (business-rules.md §4).
    osrm_url: str = ""
    # A call slower than this falls back to haversine; after a failure OSRM is skipped for
    # `osrm_cooldown_seconds`, so a down server does not cost every distance this timeout.
    osrm_timeout_seconds: float = 2.0
    osrm_cooldown_seconds: float = 30.0

    # --- S20 hardening -------------------------------------------------------------------------
    # Browser origins allowed by CORS, comma-separated: the three apps. The apps normally reach
    # the hub through their Vite `/api` proxy (same origin), so this matters for a deployment
    # that serves them from their own origins.
    cors_origins: str = DEV_APP_ORIGINS
    # Login attempts per client IP per window (api-and-events.md: 5 per minute). A dev hub may
    # raise it for e2e runs that sign in many times a minute; outside dev a value above 5 is
    # ignored (`login_attempts_allowed`), lower values apply; likewise a window shorter than
    # 60 s (`login_window_seconds`).
    login_rate_limit: int = Field(default=DEFAULT_LOGIN_RATE_LIMIT, ge=1)
    login_rate_window_seconds: int = Field(default=DEFAULT_LOGIN_RATE_WINDOW_SECONDS, ge=1)
    # Reverse proxies whose X-Forwarded-For the hub trusts, comma-separated IPs or CIDRs (e.g.
    # `10.0.0.0/8`). The login rate limit then keys on the client address the nearest trusted
    # proxy saw; from anyone else the header is ignored. Empty: the TCP peer is the client.
    trusted_proxies: str = ""
    # Dev only: let webhooks target private, loopback and link-local addresses (a receiver on
    # your machine). Ignored outside dev.
    webhook_allow_private_targets: bool = False
    # Published outbox events older than this are pruned by the worker (hourly); SSE replay
    # (Last-Event-ID) reaches back this far. Events with a webhook delivery still pending stay.
    event_retention_hours: int = Field(default=7 * 24, ge=1)
    # `coldchain.reading` (one event per reading, every few seconds per box) is pruned sooner.
    reading_event_retention_hours: int = Field(default=24, ge=1)

    @property
    def is_dev(self) -> bool:
        """Only an explicit APP_ENV=dev; unset or anything else is treated as production."""
        return self.app_env == DEV

    @property
    def login_attempts_allowed(self) -> int:
        """LOGIN_RATE_LIMIT, but never above the default outside dev."""
        if self.is_dev:
            return self.login_rate_limit
        return min(self.login_rate_limit, DEFAULT_LOGIN_RATE_LIMIT)

    @property
    def login_window_seconds(self) -> int:
        """LOGIN_RATE_WINDOW_SECONDS, but never shorter than the default outside dev."""
        if self.is_dev:
            return self.login_rate_window_seconds
        return max(self.login_rate_window_seconds, DEFAULT_LOGIN_RATE_WINDOW_SECONDS)

    @property
    def migration_url(self) -> str:
        return self.migration_database_url or self.database_url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _real_secrets_outside_dev(self) -> "Settings":
        """Outside dev, refuse to start with a committed default or a short secret: a leaked
        default would let anyone sign tokens, post readings (fake excursions) or read as the
        AI service."""
        if self.is_dev:
            return self
        defaults = {
            "JWT_SECRET": (self.jwt_secret, DEV_JWT_SECRET),
            "INGEST_TOKEN": (self.ingest_token, DEV_INGEST_TOKEN),
            "AI_SERVICE_TOKEN": (self.ai_service_token, DEV_AI_SERVICE_TOKEN),
        }
        bad = [
            name
            for name, (value, default) in defaults.items()
            if value == default or len(value) < MIN_SECRET_LENGTH
        ]
        if bad:
            env = f"APP_ENV={self.app_env}" if self.app_env else "APP_ENV is unset (not dev)"
            raise ValueError(
                f"{env}: set {', '.join(bad)} to a random value of at least "
                f"{MIN_SECRET_LENGTH} characters (the dev defaults are only for APP_ENV=dev)."
            )
        return self


settings = Settings()
