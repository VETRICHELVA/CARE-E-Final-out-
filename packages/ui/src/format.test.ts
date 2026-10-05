import { afterEach, describe, expect, it, vi } from "vitest";
import { formatDateTime, formatMoney, formatQty } from "./format";

afterEach(() => vi.unstubAllEnvs());

describe("formatters", () => {
  it("formats paise as rupees with Indian digit grouping", () => {
    expect(formatMoney(12345600)).toBe("₹1,23,456.00");
    expect(formatMoney(0)).toBe("₹0.00");
    expect(formatMoney(1205)).toBe("₹12.05");
    expect(formatMoney(1000000000)).toBe("₹1,00,00,000.00");
  });

  it("formats a quantity with its unit", () => {
    expect(formatQty(150000, "units")).toBe("1,50,000 units");
    expect(formatQty(1, "box")).toBe("1 box");
  });

  it("shows a UTC timestamp in the local time zone", () => {
    vi.stubEnv("TZ", "Asia/Kolkata");
    expect(formatDateTime("2026-10-05T13:00:00Z")).toMatch(/^5 Oct 2026, 6:30\spm$/);
    vi.stubEnv("TZ", "UTC");
    expect(formatDateTime("2026-10-05T13:00:00Z")).toMatch(/^5 Oct 2026, 1:00\spm$/);
  });
});
