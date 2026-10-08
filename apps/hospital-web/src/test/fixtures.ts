// Scenario 1 data (demo-scenarios.md) as the hub would return it.
import type { Me } from "@care-e/api-client";
import type {
  AuditRow,
  Batch,
  Candidate,
  Facility,
  MatchRun,
  Notification,
  Receipt,
  Product,
  Recommendation,
  RecommendationLine,
  Shipment,
  ShipmentDetail,
  Shortage,
  SourceRequest,
} from "../api";

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
export const supplierY = candidate("Supplier Y", "SUPPLIER", {
  offered_qty: 2000,
  eta_hours: 24,
  landed_cost_paise: 2_400_000,
  rank: 2,
});
export const supplierX = candidate("Supplier X", "SUPPLIER", {
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

/** The clock the source-request fixtures are relative to (Scenario 1, step 2). */
export const NOW = Date.parse("2026-10-07T06:00:30Z");

/** Scenario 1 step 2, as Hospital A sees it: the hub asks Hospital B for 850 kits, with a
 *  15-minute response deadline (14:31 left at NOW). */
export const requestToB: SourceRequest = {
  id: "5e000000-0000-4000-8000-000000000001",
  shortage_id: shortage.id,
  requester_org_id: ORG_A,
  requester_org_name: "Hospital A",
  source_org_id: hospitalB.source_org_id,
  source_org_name: "Hospital B",
  product_id: kitA.id,
  priority: "CRITICAL",
  required_by: shortage.required_by,
  qty: 850,
  status: "REQUESTED",
  sla_deadline: "2026-10-07T06:15:01Z",
  hold_expires_at: null,
  held_qty: 0,
  holds: null,
  responded_by: null,
  responded_at: null,
  decline_reason: null,
  reason_source: null,
  created_at: "2026-10-07T06:00:01Z",
  updated_at: "2026-10-07T06:00:01Z",
};

/** A request to Hospital A from Hospital D, as Hospital A's store manager sees it. */
export const incoming: SourceRequest = {
  ...requestToB,
  id: "5e000000-0000-4000-8000-000000000002",
  shortage_id: "5a000000-0000-4000-8000-000000000009",
  requester_org_id: "0b000000-0000-4000-8000-0000000000d0",
  requester_org_name: "Hospital D",
  source_org_id: ORG_A,
  source_org_name: "Hospital A",
  qty: 300,
  priority: "ROUTINE",
  holds: [],
};

export const page = <T>(items: T[]) => ({ items, next_cursor: null });

// ---- S12: recommendations, shipments and audit rows ----

export const ORG_B = hospitalB.source_org_id;
export const ORG_Y = supplierY.source_org_id;
export const ORG_LOGI = "0c000000-0000-4000-8000-000000000001";
export const ME_ID = "00000000-0000-4000-8000-0000000000aa";

/** Hospital B's line as Hospital A sees it: no unit price or cost (CLAUDE.md rule 6). */
const lineB: RecommendationLine = {
  candidate_id: hospitalB.id,
  source_org_id: ORG_B,
  source_org_name: "Hospital B",
  source_type: "HOSPITAL",
  source_request_id: requestToB.id,
  qty: 850,
  eta_hours: 6.2,
  shelf_life_days: 180,
  unit_price_paise: null,
  landed_cost_paise: null,
};
const lineY: RecommendationLine = {
  candidate_id: supplierY.id,
  source_org_id: ORG_Y,
  source_org_name: "Supplier Y",
  source_type: "SUPPLIER",
  source_request_id: null,
  qty: 850,
  eta_hours: 24,
  shelf_life_days: null,
  unit_price_paise: 2_800,
  landed_cost_paise: 2_400_000,
};
const lineX: RecommendationLine = {
  ...lineY,
  candidate_id: supplierX.id,
  source_org_id: supplierX.source_org_id,
  source_org_name: "Supplier X",
  eta_hours: 68,
  unit_price_paise: 1_400,
  landed_cost_paise: 1_200_000,
};

/** Scenario 1 before B declines: TRANSFER from B, Supplier Y as the BUY alternative. Valid
 *  until 06:30:01 (29:31 left at NOW). */
export const transferRec: Recommendation = {
  id: "ec000000-0000-4000-8000-000000000001",
  shortage_id: shortage.id,
  match_run_id: matchRun.id,
  type: "TRANSFER",
  status: "PENDING",
  lines: [lineB],
  alternatives: [lineY],
  total_landed_cost_paise: null,
  explanation:
    "Hospital B: 850 kits, about 6.2 h away, 180 days of shelf life at delivery. Sources are ranked by earliest arrival because the shortage is CRITICAL.",
  expires_at: "2026-10-07T06:30:01Z",
  decided_by: null,
  decided_at: null,
  reason: null,
  reason_source: null,
  created_at: "2026-10-07T06:00:20Z",
  updated_at: "2026-10-07T06:00:20Z",
};

export const splitRec: Recommendation = {
  ...transferRec,
  type: "TRANSFER_SPLIT",
  lines: [
    { ...lineB, qty: 500 },
    {
      ...lineB,
      candidate_id: "ca000000-0000-4000-8000-0000000000c9",
      source_org_id: "0b000000-0000-4000-8000-0000000000c9",
      source_org_name: "Hospital C",
      qty: 350,
      shelf_life_days: 200,
    },
  ],
};

/** Scenario 1 step 4: BUY from Supplier Y, Supplier X as the alternative. */
export const buyRec: Recommendation = {
  ...transferRec,
  type: "BUY",
  lines: [lineY],
  alternatives: [lineX],
  total_landed_cost_paise: 2_400_000,
};

/** The hub's `recommendation.created` row for `rec`, which the lookup reads. */
export const recCreated = (rec: Recommendation): AuditRow => ({
  id: "ad000000-0000-4000-8000-0000000000e1",
  actor_id: null,
  org_id: ORG_A,
  entity: "recommendation",
  entity_id: rec.id,
  action: "recommendation.created",
  before: null,
  after: {
    status: "PENDING",
    type: rec.type,
    match_run_id: rec.match_run_id,
    expires_at: rec.expires_at,
  },
  reason: "Every source planned by match run 1 holds stock.",
  reason_source: "SYSTEM",
  ts: rec.created_at,
});

export const PO_ID = "f0000000-0000-4000-8000-000000000001";

/** Supplier Y's dispatched order on its way to Hospital A, 2 h out at NOW. */
export const shipmentToA: ShipmentDetail = {
  id: "5b000000-0000-4000-8000-000000000001",
  shortage_id: shortage.id,
  source_request_id: null,
  purchase_order_id: PO_ID,
  from_org_id: ORG_Y,
  from_org_name: "Supplier Y",
  to_org_id: ORG_A,
  to_org_name: "Hospital A",
  carrier_org_id: ORG_LOGI,
  carrier_org_name: "SwiftMed Logistics",
  product_id: kitA.id,
  product_code: kitA.code,
  product_name: kitA.name,
  qty: 850,
  requires_cold_chain: false,
  status: "IN_TRANSIT",
  priority: "CRITICAL",
  required_by: shortage.required_by,
  driver: { id: "d0000000-0000-4000-8000-000000000001", name: "Ravi" },
  vehicle: {
    id: "e0000000-0000-4000-8000-000000000001",
    reg_no: "KA-01-AB-1234",
    has_cold_chain: false,
  },
  device_id: null,
  planned_eta: "2026-10-07T07:30:00Z",
  eta: "2026-10-07T08:00:30Z",
  route_distance_km: 42,
  route_provider: "OSRM",
  pickup: null,
  drop: null,
  created_at: "2026-10-07T06:00:25Z",
  updated_at: "2026-10-07T06:00:29Z",
  route_geometry: null,
  status_history: [],
  last_location: null,
  inspection_note_required: false,
  receipt: null,
};

/** Hospital A sending stock to Hospital D: outgoing, so not one of A's deliveries. */
export const shipmentFromA: Shipment = {
  ...shipmentToA,
  id: "5b000000-0000-4000-8000-000000000002",
  purchase_order_id: null,
  from_org_id: ORG_A,
  from_org_name: "Hospital A",
  to_org_id: "0b000000-0000-4000-8000-0000000000d0",
  to_org_name: "Hospital D",
  product_name: "Outgoing kit",
};

/** A row of Hospital A's audit trail. */
export const auditRow = (fields: Partial<AuditRow> & Pick<AuditRow, "id">): AuditRow => ({
  actor_id: null,
  org_id: ORG_A,
  entity: "shortage",
  entity_id: shortage.id,
  action: "shortage.status_changed",
  before: null,
  after: null,
  reason: "No reason was entered.",
  reason_source: "SYSTEM",
  ts: "2026-10-07T06:00:00Z",
  ...fields,
});

// ---- S12 part 2: receipts, reconciliation and notifications ----

export const RECEIPT_ID = "ae000000-0000-4000-8000-000000000101";
export const RESIDUAL_ID = "5a000000-0000-4000-8000-000000000060";

/** Supplier Y's 850 kits, delivered to Hospital A and waiting for the receipt (step 7). */
export const deliveredToA: ShipmentDetail = {
  ...shipmentToA,
  status: "DELIVERED",
  eta: null,
  drop: {
    seq: 2,
    stop_type: "DROP",
    place: "Hospital A Main Store",
    lat: 0,
    lng: 0,
    planned_at: null,
    actual_at: "2026-10-07T08:00:00Z",
  },
};

/** Scenario 1 step 7: 790 of 850 accepted, reconciled PARTIAL with a residual of 60. */
export const partialReceipt: Receipt = {
  id: RECEIPT_ID,
  shipment_id: shipmentToA.id,
  shortage_id: shortage.id,
  expected: 850,
  received: 850,
  accepted: 790,
  rejected: 60,
  condition: "DAMAGED",
  inspection_note: null,
  received_by: ME_ID,
  ts: "2026-10-07T09:00:00Z",
  batch_id: "ba000000-0000-4000-8000-000000000079",
  reconciliation: {
    id: "ae000000-0000-4000-8000-000000000001",
    shortage_id: shortage.id,
    shipment_id: shipmentToA.id,
    expected: 850,
    accepted: 790,
    discrepancy: 60,
    outcome: "PARTIAL",
    residual_shortage_id: RESIDUAL_ID,
    created_at: "2026-10-07T09:00:01Z",
  },
};

/** The residual shortage the hub opened for the missing 60 (business-rules §9). */
export const residual: Shortage = {
  ...shortage,
  id: RESIDUAL_ID,
  qty_required: 60,
  qty_local_usable: 0,
  shortfall: 60,
  status: "MATCHING",
  parent_shortage_id: shortage.id,
  created_at: "2026-10-07T09:00:01Z",
  updated_at: "2026-10-07T09:00:02Z",
};

export const escalation = (
  id: string,
  fields: Partial<Notification> & { reason?: string | null } = {},
): Notification => {
  const { reason = null, ...rest } = fields;
  return {
    id,
    type: "recommendation.escalated",
    payload: {
      recommendation_id: transferRec.id,
      shortage_id: shortage.id,
      escalated_by: "00000000-0000-4000-8000-0000000000bb",
      reason,
      expires_at: transferRec.expires_at,
    },
    read_at: null,
    created_at: "2026-10-07T06:01:00Z",
    ...rest,
  };
};
