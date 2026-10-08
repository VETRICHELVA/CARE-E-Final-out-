import pytest

from app.domain.reconciliation import (
    Outcome,
    inspection_note_missing,
    receipt_problem,
    reconcile,
    reconciled_reason,
    residual_reason,
)
from app.domain.shortage import TRANSITIONS, Status


@pytest.mark.parametrize(
    ("expected", "received", "accepted", "rejected"),
    [(850, 790, 790, 0), (850, 850, 800, 50), (850, 0, 0, 0), (10, 10, 0, 10)],
)
def test_figures_that_hold(expected: int, received: int, accepted: int, rejected: int) -> None:
    assert receipt_problem(expected, received, accepted, rejected) is None


def test_received_more_than_expected() -> None:
    assert receipt_problem(850, 851, 851, 0) == "Received 851 is more than the 850 expected."


@pytest.mark.parametrize(("accepted", "rejected"), [(790, 10), (700, 0), (0, 0)])
def test_accepted_plus_rejected_must_equal_received(accepted: int, rejected: int) -> None:
    assert receipt_problem(850, 790, accepted, rejected) == (
        f"Accepted {accepted} plus rejected {rejected} must equal received 790."
    )


def test_received_over_expected_is_reported_first() -> None:
    assert receipt_problem(10, 11, 1, 1) == "Received 11 is more than the 10 expected."


@pytest.mark.parametrize(
    ("excursion", "note", "missing"),
    [
        (False, None, False),
        (False, "", False),
        (True, None, True),
        (True, "   ", True),
        (True, "Boxes cold to the touch; seals intact.", False),
    ],
)
def test_inspection_note(excursion: bool, note: str | None, missing: bool) -> None:
    assert inspection_note_missing(excursion, note) is missing


def test_scenario_1_short_delivery_opens_a_residual_of_60() -> None:
    result = reconcile(850, 790)
    assert (result.outcome, result.status, result.residual_qty) == (
        Outcome.PARTIAL,
        Status.PARTIALLY_RESOLVED,
        60,
    )


def test_full_acceptance_resolves() -> None:
    result = reconcile(850, 850)
    assert (result.outcome, result.status, result.residual_qty) == (
        Outcome.CONFIRMED,
        Status.RESOLVED,
        0,
    )


def test_nothing_accepted_leaves_the_whole_shortfall() -> None:
    assert reconcile(850, 0).residual_qty == 850


def test_more_than_the_shortfall_counts_as_covered() -> None:
    assert reconcile(850, 900).status == Status.RESOLVED


def test_shortage_moves_through_received() -> None:
    assert Status.RECEIVED in TRANSITIONS[Status.IN_FULFILLMENT]
    assert TRANSITIONS[Status.RECEIVED] == {Status.RESOLVED, Status.PARTIALLY_RESOLVED}


def test_reasons_state_only_recorded_figures() -> None:
    assert reconciled_reason(850, 850, 0) == "Accepted 850 of the 850 short."
    assert reconciled_reason(850, 790, 60) == (
        "Accepted 790 of the 850 short; a residual shortage of 60 was opened."
    )
    assert residual_reason(850, 790) == (
        "Opened for the 60 not accepted of the parent shortage's 850."
    )
