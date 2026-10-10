"""The AI service API (api-and-events.md): POST /copilot/ask (S13), POST /chat/draft (S17).

The caller is the signed-in user (hospital-web sends their access token). The service reads
the hub as that user and never writes anywhere."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app import chat, copilot
from app.config import Settings, settings
from app.hub import HubReader, HubUnauthenticated, HubUnavailable
from app.llm import AiNotConfigured, AiUnavailable, Provider, provider_from

bearer = HTTPBearer(auto_error=False)


class AskContext(BaseModel):
    """The ids of the screen the user is on; unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore")

    shortage_id: str | None = None
    recommendation_id: str | None = None
    shipment_id: str | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    context: AskContext = Field(default_factory=AskContext)


class TraceOut(BaseModel):
    tool: str
    input: dict[str, Any]
    ok: bool
    label: str = Field(description='"Based on:" chip text, e.g. "match run #3".')
    result: Any = Field(description="What the model was given: the hub's answer or an error.")
    from_context: bool = Field(description="Read from the screen context before the model ran.")


class AskOut(BaseModel):
    answer: str
    tool_trace: list[TraceOut]


class DraftIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    user_tz: str = Field(description='IANA zone the user is in, e.g. "Asia/Kolkata".')
    now: AwareDatetime | None = Field(
        default=None, description="The instant relative dates anchor on; default the server's."
    )

    @field_validator("user_tz")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError("user_tz must be an IANA time zone, e.g. Asia/Kolkata") from e
        return v


class DraftOut(BaseModel):
    """A draft only: the hospital app shows it on a card, and only the user's click sends
    POST /shortages to the hub, as the user. This service never writes."""

    draft: chat.DraftOut | None
    missing_fields: list[str] = Field(
        description="Fields the user did not give, including defaulted ones (priority, "
        "min_shelf_life_days, qty_local_usable), so the card can highlight them."
    )
    product_candidates: list[chat.CandidateOut] = Field(
        description="Set when the product is ambiguous: the user picks one; none is chosen."
    )
    question: str | None
    assumptions: list[str]
    tool_trace: list[TraceOut]


def _error(status: int, error: str, message: str) -> JSONResponse:
    return JSONResponse({"error": error, "message": message}, status_code=status)


NOT_CONFIGURED = {"error": "ai_not_configured", "message": "AI is not configured."}


def create_app(
    config: Settings | None = None,
    *,
    provider: Provider | None = None,
    hub: HubReader | None = None,
) -> FastAPI:
    """`provider` and `hub` are injected by tests; otherwise they come from `config`."""
    cfg = config or settings
    reader = hub or HubReader(
        cfg.hub_api_url,
        cfg.ai_service_token,
        client=httpx.AsyncClient(timeout=cfg.hub_timeout_seconds),
    )
    model: Provider | None = provider
    if model is None:
        try:
            model = provider_from(cfg)
        except AiNotConfigured:
            model = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await reader.aclose()

    app = FastAPI(title="CARE-E AI service", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    async def status() -> dict[str, Any]:
        """Whether the copilot can answer, so the panel can say so before anyone asks."""
        return {"configured": model is not None, "model": cfg.ai_model if model else None}

    @app.post("/copilot/ask", response_model=AskOut)
    async def ask(
        body: AskIn,
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Any:
        """Answer a question from hub data read as the caller. 503 `ai_not_configured` when
        no model is set up (nothing else in CARE-E depends on this service)."""
        if model is None:
            return JSONResponse(NOT_CONFIGURED, status_code=503)
        if creds is None:
            return _error(401, "unauthenticated", "Sign in first.")
        try:
            result = await copilot.ask(
                body.question,
                body.context.model_dump(exclude_none=True),
                user_token=creds.credentials,
                hub=reader,
                provider=model,
                max_tool_calls=cfg.ai_max_tool_calls,
            )
        except HubUnauthenticated:
            return _error(401, "unauthenticated", "Sign in again.")
        except HubUnavailable:
            return _error(502, "hub_unavailable", "The hub could not be reached.")
        except AiUnavailable as e:
            return _error(502, "ai_unavailable", str(e))
        return AskOut(
            answer=result.answer,
            tool_trace=[TraceOut(**asdict(t)) for t in result.tool_trace],
        )

    @app.post("/chat/draft", response_model=DraftOut)
    async def chat_draft(
        body: DraftIn,
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Any:
        """Chat ordering (S17): a pre-filled shortage card from one message. Product search
        reads the hub as the caller; nothing is created here."""
        if model is None:
            return JSONResponse(NOT_CONFIGURED, status_code=503)
        if creds is None:
            return _error(401, "unauthenticated", "Sign in first.")
        try:
            result = await chat.draft(
                body.message,
                user_tz=body.user_tz,
                now=body.now or datetime.now(UTC),
                user_token=creds.credentials,
                hub=reader,
                provider=model,
            )
        except HubUnauthenticated:
            return _error(401, "unauthenticated", "Sign in again.")
        except HubUnavailable:
            return _error(502, "hub_unavailable", "The hub could not be reached.")
        except AiUnavailable as e:
            return _error(502, "ai_unavailable", str(e))
        return DraftOut(
            draft=result.draft,
            missing_fields=result.missing_fields,
            product_candidates=result.product_candidates,
            question=result.question,
            assumptions=result.assumptions,
            tool_trace=[TraceOut(**asdict(t)) for t in result.tool_trace],
        )

    return app
