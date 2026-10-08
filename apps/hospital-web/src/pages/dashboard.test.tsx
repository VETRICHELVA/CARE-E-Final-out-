import { act, cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { incoming, meAs, NOW, page, products, shortage } from "../test/fixtures";
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
    const hub = (requests: unknown) =>
      fakeHub({
        "GET /api/v1/shortages": page([]),
        "GET /api/v1/products": products,
        "GET /api/v1/source-requests": requests,
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
});
