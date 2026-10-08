"""The fixed role -> capability map. Roles live in the `role` table; what they allow lives here."""

from collections.abc import Iterable
from enum import StrEnum


class RoleName(StrEnum):
    STORE_MANAGER = "STORE_MANAGER"
    REQUESTER = "REQUESTER"
    APPROVER = "APPROVER"
    RECEIVER = "RECEIVER"
    SUPPLIER_DESK = "SUPPLIER_DESK"
    DISPATCHER = "DISPATCHER"
    DRIVER = "DRIVER"
    ADMIN = "ADMIN"


class Capability(StrEnum):
    SHORTAGE_CREATE = "shortage.create"
    RECOMMENDATION_APPROVE = "recommendation.approve"
    SOURCE_REQUEST_RESPOND = "source_request.respond"
    RECEIPT_RECORD = "receipt.record"
    INVENTORY_EDIT = "inventory.edit"
    PO_RESPOND = "po.respond"
    SHIPMENT_ASSIGN = "shipment.assign"
    SHIPMENT_UPDATE_STATUS = "shipment.update_status"
    AUDIT_READ = "audit.read"


class ServiceScope(StrEnum):
    """What a service token may do. A service token is not a user and opens no user endpoint."""

    TELEMETRY_WRITE = "telemetry.write"
    AI_READ = "ai.read"


C = Capability
ROLE_CAPABILITIES: dict[RoleName, frozenset[Capability]] = {
    RoleName.STORE_MANAGER: frozenset(
        {C.INVENTORY_EDIT, C.SHORTAGE_CREATE, C.SOURCE_REQUEST_RESPOND}
    ),
    RoleName.REQUESTER: frozenset({C.SHORTAGE_CREATE}),
    RoleName.APPROVER: frozenset({C.RECOMMENDATION_APPROVE, C.AUDIT_READ}),
    RoleName.RECEIVER: frozenset({C.RECEIPT_RECORD}),
    RoleName.SUPPLIER_DESK: frozenset({C.PO_RESPOND, C.SOURCE_REQUEST_RESPOND}),
    RoleName.DISPATCHER: frozenset({C.SHIPMENT_ASSIGN}),
    RoleName.DRIVER: frozenset({C.SHIPMENT_UPDATE_STATUS}),
    RoleName.ADMIN: frozenset(Capability),
}


def capabilities_for(role_names: Iterable[str]) -> set[Capability]:
    return {c for r in role_names for c in ROLE_CAPABILITIES.get(RoleName(r), frozenset())}
