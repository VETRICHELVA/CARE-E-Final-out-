"""Webhook rules (api-and-events.md, Webhooks): the signature and the retry schedule."""

import hashlib
import hmac
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain import config

SIGNATURE_HEADER = "X-CareE-Signature"


class DeliveryStatus(StrEnum):
    """One WebhookDelivery row per attempt. PENDING is due at `next_attempt_at`; a failed
    attempt is RETRY_SCHEDULED (a new PENDING row follows) or FAILED (no more attempts)."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    FAILED = "FAILED"


def signature(secret: str, body: bytes) -> str:
    """`sha256=<hex HMAC-SHA256 of the raw body with the subscription secret>`."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str) -> bool:
    return hmac.compare_digest(signature(secret, body), header)


def retry_delay(failed_attempt: int) -> timedelta:
    """1 min after the first failure, doubling each time, capped at 1 h."""
    if failed_attempt < 1:
        raise ValueError("Attempts are numbered from 1.")
    first, cap = config.WEBHOOK_FIRST_RETRY, config.WEBHOOK_MAX_RETRY
    # Stop doubling once past the cap (also keeps the multiplier small for long runs).
    doublings = min(failed_attempt - 1, 32)
    return min(first * (1 << doublings), cap)


def next_attempt_at(first_due: datetime, failed_attempt: int, now: datetime) -> datetime | None:
    """When to try again after attempt `failed_attempt` failed at `now`, or None once the
    retry would fall outside the 24 h window that started at `first_due` (then FAILED)."""
    at = now + retry_delay(failed_attempt)
    return at if at <= first_due + config.WEBHOOK_RETRY_WINDOW else None
