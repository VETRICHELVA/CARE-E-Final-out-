"""S20: outside dev the AI service refuses the committed dev token, like the hub; an unset
APP_ENV counts as production."""

import pytest
from app.config import DEV_AI_SERVICE_TOKEN, Settings

REAL = "x" * 40


def settings(**values: str) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def test_dev_runs_with_the_committed_token() -> None:
    assert settings(app_env="dev").ai_service_token == DEV_AI_SERVICE_TOKEN


@pytest.mark.parametrize("token", [DEV_AI_SERVICE_TOKEN, "short"])
@pytest.mark.parametrize("env", ["production", "", "Dev"])
def test_outside_dev_a_default_or_short_token_is_refused(env: str, token: str) -> None:
    with pytest.raises(ValueError, match="AI_SERVICE_TOKEN"):
        settings(app_env=env, ai_service_token=token)


def test_an_unset_app_env_is_not_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(ValueError, match="APP_ENV is unset"):
        settings()
    assert not settings(ai_service_token=REAL).is_dev
