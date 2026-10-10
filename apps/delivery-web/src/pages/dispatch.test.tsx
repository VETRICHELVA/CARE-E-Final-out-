import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Shipment } from "../api";
import {
  assigned,
  coldShipment,
  coldVan,
  meAs,
  page,
  priya,
  products,
  ravi,
  transfer,
  van,
} from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { DispatchPage } from "./dispatch";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ALL: Shipment[] = [transfer, coldShipment, assigned("ASSIGNED")];

/** The fake hub's shipment list, filtered by `?status=` as the hub does. */
const list = (shipments: Shipment[]) => (call: Call) => {
  const status = call.url.searchParams.get("status");
  return page(status ? shipments.filter((s) => s.status === status) : shipments);
};

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/shipments": list(ALL),
    "GET /api/v1/drivers": page([ravi, { ...priya, active: false }]),
    "GET /api/v1/vehicles": page([van, coldVan]),
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "DISPATCHER", path = "/") =>
  renderAs(meAs(role), <DispatchPage />, { path, route: "/" });

const card = (s: Shipment) => screen.findByTestId(`shipment-${s.id}`);

/** Opens the assign dialog for `s` and returns it. */
async function openAssign(s: Shipment) {
  fireEvent.click(within(await card(s)).getByRole("button", { name: "Assign" }));
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByLabelText("Driver");
  return dialog;
}

const options = (select: HTMLElement) =>
  within(select)
    .getAllByRole("option")
    .map((o) => o.textContent);

describe("Dispatch board", () => {
  it("lists unassigned shipments with pickup, drop, deadline, qty and flags", async () => {
    const fake = hub();
    show();
    const row = await card(transfer);
    expect(within(row).getByText("Surgical Kit A")).toBeTruthy();
    expect(within(row).getByText("350 kits")).toBeTruthy();
    expect(within(row).getByText("Hospital B, Hospital B Main Store")).toBeTruthy();
    expect(within(row).getByText("Hospital A, Hospital A Main Store")).toBeTruthy();
    expect(within(row).getByText("Needed by")).toBeTruthy();
    expect(within(row).queryByText("Cold chain")).toBeNull();
    const cold = await card(coldShipment);
    expect(within(cold).getByText("Cold chain")).toBeTruthy();
    expect(within(cold).getByText("Critical")).toBeTruthy();
    expect(within(row).getByRole("link", { name: "Details" }).getAttribute("href")).toBe(
      `/shipments/${transfer.id}`,
    );
    expect(fake.to("GET", "/api/v1/shipments")[0]!.url.searchParams.get("status")).toBe("CREATED");
  });

  it("badges a shipment on the road with the hub's newest cold-chain event", async () => {
    const hot: Shipment = {
      ...assigned("IN_TRANSIT", coldShipment, 7),
      coldchain: {
        last_event_type: "EXCURSION",
        last_event_at: "2026-10-08T06:30:00Z",
        had_excursion: true,
      },
    };
    const back: Shipment = {
      ...assigned("IN_TRANSIT", coldShipment, 8),
      coldchain: {
        last_event_type: "RECOVERED",
        last_event_at: "2026-10-08T06:31:00Z",
        had_excursion: true,
      },
    };
    const quiet = assigned("IN_TRANSIT", coldShipment, 9);
    hub({ "GET /api/v1/shipments": list([hot, back, quiet]) });
    show("DISPATCHER", "/?status=IN_TRANSIT");
    expect(within(await card(hot)).getByTestId("coldchain-badge").textContent).toBe(
      "Temperature excursion",
    );
    expect(within(await card(back)).getByTestId("excursion-badge").textContent).toBe(
      "Excursion on record",
    );
    expect(within(await card(quiet)).queryByTestId("coldchain-badge")).toBeNull();
    expect(within(await card(quiet)).getByText("Cold chain")).toBeTruthy();
  });

  it("hides Assign from a user without shipment.assign", async () => {
    hub();
    show("NONE");
    const row = await card(transfer);
    expect(within(row).queryByRole("button", { name: "Assign" })).toBeNull();
  });

  it("offers only active drivers, and every vehicle for an ordinary shipment", async () => {
    hub();
    show();
    const dialog = await openAssign(transfer);
    expect(options(within(dialog).getByLabelText("Driver"))).toEqual([
      "Choose a driver",
      "Ravi (+91 90000 00001)",
    ]);
    expect(options(within(dialog).getByLabelText("Vehicle"))).toEqual([
      "Choose a vehicle",
      "KA-01-SM-0001",
      "KA-01-SM-0002 (cold chain)",
    ]);
  });

  it("offers only cold-chain vehicles for a cold-chain shipment", async () => {
    hub();
    show();
    const dialog = await openAssign(coldShipment);
    expect(options(within(dialog).getByLabelText("Vehicle"))).toEqual([
      "Choose a vehicle",
      "KA-01-SM-0002 (cold chain)",
    ]);
    expect(within(dialog).getByText(/only those are listed/)).toBeTruthy();
  });

  it("says so when a cold-chain shipment has no vehicle to go on", async () => {
    hub({ "GET /api/v1/vehicles": page([van]) });
    show();
    const dialog = await openAssign(coldShipment);
    expect(options(within(dialog).getByLabelText("Vehicle"))).toEqual(["Choose a vehicle"]);
    expect(within(dialog).getByText(/your fleet has none/)).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Assign" })).toHaveProperty("disabled", true);
  });

  it("assigns the chosen driver and vehicle, then refreshes the board", async () => {
    const fake = hub({
      "POST /api/v1/shipments/{id}/assign": assigned("ASSIGNED", coldShipment),
    });
    show();
    const dialog = await openAssign(coldShipment);
    fireEvent.change(within(dialog).getByLabelText("Driver"), { target: { value: ravi.id } });
    fireEvent.change(within(dialog).getByLabelText("Vehicle"), { target: { value: coldVan.id } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Assign" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/shipments/${coldShipment.id}/assign`);
    expect(JSON.parse(call!.body!)).toEqual({ driver_id: ravi.id, vehicle_id: coldVan.id });
    await waitFor(() => expect(fake.to("GET", "/api/v1/shipments")).toHaveLength(2));
  });

  it("shows the hub's cold-chain refusal when the vehicle turns out to have none", async () => {
    // The list said cold chain, but the hub knows better (e.g. the unit was removed since).
    hub({
      "POST /api/v1/shipments/{id}/assign": Response.json(
        {
          code: "validation",
          message:
            "This shipment needs a cold-chain vehicle; vehicle KA-01-SM-0002 has no cold chain.",
          details: { reason: "cold_chain_vehicle_required" },
        },
        { status: 400 },
      ),
    });
    show();
    const dialog = await openAssign(coldShipment);
    fireEvent.change(within(dialog).getByLabelText("Driver"), { target: { value: ravi.id } });
    fireEvent.change(within(dialog).getByLabelText("Vehicle"), { target: { value: coldVan.id } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Assign" }));
    expect((await within(dialog).findByRole("alert")).textContent).toBe(
      "This shipment needs a cold-chain vehicle; vehicle KA-01-SM-0002 has no cold chain.",
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("sends a typed reason with the assignment", async () => {
    const fake = hub({ "POST /api/v1/shipments/{id}/assign": assigned("ASSIGNED") });
    show();
    const dialog = await openAssign(transfer);
    fireEvent.change(within(dialog).getByLabelText("Driver"), { target: { value: ravi.id } });
    fireEvent.change(within(dialog).getByLabelText("Vehicle"), { target: { value: van.id } });
    fireEvent.change(within(dialog).getByLabelText("Reason (optional)"), {
      target: { value: "Closest van" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Assign" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/shipments/${transfer.id}/assign`);
    expect(JSON.parse(call!.body!)).toEqual({
      driver_id: ravi.id,
      vehicle_id: van.id,
      reason: "Closest van",
    });
  });

  it("filters by status through the hub's ?status=, with no Assign on the road", async () => {
    const fake = hub();
    show("DISPATCHER", "/?status=ASSIGNED");
    const onRoad = assigned("ASSIGNED");
    const row = await card(onRoad);
    expect(within(row).getByText("Ravi, KA-01-SM-0002")).toBeTruthy();
    expect(within(row).getByText("Assigned")).toBeTruthy();
    expect(within(row).queryByRole("button", { name: "Assign" })).toBeNull();
    expect(screen.queryByTestId(`shipment-${transfer.id}`)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Unassigned" }));
    await card(transfer);
    const statuses = fake
      .to("GET", "/api/v1/shipments")
      .map((c) => c.url.searchParams.get("status"));
    expect(statuses).toEqual(["ASSIGNED", "CREATED"]);
  });

  it("sends a driver-only user to their jobs", async () => {
    hub();
    show("DRIVER");
    expect((await screen.findByTestId("location")).textContent).toBe("/driver");
  });

  it("shows loading, then an empty state", async () => {
    hub({ "GET /api/v1/shipments": page([]) });
    show();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(await screen.findByText("Nothing to dispatch")).toBeTruthy();
  });

  it("shows the hub's message when the list fails", async () => {
    hub({ "GET /api/v1/shipments": hubError(500, "internal_error", "The hub is down.") });
    show();
    expect((await screen.findByRole("alert")).textContent).toContain("The hub is down.");
  });
});
