"""S13 fixtures: Scenario 1 from the earlier sections' fixtures, and a client that calls the
hub as the AI service does (AI service token + the user's access token in X-On-Behalf-Of)."""

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service as auth_service
from app.auth.models import User
from app.config import settings
from app.receiving.tests import conftest as s12

now = s12.now
s1 = s12.s1
shortage = s12.shortage
swiftmed = s12.swiftmed
other_fleet = s12.other_fleet
dispatcher = s12.dispatcher
ravi = s12.ravi
desk_y = s12.desk_y
po_delivered = s12.po_delivered
receiver = s12.receiver

AiClientFor = Callable[[User], Awaitable[httpx.AsyncClient]]


@pytest.fixture
async def ai_client_for(app: FastAPI, session: AsyncSession) -> AsyncIterator[AiClientFor]:
    """`await ai_client_for(user)` -> a client acting as the AI service on behalf of `user`."""
    clients: list[httpx.AsyncClient] = []

    async def make(user: User) -> httpx.AsyncClient:
        pair = await auth_service.issue_tokens(session, user)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app, client=("127.0.0.1", 1)),
            base_url="http://test/api/v1",
            headers={
                "Authorization": f"Bearer {settings.ai_service_token}",
                "X-On-Behalf-Of": pair.access_token,
            },
        )
        clients.append(client)
        return client

    yield make
    for c in clients:
        await c.aclose()
