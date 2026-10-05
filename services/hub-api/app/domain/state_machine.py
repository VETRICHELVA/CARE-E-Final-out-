from collections.abc import Mapping, Set
from typing import Any


class InvalidTransition(Exception):
    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(f"Cannot move from {from_state} to {to_state}.")
        self.from_state, self.to_state = from_state, to_state


def transition(
    obj: Any, to_state: str, allowed: Mapping[str, Set[str]], attr: str = "status"
) -> str:
    """Move `obj.<attr>` to `to_state` if allowed; return the previous state.

    Any transition not listed raises InvalidTransition (HTTP 409 `invalid_transition`).
    """
    from_state: str = getattr(obj, attr)
    if to_state not in allowed.get(from_state, set()):
        raise InvalidTransition(from_state, to_state)
    setattr(obj, attr, to_state)
    return from_state
