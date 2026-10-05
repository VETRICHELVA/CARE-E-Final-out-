// Display formatting only (apps-ai-iot.md, Shared rules): ₹ with Indian digit grouping,
// quantities with units, times in the user's local zone.

const rupees = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
const count = new Intl.NumberFormat("en-IN");

/** Money is stored as integer paise: 12345600 → "₹1,23,456.00". */
export const formatMoney = (paise: number) => rupees.format(paise / 100);

/** 1200, "units" → "1,200 units". */
export const formatQty = (qty: number, unit: string) => `${count.format(qty)} ${unit}`;

/** A UTC timestamp from the hub, shown in the browser's own time zone. */
export const formatDateTime = (iso: string) =>
  new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
