import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RoutePlan, Shipment } from "../api";
import {
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
import { fakeHub, hubError, renderAs } from "../test/hub";
import { RoutePlannerPage } from "./route-planner";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** A third unassigned shipment, due too soon to reach. */
const late: Shipment = {
  ...transfer,
  id: "51000000-0000-4000-8000-000000000009",
  product_name: "IV Cannula 20G",
  from_org_name: "Hospital C",
  pickup: { ...transfer.pickup!, place: "Hospital C Main Store" },
};

const stop = (
  seq: number,
  s: Shipment,
  type: "PICKUP" | "DROP",
  eta: string,
): RoutePlan["stops"][number] => {
  const where = type === "PICKUP" ? s.pickup! : s.drop!;
  return {
    seq,
    shipment_id: s.id,
    type,
    place: where.place,
    lat: where.lat,
    lng: where.lng,
    eta,
  };
};

const REASON =
  "Cannot reach Hospital A Main Store before 14:00 IST: even going straight there, the earliest arrival is 14:30 IST.";

const plan: RoutePlan = {
  driver_id: ravi.id,
  vehicle_id: coldVan.id,
  stops: [
    stop(1, transfer, "PICKUP", "2026-10-08T06:00:00Z"),
    stop(2, coldShipment, "PICKUP", "2026-10-08T07:20:00Z"),
    stop(3, transfer, "DROP", "2026-10-08T08:40:00Z"),
    stop(4, coldShipment, "DROP", "2026-10-08T08:55:00Z"),
  ],
  infeasible: [{ shipment_id: late.id, reason: REASON }],
};

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/shipments": page([transfer, coldShipment, late]),
    "GET /api/v1/drivers": page([ravi, { ...priya, active: false }]),
    "GET /api/v1/vehicles": page([van, coldVan]),
    "POST /api/v1/routes/optimize": plan,
    "POST /api/v1/routes/apply": {
      ...plan,
      assigned_shipment_ids: [transfer.id, coldShipment.id],
    },
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "DISPATCHER") =>
  renderAs(meAs(role), <RoutePlannerPage />, { path: "/plan" });

const options = (select: HTMLElement) =>
  within(select)
    .getAllByRole("option")
    .map((o) => o.textContent);

const box = (name: RegExp) => screen.getByRole("checkbox", { name });

/** Chooses Ravi, the cold van and all three shipments. */
async function choose() {
  fireEvent.change(await screen.findByLabelText("Driver"), { target: { value: ravi.id } });
  fireEvent.change(screen.getByLabelText("Vehicle"), { target: { value: coldVan.id } });
  for (const name of [/Surgical Kit A/, /Rapid Diagnostic Kit/, /IV Cannula 20G/])
    fireEvent.click(box(name));
}

async function planRoute() {
  await choose();
  fireEvent.click(screen.getByRole("button", { name: "Plan route" }));
  return screen.findByRole("list", { name: "Stops" });
}

describe("Route planner", () => {
  it("offers active drivers, every vehicle and every unassigned shipment", async () => {
    const fake = hub();
    show();
    expect(options(await screen.findByLabelText("Driver"))).toEqual([
      "Choose a driver",
      "Ravi (+91 90000 00001)",
    ]);
    expect(options(screen.getByLabelText("Vehicle"))).toEqual([
      "Choose a vehicle",
      "KA-01-SM-0001",
      "KA-01-SM-0002 (cold chain)",
    ]);
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    const cold = box(/Rapid Diagnostic Kit/).closest("label")!;
    expect(within(cold).getByText("Cold chain")).toBeTruthy();
    expect(cold.textContent).toContain("Supplier Y to Hospital A, Hospital A Main Store");
    expect(cold.textContent).toContain("Needed by");
    expect(fake.to("GET", "/api/v1/shipments")[0]!.url.searchParams.get("status")).toBe("CREATED");
    expect(screen.getByRole("button", { name: "Plan route" })).toHaveProperty("disabled", true);
    expect(screen.queryByRole("button", { name: /Apply/ })).toBeNull();
  });

  it("asks the hub for a plan and shows the stops in order, the map and the reasons", async () => {
    const fake = hub();
    show();
    const stops = await planRoute();
    const [call] = fake.to("POST", "/api/v1/routes/optimize");
    expect(JSON.parse(call!.body!)).toEqual({
      driver_id: ravi.id,
      vehicle_id: coldVan.id,
      shipment_ids: [transfer.id, coldShipment.id, late.id],
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    });
    const rows = within(stops).getAllByTestId("stop");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringMatching(/^1\.PickupHospital B Main StoreSurgical Kit A/),
      expect.stringMatching(/^2\.PickupSupplier YRapid Diagnostic Kit/),
      expect.stringMatching(/^3\.DropHospital A Main StoreSurgical Kit A/),
      expect.stringMatching(/^4\.DropHospital A Main StoreRapid Diagnostic Kit/),
    ]);
    // The map joins the stops in driving order and marks each one.
    const map = screen.getByTestId("map");
    expect(JSON.parse(within(map).getByTestId("map-route").dataset.positions!)).toEqual(
      plan.stops.map((s) => [s.lat, s.lng]),
    );
    expect(within(map).getAllByTestId("map-marker")).toHaveLength(4);
    expect(within(map).getByText("2. Pickup: Supplier Y")).toBeTruthy();
    // The shipment that cannot fit, in the hub's words.
    const [reason] = screen.getAllByTestId("infeasible");
    expect(reason!.textContent).toBe(`IV Cannula 20G: ${REASON}`);
    expect(screen.getByRole("button", { name: "Apply: assign 2 shipments to Ravi" })).toBeTruthy();
  });

  it("applies the plan with the same choices and a typed reason", async () => {
    const fake = hub();
    show();
    await planRoute();
    fireEvent.change(screen.getByLabelText("Reason (optional)"), {
      target: { value: "Morning round" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply: assign 2 shipments to Ravi" }));
    expect(await screen.findByText("Route applied")).toBeTruthy();
    const [call] = fake.to("POST", "/api/v1/routes/apply");
    expect(JSON.parse(call!.body!)).toEqual({
      driver_id: ravi.id,
      vehicle_id: coldVan.id,
      shipment_ids: [transfer.id, coldShipment.id, late.id],
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      reason: "Morning round",
    });
    // The applied plan stays on screen, the choices are cleared and the lists refetched.
    expect(screen.getAllByTestId("stop")).toHaveLength(4);
    expect(screen.queryByRole("button", { name: /Apply/ })).toBeNull();
    expect(screen.getAllByRole("checkbox").every((c) => !(c as HTMLInputElement).checked)).toBe(
      true,
    );
    await waitFor(() => expect(fake.to("GET", "/api/v1/shipments")).toHaveLength(2));
  });

  it("shows the hub's refusal to apply in its own words", async () => {
    hub({
      "POST /api/v1/routes/apply": hubError(
        409,
        "invalid_transition",
        "Shipment 51000000 is ASSIGNED; only unassigned shipments can be planned.",
      ),
    });
    show();
    await planRoute();
    fireEvent.click(screen.getByRole("button", { name: /Apply/ }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Shipment 51000000 is ASSIGNED; only unassigned shipments can be planned.",
    );
    expect(screen.queryByText("Route applied")).toBeNull();
  });

  it("drops a shown plan when the choices change", async () => {
    hub();
    show();
    await planRoute();
    fireEvent.click(box(/IV Cannula 20G/));
    expect(screen.queryByRole("list", { name: "Stops" })).toBeNull();
    expect(screen.getByText(/then plan the route/)).toBeTruthy();
  });

  it("offers no Apply when nothing fits", async () => {
    hub({
      "POST /api/v1/routes/optimize": {
        ...plan,
        stops: [],
        infeasible: [{ shipment_id: late.id, reason: REASON }],
      },
    });
    show();
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Plan route" }));
    expect(await screen.findByText("No shipment fits this route.")).toBeTruthy();
    expect(screen.queryByTestId("map")).toBeNull();
    expect(screen.queryByRole("button", { name: /Apply/ })).toBeNull();
  });

  it("shows the hub's message when planning fails", async () => {
    hub({
      "POST /api/v1/routes/optimize": hubError(
        403,
        "forbidden",
        "This driver belongs to another organization.",
      ),
    });
    show();
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Plan route" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "This driver belongs to another organization.",
    );
  });

  it("is for dispatchers only", async () => {
    const fake = hub();
    show("DRIVER");
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Only dispatchers can plan routes.",
    );
    expect(fake.to("GET", "/api/v1/drivers")).toHaveLength(0);
  });

  it("says when there is nothing to plan", async () => {
    hub({ "GET /api/v1/shipments": page([]) });
    show();
    expect(await screen.findByText(/Nothing to dispatch/)).toBeTruthy();
  });
});
