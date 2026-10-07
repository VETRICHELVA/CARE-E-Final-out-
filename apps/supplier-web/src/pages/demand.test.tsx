import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  demandKitA,
  demandRdk,
  meAs,
  NOW,
  offerKitA,
  page,
  products,
  staleRdk,
} from "../test/fixtures";
import { fakeHub, hubError, renderAs } from "../test/hub";
import { DemandPage } from "./demand";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/supplier-offers": page([offerKitA, staleRdk]),
    "GET /api/v1/network/demand": page([demandKitA, demandRdk]),
    ...extra,
  });

const show = () => renderAs(meAs("SUPPLIER_DESK"), <DemandPage />, { path: "/demand" });

describe("Network demand", () => {
  it("shows the hub's open shortfall per product beside this supplier's stock", async () => {
    hub();
    show();
    const kit = await screen.findByTestId("demand-SURG-KIT-A");
    expect(within(kit).getByText("Surgical Kit A")).toBeTruthy();
    expect(within(kit).getByText("850 kits")).toBeTruthy();
    expect(within(kit).getByText("2,000 kits")).toBeTruthy();
    expect(within(kit).queryByTestId("stale")).toBeNull();
    const rdk = screen.getByTestId("demand-DIAG-RDK");
    expect(within(rdk).getByText("No open demand")).toBeTruthy();
    expect(within(rdk).getByTestId("stale").textContent).toBe("Not updated in 9 days");
  });

  it("names no hospital: the hub sends none and the page adds none", async () => {
    hub();
    show();
    const table = await screen.findByRole("table", { name: "Network demand" });
    expect(table.textContent).not.toMatch(/hospital/i);
  });

  it("refreshes from the hub on request (hospitals' changes send this org no event)", async () => {
    const fake = hub();
    show();
    await screen.findByTestId("demand-SURG-KIT-A");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(fake.to("GET", "/api/v1/network/demand")).toHaveLength(2));
  });

  it("shows loading, then an empty state without offers", async () => {
    hub({ "GET /api/v1/network/demand": page([]), "GET /api/v1/supplier-offers": page([]) });
    show();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(await screen.findByText("You don't offer any products yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Add an offer" }).getAttribute("href")).toBe("/offers");
  });

  it("shows the hub's message when the list fails", async () => {
    hub({
      "GET /api/v1/network/demand": hubError(
        403,
        "forbidden",
        "Only SUPPLIER organizations can do this.",
      ),
    });
    show();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Only SUPPLIER organizations can do this.",
    );
  });
});
