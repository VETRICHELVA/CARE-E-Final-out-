import uuid

from pydantic import BaseModel

from app.orgs.schemas import OrgOut


class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token lifetime in seconds


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    org_id: uuid.UUID
    is_active: bool


class MeOut(BaseModel):
    user: UserOut
    org: OrgOut
    roles: list[str]
    capabilities: list[str]
