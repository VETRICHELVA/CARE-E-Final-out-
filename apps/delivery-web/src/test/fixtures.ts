// SwiftMed Logistics' side of the demo (demo-scenarios.md, seed.py), as the hub would return it.
import type { Me } from "@care-e/api-client";
import type { Product } from "@care-e/ui";
import type { Device, Driver, Shipment, ShipmentDetail, ShipmentStatus, Vehicle } from "../api";

export const ORG_SWIFTMED = "1c000000-0000-4000-8000-000000000001";
export const ORG_A = "0a000000-0000-4000-8000-000000000001";
export const ORG_B = "0b000000-0000-4000-8000-000000000001";
export const ORG_Y = "5f000000-0000-4000-8000-0000000000f1";

const CAPS: Record<string, string[]> = {
  DISPATCHER: ["shipment.assign"],
  DRIVER: ["shipment.update_status"],
  ADMIN: [
    "shortage.create",
    "recommendation.approve",
    "source_request.respond",
    "receipt.record",
    "inventory.edit",
    "po.respond",
    "shipment.assign",
    "shipment.update_status",
    "audit.read",
  ],
  NONE: [],
};

/** A SwiftMed user with one role's capabilities (services/hub-api/app/auth/capabilities.py). */
export function meAs(role: keyof typeof CAPS): Me {
  return {
    user: {
      id: "00000000-0000-4000-8000-0000000000cc",
      email: `${role.toLowerCase()}@swiftmed.demo`,
      full_name: role === "DRIVER" ? "Ravi" : `SwiftMed ${role}`,
      org_id: ORG_SWIFTMED,
      is_active: true,
    },
    org: {
      id: ORG_SWIFTMED,
      name: "SwiftMed Logistics",
      type: "LOGISTICS",
      status: "ACTIVE",
      location: { lat: 12.9784, lng: 77.6408 },
    },
    roles: role === "NONE" ? [] : [role],
    capabilities: CAPS[role]!,
  };
}

const product = (n: number, code: string, name: string, unit: string, cold: boolean): Product => ({
  id: `9a000000-0000-4000-8000-00000000000${n}`,
  code,
  name,
  category: "Test",
  unit,
  requires_cold_chain: cold,
  temp_min_c: cold ? 2 : null,
  temp_max_c: cold ? 8 : null,
  default_min_shelf_life_days: 30,
});

export const kitA = product(1, "SURG-KIT-A", "Surgical Kit A", "kits", false);
export const rdk = product(2, "DIAG-RDK", "Rapid Diagnostic Kit", "kits", true);

export const page = <T>(items: T[]) => ({ items, next_cursor: null });
export const products = page([kitA, rdk]);

export const ravi: Driver = {
  id: "d1000000-0000-4000-8000-000000000001",
  user_id: "00000000-0000-4000-8000-0000000000cc",
  name: "Ravi",
  phone: "+91 90000 00001",
  active: true,
};
export const priya: Driver = {
  id: "d1000000-0000-4000-8000-000000000002",
  user_id: "00000000-0000-4000-8000-0000000000cd",
  name: "Priya",
  phone: "+91 90000 00002",
  active: true,
};
export const van: Vehicle = {
  id: "e1000000-0000-4000-8000-000000000001",
  reg_no: "KA-01-SM-0001",
  has_cold_chain: false,
};
export const coldVan: Vehicle = {
  id: "e1000000-0000-4000-8000-000000000002",
  reg_no: "KA-01-SM-0002",
  has_cold_chain: true,
};

const pickupB = {
  seq: 1,
  stop_type: "PICKUP" as const,
  place: "Hospital B Main Store",
  lat: 12.9279,
  lng: 77.6271,
  planned_at: null,
  actual_at: null,
};
const dropA = {
  seq: 2,
  stop_type: "DROP" as const,
  place: "Hospital A Main Store",
  lat: 12.9592,
  lng: 77.6974,
  planned_at: "2026-10-08T09:00:00Z",
  actual_at: null,
};

/** Scenario 1: B's 350 kits of Surgical Kit A to A, unassigned. */
export const transfer: Shipment = {
  id: "51000000-0000-4000-8000-000000000001",
  shortage_id: "5a000000-0000-4000-8000-000000000001",
  source_request_id: "5b000000-0000-4000-8000-000000000001",
  purchase_order_id: null,
  from_org_id: ORG_B,
  from_org_name: "Hospital B",
  to_org_id: ORG_A,
  to_org_name: "Hospital A",
  carrier_org_id: null,
  carrier_org_name: null,
  product_id: kitA.id,
  product_code: kitA.code,
  product_name: kitA.name,
  qty: 350,
  requires_cold_chain: false,
  status: "CREATED",
  priority: "ROUTINE",
  required_by: "2026-10-09T06:00:00Z",
  driver: null,
  vehicle: null,
  device_id: null,
  planned_eta: "2026-10-08T09:00:00Z",
  eta: null,
  route_distance_km: null,
  route_provider: null,
  pickup: pickupB,
  drop: dropA,
  created_at: "2026-10-08T06:00:00Z",
  updated_at: "2026-10-08T06:00:00Z",
};

/** Scenario 2: Rapid Diagnostic Kits (2-8 °C) from Supplier Y to A, critical, unassigned. */
export const coldShipment: Shipment = {
  ...transfer,
  id: "51000000-0000-4000-8000-000000000002",
  source_request_id: null,
  purchase_order_id: "90000000-0000-4000-8000-000000000001",
  from_org_id: ORG_Y,
  from_org_name: "Supplier Y",
  product_id: rdk.id,
  product_code: rdk.code,
  product_name: rdk.name,
  qty: 40,
  requires_cold_chain: true,
  priority: "CRITICAL",
  pickup: { ...pickupB, place: "Supplier Y", lat: 12.85, lng: 77.66 },
};

/** `transfer` at `status`, assigned to Ravi in the cold van with a stored road route. */
export function assigned(status: ShipmentStatus, base: Shipment = transfer, n = 3): Shipment {
  return {
    ...base,
    id: `51000000-0000-4000-8000-00000000000${n}`,
    status,
    carrier_org_id: ORG_SWIFTMED,
    carrier_org_name: "SwiftMed Logistics",
    driver: { id: ravi.id, name: ravi.name },
    vehicle: coldVan,
    eta: "2026-10-08T08:15:00Z",
    route_distance_km: 9.42,
    route_provider: "OSRM",
  };
}

export const detail = (
  shipment: Shipment,
  extra: Partial<ShipmentDetail> = {},
): ShipmentDetail => ({
  ...shipment,
  inspection_note_required: false,
  receipt: null,
  route_geometry: shipment.driver
    ? {
        type: "LineString",
        coordinates: [
          [77.6271, 12.9279],
          [77.66, 12.94],
          [77.6974, 12.9592],
        ],
      }
    : null,
  status_history: [
    { from_status: null, to_status: "CREATED", at: "2026-10-08T06:00:00Z" },
    ...(shipment.driver
      ? [
          {
            from_status: "CREATED" as const,
            to_status: "ASSIGNED" as const,
            at: "2026-10-08T06:05:00Z",
          },
        ]
      : []),
  ],
  last_location: null,
  ...extra,
});

export const coldBox: Device = {
  id: "de000000-0000-4000-8000-000000000001",
  org_id: ORG_SWIFTMED,
  device_id: "cb-01",
  type: "COLD_BOX",
  battery_level: 87,
  last_seen: "2026-10-08T05:48:00Z",
  assigned_shipment_id: null,
};
export const spareBox: Device = {
  ...coldBox,
  id: "de000000-0000-4000-8000-000000000002",
  device_id: "cb-02",
  battery_level: null,
  last_seen: null,
};

/** Fixed clock: 2026-10-08 06:00 UTC, 12 minutes after cb-01 was last seen. */
export const NOW = Date.parse("2026-10-08T06:00:00Z");
