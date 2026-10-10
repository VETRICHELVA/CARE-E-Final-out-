"""Structured JSON logs and the X-Request-ID middleware."""

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
log = logging.getLogger("app")

_STD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id.get(),
        }
        entry |= {k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS}
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


# The SSE stream ticket rides in the query string (EventSource cannot send headers); uvicorn's
# access log would print it. It expires after 60 s and opens only the stream, but it is still
# a credential, so it never reaches a log line (S20).
_SECRET_PARAMS = re.compile(r"([?&]ticket=)[^&#\s]*", re.IGNORECASE)


def redact(path: str) -> str:
    return _SECRET_PARAMS.sub(r"\1[redacted]", path)


class RedactQuerySecrets(logging.Filter):
    """For uvicorn's access logger, whose args are (client, method, path, version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install(app: FastAPI) -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactQuerySecrets) for f in access.filters):
        access.addFilter(RedactQuerySecrets())

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            return response
        finally:
            request_id.reset(token)
