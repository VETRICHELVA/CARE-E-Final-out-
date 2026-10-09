"""Fuzzy product search for chat ordering (S17; apps-ai-iot.md, Chat ordering).

Scores how well a phrase a user typed ("SK-A", "rapid kits", "IV cannula 20 G") names each
product, against the product's name, its code and its synonyms (scripts/seed/synonyms.yaml).
The search only ranks: it never picks. Deciding that two scores are too close to choose
between is the AI service's job, which then asks the user.

Text is compared as tokens after `normalize`: lowercase, punctuation as spaces, a number
joined to the unit after it ("20 G" = "20G"), simple plurals made singular ("kits" = "kit").
A phrase scores 1.0 against a string with the same normalized text; otherwise the F1 of the
tokens they share (a token matches an equal token, a prefix of at least 3 letters, or a
near-identical spelling such as "canula"). A product's score is its best string's."""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

MIN_SCORE = 0.3  # below this a product is not a candidate at all
DEFAULT_LIMIT = 5
TYPO_RATIO = 0.85  # SequenceMatcher ratio for two tokens of 4+ letters to count as one
UNITS = {"g", "ml", "l", "fr", "iu", "cc", "mg", "mm", "cm", "ply"}
ARTICLES = {"a", "an", "the"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"(?<=\d)\.(?=\d)", "\0", text)  # keep decimal points ("7.5")
    text = re.sub(r"[^a-z0-9\0]+", " ", text).replace("\0", ".")
    out: list[str] = []
    for token in text.split():
        if out and token in UNITS and re.fullmatch(r"\d+(\.\d+)?", out[-1]):
            out[-1] += token  # "20 g" -> "20g"
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
            token = token[:-1]
        out.append(token)
    return " ".join(out)


def _token_match(q: str, s: str) -> bool:
    if q == s:
        return True
    if q.isalpha() and s.isalpha():
        if len(q) >= 3 and s.startswith(q):
            return True
        if min(len(q), len(s)) >= 4 and SequenceMatcher(None, q, s).ratio() >= TYPO_RATIO:
            return True
    return False


def similarity(query: str, text: str) -> float:
    """0..1: how well `query` names `text` (both raw; see the module docstring)."""
    q_norm, s_norm = normalize(query), normalize(text)
    if not q_norm or not s_norm:
        return 0.0
    if q_norm == s_norm:
        return 1.0
    q, s = q_norm.split(), s_norm.split()
    if len(q) > 1 and q[0] in ARTICLES:  # "a kit" is a kit, not "kit A"
        q = q[1:]
    q_hits = sum(any(_token_match(t, u) for u in s) for t in q)
    s_hits = sum(any(_token_match(t, u) for t in q) for u in s)
    if not q_hits or not s_hits:
        return 0.0
    precision, recall = q_hits / len(q), s_hits / len(s)
    return 2 * precision * recall / (precision + recall)


@dataclass(frozen=True)
class Entry:
    """One product to search: its key (e.g. id) and every string that names it."""

    key: object
    code: str
    name: str
    synonyms: Sequence[str] = ()


@dataclass(frozen=True)
class Match:
    key: object
    score: float
    matched_on: str  # the name, code or synonym that scored best


def search(
    query: str,
    entries: Iterable[Entry],
    *,
    limit: int = DEFAULT_LIMIT,
    min_score: float = MIN_SCORE,
) -> list[Match]:
    """The best-scoring products for `query`, best first (ties by name), at most `limit`,
    none below `min_score`. Scores are rounded to 3 decimals, so equal-looking scores are
    equal."""
    found: list[tuple[Match, str]] = []
    for e in entries:
        best_score, best_on = 0.0, e.name
        for candidate in (e.name, e.code, *e.synonyms):
            score = 1.0 if query.strip().upper() == e.code else similarity(query, candidate)
            if score > best_score:
                best_score, best_on = score, candidate
        score = round(best_score, 3)
        if score >= min_score:
            found.append((Match(e.key, score, best_on), e.name))
    found.sort(key=lambda m: (-m[0].score, m[1]))
    return [m for m, _ in found[:limit]]
