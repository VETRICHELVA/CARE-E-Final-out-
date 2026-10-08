"""The forecasting model (apps-ai-iot.md, Forecasting): statistics, not an LLM.

Holt-Winters exponential smoothing with weekly seasonality (statsmodels' ETSModel: additive
error, damped additive trend, additive 7-day season), with its 95% prediction interval. Under
60 days of history, or if the fit fails, a 28-day moving average whose interval is the mean
± z × the window's standard deviation. Forecasts and bounds are never below 0.

Deterministic: the same history always gives the same numbers (no random draws; statsmodels
fits by a deterministic optimizer from deterministic starting values). CPU-bound, so callers
in the event loop run it in a thread."""

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import NormalDist, fmean, pstdev

from app.domain import config
from app.domain.forecast import Day

HOLT_WINTERS = "holt-winters-weekly/1"
MOVING_AVERAGE = "moving-average-28/1"
SEASON_DAYS = 7


@dataclass(frozen=True)
class Result:
    model_version: str
    days: list[Day]


def forecast(history: Sequence[float], first_day: date, start: date, end: date) -> Result:
    """Forecast each date in [start, end] from daily `history`, where history[i] is the
    consumption on first_day + i (a gap inside it is 0: nothing was used). The model steps
    from the day after the history ends; dates before `start` are not returned."""
    if not history:
        raise ValueError("A forecast needs at least one day of history.")
    last_day = first_day + timedelta(len(history) - 1)
    if start <= last_day or end < start:
        raise ValueError("Forecast dates must follow the history.")
    steps = (end - last_day).days
    model, means, lower, upper = MOVING_AVERAGE, *_moving_average(history, steps)
    if len(history) >= config.FORECAST_MIN_HISTORY_DAYS:
        fitted = _holt_winters(history, first_day, steps)
        if fitted is not None:
            model, (means, lower, upper) = HOLT_WINTERS, fitted
    days = [
        Day(
            date=last_day + timedelta(i + 1),
            predicted=max(0.0, m),
            lower=max(0.0, min(lo, m)),
            upper=max(0.0, hi, m),
        )
        for i, (m, lo, hi) in enumerate(zip(means, lower, upper, strict=True))
    ]
    return Result(model, [d for d in days if d.date >= start])


def _z() -> float:
    return NormalDist().inv_cdf(0.5 + config.FORECAST_INTERVAL_PCT / 200)


def _moving_average(
    history: Sequence[float], steps: int
) -> tuple[list[float], list[float], list[float]]:
    window = list(history[-config.FORECAST_MOVING_AVERAGE_DAYS :])
    mean, spread = fmean(window), _z() * pstdev(window)
    return [mean] * steps, [mean - spread] * steps, [mean + spread] * steps


def _holt_winters(
    history: Sequence[float], first_day: date, steps: int
) -> tuple[list[float], list[float], list[float]] | None:
    import pandas as pd
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    series = pd.Series(
        [float(x) for x in history],
        index=pd.date_range(first_day.isoformat(), periods=len(history), freq="D"),
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # convergence chatter; a bad fit is caught below
            fit = ETSModel(
                series,
                error="add",
                trend="add",
                damped_trend=True,
                seasonal="add",
                seasonal_periods=SEASON_DAYS,
            ).fit(disp=False)
            frame = fit.get_prediction(start=len(history), end=len(history) + steps - 1)
            summary = frame.summary_frame(alpha=1 - config.FORECAST_INTERVAL_PCT / 100)
    except (ValueError, ArithmeticError, IndexError, KeyError):
        return None
    out = (
        [float(x) for x in summary["mean"]],
        [float(x) for x in summary["pi_lower"]],
        [float(x) for x in summary["pi_upper"]],
    )
    if any(not math.isfinite(x) for column in out for x in column):
        return None
    return out
