"""The Anthropic provider's request shape, against a stub client (no key, no network)."""

from types import SimpleNamespace
from typing import Any

import pytest
from app import tools
from app.config import Settings
from app.llm import AiNotConfigured, AnthropicConversation, ToolOutcome, echo, provider_from

pytestmark = pytest.mark.anyio


class StubMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = responses

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def block(**fields: Any) -> SimpleNamespace:
    return SimpleNamespace(**fields)


def stub_client(responses: list[Any]) -> tuple[Any, StubMessages]:
    messages = StubMessages(responses)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


SETTINGS = Settings(ai_api_key="sk-test", _env_file=None)  # type: ignore[call-arg]


async def test_requests_use_the_default_model_read_tools_and_append_only_history() -> None:
    tool_use = block(type="tool_use", id="tu_1", name="get_match_run", input={"shortage_id": "x"})
    thinking = block(type="thinking", thinking="", signature="sig")
    first = SimpleNamespace(stop_reason="tool_use", content=[thinking, tool_use])
    second = SimpleNamespace(
        stop_reason="end_turn", content=[block(type="text", text=" Expires in 12 days. ")]
    )
    client, messages = stub_client([first, second])
    conv = AnthropicConversation(client, SETTINGS, "system", "question", tools.TOOLS)

    turn = await conv.step()
    assert [u.name for u in turn.tool_uses] == ["get_match_run"]
    turn = await conv.step([ToolOutcome("tu_1", '{"run_no": 1}')], allow_tools=False)
    assert turn.text == "Expires in 12 days."

    one, two = messages.calls
    assert one["model"] == "claude-opus-5-5"
    assert one["thinking"] == {"type": "adaptive"}
    assert one["output_config"] == {"effort": "medium"}
    assert one["fallbacks"] == "default" and one["betas"] == ["server-side-fallback-2026-07-01"]
    assert one["tool_choice"] == {"type": "auto"} and two["tool_choice"] == {"type": "none"}
    assert [t["name"] for t in one["tools"]] == [t["name"] for t in tools.TOOLS]
    assert all(t["strict"] is True for t in one["tools"])
    assert one["system"][0]["text"] == "system"
    assert one["messages"] == [{"role": "user", "content": "question"}]
    # The assistant turn went back unchanged (thinking block included), then the result.
    assert two["messages"][1] == {"role": "assistant", "content": [thinking, tool_use]}
    assert two["messages"][2]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "tu_1",
            "content": '{"run_no": 1}',
            "is_error": False,
        }
    ]


async def test_a_refusal_is_flagged() -> None:
    client, _ = stub_client([SimpleNamespace(stop_reason="refusal", content=[])])
    conv = AnthropicConversation(client, SETTINGS, "s", "q", tools.TOOLS)
    assert (await conv.step()).refused


def test_no_key_no_provider() -> None:
    with pytest.raises(AiNotConfigured):
        provider_from(Settings(ai_api_key="", _env_file=None))  # type: ignore[call-arg]


async def test_after_a_mid_output_fallback_only_the_continuing_models_calls_count() -> None:
    declined_use = block(type="tool_use", id="tu_0", name="get_audit", input={})
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            block(type="thinking", thinking="", signature="a"),
            declined_use,
            block(type="fallback", **{"from": {"model": "x"}, "to": {"model": "y"}}),
            block(type="tool_use", id="tu_1", name="get_match_run", input={"shortage_id": "s"}),
        ],
    )
    client, messages = stub_client([response])
    conv = AnthropicConversation(client, SETTINGS, "s", "q", tools.TOOLS)
    turn = await conv.step()
    assert [u.id for u in turn.tool_uses] == ["tu_1"]
    assert [b.type for b in echo(response)] == ["fallback", "tool_use"]
