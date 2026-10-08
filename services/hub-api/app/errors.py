"""Every error response is `{code, message, details}` (api-and-events.md, Conventions)."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, DBAPIError
from starlette.exceptions import HTTPException

from app.domain.state_machine import InvalidTransition

log = logging.getLogger("app")

# Codes documented in api-and-events.md; any other status uses its HTTP phrase.
STATUS_CODES = {
    400: "validation",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "schema_error",
    429: "rate_limited",
}


BAD_VALUE = "A value is out of range or cannot be stored."


def is_data_error(exc: DBAPIError) -> bool:
    """SQLSTATE class 22 ("data exception", the DB-API DataError): a value the database
    cannot store, such as text with NUL or an int4 overflow. asyncpg raises these as a
    plain DBAPIError, so the SQLSTATE is checked as well as the class."""
    sqlstate = str(getattr(exc.orig, "sqlstate", None) or "")
    return isinstance(exc, DataError) or sqlstate.startswith("22")


class AppError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def error(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> Any:
    # jsonable_encoder: details may hold UUIDs and datetimes.
    return JSONResponse(
        {"code": code, "message": message, "details": jsonable_encoder(details or {})},
        status_code=status,
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> Any:
        return error(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(InvalidTransition)
    async def invalid_transition(_: Request, exc: InvalidTransition) -> Any:
        details = {"from": exc.from_state, "to": exc.to_state}
        return error(409, "invalid_transition", str(exc), details)

    @app.exception_handler(DBAPIError)
    async def database_error(_: Request, exc: DBAPIError) -> Any:
        if not is_data_error(exc):
            raise exc  # any other database error is a bug: the 500 handler logs it
        return error(400, "validation", BAD_VALUE)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> Any:
        phrase = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
        code = STATUS_CODES.get(exc.status_code, phrase)
        return error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def schema_error(_: Request, exc: RequestValidationError) -> Any:
        errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return error(422, "schema_error", "Request does not match the schema.", {"errors": errors})

    @app.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception) -> Any:
        log.exception("unhandled error", exc_info=exc)
        return error(500, "internal_error", "Something went wrong.")
