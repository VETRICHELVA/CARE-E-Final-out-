// @vitest-environment jsdom
import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { connectEventStream, type EventEnvelope, type Me } from "@care-e/api-client";
import {
  type ColdChain,
  ColdChainAlerts,
  ColdChainPanel,
  ColdChainStateBadge,
  describeColdChainEvent,
  formatBand,
  formatSeconds,
  isColdChainAlert,
} from "./coldchain";
import { Toaster } from "./components/ui/sonner";
import { formatDateTime } from "./format";
import { fakeHub, renderAs } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ORG_A = "0a000000-0000-4000-8000-000000000001";
const SHIPMENT = "51000000-0000-4000-8000-000000000002";

const me = (orgId = ORG_A): Me => ({
  user: { id: "u1", email: "r@a.test", full_name: "Receiver A", org_id: orgId, is_active: true },
  org: {
    id: orgId,
    name: "Hospital A",
    type: "HOSPITAL",
    status: "ACTIVE",
    location: { lat: 0, lng: 0 },
  },
  roles: [],
  capabilities: [],
});

const at = (s: number) => new Date(Date.UTC(2026, 9, 8, 6, 0, s)).toISOString();

/** Scenario 2: near 4 °C, then 9.1 and 9.4 °C, then back in range. */
const scenario2: ColdChain = {
  shipment_id: SHIPMENT,
  requires_cold_chain: true,
  band: { temp_min_c: 2, temp_max_c: 8 },
  device: { device_id: "cb-01", battery_level: 82, last_seen: at(50) },
  silent_after_seconds: 120,
  readings: [4.1, 4.3, 9.1, 9.4, 5.0, 4.6].map((temp_c, i) => ({
    device_id: "cb-01",
    ts: at(i * 10),
    temp_c,
    battery: 82,
  })),
  events: [
    {
      id: "e1",
      type: "EXCURSION",
      severity: "ALERT",
      device_id: "cb-01",
      observed_value: 9.4,
      threshold: 8,
      ts: at(30),
    },
    {
      id: "e2",
      type: "RECOVERED",
      severity: "INFO",
      device_id: "cb-01",
      observed_value: 4.6,
      threshold: 8,
      ts: at(50),
    },
  ],
  has_excursion: true,
};

describe("cold-chain wording", () => {
  it("states only the values the hub recorded", () => {
    expect(describeColdChainEvent({ type: "EXCURSION", observed_value: 9.4, threshold: 8 })).toBe(
      "Readings out of range: 9.4 °C, above the maximum of 8 °C.",
    );
    expect(describeColdChainEvent({ type: "EXCURSION", observed_value: 0.8, threshold: 2 })).toBe(
      "Readings out of range: 0.8 °C, below the minimum of 2 °C.",
    );
    expect(describeColdChainEvent({ type: "RECOVERED", observed_value: 4.6, threshold: 8 })).toBe(
      "Readings back in range, latest 4.6 °C. The excursion stays on record.",
    );
    expect(
      describeColdChainEvent({ type: "DEVICE_SILENT", observed_value: 150, threshold: 120 }),
    ).toBe("No reading received for 2 min 30 s (the limit is 2 min).");
  });

  it("formats durations and bands", () => {
    expect([formatSeconds(45), formatSeconds(120), formatSeconds(150)]).toEqual([
      "45 s",
      "2 min",
      "2 min 30 s",
    ]);
    expect(formatBand({ temp_min_c: 2, temp_max_c: 8 })).toBe("2–8 °C");
    expect(formatBand({ temp_min_c: null, temp_max_c: -15 })).toBe("at most -15 °C");
    expect(formatBand({ temp_min_c: null, temp_max_c: null })).toBeNull();
  });
});

describe("ColdChainPanel", () => {
  it("shows the band, the readings, the events, battery and last seen", async () => {
    const hub = fakeHub({ "GET /api/v1/shipments/{id}/coldchain": scenario2 });
    renderAs(me(), <ColdChainPanel shipmentId={SHIPMENT} />);
    const panel = await screen.findByTestId("temperature-chart");
    expect(hub.to("GET", `/api/v1/shipments/${SHIPMENT}/coldchain`)).toHaveLength(1);
    expect(screen.getByText("2–8 °C")).toBeTruthy();
    expect(screen.getByTestId("latest-reading").textContent).toBe("4.6 °C");
    expect(screen.getByText("cb-01")).toBeTruthy();
    expect(screen.getByText(/battery 82%/)).toBeTruthy();
    expect(screen.getByTestId("allowed-band")).toBeTruthy();
    // 9.1 and 9.4 °C are outside 2–8 °C.
    expect(within(panel as unknown as HTMLElement).getAllByTestId("out-of-range")).toHaveLength(2);
    expect(screen.getByTestId("excursion-on-record").textContent).toBe(
      "A cold-chain excursion is on record for this shipment.",
    );
    const events = screen.getByRole("list", { name: "Cold-chain events" });
    expect(within(events).getByText("Temperature excursion")).toBeTruthy();
    expect(
      within(events).getByText("Readings out of range: 9.4 °C, above the maximum of 8 °C."),
    ).toBeTruthy();
    expect(within(events).getByText("Back in range")).toBeTruthy();
  });

  it("says so when no reading has arrived yet", async () => {
    fakeHub({
      "GET /api/v1/shipments/{id}/coldchain": {
        ...scenario2,
        device: null,
        readings: [],
        events: [],
        has_excursion: false,
      },
    });
    renderAs(me(), <ColdChainPanel shipmentId={SHIPMENT} />);
    expect(await screen.findByTestId("no-readings")).toBeTruthy();
    expect(screen.getByText("No cold-chain events recorded.")).toBeTruthy();
    expect(screen.queryByTestId("excursion-on-record")).toBeNull();
  });

  it("shows the hub's refusal", async () => {
    fakeHub({
      "GET /api/v1/shipments/{id}/coldchain": Response.json(
        { code: "forbidden", message: "Only the shipment's organizations see its cold chain." },
        { status: 403 },
      ),
    });
    renderAs(me(), <ColdChainPanel shipmentId={SHIPMENT} />);
    expect(
      await screen.findByText("Only the shipment's organizations see its cold chain."),
    ).toBeTruthy();
  });
});

describe("ColdChainStateBadge", () => {
  const badges = (summary: Parameters<typeof ColdChainStateBadge>[0]["summary"]) => {
    const { container } = render(<ColdChainStateBadge summary={summary} />);
    const text = [...container.querySelectorAll("span")].map((s) => s.textContent);
    cleanup();
    return text;
  };

  it("shows the newest event and whether an excursion is on record", () => {
    const last = at(30);
    expect(badges(null)).toEqual([]);
    expect(
      badges({ last_event_type: "EXCURSION", last_event_at: last, had_excursion: true }),
    ).toEqual(["Temperature excursion"]);
    expect(
      badges({ last_event_type: "RECOVERED", last_event_at: last, had_excursion: true }),
    ).toEqual(["Excursion on record"]);
    expect(
      badges({ last_event_type: "DEVICE_SILENT", last_event_at: last, had_excursion: false }),
    ).toEqual([`Device silent at ${formatDateTime(last)}`]);
    expect(
      badges({ last_event_type: "DEVICE_SILENT", last_event_at: last, had_excursion: true }),
    ).toEqual([`Device silent at ${formatDateTime(last)}`, "Excursion on record"]);
  });
});

const envelope = (type: string, data: Record<string, unknown>): EventEnvelope => ({
  id: "1",
  type,
  occurred_at: "",
  org_ids: [ORG_A],
  data,
});

describe("cold-chain alerts", () => {
  const excursion = {
    shipment_id: SHIPMENT,
    coldchain_event_id: "e1",
    device_id: "cb-01",
    severity: "ALERT",
    observed_value: 9.4,
    threshold: 8,
    ts: at(30),
    to_org_id: ORG_A,
  };

  it("alerts on cold-chain events only, inbound ones when asked", () => {
    expect(isColdChainAlert(envelope("coldchain.excursion", excursion), ORG_A)).toBe(true);
    expect(isColdChainAlert(envelope("coldchain.reading", excursion), ORG_A)).toBe(false);
    expect(isColdChainAlert(envelope("coldchain.excursion", excursion), "other", true)).toBe(false);
    expect(isColdChainAlert(envelope("coldchain.excursion", excursion), ORG_A, true)).toBe(true);
    expect(isColdChainAlert(envelope("coldchain.excursion", excursion), undefined, true)).toBe(
      false,
    );
  });

  it("raises a toast for each new event the stream delivers, linking to the shipment", async () => {
    class FakeEventSource {
      static last: FakeEventSource | undefined;
      onopen = null;
      onerror = null;
      onmessage: ((ev: MessageEvent) => void) | null = null;
      constructor(readonly url: string) {
        FakeEventSource.last = this;
      }
      addEventListener() {}
      close() {}
    }
    renderAs(
      me(),
      <>
        <ColdChainAlerts link={(id) => `/deliveries/${id}`} inboundOnly />
        <Toaster />
      </>,
      { path: "/" },
    );
    const stop = connectEventStream(new QueryClient(), {
      ticket: async () => "t",
      EventSource: FakeEventSource as never,
    });
    await vi.waitFor(() => expect(FakeEventSource.last).toBeDefined());
    const send = (event: EventEnvelope) =>
      act(() =>
        FakeEventSource.last!.onmessage?.(
          new MessageEvent("message", { data: JSON.stringify(event) }),
        ),
      );
    // Another hospital's shipment (we are its sender): no toast in the inbound-only app.
    send(envelope("coldchain.excursion", { ...excursion, to_org_id: "other" }));
    send(envelope("coldchain.excursion", excursion));
    expect(await screen.findByText("Temperature excursion (cb-01)")).toBeTruthy();
    expect(
      screen.getByText("Readings out of range: 9.4 °C, above the maximum of 8 °C."),
    ).toBeTruthy();
    expect(screen.getAllByText(/Temperature excursion/)).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "View" }));
    expect((await screen.findByTestId("location")).textContent).toBe(`/deliveries/${SHIPMENT}`);
    stop();
  });
});
