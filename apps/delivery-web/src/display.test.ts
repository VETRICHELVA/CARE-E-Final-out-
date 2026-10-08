import { describe, expect, it } from "vitest";
import { routeLine } from "./components/route-map";
import { ago, nextStep, place, vehiclesFor } from "./display";
import { coldVan, van } from "./test/fixtures";

describe("display helpers", () => {
  it("follows the driver's part of the shipment state machine, one step at a time", () => {
    expect(nextStep("CREATED")).toBeUndefined();
    expect(nextStep("ASSIGNED")).toBe("PICKED_UP");
    expect(nextStep("PICKED_UP")).toBe("IN_TRANSIT");
    expect(nextStep("IN_TRANSIT")).toBe("DELIVERED");
    expect(nextStep("DELIVERED")).toBeUndefined();
    expect(nextStep("RECONCILED")).toBeUndefined();
  });

  it("offers only cold-chain vehicles for a cold-chain shipment", () => {
    expect(vehiclesFor({ requires_cold_chain: true }, [van, coldVan])).toEqual([coldVan]);
    expect(vehiclesFor({ requires_cold_chain: false }, [van, coldVan])).toEqual([van, coldVan]);
  });

  it("reads a GeoJSON LineString as Leaflet [lat, lng] pairs, and anything else as none", () => {
    expect(
      routeLine({
        type: "LineString",
        coordinates: [
          [77.6, 12.9],
          [77.7, 13.0],
        ],
      }),
    ).toEqual([
      [12.9, 77.6],
      [13.0, 77.7],
    ]);
    expect(routeLine(null)).toEqual([]);
    expect(routeLine({ type: "Point", coordinates: [77.6, 12.9] })).toEqual([]);
  });

  it("names a stop by org, then place", () => {
    expect(place("Hospital B", { place: "Main Store" })).toBe("Hospital B, Main Store");
    expect(place("Supplier Y", { place: "Supplier Y" })).toBe("Supplier Y");
    expect(place("Supplier Y", null)).toBe("Supplier Y");
  });

  it("says how long ago a device was last seen", () => {
    const now = Date.parse("2026-10-08T06:00:00Z");
    expect(ago("2026-10-08T05:59:30Z", now)).toBe("just now");
    expect(ago("2026-10-08T05:48:00Z", now)).toBe("12 min ago");
    expect(ago("2026-10-08T03:00:00Z", now)).toBe("3 h ago");
    expect(ago("2026-10-06T05:00:00Z", now)).toBe("2 d ago");
  });
});
