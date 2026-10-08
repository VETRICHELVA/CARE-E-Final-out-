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
# §8 Shipment. Any transition not listed is a 409 `invalid_transition`.
SHIPMENT_TRANSITIONS: dict[str, set[str]] = {
    S.CREATED: {S.ASSIGNED},
    S.ASSIGNED: {S.PICKED_UP, S.CREATED},
    S.PICKED_UP: {S.IN_TRANSIT},
    S.IN_TRANSIT: {S.DELIVERED},
    S.DELIVERED: {S.RECONCILED},
}
# The part of §8 the assigned driver moves (POST /shipments/{id}/status), one step at a
# time. Assign and unassign (CREATED <-> ASSIGNED) are the dispatcher's; RECONCILED is S12's.
DRIVER_TRANSITIONS: dict[str, set[str]] = {
    S.ASSIGNED: {S.PICKED_UP},
    S.PICKED_UP: {S.IN_TRANSIT},
    S.IN_TRANSIT: {S.DELIVERED},
}
# A shipment on its way: a cold box's readings are linked to it only in these states (S14),
# and the driver's location pings are accepted only in these states.
ACTIVE_SHIPMENT = frozenset({S.ASSIGNED, S.PICKED_UP, S.IN_TRANSIT})
# Where a device can no longer be attached: the stock has arrived.
ARRIVED = frozenset({S.DELIVERED, S.RECONCILED})


class StopType(StrEnum):
    PICKUP = "PICKUP"
    DROP = "DROP"


class RouteProvider(StrEnum):
    """Which provider gave a stored route: OSRM, or the haversine fallback (§4)."""

    OSRM = "OSRM"
    HAVERSINE = "HAVERSINE"


def driver_may_move(status: str, to: str) -> bool:
    return to in DRIVER_TRANSITIONS.get(status, set())


def cold_chain_vehicle_refusal(
    requires_cold_chain: bool, has_cold_chain: bool, reg_no: str
) -> str | None:
    """Why this vehicle cannot carry this shipment, or None if it can (S11: a cold-chain
    shipment needs a cold-chain vehicle)."""
    if requires_cold_chain and not has_cold_chain:
        return f"This shipment needs a cold-chain vehicle; vehicle {reg_no} has no cold chain."
    return None


# Factual SYSTEM reasons (§10).
PO_REJECTED = "The supplier rejected the purchase order."  # the re-run after a PO rejection
# The source's on_hand and FIRM hold drawn down at pickup (§9), recorded in the source org.
PICKUP_RECORDED = "The driver recorded the pickup of this shipment."
