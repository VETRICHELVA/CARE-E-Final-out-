"""Who may call GET /ai/read/* (S13), and what the AI service token may call: nothing else.

The AI service authenticates with its own token (scope `ai.read`) and names the user it acts
for by passing that user's access token in `X-On-Behalf-Of`. The hub then answers as that
user (their org, their capabilities, the same redaction as their own endpoints), so the AI can
never see more than the person asking. The token itself opens no other route and no method
but GET: `AiTokenGuard` refuses it before routing, so a route added later cannot forget to."""

from typing import Annotated

from fastapi import Depends, Header
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.capabilities import ServiceScope
from app.auth.deps import bearer, service_token_scopes, user_from_claims
from app.auth.models import User
from app.auth.service import decode_access_token, unauthenticated
from app.db import SessionDep

AI_READ_PREFIX = "/api/v1/ai/read/"
ON_BEHALF_OF = "X-On-Behalf-Of"
REFUSED = "The AI service token opens only GET /ai/read/*."


def _bearer(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token.strip() if scheme.lower() == "bearer" else None
    return None


class AiTokenGuard:
    """ASGI middleware: a request bearing the AI service token gets 401 unless it is a GET
    (or HEAD) under /api/v1/ai/read/. Pure ASGI, so streamed responses pass untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            token = _bearer(scope)
            if token and ServiceScope.AI_READ in service_token_scopes(token):
                allowed = scope["method"] in ("GET", "HEAD") and scope["path"].startswith(
                    AI_READ_PREFIX
                )
                if not allowed:
                    body = {"code": "unauthenticated", "message": REFUSED, "details": {}}
                    await JSONResponse(jsonable_encoder(body), status_code=401)(
                        scope, receive, send
                    )
                    return
        await self.app(scope, receive, send)


async def ai_reader(
    session: SessionDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    on_behalf_of: Annotated[
        str | None,
        Header(
            alias=ON_BEHALF_OF,
            description="The signed-in user's access token (with or without 'Bearer '); the "
            "hub answers as that user.",
        ),
    ] = None,
) -> User:
    """401 unless the bearer is the AI service token and `X-On-Behalf-Of` holds a live access
    token; returns that token's user. User tokens alone are refused here."""
    if creds is None or ServiceScope.AI_READ not in service_token_scopes(creds.credentials):
        raise unauthenticated("This endpoint needs the AI service token (scope ai.read).")
    token = (on_behalf_of or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise unauthenticated(f"{ON_BEHALF_OF} must carry the signed-in user's access token.")
    return await user_from_claims(session, decode_access_token(token))


AiUser = Annotated[User, Depends(ai_reader)]
