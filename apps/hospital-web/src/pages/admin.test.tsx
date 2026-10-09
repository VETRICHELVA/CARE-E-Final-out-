import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NetworkMetrics } from "../api";
import { meAs } from "../test/fixtures";
import { fakeHub, hubError, renderAs } from "../test/hub";
import { AdminPage, DEFINITIONS, formatMinutes } from "./admin";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PATH = "/api/v1/metrics/network";

/** Scenario 1 and 3 run once each, plus Scenario 2's monitored delivery with an excursion. */
const metrics: NetworkMetrics = {
  computed_at: "2026-10-09T06:00:00Z",
  time_to_confirmed_source: {
    median_minutes: 185.4,
    shortages_confirmed: 3,
    shortages_reported: 4,
  },
  resolution_mix: { transfers: 2, purchases: 1, transfer_share: 0.6667, purchase_share: 0.3333 },
  procurement_cost_avoided: { paise: 1_234_500, units_priced: 500, units_unpriced: 0 },
  units_saved_from_expiry: { units: 300 },
  cold_chain: {
    deliveries: 1,
    monitored: 1,
    with_excursion: 1,
    with_device_silent: 0,
    compliance_rate: 0,
  },
};

const empty: NetworkMetrics = {
  computed_at: "2026-10-09T06:00:00Z",
  time_to_confirmed_source: { median_minutes: null, shortages_confirmed: 0, shortages_reported: 0 },
  resolution_mix: { transfers: 0, purchases: 0, transfer_share: null, purchase_share: null },
  procurement_cost_avoided: { paise: 0, units_priced: 0, units_unpriced: 0 },
  units_saved_from_expiry: { units: 0 },
  cold_chain: {
    deliveries: 0,
    monitored: 0,
    with_excursion: 0,
    with_device_silent: 0,
    compliance_rate: null,
  },
};

const platform = () => meAs("ADMIN", "PLATFORM");
const show = (me = platform()) => renderAs(me, <AdminPage />, { path: "/admin" });
const metric = (id: string) => screen.getByTestId(`metric-${id}`);
const value = (id: string) => within(metric(id)).getByTestId("value").textContent;
const detail = (id: string) => within(metric(id)).getByTestId("detail").textContent;

describe("Admin: network metrics", () => {
  it("shows the five figures, each with its definition", async () => {
    const hub = fakeHub({ [`GET ${PATH}`]: metrics });
    show();
    await screen.findByTestId("metric-time");
    expect(hub.to("GET", PATH)).toHaveLength(1);

    expect(value("time")).toBe("3 h 5 min");
    expect(detail("time")).toBe("3 of 4 shortages reported have a confirmed source.");
    expect(value("mix")).toBe("66.7% transfers · 33.3% purchases");
    expect(detail("mix")).toBe("2 approved transfers, 1 approved purchase.");
    expect(value("cost")).toBe("₹12,345.00");
    expect(detail("cost")).toBe("On 500 units received from hospital transfers.");
    expect(value("expiry")).toBe("300");
    expect(value("cold-chain")).toBe("0%");
    expect(detail("cold-chain")).toBe(
      "1 of 1 cold-chain delivery monitored; 1 with an excursion; 0 with a silent device (a gap, not a breach).",
    );

    const ids = ["time", "mix", "cost", "expiry", "cold-chain"];
    const definitions = Object.values(DEFINITIONS);
    ids.forEach((id, i) =>
      expect(within(metric(id)).getByTestId("definition").textContent).toBe(definitions[i]),
    );
    expect(screen.getByRole("region", { name: "Units saved from expiry" })).toBeTruthy();
  });

  it("says when units had no supplier price and rates have no denominator", async () => {
    fakeHub({
      [`GET ${PATH}`]: {
        ...metrics,
        time_to_confirmed_source: {
          median_minutes: null,
          shortages_confirmed: 0,
          shortages_reported: 1,
        },
        resolution_mix: { transfers: 0, purchases: 0, transfer_share: null, purchase_share: null },
        procurement_cost_avoided: { paise: 0, units_priced: 0, units_unpriced: 40 },
        cold_chain: { ...empty.cold_chain, deliveries: 2 },
      },
    });
    show();
    await screen.findByTestId("metric-time");
    expect(value("time")).toBe("—");
    expect(detail("time")).toBe("No shortage has a confirmed source yet (1 shortage reported).");
    expect(value("mix")).toBe("—");
    expect(detail("mix")).toBe("No recommendation has been approved yet.");
    expect(detail("cost")).toBe(
      "On 0 units received from hospital transfers. 40 more units had no supplier price on record and are left out.",
    );
    // Two cold-chain deliveries, none monitored: no rate, never "100%".
    expect(value("cold-chain")).toBe("—");
    expect(detail("cold-chain")).toContain("0 of 2 cold-chain deliveries monitored");
  });

  it("shows loading, then an empty state while nothing has been reported", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    fakeHub({ [`GET ${PATH}`]: async () => (await gate, empty) });
    show();
    expect(await screen.findByRole("status")).toBeTruthy();
    release();
    expect(await screen.findByText("Nothing to measure yet")).toBeTruthy();
    expect(screen.queryByTestId("metric-time")).toBeNull();
  });

  it("shows the hub's error", async () => {
    fakeHub({ [`GET ${PATH}`]: hubError(500, "internal_error", "The hub is unavailable.") });
    show();
    expect((await screen.findByRole("alert")).textContent).toContain("The hub is unavailable.");
  });

  it("refreshes on request, with no polling", async () => {
    let reported = 4;
    const hub = fakeHub({
      [`GET ${PATH}`]: () => ({
        ...metrics,
        time_to_confirmed_source: {
          ...metrics.time_to_confirmed_source,
          shortages_reported: reported,
        },
      }),
    });
    show();
    await screen.findByText("3 of 4 shortages reported have a confirmed source.");
    reported = 5;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(
      await screen.findByText("3 of 5 shortages reported have a confirmed source."),
    ).toBeTruthy();
    expect(hub.to("GET", PATH)).toHaveLength(2);
    await waitFor(() =>
      expect((screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
  });

  it.each([
    ["a hospital admin with audit.read", meAs("ADMIN", "HOSPITAL")],
    ["a platform user without audit.read", { ...platform(), capabilities: [] }],
  ])("is not shown to %s, and asks the hub nothing", async (_, me) => {
    const hub = fakeHub({ [`GET ${PATH}`]: metrics });
    show(me);
    expect(
      await screen.findByText("Network metrics are for the CARE-E platform team"),
    ).toBeTruthy();
    expect(hub.to("GET", PATH)).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  });
});

describe("formatMinutes", () => {
  it.each([
    [0.4, "0 min"],
    [42, "42 min"],
    [60, "1 h"],
    [185.4, "3 h 5 min"],
    [1440, "1 d"],
    [3000, "2 d 2 h"],
  ])("%s → %s", (minutes, text) => expect(formatMinutes(minutes)).toBe(text));
});
