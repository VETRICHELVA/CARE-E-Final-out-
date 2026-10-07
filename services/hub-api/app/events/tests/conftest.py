"""S07 fixtures: an in-process SSE client and the committed database from S06's fixtures.

httpx's ASGITransport waits for the whole response body, so it cannot read an endless
stream; `SSE` drives the ASGI app directly and reads the body chunk by chunk."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, MutableMapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service as auth_service
from app.auth.models import User
from app.source_requests.tests.conftest import committed, committed_db_url  # noqa: F401

API = "/api/v1"


@dataclass
class Frame:
    """One parsed server-sent event; `comment` holds a `: ...` line (heartbeat)."""

    id: str | None = None
    event: str | None = None
    data: str | None = None
    comment: str | None = None

    @property
    def envelope(self) -> dict[str, Any]:
        assert self.data is not None
        result: dict[str, Any] = json.loads(self.data)
        return result


def parse(block: str) -> Frame:
    frame = Frame()
    for line in block.split("\n"):
        if line.startswith(":"):
            frame.comment = line[1:].strip()
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "id":
            frame.id = value
        elif name == "event":
            frame.event = value
        elif name == "data":
            frame.data = value if frame.data is None else f"{frame.data}\n{value}"
    return frame


@dataclass
class SSE:
    app: FastAPI
    path: str
    headers: dict[str, str]
    query: dict[str, str] = field(default_factory=dict)
    status: int = 0
    body: bytes = b""
    ended: bool = False
    _buffer: str = ""
    _queue: asyncio.Queue[MutableMapping[str, Any]] = field(default_factory=asyncio.Queue)
    _gone: asyncio.Event = field(default_factory=asyncio.Event)
    _sent_request: bool = False
    _task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "SSE":
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": API + self.path,
            "raw_path": (API + self.path).encode(),
            "query_string": urlencode(self.query).encode(),
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in self.headers.items()],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        }
        self._task = asyncio.create_task(self.app(scope, self._receive, self._send))
        start = await asyncio.wait_for(self._queue.get(), 5)
        assert start["type"] == "http.response.start"
        self.status = start["status"]
        if self.status != 200:  # an error answer: read its whole body
            while not self.ended:
                await self._chunk(5)
        return self

    async def __aexit__(self, *_: object) -> None:
        self._gone.set()
        assert self._task is not None
        await asyncio.wait_for(self._task, 5)

    async def _receive(self) -> dict[str, Any]:
        if not self._sent_request:
            self._sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._gone.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: MutableMapping[str, Any]) -> None:
        await self._queue.put(message)

    async def _chunk(self, wait: float) -> None:
        message = await asyncio.wait_for(self._queue.get(), wait)
        assert message["type"] == "http.response.body"
        chunk = message.get("body", b"")
        self.body += chunk
        self._buffer += chunk.decode()
        if not message.get("more_body", False):
            self.ended = True

    async def next(self, wait: float = 5) -> Frame:
        """The next frame, comments included; TimeoutError if none arrives in time."""
        async with asyncio.timeout(wait):
            while "\n\n" not in self._buffer:
                if self.ended:
                    raise EOFError("The stream ended.")
                await self._chunk(wait)
        block, _, self._buffer = self._buffer.partition("\n\n")
        return parse(block)

    async def next_event(self, wait: float = 5) -> Frame:
        """The next frame that is an event (skips comments)."""
        while True:
            frame = await self.next(wait)
            if frame.comment is None or frame.data is not None:
                return frame

    async def events_until(self, stop: Callable[[Frame], bool], wait: float = 5) -> list[Frame]:
        frames = []
        while True:
            frame = await self.next_event(wait)
            frames.append(frame)
            if stop(frame):
                return frames


async def bearer(session: AsyncSession, user: User) -> dict[str, str]:
    pair = await auth_service.issue_tokens(session, user)
    return {"Authorization": f"Bearer {pair.access_token}"}


StreamFor = Callable[..., SSE]


@pytest.fixture
async def stream_for(app: FastAPI, session: AsyncSession) -> AsyncIterator[StreamFor]:
    """`async with stream_for(headers, last_event_id=...) as sse:` opens /events/stream."""

    def make(headers: dict[str, str], **query: Any) -> SSE:
        return SSE(app, "/events/stream", headers, {k: str(v) for k, v in query.items()})

    yield make
