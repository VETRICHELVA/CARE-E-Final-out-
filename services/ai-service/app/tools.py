"""The copilot's tools (apps-ai-iot.md, Copilot): read-only, one hub GET each.

A tool result is what the model sees and what the number check compares the answer against,
so it is the hub's JSON with two display aids added deterministically: floats rounded to two
decimals, and a `*_rupees` string next to each `*_paise` amount."""

import uuid
from dataclasses import dataclass
from typing import Any

from app.hub import HubReader

UUID_PROP = {"type": "string", "description": "A UUID from the screen context or a tool result."}


def _schema(**props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_shortage",
        "description": "A shortage of the user's hospital: product, quantities, hub-computed "
        "shortfall, priority, status, required_by, its source requests (with status and any "
        "decline reason), recommendations, shipments and residual shortages.",
        "input_schema": _schema(shortage_id=UUID_PROP),
    },
    {
        "name": "get_match_run",
        "description": "The latest match run of a shortage: eligible candidates by rank, then "
        "rejected ones, each with every gate's result and the hub's reason for each failure, "
        "plus the planned resolution and the orgs excluded after a decline.",
        "input_schema": _schema(shortage_id=UUID_PROP),
    },
    {
        "name": "get_candidate",
        "description": "One candidate (a source checked in a match run) by its id.",
        "input_schema": _schema(candidate_id=UUID_PROP),
    },
    {
        "name": "get_recommendation",
        "description": "A recommendation: type (TRANSFER, TRANSFER_SPLIT, BUY), status, lines "
        "and alternatives with qty and ETA, supplier costs, and the hub's explanation.",
        "input_schema": _schema(recommendation_id=UUID_PROP),
    },
    {
        "name": "get_shipment",
        "description": "A shipment: from, to, carrier, qty, status and status history, ETA, "
        "driver and vehicle, and for the receiving hospital its receipt and reconciliation.",
        "input_schema": _schema(shipment_id=UUID_PROP),
    },
    {
        "name": "get_coldchain_events",
        "description": "A shipment's cold-chain record: whether it needs cold chain, its "
        "temperature readings summary and excursion events.",
        "input_schema": _schema(shipment_id=UUID_PROP),
    },
    {
        "name": "get_audit",
        "description": "Audit rows (oldest first) with action, before/after, reason and "
        "reason_source (USER typed it, SYSTEM recorded it). entity='shortage' returns the "
        "shortage's whole trail: match runs, source requests, recommendations, orders, "
        "shipments, receipts.",
        "input_schema": _schema(
            entity={
                "type": "string",
                "enum": [
                    "shortage",
                    "match_run",
                    "source_request",
                    "recommendation",
                    "purchase_order",
                    "shipment",
                ],
            },
            entity_id=UUID_PROP,
        ),
    },
]
TOOL_NAMES = {t["name"] for t in TOOLS}

# Screen context key -> the tool that reads it (fetched before the model is asked).
CONTEXT_TOOLS = {
    "shortage_id": "get_shortage",
    "recommendation_id": "get_recommendation",
    "shipment_id": "get_shipment",
}


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    ok: bool
    status: int
    result: Any  # what the model was given
    label: str
    error: str | None = None


def _route(name: str, args: dict[str, str]) -> tuple[str, dict[str, str] | None]:
    match name:
        case "get_shortage":
            return f"shortages/{args['shortage_id']}", None
        case "get_match_run":
            return f"shortages/{args['shortage_id']}/match-run", None
        case "get_candidate":
            return f"candidates/{args['candidate_id']}", None
        case "get_recommendation":
            return f"recommendations/{args['recommendation_id']}", None
        case "get_shipment":
            return f"shipments/{args['shipment_id']}", None
        case "get_coldchain_events":
            return f"shipments/{args['shipment_id']}/coldchain", None
        case "get_audit":
            return "audit", {"entity": args["entity"], "entity_id": args["entity_id"]}
    raise KeyError(name)


def _label(name: str, data: Any) -> str:
    """The "Based on:" chip for a successful call, e.g. "match run #3"."""
    d = data if isinstance(data, dict) else {}
    match name:
        case "get_shortage":
            return f"shortage: {d.get('product_name', 'shortage')}"
        case "get_match_run":
            return f"match run #{d.get('run_no', '?')}"
        case "get_candidate":
            return f"candidate {d.get('source_org_name', '')}".strip()
        case "get_recommendation":
            return f"recommendation ({d.get('type', '')})"
        case "get_shipment":
            return f"shipment from {d.get('from_org_name', '')}".strip()
        case "get_coldchain_events":
            return "cold-chain record"
        case "get_audit":
            return "audit trail"
    return name


def display(value: Any) -> Any:
    """The hub's JSON with floats rounded to 2 places and `*_rupees` beside `*_paise`."""
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, list):
        return [display(v) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out[k] = display(v)
            if k.endswith("_paise") and isinstance(v, int) and not isinstance(v, bool):
                out[f"{k.removesuffix('_paise')}_rupees"] = f"{v / 100:.2f}"
        return out
    return value


def _invalid(name: str, args: Any) -> str | None:
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return f"Unknown tool {name}."
    if not isinstance(args, dict):
        return "Tool input must be an object."
    schema = tool["input_schema"]
    for key in schema["required"]:
        value = args.get(key)
        if not isinstance(value, str):
            return f"{key} is required."
        prop = schema["properties"][key]
        if "enum" in prop:
            if value not in prop["enum"]:
                return f"{key} must be one of {', '.join(prop['enum'])}."
            continue
        try:
            uuid.UUID(value)
        except ValueError:
            return f"{key} must be a UUID."
    return None


async def run(hub: HubReader, user_token: str, name: str, args: Any) -> ToolCall:
    """Call one tool. A bad input or a hub 403/404 is a failed call the model is told about
    (never a guess); a hub 401 or outage raises (see app.hub)."""
    problem = _invalid(name, args)
    if problem is not None:
        return ToolCall(name, args if isinstance(args, dict) else {}, False, 400,
                        {"error": "invalid_input", "message": problem}, f"{name} (invalid input)",
                        error="invalid_input")  # fmt: skip
    path, params = _route(name, args)
    got = await hub.get(path, user_token, params)
    if got.ok:
        return ToolCall(name, args, True, got.status, display(got.data), _label(name, got.data))
    # 403 (another org's, or a capability the user lacks) and 404 read the same to the user:
    # the AI must not reveal whether a record it may not see exists.
    return ToolCall(
        name,
        args,
        False,
        got.status,
        {"error": "not_available", "message": "That information is not available to you."},
        f"{name.removeprefix('get_').replace('_', ' ')} (not available)",
        error="not_available",
    )
