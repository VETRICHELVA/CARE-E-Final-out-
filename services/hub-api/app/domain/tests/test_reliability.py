"""Reliability score and credits (business-rules.md §5, §12; S19)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain import config
from app.domain import reliability as r
from app.domain.reliability import Answer, Components, History, OrderAnswer

FULL = History(
    answers=[Answer(True, 3.0, 15.0), Answer(True, 6.0, 15.0), Answer(False, 9.0, 15.0)],
    on_time=[True, True, False, True],
    reconciled=[(850, 45), (150, 5)],
)


def test_the_formula_is_section_12() -> None:
    # 40 × acceptance + 25 × on_time + 20 × (1 − discrepancy) + 15 × response_speed
    assert r.formula(1, 1, 0, 1) == 100
    assert r.formula(0, 0, 1, 0) == 0
    assert r.formula(0.5, 0.8, 0.1, 0.6) == pytest.approx(20 + 20 + 18 + 9)


def test_components_come_from_the_recorded_history() -> None:
    c = r.components(FULL)
    assert c.acceptance_rate == pytest.approx(2 / 3)
    assert c.median_response_minutes == 6.0
    assert c.response_speed == pytest.approx(1 - 6 / 15)  # max(0, 1 − median ÷ SLA)
    assert c.on_time_rate == 0.75
    assert c.discrepancy_rate == pytest.approx(50 / 1000)
    expected = 40 * (2 / 3) + 25 * 0.75 + 20 * (1 - 0.05) + 15 * 0.6
    assert expected == pytest.approx(73.4167, abs=1e-4)
    assert r.score(c) == 73


def test_an_org_with_no_history_scores_70() -> None:
    assert config.DEFAULT_RELIABILITY == 70
    c = r.components(History())
    assert c == Components(None, None, None, None, None)
    assert not c.complete
    assert r.score(c) == 70


def test_a_missing_component_keeps_the_default() -> None:
    """The formula needs all four components; without the history for one of them the score
    stays the no-history default rather than inventing a figure for it."""
    no_deliveries = History(answers=FULL.answers)
    c = r.components(no_deliveries)
    assert c.acceptance_rate is not None and c.on_time_rate is None
    assert r.score(c) == 70


def test_an_unanswered_request_counts_against_acceptance_but_not_response_time() -> None:
    c = r.components(History(answers=[Answer(False, None, 15.0), Answer(True, 5.0, 15.0)]))
    assert c.acceptance_rate == 0.5
    assert c.median_response_minutes == 5.0


def test_response_speed_never_goes_below_zero() -> None:
    slow = History(answers=[Answer(True, 60.0, 15.0)], on_time=[True], reconciled=[(10, 0)])
    c = r.components(slow)
    assert c.response_speed == 0.0
    assert r.score(c) == 40 + 25 + 20


def test_response_speed_is_measured_against_each_requests_own_limit() -> None:
    """A CRITICAL answer in 7.5 min and a ROUTINE one in 120 min are both half their limit."""
    c = r.components(History(answers=[Answer(True, 7.5, 15.0), Answer(True, 120.0, 240.0)]))
    assert c.response_speed == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("value", "clamped"), [(-12.4, 0), (0.49, 0), (0.5, 1), (73.5, 74), (99.6, 100), (140, 100)]
)
def test_the_score_is_rounded_half_up_and_clamped_to_0_100(value: float, clamped: int) -> None:
    assert r.clamp(value) == clamped


def test_scores_at_both_ends() -> None:
    best = History(answers=[Answer(True, 0.0, 15.0)], on_time=[True], reconciled=[(10, 0)])
    worst = History(answers=[Answer(False, 30.0, 15.0)], on_time=[False], reconciled=[(10, 10)])
    assert r.score(r.components(best)) == 100
    assert r.score(r.components(worst)) == 0


# --- a supplier is scored on its purchase orders (decision 2026-10-09) ---------------------------

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
CRITICAL, ROUTINE = 15.0, 240.0  # §6 response limits, minutes


def order(minutes: float, sla: float, *, rejected: bool = False) -> OrderAnswer:
    return OrderAnswer(T0, T0 + timedelta(minutes=minutes), rejected, sla)


def supplier(
    orders: list[OrderAnswer], on_time: list[bool], reconciled: list[tuple[int, int]]
) -> History:
    return History(r.answers_for("SUPPLIER", [], orders), on_time, reconciled)


def test_a_purchase_order_answer_is_acknowledged_or_rejected_timed_from_sent() -> None:
    answers = r.order_answers([order(3, CRITICAL), order(90, ROUTINE, rejected=True)])
    assert answers == [Answer(True, 3.0, CRITICAL), Answer(False, 90.0, ROUTINE)]


def test_a_supplier_is_scored_on_purchase_orders_and_a_hospital_on_source_requests() -> None:
    requests = [Answer(True, 5.0, CRITICAL)]
    orders = [order(60, ROUTINE, rejected=True)]
    assert r.answers_for("SUPPLIER", requests, orders) == [Answer(False, 60.0, ROUTINE)]
    assert r.answers_for("HOSPITAL", requests, orders) == requests  # hospital formula unchanged


def test_a_supplier_with_history_is_no_longer_70() -> None:
    """Acceptance = acknowledged ÷ (acknowledged + rejected); response speed from each order's
    SENT → answer minutes over its shortage's §6 limit, as for source requests."""
    history = supplier(
        [order(3, CRITICAL), order(60, ROUTINE), order(6, CRITICAL, rejected=True)],
        on_time=[True, True],
        reconciled=[(850, 60)],
    )
    c = r.components(history)
    assert c.acceptance_rate == pytest.approx(2 / 3)
    assert c.median_response_minutes == 6.0
    assert c.response_speed == pytest.approx(1 - 0.25)  # ratios 0.2, 0.25, 0.4
    assert c.on_time_rate == 1.0
    assert c.discrepancy_rate == pytest.approx(60 / 850)
    expected = 40 * (2 / 3) + 25 * 1.0 + 20 * (1 - 60 / 850) + 15 * 0.75
    assert expected == pytest.approx(81.5049, abs=1e-4)
    assert r.score(c) == 82 != config.DEFAULT_RELIABILITY


def test_two_suppliers_with_different_histories_rank_differently() -> None:
    prompt = supplier([order(3, CRITICAL), order(30, ROUTINE)], [True, True], [(850, 0)])
    slow = supplier([order(200, ROUTINE)], [False], [(100, 30)])
    prompt_score, slow_score = r.score(r.components(prompt)), r.score(r.components(slow))
    assert slow_score == 57  # 40 × 1 + 25 × 0 + 20 × 0.7 + 15 × (1 − 200/240), half up
    assert prompt_score == 98  # 40 + 25 + 20 + 15 × (1 − median(0.2, 0.125))
    assert prompt_score > slow_score  # matching ranks the prompt supplier above the slow one


def test_a_supplier_without_history_in_a_component_keeps_70() -> None:
    assert r.score(r.components(supplier([], [], []))) == 70
    # Delivered and reconciled, but no purchase order answered: no acceptance history.
    assert r.score(r.components(supplier([], [True], [(850, 60)]))) == 70


@pytest.mark.parametrize(("units", "credits"), [(0, 0), (9, 0), (10, 1), (805, 80), (850, 85)])
def test_one_credit_per_10_units_accepted(units: int, credits: int) -> None:
    assert config.UNITS_PER_CREDIT == 10
    assert r.credits_for(units) == credits


def test_the_credit_reason_states_only_the_recorded_figure() -> None:
    assert r.credit_reason(805) == "Transfer reconciled: 805 units accepted by the receiver."
    assert r.credit_reason(1_000) == "Transfer reconciled: 1,000 units accepted by the receiver."


def test_a_supplier_is_scored_from_the_components_that_have_history() -> None:
    """§12 (user-approved): a supplier with answered orders but nothing delivered or
    reconciled yet is scored from acceptance and speed alone; a hospital is not."""
    c = r.components(supplier([order(3, CRITICAL), order(6, CRITICAL, rejected=True)], [], []))
    assert (c.on_time_rate, c.discrepancy_rate) == (None, None)
    speed = 1 - (3 / 15 + 6 / 15) / 2  # median of the two ratios
    expected = (40 * 0.5 + 15 * speed) / 55 * 100
    assert r.score(c, partial=True) == round(expected) != config.DEFAULT_RELIABILITY
    assert r.score(c) == config.DEFAULT_RELIABILITY  # a hospital still needs all four
    nothing = r.components(supplier([], [], []))
    assert r.score(nothing, partial=True) == config.DEFAULT_RELIABILITY
    full = r.components(supplier([order(3, CRITICAL)], [True], [(100, 0)]))
    assert r.score(full, partial=True) == r.score(full)  # all four: the same formula
