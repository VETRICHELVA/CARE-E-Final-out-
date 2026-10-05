from types import SimpleNamespace

import pytest

from app.conftest import ClientFor
from app.domain.state_machine import InvalidTransition, transition

ALLOWED = {"DRAFT": {"OPEN"}, "OPEN": {"CLOSED"}}


def test_allowed_transition_moves_state_and_returns_previous() -> None:
    obj = SimpleNamespace(status="DRAFT")
    assert transition(obj, "OPEN", ALLOWED) == "DRAFT"
    assert obj.status == "OPEN"


@pytest.mark.parametrize(("start", "to"), [("DRAFT", "CLOSED"), ("CLOSED", "OPEN"), ("X", "OPEN")])
def test_unlisted_transition_raises_and_leaves_state(start: str, to: str) -> None:
    obj = SimpleNamespace(status=start)
    with pytest.raises(InvalidTransition) as exc:
        transition(obj, to, ALLOWED)
    assert (exc.value.from_state, exc.value.to_state) == (start, to)
    assert obj.status == start


def test_custom_attribute() -> None:
    obj = SimpleNamespace(state="DRAFT")
    transition(obj, "OPEN", ALLOWED, attr="state")
    assert obj.state == "OPEN"


@pytest.mark.anyio
async def test_api_allowed_transition_succeeds(client_for: ClientFor) -> None:
    client = await client_for()
    r = await client.post("/_test/transition", params={"status": "DRAFT", "to": "OPEN"})
    assert r.status_code == 200
    assert r.json() == {"status": "OPEN"}


@pytest.mark.anyio
async def test_api_disallowed_transition_returns_409(client_for: ClientFor) -> None:
    client = await client_for()
    r = await client.post("/_test/transition", params={"status": "DRAFT", "to": "CLOSED"})
    assert r.status_code == 409
    assert r.json() == {
        "code": "invalid_transition",
        "message": "Cannot move from DRAFT to CLOSED.",
        "details": {"from": "DRAFT", "to": "CLOSED"},
    }
