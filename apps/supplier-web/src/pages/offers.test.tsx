import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cannula, kitA, meAs, NOW, offerKitA, page, products, staleRdk } from "../test/fixtures";
import { fakeHub, hubError, renderAs } from "../test/hub";
import { OffersPage } from "./offers";

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
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "SUPPLIER_DESK") =>
  renderAs(meAs(role), <OffersPage />, { path: "/offers" });

const row = (code: string) => screen.findByTestId(`offer-${code}`);
const field = (r: HTMLElement, label: string) => within(r).getByLabelText(label);

describe("Catalog and offers", () => {
  it("shows price in ₹, lead time, available quantity and last updated per offer", async () => {
    hub();
    show();
    const r = await row("SURG-KIT-A");
    expect(within(r).getByText("₹28.00")).toBeTruthy();
    expect(within(r).getByText("22 h")).toBeTruthy();
    expect(within(r).getByText("2,000 kits")).toBeTruthy();
    expect(
      within(r).getByText(
        new Date(offerKitA.updated_at).toLocaleString("en-IN", {
          dateStyle: "medium",
          timeStyle: "short",
        }),
      ),
    ).toBeTruthy();
    expect(within(r).queryByTestId("stale")).toBeNull();
    // Products not offered stay hidden until asked for.
    expect(screen.queryByTestId(`offer-${cannula.code}`)).toBeNull();
    fireEvent.click(screen.getByLabelText("Show products you don't offer"));
    expect(within(await row(cannula.code)).getByText("Not offered")).toBeTruthy();
  });

  it("flags an offer not updated in 7 days", async () => {
    hub();
    show();
    const r = await row("DIAG-RDK");
    expect(within(r).getByTestId("stale").textContent).toBe("Not updated in 9 days");
    expect(within(r).getByText("₹900.50")).toBeTruthy();
  });

  it("edits inline and sends integer paise, hours and quantity", async () => {
    const fake = hub({ "PUT /api/v1/supplier-offers": offerKitA });
    show();
    fireEvent.click(within(await row("SURG-KIT-A")).getByRole("button", { name: "Edit" }));
    const r = await row("SURG-KIT-A");
    expect((field(r, "Unit price (₹)") as HTMLInputElement).value).toBe("28.00");
    fireEvent.change(field(r, "Unit price (₹)"), { target: { value: "27.5" } });
    fireEvent.change(field(r, "Available quantity (kits)"), { target: { value: "1800" } });
    fireEvent.click(within(r).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(fake.to("PUT", "/api/v1/supplier-offers")).toHaveLength(1));
    expect(JSON.parse(fake.to("PUT", "/api/v1/supplier-offers")[0]!.body!)).toEqual({
      product_id: kitA.id,
      unit_price_paise: 2750,
      lead_time_hours: 22,
      available_qty: 1800,
    });
    // Back to the read-only row; the list is refetched from the hub.
    expect(
      await within(await row("SURG-KIT-A")).findByRole("button", { name: "Edit" }),
    ).toBeTruthy();
    await waitFor(() => expect(fake.to("GET", "/api/v1/supplier-offers")).toHaveLength(2));
  });

  it("checks the shape of each field before sending anything", async () => {
    const fake = hub();
    show();
    fireEvent.click(within(await row("SURG-KIT-A")).getByRole("button", { name: "Edit" }));
    const r = await row("SURG-KIT-A");
    fireEvent.change(field(r, "Unit price (₹)"), { target: { value: "28.505" } });
    fireEvent.change(field(r, "Lead time (hours)"), { target: { value: "-1" } });
    fireEvent.change(field(r, "Available quantity (kits)"), { target: { value: "1.5" } });
    fireEvent.click(within(r).getByRole("button", { name: "Save" }));
    expect(await within(r).findByText("Enter a price in ₹, e.g. 28 or 28.50.")).toBeTruthy();
    expect(within(r).getByText("Lead time must be a whole number, 0 or more.")).toBeTruthy();
    expect(
      within(r).getByText("Available quantity must be a whole number, 0 or more."),
    ).toBeTruthy();
    expect(fake.to("PUT", "/api/v1/supplier-offers")).toHaveLength(0);
  });

  it("shows the hub's refusal in the row and keeps the edit open", async () => {
    hub({
      "PUT /api/v1/supplier-offers": hubError(403, "forbidden", "Missing capability po.respond."),
    });
    show();
    fireEvent.click(within(await row("SURG-KIT-A")).getByRole("button", { name: "Edit" }));
    const r = await row("SURG-KIT-A");
    fireEvent.click(within(r).getByRole("button", { name: "Save" }));
    expect(await within(r).findByText("Missing capability po.respond.")).toBeTruthy();
    expect(within(r).getByRole("button", { name: "Save" })).toBeTruthy();
  });

  it("re-confirms a stale offer unchanged with Still current", async () => {
    const fake = hub({ "PUT /api/v1/supplier-offers": staleRdk });
    show();
    const r = await row("DIAG-RDK");
    expect(
      within(await row("SURG-KIT-A")).queryByRole("button", { name: "Still current" }),
    ).toBeNull();
    fireEvent.click(within(r).getByRole("button", { name: "Still current" }));
    await waitFor(() => expect(fake.to("PUT", "/api/v1/supplier-offers")).toHaveLength(1));
    expect(JSON.parse(fake.to("PUT", "/api/v1/supplier-offers")[0]!.body!)).toEqual({
      product_id: staleRdk.product_id,
      unit_price_paise: 90050,
      lead_time_hours: 48,
      available_qty: 50,
    });
  });

  it("adds an offer for a product not offered yet", async () => {
    const fake = hub({ "PUT /api/v1/supplier-offers": offerKitA });
    show();
    fireEvent.click(await screen.findByLabelText("Show products you don't offer"));
    fireEvent.click(within(await row(cannula.code)).getByRole("button", { name: "Offer" }));
    const r = await row(cannula.code);
    fireEvent.change(field(r, "Unit price (₹)"), { target: { value: "12" } });
    fireEvent.change(field(r, "Lead time (hours)"), { target: { value: "24" } });
    fireEvent.change(field(r, "Available quantity (pieces)"), { target: { value: "500" } });
    fireEvent.click(within(r).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(fake.to("PUT", "/api/v1/supplier-offers")).toHaveLength(1));
    expect(JSON.parse(fake.to("PUT", "/api/v1/supplier-offers")[0]!.body!)).toEqual({
      product_id: cannula.id,
      unit_price_paise: 1200,
      lead_time_hours: 24,
      available_qty: 500,
    });
  });

  it("is read-only without po.respond", async () => {
    hub();
    show("DISPATCHER");
    const r = await row("DIAG-RDK");
    expect(within(r).getByTestId("stale")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Still current" })).toBeNull();
  });

  it("shows loading, then an empty state", async () => {
    hub({ "GET /api/v1/supplier-offers": page([]) });
    show();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(await screen.findByText("You don't offer any products yet")).toBeTruthy();
  });

  it("shows the hub's message when the list fails", async () => {
    hub({ "GET /api/v1/supplier-offers": hubError(500, "internal_error", "The hub is down.") });
    show();
    expect((await screen.findByRole("alert")).textContent).toContain("The hub is down.");
  });
});
