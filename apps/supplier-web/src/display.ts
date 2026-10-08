// Labels and display gates. Display only: the hub enforces every rule and still checks each call.
import { formatQty } from "@care-e/ui";
import type { PoAction, PoStatus, Product } from "./api";

/** api-and-events.md (S09/S10): purchase orders, their actions and network demand need
 *  `po.respond` in a SUPPLIER org. Offers are readable by any user of the org. */
export const PO_RESPOND = "po.respond";

/** business-rules.md §8, PurchaseOrder: the actions the hub accepts from each state
 *  (SENT → ACKNOWLEDGED → DISPATCHED; SENT or ACKNOWLEDGED → REJECTED). Used only to hide
 *  buttons that would earn a 409. */
export const PO_ACTIONS: Partial<Record<PoStatus, readonly PoAction[]>> = {
  SENT: ["acknowledge", "reject"],
  ACKNOWLEDGED: ["dispatch", "reject"],
};
export const actionsFor = (status: PoStatus): readonly PoAction[] => PO_ACTIONS[status] ?? [];

/** The dashboard's two order queues. */
export const NEW_ORDERS: PoStatus = "SENT";
export const TO_DISPATCH: PoStatus = "ACKNOWLEDGED";

export const PO_STATUSES: readonly PoStatus[] = [
  "SENT",
  "ACKNOWLEDGED",
  "DISPATCHED",
  "DELIVERED",
  "REJECTED",
];

/** business-rules.md §3, freshness gate: an offer counts only if updated within 7 days
 *  (`OFFER_UPDATED_WITHIN` in the hub). Mirrored here only to flag offers that matching will
 *  skip; the hub applies the gate itself. */
export const OFFER_FRESH_DAYS = 7;
const DAY_MS = 86_400_000;
export const isStaleOffer = (updatedAt: string, now: number) =>
  now - new Date(updatedAt).getTime() > OFFER_FRESH_DAYS * DAY_MS;

/** Whole days since `iso`, for "Updated 9 days ago". */
export const daysSince = (iso: string, now: number) =>
  Math.floor((now - new Date(iso).getTime()) / DAY_MS);

/** A quantity in the product's unit; "units" while the catalog is loading. */
export const qty = (n: number, product: Product | undefined) =>
  formatQty(n, product?.unit ?? "units");

const hours = new Intl.NumberFormat("en-IN");
export const formatHours = (h: number) => `${hours.format(h)} h`;
