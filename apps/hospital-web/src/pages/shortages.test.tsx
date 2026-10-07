import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { facility, kitA, meAs, page, products, shortage } from "../test/fixtures";
import { type Call, fakeHub, hubError, renderAs } from "../test/hub";
import { ShortagesPage } from "./shortages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const hub = (extra: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/shortages": page([shortage]),
    "GET /api/v1/products": products,
    "GET /api/v1/orgs/{id}/facilities": page([facility]),
    ...extra,
  });

const show = (role: Parameters<typeof meAs>[0] = "REQUESTER") =>
  renderAs(meAs(role), <ShortagesPage />, { path: "/shortages" });

/** Fills the form with Scenario 1 step 1. */
function fillScenario1() {
  fireEvent.change(screen.getByLabelText("Product"), { target: { value: kitA.id } });
  fireEvent.change(screen.getByLabelText("Quantity required"), { target: { value: "1000" } });
  fireEvent.change(screen.getByLabelText("Usable stock on hand"), { target: { value: "150" } });
  fireEvent.change(screen.getByLabelText("Required by"), { target: { value: "2026-10-10T11:30" } });
  fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "CRITICAL" } });
  fireEvent.change(screen.getByLabelText("Minimum shelf life (days)"), { target: { value: "30" } });
}

describe("Shortages list", () => {
  it("lists shortages with a status chip and the hub's shortfall, linking to the detail", async () => {
    hub();
    show();
    const link = await screen.findByRole("link", { name: "Surgical Kit A" });
    const row = within(link.closest("tr")!);
    expect(row.getByText("Matching")).toBeTruthy();
    expect(row.getByText("Critical")).toBeTruthy();
    expect(row.getByText("850 kits")).toBeTruthy();
    fireEvent.click(link);
    expect(screen.getByTestId("location").textContent).toBe(`/shortages/${shortage.id}`);
  });

  it("shows loading, then an empty state", async () => {
    hub({ "GET /api/v1/shortages": page([]) });
    show();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(await screen.findByText("No shortages reported")).toBeTruthy();
  });

  it("shows the hub's message when the list fails", async () => {
    hub({ "GET /api/v1/shortages": hubError(403, "forbidden", "You lack shortage.create.") });
    show();
    expect((await screen.findByRole("alert")).textContent).toContain("You lack shortage.create.");
  });

  it("hides New shortage from a user without shortage.create", async () => {
    hub();
    show("APPROVER");
    await screen.findByRole("link", { name: "Surgical Kit A" });
    expect(screen.queryByRole("button", { name: "New shortage" })).toBeNull();
  });

  it("shows New shortage to a requester and a store manager", async () => {
    for (const role of ["REQUESTER", "STORE_MANAGER"] as const) {
      hub();
      show(role);
      expect(await screen.findByRole("button", { name: "New shortage" })).toBeTruthy();
      cleanup();
    }
  });
});

describe("New shortage", () => {
  it("validates with zod before calling the hub", async () => {
    const fake = hub();
    show();
    fireEvent.click(await screen.findByRole("button", { name: "New shortage" }));
    fireEvent.click(await screen.findByRole("button", { name: "Report shortage" }));
    expect(await screen.findByText("Choose a product.")).toBeTruthy();
    expect(screen.getByText("Enter the quantity required.")).toBeTruthy();
    expect(screen.getByText("Choose when you need it by.")).toBeTruthy();
    expect(screen.getByText("Choose a priority.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Quantity required"), { target: { value: "12.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Report shortage" }));
    expect(await screen.findByText("Enter a whole number, 0 or more.")).toBeTruthy();
    expect(fake.to("POST", "/api/v1/shortages")).toHaveLength(0);
  });

  it("sends the form without a shortfall and shows the hub's shortfall after save", async () => {
    const fake = hub({
      // The hub's figure on purpose differs from 1000 − 150, to prove we show the hub's answer.
      "POST /api/v1/shortages": (c: Call) =>
        Response.json({ ...shortage, ...JSON.parse(c.body!), shortfall: 851 }, { status: 201 }),
    });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "New shortage" }));
    await screen.findByLabelText("Product");
    expect(screen.getByText("Leave blank for the product's default.")).toBeTruthy();
    fillScenario1();
    fireEvent.click(screen.getByRole("button", { name: "Report shortage" }));

    expect((await screen.findByTestId("shortfall")).textContent).toBe("851 kits");
    expect(screen.getByText("Shortfall, computed by the hub")).toBeTruthy();
    const [call] = fake.to("POST", "/api/v1/shortages");
    const body = JSON.parse(call!.body!);
    expect(body).toEqual({
      product_id: kitA.id,
      facility_id: facility.id,
      qty_required: 1000,
      qty_local_usable: 150,
      required_by: new Date("2026-10-10T11:30").toISOString(),
      priority: "CRITICAL",
      min_shelf_life_days: 30,
    });
    expect(body).not.toHaveProperty("shortfall");

    fireEvent.click(screen.getByRole("link", { name: "View shortage" }));
    expect(screen.getByTestId("location").textContent).toBe(`/shortages/${shortage.id}`);
  });

  it("shows the hub's message when there is nothing to source", async () => {
    const message = "Nothing to source: local usable stock covers the requirement.";
    hub({ "POST /api/v1/shortages": hubError(400, "validation", message) });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "New shortage" }));
    await screen.findByLabelText("Product");
    fillScenario1();
    fireEvent.change(screen.getByLabelText("Usable stock on hand"), { target: { value: "1000" } });
    fireEvent.click(screen.getByRole("button", { name: "Report shortage" }));
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    await waitFor(() => expect(screen.queryByTestId("shortfall")).toBeNull());
  });

  it("names the product's default shelf life once a product is chosen", async () => {
    hub();
    show();
    fireEvent.click(await screen.findByRole("button", { name: "New shortage" }));
    fireEvent.change(await screen.findByLabelText("Product"), { target: { value: kitA.id } });
    expect(screen.getByText("Leave blank for the product's default of 30 days.")).toBeTruthy();
  });
});
