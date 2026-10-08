import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  assigned,
  coldBox,
  coldShipment,
  coldVan,
  meAs,
  NOW,
  page,
  priya,
  ravi,
  spareBox,
  van,
} from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { FleetPage } from "./fleet";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const onRoad = assigned("IN_TRANSIT", coldShipment);
const carrying = assigned("ASSIGNED", coldShipment, 7);

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/drivers": page([ravi, { ...priya, active: false }]),
    "GET /api/v1/vehicles": page([van, coldVan]),
    "GET /api/v1/devices": page([coldBox, { ...spareBox, assigned_shipment_id: carrying.id }]),
    "GET /api/v1/shipments": (call: Call) => {
      const status = call.url.searchParams.get("status");
      const all = [onRoad, { ...carrying, device_id: spareBox.id }];
      return page(all.filter((s) => s.status === status));
    },
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "DISPATCHER") =>
  renderAs(meAs(role), <FleetPage />, { path: "/fleet" });

describe("Fleet", () => {
  it("lists drivers with phone and active state, and vehicles with cold chain", async () => {
    hub();
    show();
    const r = await screen.findByTestId("driver-Ravi");
    expect(within(r).getByText("+91 90000 00001").getAttribute("href")).toBe("tel:+91 90000 00001");
    expect(within(r).getByText("Active")).toBeTruthy();
    expect(within(screen.getByTestId("driver-Priya")).getByText("Inactive")).toBeTruthy();
    expect(
      within(await screen.findByTestId("vehicle-KA-01-SM-0001")).getByText("No cold chain"),
    ).toBeTruthy();
    expect(
      within(screen.getByTestId("vehicle-KA-01-SM-0002")).getByText("Cold chain"),
    ).toBeTruthy();
  });

  it("lists cold boxes with battery, last seen and the shipment each rides with", async () => {
    hub();
    show();
    const box = await screen.findByTestId("device-cb-01");
    expect(within(box).getByTestId("battery").textContent).toBe("87%");
    expect(within(box).getByTestId("last-seen").textContent).toBe("12 min ago");
    expect(within(box).getByTestId("device-shipment").textContent).toBe("Not on a shipment");
    const spare = screen.getByTestId("device-cb-02");
    expect(within(spare).getByTestId("battery").textContent).toBe("Unknown");
    expect(within(spare).getByTestId("last-seen").textContent).toBe("Never");
    await waitFor(() =>
      expect(within(spare).getByTestId("device-shipment").textContent).toBe(
        "Rapid Diagnostic Kit to Hospital A",
      ),
    );
    expect(within(spare).getByRole("link").getAttribute("href")).toBe(`/shipments/${carrying.id}`);
  });

  it("attaches a free box to a shipment on the way without a box", async () => {
    const fake = hub({
      "POST /api/v1/devices/{id}/assign": { ...coldBox, assigned_shipment_id: onRoad.id },
    });
    show();
    const box = await screen.findByTestId("device-cb-01");
    fireEvent.click(within(box).getByRole("button", { name: "Attach to shipment" }));
    const dialog = screen.getByRole("dialog");
    const select = within(dialog).getByLabelText("Shipment");
    await waitFor(() =>
      expect(
        within(select)
          .getAllByRole("option")
          .map((o) => o.textContent),
      ).toEqual(["Choose…", "Rapid Diagnostic Kit to Hospital A (Ravi)"]),
    );
    fireEvent.change(select, { target: { value: onRoad.id } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Attach" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/devices/${coldBox.id}/assign`);
    expect(JSON.parse(call!.body!)).toEqual({ shipment_id: onRoad.id });
    await waitFor(() => expect(fake.to("GET", "/api/v1/devices")).toHaveLength(2));
  });

  it("takes a box off its shipment with the typed reason", async () => {
    const fake = hub({ "POST /api/v1/devices/{id}/assign": spareBox });
    show();
    const spare = await screen.findByTestId("device-cb-02");
    fireEvent.click(within(spare).getByRole("button", { name: "Take off" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Reason (optional)"), {
      target: { value: "Battery swap" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Take off" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/devices/${spareBox.id}/assign`);
    expect(JSON.parse(call!.body!)).toEqual({ shipment_id: null, reason: "Battery swap" });
  });

  it("is for dispatchers: a driver sees a refusal and nothing is fetched", async () => {
    const fake = hub();
    show("DRIVER");
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Only dispatchers can see the fleet.",
    );
    expect(fake.calls).toHaveLength(0);
  });

  it("shows empty lists and the hub's error per section", async () => {
    hub({
      "GET /api/v1/drivers": page([]),
      "GET /api/v1/devices": hubError(500, "internal_error", "Devices are unavailable."),
    });
    show();
    expect(await screen.findByText("No drivers yet.")).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toContain("Devices are unavailable.");
  });
});
