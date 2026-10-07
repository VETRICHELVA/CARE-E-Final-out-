from types import SimpleNamespace

import pytest

from app.domain.shortage import TRANSITIONS, Status, shortfall
from app.domain.state_machine import InvalidTransition, transition


def test_shortfall_is_required_minus_local_usable() -> None:
    assert shortfall(1000, 150) == 850  # Scenario 1


@pytest.mark.parametrize(("required", "local"), [(100, 150), (100, 100), (0, 0)])
def test_shortfall_is_never_negative(required: int, local: int) -> None:
    assert shortfall(required, local) == 0


CANCELLABLE = {Status.OPEN, Status.MATCHING, Status.AWAITING_DECISION}


@pytest.mark.parametrize("status", list(Status))
def test_cancel_only_from_open_matching_or_awaiting_decision(status: Status) -> None:
    shortage = SimpleNamespace(status=status.value)
    if status in CANCELLABLE:
        assert transition(shortage, Status.CANCELLED, TRANSITIONS) == status
        assert shortage.status == Status.CANCELLED
    else:
        with pytest.raises(InvalidTransition):
            transition(shortage, Status.CANCELLED, TRANSITIONS)
        assert shortage.status == status
