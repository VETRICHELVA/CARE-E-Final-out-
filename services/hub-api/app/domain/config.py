"""Every tunable number from business-rules.md. Domain code reads these, never literals.

Each value can be overridden by an environment variable (or a line in `.env`) of the same
name, e.g. `SLA_CRITICAL_RESPONSE_MINUTES=1` for a quick manual check of the timers."""

from datetime import timedelta
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Pos = Annotated[float, Field(gt=0)]
PosInt = Annotated[int, Field(gt=0)]
NonNegInt = Annotated[int, Field(ge=0)]


class Tunables(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # §4 Cost and ETA
    road_factor: Pos = 1.3  # haversine x 1.3 when OSRM is off, failing or slow (S11)
    avg_speed_kmh: PosInt = 40
    handover_hours: NonNegInt = 1
    transport_rate_paise_per_km: NonNegInt = 2_500  # Rs 25/km
    handling_fee_pct: NonNegInt = 2  # % of item value, hospital sources; integer maths
    cold_chain_max_transit_hours: Pos = 4  # pickup to drop, cold-chain shipments (S16)

    # §3 Eligibility gates: freshness
    verified_within_critical_hours: Pos = 24
    verified_within_routine_days: Pos = 7
    offer_updated_within_days: Pos = 7

    # §5 Ranking and resolution
    near_expiry_days: NonNegInt = 90
    max_split_sources: Annotated[int, Field(ge=2)] = 3
    default_reliability: Annotated[int, Field(ge=0, le=100)] = 70  # an org with no history

    # §6 Time limits, by shortage priority
    sla_critical_response_minutes: Pos = 15
    sla_routine_response_minutes: Pos = 4 * 60
    sla_critical_hold_minutes: Pos = 30
    sla_routine_hold_minutes: Pos = 24 * 60
    sla_critical_recommendation_minutes: Pos = 30
    sla_routine_recommendation_minutes: Pos = 24 * 60

    # §6 Timers: how often the worker looks for overdue deadlines
    timer_interval_seconds: Annotated[int, Field(gt=0, le=60)] = 30

    # api-and-events.md, Webhooks: 1 min doubling, capped at 1 h, for 24 h
    webhook_first_retry_seconds: PosInt = 60
    webhook_max_retry_seconds: PosInt = 60 * 60
    webhook_retry_window_hours: PosInt = 24

    # apps-ai-iot.md, Forecasting (S18): run nightly in the hub worker and on demand
    forecast_horizon_days: PosInt = 30  # the "30-day forecast" each product gets
    forecast_max_horizon_days: PosInt = 365  # how far out a batch's expiry can be judged
    forecast_min_history_days: PosInt = 60  # below this: the moving average
    forecast_moving_average_days: PosInt = 28
    forecast_interval_pct: Annotated[int, Field(gt=0, lt=100)] = 95
    forecast_default_lead_time_days: PosInt = 7  # reorder lead time with no supplier offer
    forecast_nightly_hour_utc: Annotated[int, Field(ge=0, le=23)] = 20  # 02:00 IST
    forecast_nightly_minute_utc: Annotated[int, Field(ge=0, le=59)] = 30
    # S18 surplus: match open posts to forecast stock-outs this close (brief)
    surplus_stockout_match_days: PosInt = 14


_t = Tunables()

# §4 Cost and ETA
ROAD_FACTOR = _t.road_factor
AVG_SPEED_KMH = _t.avg_speed_kmh
HANDOVER_HOURS = _t.handover_hours
TRANSPORT_RATE_PAISE_PER_KM = _t.transport_rate_paise_per_km
HANDLING_FEE_PCT = _t.handling_fee_pct
COLD_CHAIN_MAX_TRANSIT = timedelta(hours=_t.cold_chain_max_transit_hours)

# §3 Eligibility gates: freshness
VERIFIED_WITHIN_CRITICAL = timedelta(hours=_t.verified_within_critical_hours)
VERIFIED_WITHIN_ROUTINE = timedelta(days=_t.verified_within_routine_days)
OFFER_UPDATED_WITHIN = timedelta(days=_t.offer_updated_within_days)

# §5 Ranking and resolution
NEAR_EXPIRY_DAYS = _t.near_expiry_days
MAX_SPLIT_SOURCES = _t.max_split_sources
DEFAULT_RELIABILITY = _t.default_reliability

# §6 Time limits, by shortage priority
SOURCE_RESPONSE_LIMIT = {
    "CRITICAL": timedelta(minutes=_t.sla_critical_response_minutes),
    "ROUTINE": timedelta(minutes=_t.sla_routine_response_minutes),
}
TENTATIVE_HOLD_LIMIT = {
    "CRITICAL": timedelta(minutes=_t.sla_critical_hold_minutes),
    "ROUTINE": timedelta(minutes=_t.sla_routine_hold_minutes),
}
RECOMMENDATION_VALIDITY = {
    "CRITICAL": timedelta(minutes=_t.sla_critical_recommendation_minutes),
    "ROUTINE": timedelta(minutes=_t.sla_routine_recommendation_minutes),
}
TIMER_INTERVAL_SECONDS = _t.timer_interval_seconds

# api-and-events.md, Webhooks
WEBHOOK_FIRST_RETRY = timedelta(seconds=_t.webhook_first_retry_seconds)
WEBHOOK_MAX_RETRY = timedelta(seconds=_t.webhook_max_retry_seconds)
WEBHOOK_RETRY_WINDOW = timedelta(hours=_t.webhook_retry_window_hours)

# apps-ai-iot.md, Forecasting (S18)
FORECAST_HORIZON_DAYS = _t.forecast_horizon_days
FORECAST_MAX_HORIZON_DAYS = max(_t.forecast_max_horizon_days, _t.forecast_horizon_days)
FORECAST_MIN_HISTORY_DAYS = _t.forecast_min_history_days
FORECAST_MOVING_AVERAGE_DAYS = _t.forecast_moving_average_days
FORECAST_INTERVAL_PCT = _t.forecast_interval_pct
FORECAST_DEFAULT_LEAD_TIME_DAYS = _t.forecast_default_lead_time_days
FORECAST_NIGHTLY_HOUR_UTC = _t.forecast_nightly_hour_utc
FORECAST_NIGHTLY_MINUTE_UTC = _t.forecast_nightly_minute_utc
SURPLUS_STOCKOUT_MATCH_DAYS = _t.surplus_stockout_match_days
# The expiry band other orgs see instead of a surplus batch's expiry date (CLAUDE.md rule 6):
# under 30 days, 30-59, 60-89, or 90 and more.
SURPLUS_EXPIRY_BAND_DAYS = (30, 60, 90)
