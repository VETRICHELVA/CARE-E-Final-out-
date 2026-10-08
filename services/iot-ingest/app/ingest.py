"""MQTT -> hub: subscribe to every cold box's telemetry, validate, de-duplicate, and post
batches to the hub every 2 s. The hub stores the readings and runs the cold-chain rules."""

import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

from paho.mqtt.client import Client, MQTTMessage
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.reasoncodes import ReasonCode

from app.batcher import Batcher
from app.config import Settings
from app.hub import HubClient, Outcome
from app.telemetry import TOPIC_FILTER, Reading, parse

log = logging.getLogger("iot_ingest")


class Subscriber(Protocol):
    def subscribe(self, topic: str, qos: int = 0) -> Any: ...


class Ingest:
    def __init__(self, batcher: Batcher, hub: HubClient, max_batch: int = 500) -> None:
        self.batcher, self.hub, self.max_batch = batcher, hub, max_batch

    # paho callbacks (CallbackAPIVersion.VERSION2); they run on paho's network thread.
    def on_connect(
        self, client: Subscriber, userdata: Any, flags: Any, reason: ReasonCode, props: Any
    ) -> None:
        if reason.is_failure:
            log.error("MQTT connect failed: %s", reason)
            return
        client.subscribe(TOPIC_FILTER, qos=1)  # (re)subscribe on every (re)connect
        log.info("subscribed to %s", TOPIC_FILTER)

    def on_message(self, client: Any, userdata: Any, message: MQTTMessage) -> None:
        reading = parse(message.topic, message.payload)
        if reading is not None:
            self.batcher.add(reading)

    def flush(self) -> int:
        """Post everything queued, `max_batch` readings per request. Stops at the first batch
        the hub can't take right now and keeps it (and the rest) for the next flush.
        Returns the number of readings the hub accepted."""
        sent = 0
        while batch := self.batcher.take(self.max_batch):
            accepted, left = self._post(batch)
            sent += accepted
            if left:
                self.batcher.put_back(left)
                break
        return sent

    def _post(self, batch: list[Reading]) -> tuple[int, list[Reading]]:
        """(readings accepted, readings to retry). A rejected batch is split in halves until
        only the readings the hub refuses on their own are dropped."""
        outcome = self.hub.post(batch)
        if outcome is Outcome.SENT:
            return len(batch), []
        if outcome is Outcome.RETRY:
            return 0, batch
        if len(batch) == 1:
            return 0, []  # the hub will never take this reading (logged by HubClient)
        mid = len(batch) // 2
        first, retry = self._post(batch[:mid])
        if retry:
            return first, retry + batch[mid:]
        second, retry = self._post(batch[mid:])
        return first + second, retry


def run(
    settings: Settings,
    stop: threading.Event,
    make_client: Callable[[], Client] | None = None,
    hub: HubClient | None = None,
) -> None:
    """Connect (paho retries with backoff in its own thread) and flush every interval until
    `stop` is set; then flush once more."""
    hub = hub or HubClient(settings.hub_api_url, settings.ingest_token)
    ingest = Ingest(Batcher(settings.max_pending, settings.dedup_window), hub, settings.max_batch)
    client = (make_client or _default_client)()
    client.on_connect = ingest.on_connect
    client.on_message = ingest.on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(settings.mqtt_host, settings.mqtt_port)
    client.loop_start()
    log.info(
        "iot-ingest: MQTT %s:%d -> %s", settings.mqtt_host, settings.mqtt_port, settings.hub_api_url
    )
    try:
        while not stop.wait(settings.flush_interval_s):
            ingest.flush()
    finally:
        client.loop_stop()
        client.disconnect()
        ingest.flush()
        hub.close()


def _default_client() -> Client:
    return Client(CallbackAPIVersion.VERSION2, client_id="iot-ingest")
