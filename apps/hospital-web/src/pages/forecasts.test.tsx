import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  forecastB,
  forecastE,
  IV_BATCH,
  meAs,
  offerFromB,
  ownPost,
  page,
  productsWithIv,
  SURPLUS_ID,
} from "../test/fixtures";
import { fakeHub, hubError, renderAs } from "../test/hub";
import { ForecastsPage } from "./forecasts";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(Date.parse("2026-10-08T06:00:00Z"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const hub = (routes: Record<string, unknown> = {}) =>
  fakeHub({
    "GET /api/v1/products": productsWithIv,
    "GET /api/v1/forecasts": page([forecastB, forecastE]),
    "GET /api/v1/surplus/incoming": page([]),
    "GET /api/v1/surplus": page([]),
    ...routes,
  });

/** Opens a confirm dialog, types an optional reason and presses its confirm button. */
async function confirm(trigger: HTMLElement, label: string, reason?: string) {
  fireEvent.click(trigger);
  const dialog = await screen.findByRole("dialog");
  if (reason)
    fireEvent.change(within(dialog).getByLabelText("Reason (optional)"), {
      target: { value: reason },
    });
  fireEvent.click(within(dialog).getByRole("button", { name: label }));
}

describe("Forecasts and surplus", () => {
  it("lists predicted stock-outs with dates and reorder suggestions, labelled synthetic", async () => {
    hub();
    renderAs(meAs("STORE_MANAGER"), <ForecastsPage />);
    const table = await screen.findByRole("table", { name: "Predicted stock-outs" });
    const [, row] = within(table).getAllByRole("row");
    expect(within(row!).getByText("IV Cannula 20G")).toBeTruthy();
    expect(within(row!).getByText("120 each")).toBeTruthy();
    expect(within(row!).getByTestId("stockout").textContent).toContain("(in 4 days)");
    expect(within(row!).getByTestId("reorder").textContent).toBe("0 each (lead time 1 d)");
    expect(within(row!).getByTestId("synthetic").textContent).toBe("Synthetic history");
  });

  it("offers an expiry-risk batch's suggested qty to the network", async () => {
    const fake = hub({ "POST /api/v1/surplus": { ...ownPost, status: "OPEN" } });
    renderAs(meAs("STORE_MANAGER"), <ForecastsPage />);
    const table = await screen.findByRole("table", { name: "Expiry-risk batches" });
    const [, row] = within(table).getAllByRole("row");
    expect(within(row!).getByTestId("excess").textContent).toBe("300 each");
    await confirm(
      within(row!).getByRole("button", { name: "Offer to network" }),
      "Offer to network",
      "Expiring",
    );
    await waitFor(() => expect(fake.to("POST", "/api/v1/surplus")).toHaveLength(1));
    expect(JSON.parse(fake.to("POST", "/api/v1/surplus")[0]!.body!)).toEqual({
      batch_id: IV_BATCH,
      qty: 300,
      reason: "Expiring",
    });
    await waitFor(() => expect(fake.to("GET", "/api/v1/forecasts").length).toBeGreaterThan(1));
  });

  it("shows the hub's refusal in the offer dialog", async () => {
    hub({
      "POST /api/v1/surplus": hubError(
        400,
        "validation",
        "Only 120 of this batch is transferable.",
      ),
    });
    renderAs(meAs("STORE_MANAGER"), <ForecastsPage />);
    await confirm(
      await screen.findByRole("button", { name: "Offer to network" }),
      "Offer to network",
    );
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Only 120 of this batch is transferable.")).toBeTruthy();
  });

  it("marks a batch already offered", async () => {
    const offered = {
      ...forecastB,
      expiry_risks: [{ ...forecastB.expiry_risks[0]!, surplus_post_id: SURPLUS_ID }],
    };
    hub({ "GET /api/v1/forecasts": page([offered]) });
    renderAs(meAs("STORE_MANAGER"), <ForecastsPage />);
    const table = await screen.findByRole("table", { name: "Expiry-risk batches" });
    expect(within(table).getByText("Offered")).toBeTruthy();
    expect(within(table).queryByRole("button", { name: "Offer to network" })).toBeNull();
  });

  it("lists surplus offered to this hospital with band and location only", async () => {
    hub({ "GET /api/v1/surplus/incoming": page([offerFromB]) });
    renderAs(meAs("STORE_MANAGER"), <ForecastsPage />);
    const list = await screen.findByRole("list", { name: "Surplus offered to you" });
    expect(list.textContent).toContain("Hospital B offers 300 each IV Cannula 20G");
    expect(list.textContent).toContain("Expires in 30–59 days");
    expect(list.textContent).toContain("Hospital B central store");
    expect(list.textContent).toContain("Matches your predicted stock-out on");
  });

  it("withdraws an own post with the typed reason", async () => {
    const fake = hub({
      "GET /api/v1/surplus": page([ownPost]),
      "POST /api/v1/surplus/{id}/withdraw": { ...ownPost, status: "WITHDRAWN" },
    });
    renderAs(meAs("STORE_MANAGER"), <ForecastsPage />);
    const table = await screen.findByRole("table", { name: "Your surplus posts" });
    expect(within(table).getByTestId("offered").textContent).toBe("300 each");
    expect(within(table).getByText("Matched")).toBeTruthy();
    await confirm(
      within(table).getByRole("button", { name: "Withdraw" }),
      "Withdraw",
      "Used locally",
    );
    await waitFor(() =>
      expect(fake.to("POST", `/api/v1/surplus/${SURPLUS_ID}/withdraw`)).toHaveLength(1),
    );
    expect(fake.to("POST", `/api/v1/surplus/${SURPLUS_ID}/withdraw`)[0]!.body).toBe(
      JSON.stringify({ reason: "Used locally" }),
    );
  });

  it("runs the forecast on demand", async () => {
    const fake = hub({
      "POST /api/v1/forecasts/run": { org_ids: ["a"], series: 15, models: {} },
    });
    renderAs(meAs("STORE_MANAGER"), <ForecastsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Run forecast now" }));
    await waitFor(() => expect(fake.to("POST", "/api/v1/forecasts/run")).toHaveLength(1));
  });

  it("shows forecasts read-only without inventory.edit and never asks for surplus", async () => {
    const fake = hub();
    renderAs(meAs("APPROVER"), <ForecastsPage />);
    await screen.findByRole("table", { name: "Expiry-risk batches" });
    expect(screen.queryByRole("button", { name: "Offer to network" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Run forecast now" })).toBeNull();
    expect(screen.queryByText("Surplus offered to you")).toBeNull();
    expect(fake.to("GET", "/api/v1/surplus")).toHaveLength(0);
    expect(fake.to("GET", "/api/v1/surplus/incoming")).toHaveLength(0);
  });

  it("has an empty state and shows the hub's error", async () => {
    hub({ "GET /api/v1/forecasts": page([]) });
    renderAs(meAs("APPROVER"), <ForecastsPage />);
    expect(await screen.findByText("No forecasts yet")).toBeTruthy();
    cleanup();
    hub({ "GET /api/v1/forecasts": hubError(500, "internal_error", "Hub is down.") });
    renderAs(meAs("APPROVER"), <ForecastsPage />);
    expect((await screen.findByRole("alert")).textContent).toContain("Hub is down.");
  });
});
