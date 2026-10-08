"""S12 notifications: a user lists and marks read only their own (an escalation notifies
every APPROVER of the org). Success, 403 for another user's (same org or another org), 404."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.conftest import ClientFor, World
from app.notifications.models import Notification
from app.recommendations.tests import conftest as s09
from app.recommendations.tests.conftest import answer_b, open_rec
from app.shortages.models import Shortage
from app.source_requests.tests.conftest import add_user

pytestmark = pytest.mark.anyio

now = s09.now
s1 = s09.s1
shortage = s09.shortage


@pytest.fixture
async def escalated(
    session: AsyncSession, world: World, shortage: Shortage, client_for: ClientFor
) -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    """A's approver escalates B's TRANSFER; a second approver is notified too."""
    second = await add_user(session, world.hospital_a, "APPROVER", "approver2@a.test")
    await answer_b(session, await client_for(world.users["b.STORE_MANAGER"]), shortage, "accept")
    rec = await open_rec(session, shortage)
    first = await client_for(world.users["a.APPROVER"])
    r = await first.post(f"/recommendations/{rec.id}/escalate", json={"reason": "Over budget"})
    assert r.status_code == 200, r.text
    return first, await client_for(second)


async def test_each_approver_lists_their_own_and_marks_it_read(
    world: World,
    shortage: Shortage,
    escalated: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    first, second = escalated
    for client in (first, second):
        r = await client.get("/notifications")
        assert r.status_code == 200, r.text
        (item,) = r.json()["items"]
        assert (item["type"], item["read_at"]) == ("recommendation.escalated", None)
        assert item["payload"]["shortage_id"] == str(shortage.id)
        assert item["payload"]["reason"] == "Over budget"

    (item,) = (await second.get("/notifications", params={"unread": True})).json()["items"]
    r = await second.post(f"/notifications/{item['id']}/read")
    assert r.status_code == 200, r.text
    read_at = r.json()["read_at"]
    assert read_at is not None
    assert (await second.get("/notifications", params={"unread": True})).json()["items"] == []
    assert len((await second.get("/notifications")).json()["items"]) == 1
    # Marking it again keeps the first time; the other approver's copy is still unread.
    assert (await second.post(f"/notifications/{item['id']}/read")).json()["read_at"] == read_at
    assert len((await first.get("/notifications", params={"unread": True})).json()["items"]) == 1


async def test_nobody_reads_or_marks_another_users_notification(
    session: AsyncSession,
    world: World,
    escalated: tuple[httpx.AsyncClient, httpx.AsyncClient],
    client_for: ClientFor,
) -> None:
    first, _ = escalated
    (item,) = (await first.get("/notifications")).json()["items"]
    for key in ("a.REQUESTER", "b.APPROVER"):  # same org, another org
        other = await client_for(world.users[key])
        assert (await other.get("/notifications")).json()["items"] == []
        r = await other.post(f"/notifications/{item['id']}/read")
        assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    notification = await session.get_one(Notification, item["id"], populate_existing=True)
    assert notification.read_at is None
    r = await first.post("/notifications/00000000-0000-0000-0000-000000000000/read")
    assert r.status_code == 404


async def test_notifications_need_a_signed_in_user(client_for: ClientFor) -> None:
    assert (await (await client_for()).get("/notifications")).status_code == 401
