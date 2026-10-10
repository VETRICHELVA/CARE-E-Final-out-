import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RefreshToken, User
from app.auth.schemas import TokenPair
from app.config import settings
from app.errors import AppError

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=7)
JWT_ALG = "HS256"
# A stream ticket opens GET /events/stream (EventSource cannot send an Authorization header).
# Its audience makes it useless as an access token, and an access token useless as a ticket.
STREAM_TICKET_TTL = timedelta(seconds=60)
STREAM_AUDIENCE = "events.stream"

_hasher = PasswordHasher()
redis_client = Redis.from_url(settings.redis_url)


def get_redis() -> Redis:
    return redis_client


def unauthenticated(message: str = "Sign in again.") -> AppError:
    return AppError(401, "unauthenticated", message)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


@cache
def _dummy_hash() -> str:
    return _hasher.hash("timing-equalizer")


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret, algorithms=[JWT_ALG], options={"require": ["exp", "sub"]}
        )
    except jwt.ExpiredSignatureError as e:
        raise unauthenticated("Access token expired.") from e
    except jwt.InvalidTokenError as e:
        raise unauthenticated("Invalid access token.") from e
    return claims


def issue_stream_ticket(access_claims: dict[str, Any]) -> tuple[str, datetime]:
    """A short-lived ticket for the same user and session as the access token."""
    now = datetime.now(UTC)
    expires_at = now + STREAM_TICKET_TTL
    claims = {
        "sub": access_claims["sub"],
        "sid": access_claims["sid"],
        "aud": STREAM_AUDIENCE,
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=JWT_ALG), expires_at


def decode_stream_ticket(ticket: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            ticket,
            settings.jwt_secret,
            algorithms=[JWT_ALG],
            audience=STREAM_AUDIENCE,
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise unauthenticated("Stream ticket expired.") from e
    except jwt.InvalidTokenError as e:
        raise unauthenticated("Invalid stream ticket.") from e
    return claims


async def issue_tokens(
    session: AsyncSession, user: User, family_id: uuid.UUID | None = None
) -> TokenPair:
    now = datetime.now(UTC)
    family_id = family_id or uuid.uuid4()
    raw = secrets.token_urlsafe(32)
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=_sha256(raw),
            expires_at=now + REFRESH_TTL,
        )
    )
    await session.flush()
    access = jwt.encode(
        {"sub": str(user.id), "sid": str(family_id), "iat": now, "exp": now + ACCESS_TTL},
        settings.jwt_secret,
        algorithm=JWT_ALG,
    )
    return TokenPair(
        access_token=access, refresh_token=raw, expires_in=int(ACCESS_TTL.total_seconds())
    )


async def check_login_rate(redis: Redis, ip: str) -> None:
    """Fixed window: every login attempt from an IP counts, success or not
    (`LOGIN_RATE_LIMIT` attempts per `LOGIN_RATE_WINDOW_SECONDS`, default 5 per minute; only
    a dev hub may loosen it, `Settings.login_attempts_allowed`)."""
    key = f"rl:login:{ip}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, settings.login_window_seconds, nx=True)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
    if count > settings.login_attempts_allowed:
        raise AppError(
            429,
            "rate_limited",
            "Too many login attempts. Try again shortly.",
            {"retry_after": max(ttl, 1)},
        )


async def login(session: AsyncSession, email: str, password: str) -> TokenPair:
    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        verify_password(_dummy_hash(), password)  # same cost as a real check
        raise AppError(401, "invalid_credentials", "Email or password is incorrect.")
    if not verify_password(user.password_hash, password) or not user.is_active:
        raise AppError(401, "invalid_credentials", "Email or password is incorrect.")
    return await issue_tokens(session, user)


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def refresh(session: AsyncSession, raw: str) -> TokenPair:
    token_hash, now = _sha256(raw), datetime.now(UTC)
    # Consume atomically: of two concurrent refreshes with one token, only one gets the row.
    consumed = (
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .values(revoked_at=now)
            .returning(RefreshToken.user_id, RefreshToken.family_id)
        )
    ).one_or_none()
    if consumed is None:
        token = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if token is None:
            raise unauthenticated("Invalid refresh token.")
        if token.revoked_at is None:
            raise unauthenticated("Refresh token expired.")
        # A used token came back: assume theft and end the whole session.
        await _revoke_family(session, token.family_id)
        await session.commit()  # keep the revocation even though the request fails
        raise unauthenticated("Refresh token was revoked.")
    user = await session.get(User, consumed.user_id)
    if user is None or not user.is_active:
        raise unauthenticated()
    return await issue_tokens(session, user, consumed.family_id)


async def logout(session: AsyncSession, raw: str) -> None:
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _sha256(raw))
    )
    if token is not None:
        await _revoke_family(session, token.family_id)
