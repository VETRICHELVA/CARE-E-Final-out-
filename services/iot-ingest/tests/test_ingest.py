"""The subscriber and the 2 s flush, with a fake MQTT client and a fake hub (no broker)."""

import json
import threading
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest
from app.batcher import Batcher
from app.config import Settings
from app.hub import HubClient, Outcome
from app.ingest import Ingest, run
from app.telemetry import parse
from paho.mqtt.client import Client, MQTTMessage
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

TOKEN = "test-ingest-token"


def message(device: str = "cb-01", second: int = 0, temp: float = 4.31) -> MQTTMessage:
    msg = MQTTMessage(topic=f"careE/devices/{device}/telemetry".encode())
    ts = f"2026-11-20T10:15:{second:02d}Z"
    msg.payload = json.dumps(
        {"device_id": device, "ts": ts, "temp_c": temp, "battery": 82}
    ).encode()
    return msg


class FakeHub:
    """Answers POST /internal/telemetry like the hub; `status` can be changed per test."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        self.requests.append(request)
        n = len(json.loads(request.content)["readings"])
        if self.status != 200:
            return httpx.Response(self.status, json={"code": "x", "message": "x", "details": {}})
        return httpx.Response(200, json={"stored": n, "duplicates": 0, "unknown_devices": []})

    def client(self) -> HubClient:
        return HubClient("http://hub/api/v1", TOKEN, transport=httpx.MockTransport(self.handler))

    def posted(self) -> list[list[dict[str, Any]]]:
        return [json.loads(r.content)["readings"] for r in self.requests]


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()


@pytest.fixture
def ingest(hub: FakeHub) -> Ingest:
    return Ingest(Batcher(), hub.client(), max_batch=3)


def deliver(ingest: Ingest, *messages: MQTTMessage) -> None:
    for m in messages:
        ingest.on_message(None, None, m)


def test_posts_to_the_hub_with_the_ingest_token(hub: FakeHub, ingest: Ingest) -> None:
    deliver(ingest, message(second=0), message(second=10))
    assert ingest.flush() == 2

    (request,) = hub.requests
    assert request.method == "POST"
    assert str(request.url) == "http://hub/api/v1/internal/telemetry"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert hub.posted() == [
        [
            {"device_id": "cb-01", "ts": "2026-11-20T10:15:00Z", "temp_c": 4.31, "battery": 82},
            {"device_id": "cb-01", "ts": "2026-11-20T10:15:10Z", "temp_c": 4.31, "battery": 82},
        ]
    ]


def test_replayed_messages_are_posted_once(hub: FakeHub, ingest: Ingest) -> None:
    deliver(ingest, message(second=0), message(second=0), message(device="cb-02", second=0))
    ingest.flush()
    deliver(ingest, message(second=0))  # e.g. the firmware's offline buffer, re-sent
    assert ingest.flush() == 0

    assert [[r["device_id"] for r in batch] for batch in hub.posted()] == [["cb-01", "cb-02"]]


def test_invalid_messages_are_not_posted(hub: FakeHub, ingest: Ingest) -> None:
    bad = message()
    bad.payload = b'{"device_id":"cb-01","ts":"2026-11-20T10:15:00Z","temp_c":"warm"}'
    deliver(ingest, bad, message(device="cb-02", second=5))
    ingest.flush()
    assert [r["device_id"] for r in hub.posted()[0]] == ["cb-02"]


def test_large_queues_go_in_several_posts(hub: FakeHub, ingest: Ingest) -> None:
    deliver(ingest, *[message(second=s) for s in range(7)])
    assert ingest.flush() == 7
    assert [len(batch) for batch in hub.posted()] == [3, 3, 1]


def test_nothing_queued_means_no_request(hub: FakeHub, ingest: Ingest) -> None:
    assert ingest.flush() == 0
    assert hub.requests == []


@pytest.mark.parametrize("trouble", ["down", 503, 500, 429, 401])
def test_readings_are_kept_and_retried_while_the_hub_cannot_take_them(
    hub: FakeHub, ingest: Ingest, trouble: str | int
) -> None:
    deliver(ingest, *[message(second=s) for s in range(5)])
    if trouble == "down":
        hub.down = True
    else:
        hub.status = int(trouble)
    assert ingest.flush() == 0
    assert len(ingest.batcher) == 5

    hub.down, hub.status = False, 200
    hub.requests.clear()
    assert ingest.flush() == 5
    assert [r["ts"][-4:] for batch in hub.posted() for r in batch] == [
        f":{s:02d}Z" for s in range(5)
    ]


def test_a_batch_the_hub_rejects_is_dropped(hub: FakeHub, ingest: Ingest) -> None:
    deliver(ingest, message(second=0))
    hub.status = 422
    assert ingest.flush() == 0
    assert len(ingest.batcher) == 0  # resending would be refused again


@pytest.mark.parametrize(
    ("status", "outcome"),
    [(200, Outcome.SENT), (400, Outcome.REJECTED), (422, Outcome.REJECTED), (403, Outcome.RETRY)],
)
def test_hub_client_outcomes(hub: FakeHub, status: int, outcome: Outcome) -> None:
    reading = parse("careE/devices/cb-01/telemetry", message().payload)
    assert reading is not None
    hub.status = status
    assert hub.client().post([reading]) is outcome


def connack(name: str) -> ReasonCode:
    return ReasonCode(PacketTypes.CONNACK, name)


class FakeMqtt:
    """Stands in for paho's Client: loop_start() 'connects' and delivers `inbox`."""

    def __init__(self, inbox: list[MQTTMessage]) -> None:
        self.inbox = inbox
        self.subscriptions: list[tuple[str, int]] = []
        self.calls: list[str] = []
        self.on_connect: Callable[..., None] | None = None
        self.on_message: Callable[..., None] | None = None

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscriptions.append((topic, qos))

    def reconnect_delay_set(self, min_delay: int, max_delay: int) -> None:
        self.calls.append(f"backoff {min_delay}-{max_delay}s")

    def connect_async(self, host: str, port: int) -> None:
        self.calls.append(f"connect {host}:{port}")

    def loop_start(self) -> None:
        assert self.on_connect and self.on_message
        self.on_connect(self, None, None, connack("Success"), None)
        for m in self.inbox:
            self.on_message(self, None, m)

    def loop_stop(self) -> None:
        self.calls.append("loop_stop")

    def disconnect(self) -> None:
        self.calls.append("disconnect")


def test_on_connect_subscribes_and_a_refused_connect_does_not(ingest: Ingest) -> None:
    fake = FakeMqtt([])
    ingest.on_connect(fake, None, None, connack("Not authorized"), None)
    assert fake.subscriptions == []
    ingest.on_connect(fake, None, None, connack("Success"), None)
    assert fake.subscriptions == [("careE/devices/+/telemetry", 1)]


def test_run_subscribes_flushes_on_its_interval_and_once_more_on_stop(hub: FakeHub) -> None:
    stop = threading.Event()
    fake = FakeMqtt([message(second=0), message(second=0), message(second=10)])
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        mqtt_host="broker",
        mqtt_port=1884,
        flush_interval_s=0.01,
    )
    calls: list[str] = []

    def stop_after_first_post(request: httpx.Request) -> httpx.Response:
        calls.append("post")
        stop.set()
        return hub.handler(request)

    client = HubClient("http://hub/api/v1", TOKEN, httpx.MockTransport(stop_after_first_post))
    run(settings, stop, make_client=lambda: cast(Client, fake), hub=client)

    assert fake.subscriptions == [("careE/devices/+/telemetry", 1)]
    assert fake.calls == ["backoff 1-30s", "connect broker:1884", "loop_stop", "disconnect"]
    assert calls == ["post"]
    assert [r["ts"] for r in hub.posted()[0]] == ["2026-11-20T10:15:00Z", "2026-11-20T10:15:10Z"]
