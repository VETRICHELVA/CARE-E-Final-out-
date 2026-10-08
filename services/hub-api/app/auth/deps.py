"""Request-time authentication and authorization helpers."""

import hmac
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.capabilities import Capability, RoleName, ServiceScope, capabilities_for
from app.auth.models import RefreshToken, User
from app.auth.service import decode_access_token, decode_stream_ticket, unauthenticated
from app.config import settings
from app.db import SessionDep
from app.errors import AppError
from app.orgs.models import OrgType

bearer = HTTPBearer(auto_error=False)


async def current_user(
    session: SessionDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if creds is None:
        raise unauthenticated("Sign in first.")
    return await user_from_claims(session, decode_access_token(creds.credentials))


async def access_claims(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    """The verified claims of the caller's access token (use with CurrentUser)."""
    if creds is None:
        raise unauthenticated("Sign in first.")
    return decode_access_token(creds.credentials)


async def stream_user(
    session: SessionDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ticket: Annotated[
        str | None, Query(description="A stream ticket from POST /events/ticket.")
    ] = None,
) -> User:
    """GET /events/stream: a stream ticket (`?ticket=`, for EventSource) or an access token."""
    if ticket is not None:
        claims = decode_stream_ticket(ticket)
    elif creds is not None:
        claims = decode_access_token(creds.credentials)
    else:
        raise unauthenticated("Sign in first.")
    return await user_from_claims(session, claims)


async def user_from_claims(session: AsyncSession, claims: dict[str, Any]) -> User:
    try:
        user_id, family_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except (KeyError, ValueError) as e:
        raise unauthenticated("Invalid access token.") from e
    # Logout revokes the refresh family, which also ends its access tokens.
    live = await session.scalar(
        select(
            exists().where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        )
    )
    user = await session.get(User, user_id)
    if not live or user is None or not user.is_active:
        raise unauthenticated()
    return user


CurrentUser = Annotated[User, Depends(current_user)]
StreamUser = Annotated[User, Depends(stream_user)]


def require_role(role: RoleName) -> Callable[..., Awaitable[User]]:
    """403 unless the user has `role` (e.g. ADMIN: "org admin" in api-and-events.md)."""

    async def dependency(user: CurrentUser) -> User:
        if all(r.name != role for r in user.roles):
            raise AppError(403, "forbidden", f"Only an organization {role} can do this.")
        return user

    return dependency


def user_capabilities(user: User) -> set[Capability]:
    return capabilities_for(r.name for r in user.roles)


def is_platform_admin(user: User) -> bool:
    return user.org.type == OrgType.PLATFORM and any(r.name == RoleName.ADMIN for r in user.roles)


def require(
    capability: Capability, org_type: OrgType | None = None
) -> Callable[..., Awaitable[User]]:
    """403 unless the user has `capability` and, if given, belongs to an `org_type` org
    (business-rules.md §2: batches are HOSPITAL-only, supplier offers SUPPLIER-only)."""

    async def dependency(user: CurrentUser) -> User:
        if capability not in user_capabilities(user):
            raise AppError(
                403, "forbidden", f"Missing capability {capability}.", {"capability": capability}
            )
        if org_type is not None and user.org.type != org_type:
            raise AppError(
                403,
                "forbidden",
                f"Only {org_type} organizations can do this.",
                {"org_type": org_type},
            )
        return user

    return dependency


def ensure_any(user: User, *capabilities: Capability) -> User:
    """403 unless the user has at least one of `capabilities` (`require_any` as a call)."""
    if user_capabilities(user).isdisjoint(capabilities):
        names = ", ".join(capabilities)
        raise AppError(
            403,
            "forbidden",
            f"Needs one of these capabilities: {names}.",
            {"capabilities": list(capabilities)},
        )
    return user


def require_any(*capabilities: Capability) -> Callable[..., Awaitable[User]]:
    """403 unless the user has at least one of `capabilities`."""

    async def dependency(user: CurrentUser) -> User:
        return ensure_any(user, *capabilities)

    return dependency


def service_token_scopes(token: str) -> frozenset[ServiceScope]:
    """Scopes granted to a service bearer token; empty for anything else (user JWTs too)."""
    ingest = settings.ingest_token.encode()
    if ingest and hmac.compare_digest(token.encode(), ingest):
        return frozenset({ServiceScope.TELEMETRY_WRITE})
    ai = settings.ai_service_token.encode()
    if ai and hmac.compare_digest(token.encode(), ai):
        return frozenset({ServiceScope.AI_READ})
    return frozenset()


def require_scope(scope: ServiceScope) -> Callable[..., Awaitable[ServiceScope]]:
    """401 unless the bearer is a service token with `scope`. User access tokens are refused
    here, and service tokens are refused by `current_user` (they are not JWTs)."""

    async def dependency(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> ServiceScope:
        if creds is None or scope not in service_token_scopes(creds.credentials):
            raise unauthenticated(f"This endpoint needs a service token with scope {scope}.")
        return scope

    return dependency


def org_scoped[S: Select[Any]](stmt: S, user: User) -> S:
    """Limit a select of an org-owned model to the caller's org."""
    model = stmt.column_descriptions[0]["entity"]
    return stmt.where(model.org_id == user.org_id)
