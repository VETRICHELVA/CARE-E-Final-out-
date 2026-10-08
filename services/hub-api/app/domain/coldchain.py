"""Cold chain (business-rules.md §11). Pure, no I/O.

Every finding comes from readings the hub actually received, and states only what they
show (CLAUDE.md rule 5): an EXCURSION or RECOVERED names the readings and the band, a
DEVICE_SILENT names how long no reading has arrived. Nothing here infers what happened to
the stock."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.domain import config


class ColdChainEventType(StrEnum):
    EXCURSION = "EXCURSION"
    DEVICE_SILENT = "DEVICE_SILENT"
    RECOVERED = "RECOVERED"


class Severity(StrEnum):
    ALERT = "ALERT"
    WARNING = "WARNING"
    INFO = "INFO"


# §11 calls DEVICE_SILENT a warning; an excursion is the alert the apps raise (Scenario 2).
SEVERITY: dict[ColdChainEventType, Severity] = {
    ColdChainEventType.EXCURSION: Severity.ALERT,
    ColdChainEventType.DEVICE_SILENT: Severity.WARNING,
    ColdChainEventType.RECOVERED: Severity.INFO,
}


@dataclass(frozen=True)
class Band:
    """The product's allowed range [temp_min_c, temp_max_c], bounds inclusive. A missing
    bound does not limit that side; a product with neither has no cold-chain rule."""

    min_c: float | None
    max_c: float | None

    @property
    def defined(self) -> bool:
        return self.min_c is not None or self.max_c is not None

    def contains(self, temp_c: float) -> bool:
        if self.min_c is not None and temp_c < self.min_c:
            return False
        return not (self.max_c is not None and temp_c > self.max_c)

    def crossed(self, temp_c: float) -> float | None:
        """The bound `temp_c` is beyond, or None when it is in range."""
        if self.max_c is not None and temp_c > self.max_c:
            return self.max_c
        if self.min_c is not None and temp_c < self.min_c:
            return self.min_c
        return None

    def describe(self) -> str:
        if self.min_c is not None and self.max_c is not None:
            return f"{self.min_c:g}–{self.max_c:g} °C"
        if self.max_c is not None:
            return f"at most {self.max_c:g} °C"
        return f"at least {self.min_c:g} °C"


@dataclass(frozen=True)
class Reading:
    ts: datetime
    temp_c: float


@dataclass(frozen=True)
class Finding:
    """A cold-chain event the readings establish. `observed_value` is the reading that
    completed the run of consecutive readings, `threshold` the bound involved, and
    `readings` the whole run (oldest first), for the audit row."""

    type: ColdChainEventType
    threshold: float
    observed_value: float
    ts: datetime
    readings: tuple[Reading, ...]

    @property
    def severity(self) -> Severity:
        return SEVERITY[self.type]


@dataclass(frozen=True)
class State:
    """Where a shipment stands before new readings: whether an excursion is still running
    (its last EXCURSION has no later RECOVERED) and the bound that excursion crossed."""

    in_excursion: bool = False
    excursion_threshold: float | None = None


def evaluate(
    band: Band,
    previous: Sequence[Reading],
    new: Sequence[Reading],
    state: State,
    *,
    consecutive: int | None = None,
) -> list[Finding]:
    """Run §11 over a shipment's `new` readings in timestamp order. `previous` are the
    shipment's readings just before them (already evaluated; only the last `consecutive - 1`
    matter).

    - EXCURSION when `consecutive` readings in a row are outside the band and no excursion
      is running. A single out-of-range reading creates nothing.
    - RECOVERED when, during an excursion, `consecutive` readings in a row are back in range.
      The excursion stays on record."""
    n = consecutive or config.COLDCHAIN_CONSECUTIVE_READINGS
    if not band.defined:
        return []
    window = sorted(previous, key=lambda r: r.ts)[-(n - 1) :] if n > 1 else []
    in_excursion, threshold = state.in_excursion, state.excursion_threshold
    findings: list[Finding] = []
    for reading in sorted(new, key=lambda r: r.ts):
        window = [*window, reading][-n:]
        if len(window) < n:
            continue
        if not in_excursion and all(not band.contains(r.temp_c) for r in window):
            bound = band.crossed(reading.temp_c)
            assert bound is not None
            findings.append(
                Finding(
                    ColdChainEventType.EXCURSION, bound, reading.temp_c, reading.ts, tuple(window)
                )
            )
            in_excursion, threshold = True, bound
        elif in_excursion and all(band.contains(r.temp_c) for r in window):
            bound = threshold if threshold is not None else _nearest(band, reading.temp_c)
            findings.append(
                Finding(
                    ColdChainEventType.RECOVERED, bound, reading.temp_c, reading.ts, tuple(window)
                )
            )
            in_excursion, threshold = False, None
    return findings


def _nearest(band: Band, temp_c: float) -> float:
    bounds = [b for b in (band.min_c, band.max_c) if b is not None]
    return min(bounds, key=lambda b: abs(b - temp_c))


def silence_start(last_seen: datetime | None, in_transit_at: datetime | None) -> datetime | None:
    """Since when no reading has arrived: the device's last reading or, for a device that
    has never sent one, the moment the shipment went IN_TRANSIT."""
    return last_seen or in_transit_at


def silent_finding(
    since: datetime | None,
    now: datetime,
    last_silent_at: datetime | None,
    *,
    limit: timedelta | None = None,
) -> Finding | None:
    """§11: DEVICE_SILENT once no reading has arrived for `limit` (2 min). Raised once per
    silence: not again while the newest DEVICE_SILENT (`last_silent_at`) is later than the
    last reading. `observed_value` and `threshold` are seconds."""
    limit = limit or config.COLDCHAIN_SILENT_AFTER
    if since is None or now - since < limit:
        return None
    if last_silent_at is not None and last_silent_at >= since:
        return None
    return Finding(
        ColdChainEventType.DEVICE_SILENT,
        threshold=limit.total_seconds(),
        observed_value=float(int((now - since).total_seconds())),
        ts=now,
        readings=(),
    )


def _at(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def describe(finding: Finding, band: Band, device_id: str, since: datetime | None = None) -> str:
    """The audit row's reason: the readings (or their absence) as received, nothing more."""
    run = ", ".join(f"{r.temp_c:g} °C at {_at(r.ts)}" for r in finding.readings)
    n = len(finding.readings)
    if finding.type == ColdChainEventType.EXCURSION:
        return f"{n} consecutive readings from {device_id} outside {band.describe()}: {run}."
    if finding.type == ColdChainEventType.RECOVERED:
        return (
            f"{n} consecutive readings from {device_id} back within {band.describe()}: {run}. "
            "The excursion stays on record."
        )
    last = f"since {_at(since)}" if since else "yet"
    return (
        f"No reading received from {device_id} {last} "
        f"({finding.observed_value:g} s; the limit is {finding.threshold:g} s)."
    )
