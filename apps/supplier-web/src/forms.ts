// The offer editor's input checks: shape only (whole numbers, ₹ with at most two decimals).
// The hub validates the values again and decides what they mean.
import type { OfferIn } from "./api";

export type OfferDraft = { price: string; leadTime: string; available: string };
export type OfferErrors = Partial<Record<keyof OfferDraft, string>>;

const INT4_MAX = 2_147_483_647; // the hub stores these as int4
const WHOLE = /^\d+$/;
const RUPEES = /^\d+(\.\d{1,2})?$/;

/** "28.5" → 2850 paise; money is integer paise everywhere (CLAUDE.md). */
export const rupeesToPaise = (rupees: string) => {
  const [whole, fraction = ""] = rupees.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
};

/** 2850 → "28.50", the editor's starting text. */
export const paiseToRupees = (paise: number) =>
  `${Math.floor(paise / 100)}.${String(paise % 100).padStart(2, "0")}`;

function whole(value: string, label: string): string | undefined {
  const v = value.trim();
  if (!WHOLE.test(v)) return `${label} must be a whole number, 0 or more.`;
  if (Number(v) > INT4_MAX) return `${label} is too large.`;
  return undefined;
}

/** The PUT body for `productId`, or the errors to show next to each field. */
export function parseOffer(
  productId: string,
  draft: OfferDraft,
): { body: OfferIn; errors?: never } | { body?: never; errors: OfferErrors } {
  const errors: OfferErrors = {};
  const price = draft.price.trim();
  if (!RUPEES.test(price)) errors.price = "Enter a price in ₹, e.g. 28 or 28.50.";
  else if (rupeesToPaise(price) > INT4_MAX) errors.price = "Price is too large.";
  const lead = whole(draft.leadTime, "Lead time");
  if (lead) errors.leadTime = lead;
  const available = whole(draft.available, "Available quantity");
  if (available) errors.available = available;
  if (Object.keys(errors).length > 0) return { errors };
  return {
    body: {
      product_id: productId,
      unit_price_paise: rupeesToPaise(price),
      lead_time_hours: Number(draft.leadTime.trim()),
      available_qty: Number(draft.available.trim()),
    },
  };
}
