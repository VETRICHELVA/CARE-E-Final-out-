import uuid

from pydantic import BaseModel, ConfigDict

from app.orgs.models import Facility, Organization


class Location(BaseModel):
    lat: float
    lng: float


class PublicOrgView(BaseModel):
    """What any org may see of another org: name, type, location only."""

    model_config = ConfigDict(extra="forbid")  # never matches a full OrgOut payload

    name: str
    type: str
    location: Location

    @classmethod
    def of(cls, org: Organization) -> "PublicOrgView":
        return cls(name=org.name, type=org.type, location=Location(lat=org.lat, lng=org.lng))


class OrgOut(PublicOrgView):
    id: uuid.UUID
    status: str

    @classmethod
    def of(cls, org: Organization) -> "OrgOut":
        return cls(
            id=org.id,
            name=org.name,
            type=org.type,
            status=org.status,
            location=Location(lat=org.lat, lng=org.lng),
        )


class PublicFacilityView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    location: Location

    @classmethod
    def of(cls, f: Facility) -> "PublicFacilityView":
        return cls(name=f.name, location=Location(lat=f.lat, lng=f.lng))


class FacilityOut(PublicFacilityView):
    id: uuid.UUID
    org_id: uuid.UUID
    address: str
    has_cold_storage: bool

    @classmethod
    def of(cls, f: Facility) -> "FacilityOut":
        return cls(
            id=f.id,
            org_id=f.org_id,
            name=f.name,
            address=f.address,
            has_cold_storage=f.has_cold_storage,
            location=Location(lat=f.lat, lng=f.lng),
        )
