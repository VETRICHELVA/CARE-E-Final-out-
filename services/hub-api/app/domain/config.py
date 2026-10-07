"""Every tunable number from business-rules.md. Domain code reads these, never literals."""

from datetime import timedelta

# §4 Cost and ETA
ROAD_FACTOR = 1.3  # haversine x 1.3 until OSRM routing (S11), and whenever OSRM is down
AVG_SPEED_KMH = 40
HANDOVER_HOURS = 1
TRANSPORT_RATE_PAISE_PER_KM = 2_500  # Rs 25/km
HANDLING_FEE_PCT = 2  # of item value, hospital sources only

# §3 Eligibility gates: freshness
VERIFIED_WITHIN_CRITICAL = timedelta(hours=24)
VERIFIED_WITHIN_ROUTINE = timedelta(days=7)
OFFER_UPDATED_WITHIN = timedelta(days=7)

# §5 Ranking and resolution
NEAR_EXPIRY_DAYS = 90
MAX_SPLIT_SOURCES = 3
DEFAULT_RELIABILITY = 70  # an org with no history

# §6 Time limits, by shortage priority
SOURCE_RESPONSE_LIMIT = {"CRITICAL": timedelta(minutes=15), "ROUTINE": timedelta(hours=4)}
TENTATIVE_HOLD_LIMIT = {"CRITICAL": timedelta(minutes=30), "ROUTINE": timedelta(hours=24)}
RECOMMENDATION_VALIDITY = {"CRITICAL": timedelta(minutes=30), "ROUTINE": timedelta(hours=24)}
