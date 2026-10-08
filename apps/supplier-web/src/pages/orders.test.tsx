import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PoStatus, PurchaseOrder } from "../api";
import { meAs, page, po, poAt, products } from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { OrdersPage } from "./orders";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ALL: PurchaseOrder[] = [
  po,
  poAt("ACKNOWLEDGED", 2),
  poAt("DISPATCHED", 3),
  poAt("DELIVERED", 4),
  poAt("REJECTED", 5),
];

/** The fake hub's order list, filtered by `?status=` as the hub does. */
const list = (orders: PurchaseOrder[]) => (call: Call) => {
  const status = call.url.searchParams.get("status");
  return page(status ? orders.filter((o) => o.status === status) : orders);
};

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/products": products,
    "GET /api/v1/purchase-orders": list(ALL),
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "SUPPLIER_DESK", path = "/orders") =>
  renderAs(meAs(role), <OrdersPage />, { path, route: "/orders" });

const rowOf = async (order: PurchaseOrder) => screen.findByTestId(`order-${order.id}`);
const buttonsIn = (row: HTMLElement) =>
  within(row)
    .queryAllByRole("button")
    .map((b) => b.textContent);

describe("Purchase orders", () => {
  it("lists the orders sent to this supplier with product, qty, price and hospital", async () => {
    hub();
    show();
    const row = await rowOf(po);
    expect(within(row).getByText("Surgical Kit A")).toBeTruthy();
    expect(within(row).getByText("850 kits")).toBeTruthy();
    expect(within(row).getByText("₹28.00")).toBeTruthy();
    expect(within(row).getByText("Hospital A")).toBeTruthy();
    expect(within(row).getByText("Sent")).toBeTruthy();
    expect(within(row).getByRole("link", { name: "Details" }).getAttribute("href")).toBe(
      `/orders/${po.id}`,
    );
  });

  it.each<[PoStatus, string[]]>([
    ["SENT", ["Acknowledge", "Reject"]],
    ["ACKNOWLEDGED", ["Mark dispatched", "Reject"]],
    ["DISPATCHED", []],
    ["DELIVERED", []],
    ["REJECTED", []],
  ])("shows only the valid actions for a %s order", async (status, expected) => {
    hub();
    show();
    const order = ALL.find((o) => o.status === status)!;
    expect(buttonsIn(await rowOf(order))).toEqual(expected);
  });

  it("shows no actions to a user without po.respond", async () => {
    hub();
    show("DISPATCHER");
    for (const order of ALL) expect(buttonsIn(await rowOf(order))).toEqual([]);
  });

  it("acknowledges without a reason: no body, so the hub records its own wording", async () => {
    const fake = hub({ "POST /api/v1/purchase-orders/{id}/acknowledge": poAt("ACKNOWLEDGED", 1) });
    show();
    fireEvent.click(within(await rowOf(po)).getByRole("button", { name: "Acknowledge" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Acknowledge Hospital A's order?")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/purchase-orders/${po.id}/acknowledge`);
    expect(call!.body).toBe("");
    // The list is refetched after the answer.
    await waitFor(() => expect(fake.to("GET", "/api/v1/purchase-orders")).toHaveLength(2));
  });

  it("rejects with the typed reason", async () => {
    const fake = hub({ "POST /api/v1/purchase-orders/{id}/reject": poAt("REJECTED", 1) });
    show();
    fireEvent.click(within(await rowOf(po)).getByRole("button", { name: "Reject" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Reason (optional)"), {
      target: { value: "Batch failed QC" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject order" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [call] = fake.to("POST", `/api/v1/purchase-orders/${po.id}/reject`);
    expect(JSON.parse(call!.body!)).toEqual({ reason: "Batch failed QC" });
  });

  it("shows the hub's 409 when the order already moved on, and refetches", async () => {
    const fake = hub({
      "POST /api/v1/purchase-orders/{id}/dispatch": hubError(
        409,
        "invalid_transition",
        "Cannot move a purchase order from REJECTED to DISPATCHED.",
      ),
    });
    show();
    const order = poAt("ACKNOWLEDGED", 2);
    fireEvent.click(within(await rowOf(order)).getByRole("button", { name: "Mark dispatched" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Mark dispatched" }));
    expect(
      await within(dialog).findByText("Cannot move a purchase order from REJECTED to DISPATCHED."),
    ).toBeTruthy();
    await waitFor(() => expect(fake.to("GET", "/api/v1/purchase-orders")).toHaveLength(2));
  });

  it("filters by status through the hub's ?status=", async () => {
    const fake = hub();
    show("SUPPLIER_DESK", "/orders?status=ACKNOWLEDGED");
    await rowOf(poAt("ACKNOWLEDGED", 2));
    expect(screen.queryByTestId(`order-${po.id}`)).toBeNull();
    expect(screen.getByRole("button", { name: "Acknowledged" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "Sent" }));
    await rowOf(po);
    const statuses = fake
      .to("GET", "/api/v1/purchase-orders")
      .map((c) => c.url.searchParams.get("status"));
    expect(statuses).toEqual(["ACKNOWLEDGED", "SENT"]);
  });

  it("shows loading, then an empty state", async () => {
    hub({ "GET /api/v1/purchase-orders": page([]) });
    show();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(await screen.findByText("No purchase orders yet")).toBeTruthy();
  });

  it("shows the hub's message when the list fails", async () => {
    hub({
      "GET /api/v1/purchase-orders": hubError(403, "forbidden", "Missing capability po.respond."),
    });
    show();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Missing capability po.respond.",
    );
  });
});
