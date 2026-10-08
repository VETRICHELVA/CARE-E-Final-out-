import { act, cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  forecastB,
  forecastE,
  incoming,
  meAs,
  NOW,
  offerFromB,
  page,
  products,
  productsWithIv,
  shortage,
} from "../test/fixtures";
import { fakeHub, hubError, renderAs } from "../test/hub";
import { DashboardPage } from "./dashboard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const at = (status: string, i: number) => ({ ...shortage, id: `${shortage.id}-${i}`, status });

describe("Dashboard", () => {
  it("counts open shortages by status and leaves out closed ones", async () => {
    fakeHub({
      "GET /api/v1/shortages": page([
        at("MATCHING", 1),
        at("MATCHING", 2),
        at("AWAITING_DECISION", 3),
        at("RESOLVED", 4),
        at("CANCELLED", 5),
      ]),
    });
    renderAs(meAs("REQUESTER"), <DashboardPage />);
    expect((await screen.findByTestId("count-MATCHING")).textContent).toBe("2");
    expect(screen.getByTestId("count-AWAITING_DECISION").textContent).toBe("1");
    expect(screen.queryByTestId("count-RESOLVED")).toBeNull();
    expect(screen.queryByTestId("count-CANCELLED")).toBeNull();
  });

  it("shows loading, then an empty state", async () => {
    fakeHub({ "GET /api/v1/shortages": page([at("RESOLVED", 1)]) });
    renderAs(meAs("REQUESTER"), <DashboardPage />);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(await screen.findByText("No open shortages")).toBeTruthy();
  });

  it("shows the hub's message when the list fails", async () => {
    fakeHub({ "GET /api/v1/shortages": hubError(500, "internal_error", "Hub is down.") });
    renderAs(meAs("REQUESTER"), <DashboardPage />);
    expect((await screen.findByRole("alert")).textContent).toContain("Hub is down.");
  });

  it("shows an approver their org's open shortages", async () => {
    fakeHub({ "GET /api/v1/shortages": page([at("AWAITING_DECISION", 1)]) });
    renderAs(meAs("APPROVER"), <DashboardPage />);
    expect((await screen.findByTestId("count-AWAITING_DECISION")).textContent).toBe("1");
  });

  it("doesn't ask for shortages without shortage.create or recommendation.approve", async () => {
    const fake = fakeHub({});
    renderAs(meAs("RECEIVER"), <DashboardPage />);
    expect(await screen.findByText("Nothing to show yet")).toBeTruthy();
    expect(fake.calls).toHaveLength(0);
  });

  describe("incoming requests awaiting response", () => {
    // A store manager also keeps the stock, so the S18 cards ask for forecasts and surplus.
    const hub = (requests: unknown) =>
      fakeHub({
        "GET /api/v1/shortages": page([]),
        "GET /api/v1/products": products,
        "GET /api/v1/source-requests": requests,
        "GET /api/v1/forecasts": page([]),
        "GET /api/v1/surplus/incoming": page([]),
      });

    it("lists each request with product, qty, requester and a countdown", async () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(NOW);
      const fake = hub(page([incoming]));
      renderAs(meAs("STORE_MANAGER"), <DashboardPage />);
      const list = await screen.findByRole("list", {
        name: "Incoming requests awaiting response",
      });
      const [item] = within(list).getAllByRole("listitem");
      expect(within(item!).getByText("300 kits Surgical Kit A")).toBeTruthy();
      expect(within(item!).getByText("from Hospital D")).toBeTruthy();
      expect(within(item!).getByTestId("countdown").textContent).toBe("14:31 left");
      const query = fake.to("GET", "/api/v1/source-requests")[0]!.url.searchParams;
      expect(query.get("direction")).toBe("incoming");
      expect(query.get("status")).toBe("REQUESTED");
    });

    it("counts down to Deadline passed", async () => {
      vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
      vi.setSystemTime(Date.parse(incoming.sla_deadline) - 1_000);
      hub(page([incoming]));
      renderAs(meAs("STORE_MANAGER"), <DashboardPage />);
      const clock = await screen.findByTestId("countdown");
      expect(clock.textContent).toBe("0:01 left");
      act(() => vi.advanceTimersByTime(1_000));
      expect(clock.textContent).toBe("Deadline passed");
    });

    it("has an empty state and shows the hub's error", async () => {
      hub(page([]));
      renderAs(meAs("STORE_MANAGER"), <DashboardPage />);
      expect(await screen.findByText("No requests awaiting your response")).toBeTruthy();
      cleanup();

      hub(hubError(500, "internal_error", "Hub is down."));
      renderAs(meAs("STORE_MANAGER"), <DashboardPage />);
      expect((await screen.findByRole("alert")).textContent).toContain("Hub is down.");
    });

    it("isn't shown without source_request.respond", async () => {
      const fake = hub(page([incoming]));
      renderAs(meAs("REQUESTER"), <DashboardPage />);
      await screen.findByText("No open shortages");
      expect(screen.queryByText("Incoming requests awaiting response")).toBeNull();
      expect(fake.to("GET", "/api/v1/source-requests")).toHaveLength(0);
    });
  });

  describe("forecasts and surplus (S18)", () => {
    const hub = (routes: Record<string, unknown>) =>
      fakeHub({
        "GET /api/v1/shortages": page([]),
        "GET /api/v1/products": productsWithIv,
        "GET /api/v1/source-requests": page([]),
        "GET /api/v1/forecasts": page([]),
        "GET /api/v1/surplus/incoming": page([]),
        ...routes,
      });

    it("suggests offering an expiry-risk batch's excess and counts the risks", async () => {
      hub({ "GET /api/v1/forecasts": page([forecastB]) });
      renderAs(meAs("STORE_MANAGER"), <DashboardPage />);
      expect((await screen.findByTestId("expiry-risk-count")).textContent).toBe("1");
      const list = screen.getByRole("list", { name: "Expiry-risk suggestions" });
      expect(list.textContent).toContain("Offer 300 each to the network: IV Cannula 20G");
      expect(within(list).getByTestId("synthetic").textContent).toBe("Synthetic history");
    });

    it("shows the predicted stock-out and the surplus matched to this hospital", async () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(Date.parse("2026-10-08T06:00:00Z"));
      hub({
        "GET /api/v1/forecasts": page([forecastE]),
        "GET /api/v1/surplus/incoming": page([offerFromB]),
      });
      renderAs(meAs("STORE_MANAGER"), <DashboardPage />);
      const stockouts = await screen.findByRole("list", { name: "Predicted stock-outs" });
      expect(within(stockouts).getByTestId("stockout").textContent).toContain("(in 4 days)");
      const offers = await screen.findByRole("list", { name: "Surplus offered to you" });
      expect(offers.textContent).toContain("Hospital B offers 300 each IV Cannula 20G");
      expect(offers.textContent).toContain("Expires in 30–59 days");
      expect(offers.textContent).toContain("Matches your predicted stock-out on");
    });

    it("isn't shown without inventory.edit", async () => {
      const fake = hub({});
      renderAs(meAs("REQUESTER"), <DashboardPage />);
      await screen.findByText("No open shortages");
      expect(fake.to("GET", "/api/v1/forecasts")).toHaveLength(0);
      expect(fake.to("GET", "/api/v1/surplus/incoming")).toHaveLength(0);
    });
  });
});
