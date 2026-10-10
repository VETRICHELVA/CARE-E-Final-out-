"""LLM providers behind one small interface, chosen by AI_PROVIDER (apps-ai-iot.md).

A provider opens a conversation; the copilot steps it, passing tool results back, until the
model answers in text. Unit tests use a scripted fake (tests/fakes.py); `anthropic` is the
only real provider. The model never gets a write tool: every tool is a hub GET (app.tools)."""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import anthropic
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaMessageParam,
    BetaTextBlockParam,
    BetaToolChoiceParam,
    BetaToolParam,
)

from app.config import Settings


@dataclass
class ModelToolUse:
    id: str
    name: str
    input: Any


@dataclass
class ModelTurn:
    text: str
    tool_uses: list[ModelToolUse] = field(default_factory=list)
    refused: bool = False


@dataclass
class ToolOutcome:
    tool_use_id: str
    content: str  # JSON text
    is_error: bool = False


class Conversation(Protocol):
    async def step(
        self,
        tool_outcomes: list[ToolOutcome] | None = None,
        *,
        followup: str | None = None,
        allow_tools: bool = True,
    ) -> ModelTurn:
        """Send the tool outcomes for the last turn (and/or a follow-up user note) and get
        the model's next turn. `allow_tools=False` makes it answer without calling tools."""
        ...


class Provider(Protocol):
    def start(self, system: str, user_text: str, tools: list[dict[str, Any]]) -> Conversation: ...

    async def extract(
        self, system: str, user_text: str, schema: dict[str, Any]
    ) -> dict[str, Any] | None:
        """One structured reply (JSON matching `schema`), no tools: chat ordering's field
        extraction (S17). None if the model declined."""
        ...


class AiNotConfigured(Exception):
    pass


class AiUnavailable(Exception):
    """The model API failed (rate limit, outage, rejected key)."""


# --- Anthropic ----------------------------------------------------------------------------------

# Server-side fallback when a safety classifier declines (beta "default" routing).
FALLBACK_BETA = "server-side-fallback-2026-07-01"


DECLINED_PARTIAL = {"thinking", "redacted_thinking", "tool_use"}


def unavailable(e: anthropic.APIError) -> AiUnavailable:
    """The model API's failure as the service reports it (no key or message text echoed)."""
    if isinstance(e, anthropic.AuthenticationError):
        return AiUnavailable("The AI provider rejected the API key.")
    if isinstance(e, anthropic.RateLimitError):
        return AiUnavailable("The AI provider is rate limiting requests.")
    if isinstance(e, anthropic.APIStatusError):
        return AiUnavailable(f"The AI provider returned {e.status_code}.")
    return AiUnavailable("The AI provider could not be reached.")


def echo(response: Any) -> list[Any]:
    """The assistant content to send back next turn. After a mid-output fallback, the declined
    model's thinking and tool calls before the last `fallback` block are left out (only the
    model that continued may be answered); everything else goes back unchanged."""
    content = list(response.content)
    marks = [i for i, b in enumerate(content) if b.type == "fallback"]
    if not marks:
        return content
    return [b for i, b in enumerate(content) if i > marks[-1] or b.type not in DECLINED_PARTIAL]


class AnthropicConversation:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        settings: Settings,
        system: str,
        user_text: str,
        tools: list[dict[str, Any]],
    ) -> None:
        self._client, self._settings = client, settings
        self._system: list[BetaTextBlockParam] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        self._tools = [cast(BetaToolParam, {**t, "strict": True}) for t in tools]
        # Append-only history: each assistant turn goes back exactly as received.
        self._messages: list[BetaMessageParam] = [{"role": "user", "content": user_text}]
        self._started = False

    async def step(
        self,
        tool_outcomes: list[ToolOutcome] | None = None,
        *,
        followup: str | None = None,
        allow_tools: bool = True,
    ) -> ModelTurn:
        content: list[BetaContentBlockParam] = [
            {
                "type": "tool_result",
                "tool_use_id": o.tool_use_id,
                "content": o.content,
                "is_error": o.is_error,
            }
            for o in tool_outcomes or []
        ]
        if followup:
            content.append({"type": "text", "text": followup})
        if self._started and content:
            self._messages.append({"role": "user", "content": content})
        self._started = True
        choice: BetaToolChoiceParam = {"type": "auto"} if allow_tools else {"type": "none"}
        try:
            response = await self._client.beta.messages.create(
                model=self._settings.ai_model,
                max_tokens=16000,
                system=self._system,
                messages=self._messages,
                tools=self._tools,
                tool_choice=choice,
                thinking={"type": "adaptive"},
                output_config={"effort": cast(Any, self._settings.ai_effort)},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as e:
            raise unavailable(e) from e
        # The SDK accepts its own response blocks as input; thinking blocks must go back as is.
        blocks = echo(response)
        self._messages.append(
            {"role": "assistant", "content": cast(list[BetaContentBlockParam], blocks)}
        )
        if response.stop_reason == "refusal":
            return ModelTurn(text="", refused=True)
        text = "".join(b.text for b in blocks if b.type == "text")
        uses = [
            ModelToolUse(id=b.id, name=b.name, input=b.input)
            for b in blocks
            if b.type == "tool_use"
        ]
        return ModelTurn(text=text.strip(), tool_uses=uses)


class AnthropicProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = anthropic.AsyncAnthropic(api_key=settings.ai_api_key, timeout=120.0)

    def start(self, system: str, user_text: str, tools: list[dict[str, Any]]) -> Conversation:
        return AnthropicConversation(self._client, self._settings, system, user_text, tools)

    async def extract(
        self, system: str, user_text: str, schema: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Structured outputs (`output_config.format`): the reply is JSON matching `schema`."""
        try:
            response = await self._client.beta.messages.create(
                model=self._settings.ai_model,
                max_tokens=16000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_text}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": cast(Any, self._settings.ai_chat_effort),
                    "format": {"type": "json_schema", "schema": schema},
                },
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as e:
            raise unavailable(e) from e
        if response.stop_reason == "refusal":
            return None
        if response.stop_reason == "max_tokens":
            raise AiUnavailable("The AI provider's reply was cut off.")
        # After a fallback, only the model that continued wrote the reply.
        content = list(response.content)
        marks = [i for i, b in enumerate(content) if b.type == "fallback"]
        text = "".join(b.text for b in content[marks[-1] + 1 if marks else 0 :] if b.type == "text")
        try:
            data = json.loads(text)
        except ValueError as e:
            raise AiUnavailable("The AI provider returned an unreadable reply.") from e
        if not isinstance(data, dict):
            raise AiUnavailable("The AI provider returned an unreadable reply.")
        return data


def provider_from(settings: Settings) -> Provider:
    """The configured provider; AiNotConfigured when there is no key or no known provider."""
    if not settings.configured:
        raise AiNotConfigured()
    return AnthropicProvider(settings)
