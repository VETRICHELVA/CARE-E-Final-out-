// Labels and display gates. Display only: the hub enforces every rule and still checks each call.
import { formatQty, STATUS } from "@care-e/ui";
import type { Candidate, Product } from "./api";

/** A hub state's label ("IN_TRANSIT" → "In transit"); "—" for anything that is not a string. */
export const statusLabel = (s: unknown) => (typeof s === "string" ? (STATUS[s]?.label ?? s) : "—");

/** business-rules.md §8: the Shortage states the hub accepts each requester action from.
 *  Used to hide buttons that would only earn a 409. */
export const RERUN_FROM = new Set(["OPEN", "MATCHING"]);
/** api-and-events.md (S05): who may list and read their org's shortages. Writes stay
 *  `shortage.create` only. */
export const SHORTAGE_READERS = ["shortage.create", "recommendation.approve"] as const;
export const CANCEL_FROM = new Set(["OPEN", "MATCHING", "AWAITING_DECISION"]);
/** Shortage states that are still being worked on (the dashboard's "open" shortages). */
export const OPEN_STATES = [
  "DRAFT",
  "OPEN",
  "MATCHING",
  "AWAITING_DECISION",
  "IN_FULFILLMENT",
  "RECEIVED",
];

/** business-rules.md §8: a SourceRequest the source may still answer. */
export const ANSWERABLE = "REQUESTED";
/** SourceRequest states that are still open (the hub refuses a manual re-run while any is). */
export const OPEN_REQUEST_STATES = new Set(["REQUESTED", "TENTATIVE_HOLD"]);

/** Gate names from business-rules.md §3. */
export const GATE_LABELS: Record<string, string> = {
  product: "Product",
  quantity: "Quantity",
  shelf_life: "Shelf life",
  authorization: "Authorization",
  freshness: "Freshness",
  deadline: "Deadline",
  cold_chain: "Cold chain",
};

export const TRIGGER_LABELS: Record<string, string> = {
  CREATE: "Shortage reported",
  DECLINE: "A source declined",
  EXPIRY: "A request expired",
  MANUAL: "Manual re-run",
  RECOMMENDATION_EXPIRED: "Recommendation expired",
  STOCK_CHANGE: "Stock or offers changed",
};

export const PRIORITY_LABELS: Record<string, string> = { CRITICAL: "Critical", ROUTINE: "Routine" };

export const RESOLUTION_LABELS: Record<string, string> = {
  TRANSFER: "Transfer",
  TRANSFER_SPLIT: "Split transfer",
  BUY: "Buy",
};

/** business-rules.md §13, word for word: the approve button and what the approver reads once
 *  the hub has approved (the hub's approve response carries the same `message`). */
export const DECISION_WORDING: Record<string, { approve: string; approved: string }> = {
  TRANSFER: { approve: "Approve transfer", approved: "Stock is now held at the source." },
  TRANSFER_SPLIT: { approve: "Approve transfers", approved: "Stock is now held at each source." },
  BUY: { approve: "Approve purchase", approved: "The order has gone to the supplier." },
};

/** business-rules.md §8, Recommendation: the states each decision is accepted from. */
export const APPROVE_FROM = new Set(["PENDING", "ESCALATED"]);
export const REJECT_FROM = APPROVE_FROM;
export const ESCALATE_FROM = new Set(["PENDING"]);

/** Shipment states before the receiving hospital has the goods. */
export const IN_MOTION = new Set(["CREATED", "ASSIGNED", "PICKED_UP", "IN_TRANSIT"]);

/** Audit entities as the audit tab names them. */
export const ENTITY_LABELS: Record<string, string> = {
  shortage: "Shortage",
  source_request: "Source request",
  recommendation: "Recommendation",
  purchase_order: "Purchase order",
  shipment: "Shipment",
  match_run: "Match run",
  receipt: "Receipt",
  reconciliation: "Reconciliation",
  batch: "Batch",
};

/** Receipt conditions (api-and-events.md, S12). */
export const CONDITION_LABELS: Record<string, string> = {
  GOOD: "Good",
  DAMAGED: "Damaged",
  TEMPERATURE_ISSUE: "Temperature issue",
};

/** Notification types the hub writes (S12: escalations). */
export const NOTIFICATION_LABELS: Record<string, string> = {
  "recommendation.escalated": "Recommendation escalated to you",
};

/** The hub's wording when a user acted without typing a reason (business-rules.md §10). */
export const NO_REASON = "No reason was entered.";

/**
 * True for a row another org's user wrote, mirrored into this org's trail (business-rules.md
 * §10): the hub drops the actor's id, but keeps the reason as recorded. A user action's reason
 * is either typed (USER) or the hub's "No reason was entered."; a system change always carries
 * a factual cause instead. So an actor-less row with a user's reason is a mirrored one.
 */
export const isMirrored = (row: {
  actor_id: string | null;
  reason: string;
  reason_source: string;
}) => row.actor_id === null && (row.reason_source === "USER" || row.reason === NO_REASON);

const hours = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });
/** A hub ETA in hours: 6.24 → "6.2 h". */
export const formatHours = (h: number) => `${hours.format(h)} h`;

/** A quantity in the product's unit; "units" while the catalog is loading. */
export const qty = (n: number, product: Product | undefined) =>
  formatQty(n, product?.unit ?? "units");

/** A hospital source shows hub-computed transferable; a supplier shows its offered qty. */
export function candidateQty(c: Candidate, product: Product | undefined) {
  if (c.transferable_qty !== null) return `${qty(c.transferable_qty, product)} transferable`;
  if (c.offered_qty !== null) return `${qty(c.offered_qty, product)} offered`;
  return "—";
}

/** A browser date (`YYYY-MM-DD`) from a hub date, shown in the user's locale. */
export const formatDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { dateStyle: "medium" });
