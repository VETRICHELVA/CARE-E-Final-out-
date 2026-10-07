import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PurchaseOrder } from "../api";
import { meAs, page, po, poAt, products } from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { OrderDetailPage } from "./order-detail";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const show = (id: string, role: Parameters<typeof meAs>[0] = "SUPPLIER_DESK") =>
  renderAs(meAs(role), <OrderDetailPage />, { path: `/orders/${id}`, route: "/orders/:id" });

const actions = () =>
  within(screen.getByRole("heading", { level: 1 }).parentElement!.parentElement!)
    .queryAllByRole("button")
    .map((b) => b.textContent);

describe("Purchase order detail", () => {
  it("shows the order as the hub returns it", async () => {
    fakeHub({ "GET /api/v1/products": products, "GET /api/v1/purchase-orders": page([po]) });
    show(po.id);
    expect(await screen.findByText("Order for Hospital A")).toBeTruthy();
    expect(screen.getByText("850 kits")).toBeTruthy();
    expect(screen.getByText("₹28.00")).toBeTruthy();
    expect(screen.getByText("Surgical Kit A")).toBeTruthy();
    expect(screen.getByText("Created when you mark it dispatched")).toBeTruthy();
    expect(actions()).toEqual(["Acknowledge", "Reject"]);
  });

  it("finds the order on a later page of the hub's list", async () => {
    const other = poAt("REJECTED", 7);
    const fake = fakeHub({
      "GET /api/v1/products": products,
      "GET /api/v1/purchase-orders": (call: Call) =>
        call.url.searchParams.get("cursor") === "c2"
          ? page([poAt("ACKNOWLEDGED", 2)])
          : { items: [other], next_cursor: "c2" },
    });
    show(poAt("ACKNOWLEDGED", 2).id);
    expect(await screen.findByText("Acknowledged")).toBeTruthy();
    expect(actions()).toEqual(["Mark dispatched", "Reject"]);
    expect(fake.to("GET", "/api/v1/purchase-orders")).toHaveLength(2);
  });

  it.each(["DISPATCHED", "DELIVERED", "REJECTED"] as const)(
    "offers no action on a %s order",
    async (status) => {
      const order = poAt(status, 3);
      fakeHub({ "GET /api/v1/products": products, "GET /api/v1/purchase-orders": page([order]) });
      show(order.id);
      expect(await screen.findByTestId("no-actions")).toBeTruthy();
      expect(actions()).toEqual([]);
    },
  );

  it("hides the actions from a user without po.respond", async () => {
    fakeHub({ "GET /api/v1/products": products, "GET /api/v1/purchase-orders": page([po]) });
    show(po.id, "DISPATCHER");
    expect(await screen.findByText("Order for Hospital A")).toBeTruthy();
    expect(actions()).toEqual([]);
  });

  it("says so when the order is not this supplier's", async () => {
    fakeHub({ "GET /api/v1/products": products, "GET /api/v1/purchase-orders": page([]) });
    show(po.id);
    expect(await screen.findByText("Purchase order not found")).toBeTruthy();
  });

  it("shows the hub's message when loading fails", async () => {
    fakeHub({
      "GET /api/v1/products": products,
      "GET /api/v1/purchase-orders": hubError(500, "internal_error", "The hub is down."),
    });
    show(po.id);
    expect((await screen.findByRole("alert")).textContent).toContain("The hub is down.");
  });

  it("Scenario 1 step 6: Supplier Y acknowledges, then dispatches the order", async () => {
    // A fake hub that keeps the order's state, as the real one would.
    let current: PurchaseOrder = po;
    const move = (to: PurchaseOrder["status"], shipment: string | null) => () => {
      current = { ...current, status: to, shipment_id: shipment };
      return current;
    };
    const fake = fakeHub({
      "GET /api/v1/products": products,
      "GET /api/v1/purchase-orders": () => page([current]),
      "POST /api/v1/purchase-orders/{id}/acknowledge": move("ACKNOWLEDGED", null),
      "POST /api/v1/purchase-orders/{id}/dispatch": move(
        "DISPATCHED",
        "51000000-0000-4000-8000-000000000001",
      ),
    });
    show(po.id);
    expect(await screen.findByText("Sent")).toBeTruthy();
    expect(actions()).toEqual(["Acknowledge", "Reject"]);

    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Acknowledge" }),
    );
    expect(await screen.findByText("Acknowledged")).toBeTruthy();
    await waitFor(() => expect(actions()).toEqual(["Mark dispatched", "Reject"]));

    fireEvent.click(screen.getByRole("button", { name: "Mark dispatched" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/850 kits left for Hospital A/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Mark dispatched" }));
    expect(await screen.findByText("Dispatched")).toBeTruthy();
    expect(screen.getByTestId("shipment").textContent).toBe("Created (51000000)");
    expect(actions()).toEqual([]);
    expect(
      fake.calls.filter((c) => c.method === "POST").map((c) => c.path.split("/").at(-1)),
    ).toEqual(["acknowledge", "dispatch"]);
  });
});
