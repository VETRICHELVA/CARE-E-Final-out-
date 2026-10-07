"""Purchase orders and shipments (business-rules.md §7 step 5, §8). Pure, no I/O."""

from enum import StrEnum


class PoStatus(StrEnum):
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    REJECTED = "REJECTED"


P = PoStatus
# §8 PurchaseOrder. Any transition not listed is a 409 `invalid_transition`.
PO_TRANSITIONS: dict[str, set[str]] = {
    P.SENT: {P.ACKNOWLEDGED, P.REJECTED},
    P.ACKNOWLEDGED: {P.DISPATCHED, P.REJECTED},
    P.DISPATCHED: {P.DELIVERED},
}


class ShipmentStatus(StrEnum):
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    RECONCILED = "RECONCILED"


S = ShipmentStatus
# §8 Shipment. S09 only creates shipments (CREATED); S11 moves them.
SHIPMENT_TRANSITIONS: dict[str, set[str]] = {
    S.CREATED: {S.ASSIGNED},
    S.ASSIGNED: {S.PICKED_UP, S.CREATED},
    S.PICKED_UP: {S.IN_TRANSIT},
    S.IN_TRANSIT: {S.DELIVERED},
    S.DELIVERED: {S.RECONCILED},
}

# Factual SYSTEM reason (§10) for the re-run after a supplier rejects a PO.
PO_REJECTED = "The supplier rejected the purchase order."
