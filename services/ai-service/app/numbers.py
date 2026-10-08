"""The number check (S13; CLAUDE.md rule 2: the AI states no figure a tool didn't return).

An answer fails if it contains a number that appears in none of its tool results. Numbers are
compared by value ("09" = "9", "1000.0" = "1000"); commas between digits are dropped first
("1,000" -> "1000", as the eval README says). On the tool side every JSON number counts, and
so does every number inside a string (gate reasons, dates, product codes), except the digits
inside UUIDs, which are identifiers, not figures."""

import json
import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
DIGIT_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")


def _normal(token: str) -> str:
    try:
        value = Decimal(token)
    except InvalidOperation:
        return token
    return format(value.normalize(), "f")


def numbers_in_text(text: str) -> list[str]:
    """Every number in `text`, normalized, in order of appearance."""
    text = UUID_RE.sub(" ", DIGIT_COMMA_RE.sub("", text))
    return [_normal(m.group()) for m in NUMBER_RE.finditer(text)]


def numbers_in_data(value: Any) -> set[str]:
    """Every number a tool result holds: JSON numbers and numbers inside its strings."""
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, int | float):
            found.update(numbers_in_text(json.dumps(v)))
        elif isinstance(v, str):
            found.update(numbers_in_text(v))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, list | tuple):
            for x in v:
                walk(x)

    walk(value)
    return found


def unsupported_numbers(answer: str, results: Iterable[Any], *, also: str = "") -> list[str]:
    """Numbers in `answer` found in none of `results` (nor in `also`, e.g. the question when
    the service guards its own answers). Empty means the answer passes."""
    allowed: set[str] = set(numbers_in_text(also))
    for r in results:
        allowed |= numbers_in_data(r)
    missing: list[str] = []
    for n in numbers_in_text(answer):
        if n not in allowed and n not in missing:
            missing.append(n)
    return missing
