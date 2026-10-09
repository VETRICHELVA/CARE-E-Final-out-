import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ShipmentDetail } from "../api";
import {
  deliveredToA,
  excursionColdChain,
  meAs,
  NOW,
  products,
  shipmentToA,
} from "../test/fixtures";
import { fakeHub, hubError, renderAs } from "../test/hub";
import { DeliveryDetailPage } from "./delivery-detail";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const cold: ShipmentDetail = {
  ...shipmentToA,
  requires_cold_chain: true,
  device_id: "de000000-0000-4000-8000-000000000001",
  coldchain: {
    last_event_type: "RECOVERED",
    last_event_at: "2026-10-07T07:00:50Z",
    had_excursion: true,
  },
  inspection_note_required: true,
};

const hub = (shipment: ShipmentDetail | Response, extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/shipments/{id}": shipment,
    "GET /api/v1/shipments/{id}/coldchain": excursionColdChain,
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "RECEIVER", id = shipmentToA.id) =>
  renderAs(meAs(role), <DeliveryDetailPage />, {
    path: `/deliveries/${id}`,
    route: "/deliveries/:id",
  });

describe("Delivery detail", () => {
  it("shows the inbound shipment with its live cold-chain panel", async () => {
    const fake = hub(cold);
    show();
    const panel = await screen.findByTestId("coldchain-panel");
    expect(screen.getByText("Supplier Y", { selector: "dd" })).toBeTruthy();
    expect(screen.getByText("SwiftMed Logistics")).toBeTruthy();
    expect(screen.getByTestId("excursion-badge").textContent).toBe("Excursion on record");
    expect(screen.getByTestId("excursion-notice").textContent).toBe(
      "A cold-chain excursion is on record for this shipment. Its receipt needs an inspection note.",
    );
    expect(await within(panel).findByTestId("temperature-chart")).toBeTruthy();
    expect(within(panel).getAllByTestId("out-of-range")).toHaveLength(2);
    expect(within(panel).getByText("cb-01")).toBeTruthy();
    expect(
      within(panel).getByText(
        "Readings back in range, latest 4.6 °C. The excursion stays on record.",
      ),
    ).toBeTruthy();
    expect(fake.to("GET", `/api/v1/shipments/${shipmentToA.id}/coldchain`)).toHaveLength(1);
    // Not delivered yet: no Receive link.
    expect(screen.queryByRole("link", { name: "Receive" })).toBeNull();
  });

  it("links a delivered shipment to Receive for a receiver", async () => {
    hub({ ...deliveredToA, coldchain: null });
    show();
    expect((await screen.findByRole("link", { name: "Receive" })).getAttribute("href")).toBe(
      `/deliveries/${deliveredToA.id}/receive`,
    );
    expect(screen.queryByTestId("excursion-notice")).toBeNull();
  });

  it("shows no cold-chain panel for an ordinary shipment", async () => {
    const fake = hub(shipmentToA);
    show("APPROVER");
    expect(await screen.findByText("SwiftMed Logistics")).toBeTruthy();
    expect(screen.queryByTestId("coldchain-panel")).toBeNull();
    expect(fake.to("GET", `/api/v1/shipments/${shipmentToA.id}/coldchain`)).toHaveLength(0);
  });

  it("shows the hub's refusal for another organization's shipment", async () => {
    hub(hubError(403, "forbidden", "This shipment belongs to other organizations."));
    show();
    expect(await screen.findByText("This shipment belongs to other organizations.")).toBeTruthy();
  });
});
