"""Reliability score and credits (business-rules.md §5, §12; S19)."""

import pytest

from app.domain import config
from app.domain import reliability as r
from app.domain.reliability import Answer, Components, History

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


@pytest.mark.parametrize(("units", "credits"), [(0, 0), (9, 0), (10, 1), (805, 80), (850, 85)])
def test_one_credit_per_10_units_accepted(units: int, credits: int) -> None:
    assert config.UNITS_PER_CREDIT == 10
    assert r.credits_for(units) == credits


def test_the_credit_reason_states_only_the_recorded_figure() -> None:
    assert r.credit_reason(805) == "Transfer reconciled: 805 units accepted by the receiver."
    assert r.credit_reason(1_000) == "Transfer reconciled: 1,000 units accepted by the receiver."
