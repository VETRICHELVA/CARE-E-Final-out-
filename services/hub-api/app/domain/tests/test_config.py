from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.domain import config
from app.domain.config import Tunables


def test_defaults_match_business_rules() -> None:
    assert {
        "CRITICAL": timedelta(minutes=15),
        "ROUTINE": timedelta(hours=4),
    } == config.SOURCE_RESPONSE_LIMIT
    assert {
        "CRITICAL": timedelta(minutes=30),
        "ROUTINE": timedelta(hours=24),
    } == config.TENTATIVE_HOLD_LIMIT
    assert config.RECOMMENDATION_VALIDITY["CRITICAL"] == timedelta(minutes=30)
    assert config.TIMER_INTERVAL_SECONDS == 30


def test_environment_overrides_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLA_CRITICAL_RESPONSE_MINUTES", "1")
    monkeypatch.setenv("TRANSPORT_RATE_PAISE_PER_KM", "3000")
    t = Tunables()
    assert (t.sla_critical_response_minutes, t.transport_rate_paise_per_km) == (1, 3000)


def test_an_invalid_override_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLA_CRITICAL_RESPONSE_MINUTES", "0")
    with pytest.raises(ValidationError):
        Tunables()
