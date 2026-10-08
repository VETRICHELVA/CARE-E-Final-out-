"""The AI service API (api-and-events.md: POST /copilot/ask; S17 adds POST /chat/draft).

The caller is the signed-in user (hospital-web sends their access token). The service reads
the hub as that user and never writes anywhere."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app import copilot
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

    return app
