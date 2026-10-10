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
    """Where a shipment stands, from its last EXCURSION or RECOVERED on record: whether an
    excursion is still running (its last EXCURSION has no later RECOVERED), the bound that
    excursion crossed, and `as_of`, that event's timestamp (None when there is none)."""

    in_excursion: bool = False
    excursion_threshold: float | None = None
    as_of: datetime | None = None


def evaluate(
    band: Band,
    previous: Sequence[Reading],
    new: Sequence[Reading],
    state: State,
    *,
    consecutive: int | None = None,
) -> list[Finding]:
    """Run §11 over a shipment's `new` readings in timestamp order, from `state`. `previous`
    are the shipment's readings just before them (only the last `consecutive - 1` matter:
    they start the run of consecutive readings). `on_arrival` picks both for a batch.

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


def resume_from(state: State, earliest_new: datetime) -> tuple[datetime, bool]:
    """Where §11 picks up for a batch whose earliest reading is `earliest_new`: the readings
    from `(ts, inclusive)` on, in timestamp order, are evaluated from `state`; the ones
    before only fill the run of consecutive readings that leads into them.

    - Usually the batch's earliest reading (inclusive). Everything before it was evaluated
      when it arrived, and found nothing after the last event on record.
    - When the batch reaches back to or before the last EXCURSION or RECOVERED on record
      (`state.as_of`), just after that event (exclusive), re-reading every later reading
      with the late ones merged in. Events on record are never revised (CLAUDE.md rule 5),
      so a late reading dated at or before the last event changes nothing before it."""
    if state.as_of is not None and earliest_new <= state.as_of:
        return state.as_of, False
    return earliest_new, True


def on_arrival(
    band: Band,
    stored: Sequence[Reading],
    new: Sequence[Reading],
    state: State,
    *,
    consecutive: int | None = None,
) -> list[Finding]:
    """§11 for one ingested batch. `stored` is the shipment's readings (the new ones
    included; any superset of the ones from `resume_from` on and the `consecutive - 1`
    before them), `new` the batch's newly stored ones, `state` the shipment's last
    EXCURSION or RECOVERED on record.

    Every finding is dated after `state.as_of` and alternates with the events on record, so
    recording them never contradicts what is already recorded. The events recorded are those
    of the readings in timestamp order, whatever their arrival order or batching, as long as
    no batch reaches back to or before the last EXCURSION or RECOVERED on record. A batch
    that does is evaluated only after that event: an excursion or recovery its readings would
    have shown before it is not recorded (the events on record stand), and its readings
    count only as the run leading into later readings. Replays store nothing, so they find
    nothing."""
    if not new:
        return []
    start, inclusive = resume_from(state, min(r.ts for r in new))

    def pending(r: Reading) -> bool:
        return r.ts > start or (inclusive and r.ts == start)

    return evaluate(
        band,
        [r for r in stored if not pending(r)],
        [r for r in stored if pending(r)],
        state,
        consecutive=consecutive,
    )


def _nearest(band: Band, temp_c: float) -> float:
    bounds = [b for b in (band.min_c, band.max_c) if b is not None]
    return min(bounds, key=lambda b: abs(b - temp_c))


def silence_start(last_seen: datetime | None, in_transit_at: datetime | None) -> datetime | None:
    """§11 counts silence only while the shipment is IN_TRANSIT: from the later of the
    device's last reading and the moment the shipment went IN_TRANSIT (a box last heard
    from before the trip is silent from the start of the trip, not from then)."""
    known = [t for t in (last_seen, in_transit_at) if t is not None]
    return max(known) if known else None


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


def describe(
    finding: Finding,
    band: Band,
    device_id: str,
    since: datetime | None = None,
    *,
    in_transit_at: datetime | None = None,
) -> str:
    """The audit row's reason: the readings (or their absence) as received, nothing more.
    A DEVICE_SILENT counted from the moment the shipment went IN_TRANSIT (`since` is
    `in_transit_at`) says so."""
    run = ", ".join(f"{r.temp_c:g} °C at {_at(r.ts)}" for r in finding.readings)
    n = len(finding.readings)
    if finding.type == ColdChainEventType.EXCURSION:
        return f"{n} consecutive readings from {device_id} outside {band.describe()}: {run}."
    if finding.type == ColdChainEventType.RECOVERED:
        return (
            f"{n} consecutive readings from {device_id} back within {band.describe()}: {run}. "
            "The excursion stays on record."
        )
    if since is not None and since == in_transit_at:
        last = f"since the shipment went IN_TRANSIT at {_at(since)}"
    else:
        last = f"since {_at(since)}" if since else "yet"
    return (
        f"No reading received from {device_id} {last} "
        f"({finding.observed_value:g} s; the limit is {finding.threshold:g} s)."
    )
