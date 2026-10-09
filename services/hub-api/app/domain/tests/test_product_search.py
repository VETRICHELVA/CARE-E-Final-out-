"""Product search scoring (S17) on the real catalog and synonyms (PROGRESS.md S04 -> S17)."""

import runpy

import pytest

from app.catalog.service import CATALOG_FILE, load_synonyms
from app.domain.product_search import MIN_SCORE, Entry, normalize, search, similarity

PRODUCTS = runpy.run_path(str(CATALOG_FILE))["PRODUCTS"]
ENTRIES = [Entry(code, code, name, load_synonyms().get(code, ())) for code, name, *_ in PRODUCTS]


def scores(query: str) -> dict[object, float]:
    return {m.key: m.score for m in search(query, ENTRIES, limit=40)}


def test_every_synonym_names_a_catalog_product() -> None:
    codes = {code for code, *_ in PRODUCTS}
    assert set(load_synonyms()) <= codes
    assert {"SURG-KIT-A", "DIAG-RDK", "IV-CAN-20G"} <= set(load_synonyms())


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("SK-A", "SURG-KIT-A"),
        ("surgical kit A", "SURG-KIT-A"),
        ("surgical kits A", "SURG-KIT-A"),
        ("kit A", "SURG-KIT-A"),
        ("SURG-KIT-A", "SURG-KIT-A"),
        ("rapid kits", "DIAG-RDK"),
        ("Rapid diagnostic kits", "DIAG-RDK"),
        ("20G cannula", "IV-CAN-20G"),
        ("IV cannula 20 G", "IV-CAN-20G"),
        ("IV canula 20G", "IV-CAN-20G"),
    ],
)
def test_synonyms_and_names_resolve_to_one_clear_product(query: str, code: str) -> None:
    (best, *rest) = search(query, ENTRIES)
    assert (best.key, best.score) == (code, 1.0)
    # Clear: nothing else within 10% of it, so the chat need not ask.
    assert all(m.score < 0.9 * best.score for m in rest)


@pytest.mark.parametrize("query", ["kits", "OT kits", "a kit"])
def test_plain_kits_scores_both_kits_within_10_percent(query: str) -> None:
    got = scores(query)
    kit_a, rdk = got["SURG-KIT-A"], got["DIAG-RDK"]
    assert abs(kit_a - rdk) <= 0.1 * max(kit_a, rdk)
    assert max(got.values()) == max(kit_a, rdk)  # no third product beats them


def test_nothing_scores_below_the_minimum_or_for_unrelated_words() -> None:
    assert search("ward 4", ENTRIES) == []
    assert all(m.score >= MIN_SCORE for m in search("gloves", ENTRIES))


def test_normalize_joins_units_and_singularizes() -> None:
    assert normalize("IV Cannula 20 G") == normalize("iv cannulas 20G") == "iv cannula 20g"
    assert normalize("SK-A") == "sk a"
    assert normalize("ET tube 7.5") == "et tube 7.5"
    assert normalize("Gloss") == "gloss"


def test_similarity_bounds() -> None:
    assert similarity("", "Surgical Kit A") == 0.0
    assert similarity("surgical kit a", "Surgical Kit A") == 1.0
    assert 0 < similarity("kit", "Surgical Kit A") < 1


def test_ties_order_by_name_and_limit_applies() -> None:
    both = search("kits", ENTRIES)
    assert [m.key for m in both] == ["DIAG-RDK", "SURG-KIT-A"]  # Rapid... < Surgical...
    assert len(search("kits", ENTRIES, limit=1)) == 1
