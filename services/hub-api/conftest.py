"""Loaded before any test module imports `app`: the tests run as a dev hub.

An unset APP_ENV counts as production (app/config.py), which refuses the committed dev
secrets, so every test process sets APP_ENV=dev explicitly. Tests of the production checks
build their own `Settings(app_env="production", ...)`."""

import os

os.environ["APP_ENV"] = "dev"
