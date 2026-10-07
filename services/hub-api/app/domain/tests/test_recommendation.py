"""Recommendation, purchase order and shipment rules (business-rules.md §6, §8, §13) and the
deterministic explanation."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.fulfillment import PO_TRANSITIONS, SHIPMENT_TRANSITIONS, PoStatus, ShipmentStatus
from app.domain.recommendation import (
    APPROVED_MESSAGE,
    OPEN_RECOMMENDATION,
    REC_TRANSITIONS,
    RecStatus,
    Rejected,
    Source,
    about_hours,
    explain,
    is_expired,
    rupees,
    valid_until,
)
from app.domain.state_machine import InvalidTransition, transition

NOW = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
R, P, S = RecStatus, PoStatus, ShipmentStatus


class Thing:
    def __init__(self, status: str) -> None:
        self.status = status


ALLOWED_REC = {
    (R.PENDING, R.APPROVED),
    (R.PENDING, R.REJECTED),
    (R.PENDING, R.ESCALATED),
    (R.PENDING, R.EXPIRED),
    (R.ESCALATED, R.APPROVED),
    (R.ESCALATED, R.REJECTED),
    (R.ESCALATED, R.EXPIRED),
}


@pytest.mark.parametrize("src", list(R))
@pytest.mark.parametrize("dst", list(R))
def test_recommendation_state_machine_is_exactly_the_spec(src: RecStatus, dst: RecStatus) -> None:
    thing = Thing(src)
    if (src, dst) in ALLOWED_REC:
        assert transition(thing, dst, REC_TRANSITIONS) == src
        assert thing.status == dst
    else:
        with pytest.raises(InvalidTransition):
            transition(thing, dst, REC_TRANSITIONS)


def test_only_pending_and_escalated_wait_for_a_decision() -> None:
    assert frozenset({R.PENDING, R.ESCALATED}) == OPEN_RECOMMENDATION


ALLOWED_PO = {
    (P.SENT, P.ACKNOWLEDGED),
    (P.ACKNOWLEDGED, P.DISPATCHED),
    (P.DISPATCHED, P.DELIVERED),
    (P.SENT, P.REJECTED),
    (P.ACKNOWLEDGED, P.REJECTED),
}


@pytest.mark.parametrize("src", list(P))
@pytest.mark.parametrize("dst", list(P))
def test_purchase_order_state_machine_is_exactly_the_spec(src: PoStatus, dst: PoStatus) -> None:
    thing = Thing(src)
    if (src, dst) in ALLOWED_PO:
        transition(thing, dst, PO_TRANSITIONS)
        assert thing.status == dst
    else:
        with pytest.raises(InvalidTransition):
            transition(thing, dst, PO_TRANSITIONS)


def test_shipment_state_machine_is_the_spec() -> None:
    chain = [S.CREATED, S.ASSIGNED, S.PICKED_UP, S.IN_TRANSIT, S.DELIVERED, S.RECONCILED]
    allowed = set(zip(chain, chain[1:], strict=False)) | {(S.ASSIGNED, S.CREATED)}
    got = {(a, b) for a, targets in SHIPMENT_TRANSITIONS.items() for b in targets}
    assert got == allowed


@pytest.mark.parametrize(
    ("priority", "limit"), [("CRITICAL", timedelta(minutes=30)), ("ROUTINE", timedelta(hours=24))]
)
def test_validity_by_priority(priority: str, limit: timedelta) -> None:
    deadline = valid_until(priority, NOW)
    assert deadline == NOW + limit
    assert not is_expired(deadline, deadline - timedelta(seconds=1))
    assert is_expired(deadline, deadline)


def test_approval_wording_is_section_13() -> None:
    assert APPROVED_MESSAGE == {
        "TRANSFER": "Stock is now held at the source.",
        "TRANSFER_SPLIT": "Stock is now held at each source.",
        "BUY": "The order has gone to the supplier.",
    }


@pytest.mark.parametrize(
    ("hours", "shown"), [(0.2, 1), (1.49, 1), (1.5, 2), (5.73, 6), (24.0, 24), (68.4, 68)]
)
def test_about_hours_rounds_half_up_and_never_says_zero(hours: float, shown: int) -> None:
    assert about_hours(hours) == shown


@pytest.mark.parametrize(
    ("paise", "text"), [(0, "₹0.00"), (5, "₹0.05"), (2_419_050, "₹24,190.50"), (100, "₹1.00")]
)
def test_rupees(paise: int, text: str) -> None:
    assert rupees(paise) == text


B = Source("Hospital B", 850, 5.7, shelf_life_days=180)
X = Source("Supplier X", 850, 68.2, cost_paise=1_212_000)
Y = Source("Supplier Y", 850, 24.4, cost_paise=2_419_050)
REJECTED = [
    Rejected("Hospital C", ("Only 100 transferable; 850 needed",)),
    Rejected(
        "Hospital E", ("Not authorized to supply this product", "Stock last verified 9 days ago")
    ),
]


def test_a_transfer_explanation() -> None:
    text = explain(
        "TRANSFER", [B], X, shortfall=850, critical=True, other_eligible=0, rejected=REJECTED
    )
    assert text == (
        "Hospital B holds 850 units with 180 days of shelf life at delivery and can deliver "
        "in about 6 h. Sources are ranked by earliest arrival because the shortage is "
        "CRITICAL. 2 other sources were not eligible: Hospital C (Only 100 transferable; 850 "
        "needed); Hospital E (Not authorized to supply this product, Stock last verified 9 "
        "days ago). Alternative: buy 850 units from Supplier X for ₹12,120.00, arriving in "
        "about 68 h."
    )


def test_a_split_explanation_names_every_source() -> None:
    p = Source("Hospital P", 500, 2.0, shelf_life_days=170)
    q = Source("Hospital Q", 350, 3.2, shelf_life_days=1)
    text = explain(
        "TRANSFER_SPLIT", [p, q], None, shortfall=850, critical=False, other_eligible=1,
        rejected=[],
    )  # fmt: skip
    assert text == (
        "No single hospital source covers the shortfall of 850 units, so it is split across 2 "
        "sources: Hospital P holds 500 units with 170 days of shelf life at delivery and can "
        "deliver in about 2 h; Hospital Q holds 350 units with 1 day of shelf life at "
        "delivery and can deliver in about 3 h. Sources are ranked by lowest landed cost "
        "because the shortage is ROUTINE. 1 other eligible source ranked lower. Alternative: "
        "no supplier is eligible to buy from."
    )


def test_a_buy_explanation_lists_its_alternative_and_who_was_left_out() -> None:
    text = explain(
        "BUY", [Y], X, shortfall=850, critical=True, other_eligible=0, rejected=REJECTED[:1],
        left_out=[("Hospital B", "declined the request")],
    )  # fmt: skip
    assert text == (
        "No eligible hospital source, alone or with up to 2 others, covers the shortfall of "
        "850 units. Recommended: buy 850 units from Supplier Y for ₹24,190.50, arriving in "
        "about 24 h. Sources are ranked by earliest arrival because the shortage is CRITICAL. "
        "1 other source was not eligible: Hospital C (Only 100 transferable; 850 needed). Not "
        "asked again for this shortage: Hospital B (declined the request). Alternative: buy "
        "850 units from Supplier X for ₹12,120.00, arriving in about 68 h instead."
    )


def test_a_buy_without_a_second_supplier_says_so() -> None:
    text = explain("BUY", [Y], None, shortfall=850, critical=True, other_eligible=0, rejected=[])
    assert text.endswith("Alternative: no other supplier is eligible.")


def test_a_hospital_source_never_shows_a_cost() -> None:
    # Source carries no cost for a hospital; even if one slipped in, only suppliers show it.
    text = explain(
        "TRANSFER", [B], None, shortfall=850, critical=True, other_eligible=0, rejected=[]
    )
    assert "₹" not in text


def test_an_unknown_type_is_refused() -> None:
    with pytest.raises(ValueError):
        explain("GIFT", [B], None, shortfall=1, critical=True, other_eligible=0, rejected=[])
