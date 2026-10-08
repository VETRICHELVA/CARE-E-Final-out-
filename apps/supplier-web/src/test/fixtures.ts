// Scenario 1 data (demo-scenarios.md), Supplier Y's side, as the hub would return it.
import type { Me } from "@care-e/api-client";
import type { Demand, Offer, Product, PurchaseOrder } from "../api";

export const ORG_Y = "5f000000-0000-4000-8000-0000000000f1";
export const ORG_A = "0a000000-0000-4000-8000-000000000001";

/** Fixed clock for staleness: offers updated before 2026-09-30T06:00:30Z are stale. */
export const NOW = Date.parse("2026-10-07T06:00:30Z");
const daysAgo = (days: number) => new Date(NOW - days * 86_400_000).toISOString();

const CAPS: Record<string, string[]> = {
  SUPPLIER_DESK: ["po.respond", "source_request.respond"],
  DISPATCHER: ["shipment.assign"],
};

/** A Supplier Y user with one role's capabilities (services/hub-api/app/auth/capabilities.py). */
export function meAs(role: keyof typeof CAPS | "ADMIN", orgType = "SUPPLIER"): Me {
  const capabilities =
    role === "ADMIN"
      ? [
          "shortage.create",
          "recommendation.approve",
          "source_request.respond",
          "receipt.record",
          "inventory.edit",
          "po.respond",
          "shipment.assign",
          "shipment.update_status",
          "audit.read",
        ]
      : CAPS[role]!;
  return {
    user: {
      id: "00000000-0000-4000-8000-0000000000bb",
      email: `${role.toLowerCase()}@supplier-y.demo`,
      full_name: `Supplier Y ${role}`,
      org_id: ORG_Y,
      is_active: true,
    },
    org: {
      id: ORG_Y,
      name: "Supplier Y",
      type: orgType,
      status: "ACTIVE",
      location: { lat: 0, lng: 0 },
    },
    roles: [role],
    capabilities,
  };
}

const product = (n: number, code: string, name: string, unit: string): Product => ({
  id: `9a000000-0000-4000-8000-00000000000${n}`,
  code,
  name,
  category: "Test",
  unit,
  requires_cold_chain: false,
  temp_min_c: null,
  temp_max_c: null,
  default_min_shelf_life_days: 30,
});

export const kitA = product(1, "SURG-KIT-A", "Surgical Kit A", "kits");
export const rdk = product(2, "DIAG-RDK", "Rapid Diagnostic Kit", "kits");
export const cannula = product(3, "IV-CAN-20G", "IV Cannula 20G", "pieces");

export const page = <T>(items: T[]) => ({ items, next_cursor: null });
export const products = page([kitA, rdk, cannula]);

/** Supplier Y's Surgical Kit A offer (₹28, 22 h, 2,000), updated an hour ago. */
export const offerKitA: Offer = {
  id: "0f000000-0000-4000-8000-000000000001",
  org_id: ORG_Y,
  product_id: kitA.id,
  unit_price_paise: 2800,
  lead_time_hours: 22,
  available_qty: 2000,
  created_at: daysAgo(30),
  updated_at: daysAgo(1 / 24),
};

/** A Rapid Diagnostic Kit offer last updated 9 days ago: it fails the freshness gate. */
export const staleRdk: Offer = {
  id: "0f000000-0000-4000-8000-000000000002",
  org_id: ORG_Y,
  product_id: rdk.id,
  unit_price_paise: 90050,
  lead_time_hours: 48,
  available_qty: 50,
  created_at: daysAgo(40),
  updated_at: daysAgo(9),
};

/** Scenario 1 step 5: the approved BUY of 850 kits from Supplier Y at ₹28. */
export const po: PurchaseOrder = {
  id: "90000000-0000-4000-8000-000000000001",
  shortage_id: "5a000000-0000-4000-8000-000000000001",
  supplier_org_id: ORG_Y,
  to_org_id: ORG_A,
  to_org_name: "Hospital A",
  product_id: kitA.id,
  qty: 850,
  unit_price_paise: 2800,
  status: "SENT",
  eta: "2026-10-08T04:00:00Z",
  shipment_id: null,
  created_at: "2026-10-07T06:00:00Z",
  updated_at: "2026-10-07T06:00:00Z",
};

export const poAt = (status: PurchaseOrder["status"], n = 2): PurchaseOrder => ({
  ...po,
  id: `90000000-0000-4000-8000-00000000000${n}`,
  status,
  shipment_id: status === "DISPATCHED" ? "51000000-0000-4000-8000-000000000001" : null,
});

export const demandKitA: Demand = { product_id: kitA.id, open_shortfall_qty: 850 };
export const demandRdk: Demand = { product_id: rdk.id, open_shortfall_qty: 0 };
