import { describe, expect, it } from "vitest";
import { paiseToRupees, parseOffer, rupeesToPaise } from "./forms";

describe("offer form", () => {
  it.each([
    ["28", 2800],
    ["28.5", 2850],
    ["28.50", 2850],
    ["0.05", 5],
    ["0", 0],
  ])("reads ₹%s as %i paise", (rupees, paise) => expect(rupeesToPaise(rupees)).toBe(paise));

  it.each([
    [2800, "28.00"],
    [90050, "900.50"],
    [5, "0.05"],
  ])("shows %i paise as %s", (paise, text) => expect(paiseToRupees(paise)).toBe(text));

  it("builds the PUT body from trimmed whole numbers", () => {
    expect(parseOffer("p1", { price: " 28.5 ", leadTime: "22 ", available: "2000" })).toEqual({
      body: { product_id: "p1", unit_price_paise: 2850, lead_time_hours: 22, available_qty: 2000 },
    });
  });

  it("names every bad field", () => {
    expect(parseOffer("p1", { price: "₹28", leadTime: "", available: "3000000000" })).toEqual({
      errors: {
        price: "Enter a price in ₹, e.g. 28 or 28.50.",
        leadTime: "Lead time must be a whole number, 0 or more.",
        available: "Available quantity is too large.",
      },
    });
  });
});
