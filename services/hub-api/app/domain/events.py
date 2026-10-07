"""Event types (api-and-events.md, Events). An event goes only to the orgs in its `org_ids`."""

from enum import StrEnum


class EventType(StrEnum):
    SHORTAGE_STATUS_CHANGED = "shortage.status_changed"
    SOURCE_REQUEST_CREATED = "source_request.created"
    SOURCE_REQUEST_STATUS_CHANGED = "source_request.status_changed"
    RECOMMENDATION_READY = "recommendation.ready"
    PURCHASE_ORDER_CREATED = "purchase_order.created"
    PURCHASE_ORDER_STATUS_CHANGED = "purchase_order.status_changed"
    SHIPMENT_CREATED = "shipment.created"
    SHIPMENT_STATUS_CHANGED = "shipment.status_changed"
    SHIPMENT_LOCATION = "shipment.location"
    COLDCHAIN_READING = "coldchain.reading"
    COLDCHAIN_EXCURSION = "coldchain.excursion"
    COLDCHAIN_DEVICE_SILENT = "coldchain.device_silent"
    COLDCHAIN_RECOVERED = "coldchain.recovered"
    RECONCILIATION_COMPLETED = "reconciliation.completed"
    SURPLUS_MATCHED = "surplus.matched"
    INVENTORY_CHANGED = "inventory.changed"
    SUPPLIER_OFFER_CHANGED = "supplier_offer.changed"
