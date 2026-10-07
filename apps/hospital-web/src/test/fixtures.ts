// Scenario 1 data (demo-scenarios.md) as the hub would return it.
import type { Me } from "@care-e/api-client";
import type { AuditRow, Batch, Candidate, Facility, MatchRun, Product, Shortage } from "../api";

export const ORG_A = "0a000000-0000-4000-8000-000000000001";
export const FACILITY_A = "fa000000-0000-4000-8000-000000000001";

const CAPS: Record<string, string[]> = {
  STORE_MANAGER: ["inventory.edit", "shortage.create", "source_request.respond"],
  REQUESTER: ["shortage.create"],
  APPROVER: ["recommendation.approve", "audit.read"],
  RECEIVER: ["receipt.record"],
};

/** A Hospital A user with one role's capabilities (services/hub-api/app/auth/capabilities.py). */
export function meAs(role: keyof typeof CAPS | "ADMIN", orgType = "HOSPITAL"): Me {
  const capabilities =
    role === "ADMIN" ? [...new Set(Object.values(CAPS).flat()), "audit.read"] : CAPS[role]!;
  return {
    user: {
      id: "00000000-0000-4000-8000-0000000000aa",
      email: `${role.toLowerCase()}@hospital-a.demo`,
      full_name: `Hospital A ${role}`,
      org_id: ORG_A,
      is_active: true,
    },
    org: {
      id: ORG_A,
      name: "Hospital A",
      type: orgType,
      status: "ACTIVE",
      location: { lat: 0, lng: 0 },
    },
    roles: [role],
    capabilities,
  };
}

export const kitA: Product = {
  id: "9a000000-0000-4000-8000-000000000001",
  code: "SURG-KIT-A",
  name: "Surgical Kit A",
  category: "Surgical",
  unit: "kits",
  requires_cold_chain: false,
  temp_min_c: null,
  temp_max_c: null,
  default_min_shelf_life_days: 30,
};

export const products = { items: [kitA], next_cursor: null };

export const facility: Facility = {
  id: FACILITY_A,
  org_id: ORG_A,
  name: "Hospital A Main Store",
  address: "1 Main Road",
  has_cold_storage: true,
  location: { lat: 0, lng: 0 },
};

export const batch: Batch = {
  id: "ba000000-0000-4000-8000-000000000001",
  org_id: ORG_A,
  facility_id: FACILITY_A,
  product_id: kitA.id,
  batch_no: "SKA-2026-01",
  on_hand: 2500,
  reserved: 800,
  allocated: 200,
  safety_stock: 500,
  quarantined: 0,
  expiry_date: "2027-04-05",
  unit_cost_paise: 1400,
  last_verified_at: "2026-10-07T04:00:00Z",
  created_at: "2026-10-01T04:00:00Z",
  updated_at: "2026-10-07T04:00:00Z",
  transferable: 1000,
  held_qty: 0,
};

export const shortage: Shortage = {
  id: "5a000000-0000-4000-8000-000000000001",
  org_id: ORG_A,
  facility_id: FACILITY_A,
  product_id: kitA.id,
  qty_required: 1000,
  qty_local_usable: 150,
  shortfall: 850,
  required_by: "2026-10-10T06:00:00Z",
  priority: "CRITICAL",
  min_shelf_life_days: 30,
  status: "MATCHING",
  notes: null,
  parent_shortage_id: null,
  created_by: "00000000-0000-4000-8000-0000000000aa",
  source: "FORM",
  created_at: "2026-10-07T06:00:00Z",
  updated_at: "2026-10-07T06:00:01Z",
};

const gates = (fail: Record<string, string> = {}) =>
  ["product", "quantity", "shelf_life", "authorization", "freshness", "deadline", "cold_chain"].map(
    (gate) => ({ gate, passed: !(gate in fail), reason: fail[gate] ?? null }),
  );

let n = 0;
function candidate(
  name: string,
  type: "HOSPITAL" | "SUPPLIER",
  fields: Partial<Candidate>,
  fail?: Record<string, string>,
): Candidate {
  n += 1;
  const eligible = !fail;
  return {
    id: `ca000000-0000-4000-8000-00000000000${n}`,
    source_org_id: `0b000000-0000-4000-8000-00000000000${n}`,
    source_org_name: name,
    source_type: type,
    transferable_qty: null,
    offered_qty: null,
    gate_results: gates(fail),
    eligible,
    landed_cost_paise: null,
    eta_hours: 6,
    reliability: 70,
    rank: null,
    ...fields,
  };
}

export const hospitalB = candidate("Hospital B", "HOSPITAL", {
  transferable_qty: 1000,
  eta_hours: 6.2,
  rank: 1,
});
const supplierY = candidate("Supplier Y", "SUPPLIER", {
  offered_qty: 2000,
  eta_hours: 24,
  landed_cost_paise: 2_400_000,
  rank: 2,
});
const supplierX = candidate("Supplier X", "SUPPLIER", {
  offered_qty: 5000,
  eta_hours: 68,
  landed_cost_paise: 1_200_000,
  rank: 3,
});
const hospitalC = candidate(
  "Hospital C",
  "HOSPITAL",
  { transferable_qty: 100 },
  { quantity: "Only 100 transferable; 850 needed" },
);
const hospitalD = candidate(
  "Hospital D",
  "HOSPITAL",
  { transferable_qty: 900 },
  { shelf_life: "Expires in 12 days; 30 required" },
);
const hospitalE = candidate(
  "Hospital E",
  "HOSPITAL",
  { transferable_qty: 1200 },
  { authorization: "Not authorized to supply this product" },
);

export const matchRun: MatchRun = {
  id: "aa000000-0000-4000-8000-000000000001",
  shortage_id: shortage.id,
  run_no: 1,
  triggered_by: "CREATE",
  ts: "2026-10-07T06:00:01Z",
  excluded_org_ids: [],
  planned_resolution: {
    type: "TRANSFER",
    lines: [
      {
        candidate_id: hospitalB.id,
        source_org_id: hospitalB.source_org_id,
        source_type: "HOSPITAL",
        qty: 850,
        landed_cost_paise: null,
        eta_hours: 6.2,
      },
    ],
    alternatives: [],
  },
  reason: null,
  candidates: [hospitalB, supplierY, supplierX, hospitalC, hospitalD, hospitalE],
};

export const auditRows: AuditRow[] = [
  {
    id: "ad000000-0000-4000-8000-000000000002",
    actor_id: null,
    org_id: ORG_A,
    entity: "shortage",
    entity_id: shortage.id,
    action: "shortage.status_changed",
    before: { status: "OPEN" },
    after: { status: "MATCHING" },
    reason: "No reason was entered.",
    reason_source: "SYSTEM",
    ts: "2026-10-07T06:00:01Z",
  },
  {
    id: "ad000000-0000-4000-8000-000000000001",
    actor_id: "00000000-0000-4000-8000-0000000000aa",
    org_id: ORG_A,
    entity: "shortage",
    entity_id: shortage.id,
    action: "shortage.created",
    before: null,
    after: { status: "OPEN" },
    reason: "No reason was entered.",
    reason_source: "SYSTEM",
    ts: "2026-10-07T06:00:00Z",
  },
];

export const page = <T>(items: T[]) => ({ items, next_cursor: null });
