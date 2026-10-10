from datetime import date, timedelta

import pytest

from app.domain.state_machine import InvalidTransition, transition
from app.domain.surplus import TRANSITIONS, expiry_band, offered_qty

TODAY = date(2026, 10, 8)


def test_a_post_offers_only_up_to_the_batch_transferable() -> None:
    assert offered_qty(300, 800) == 300
    assert offered_qty(300, 120) == 120
    assert offered_qty(300, 0) == 0


@pytest.mark.parametrize(
    ("days", "band"),
    [
        (1, "UNDER_30_DAYS"),
        (29, "UNDER_30_DAYS"),
        (30, "30_TO_59_DAYS"),
        (55, "30_TO_59_DAYS"),
        (60, "60_TO_89_DAYS"),
        (89, "60_TO_89_DAYS"),
        (90, "90_DAYS_OR_MORE"),
        (400, "90_DAYS_OR_MORE"),
    ],
)
def test_expiry_band(days: int, band: str) -> None:
    assert expiry_band(TODAY + timedelta(days), TODAY) == band


class Post:
    def __init__(self, status: str) -> None:
        self.status = status


@pytest.mark.parametrize(
    ("start", "to"),
    [
        ("OPEN", "MATCHED"),
        ("OPEN", "WITHDRAWN"),
        ("OPEN", "EXPIRED"),
        ("MATCHED", "WITHDRAWN"),
        ("MATCHED", "EXPIRED"),
    ],
)
def test_allowed_transitions(start: str, to: str) -> None:
    post = Post(start)
    assert transition(post, to, TRANSITIONS) == start
    assert post.status == to


@pytest.mark.parametrize(
    ("start", "to"),
    [
        ("MATCHED", "OPEN"),
        ("MATCHED", "MATCHED"),
        ("WITHDRAWN", "MATCHED"),
        ("WITHDRAWN", "OPEN"),
        ("WITHDRAWN", "WITHDRAWN"),
        ("EXPIRED", "WITHDRAWN"),
        ("EXPIRED", "MATCHED"),
    ],
)
def test_other_transitions_are_refused(start: str, to: str) -> None:
    with pytest.raises(InvalidTransition):
        transition(Post(start), to, TRANSITIONS)
