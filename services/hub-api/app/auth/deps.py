"""Request-time authentication and authorization helpers."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, exists, select

from app.auth.capabilities import Capability, RoleName, capabilities_for
from app.auth.models import RefreshToken, User
from app.auth.service import decode_access_token, unauthenticated
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
    claims = decode_access_token(creds.credentials)
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


def user_capabilities(user: User) -> set[Capability]:
    return capabilities_for(r.name for r in user.roles)


def is_platform_admin(user: User) -> bool:
    return user.org.type == OrgType.PLATFORM and any(r.name == RoleName.ADMIN for r in user.roles)


def require(capability: Capability) -> Callable[..., Awaitable[User]]:
    async def dependency(user: CurrentUser) -> User:
        if capability not in user_capabilities(user):
            raise AppError(
                403, "forbidden", f"Missing capability {capability}.", {"capability": capability}
            )
        return user

    return dependency


def org_scoped[S: Select[Any]](stmt: S, user: User) -> S:
    """Limit a select of an org-owned model to the caller's org."""
    model = stmt.column_descriptions[0]["entity"]
    return stmt.where(model.org_id == user.org_id)
