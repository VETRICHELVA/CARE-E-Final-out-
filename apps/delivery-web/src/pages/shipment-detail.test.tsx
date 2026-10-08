import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { invalidateFor } from "@care-e/api-client";
import type { ShipmentDetail } from "../api";
import {
  assigned,
  coldBox,
  coldShipment,
  detail,
  meAs,
  ORG_SWIFTMED,
  page,
  products,
  ravi,
  spareBox,
  transfer,
  van,
  coldVan,
} from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { ShipmentDetailPage } from "./shipment-detail";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const onRoad = assigned("IN_TRANSIT", coldShipment);
const here = { shipment_id: onRoad.id, lat: 12.94, lng: 77.66, ts: "2026-10-08T06:30:00Z" };

const hub = (
  shipment: ShipmentDetail,
  extra: Record<string, unknown> = {},
  jobs: { id: string }[] = [],
) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/shipments/{id}": shipment,
    "GET /api/v1/shipments": page(jobs),
    "GET /api/v1/devices": page([coldBox, spareBox]),
    "GET /api/v1/drivers": page([ravi]),
    "GET /api/v1/vehicles": page([van, coldVan]),
    ...extra,
  });

const show = (shipment: { id: string }, role: Parameters<typeof meAs>[0] = "DISPATCHER") =>
  renderAs(meAs(role), <ShipmentDetailPage />, {
    path: `/shipments/${shipment.id}`,
    route: "/shipments/:id",
  });

const actionNames = () =>
  screen
    .queryAllByRole("button")
    .map((b) => b.textContent)
    .filter((t) => t !== "Take off" && t !== "Attach cold box");

describe("Shipment detail", () => {
  it("draws the hub's stored route ([lng, lat] → [lat, lng]), stops and live position", async () => {
    hub(detail(onRoad, { last_location: here }));
    show(onRoad);
    const map = await screen.findByTestId("map");
    expect(JSON.parse(screen.getByTestId("map-route").dataset.positions!)).toEqual([
      [12.9279, 77.6271],
      [12.94, 77.66],
      [12.9592, 77.6974],
    ]);
    const markers = within(map).getAllByTestId("map-marker");
    expect(markers.map((m) => JSON.parse(m.dataset.center!))).toEqual([
      [12.85, 77.66],
      [12.9592, 77.6974],
      [12.94, 77.66],
    ]);
    expect(within(map).getByText("Pickup: Supplier Y")).toBeTruthy();
    expect(within(map).getByText("Drop: Hospital A Main Store")).toBeTruthy();
    expect(within(map).getByText(/^Driver, /)).toBeTruthy();
    expect(screen.getByTestId("map-tiles").dataset.url).toBe(
      "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    );
    expect(screen.getByTestId("live-position").textContent).toMatch(/^12\.94000, 77\.66000 at /);
  });

  it("shows ETA, route distance and source, driver, vehicle and status history", async () => {
    hub(detail(onRoad));
    show(onRoad);
    expect(await screen.findByTestId("eta")).toBeTruthy();
    expect(screen.getByTestId("route").textContent).toBe("9.4 km, road route");
    expect(screen.getByText("Ravi")).toBeTruthy();
    expect(screen.getByText("KA-01-SM-0002 (cold chain)")).toBeTruthy();
    expect(screen.getByText("SwiftMed Logistics")).toBeTruthy();
    const history = screen.getByRole("list", { name: "Status history" });
    expect(
      within(history)
        .getAllByRole("listitem")
        .map((li) => li.textContent?.split(/\d/)[0]),
    ).toEqual(["Created", "Assigned"]);
    expect(screen.getByText("No position shared yet")).toBeTruthy();
  });

  it("names the haversine fallback when OSRM gave no route", async () => {
    hub(detail({ ...onRoad, route_provider: "HAVERSINE" }));
    show(onRoad);
    expect((await screen.findByTestId("route")).textContent).toBe("9.4 km, straight-line estimate");
  });

  it("shows an unassigned shipment without a route, with Assign for a dispatcher", async () => {
    hub(detail(transfer));
    show(transfer);
    expect(await screen.findByText("Planned when a driver is assigned")).toBeTruthy();
    expect(screen.queryByTestId("map-route")).toBeNull();
    expect(actionNames()).toEqual(["Assign"]);
  });

  it("offers Unassign to the carrier's dispatcher while ASSIGNED", async () => {
    const s = assigned("ASSIGNED");
    const fake = hub(detail(s), {
      "POST /api/v1/shipments/{id}/unassign": detail(transfer),
    });
    show(s);
    fireEvent.click(await screen.findByRole("button", { name: "Unassign" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Unassign" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(fake.to("POST", `/api/v1/shipments/${s.id}/unassign`)[0]!.body).toBe("");
  });

  it("offers no Unassign once picked up, or to another carrier", async () => {
    hub(detail(assigned("PICKED_UP")));
    show(assigned("PICKED_UP"));
    await screen.findByTestId("eta");
    expect(actionNames()).toEqual([]);
    cleanup();
    hub(
      detail({ ...assigned("ASSIGNED"), carrier_org_id: "1c000000-0000-4000-8000-0000000000ff" }),
    );
    show(assigned("ASSIGNED"));
    await screen.findByTestId("eta");
    expect(actionNames()).toEqual([]);
  });

  it("gives the assigned driver the next-step button only", async () => {
    const s = assigned("PICKED_UP");
    hub(detail(s), {}, [s]);
    show(s, "DRIVER");
    expect(await screen.findByRole("button", { name: "In transit" })).toBeTruthy();
    expect(actionNames()).toEqual(["In transit"]);
  });

  it("gives another driver of the org no buttons", async () => {
    const s = assigned("PICKED_UP");
    hub(detail(s), {}, []);
    show(s, "DRIVER");
    await screen.findByTestId("eta");
    expect(actionNames()).toEqual([]);
    // A driver cannot list devices (shipment.assign): only whether a box rides along.
    expect(screen.getByTestId("cold-box").textContent).toBe("No cold box is attached.");
  });

  it("refetches the detail on a shipment.location event for this shipment only", async () => {
    const fake = hub(detail(onRoad));
    const queryClient = show(onRoad);
    await screen.findByTestId("eta");
    const gets = () => fake.to("GET", `/api/v1/shipments/${onRoad.id}`).length;
    expect(gets()).toBe(1);
    const event = (shipment_id: string) => ({
      id: "1",
      type: "shipment.location",
      occurred_at: here.ts,
      org_ids: [ORG_SWIFTMED],
      data: { ...here, shipment_id },
    });
    await invalidateFor(queryClient, event(transfer.id));
    expect(gets()).toBe(1);
    await invalidateFor(queryClient, event(onRoad.id));
    await waitFor(() => expect(gets()).toBe(2));
  });

  it("shows the hub's refusal for a shipment this org may not see", async () => {
    hub(detail(onRoad), {
      "GET /api/v1/shipments/{id}": hubError(403, "forbidden", "Another carrier has it."),
    });
    show(onRoad);
    expect(await screen.findByText("Shipment not available")).toBeTruthy();
    expect(screen.getByText(/Another carrier has it\./)).toBeTruthy();
  });

  it("shows the hub's message on any other failure", async () => {
    hub(detail(onRoad), {
      "GET /api/v1/shipments/{id}": hubError(500, "internal_error", "The hub is down."),
    });
    show(onRoad);
    expect(screen.getByRole("status")).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toContain("The hub is down.");
  });
});

describe("Shipment detail: cold box (S14)", () => {
  it("attaches one of the free boxes to the shipment", async () => {
    const fake = hub(detail(onRoad), {
      "GET /api/v1/devices": page([{ ...coldBox, assigned_shipment_id: transfer.id }, spareBox]),
      "POST /api/v1/devices/{id}/assign": (call: Call) => ({
        ...spareBox,
        assigned_shipment_id: JSON.parse(call.body!).shipment_id,
      }),
    });
    show(onRoad);
    expect((await screen.findByTestId("cold-box")).textContent).toBe("No cold box is attached.");
    fireEvent.click(screen.getByRole("button", { name: "Attach cold box" }));
    const dialog = screen.getByRole("dialog");
    const select = within(dialog).getByLabelText("Cold box");
    expect(
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Choose…", "cb-02"]);
    fireEvent.change(select, { target: { value: spareBox.id } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Attach" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/devices/${spareBox.id}/assign`);
    expect(JSON.parse(call!.body!)).toEqual({ shipment_id: onRoad.id });
    await waitFor(() => expect(fake.to("GET", "/api/v1/devices")).toHaveLength(2));
  });

  it("shows the box riding along with its battery, and takes it off", async () => {
    const riding = { ...onRoad, device_id: coldBox.id };
    const fake = hub(detail(riding), {
      "GET /api/v1/devices": page([{ ...coldBox, assigned_shipment_id: riding.id }]),
      "POST /api/v1/devices/{id}/assign": coldBox,
    });
    show(riding);
    expect((await screen.findByTestId("cold-box")).textContent).toBe("cb-01, battery 87%");
    expect(screen.queryByRole("button", { name: "Attach cold box" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Take off" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Take off" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/devices/${coldBox.id}/assign`);
    expect(JSON.parse(call!.body!)).toEqual({ shipment_id: null });
  });

  it("shows the hub's 409 when the shipment already carries another box", async () => {
    hub(detail(onRoad), {
      "POST /api/v1/devices/{id}/assign": hubError(
        409,
        "conflict",
        "Another device is on this shipment; take it off first.",
      ),
    });
    show(onRoad);
    fireEvent.click(await screen.findByRole("button", { name: "Attach cold box" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Cold box"), { target: { value: coldBox.id } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Attach" }));
    expect((await within(dialog).findByRole("alert")).textContent).toBe(
      "Another device is on this shipment; take it off first.",
    );
  });

  it("offers no attaching once delivered", async () => {
    const s = assigned("DELIVERED");
    hub(detail(s));
    show(s);
    await screen.findByTestId("cold-box");
    expect(screen.queryByRole("button", { name: "Attach cold box" })).toBeNull();
  });
});
