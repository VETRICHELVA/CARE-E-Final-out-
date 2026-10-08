"""The number check (S13; CLAUDE.md rule 2: the AI states no figure a tool didn't return).

An answer fails if it contains a figure that appears in none of its tool results. Figures are
compared as tokens:

- Numbers by value, sign included ("09" = "9", "1000.0" = "1000", "-3" != "3"); commas between
  digits are dropped first ("1,000" -> "1000", as the eval README says). Numbers written as
  words count too ("twelve hundred" -> "1200"); a lone "one" is left alone, being mostly a
  pronoun.
- Dates and times as wholes, never as their digit groups, so a timestamp in a tool result
  allows that date and that time, not its year, month, minute or second as quantities:
  "2026-10-09" (ISO date), "10-09" (a day and month such as "9 Oct"), "06:00" (a clock time;
  the copilot gives times in UTC as the tools return them).

On the tool side every JSON number counts, and so does every figure inside a string (gate
reasons, dates, product codes), except the digits inside UUIDs, which are identifiers."""

import json
import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
DIGIT_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")
ISO_RE = re.compile(
    r"(?<!\d)(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?(?!\d)"
)
TIME_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2})?(?![\d:])")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DAY_MONTH_RE = re.compile(rf"(?<![\d:])(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH}", re.I)
MONTH_DAY_RE = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?![\d:])", re.I)
# A minus sign counts only where it can't be a hyphen joining words or a range ("IV-CAN-20G",
# "3-5"): at the start or after a space or an opening bracket.
NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?|(?<=[\w.])\d+(?:\.\d+)?")

UNITS = {
    w: n
    for n, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}
TENS = {
    w: 10 * n
    for n, w in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], 2
    )
}
SCALES = {
    "thousand": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "million": 1_000_000,
    "crore": 10_000_000,
}
NUMBER_WORDS = {*UNITS, *TENS, "hundred", *SCALES}
WORD_RE = re.compile(r"[a-z]+", re.I)


def _normal(token: str) -> str:
    try:
        value = Decimal(token)
    except InvalidOperation:
        return token
    return "0" if value == 0 else format(value.normalize(), "f")


def _md(month: int, day: int) -> str:
    return f"{month:02d}-{day:02d}"


def _words_value(words: list[str]) -> int:
    total = current = 0
    for w in words:
        if w in UNITS:
            current += UNITS[w]
        elif w in TENS:
            current += TENS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        else:
            total += max(current, 1) * SCALES[w]
            current = 0
    return total + current


def _number_words(text: str) -> list[str]:
    """Values of runs of number words ("twelve hundred", "twenty-five"); not a lone "one"."""
    found: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run and run != ["one"]:
            found.append(str(_words_value(run)))
        run.clear()

    words = WORD_RE.finditer(text)
    last_end = None
    for m in words:
        w = m.group().lower()
        gap = text[last_end : m.start()] if last_end is not None else " "
        joined = gap.strip() in ("", "-") and "\n" not in gap
        if w in NUMBER_WORDS and (not run or joined):
            run.append(w)
        elif w == "and" and run and joined:
            pass  # "one hundred and twenty"
        else:
            flush()
            if w in NUMBER_WORDS:
                run.append(w)
        last_end = m.end()
    flush()
    return found


def numbers_in_text(text: str) -> list[str]:
    """Every figure in `text` as a token (see the module docstring), in order of appearance."""
    text = UUID_RE.sub(" ", DIGIT_COMMA_RE.sub("", text))
    tokens: list[tuple[int, str]] = []

    def take(regex: re.Pattern[str], make: Any) -> None:
        nonlocal text
        for m in regex.finditer(text):
            tokens.extend((m.start(), t) for t in make(m))
        text = regex.sub(lambda m: " " * len(m.group()), text)

    def iso(m: re.Match[str]) -> list[str]:
        y, mo, d, hh, mm = m.groups()
        out = [f"{y}-{mo}-{d}", _md(int(mo), int(d))]
        return out + ([f"{int(hh):02d}:{mm}"] if hh else [])

    take(ISO_RE, iso)
    take(TIME_RE, lambda m: [f"{int(m.group(1)):02d}:{m.group(2)}"])
    take(DAY_MONTH_RE, lambda m: [_md(MONTHS.index(m.group(2)[:3].lower()) + 1, int(m.group(1)))])
    take(MONTH_DAY_RE, lambda m: [_md(MONTHS.index(m.group(1)[:3].lower()) + 1, int(m.group(2)))])
    tokens.extend((m.start(), _normal(m.group())) for m in NUMBER_RE.finditer(text))
    ordered = [t for _, t in sorted(tokens, key=lambda x: x[0])]
    return ordered + _number_words(text)


def numbers_in_data(value: Any) -> set[str]:
    """Every figure a tool result holds: JSON numbers and figures inside its strings."""
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
    """Figures in `answer` found in none of `results` (nor in `also`, e.g. the question when
    the service guards its own answers). Empty means the answer passes."""
    allowed: set[str] = set(numbers_in_text(also))
    for r in results:
        allowed |= numbers_in_data(r)
    missing: list[str] = []
    for n in numbers_in_text(answer):
        if n not in allowed and n not in missing:
            missing.append(n)
    return missing
