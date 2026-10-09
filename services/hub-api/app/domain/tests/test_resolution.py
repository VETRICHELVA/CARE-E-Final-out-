from app.domain import config
from app.domain.ranking import Option
from app.domain.resolution import BUY, TRANSFER, TRANSFER_SPLIT, Plan, plan

NEED = 850


def hospital(key: str, qty: int, unit: int = 1000, eta: float = 2.0, transport: int = 0) -> Option:
    return Option(key, True, qty, eta, 70, False, ((qty, unit),), transport_paise=transport)


def supplier(key: str, unit: int, eta: float = 24.0, qty: int = 5000) -> Option:
    return Option(key, False, qty, eta, 70, False, ((qty, unit),), transport_paise=0)


def lines(p: Plan | None) -> list[tuple[str, int]]:
    assert p is not None
    return [(x.option.key, x.qty) for x in p.lines]


def alternatives(p: Plan | None) -> list[tuple[str, int]]:
    assert p is not None
    return [(x.option.key, x.qty) for x in p.alternatives]


X, Y = supplier("X", 1400, eta=68), supplier("Y", 2800, eta=24)


def test_a_single_hospital_covering_the_shortfall_is_a_transfer() -> None:
    p = plan([hospital("B", 1000), hospital("C", 100)], [X, Y], NEED, critical=True)
    assert p is not None and p.type == TRANSFER
    assert lines(p) == [("B", 850)]
    assert alternatives(p) == [("Y", 850)]  # the best BUY is always kept ready


def test_transfer_takes_the_top_ranked_covering_hospital() -> None:
    dear, cheap = hospital("dear", 900, unit=2000), hospital("cheap", 900, unit=1000)
    assert lines(plan([dear, cheap], [], NEED, critical=False)) == [("cheap", 850)]


def test_split_uses_both_hospitals_when_no_single_one_covers() -> None:
    p = plan([hospital("P", 500), hospital("Q", 350)], [X], NEED, critical=False)
    assert p is not None and p.type == TRANSFER_SPLIT
    assert sorted(lines(p)) == [("P", 500), ("Q", 350)]
    assert alternatives(p) == [("X", 850)]


def test_split_is_greedy_by_rank_and_takes_only_what_is_needed() -> None:
    a, b = hospital("a", 600, unit=1000), hospital("b", 600, unit=1100)
    assert lines(plan([b, a], [], NEED, critical=False)) == [("a", 600), ("b", 250)]


def test_split_never_uses_more_than_three_sources() -> None:
    p = plan([hospital(k, 300) for k in "abcd"], [], NEED, critical=False)
    assert p is not None and p.type == TRANSFER_SPLIT
    assert lines(p) == [("a", 300), ("b", 300), ("c", 250)]
    assert config.MAX_SPLIT_SOURCES == 3


def test_small_sources_do_not_crowd_out_a_split_that_covers_it() -> None:
    # Similar distance, so similar transport: per unit, the large stores are cheaper.
    small = [hospital(k, q, transport=50_000) for k, q in (("s10", 10), ("s20", 20), ("s30", 30))]
    large = [hospital(k, 500, transport=50_000) for k in ("L1", "L2")]
    p = plan([*small, *large], [X], NEED, critical=False)
    assert p is not None and p.type == TRANSFER_SPLIT
    assert lines(p) == [("L1", 500), ("L2", 350)]


def test_no_split_when_the_top_three_cannot_cover_it() -> None:
    p = plan([hospital(k, 200) for k in "abcde"], [X], NEED, critical=False)
    assert p is not None and p.type == BUY  # five would cover 850, but at most three may split


def test_buy_from_the_top_ranked_supplier_with_the_next_as_alternative() -> None:
    p = plan([hospital("C", 100)], [X, Y], NEED, critical=True)  # Scenario 1 after B declines
    assert p is not None and p.type == BUY
    assert (lines(p), alternatives(p)) == ([("Y", 850)], [("X", 850)])


def test_hospitals_with_nothing_transferable_are_skipped() -> None:
    assert lines(plan([hospital("empty", 0), hospital("B", 1000)], [], NEED, True)) == [("B", 850)]


def test_nothing_eligible_is_no_plan() -> None:
    assert plan([], [], NEED, critical=True) is None


# --- CRITICAL parallel requests (§7 step 2, S19) ----------------------------------------------


def parallel(p: Plan | None) -> list[tuple[str, int]]:
    assert p is not None
    return [(x.option.key, x.qty) for x in p.parallel]


def test_a_critical_transfer_asks_up_to_three_single_sources_in_rank_order() -> None:
    hospitals = [hospital(k, 900, eta=eta) for k, eta in (("d", 4), ("a", 1), ("c", 3), ("b", 2))]
    p = plan(hospitals, [X], NEED, critical=True)
    assert p is not None and p.type == TRANSFER
    assert lines(p) == [("a", 850)]  # the planned line is the top-ranked, and asked first
    assert parallel(p) == [("a", 850), ("b", 850), ("c", 850)]
    assert config.CRITICAL_PARALLEL_REQUESTS == 3


def test_only_sources_covering_the_shortfall_alone_are_asked_in_parallel() -> None:
    p = plan([hospital("big", 900, eta=3), hospital("small", 500, eta=1)], [], NEED, critical=True)
    assert lines(p) == [("big", 850)]
    assert parallel(p) == [("big", 850)]


def test_a_routine_transfer_asks_one_source() -> None:
    p = plan([hospital(k, 900) for k in "abc"], [], NEED, critical=False)
    assert p is not None and p.type == TRANSFER and lines(p) == [("a", 850)]
    assert parallel(p) == []


def test_a_critical_split_or_buy_asks_no_one_in_parallel() -> None:
    split = plan([hospital("P", 500), hospital("Q", 350)], [X], NEED, critical=True)
    assert split is not None and split.type == TRANSFER_SPLIT and parallel(split) == []
    buy = plan([], [X, Y], NEED, critical=True)
    assert buy is not None and buy.type == BUY and parallel(buy) == []
