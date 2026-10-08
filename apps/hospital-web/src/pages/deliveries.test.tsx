import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invalidateFor } from "@care-e/api-client";
import type { Shipment } from "../api";
import { deliveredToA, meAs, NOW, page, products, shipmentToA } from "../test/fixtures";
import { fakeHub, hubError, renderAs } from "../test/hub";
import { DeliveriesPage } from "./deliveries";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const hub = (shipments: Shipment[] | Response) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/shipments": shipments instanceof Response ? shipments : page(shipments),
  });

const show = (role: Parameters<typeof meAs>[0] = "APPROVER") =>
  renderAs(meAs(role), <DeliveriesPage />, { path: "/deliveries" });

const rows = async () => {
  const table = await screen.findByRole("table", { name: "Deliveries" });
  return within(table).getAllByRole("row").slice(1);
};

describe("Deliveries", () => {
  it("asks the hub for inbound shipments and shows status, carrier and live ETA", async () => {
    const fake = hub([shipmentToA]);
    show();
    const [row, ...rest] = await rows();
    expect(rest).toHaveLength(0);
    expect(within(row!).getByText("Surgical Kit A")).toBeTruthy();
    expect(within(row!).getByText("850 kits")).toBeTruthy();
    expect(within(row!).getByText("Supplier Y")).toBeTruthy();
    expect(within(row!).getByText("In transit")).toBeTruthy();
    expect(within(row!).getByText("SwiftMed Logistics")).toBeTruthy();
    expect(within(row!).getByText("Ravi · KA-01-AB-1234")).toBeTruthy();
    expect(within(row!).getByTestId("countdown").textContent).toBe("2 h 00 m left");
    expect(within(row!).queryByText("Cold chain")).toBeNull();
    expect(within(row!).getByRole("link", { name: "Shortage" }).getAttribute("href")).toBe(
      `/shortages/${shipmentToA.shortage_id}`,
    );
    const [call] = fake.to("GET", "/api/v1/shipments");
    expect(call!.url.searchParams.get("direction")).toBe("inbound");
    expect(fake.to("GET", "/api/v1/shipments")).toHaveLength(1);
  });

  it("shows the planned ETA before a carrier has the shipment, and a cold-chain badge", async () => {
    hub([
      {
        ...shipmentToA,
        status: "CREATED",
        carrier_org_id: null,
        carrier_org_name: null,
        driver: null,
        vehicle: null,
        eta: null,
        requires_cold_chain: true,
      },
    ]);
    show();
    const [row] = await rows();
    expect(within(row!).getByText("Created")).toBeTruthy();
    expect(within(row!).getByText("Not assigned yet")).toBeTruthy();
    expect(within(row!).getByText("Planned (no carrier yet)")).toBeTruthy();
    expect(within(row!).queryByTestId("countdown")).toBeNull();
    expect(within(row!).getByText("Cold chain")).toBeTruthy();
  });

  it("links each delivery to its detail and badges the hub's cold-chain events", async () => {
    const hot: Shipment = {
      ...shipmentToA,
      requires_cold_chain: true,
      coldchain: {
        last_event_type: "EXCURSION",
        last_event_at: "2026-10-07T07:00:30Z",
        had_excursion: true,
      },
    };
    hub([hot]);
    show();
    const [row] = await rows();
    expect(within(row!).getByRole("link", { name: "Surgical Kit A" }).getAttribute("href")).toBe(
      `/deliveries/${hot.id}`,
    );
    expect(within(row!).getByTestId("coldchain-badge").textContent).toBe("Temperature excursion");
  });

  it("refreshes the badges when the hub reports a cold-chain event", async () => {
    let current: Shipment = shipmentToA;
    fakeHub({
      "GET /api/v1/products": products,
      "GET /api/v1/shipments": () => page([current]),
    });
    const queryClient = show();
    const [row] = await rows();
    expect(within(row!).queryByTestId("coldchain-badge")).toBeNull();
    current = {
      ...shipmentToA,
      coldchain: {
        last_event_type: "DEVICE_SILENT",
        last_event_at: "2026-10-07T07:03:00Z",
        had_excursion: false,
      },
    };
    await act(() =>
      invalidateFor(queryClient, {
        id: "1",
        type: "coldchain.device_silent",
        occurred_at: "2026-10-07T07:03:00Z",
        org_ids: [],
        data: { shipment_id: shipmentToA.id, observed_value: 150, threshold: 120 },
      }),
    );
    await waitFor(async () =>
      expect(within((await rows())[0]!).getByTestId("coldchain-badge").textContent).toBe(
        "Device silent",
      ),
    );
  });

  it("shows when a delivered shipment arrived", async () => {
    hub([
      {
        ...shipmentToA,
        status: "DELIVERED",
        drop: {
          seq: 2,
          stop_type: "DROP",
          place: "Hospital A Main Store",
          lat: 0,
          lng: 0,
          planned_at: null,
          actual_at: "2026-10-07T05:00:00Z",
        },
      },
    ]);
    show();
    const [row] = await rows();
    expect(within(row!).getByText("Delivered")).toBeTruthy();
    expect(within(row!).getByText(/^Delivered \d/)).toBeTruthy();
    expect(within(row!).queryByTestId("countdown")).toBeNull();
  });

  it("hides the shortage link from a user who cannot read shortages", async () => {
    hub([shipmentToA]);
    show("RECEIVER");
    const [row] = await rows();
    expect(within(row!).queryByRole("link", { name: "Shortage" })).toBeNull();
  });

  it("refreshes when the hub reports a shipment change (no polling)", async () => {
    let current: Shipment = shipmentToA;
    fakeHub({
      "GET /api/v1/products": products,
      "GET /api/v1/shipments": () => page([current]),
    });
    const queryClient = show();
    const [row] = await rows();
    expect(within(row!).getByText("In transit")).toBeTruthy();
    current = { ...shipmentToA, status: "DELIVERED" };
    await act(() =>
      invalidateFor(queryClient, {
        id: "1",
        type: "shipment.status_changed",
        occurred_at: "2026-10-07T06:01:00Z",
        org_ids: [],
        data: { shipment_id: shipmentToA.id, from: "IN_TRANSIT", to: "DELIVERED" },
      }),
    );
    await waitFor(async () =>
      expect(within((await rows())[0]!).getByText("Delivered")).toBeTruthy(),
    );
  });

  it("links a delivered shipment to the Receive screen for a receiver", async () => {
    hub([deliveredToA, { ...shipmentToA, id: "5b000000-0000-4000-8000-000000000009" }]);
    show("RECEIVER");
    const [delivered, moving] = await rows();
    expect(within(delivered!).getByRole("link", { name: "Receive" }).getAttribute("href")).toBe(
      `/deliveries/${deliveredToA.id}/receive`,
    );
    expect(within(moving!).queryByRole("link", { name: "Receive" })).toBeNull();
  });

  it("links a reconciled shipment to its recorded receipt", async () => {
    hub([{ ...deliveredToA, status: "RECONCILED" }]);
    show("RECEIVER");
    const [row] = await rows();
    expect(within(row!).getByRole("link", { name: "View receipt" }).getAttribute("href")).toBe(
      `/deliveries/${deliveredToA.id}/receive`,
    );
    expect(within(row!).queryByRole("link", { name: "Receive" })).toBeNull();
  });

  it("offers no Receive link without receipt.record", async () => {
    hub([deliveredToA]);
    show("APPROVER");
    const [row] = await rows();
    expect(within(row!).queryByRole("link", { name: "Receive" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Receipt" })).toBeNull();
  });

  it("has an empty state", async () => {
    hub([]);
    show();
    expect(await screen.findByText("No deliveries to your hospital")).toBeTruthy();
  });

  it("shows the hub's error", async () => {
    hub(hubError(500, "internal_error", "The hub is down."));
    show();
    expect(await screen.findByText("The hub is down.")).toBeTruthy();
  });
});
