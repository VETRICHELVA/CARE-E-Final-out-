import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PurchaseOrder } from "../api";
import { meAs, NOW, offerKitA, page, po, poAt, products, staleRdk } from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { DashboardPage } from "./dashboard";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const ORDERS: PurchaseOrder[] = [
  po,
  poAt("SENT", 2),
  poAt("ACKNOWLEDGED", 3),
  poAt("DISPATCHED", 4),
];

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/supplier-offers": page([offerKitA, staleRdk]),
    "GET /api/v1/purchase-orders": (call: Call) =>
      page(ORDERS.filter((o) => o.status === call.url.searchParams.get("status"))),
    ...extra,
  });

const card = (name: string) => screen.findByRole("region", { name: new RegExp(`^${name}`) });

describe("Dashboard", () => {
  it("shows new purchase orders and orders to dispatch from the hub's status lists", async () => {
    const fake = hub();
    renderAs(meAs("SUPPLIER_DESK"), <DashboardPage />);
    const fresh = await card("New purchase orders");
    expect((await within(fresh).findByTestId("new-purchase-orders-count")).textContent).toBe("2");
    expect(within(fresh).getAllByRole("link", { name: "850 kits Surgical Kit A" })).toHaveLength(2);
    expect(within(fresh).getByRole("link", { name: "View all" }).getAttribute("href")).toBe(
      "/orders?status=SENT",
    );
    const toDispatch = await card("Orders to dispatch");
    expect((await within(toDispatch).findByTestId("orders-to-dispatch-count")).textContent).toBe(
      "1",
    );
    expect(
      within(toDispatch)
        .getByRole("link", { name: "850 kits Surgical Kit A" })
        .getAttribute("href"),
    ).toBe(`/orders/${poAt("ACKNOWLEDGED", 3).id}`);
    const statuses = fake
      .to("GET", "/api/v1/purchase-orders")
      .map((c) => c.url.searchParams.get("status"));
    expect(new Set(statuses)).toEqual(new Set(["SENT", "ACKNOWLEDGED"]));
  });

  it("flags offers not updated in 7 days (they fail the freshness gate)", async () => {
    hub();
    renderAs(meAs("SUPPLIER_DESK"), <DashboardPage />);
    const stale = await card("Offers not updated in 7 days");
    const list = await within(stale).findByRole("list", { name: "Stale offers" });
    expect(within(list).getByText("Rapid Diagnostic Kit")).toBeTruthy();
    expect(within(list).getByText("Not updated in 9 days")).toBeTruthy();
    expect(within(list).queryByText("Surgical Kit A")).toBeNull();
  });

  it("shows only the offers card to a user without po.respond, and asks for no orders", async () => {
    const fake = hub();
    renderAs(meAs("DISPATCHER"), <DashboardPage />);
    expect(await card("Offers not updated in 7 days")).toBeTruthy();
    expect(screen.queryByRole("region", { name: /New purchase orders/ })).toBeNull();
    expect(screen.queryByRole("region", { name: /Orders to dispatch/ })).toBeNull();
    expect(fake.to("GET", "/api/v1/purchase-orders")).toHaveLength(0);
  });

  it("shows empty states when nothing needs attention", async () => {
    hub({
      "GET /api/v1/purchase-orders": page([]),
      "GET /api/v1/supplier-offers": page([offerKitA]),
    });
    renderAs(meAs("SUPPLIER_DESK"), <DashboardPage />);
    expect(await screen.findByText("No new purchase orders")).toBeTruthy();
    expect(screen.getByText("Nothing waiting to be dispatched")).toBeTruthy();
    expect(await screen.findByText("Every offer is up to date")).toBeTruthy();
  });

  it("shows the hub's message when a list fails", async () => {
    hub({ "GET /api/v1/supplier-offers": hubError(500, "internal_error", "The hub is down.") });
    renderAs(meAs("SUPPLIER_DESK"), <DashboardPage />);
    expect((await screen.findByRole("alert")).textContent).toContain("The hub is down.");
  });
});
