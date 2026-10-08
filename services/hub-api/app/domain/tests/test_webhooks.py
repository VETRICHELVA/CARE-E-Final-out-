from datetime import UTC, datetime, timedelta

import pytest

from app.domain import config, webhooks

T0 = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)


def test_signature_is_a_hex_hmac_sha256_of_the_raw_body() -> None:
    # Known vector: HMAC-SHA256(key="secret", msg="body").
    expected = "sha256=dc46983557fea127b43af721467eb9b3fde2338fe3e14f51952aa8478c13d355"
    assert webhooks.signature("secret", b"body") == expected
    assert webhooks.verify("secret", b"body", expected)
    assert not webhooks.verify("other", b"body", expected)
    assert not webhooks.verify("secret", b"body!", expected)


def test_retry_delay_starts_at_one_minute_doubles_and_caps_at_one_hour() -> None:
    minutes = [webhooks.retry_delay(n) / timedelta(minutes=1) for n in range(1, 10)]
    assert minutes == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    assert webhooks.retry_delay(10_000) == timedelta(hours=1)
    with pytest.raises(ValueError):
        webhooks.retry_delay(0)


def test_retries_stop_after_24_hours() -> None:
    assert timedelta(hours=24) == config.WEBHOOK_RETRY_WINDOW
    assert webhooks.next_attempt_at(T0, 1, T0) == T0 + timedelta(minutes=1)
    # A retry that would land exactly at the 24 h mark is still made; later ones are not.
    last = T0 + timedelta(hours=23)
    assert webhooks.next_attempt_at(T0, 8, last) == T0 + timedelta(hours=24)
    assert webhooks.next_attempt_at(T0, 8, last + timedelta(seconds=1)) is None
