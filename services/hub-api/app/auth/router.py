from typing import Annotated

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis

from app.auth import service
from app.auth.deps import CurrentUser, user_capabilities
from app.auth.schemas import LoginIn, MeOut, RefreshIn, TokenPair, UserOut
from app.db import SessionDep
from app.orgs.schemas import OrgOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    session: SessionDep,
    redis: Annotated[Redis, Depends(service.get_redis)],
) -> TokenPair:
    await service.check_login_rate(redis, request.client.host if request.client else "unknown")
    pair = await service.login(session, body.email, body.password)
    await session.commit()
    return pair


@router.post("/refresh")
async def refresh(body: RefreshIn, session: SessionDep) -> TokenPair:
    pair = await service.refresh(session, body.refresh_token)
    await session.commit()
    return pair


@router.post("/logout", status_code=204)
async def logout(body: RefreshIn, session: SessionDep) -> None:
    await service.logout(session, body.refresh_token)
    await session.commit()


@router.get("/me")
async def me(user: CurrentUser) -> MeOut:
    return MeOut(
        user=UserOut.model_validate(user, from_attributes=True),
        org=OrgOut.of(user.org),
        roles=sorted(r.name for r in user.roles),
        capabilities=sorted(user_capabilities(user)),
    )
