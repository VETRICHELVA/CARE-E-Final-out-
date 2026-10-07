// Labels and display gates. Display only: the hub enforces every rule and still checks each call.
import { formatQty } from "@care-e/ui";
import type { Candidate, Product } from "./api";

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
};

export const PRIORITY_LABELS: Record<string, string> = { CRITICAL: "Critical", ROUTINE: "Routine" };

export const RESOLUTION_LABELS: Record<string, string> = {
  TRANSFER: "Transfer",
  TRANSFER_SPLIT: "Split transfer",
  BUY: "Buy",
};

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
