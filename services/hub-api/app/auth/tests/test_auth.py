import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth import service
from app.auth.capabilities import ROLE_CAPABILITIES, Capability, RoleName
from app.auth.models import RefreshToken, User
from app.config import settings
from app.conftest import PASSWORD, TEST_DB_URL, ClientFor, World
from app.errors import AppError
from app.orgs.models import Organization

pytestmark = pytest.mark.anyio


async def _login(client_for: ClientFor, email: str, ip: str = "127.0.0.1") -> dict[str, Any]:
    client = await client_for(ip=ip)
    r = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def _me(client_for: ClientFor, access: str) -> int:
    client = await client_for()
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    return r.status_code


async def test_login_then_me_returns_user_org_roles_capabilities(
    client_for: ClientFor, world: World
) -> None:
    user = world.users["a.APPROVER"]
    tokens = await _login(client_for, "  APPROVER@a.test ")  # email is case/space-insensitive
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 900

    client = await client_for()
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 200
    me = r.json()
    assert me["user"]["id"] == str(user.id)
    assert me["user"]["email"] == "approver@a.test"
    assert "password_hash" not in me["user"]
    assert me["org"]["id"] == str(world.hospital_a.id)
    assert me["org"]["status"] == "ACTIVE"
    assert me["roles"] == ["APPROVER"]
    assert me["capabilities"] == ["audit.read", "recommendation.approve"]


@pytest.mark.parametrize("email", ["approver@a.test", "nobody@a.test"])
async def test_bad_credentials_return_same_401(
    client_for: ClientFor, world: World, email: str
) -> None:
    client = await client_for()
    r = await client.post("/auth/login", json={"email": email, "password": "wrong"})
    assert r.status_code == 401
    assert r.json() == {
        "code": "invalid_credentials",
        "message": "Email or password is incorrect.",
        "details": {},
    }


async def test_inactive_user_cannot_log_in(
    client_for: ClientFor, world: World, session: AsyncSession
) -> None:
    world.users["a.REQUESTER"].is_active = False
    await session.flush()
    client = await client_for()
    r = await client.post("/auth/login", json={"email": "requester@a.test", "password": PASSWORD})
    assert r.status_code == 401


async def test_me_without_token_is_401(client_for: ClientFor) -> None:
    client = await client_for()
    r = await client.get("/auth/me")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


async def test_expired_access_token_is_401(client_for: ClientFor, world: World) -> None:
    tokens = await _login(client_for, "approver@a.test")
    claims = jwt.decode(tokens["access_token"], settings.jwt_secret, algorithms=["HS256"])
    claims["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    expired = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    client = await client_for()
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert r.json()["message"] == "Access token expired."


async def test_forged_access_token_is_401(client_for: ClientFor, world: World) -> None:
    tokens = await _login(client_for, "approver@a.test")
    claims = jwt.decode(tokens["access_token"], settings.jwt_secret, algorithms=["HS256"])
    forged = jwt.encode(claims, "not-the-secret-not-the-secret-123", algorithm="HS256")
    assert await _me(client_for, forged) == 401


async def test_refresh_rotates_and_reuse_revokes_the_session(
    client_for: ClientFor, world: World
) -> None:
    first = await _login(client_for, "approver@a.test")
    client = await client_for()

    r = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert await _me(client_for, second["access_token"]) == 200

    # The rotated token is revoked; presenting it again ends the whole session.
    r = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 401
    assert r.json()["message"] == "Refresh token was revoked."
    r = await client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert r.status_code == 401
    assert await _me(client_for, second["access_token"]) == 401


async def test_expired_refresh_token_is_401(
    client_for: ClientFor, world: World, session: AsyncSession
) -> None:
    tokens = await _login(client_for, "approver@a.test")
    await session.execute(
        update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    client = await client_for()
    r = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert r.json()["message"] == "Refresh token expired."


async def test_unknown_refresh_token_is_401(client_for: ClientFor) -> None:
    client = await client_for()
    r = await client.post("/auth/refresh", json={"refresh_token": "nope"})
    assert r.status_code == 401


async def test_logout_revokes_refresh_and_access_tokens(
    client_for: ClientFor, world: World
) -> None:
    tokens = await _login(client_for, "approver@a.test")
    other = await _login(client_for, "approver@a.test")  # a second session stays alive
    client = await client_for()

    r = await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204
    r = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert await _me(client_for, tokens["access_token"]) == 401
    assert await _me(client_for, other["access_token"]) == 200

    r = await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204  # idempotent


async def test_login_rate_limit_is_5_per_minute_per_ip(client_for: ClientFor, world: World) -> None:
    bad = {"email": "approver@a.test", "password": "wrong"}
    good = {"email": "approver@a.test", "password": PASSWORD}
    client = await client_for(ip="10.0.0.1")
    for _ in range(5):
        assert (await client.post("/auth/login", json=bad)).status_code == 401

    r = await client.post("/auth/login", json=good)  # 6th attempt, even with the right password
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
    assert 0 < r.json()["details"]["retry_after"] <= 60

    other_ip = await client_for(ip="10.0.0.2")
    assert (await other_ip.post("/auth/login", json=good)).status_code == 200


def test_every_role_has_capabilities_and_admin_has_all() -> None:
    assert set(ROLE_CAPABILITIES) == set(RoleName)
    assert all(ROLE_CAPABILITIES[r] for r in RoleName)
    assert ROLE_CAPABILITIES[RoleName.ADMIN] == frozenset(Capability)


@pytest.mark.parametrize("pexpire_ms", [None, 300])  # stale key without TTL; window nearly over
async def test_rate_limit_retry_after_is_at_least_one_second(
    client_for: ClientFor, world: World, redis: Redis, pexpire_ms: int | None
) -> None:
    key = "rl:login:10.0.0.9"
    await redis.set(key, 5)
    if pexpire_ms:
        await redis.pexpire(key, pexpire_ms)
    client = await client_for(ip="10.0.0.9")
    r = await client.post("/auth/login", json={"email": "approver@a.test", "password": PASSWORD})
    assert r.status_code == 429
    assert 1 <= r.json()["details"]["retry_after"] <= 60
    assert await redis.ttl(key) != -1  # -1 = no expiry; a key never outlives its window


async def test_concurrent_refresh_with_one_token_rotates_once(migrated_db: None) -> None:
    """Two requests on two real connections race to refresh the same token: exactly one
    rotates; the other sees reuse and the whole session family is revoked."""
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    org_id = uuid.uuid4()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            setup.add(Organization(id=org_id, name="Race", type="HOSPITAL", lat=0, lng=0))
            user = User(
                email=f"race-{org_id}@x.test",
                password_hash="x",
                full_name="Race",
                org_id=org_id,
            )
            setup.add(user)
            await setup.flush()
            raw = (await service.issue_tokens(setup, user)).refresh_token
            await setup.commit()

        async def attempt() -> str:
            async with AsyncSession(engine, expire_on_commit=False) as s:
                await s.connection()  # both connections open before either refreshes
                try:
                    await service.refresh(s, raw)
                    await s.commit()
                    return "rotated"
                except AppError as e:
                    return e.message

        results = await asyncio.gather(attempt(), attempt())
        assert sorted(results) == ["Refresh token was revoked.", "rotated"]

        async with AsyncSession(engine) as check:
            live = await check.scalar(
                select(func.count())
                .select_from(RefreshToken)
                .join(User)
                .where(User.org_id == org_id, RefreshToken.revoked_at.is_(None))
            )
        assert live == 0  # the reuse revoked the token the winner just received
    finally:
        async with engine.begin() as conn:  # app_user delete cascades to tokens and roles
            await conn.execute(delete(User).where(User.org_id == org_id))
            await conn.execute(delete(Organization).where(Organization.id == org_id))
        await engine.dispose()
