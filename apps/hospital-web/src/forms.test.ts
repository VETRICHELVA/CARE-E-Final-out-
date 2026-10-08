import { describe, expect, it } from "vitest";
import { count, optionalCount, paiseToRupees, rupees } from "./forms";

describe("form helpers", () => {
  it("accepts whole numbers only", () => {
    expect(count("a quantity").parse(" 850 ")).toBe(850);
    expect(count("a quantity").safeParse("").error?.issues[0]?.message).toBe("Enter a quantity.");
    expect(count("a quantity").safeParse("-1").success).toBe(false);
    expect(count("a quantity").safeParse("1.5").success).toBe(false);
    expect(count("a quantity").safeParse("2147483648").success).toBe(false);
  });

  it("treats a blank optional number as not given", () => {
    expect(optionalCount.parse("")).toBeUndefined();
    expect(optionalCount.parse("30")).toBe(30);
  });

  it("converts rupees to paise exactly", () => {
    expect(rupees.parse("14")).toBe(1400);
    expect(rupees.parse("14.5")).toBe(1450);
    expect(rupees.parse("0.29")).toBe(29);
    expect(rupees.safeParse("14.555").success).toBe(false);
    expect(paiseToRupees(1450)).toBe("14.50");
    expect(paiseToRupees(5)).toBe("0.05");
  });
});
