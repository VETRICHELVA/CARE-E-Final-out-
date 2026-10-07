import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { meAs, page, shortage } from "../test/fixtures";
import { fakeHub, hubError, renderAs } from "../test/hub";
import { DashboardPage } from "./dashboard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
});
