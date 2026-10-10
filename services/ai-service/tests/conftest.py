import os

import pytest

# Unit tests never reach a real model: drop any key before a test module builds Settings.
for _name in ("AI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_name, None)
# An unset APP_ENV counts as production, which refuses the committed dev token (app/config.py).
os.environ["APP_ENV"] = "dev"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"
