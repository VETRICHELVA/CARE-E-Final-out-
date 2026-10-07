"""The queue between the MQTT thread and the 2 s flush: de-duplicates on (device_id, ts)."""

import logging
import threading
from collections import OrderedDict, deque
from collections.abc import Sequence
from datetime import datetime

from app.telemetry import Reading

log = logging.getLogger("iot_ingest")


class Batcher:
    """Thread-safe. Remembers the last `dedup_window` keys it accepted, so a message the
    firmware re-sends from its offline buffer, or the broker delivers twice, is queued once.
    The hub's unique (device_id, ts) still catches anything older than the window."""

    def __init__(self, max_pending: int = 10_000, dedup_window: int = 50_000) -> None:
        self._lock = threading.Lock()
        self._pending: deque[Reading] = deque()
        self._seen: OrderedDict[tuple[str, datetime], None] = OrderedDict()
        self._max_pending = max_pending
        self._dedup_window = dedup_window
        self.dropped = 0  # readings discarded because the queue was full

    def add(self, reading: Reading) -> bool:
        """Queue `reading` unless its key was seen; True if it was queued."""
        with self._lock:
            if reading.key in self._seen:
                return False
            self._seen[reading.key] = None
            if len(self._seen) > self._dedup_window:
                self._seen.popitem(last=False)
            self._pending.append(reading)
            self._trim()
            return True

    def take(self, n: int) -> list[Reading]:
        """Remove and return up to `n` readings, oldest first."""
        with self._lock:
            return [self._pending.popleft() for _ in range(min(n, len(self._pending)))]

    def put_back(self, readings: Sequence[Reading]) -> None:
        """Return readings the hub didn't take to the front of the queue, to retry them."""
        with self._lock:
            self._pending.extendleft(reversed(readings))
            self._trim()

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    def _trim(self) -> None:
        excess = len(self._pending) - self._max_pending
        if excess > 0:
            for _ in range(excess):
                self._pending.popleft()
            self.dropped += excess
            log.warning("queue full: dropped the %d oldest readings", excess)
